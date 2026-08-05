from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.effective_message.reply_text("Cancelled. Send a new headline whenever you're ready.")
    return ConversationHandler.END


async def timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update and update.effective_message:
        await update.effective_message.reply_text(
            "Timed out waiting for a reply. Send a new headline to start over."
        )
    return ConversationHandler.END
