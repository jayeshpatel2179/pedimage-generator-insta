"""End-to-end tests for the mandatory manual Instagram caption step.

Drives the real Application (routing, handler groups, ConversationHandler, publish handlers)
with a fake Telegram transport, fake OpenAI calls and DRY_RUN=1 - nothing touches the network,
Instagram or the website. Run from the repo root:

    venv/Scripts/python.exe tests/test_manual_ig_caption.py
"""
import asyncio
import html
import inspect
import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Must happen before any bot import: config.py reads env at import time, and load_dotenv()
# never overrides variables that are already set - so the real .env secrets are never used.
os.environ.update(
    {
        "TELEGRAM_BOT_TOKEN": "123456:TEST-TOKEN",
        "OPENAI_API_KEY": "sk-test",
        "UPLOAD_POST_API_KEY": "test",
        "UPLOAD_POST_PROFILE": "test-profile",
        "WEBSITE_UPLOAD_API_KEY": "test",
        "WEBSITE_UPLOAD_URL": "https://dry-run.invalid/api/upload",
        "GO_LIVE_ALLOWED_USER_IDS": "",
        "DRY_RUN": "1",
    }
)
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from PIL import Image  # noqa: E402
from telegram import Update  # noqa: E402
from telegram.request import HTTPXRequest  # noqa: E402

import bot.config as config  # noqa: E402
import bot.handlers.conversation as conversation_mod  # noqa: E402
import bot.handlers.publish as publish_mod  # noqa: E402
import bot.publish.state as state  # noqa: E402
import bot.utils.image_utils as image_utils  # noqa: E402
from bot.go_live.uploadpost_client import UploadPostError, upload_photo_to_instagram  # noqa: E402
from bot.main import build_application  # noqa: E402
from bot.publish import texts  # noqa: E402

assert config.DRY_RUN, "tests must run with DRY_RUN on"

# Isolated storage so tests never touch storage/ of a real deployment.
_TMP = Path(tempfile.mkdtemp(prefix="pedtalks-test-"))
image_utils.TEMP_DIR = _TMP
state.PUBLISH_DRAFTS_FILE = _TMP / "publish_drafts.json"

CHAT_ID = 1001
USER_ID = 42
USER = {"id": USER_ID, "is_bot": False, "first_name": "Editor"}


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


PNG = _png_bytes()


# --- Fake Telegram -----------------------------------------------------------------------
class FakeTelegram:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self._next_id = 5000

    def reset(self):
        self.calls.clear()

    def new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _message(self, params: dict, message_id: int | None = None) -> dict:
        return {
            "message_id": message_id or self.new_id(),
            "date": int(time.time()),
            "chat": {"id": int(params.get("chat_id", CHAT_ID)), "type": "private"},
            "from": {"id": 1, "is_bot": True, "first_name": "Bot"},
            "text": params.get("text", ""),
        }

    async def do_request(self, url, method, request_data=None, **_):
        if "/file/bot" in url:
            return 200, PNG
        endpoint = url.rsplit("/", 1)[-1]
        params = dict(request_data.parameters) if request_data else {}
        self.calls.append((endpoint, params))

        if endpoint == "getMe":
            result = {"id": 1, "is_bot": True, "first_name": "Bot", "username": "test_bot",
                      "can_join_groups": True, "can_read_all_group_messages": True, "supports_inline_queries": False}
        elif endpoint in ("sendMessage", "sendPhoto"):
            result = self._message(params)
        elif endpoint == "editMessageText":
            result = self._message(params, int(params["message_id"]))
        elif endpoint == "getFile":
            result = {"file_id": params.get("file_id", "f"), "file_unique_id": "u", "file_size": len(PNG),
                      "file_path": "photos/file_1.jpg"}
        else:
            result = True
        return 200, json.dumps({"ok": True, "result": result}).encode()

    # helpers
    def of(self, endpoint: str) -> list[dict]:
        return [p for e, p in self.calls if e == endpoint]

    def texts_sent(self) -> list[str]:
        return [p.get("text", "") for p in self.of("sendMessage")]


TG = FakeTelegram()
HTTPXRequest.do_request = lambda self, *a, **kw: TG.do_request(*a, **kw)  # type: ignore[assignment]


# --- Fake OpenAI + payload capture -----------------------------------------------------
AI_CAPTION = "AI WROTE THIS caption\n\n#ai #generated #tags"
SUMMARY_CALLS: list[dict] = []
IMAGE_CALLS: list[str] = []


async def fake_generate_image(photo_path, prompt):
    IMAGE_CALLS.append(prompt)
    return PNG


async def fake_generate_caption(headline, image_path):
    return AI_CAPTION


async def fake_generate_summary(image_path, headline, caption, *, is_regeneration, history):
    SUMMARY_CALLS.append({"headline": headline, "caption": caption, "is_regeneration": is_regeneration})
    return f"AI website summary for {headline} (call {len(SUMMARY_CALLS)})."


conversation_mod.generate_image = fake_generate_image
publish_mod.generate_caption = fake_generate_caption
publish_mod.generate_website_summary = fake_generate_summary


class PayloadCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.instagram: list[dict] = []
        self.website: list[dict] = []

    def emit(self, record):
        msg = record.getMessage()
        for prefix, bucket in (("DRY_RUN instagram payload: ", self.instagram), ("DRY_RUN website payload: ", self.website)):
            if msg.startswith(prefix):
                bucket.append(json.loads(msg[len(prefix):]))

    def reset(self):
        self.instagram.clear()
        self.website.clear()


PAYLOADS = PayloadCapture()
logging.getLogger("bot").addHandler(PAYLOADS)
logging.getLogger("bot").setLevel(logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)


# --- Update builders -----------------------------------------------------------------------
_update_id = 0


def _next_update_id() -> int:
    global _update_id
    _update_id += 1
    return _update_id


def _base_message(**extra) -> dict:
    msg = {"message_id": TG.new_id(), "date": int(time.time()), "chat": {"id": CHAT_ID, "type": "private"}, "from": USER}
    msg.update(extra)
    return msg


async def send_text(app, text, reply_to: int | None = None):
    extra = {"text": text}
    if text.startswith("/"):
        extra["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
    if reply_to is not None:
        extra["reply_to_message"] = {"message_id": reply_to, "date": int(time.time()), "chat": {"id": CHAT_ID, "type": "private"}}
    await app.process_update(Update.de_json({"update_id": _next_update_id(), "message": _base_message(**extra)}, app.bot))
    await settle()


async def send_photo(app):
    photo = [{"file_id": "photo1", "file_unique_id": "pu1", "width": 64, "height": 64}]
    await app.process_update(Update.de_json({"update_id": _next_update_id(), "message": _base_message(photo=photo)}, app.bot))
    await settle()


async def send_voice(app):
    voice = {"file_id": "voice1", "file_unique_id": "vu1", "duration": 2}
    await app.process_update(Update.de_json({"update_id": _next_update_id(), "message": _base_message(voice=voice)}, app.bot))
    await settle()


async def send_edited_text(app, text):
    msg = _base_message(text=text, edit_date=int(time.time()))
    await app.process_update(Update.de_json({"update_id": _next_update_id(), "edited_message": msg}, app.bot))
    await settle()


async def tap(app, data: str, message_id: int):
    cq = {
        "id": str(_next_update_id()),
        "from": USER,
        "chat_instance": "ci",
        "data": data,
        "message": {"message_id": message_id, "date": int(time.time()), "chat": {"id": CHAT_ID, "type": "private"},
                    "from": {"id": 1, "is_bot": True, "first_name": "Bot"}, "text": "x"},
    }
    await app.process_update(Update.de_json({"update_id": _next_update_id(), "callback_query": cq}, app.bot))
    await settle()


def _is_expiry_timer(task) -> bool:
    # PTB wraps every application.create_task coroutine, so walk the await chain.
    coro = task.get_coro()
    while coro is not None:
        if getattr(coro, "__qualname__", "") == "_expire_after_timeout":
            return True
        coro = getattr(coro, "cr_await", None)
    return False


async def settle():
    """Waits for background tasks (generation, summary, publish) - not the 30-min expiry timers."""
    for _ in range(500):
        pending = [
            t for t in asyncio.all_tasks()
            if t is not asyncio.current_task() and not t.done() and not _is_expiry_timer(t)
        ]
        if not pending:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"background tasks never finished: {pending}")


async def new_app():
    app = build_application()
    await app.initialize()
    await app.post_init(app)
    return app


async def make_awaiting_draft(app, headline="Messi is **back** & better") -> state.PublishDraft:
    await send_text(app, headline)
    await tap(app, "layout:left", TG.new_id())
    await send_photo(app)
    drafts = state.awaiting_caption_drafts(CHAT_ID, USER_ID)
    assert drafts, "no draft awaiting a caption after generation"
    return drafts[-1]


def reset_world():
    for draft in state.all_drafts():
        state.pop_draft(draft.id)
    TG.reset()
    PAYLOADS.reset()
    SUMMARY_CALLS.clear()
    IMAGE_CALLS.clear()


# --- Tests ----------------------------------------------------------------------------------
async def _caption_round_trip(app, caption_as_typed: str):
    draft = await make_awaiting_draft(app)

    ask = [p for p in TG.of("sendMessage") if p.get("text") == texts.ASK_CAPTION]
    assert len(ask) == 1, "ask-for-caption message not sent exactly once"
    ask_buttons = ask[0]["reply_markup"]["inline_keyboard"]
    assert [b["text"] for row in ask_buttons for b in row] == [texts.BTN_CANCEL]
    assert draft.status == "awaiting_ig_caption"

    await send_text(app, caption_as_typed)
    expected = caption_as_typed.strip()
    assert draft.status == "review"
    assert draft.ig_caption == expected

    review = [p for p in TG.of("sendMessage") if p.get("parse_mode") == "HTML"]
    assert len(review) == 1, "review message not sent"
    review_text = review[0]["text"]
    assert f"<blockquote>{html.escape(expected)}</blockquote>" in review_text, "caption not shown escaped in review"
    assert f"{len(expected)} characters" in review_text
    assert "AI website summary" in review_text, "summary missing from review"
    buttons = [b["text"] for row in review[0]["reply_markup"]["inline_keyboard"] for b in row]
    assert buttons == [texts.BTN_GO_LIVE, texts.BTN_REGEN_SUMMARY, texts.BTN_EDIT_CAPTION, texts.BTN_CANCEL], buttons

    await tap(app, f"pub_golive:{draft.id}", draft.review_message_id)
    assert len(PAYLOADS.instagram) == 1 and len(PAYLOADS.website) == 1
    posted = PAYLOADS.instagram[0]["title"]
    assert posted == expected, f"posted caption differs:\n{posted!r}\n!=\n{expected!r}"
    assert AI_CAPTION not in posted and "#ai" not in posted
    assert PAYLOADS.website[0]["data"]["summary"].startswith("AI website summary")
    assert state.get_draft(draft.id) is None, "draft not cleaned up after posting"
    return posted, expected


async def test_1_normal_path(app):
    posted, expected = await _caption_round_trip(app, "  Line one\nLine two\n\nLine four  ")
    assert posted == "Line one\nLine two\n\nLine four"
    # The website summary is still generated from the headline (+ the internal AI caption, as before).
    assert SUMMARY_CALLS[0]["caption"] == AI_CAPTION
    assert SUMMARY_CALLS[0]["headline"] == "Messi is back & better"


async def test_2_special_characters(app):
    caption = (
        "🔥🐐 मेसी वापस आ गया है!\n"
        "Big night for @leomessi & @intermiami <3 > everyone\n"
        "snake_case *stars* _under_ <b>not bold</b> &amp; literal\n\n"
        "https://pedtalkssports.com/news?id=1&x=2\n"
        "#Messi #फुटबॉल #MLS"
    )
    posted, expected = await _caption_round_trip(app, caption)
    assert posted == caption, "special characters altered"
    assert posted.encode("utf-8") == caption.encode("utf-8")


async def test_3_validation(app):
    draft = await make_awaiting_draft(app)

    await send_text(app, "   \n  ")
    assert TG.texts_sent()[-1] == texts.CAPTION_EMPTY and draft.status == "awaiting_ig_caption"

    too_long = "x" * (config.IG_CAPTION_MAX_CHARS + 1)
    await send_text(app, too_long)
    assert TG.texts_sent()[-1] == texts.CAPTION_TOO_LONG.format(count=len(too_long), limit=config.IG_CAPTION_MAX_CHARS)
    assert draft.status == "awaiting_ig_caption"

    exactly_max = "y" * config.IG_CAPTION_MAX_CHARS  # boundary - accepted later below

    too_many_tags = " ".join(f"#tag{i}" for i in range(config.IG_CAPTION_MAX_HASHTAGS + 1))
    await send_text(app, too_many_tags)
    assert TG.texts_sent()[-1] == texts.CAPTION_TOO_MANY_HASHTAGS.format(
        count=config.IG_CAPTION_MAX_HASHTAGS + 1, limit=config.IG_CAPTION_MAX_HASHTAGS
    )

    too_many_mentions = " ".join(f"@user{i}" for i in range(config.IG_CAPTION_MAX_MENTIONS + 1))
    await send_text(app, too_many_mentions)
    assert TG.texts_sent()[-1] == texts.CAPTION_TOO_MANY_MENTIONS.format(
        count=config.IG_CAPTION_MAX_MENTIONS + 1, limit=config.IG_CAPTION_MAX_MENTIONS
    )

    await send_voice(app)
    assert TG.texts_sent()[-1] == texts.CAPTION_NOT_TEXT and draft.status == "awaiting_ig_caption"

    await send_photo(app)
    assert TG.texts_sent()[-1] == texts.STILL_WAITING_FOR_CAPTION and draft.status == "awaiting_ig_caption"

    await send_edited_text(app, "an edited old message")
    assert draft.status == "awaiting_ig_caption" and draft.ig_caption is None, "edit was treated as a caption"
    assert "Should the text panel go" not in TG.texts_sent()[-1], "edit started a new headline"

    exactly_30_tags = " ".join(f"#t{i}" for i in range(config.IG_CAPTION_MAX_HASHTAGS))
    assert publish_mod.validation_error(exactly_30_tags) is None
    await send_text(app, exactly_max)
    assert draft.status == "review" and draft.ig_caption == exactly_max
    assert not PAYLOADS.instagram, "nothing should have been posted"


async def test_4_cancel_while_waiting(app):
    draft = await make_awaiting_draft(app)
    await send_text(app, "/cancel")
    assert state.get_draft(draft.id) is None and not draft.image_path.exists()
    assert TG.texts_sent()[-1] == texts.CANCELLED
    await send_text(app, "Next headline")  # no longer claimed as a caption
    assert "left or the right" in TG.texts_sent()[-1]
    await send_text(app, "/cancel")  # leave the headline conversation

    draft = await make_awaiting_draft(app)
    await tap(app, f"pub_abort:{draft.id}", draft.ask_message_id)
    assert state.get_draft(draft.id) is None and not draft.image_path.exists()
    edits = [p for p in TG.of("editMessageText") if int(p["message_id"]) == draft.ask_message_id]
    assert edits and edits[-1]["text"] == texts.CANCELLED
    assert not PAYLOADS.instagram


async def test_5_second_image_while_waiting(app):
    draft = await make_awaiting_draft(app)
    generations = len(IMAGE_CALLS)
    await send_photo(app)
    last = TG.of("sendMessage")[-1]
    assert last["text"] == texts.STILL_WAITING_FOR_CAPTION
    assert last["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"pub_abort:{draft.id}"
    assert len(IMAGE_CALLS) == generations, "a second generation started"
    assert len(state.all_drafts()) == 1


async def test_6_restart_while_waiting(app):
    draft = await make_awaiting_draft(app)
    draft_id = draft.id
    assert state.PUBLISH_DRAFTS_FILE.exists()
    await app.shutdown()

    state._drafts.clear()  # simulate the process dying: in-memory state gone
    app2 = await new_app()
    restored = state.get_draft(draft_id)
    assert restored is not None and restored.status == "awaiting_ig_caption"

    await send_text(app2, "Caption after restart ✅")
    assert restored.status == "review" and restored.ig_caption == "Caption after restart ✅"
    await tap(app2, f"pub_golive:{draft_id}", restored.review_message_id)
    assert PAYLOADS.instagram[-1]["title"] == "Caption after restart ✅"
    return app2


async def test_7_edit_flow(app):
    draft = await make_awaiting_draft(app)
    await send_text(app, "First caption")
    review_id = draft.review_message_id

    await tap(app, f"pub_edit:{draft.id}", review_id)
    assert draft.status == "awaiting_ig_caption"
    assert TG.texts_sent()[-1] == texts.ASK_NEW_CAPTION
    await tap(app, f"pub_golive:{draft.id}", review_id)  # must not post while editing
    assert not PAYLOADS.instagram

    await send_text(app, "Second caption\nwith a new line")
    assert draft.ig_caption == "Second caption\nwith a new line" and draft.review_message_id == review_id
    review_edits = [p for p in TG.of("editMessageText") if int(p["message_id"]) == review_id]
    assert html.escape("Second caption\nwith a new line") in review_edits[-1]["text"]
    assert texts.REVIEW_NOTE_CAPTION_UPDATED in review_edits[-1]["text"]
    assert len([p for p in TG.of("sendMessage") if p.get("parse_mode") == "HTML"]) == 1, "review should be edited, not resent"

    await tap(app, f"pub_golive:{draft.id}", review_id)
    assert PAYLOADS.instagram[-1]["title"] == "Second caption\nwith a new line"


async def test_8_instagram_failure_then_retry(app):
    real_upload = publish_mod.upload_photo_to_instagram
    seen_captions = []

    async def flaky_upload(image_path, caption):
        seen_captions.append(caption)
        if len(seen_captions) == 1:
            raise UploadPostError("simulated Instagram outage")
        return await real_upload(image_path, caption)

    publish_mod.upload_photo_to_instagram = flaky_upload
    try:
        draft = await make_awaiting_draft(app)
        await send_text(app, "Retry me #once")
        await tap(app, f"pub_golive:{draft.id}", draft.review_message_id)
        assert draft.status == "review" and draft.website_posted and not draft.instagram_posted
        assert len(PAYLOADS.website) == 1 and not PAYLOADS.instagram
        note = [p for p in TG.of("editMessageText") if int(p["message_id"]) == draft.review_message_id][-1]["text"]
        assert "simulated Instagram outage" in note

        summary_calls = len(SUMMARY_CALLS)
        await tap(app, f"pub_golive:{draft.id}", draft.review_message_id)
        assert seen_captions == ["Retry me #once", "Retry me #once"]
        assert PAYLOADS.instagram[-1]["title"] == "Retry me #once"
        assert len(PAYLOADS.website) == 1, "website re-posted on Instagram retry"
        assert len(SUMMARY_CALLS) == summary_calls, "summary regenerated on retry"
        assert state.get_draft(draft.id) is None
    finally:
        publish_mod.upload_photo_to_instagram = real_upload


async def test_9_mandatory_guard(app):
    image = _TMP / "guard.png"
    image.write_bytes(PNG)
    for bad in (None, "", "   \n "):
        try:
            await upload_photo_to_instagram(image, bad)
        except UploadPostError as exc:
            assert "no manual caption" in str(exc)
        else:
            raise AssertionError(f"Instagram publish accepted caption {bad!r}")
    try:
        await upload_photo_to_instagram(image, "z" * (config.IG_CAPTION_MAX_CHARS + 1))
    except UploadPostError:
        pass
    else:
        raise AssertionError("over-limit caption accepted by the posting layer")

    # A draft that somehow reaches publishing without a manual caption: Instagram refused,
    # the website still goes out independently.
    draft = state.create_draft(image, "Guard headline", CHAT_ID, USER_ID)
    draft.summary, draft.summary_state, draft.status = "Summary", "ready", "posting"
    draft.review_message_id = TG.new_id()
    await publish_mod._do_publish(app.bot, draft)
    assert not draft.instagram_posted and draft.website_posted
    assert not PAYLOADS.instagram, "an Instagram payload went out without a manual caption"


def test_10_untouched_files():
    paths = ["bot/generation", "bot/website/summary_generator.py", "bot/go_live/caption_generator.py"]
    result = subprocess.run(["git", "diff", "--stat", "main", "--", *paths], cwd=REPO_ROOT, capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.strip() == "", f"changed: {result.stdout}"
    status = subprocess.run(["git", "status", "--porcelain", "--", *paths], cwd=REPO_ROOT, capture_output=True, text=True)
    assert status.stdout.strip() == "", f"uncommitted changes: {status.stdout}"


async def test_11_headline_without_waiting_draft(app):
    assert not state.awaiting_caption_drafts(CHAT_ID, USER_ID)
    await send_text(app, "A brand new **headline**")
    last = TG.of("sendMessage")[-1]
    assert last["text"] == "Got it. Should the text panel go on the left or the right?"
    assert last["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "layout:left"
    await send_text(app, "/cancel")


async def main() -> int:
    app = await new_app()
    tests = [
        test_1_normal_path, test_2_special_characters, test_3_validation, test_4_cancel_while_waiting,
        test_5_second_image_while_waiting, test_6_restart_while_waiting, test_7_edit_flow,
        test_8_instagram_failure_then_retry, test_9_mandatory_guard, test_10_untouched_files,
        test_11_headline_without_waiting_draft,
    ]
    failures = 0
    for test in tests:
        reset_world()
        try:
            result = test() if not inspect.iscoroutinefunction(test) else await test(app)
            if test is test_6_restart_while_waiting:
                app = result
            print(f"PASS  {test.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL  {test.__name__}: {type(exc).__name__}: {exc}")
            if test is test_6_restart_while_waiting and not app.running:
                app = await new_app()
    await app.shutdown()
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
