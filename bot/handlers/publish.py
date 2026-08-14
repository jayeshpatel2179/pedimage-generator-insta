import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from bot.config import GO_LIVE_ALLOWED_USER_IDS, WEBSITE_SUMMARY_HISTORY_LIMIT
from bot.go_live.caption_generator import generate_caption
from bot.go_live.uploadpost_client import upload_photo_to_instagram
from bot.publish import state
from bot.utils.image_utils import cleanup
from bot.website.summary_generator import SummaryGenerationError, generate_website_summary
from bot.website.website_client import upload_photo_to_website

logger = logging.getLogger(__name__)

_EXPIRED_TEXT = "This draft expired (30 min passed). Regenerate the image to post it."
_CAPTION_FAILURE_TEXT = "Couldn't generate a caption (API error). Tap Retry."
_SUMMARY_FAILURE_TEXT = "Couldn't generate a website summary (API error). Tap Retry."


def _is_allowed(user_id: int) -> bool:
    # Empty allowlist = unrestricted, see GO_LIVE_ALLOWED_USER_IDS in config.py.
    return not GO_LIVE_ALLOWED_USER_IDS or user_id in GO_LIVE_ALLOWED_USER_IDS


def _caption_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Save", callback_data=f"pub_save:{draft_id}"),
                InlineKeyboardButton("\U0001f504 Regenerate", callback_data=f"pub_regen_caption:{draft_id}"),
            ]
        ]
    )


def _caption_busy_keyboard(draft_id: str, label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=f"pub_regen_caption:{draft_id}")]])


def _caption_failed_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("\U0001f504 Retry", callback_data=f"pub_regen_caption:{draft_id}"),
                InlineKeyboardButton("❌ Abort", callback_data=f"pub_abort:{draft_id}"),
            ]
        ]
    )


def _summary_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("\U0001f680 Go Live", callback_data=f"pub_golive:{draft_id}"),
                InlineKeyboardButton("\U0001f504 Regenerate Summary", callback_data=f"pub_regen_summary:{draft_id}"),
            ],
            [InlineKeyboardButton("❌ Abort", callback_data=f"pub_abort:{draft_id}")],
        ]
    )


def _summary_busy_keyboard(draft_id: str, label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=f"pub_regen_summary:{draft_id}")]])


def _summary_failed_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("\U0001f504 Retry", callback_data=f"pub_regen_summary:{draft_id}"),
                InlineKeyboardButton("❌ Abort", callback_data=f"pub_abort:{draft_id}"),
            ]
        ]
    )


def _caption_preview_text(caption: str, note: str | None = None) -> str:
    base = (
        f"Instagram caption preview:\n\n{caption}\n\n"
        "Reply to this message with new text to edit it, or use the buttons below."
    )
    return f"{note}\n\n{base}" if note else base


def _summary_preview_text(summary: str, note: str | None = None) -> str:
    base = f"Website summary preview:\n\n{summary}"
    return f"{note}\n\n{base}" if note else base


async def begin_publish_flow(context: ContextTypes.DEFAULT_TYPE, draft: state.PublishDraft) -> None:
    """Kicks off the merged publish pipeline right after the poster image is generated.

    No manual "Go Live" tap needed here - caption + hashtag generation runs immediately so the
    image and its Instagram copy are reviewed together as one step (see conversation.py).
    """
    status_msg = await context.bot.send_message(
        chat_id=draft.chat_id,
        text="\U0001f916 Generating caption + hashtags...",
        reply_to_message_id=draft.image_message_id,
    )
    state.set_caption_message(draft, status_msg.message_id)
    await _produce_caption(context, draft, status_msg.message_id)


async def _produce_caption(context, draft: state.PublishDraft, message_id: int) -> None:
    try:
        caption = await generate_caption(draft.headline, draft.image_path)
    except Exception:
        logger.exception("Caption generation failed for draft %s", draft.id)
        had_previous = draft.caption is not None
        async with draft.lock:
            draft.status = "awaiting_caption_review" if had_previous else "failed_caption"
        if had_previous:
            await context.bot.edit_message_text(
                chat_id=draft.chat_id,
                message_id=message_id,
                text=_caption_preview_text(draft.caption, note="⚠️ Regeneration failed - showing the previous caption."),
                reply_markup=_caption_keyboard(draft.id),
            )
        else:
            await context.bot.edit_message_text(
                chat_id=draft.chat_id, message_id=message_id, text=_CAPTION_FAILURE_TEXT, reply_markup=_caption_failed_keyboard(draft.id)
            )
        return

    async with draft.lock:
        draft.caption = caption
        draft.status = "awaiting_caption_review"

    await context.bot.edit_message_text(
        chat_id=draft.chat_id, message_id=message_id, text=_caption_preview_text(caption), reply_markup=_caption_keyboard(draft.id)
    )


async def on_regen_caption_tap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    draft_id = query.data.split(":", 1)[1]

    if not _is_allowed(user.id):
        await query.answer("You're not authorized to publish.", show_alert=True)
        return

    draft = state.get_draft(draft_id)
    if draft is None:
        await query.answer(_EXPIRED_TEXT, show_alert=True)
        return

    async with draft.lock:
        if state.is_expired(draft):
            await query.answer()
            state.pop_draft(draft.id)
            cleanup(draft.image_path)
            await query.edit_message_text(_EXPIRED_TEXT)
            return

        if draft.status not in ("awaiting_caption_review", "failed_caption"):
            await query.answer("Hang on, that's already in progress.", show_alert=False)
            return

        had_previous = draft.status == "awaiting_caption_review"
        draft.status = "regenerating_caption"
        await query.answer()
        await query.edit_message_text(
            "\U0001f504 Regenerating..." if had_previous else "\U0001f504 Retrying...",
            reply_markup=_caption_busy_keyboard(draft.id, "\U0001f504 Working..."),
        )

    context.application.create_task(_produce_caption(context, draft, draft.caption_message_id))


async def on_save_tap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    draft_id = query.data.split(":", 1)[1]

    if not _is_allowed(user.id):
        await query.answer("You're not authorized to publish.", show_alert=True)
        return

    draft = state.get_draft(draft_id)
    if draft is None:
        await query.answer(_EXPIRED_TEXT, show_alert=True)
        return

    async with draft.lock:
        if state.is_expired(draft):
            await query.answer()
            state.pop_draft(draft.id)
            cleanup(draft.image_path)
            await query.edit_message_text(_EXPIRED_TEXT)
            return

        if draft.status != "awaiting_caption_review":
            await query.answer("Hang on, that's already in progress.", show_alert=False)
            return

        draft.status = "generating_summary"
        await query.answer()
        await query.edit_message_text(f"✅ Caption saved:\n\n{draft.caption}")

    status_msg = await context.bot.send_message(
        chat_id=draft.chat_id,
        text="\U0001f916 Generating website summary...",
        reply_to_message_id=draft.caption_message_id,
    )
    draft.summary_message_id = status_msg.message_id
    await _produce_summary(context, draft, is_regeneration=False)


async def _produce_summary(context, draft: state.PublishDraft, *, is_regeneration: bool) -> None:
    try:
        summary = await generate_website_summary(
            draft.image_path,
            draft.headline,
            draft.caption,
            is_regeneration=is_regeneration,
            history=draft.summary_history[-WEBSITE_SUMMARY_HISTORY_LIMIT:],
        )
    except SummaryGenerationError:
        logger.exception("Website summary generation failed for draft %s", draft.id)
        had_previous = draft.summary is not None
        async with draft.lock:
            draft.status = "awaiting_summary_review" if had_previous else "failed_summary"
        if had_previous:
            await context.bot.edit_message_text(
                chat_id=draft.chat_id,
                message_id=draft.summary_message_id,
                text=_summary_preview_text(draft.summary, note="⚠️ Regeneration failed - showing the previous summary."),
                reply_markup=_summary_keyboard(draft.id),
            )
        else:
            await context.bot.edit_message_text(
                chat_id=draft.chat_id, message_id=draft.summary_message_id, text=_SUMMARY_FAILURE_TEXT, reply_markup=_summary_failed_keyboard(draft.id)
            )
        return

    async with draft.lock:
        draft.summary = summary
        draft.summary_history.append(summary)
        draft.status = "awaiting_summary_review"

    await context.bot.edit_message_text(
        chat_id=draft.chat_id, message_id=draft.summary_message_id, text=_summary_preview_text(summary), reply_markup=_summary_keyboard(draft.id)
    )


async def on_regen_summary_tap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    draft_id = query.data.split(":", 1)[1]

    if not _is_allowed(user.id):
        await query.answer("You're not authorized to publish.", show_alert=True)
        return

    draft = state.get_draft(draft_id)
    if draft is None:
        await query.answer(_EXPIRED_TEXT, show_alert=True)
        return

    async with draft.lock:
        if state.is_expired(draft):
            await query.answer()
            state.pop_draft(draft.id)
            cleanup(draft.image_path)
            await query.edit_message_text(_EXPIRED_TEXT)
            return

        if draft.status not in ("awaiting_summary_review", "failed_summary"):
            await query.answer("Hang on, that's already in progress.", show_alert=False)
            return

        had_previous = draft.status == "awaiting_summary_review"
        draft.status = "regenerating_summary"
        await query.answer()
        await query.edit_message_text(
            "\U0001f504 Rewriting..." if had_previous else "\U0001f504 Retrying...",
            reply_markup=_summary_busy_keyboard(draft.id, "\U0001f504 Working..."),
        )

    context.application.create_task(_produce_summary(context, draft, is_regeneration=had_previous))


async def on_golive_tap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    draft_id = query.data.split(":", 1)[1]

    if not _is_allowed(user.id):
        await query.answer("You're not authorized to publish.", show_alert=True)
        return

    draft = state.get_draft(draft_id)
    if draft is None:
        await query.answer(_EXPIRED_TEXT, show_alert=True)
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
            await query.edit_message_text(_EXPIRED_TEXT)
            return
        if draft.status != "awaiting_summary_review":
            await query.answer("Hang on, still preparing the summary.", show_alert=False)
            return

        draft.status = "posting"
        await query.answer()
        await query.edit_message_text(
            f"Posting to Instagram + website...\n\n{draft.summary}",
            reply_markup=_summary_busy_keyboard(draft.id, "⏳ Posting..."),
        )

    context.application.create_task(_do_publish(context, draft))


async def _do_publish(context, draft: state.PublishDraft) -> None:
    # Only (re)posts to platforms not already confirmed live, so retrying after a partial
    # failure (one platform succeeded, the other errored) never double-posts.
    jobs: dict[str, asyncio.Task] = {}
    if not draft.instagram_posted:
        jobs["instagram"] = asyncio.create_task(upload_photo_to_instagram(draft.image_path, draft.caption))
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

        draft.status = "awaiting_summary_review" if errors else "posted"

    if errors:
        note_lines = ["⚠️ Posting failed for: " + "; ".join(errors)]
        if draft.instagram_posted:
            note_lines.append("✅ Instagram already posted - Go Live again will only retry the website.")
        if draft.website_posted:
            note_lines.append("✅ Website already posted - Go Live again will only retry Instagram.")
        await context.bot.edit_message_text(
            chat_id=draft.chat_id,
            message_id=draft.summary_message_id,
            text=_summary_preview_text(draft.summary, note="\n".join(note_lines)),
            reply_markup=_summary_keyboard(draft.id),
        )
        return

    lines = ["✅ Posted to Instagram and the website!"]
    if draft.instagram_url:
        lines.append(f"Instagram: {draft.instagram_url}")
    await context.bot.edit_message_text(chat_id=draft.chat_id, message_id=draft.summary_message_id, text="\n".join(lines))

    cleanup(draft.image_path)
    state.pop_draft(draft.id)


async def on_abort_tap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    draft_id = query.data.split(":", 1)[1]

    if not _is_allowed(user.id):
        await query.answer("You're not authorized to publish.", show_alert=True)
        return

    draft = state.get_draft(draft_id)
    if draft is None:
        await query.answer(_EXPIRED_TEXT, show_alert=True)
        return

    async with draft.lock:
        if draft.status == "posting":
            await query.answer("Already posting - can't abort now.", show_alert=False)
            return
        if draft.status == "posted":
            await query.answer("Already posted.", show_alert=False)
            return
        if draft.instagram_posted or draft.website_posted:
            posted_where = " and ".join(
                p for p, ok in (("Instagram", draft.instagram_posted), ("the website", draft.website_posted)) if ok
            )
            await query.answer(f"Already posted to {posted_where} - can't abort, only retry the rest.", show_alert=True)
            return
        if draft.status not in ("failed_caption", "awaiting_summary_review", "failed_summary"):
            await query.answer("Hang on, still working on that.", show_alert=False)
            return

        state.pop_draft(draft.id)
        cleanup(draft.image_path)
        await query.answer()
        await query.edit_message_text("❌ Aborted - nothing was posted.")


async def on_caption_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lets a user edit the pending caption by replying to the caption preview message.

    Registered ahead of the headline ConversationHandler (lower group number) so a caption edit
    isn't mistaken for a new headline; only stops propagation (ApplicationHandlerStop) when the
    reply actually targets a known draft.
    """
    message = update.effective_message
    reply_to = message.reply_to_message
    if reply_to is None:
        return

    draft = state.get_draft_by_caption_message(message.chat_id, reply_to.message_id)
    if draft is None:
        return

    new_caption = (message.text or "").strip()
    if not new_caption:
        await message.reply_text("That looked empty - send the replacement caption text.")
        raise ApplicationHandlerStop

    async with draft.lock:
        if state.is_expired(draft):
            state.pop_draft(draft.id)
            cleanup(draft.image_path)
            await message.reply_text(_EXPIRED_TEXT)
            raise ApplicationHandlerStop

        if draft.status != "awaiting_caption_review":
            await message.reply_text("Can't edit the caption right now - it's already saved or in progress.")
            raise ApplicationHandlerStop

        draft.caption = new_caption

    await context.bot.edit_message_text(
        chat_id=draft.chat_id,
        message_id=draft.caption_message_id,
        text=_caption_preview_text(new_caption, note="✏️ Caption updated."),
        reply_markup=_caption_keyboard(draft.id),
    )
    raise ApplicationHandlerStop
