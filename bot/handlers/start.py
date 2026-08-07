from telegram import Update
from telegram.ext import ContextTypes

HELP_TEXT = (
    "Send me a headline for a PedTalkSports graphic "
    "(wrap words in **double asterisks** to highlight them in maroon), "
    "then I'll ask you to pick a layout side and send the image. "
    "I'll generate the finished Instagram graphic from both.\n\n"
    "/cancel - abort the current request"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP_TEXT)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP_TEXT)
