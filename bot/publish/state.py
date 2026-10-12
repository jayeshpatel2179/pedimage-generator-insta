import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from bot.config import PUBLISH_DRAFT_EXPIRY_SECONDS, PUBLISH_DRAFTS_FILE

logger = logging.getLogger(__name__)


@dataclass
class PublishDraft:
    """One generated image moving through the publish pipeline:
    manual Instagram caption (typed by the editor) + AI website summary (generated in the
    background meanwhile) -> one review -> simultaneous Instagram + website Go Live.

    Persisted to PUBLISH_DRAFTS_FILE on every change (see save()/load()) so a draft awaiting
    its caption or sitting in review survives a bot restart.
    """

    id: str
    image_path: Path
    headline: str
    chat_id: int
    # Who generated the image - their next text message in this chat is claimed as the caption.
    user_id: int
    image_message_id: Optional[int] = None
    status: str = "preparing"
    # preparing | awaiting_ig_caption | review | posting | posted

    # The editor's caption, exactly as typed (only leading/trailing whitespace trimmed). The
    # ONLY text ever sent to Instagram.
    ig_caption: Optional[str] = None
    # AI caption generated purely as an input to the website summary (same calls as before
    # the manual-caption change). Never shown, never sent to Instagram.
    ai_caption: Optional[str] = None
    ask_message_id: Optional[int] = None
    review_message_id: Optional[int] = None

    summary: Optional[str] = None
    summary_state: str = "pending"
    # pending | regenerating | ready | failed
    summary_history: list[str] = field(default_factory=list)

    instagram_posted: bool = False
    instagram_url: Optional[str] = None
    website_posted: bool = False

    # Wall-clock (not monotonic) so expiry still works across a restart.
    created_at: float = field(default_factory=time.time)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_PERSISTED_FIELDS = (
    "id", "headline", "chat_id", "user_id", "image_message_id", "status", "ig_caption",
    "ai_caption", "ask_message_id", "review_message_id", "summary", "summary_state",
    "summary_history", "instagram_posted", "instagram_url", "website_posted", "created_at",
)

_drafts: dict[str, PublishDraft] = {}


def create_draft(image_path: Path, headline: str, chat_id: int, user_id: int) -> PublishDraft:
    draft_id = uuid.uuid4().hex[:8]
    while draft_id in _drafts:
        draft_id = uuid.uuid4().hex[:8]
    draft = PublishDraft(id=draft_id, image_path=image_path, headline=headline, chat_id=chat_id, user_id=user_id)
    _drafts[draft_id] = draft
    save()
    return draft


def get_draft(draft_id: str) -> Optional[PublishDraft]:
    return _drafts.get(draft_id)


def all_drafts() -> list[PublishDraft]:
    return list(_drafts.values())


def is_live(draft: PublishDraft) -> bool:
    """False once the draft was cancelled/expired/posted - background work finishing late checks this."""
    return _drafts.get(draft.id) is draft


def is_expired(draft: PublishDraft) -> bool:
    return (time.time() - draft.created_at) > PUBLISH_DRAFT_EXPIRY_SECONDS


def pop_draft(draft_id: str) -> Optional[PublishDraft]:
    draft = _drafts.pop(draft_id, None)
    if draft is not None:
        save()
    return draft


def attach_image_message(draft: PublishDraft, message_id: int) -> None:
    draft.image_message_id = message_id
    save()


def awaiting_caption_drafts(chat_id: int, user_id: int) -> list[PublishDraft]:
    """This user's drafts in this chat that are waiting for their Instagram caption, oldest first."""
    found = [
        d for d in _drafts.values()
        if d.chat_id == chat_id and d.user_id == user_id and d.status == "awaiting_ig_caption"
    ]
    return sorted(found, key=lambda d: d.created_at)


def find_awaiting_caption_draft(chat_id: int, user_id: int, reply_to_message_id: Optional[int] = None) -> Optional[PublishDraft]:
    """The draft a caption message from this user belongs to.

    A reply to one draft's image/prompt/review message targets that draft; otherwise the oldest
    one waiting (normally there's only one - a second can only appear if two generations were
    in flight at once).
    """
    waiting = awaiting_caption_drafts(chat_id, user_id)
    if reply_to_message_id is not None:
        for draft in waiting:
            if reply_to_message_id in (draft.image_message_id, draft.ask_message_id, draft.review_message_id):
                return draft
    return waiting[0] if waiting else None


def save() -> None:
    data = []
    for draft in _drafts.values():
        record = {name: getattr(draft, name) for name in _PERSISTED_FIELDS}
        record["image_path"] = str(draft.image_path)
        data.append(record)

    tmp_path = PUBLISH_DRAFTS_FILE.with_suffix(".tmp")
    try:
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp_path, PUBLISH_DRAFTS_FILE)
    except OSError:
        logger.exception("Couldn't persist publish drafts to %s", PUBLISH_DRAFTS_FILE)


def load() -> list[tuple[PublishDraft, Optional[str]]]:
    """Restores drafts saved before a restart. Returns the drafts whose on-screen review needs
    refreshing because restoring changed their state, each with an optional note to show.

    Background work (summary generation, posting) doesn't survive a restart, so drafts caught
    mid-way are moved back to a state the editor can act on from the buttons.
    """
    _drafts.clear()
    if not PUBLISH_DRAFTS_FILE.exists():
        return []

    try:
        records = json.loads(PUBLISH_DRAFTS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.exception("Couldn't read publish drafts from %s - starting empty", PUBLISH_DRAFTS_FILE)
        return []

    to_refresh: list[tuple[PublishDraft, Optional[str]]] = []
    for record in records:
        image_path = Path(record.pop("image_path"))
        draft = PublishDraft(image_path=image_path, **{k: v for k, v in record.items() if k in _PERSISTED_FIELDS})

        if not image_path.exists() or draft.status in ("preparing", "posted") or is_expired(draft):
            logger.info("Dropping restored publish draft %s (status=%s)", draft.id, draft.status)
            image_path.unlink(missing_ok=True)
            continue

        changed, note = False, None
        if draft.summary_state in ("pending", "regenerating"):
            # Keep a previous summary if there was one (regeneration), otherwise offer Retry.
            draft.summary_state = "ready" if draft.summary else "failed"
            changed = True
        if draft.status == "posting":
            # Unknown outcome - per-platform flags only flip on a confirmed result, so Go Live
            # again still never re-posts to a platform already confirmed.
            draft.status = "review"
            changed, note = True, "restarted_while_posting"

        _drafts[draft.id] = draft
        if changed:
            to_refresh.append((draft, note))

    save()
    logger.info("Restored %d publish draft(s) from %s", len(_drafts), PUBLISH_DRAFTS_FILE)
    return to_refresh


def schedule_expiry_cleanup(application, draft_id: str, on_expire=None) -> None:
    application.create_task(_expire_after_timeout(draft_id, on_expire))


async def _expire_after_timeout(draft_id: str, on_expire) -> None:
    draft = _drafts.get(draft_id)
    if draft is None:
        return
    await asyncio.sleep(max(0.0, draft.created_at + PUBLISH_DRAFT_EXPIRY_SECONDS - time.time()))
    draft = _drafts.get(draft_id)
    if draft is None or draft.status in ("posting", "posted"):
        return
    logger.info("Publish draft %s expired without resolving; cleaning up temp file", draft_id)
    pop_draft(draft_id)
    draft.image_path.unlink(missing_ok=True)
    if on_expire is not None:
        await on_expire(draft)
