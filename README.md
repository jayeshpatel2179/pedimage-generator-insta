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
     "Go Live" button (image generation still works without these set)
   - `WEBSITE_UPLOAD_API_KEY` - required for the "Add Website Summary" /
     "Go Live Website" flow (everything else, including Instagram Go Live,
     still works without it)
   - `WEBSITE_UPLOAD_URL` - optional override; defaults to
     `https://www.pedtalkssports.com/api/instagram/upload`
   - `GO_LIVE_ALLOWED_USER_IDS` - optional, comma-separated Telegram user IDs.
     Leave empty to let anyone in the group use Go Live / website posting.

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
4. Wait - the bot generates the graphic in the background and posts it back
   to the chat with a **Go Live** button. Multiple people can do this at the
   same time; each person's request is tracked independently.

`/cancel` aborts a request in progress. A request that gets no reply for 5
minutes times out automatically.

### Go Live (posting to Instagram)

Every generated image is sent with a **Go Live** button:

1. Tap **Go Live** - the bot generates a first-person IG caption (a separate
   `gpt-4o-mini` call, not the on-image headline text) and posts a preview.
2. Reply to the caption preview with new text to edit it, or tap
   **Regenerate** for another AI attempt.
3. Tap **Confirm & Post** to publish to the PedTalkSports Instagram account
   via [upload-post.com](https://www.upload-post.com/). On success the bot
   replies with the live post URL.

The draft (image + caption) expires 30 minutes after the image was generated;
after that, regenerate the image to post it. If posting fails (API/network
error) the confirm button stays retryable - no need to regenerate.

### Posting to the website

Once a Go Live post to Instagram succeeds, the confirmation message gets an
**📝 Add Website Summary** button:

1. Tap it - the bot asks you to resend that same image, this time with a
   **30-40 word plain-text description** of what you want the website copy to
   say about it, written as the photo's caption.
2. Send that photo + caption. The bot rewrites it into an exact **50-60 word**
   summary (a genuine expansion, not just a polish, since your draft is
   shorter than the target) and posts it back for review with three buttons:
   - **🌐 Go Live Website** - publishes the image + summary to the PedTalks
     website.
   - **🔄 Regenerate** - produces a different 50-60 word rewrite of the same
     facts without resending the image.
   - **❌ Abort** - drops the draft, nothing is posted.

This is a separate leg from Instagram Go Live and uses its own 30-minute
draft expiry. Tapping "Add Website Summary" only arms the *next* photo you
send for this flow - it doesn't affect normal generation requests, and
un-tapped buttons never intercept anything.

## Notes

- Each generation costs OpenAI API credits (roughly $0.20-0.35/image). There is
  no per-user allowlist by design - anyone in the group can trigger generations.
- Output isn't guaranteed to be pixel-identical to the reference brand images
  every time (font/logo rendering is model-driven, not template-based) -
  spot-check before posting, especially early on.
# pedimage-generator-insta
