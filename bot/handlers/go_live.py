import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ApplicationHandlerStop, ContextTypes

from bot.config import GO_LIVE_ALLOWED_USER_IDS
from bot.go_live import state
from bot.go_live.caption_generator import generate_caption
from bot.go_live.uploadpost_client import UploadPostError, upload_photo_to_instagram
from bot.utils.image_utils import cleanup

logger = logging.getLogger(__name__)

_EXPIRED_TEXT = "This draft expired (30 min passed). Regenerate the image to post it."


def _is_allowed(user_id: int) -> bool:
    # Empty allowlist = unrestricted, see GO_LIVE_ALLOWED_USER_IDS in config.py.
    return not GO_LIVE_ALLOWED_USER_IDS or user_id in GO_LIVE_ALLOWED_USER_IDS


def go_live_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("\U0001f7e5 Go Live", callback_data=f"golive:{draft_id}")]])


def _pending_image_keyboard(draft_id: str, label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=f"golive:{draft_id}")]])


def _confirm_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Confirm & Post", callback_data=f"golive_confirm:{draft_id}"),
                InlineKeyboardButton("\U0001f504 Regenerate", callback_data=f"golive_regen:{draft_id}"),
            ],
            [InlineKeyboardButton("❌ Cancel", callback_data=f"golive_cancel:{draft_id}")],
        ]
    )


def _posting_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⏳ Posting...", callback_data=f"golive_confirm:{draft_id}")]])


def _regenerating_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("\U0001f504 Regenerating...", callback_data=f"golive_regen:{draft_id}")]])


def _preview_text(caption: str, note: str | None = None) -> str:
    base = (
        f"Caption preview:\n\n{caption}\n\n"
        "Reply to this message with new text to edit the caption, or use the buttons below."
    )
    return f"{note}\n\n{base}" if note else base


async def _safe_edit_image_keyboard(context, draft: state.GoLiveDraft, reply_markup) -> None:
    try:
        await context.bot.edit_message_reply_markup(
            chat_id=draft.chat_id, message_id=draft.message_id, reply_markup=reply_markup
        )
    except TelegramError:
        logger.warning("Could not edit image message reply_markup for draft %s", draft.id)


async def on_go_live_tap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    draft_id = query.data.split(":", 1)[1]

    if not _is_allowed(user.id):
        await query.answer("You're not authorized to publish to Instagram.", show_alert=True)
        return

    draft = state.get_draft(draft_id)
    if draft is None:
        await query.answer()
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=_EXPIRED_TEXT,
            reply_to_message_id=query.message.message_id,
        )
        return

    async with draft.lock:
        if state.is_expired(draft):
            await query.answer()
            state.pop_draft(draft.id)
            cleanup(draft.image_path)
            await query.edit_message_reply_markup(reply_markup=None)
            await context.bot.send_message(chat_id=draft.chat_id, text=_EXPIRED_TEXT, reply_to_message_id=draft.message_id)
            return

        if draft.status != "awaiting_golive":
            await query.answer("Already in progress - check the caption message below.", show_alert=False)
            return

        draft.status = "generating_caption"
        await query.answer()
        await query.edit_message_reply_markup(reply_markup=_pending_image_keyboard(draft.id, "Generating caption..."))

    context.application.create_task(_produce_caption_preview(context, draft))


async def _produce_caption_preview(context, draft: state.GoLiveDraft) -> None:
    try:
        caption = await generate_caption(draft.headline, draft.image_path)
    except Exception:
        logger.exception("Caption generation failed for draft %s", draft.id)
        async with draft.lock:
            draft.status = "awaiting_golive"
        await _safe_edit_image_keyboard(context, draft, go_live_keyboard(draft.id))
        await context.bot.send_message(
            chat_id=draft.chat_id,
            text="Couldn't generate a caption (API error). Tap Go Live to try again.",
            reply_to_message_id=draft.message_id,
        )
        return

    async with draft.lock:
        draft.caption = caption
        draft.status = "awaiting_confirm"

    await _safe_edit_image_keyboard(context, draft, _pending_image_keyboard(draft.id, "See caption below ⬇️"))

    msg = await context.bot.send_message(
        chat_id=draft.chat_id,
        text=_preview_text(caption),
        reply_markup=_confirm_keyboard(draft.id),
        reply_to_message_id=draft.message_id,
    )
    state.set_caption_message(draft, msg.message_id)


async def on_regenerate_tap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    draft_id = query.data.split(":", 1)[1]

    if not _is_allowed(user.id):
        await query.answer("You're not authorized to publish to Instagram.", show_alert=True)
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

        if draft.status != "awaiting_confirm":
            await query.answer("Hang on, that's already in progress.", show_alert=False)
            return

        draft.status = "generating_caption"
        await query.answer()
        await query.edit_message_text("Regenerating caption...", reply_markup=_regenerating_keyboard(draft.id))

    context.application.create_task(_regenerate_caption(context, draft))


async def _regenerate_caption(context, draft: state.GoLiveDraft) -> None:
    try:
        caption = await generate_caption(draft.headline, draft.image_path)
    except Exception:
        logger.exception("Caption regeneration failed for draft %s", draft.id)
        async with draft.lock:
            draft.status = "awaiting_confirm"
        await context.bot.edit_message_text(
            chat_id=draft.chat_id,
            message_id=draft.caption_message_id,
            text=_preview_text(draft.caption, note="⚠️ Regeneration failed - showing the previous caption."),
            reply_markup=_confirm_keyboard(draft.id),
        )
        return

    async with draft.lock:
        draft.caption = caption
        draft.status = "awaiting_confirm"

    await context.bot.edit_message_text(
        chat_id=draft.chat_id,
        message_id=draft.caption_message_id,
        text=_preview_text(caption),
        reply_markup=_confirm_keyboard(draft.id),
    )


async def on_confirm_tap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    draft_id = query.data.split(":", 1)[1]

    if not _is_allowed(user.id):
        await query.answer("You're not authorized to publish to Instagram.", show_alert=True)
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
        if draft.status != "awaiting_confirm":
            await query.answer("Hang on, still preparing the caption.", show_alert=False)
            return

        draft.status = "posting"
        await query.answer()
        await query.edit_message_text(
            f"Posting to Instagram...\n\n{draft.caption}", reply_markup=_posting_keyboard(draft.id)
        )

    context.application.create_task(_do_post(context, draft))


async def _do_post(context, draft: state.GoLiveDraft) -> None:
    try:
        url = await upload_photo_to_instagram(draft.image_path, draft.caption)
    except UploadPostError as exc:
        logger.exception("Instagram upload failed for draft %s", draft.id)
        async with draft.lock:
            draft.status = "awaiting_confirm"
        await context.bot.edit_message_text(
            chat_id=draft.chat_id,
            message_id=draft.caption_message_id,
            text=_preview_text(draft.caption, note=f"⚠️ Posting failed: {exc}"),
            reply_markup=_confirm_keyboard(draft.id),
        )
        return

    async with draft.lock:
        draft.status = "posted"

    await context.bot.edit_message_text(
        chat_id=draft.chat_id, message_id=draft.caption_message_id, text=f"✅ Posted to Instagram!\n{url}"
    )
    await _safe_edit_image_keyboard(context, draft, None)

    cleanup(draft.image_path)
    state.pop_draft(draft.id)


async def on_cancel_tap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    draft_id = query.data.split(":", 1)[1]

    if not _is_allowed(user.id):
        await query.answer("You're not authorized to publish to Instagram.", show_alert=True)
        return

    draft = state.get_draft(draft_id)
    if draft is None:
        await query.answer(_EXPIRED_TEXT, show_alert=True)
        return

    async with draft.lock:
        if draft.status == "posting":
            await query.answer("Already posting - can't cancel now.", show_alert=False)
            return
        if draft.status == "posted":
            await query.answer("Already posted.", show_alert=False)
            return
        if draft.status != "awaiting_confirm":
            await query.answer("Hang on, still preparing the caption.", show_alert=False)
            return

        state.pop_draft(draft.id)
        cleanup(draft.image_path)
        await query.answer()
        await query.edit_message_text("❌ Go Live cancelled. Regenerate the image to post it.")

    await _safe_edit_image_keyboard(context, draft, None)


async def on_caption_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lets a user edit the pending caption by replying to the preview message.

    Registered ahead of the headline ConversationHandler (lower group number) so a
    caption edit isn't mistaken for a new headline; only stops propagation
    (ApplicationHandlerStop) when the reply actually targets a known draft.
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

        if draft.status != "awaiting_confirm":
            await message.reply_text("Can't edit the caption right now - it's already posting or posted.")
            raise ApplicationHandlerStop

        draft.caption = new_caption

    await context.bot.edit_message_text(
        chat_id=draft.chat_id,
        message_id=draft.caption_message_id,
        text=_preview_text(new_caption, note="✏️ Caption updated."),
        reply_markup=_confirm_keyboard(draft.id),
    )
    raise ApplicationHandlerStop
