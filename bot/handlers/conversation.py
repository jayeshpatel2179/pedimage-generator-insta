import logging

from openai import BadRequestError as OpenAIBadRequestError
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import ApplicationHandlerStop, ContextTypes, ConversationHandler

from bot.generation.ai_generator import generate_image
from bot.generation.prompt_builder import build_prompt
from bot.handlers.publish import begin_publish_flow
from bot.publish import state as publish_state
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

    await query.edit_message_text(f"Text panel: {layout_side}. Now send me the image.")
    return WAITING_PHOTO


async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    headline = context.user_data.get("headline")
    emphasis = context.user_data.get("emphasis", [])
    layout_side = context.user_data.get("layout_side", "left")

    photo_file = await update.effective_message.photo[-1].get_file()
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    await update.effective_message.reply_text("Generating your image, this can take a moment...")
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)

    context.application.create_task(
        _generate_and_send(context, chat_id, user_id, photo_file, headline, emphasis, layout_side)
    )

    context.user_data.clear()
    # Stop propagation to later handler groups (e.g. the group=1 stray-photo
    # catch-all in bot/main.py) - otherwise it also fires on this same photo
    # since it was already handled here, producing a confusing extra message.
    raise ApplicationHandlerStop(ConversationHandler.END)


async def _generate_and_send(context, chat_id, user_id, photo_file, headline, emphasis, layout_side) -> None:
    input_path = None
    output_path = None
    draft = None
    try:
        input_path = await save_telegram_photo(photo_file, chat_id)
        prompt = build_prompt(headline, emphasis, layout_side)
        image_bytes = await generate_image(input_path, prompt)
        output_path = resize_to_final(image_bytes, chat_id)

        # output_path is kept (not cleaned up here) so the publish flow can upload the
        # pre-Telegram-compression file to both Instagram and the website - see
        # publish/state.py, which owns its cleanup from here on (on post success, abort,
        # or the 30-minute expiry sweep, armed in begin_publish_flow).
        draft = publish_state.create_draft(
            image_path=output_path, headline=headline, chat_id=chat_id, user_id=user_id
        )

        with open(output_path, "rb") as final_image:
            sent_message = await context.bot.send_photo(chat_id=chat_id, photo=final_image)

        publish_state.attach_image_message(draft, sent_message.message_id)
        await begin_publish_flow(context, draft)
    except Exception as exc:
        logger.exception("Image generation failed for chat_id=%s", chat_id)
        if draft is not None:
            # send_photo may have actually delivered the message server-side despite a
            # client-side error (e.g. a timeout waiting for the response) - drop the
            # draft so a tap on any stray publish button fails gracefully ("draft
            # expired") instead of crashing on the file cleanup() is about to do below.
            publish_state.pop_draft(draft.id)

        if isinstance(exc, OpenAIBadRequestError) and exc.code == "moderation_blocked":
            # OpenAI's images.edit endpoint can reject either the input photo or its own
            # generated output on safety grounds - categories/stage vary per request (e.g.
            # "public-figure" on the input, or "other" on the output), so surface whatever
            # it actually flagged rather than assuming one reason. This is a hard
            # server-side policy block, not a transient failure - retrying the exact same
            # photo/headline is unlikely to help.
            details = exc.body.get("moderation_details") if isinstance(exc.body, dict) else None
            categories = details.get("categories") if isinstance(details, dict) else None
            stage = details.get("moderation_stage") if isinstance(details, dict) else None
            reason = ", ".join(categories) if categories else "unspecified"
            stage_text = f" ({stage} stage)" if stage else ""
            text = (
                f"⚠️ OpenAI's safety system blocked this generation - flagged as "
                f"'{reason}'{stage_text}. This is a policy block, not a network hiccup - "
                "try a different photo and/or reword the headline, then send it again."
            )
        else:
            text = (
                "Sorry, that generation failed (the model may have refused the request, "
                "or there was a network error). Send a new headline to try again."
            )
        await context.bot.send_message(chat_id=chat_id, text=text)
        cleanup(output_path)
    finally:
        cleanup(input_path)
