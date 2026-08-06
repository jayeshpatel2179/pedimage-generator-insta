import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes, ConversationHandler

from bot.generation.ai_generator import generate_image
from bot.generation.prompt_builder import build_prompt
from bot.go_live import state as go_live_state
from bot.handlers.go_live import go_live_keyboard
from bot.utils.image_utils import cleanup, resize_to_final, save_telegram_photo
from bot.utils.text_utils import parse_emphasis

logger = logging.getLogger(__name__)

CHOOSING_LAYOUT, WAITING_PHOTO = range(2)

LAYOUT_KEYBOARD = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton("Text on the Left", callback_data="layout:left"),
            InlineKeyboardButton("Text on the Right", callback_data="layout:right"),
        ]
    ]
)


async def receive_headline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    plain_text, emphasis = parse_emphasis(update.effective_message.text)
    if not plain_text:
        await update.effective_message.reply_text("That looked empty - send the headline text.")
        return CHOOSING_LAYOUT

    context.user_data["headline"] = plain_text
    context.user_data["emphasis"] = emphasis

    await update.effective_message.reply_text(
        "Got it. Should the text panel go on the left or the right?",
        reply_markup=LAYOUT_KEYBOARD,
    )
    return CHOOSING_LAYOUT


async def receive_layout_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    layout_side = query.data.split(":", 1)[1]
    context.user_data["layout_side"] = layout_side

    await query.edit_message_text(f"Text panel: {layout_side}. Now send me the player photo.")
    return WAITING_PHOTO


async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    headline = context.user_data.get("headline")
    emphasis = context.user_data.get("emphasis", [])
    layout_side = context.user_data.get("layout_side", "left")

    photo_file = await update.effective_message.photo[-1].get_file()
    chat_id = update.effective_chat.id

    await update.effective_message.reply_text("Generating your image, this can take a moment...")
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)

    context.application.create_task(
        _generate_and_send(context, chat_id, photo_file, headline, emphasis, layout_side)
    )

    context.user_data.clear()
    return ConversationHandler.END


async def _generate_and_send(context, chat_id, photo_file, headline, emphasis, layout_side) -> None:
    input_path = None
    output_path = None
    try:
        input_path = await save_telegram_photo(photo_file, chat_id)
        prompt = build_prompt(headline, emphasis, layout_side)
        image_bytes = await generate_image(input_path, prompt)
        output_path = resize_to_final(image_bytes, chat_id)

        # output_path is kept (not cleaned up here) so a later "Go Live" tap can
        # upload the pre-Telegram-compression file - see go_live/state.py, which
        # owns its cleanup from here on (on post success, failure retry exhausted
        # only by expiry, or the 30-minute expiry sweep).
        draft = go_live_state.create_draft(image_path=output_path, headline=headline, chat_id=chat_id)

        with open(output_path, "rb") as final_image:
            sent_message = await context.bot.send_photo(
                chat_id=chat_id, photo=final_image, reply_markup=go_live_keyboard(draft.id)
            )

        go_live_state.attach_image_message(draft, sent_message.message_id)
        go_live_state.schedule_expiry_cleanup(context.application, draft.id)
    except Exception:
        logger.exception("Image generation failed for chat_id=%s", chat_id)
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "Sorry, that generation failed (the model may have refused the request, "
                "or there was a network error). Send a new headline to try again."
            ),
        )
        cleanup(output_path)
    finally:
        cleanup(input_path)
