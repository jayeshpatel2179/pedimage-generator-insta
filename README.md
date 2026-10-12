# PedTalkSports Instagram Image Bot

Telegram bot that turns a headline + a source image (a single player photo, or an
existing multi-player collage/graphic) into a PedTalkSports-branded Instagram
graphic using OpenAI's `gpt-image-2` image-edit endpoint.

See `PROJECT_SPEC.md` for the full design (architecture, conversation flow, brand
style rules, and the decisions behind them).

## Setup

1. Create a virtual environment and install dependencies:

   ```
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and fill in the values:
   - `TELEGRAM_BOT_TOKEN` - from @BotFather
   - `OPENAI_API_KEY`
   - `OPENAI_IMAGE_MODEL` - defaults to `gpt-image-2`, only change if needed
   - `UPLOAD_POST_API_KEY` / `UPLOAD_POST_PROFILE` - from your
     [upload-post.com](https://www.upload-post.com/) dashboard; required for the
     Instagram half of **Go Live** (image generation and the review still
     work without these set - only the final publish step needs them)
   - `WEBSITE_UPLOAD_API_KEY` - required for the website half of **Go Live**
     (everything else, including the Instagram half, still works without it)
   - `WEBSITE_UPLOAD_URL` - optional override; defaults to
     `https://www.pedtalkssports.com/api/instagram/upload`
   - `GO_LIVE_ALLOWED_USER_IDS` - optional, comma-separated Telegram user IDs.
     Leave empty to let anyone in the group publish.
   - `DRY_RUN` - optional; `1` logs the outgoing Instagram/website payloads
     instead of posting. Leave unset (off) in production.

3. **Required Telegram group setup** (do this before using the bot in the group):
   - Message @BotFather -> `/setprivacy` -> select your bot -> `Disable`.
     (Or promote the bot to admin in the group instead - either works.)
   - Remove and re-add the bot to the group so the privacy change takes effect.
     Without this step the bot won't see plain text/photo messages in a group.

4. Run the bot:

   ```
   python run.py
   ```

## Using it

In the group (or a DM with the bot):

1. Send the headline text. Wrap words in `**double asterisks**` to mark them for
   maroon/red emphasis, e.g.:
   `Performance so good he rinsed the **"rigged" narrative** in one match`
2. Tap "Text on the Left" or "Text on the Right".
3. Send the image - either a single player photo or an existing multi-player
   collage/graphic. Either way it's used as-is (no cropping to a single
   player); only the text panel is generated and merged onto the chosen side.
4. Wait - the bot generates the graphic in the background, posts it back to
   the chat, and immediately starts the publish review below. Multiple people
   can do this at the same time; each person's request is tracked
   independently.

`/cancel` aborts a request in progress. A request that gets no reply for 5
minutes times out automatically.

### Publishing (manual Instagram caption, one review, then Go Live posts to both platforms)

Nothing goes live until you've reviewed and approved the Instagram caption and
the website summary together - the bot never posts without that approval.

**1. Type the Instagram caption (required)**

As soon as the image is generated, the bot asks you to type the Instagram
caption. Your next text message is the caption (it's never mistaken for a new
headline), and it's posted to Instagram **exactly as typed** - no AI rewriting,
no added hashtags. Only leading/trailing whitespace is trimmed. Captions over
Instagram's limits (2,200 characters, 30 hashtags, 20 @mentions) are rejected
with a message so you can shorten and resend - never truncated. **❌ Cancel**
discards the draft (so does `/cancel`).

Meanwhile the bot writes a **50-60 word** website summary in the background
(AI, grounded on the headline and the image - unchanged from before).

**2. Review**

One review message shows your caption (with its character count) and the AI
website summary:

- **🚀 Go Live** - publishes to **both** the PedTalkSports Instagram account
  (via [upload-post.com](https://www.upload-post.com/)) and the PedTalks
  website **at the same time**. This is the only button that actually
  publishes anything.
- **🔄 Regenerate Summary** - a different 50-60 word rewrite of the same
  facts, same image.
- **✏️ Edit Instagram caption** - asks for a new caption; the review updates.
- **❌ Cancel** - drops the whole draft, nothing is posted anywhere.

If Go Live only partially succeeds (one platform posts, the other errors),
the bot tells you which side already went live and tapping Go Live again
only retries the side that failed (with the same caption/summary) - it never
double-posts. The draft expires 30 minutes after the image was generated;
after that, regenerate the image to publish it. Drafts are saved to
`storage/publish_drafts.json`, so a bot restart doesn't lose a draft that's
waiting for its caption or in review.

**Testing without posting:** set `DRY_RUN=1` and Go Live logs the exact
Instagram/website payload instead of posting. Tests:
`venv/Scripts/python.exe tests/test_manual_ig_caption.py` (always dry-run,
never touches the network).

## Notes

- Each generation costs OpenAI API credits (roughly $0.20-0.35/image). There is
  no per-user allowlist by design - anyone in the group can trigger generations.
- Output isn't guaranteed to be pixel-identical to the reference brand images
  every time (font/logo rendering is model-driven, not template-based) -
  spot-check before posting, especially early on.
# pedimage-generator-insta
