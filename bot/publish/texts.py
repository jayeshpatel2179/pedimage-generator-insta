"""User-facing texts for the manual Instagram caption step and the publish review - edit freely.

Plain-text messages are sent without parse_mode. The REVIEW_* fragments are HTML (the review
message is sent with parse_mode=HTML); anything user-supplied is escaped before it's inserted.
"""

# --- Caption step ---------------------------------------------------------------------------
ASK_CAPTION = (
    "\U0001f5bc Image ready.\n"
    "✍️ Now send the Instagram caption. It is required, and I will post exactly what you type.\n"
    "(The website summary is still written by AI from the headline.)"
)
ASK_NEW_CAPTION = "Send the new Instagram caption."
CAPTION_EMPTY = "That caption is empty. Please type the Instagram caption."
CAPTION_TOO_LONG = (
    "That caption is {count} characters. Instagram allows up to {limit}. "
    "Please shorten it and send it again."
)
CAPTION_TOO_MANY_HASHTAGS = (
    "That caption has {count} hashtags. Instagram allows up to {limit}. "
    "Please remove some and send it again."
)
CAPTION_TOO_MANY_MENTIONS = (
    "That caption has {count} @mentions. Instagram allows up to {limit}. "
    "Please remove some and send it again."
)
CAPTION_NOT_TEXT = "Please send the caption as text."
STILL_WAITING_FOR_CAPTION = (
    "You still have a draft waiting for its Instagram caption. Send the caption, or tap Cancel."
)
ASK_CAPTION_ANSWERED = "✅ Caption received - see the review below."

# --- Cancel / expiry ------------------------------------------------------------------------
CANCELLED = "❌ Cancelled - the draft and its caption were discarded, nothing was posted."
EXPIRED = "This draft expired (30 min passed). Regenerate the image to post it."
NOT_AUTHORIZED = "You're not authorized to publish."

# --- Review message (HTML) ------------------------------------------------------------------
REVIEW_TITLE = "<b>Review before publishing</b>"
REVIEW_CAPTION_HEADER = "\U0001f4f8 <b>Instagram caption</b> (yours, posted exactly as typed - {count} characters):"
REVIEW_CAPTION_WAITING = "✏️ Waiting for the new Instagram caption..."
REVIEW_SUMMARY_HEADER = "\U0001f310 <b>Website summary</b> (AI):"
REVIEW_SUMMARY_PENDING = "\U0001f916 Generating website summary..."
REVIEW_SUMMARY_FAILED = "⚠️ Couldn't generate a website summary (API error). Tap Retry summary."
REVIEW_NOTE_CAPTION_UPDATED = "✏️ Instagram caption updated."
REVIEW_NOTE_SUMMARY_REGEN_FAILED = "⚠️ Regeneration failed - showing the previous summary."
REVIEW_NOTE_RESTARTED_WHILE_POSTING = (
    "⚠️ The bot restarted while posting - check Instagram and the website before tapping "
    "Go Live again (anything confirmed posted is skipped)."
)
REVIEW_POSTING = "⏳ Posting to Instagram + website..."
POSTED = "✅ Posted to Instagram and the website!"

# --- Buttons --------------------------------------------------------------------------------
BTN_GO_LIVE = "\U0001f680 Go Live"
BTN_REGEN_SUMMARY = "\U0001f504 Regenerate Summary"
BTN_RETRY_SUMMARY = "\U0001f504 Retry summary"
BTN_EDIT_CAPTION = "✏️ Edit Instagram caption"
BTN_CANCEL = "❌ Cancel"
BTN_WORKING = "\U0001f504 Working..."
BTN_POSTING = "⏳ Posting..."
