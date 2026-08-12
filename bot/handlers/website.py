import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ApplicationHandlerStop, ContextTypes

from bot.config import GO_LIVE_ALLOWED_USER_IDS, WEBSITE_IMAGE_MAX_BYTES, WEBSITE_SUMMARY_HISTORY_LIMIT
from bot.utils.image_utils import cleanup, save_telegram_photo
from bot.website import state as website_state
from bot.website.summary_generator import SummaryGenerationError, generate_website_summary
from bot.website.website_client import WebsiteUploadError, upload_photo_to_website

logger = logging.getLogger(__name__)

_EXPIRED_TEXT = "This draft expired (30 min passed). Resend the photo + description to try again."
_FAILURE_TEXT = "Couldn't rewrite your description (AI error). Tap Retry."
_ADD_SUMMARY_PROMPT = (
    "\U0001f4dd Resend that image, and this time write a 30-40 word description of what you want "
    "the website copy to say about it as the photo's caption."
)


def _is_allowed(user_id: int) -> bool:
    # Empty allowlist = unrestricted, see GO_LIVE_ALLOWED_USER_IDS in config.py.
    return not GO_LIVE_ALLOWED_USER_IDS or user_id in GO_LIVE_ALLOWED_USER_IDS


def website_add_summary_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("\U0001f4dd Add Website Summary", callback_data="website_add")]])


def _ready_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("\U0001f310 Go Live Website", callback_data=f"website_golive:{draft_id}"),
                InlineKeyboardButton("\U0001f504 Regenerate", callback_data=f"website_regen:{draft_id}"),
            ],
            [InlineKeyboardButton("❌ Abort", callback_data=f"website_abort:{draft_id}")],
        ]
    )


def _failed_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("\U0001f504 Retry", callback_data=f"website_regen:{draft_id}"),
                InlineKeyboardButton("❌ Abort", callback_data=f"website_abort:{draft_id}"),
            ]
        ]
    )


def _busy_keyboard(draft_id: str, label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=f"website_regen:{draft_id}")]])


def _preview_text(summary: str, note: str | None = None) -> str:
    return f"{note}\n\n{summary}" if note else summary


async def on_add_summary_tap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    chat_id = update.effective_chat.id

    if not _is_allowed(user.id):
        await query.answer("You're not authorized to post to the website.", show_alert=True)
        return

    if website_state.get_pending(chat_id, user.id) is not None:
        await query.answer("Already waiting on your description - resend the photo with it.", show_alert=True)
        return

    website_state.arm_pending(chat_id, user.id)
    website_state.schedule_pending_expiry(context.application, chat_id, user.id)

    await query.answer()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        logger.warning("Could not clear reply_markup after Add Website Summary tap")

    await context.bot.send_message(
        chat_id=chat_id, text=_ADD_SUMMARY_PROMPT, reply_to_message_id=query.message.message_id
    )


async def on_photo_for_website(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Routes a photo to the website flow if this (chat, user) armed it via "Add Website Summary".

    Registered ahead of the headline ConversationHandler and the stray-photo catch-all (lower group
    number, see bot/main.py) so an armed description photo isn't mistaken for a new generation
    request; only stops propagation (ApplicationHandlerStop) when the pending arm actually matches.
    """
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if website_state.get_pending(chat.id, user.id) is None:
        return

    raw_summary = (message.caption or "").strip()
    if not raw_summary:
        await message.reply_text(
            "That needs a 30-40 word description attached as the photo's caption - resend it that way."
        )
        raise ApplicationHandlerStop

    photo = message.photo[-1]
    if photo.file_size and photo.file_size > WEBSITE_IMAGE_MAX_BYTES:
        await message.reply_text("That image is over the website's 10MB limit - resend a smaller one.")
        raise ApplicationHandlerStop

    website_state.pop_pending(chat.id, user.id)

    photo_file = await photo.get_file()
    image_path = await save_telegram_photo(photo_file, chat.id)

    draft = website_state.create_draft(chat_id=chat.id, user_id=user.id, image_path=image_path, raw_summary=raw_summary)
    website_state.schedule_expiry_cleanup(context.application, draft.id)

    status_msg = await message.reply_text("\U0001f916 Rewriting your description for the website...")

    context.application.create_task(_produce_first_summary(context, draft, status_msg.message_id))
    raise ApplicationHandlerStop


async def _produce_first_summary(context, draft: "website_state.WebsiteDraft", status_message_id: int) -> None:
    try:
        summary = await generate_website_summary(draft.image_path, draft.raw_summary, is_regeneration=False, history=[])
    except SummaryGenerationError:
        logger.exception("Initial website summary rewrite failed for draft %s", draft.id)
        async with draft.lock:
            draft.status = "failed"
        draft.review_message_id = status_message_id
        await context.bot.edit_message_text(
            chat_id=draft.chat_id, message_id=status_message_id, text=_FAILURE_TEXT, reply_markup=_failed_keyboard(draft.id)
        )
        return

    async with draft.lock:
        draft.summary = summary
        draft.summary_history.append(summary)
        draft.status = "ready"
    draft.review_message_id = status_message_id

    await context.bot.edit_message_text(
        chat_id=draft.chat_id, message_id=status_message_id, text=_preview_text(summary), reply_markup=_ready_keyboard(draft.id)
    )


async def on_website_regen_tap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    draft_id = query.data.split(":", 1)[1]
    draft = website_state.get_draft(draft_id)

    if draft is None:
        await query.answer(_EXPIRED_TEXT, show_alert=True)
        return

    async with draft.lock:
        if website_state.is_expired(draft):
            await query.answer()
            website_state.pop_draft(draft.id)
            cleanup(draft.image_path)
            await query.edit_message_text(_EXPIRED_TEXT)
            return

        if draft.status not in ("ready", "failed"):
            await query.answer("Hang on, that's already in progress.", show_alert=False)
            return

        had_previous = draft.status == "ready"
        draft.status = "regenerating"
        await query.answer()
        await query.edit_message_text(
            "\U0001f504 Rewriting..." if had_previous else "\U0001f504 Retrying...",
            reply_markup=_busy_keyboard(draft.id, "\U0001f504 Working..."),
        )

    context.application.create_task(_do_regenerate(context, draft, had_previous))


async def _do_regenerate(context, draft: "website_state.WebsiteDraft", had_previous: bool) -> None:
    try:
        summary = await generate_website_summary(
            draft.image_path,
            draft.raw_summary,
            is_regeneration=True,
            history=draft.summary_history[-WEBSITE_SUMMARY_HISTORY_LIMIT:],
        )
    except SummaryGenerationError:
        logger.exception("Website summary regeneration failed for draft %s", draft.id)
        async with draft.lock:
            draft.status = "ready" if had_previous else "failed"
        if had_previous:
            await context.bot.edit_message_text(
                chat_id=draft.chat_id,
                message_id=draft.review_message_id,
                text=_preview_text(draft.summary, note="⚠️ Regeneration failed - showing the previous summary."),
                reply_markup=_ready_keyboard(draft.id),
            )
        else:
            await context.bot.edit_message_text(
                chat_id=draft.chat_id, message_id=draft.review_message_id, text=_FAILURE_TEXT, reply_markup=_failed_keyboard(draft.id)
            )
        return

    async with draft.lock:
        draft.summary = summary
        draft.summary_history.append(summary)
        draft.status = "ready"

    await context.bot.edit_message_text(
        chat_id=draft.chat_id, message_id=draft.review_message_id, text=_preview_text(summary), reply_markup=_ready_keyboard(draft.id)
    )


async def on_website_golive_tap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    draft_id = query.data.split(":", 1)[1]

    if not _is_allowed(user.id):
        await query.answer("You're not authorized to post to the website.", show_alert=True)
        return

    draft = website_state.get_draft(draft_id)
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
        if website_state.is_expired(draft):
            await query.answer()
            website_state.pop_draft(draft.id)
            cleanup(draft.image_path)
            await query.edit_message_text(_EXPIRED_TEXT)
            return
        if draft.status != "ready":
            await query.answer("Hang on, still preparing the summary.", show_alert=False)
            return

        draft.status = "posting"
        await query.answer()
        await query.edit_message_text(
            f"Posting to the website...\n\n{draft.summary}", reply_markup=_busy_keyboard(draft.id, "⏳ Posting...")
        )

    context.application.create_task(_do_post(context, draft))


async def _do_post(context, draft: "website_state.WebsiteDraft") -> None:
    try:
        url = await upload_photo_to_website(draft.image_path, draft.summary)
    except WebsiteUploadError as exc:
        logger.exception("Website upload failed for draft %s", draft.id)
        async with draft.lock:
            draft.status = "ready"
        await context.bot.edit_message_text(
            chat_id=draft.chat_id,
            message_id=draft.review_message_id,
            text=_preview_text(draft.summary, note=f"⚠️ Posting failed: {exc}"),
            reply_markup=_ready_keyboard(draft.id),
        )
        return

    async with draft.lock:
        draft.status = "posted"

    await context.bot.edit_message_text(
        chat_id=draft.chat_id, message_id=draft.review_message_id, text=f"✅ Posted to the website!\n{url}"
    )

    cleanup(draft.image_path)
    website_state.pop_draft(draft.id)


async def on_website_abort_tap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    draft_id = query.data.split(":", 1)[1]
    draft = website_state.get_draft(draft_id)

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
        if draft.status == "regenerating":
            await query.answer("Hang on, still working on that.", show_alert=False)
            return

        website_state.pop_draft(draft.id)
        cleanup(draft.image_path)
        await query.answer()
        await query.edit_message_text("❌ Aborted - nothing was posted.")
