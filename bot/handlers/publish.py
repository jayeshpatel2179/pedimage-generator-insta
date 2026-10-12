import asyncio
import html
import logging
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ApplicationHandlerStop, ContextTypes

from bot.config import GO_LIVE_ALLOWED_USER_IDS, WEBSITE_SUMMARY_HISTORY_LIMIT
from bot.go_live.caption_generator import generate_caption
from bot.go_live.ig_caption import validation_error
from bot.go_live.uploadpost_client import upload_photo_to_instagram
from bot.publish import state, texts
from bot.utils.image_utils import cleanup
from bot.website.summary_generator import generate_website_summary
from bot.website.website_client import upload_photo_to_website

logger = logging.getLogger(__name__)


def _is_allowed(user_id: int) -> bool:
    # Empty allowlist = unrestricted, see GO_LIVE_ALLOWED_USER_IDS in config.py.
    return not GO_LIVE_ALLOWED_USER_IDS or user_id in GO_LIVE_ALLOWED_USER_IDS


def _cancel_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(texts.BTN_CANCEL, callback_data=f"pub_abort:{draft_id}")]])


def _nothing_posted(draft: state.PublishDraft) -> bool:
    return not (draft.instagram_posted or draft.website_posted)


def _review_keyboard(draft: state.PublishDraft) -> Optional[InlineKeyboardMarkup]:
    if draft.status == "posting":
        return InlineKeyboardMarkup([[InlineKeyboardButton(texts.BTN_POSTING, callback_data=f"pub_golive:{draft.id}")]])
    if draft.status == "awaiting_ig_caption":
        return _cancel_keyboard(draft.id) if _nothing_posted(draft) else None

    rows = []
    if draft.summary_state == "ready":
        rows.append(
            [
                InlineKeyboardButton(texts.BTN_GO_LIVE, callback_data=f"pub_golive:{draft.id}"),
                InlineKeyboardButton(texts.BTN_REGEN_SUMMARY, callback_data=f"pub_regen_summary:{draft.id}"),
            ]
        )
    elif draft.summary_state == "failed":
        rows.append([InlineKeyboardButton(texts.BTN_RETRY_SUMMARY, callback_data=f"pub_regen_summary:{draft.id}")])
    else:
        rows.append([InlineKeyboardButton(texts.BTN_WORKING, callback_data=f"pub_regen_summary:{draft.id}")])

    second_row = []
    if not draft.instagram_posted:
        second_row.append(InlineKeyboardButton(texts.BTN_EDIT_CAPTION, callback_data=f"pub_edit:{draft.id}"))
    if _nothing_posted(draft):
        second_row.append(InlineKeyboardButton(texts.BTN_CANCEL, callback_data=f"pub_abort:{draft.id}"))
    if second_row:
        rows.append(second_row)
    return InlineKeyboardMarkup(rows)


def _review_text(draft: state.PublishDraft, note: Optional[str] = None) -> str:
    """HTML review message. Every user/AI-supplied string is escaped - escaping only affects how
    Telegram displays it, draft.ig_caption itself (what gets posted) is never touched."""
    parts = []
    if note:
        parts.append(html.escape(note))
    parts.append(texts.REVIEW_TITLE)

    if draft.status == "awaiting_ig_caption" or draft.ig_caption is None:
        parts.append(texts.REVIEW_CAPTION_WAITING)
    else:
        parts.append(
            texts.REVIEW_CAPTION_HEADER.format(count=len(draft.ig_caption))
            + f"\n<blockquote>{html.escape(draft.ig_caption)}</blockquote>"
        )

    if draft.summary_state == "ready" and draft.summary:
        summary_part = f"<blockquote>{html.escape(draft.summary)}</blockquote>"
    elif draft.summary_state == "failed":
        summary_part = texts.REVIEW_SUMMARY_FAILED
    else:
        summary_part = texts.REVIEW_SUMMARY_PENDING
    parts.append(f"{texts.REVIEW_SUMMARY_HEADER}\n{summary_part}")

    if draft.status == "posting":
        parts.append(texts.REVIEW_POSTING)
    return "\n\n".join(parts)


async def _safe_edit(bot, chat_id: int, message_id: Optional[int], text: str, reply_markup=None, parse_mode=None) -> None:
    if message_id is None:
        return
    try:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode
        )
    except BadRequest as exc:
        # "Message is not modified" / "message to edit not found" - nothing useful to do.
        logger.info("Couldn't edit message %s in chat %s: %s", message_id, chat_id, exc)


async def _refresh_review(bot, draft: state.PublishDraft, note: Optional[str] = None) -> None:
    await _safe_edit(
        bot, draft.chat_id, draft.review_message_id, _review_text(draft, note), _review_keyboard(draft), ParseMode.HTML
    )


async def _close_draft_messages(bot, draft: state.PublishDraft, text: str, skip_message_id: Optional[int] = None) -> None:
    """Replaces the draft's prompt/review messages with a final line so no stale buttons remain."""
    for message_id in {draft.ask_message_id, draft.review_message_id}:
        if message_id is not None and message_id != skip_message_id:
            await _safe_edit(bot, draft.chat_id, message_id, text)


async def _on_draft_expired(application, draft: state.PublishDraft) -> None:
    await _close_draft_messages(application.bot, draft, texts.EXPIRED)


def _schedule_expiry(application, draft: state.PublishDraft) -> None:
    state.schedule_expiry_cleanup(application, draft.id, lambda d: _on_draft_expired(application, d))


async def begin_publish_flow(context: ContextTypes.DEFAULT_TYPE, draft: state.PublishDraft) -> None:
    """Runs right after the poster image is sent: asks for the (mandatory, manual) Instagram
    caption and, meanwhile, generates the website summary in the background."""
    _schedule_expiry(context.application, draft)

    ask_msg = await context.bot.send_message(
        chat_id=draft.chat_id,
        text=texts.ASK_CAPTION,
        reply_to_message_id=draft.image_message_id,
        reply_markup=_cancel_keyboard(draft.id),
    )
    async with draft.lock:
        draft.ask_message_id = ask_msg.message_id
        draft.status = "awaiting_ig_caption"
        state.save()

    context.application.create_task(_produce_summary(context.bot, draft, is_regeneration=False))


async def _produce_summary(bot, draft: state.PublishDraft, *, is_regeneration: bool) -> None:
    # Same two calls as before the manual-caption change: the AI caption is still generated
    # because the website summary prompt is grounded on it - it's an input only and is never
    # shown or sent to Instagram (that's draft.ig_caption, typed by the editor).
    try:
        if draft.ai_caption is None:
            ai_caption = await generate_caption(draft.headline, draft.image_path)
            async with draft.lock:
                draft.ai_caption = ai_caption
        summary = await generate_website_summary(
            draft.image_path,
            draft.headline,
            draft.ai_caption,
            is_regeneration=is_regeneration,
            history=draft.summary_history[-WEBSITE_SUMMARY_HISTORY_LIMIT:],
        )
    except Exception:
        logger.exception("Website summary generation failed for draft %s", draft.id)
        async with draft.lock:
            if not state.is_live(draft):
                return
            had_previous = draft.summary is not None
            draft.summary_state = "ready" if had_previous else "failed"
            state.save()
        await _refresh_review(bot, draft, texts.REVIEW_NOTE_SUMMARY_REGEN_FAILED if had_previous else None)
        return

    async with draft.lock:
        if not state.is_live(draft):
            return
        draft.summary = summary
        draft.summary_history.append(summary)
        draft.summary_state = "ready"
        state.save()
    await _refresh_review(bot, draft)


def _reply_target_id(message) -> Optional[int]:
    return message.reply_to_message.message_id if message.reply_to_message else None


async def on_caption_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Claims the next text message from a user who has a draft awaiting its Instagram caption.

    Registered ahead of the headline ConversationHandler (group -1) so the caption is never
    mistaken for a new headline. Commands aren't routed here, so /cancel, /start and /help keep
    working. Falls through (no ApplicationHandlerStop) when there's no draft waiting.
    """
    message = update.effective_message
    user = update.effective_user
    if user is None or message.text is None:
        return
    draft = state.find_awaiting_caption_draft(message.chat_id, user.id, _reply_target_id(message))
    if draft is None:
        return

    # Only leading/trailing whitespace is trimmed - everything else is kept byte-for-byte.
    caption = message.text.strip()

    async with draft.lock:
        if not state.is_live(draft) or draft.status != "awaiting_ig_caption":
            return
        if state.is_expired(draft):
            state.pop_draft(draft.id)
            cleanup(draft.image_path)
            await message.reply_text(texts.EXPIRED)
            await _close_draft_messages(context.bot, draft, texts.EXPIRED)
            raise ApplicationHandlerStop

        error = validation_error(caption)
        if error:
            await message.reply_text(error)
            raise ApplicationHandlerStop

        is_edit = draft.ig_caption is not None
        draft.ig_caption = caption
        draft.status = "review"
        state.save()

    await _safe_edit(context.bot, draft.chat_id, draft.ask_message_id, texts.ASK_CAPTION_ANSWERED)

    if draft.review_message_id is not None:
        await _refresh_review(context.bot, draft, texts.REVIEW_NOTE_CAPTION_UPDATED if is_edit else None)
    else:
        review_msg = await context.bot.send_message(
            chat_id=draft.chat_id,
            text=_review_text(draft),
            parse_mode=ParseMode.HTML,
            reply_markup=_review_keyboard(draft),
            reply_to_message_id=draft.image_message_id,
        )
        async with draft.lock:
            draft.review_message_id = review_msg.message_id
            state.save()
    raise ApplicationHandlerStop


async def on_non_text_while_awaiting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A photo/voice/sticker/file from a user whose draft is awaiting its caption. A new image
    never starts a second draft while one is waiting."""
    message = update.effective_message
    user = update.effective_user
    if user is None:
        return
    draft = state.find_awaiting_caption_draft(message.chat_id, user.id, _reply_target_id(message))
    if draft is None:
        return

    is_image = bool(message.photo) or bool(
        message.document and (message.document.mime_type or "").startswith("image/")
    )
    if is_image:
        await message.reply_text(texts.STILL_WAITING_FOR_CAPTION, reply_markup=_cancel_keyboard(draft.id))
    else:
        await message.reply_text(texts.CAPTION_NOT_TEXT)
    raise ApplicationHandlerStop


async def on_edited_while_awaiting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Edits are ignored while a caption is awaited - otherwise an edited earlier message would
    fall through to the headline handler and start a new graphic."""
    message = update.effective_message
    user = update.effective_user
    if user is not None and state.awaiting_caption_drafts(message.chat_id, user.id):
        raise ApplicationHandlerStop


async def on_edit_caption_tap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    draft_id = query.data.split(":", 1)[1]

    if not _is_allowed(user.id):
        await query.answer(texts.NOT_AUTHORIZED, show_alert=True)
        return

    draft = state.get_draft(draft_id)
    if draft is None:
        await query.answer(texts.EXPIRED, show_alert=True)
        return

    async with draft.lock:
        if state.is_expired(draft):
            await query.answer()
            state.pop_draft(draft.id)
            cleanup(draft.image_path)
            await query.edit_message_text(texts.EXPIRED)
            return
        if draft.instagram_posted:
            await query.answer("Already posted to Instagram - the caption can't change now.", show_alert=True)
            return
        if draft.status != "review":
            await query.answer("Hang on, that's already in progress.", show_alert=False)
            return

        # Whoever tapped Edit types the replacement.
        draft.user_id = user.id
        draft.status = "awaiting_ig_caption"
        state.save()
        await query.answer()

    await _refresh_review(context.bot, draft)
    ask_msg = await context.bot.send_message(
        chat_id=draft.chat_id,
        text=texts.ASK_NEW_CAPTION,
        reply_to_message_id=draft.review_message_id,
        reply_markup=_cancel_keyboard(draft.id),
    )
    async with draft.lock:
        draft.ask_message_id = ask_msg.message_id
        state.save()


async def on_regen_summary_tap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    draft_id = query.data.split(":", 1)[1]

    if not _is_allowed(user.id):
        await query.answer(texts.NOT_AUTHORIZED, show_alert=True)
        return

    draft = state.get_draft(draft_id)
    if draft is None:
        await query.answer(texts.EXPIRED, show_alert=True)
        return

    async with draft.lock:
        if state.is_expired(draft):
            await query.answer()
            state.pop_draft(draft.id)
            cleanup(draft.image_path)
            await query.edit_message_text(texts.EXPIRED)
            return

        if draft.status != "review" or draft.summary_state not in ("ready", "failed"):
            await query.answer("Hang on, that's already in progress.", show_alert=False)
            return

        had_previous = draft.summary_state == "ready"
        draft.summary_state = "regenerating" if had_previous else "pending"
        state.save()
        await query.answer()

    await _refresh_review(context.bot, draft)
    context.application.create_task(_produce_summary(context.bot, draft, is_regeneration=had_previous))


async def on_golive_tap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    draft_id = query.data.split(":", 1)[1]

    if not _is_allowed(user.id):
        await query.answer(texts.NOT_AUTHORIZED, show_alert=True)
        return

    draft = state.get_draft(draft_id)
    if draft is None:
        await query.answer(texts.EXPIRED, show_alert=True)
        return

    async with draft.lock:
        if draft.status == "posting":
            await query.answer("Already posting, hang tight...", show_alert=False)
            return
        if draft.status == "posted":
            await query.answer("Already posted.", show_alert=False)
            return
        if state.is_expired(draft):
            await query.answer()
            state.pop_draft(draft.id)
            cleanup(draft.image_path)
            await query.edit_message_text(texts.EXPIRED)
            return
        if draft.status != "review" or draft.summary_state != "ready":
            await query.answer("Hang on, still preparing the summary.", show_alert=False)
            return
        if not draft.ig_caption:
            await query.answer("Send the Instagram caption first.", show_alert=True)
            return

        draft.status = "posting"
        state.save()
        await query.answer()

    await _refresh_review(context.bot, draft)
    context.application.create_task(_do_publish(context.bot, draft))


async def _do_publish(bot, draft: state.PublishDraft) -> None:
    # Only (re)posts to platforms not already confirmed live, so retrying after a partial
    # failure (one platform succeeded, the other errored) never double-posts. A retry reuses
    # the same stored manual caption and AI summary.
    jobs: dict[str, asyncio.Task] = {}
    if not draft.instagram_posted:
        jobs["instagram"] = asyncio.create_task(upload_photo_to_instagram(draft.image_path, draft.ig_caption))
    if not draft.website_posted:
        jobs["website"] = asyncio.create_task(upload_photo_to_website(draft.image_path, draft.summary))

    results = await asyncio.gather(*jobs.values(), return_exceptions=True)
    outcomes = dict(zip(jobs.keys(), results))

    errors = []
    async with draft.lock:
        if "instagram" in outcomes:
            result = outcomes["instagram"]
            if isinstance(result, Exception):
                logger.error("Instagram publish failed for draft %s: %s", draft.id, result)
                errors.append(f"Instagram: {result}")
            else:
                draft.instagram_posted = True
                draft.instagram_url = result
        if "website" in outcomes:
            result = outcomes["website"]
            if isinstance(result, Exception):
                logger.error("Website publish failed for draft %s: %s", draft.id, result)
                errors.append(f"Website: {result}")
            else:
                draft.website_posted = True

        draft.status = "review" if errors else "posted"
        state.save()

    if errors:
        note_lines = ["⚠️ Posting failed for: " + "; ".join(errors)]
        if draft.instagram_posted:
            note_lines.append("✅ Instagram already posted - Go Live again will only retry the website.")
        if draft.website_posted:
            note_lines.append("✅ Website already posted - Go Live again will only retry Instagram.")
        await _refresh_review(bot, draft, "\n".join(note_lines))
        return

    lines = [texts.POSTED]
    if draft.instagram_url:
        lines.append(f"Instagram: {draft.instagram_url}")
    await _safe_edit(bot, draft.chat_id, draft.review_message_id, "\n".join(lines))

    cleanup(draft.image_path)
    state.pop_draft(draft.id)


async def on_abort_tap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    draft_id = query.data.split(":", 1)[1]

    if not _is_allowed(user.id):
        await query.answer(texts.NOT_AUTHORIZED, show_alert=True)
        return

    draft = state.get_draft(draft_id)
    if draft is None:
        await query.answer(texts.EXPIRED, show_alert=True)
        return

    async with draft.lock:
        if draft.status == "posting":
            await query.answer("Already posting - can't cancel now.", show_alert=False)
            return
        if draft.status == "posted":
            await query.answer("Already posted.", show_alert=False)
            return
        if not _nothing_posted(draft):
            posted_where = " and ".join(
                p for p, ok in (("Instagram", draft.instagram_posted), ("the website", draft.website_posted)) if ok
            )
            await query.answer(f"Already posted to {posted_where} - can't cancel, only retry the rest.", show_alert=True)
            return
        if draft.status not in ("awaiting_ig_caption", "review"):
            await query.answer("Hang on, still working on that.", show_alert=False)
            return

        state.pop_draft(draft.id)
        cleanup(draft.image_path)
        await query.answer()

    await query.edit_message_text(texts.CANCELLED)
    await _close_draft_messages(context.bot, draft, texts.CANCELLED, skip_message_id=query.message.message_id)


async def cancel_awaiting_drafts(bot, chat_id: int, user_id: int) -> int:
    """/cancel: discards this user's drafts that are waiting for their caption. Returns how many."""
    cancelled = 0
    for draft in state.awaiting_caption_drafts(chat_id, user_id):
        async with draft.lock:
            if not state.is_live(draft) or draft.status != "awaiting_ig_caption" or not _nothing_posted(draft):
                continue
            state.pop_draft(draft.id)
            cleanup(draft.image_path)
        await _close_draft_messages(bot, draft, texts.CANCELLED)
        cancelled += 1
    return cancelled


async def restore_after_restart(application) -> None:
    """post_init hook: reloads persisted drafts, re-arms their expiry timers, and refreshes the
    review of any draft whose in-flight work was interrupted by the restart."""
    for draft, note in state.load():
        note_text = texts.REVIEW_NOTE_RESTARTED_WHILE_POSTING if note == "restarted_while_posting" else None
        await _refresh_review(application.bot, draft, note_text)
    for draft in state.all_drafts():
        _schedule_expiry(application, draft)
