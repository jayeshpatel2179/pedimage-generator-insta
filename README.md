# PedTalkSports Instagram Image Bot

Telegram bot that turns a headline + a player photo into a PedTalkSports-branded
Instagram graphic using OpenAI's `gpt-image-2` image-edit endpoint.

See `PROJECT_SPEC.md` for the full design (architecture, conversation flow, brand
style rules, and the decisions behind them).

## Setup

1. Create a virtual environment and install dependencies:

   ```
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and fill in the 3 values:
   - `TELEGRAM_BOT_TOKEN` - from @BotFather
   - `OPENAI_API_KEY`
   - `OPENAI_IMAGE_MODEL` - defaults to `gpt-image-2`, only change if needed

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
3. Send the player photo.
4. Wait - the bot generates the graphic in the background and posts it back
   to the chat when ready. Multiple people can do this at the same time; each
   person's request is tracked independently.

`/cancel` aborts a request in progress. A request that gets no reply for 5
minutes times out automatically.

## Notes

- Each generation costs OpenAI API credits (roughly $0.20-0.35/image). There is
  no per-user allowlist by design - anyone in the group can trigger generations.
- Output isn't guaranteed to be pixel-identical to the reference brand images
  every time (font/logo rendering is model-driven, not template-based) -
  spot-check before posting, especially early on.
# pedimage-generator-insta
