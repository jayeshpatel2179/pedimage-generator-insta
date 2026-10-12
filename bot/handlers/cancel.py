from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.handlers.publish import cancel_awaiting_drafts
from bot.publish import texts


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    # Also discards a draft waiting for its Instagram caption - registered both as the headline
    # conversation's fallback and standalone (bot/main.py), so it works outside the conversation.
    cancelled = await cancel_awaiting_drafts(context.bot, update.effective_chat.id, update.effective_user.id)
    if cancelled:
        await update.effective_message.reply_text(texts.CANCELLED)
    else:
        await update.effective_message.reply_text("Cancelled. Send a new headline whenever you're ready.")
    return ConversationHandler.END


async def timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update and update.effective_message:
        await update.effective_message.reply_text(
            "Timed out waiting for a reply. Send a new headline to start over."
        )
    return ConversationHandler.END
