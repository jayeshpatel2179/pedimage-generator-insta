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
from bot.handlers.publish import (
    on_abort_tap,
    on_caption_reply,
    on_golive_tap,
    on_regen_caption_tap,
    on_regen_summary_tap,
    on_save_tap,
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
    # Default python-telegram-bot timeouts (5s read/write/connect) are too tight for
    # sending the multi-MB generated images this bot posts - a slow connection can
    # cause send_photo to raise TimedOut even though Telegram already received and
    # posted it, which is confusing (see _generate_and_send's exception handling).
    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .connect_timeout(30)
        .read_timeout(60)
        .write_timeout(60)
        .pool_timeout(30)
        .build()
    )

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
    # Runs before the conversation's headline handler so replies editing the caption
    # preview aren't mistaken for a new headline (see handlers/publish.py).
    application.add_handler(
        MessageHandler(filters.TEXT & filters.REPLY & ~filters.COMMAND, on_caption_reply), group=-1
    )
    application.add_handler(conversation)
    application.add_handler(CallbackQueryHandler(on_save_tap, pattern=r"^pub_save:"))
    application.add_handler(CallbackQueryHandler(on_regen_caption_tap, pattern=r"^pub_regen_caption:"))
    application.add_handler(CallbackQueryHandler(on_golive_tap, pattern=r"^pub_golive:"))
    application.add_handler(CallbackQueryHandler(on_regen_summary_tap, pattern=r"^pub_regen_summary:"))
    application.add_handler(CallbackQueryHandler(on_abort_tap, pattern=r"^pub_abort:"))
    application.add_handler(MessageHandler(filters.PHOTO, _stray_photo), group=1)
    application.add_error_handler(_error_handler)

    return application


def main() -> None:
    application = build_application()
    logger.info("Starting PedTalkSports bot (polling)...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
