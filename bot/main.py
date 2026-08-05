import logging

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.config import CONVERSATION_TIMEOUT_SECONDS, LOG_LEVEL, TELEGRAM_BOT_TOKEN
from bot.handlers.cancel import cancel, timeout
from bot.handlers.conversation import (
    CHOOSING_LAYOUT,
    WAITING_PHOTO,
    receive_headline,
    receive_layout_choice,
    receive_photo,
)
from bot.handlers.start import help_command, start

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s", level=LOG_LEVEL
)
logger = logging.getLogger(__name__)


async def _stray_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Send me the headline text first, then I'll ask for a photo."
    )


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled exception while processing an update", exc_info=context.error)


def build_application() -> Application:
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    conversation = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_headline)
        ],
        states={
            CHOOSING_LAYOUT: [
                CallbackQueryHandler(receive_layout_choice, pattern=r"^layout:")
            ],
            WAITING_PHOTO: [MessageHandler(filters.PHOTO, receive_photo)],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, timeout),
                CallbackQueryHandler(timeout),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=CONVERSATION_TIMEOUT_SECONDS,
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(conversation)
    application.add_handler(MessageHandler(filters.PHOTO, _stray_photo), group=1)
    application.add_error_handler(_error_handler)

    return application


def main() -> None:
    application = build_application()
    logger.info("Starting PedTalkSports bot (polling)...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
