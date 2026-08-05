# PedTalkSports Instagram Image Bot — Project Spec

## 1. Overview

A Telegram bot (Python) used inside a company Telegram group. A user sends a headline text, the bot asks for a player photo, the user sends it, and the bot combines both into a finished PedTalkSports-branded Instagram graphic using OpenAI's `gpt-image-2` image-edit endpoint. Output is a single generated image sent back into the chat.

Architecture: **Option A — full AI generation.** No local template compositing (Pillow/rembg) is used to build the graphic; `gpt-image-2` receives the player photo + a detailed text prompt describing the full brand layout and returns the finished image directly.

## 2. Reference Brand Style (extracted from the two sample images)

These are the fixed visual rules every generated image must follow. They get baked into the prompt sent to `gpt-image-2` on every request — they are not user-configurable.

**Layout, base style ("quote card" — v1 default):**
- 1:1 square, cream/tan textured panel on one side with a rough torn/spray paper edge where it meets the photo, player photo bleeding full-height on the other side.
- Headline in a bold, heavy, condensed sans-serif (Anton/Archivo Black/Oswald Bold family), all caps, stacked in short line breaks.
- Emphasis words within the headline rendered in dark maroon/red (~`#7B1E23`); the rest of the headline in near-black (~`#1A1A1A`).
- Thin maroon horizontal divider line with a small diamond/star glyph centered on it, beneath the headline.

**Fixed footer block (verbatim on every image, bottom of the text panel):**
- "PedTalkSports" in bold maroon, immediately followed by a blue verified-style checkmark badge.
- Beneath it, "@PedTalkSports" in black.
- A thin horizontal divider line.
- A small black circular badge containing a white "X" (Twitter/X logo).
- Two lines of smaller black text next to that badge: "Want our live reactions before Instagram?" / "Follow **@PedTalkSports** on X." (handle bolded).

**Color palette:** cream/tan background ~`#E8DCC3`, maroon/dark red ~`#7B1E23`, near-black ~`#1A1A1A`, navy (only used in the stats-card variant) ~`#16233F`.

**Secondary style ("stats card" — not built in v1, noted for future prompt variant):** torn-paper red/cream/navy collage background instead of the flat cream panel, adds a right-hand stat column (e.g. "8 GOALS / 4 ASSISTS / 7 MATCHES") next to the photo. Same footer block. Deferred — v1 ships the quote-card style only, since it's the more general-purpose layout for arbitrary hot-take text. Extending to the stats style later is a prompt-template change, not an architecture change.

## 3. Conversation Flow

1. User sends a plain text message → bot stores it as the headline for that `(chat_id, user_id)`.
2. Bot replies with an inline-keyboard prompt: **"Text on the Left" / "Text on the Right"**. User taps one → stored as the layout side.
3. Bot asks for the player photo.
4. User sends a photo → bot downloads the highest-resolution file.
5. Bot sends a "generating…" chat action, fires the generation as a background task (see §7), and continues listening for other users in the meantime.
6. On success: bot sends the finished image back into the chat as a photo.
7. On failure (API error, safety refusal, network issue): bot sends a plain-language error and lets the user retry from step 1.
8. `/cancel` aborts the flow at any point. If 5 minutes pass with no reply to the bot's last prompt, the conversation auto-resets (`conversation_timeout=300`, hardcoded).

**Marking emphasis words:** the user wraps words in `**double asterisks**` in their headline message to mark them for maroon/red styling (e.g. `Performance so good he rinsed the **"rigged" narrative** in one match`). Anything not wrapped stays black. This was an open design question — decided in favor of manual marking over auto-detection, since auto-picking "important" words from arbitrary text is unreliable and would produce inconsistent emphasis choices between posts.

## 4. Telegram Group Setup Requirements (action items for you, not code)

- **Privacy mode must be disabled** for the bot via BotFather (`/setprivacy` → Disable), OR the bot must be promoted to **admin** in the group. Without one of these, Telegram hides plain text/photo messages from the bot in a group — it would only ever see `/commands` or `@mentions`, breaking this flow.
- After changing the privacy setting, **remove and re-add the bot to the group** — Telegram only applies the change on rejoin.
- No user allowlist — anyone in the group can trigger the bot. Each generation costs real OpenAI credits (~$0.20–0.35/image); this is an accepted tradeoff since it's a controlled company group, not a public bot.

## 5. Environment Variables (exactly 3)

| Variable | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather |
| `OPENAI_API_KEY` | OpenAI API key |
| `OPENAI_IMAGE_MODEL` | Defaults to `gpt-image-2`; kept swappable without a code change |

## 6. Hardcoded Configuration Constants (not env vars, by design)

- Output image size requested from the API: `1024x1024` (a valid gpt-image-2 size), resized to `1080x1080` after the fact for exact Instagram spec — a one-line Pillow resize, no re-generation.
- `conversation_timeout = 300` seconds (5 minutes).
- Temp storage directory, log level: fixed constants in `config.py`.
- If any of these ever need to change, it's a direct code edit — not meant to vary at runtime.

## 7. Concurrency Handling

`ConversationHandler` defaults to `per_chat=True, per_user=True`, which keys conversation state as `(chat_id, user_id)` — multiple people in the same company group mid-flow at once are tracked independently by the library out of the box, no extra logic needed.

Because `gpt-image-2` calls take several seconds, the actual generation call is dispatched as a background task from inside the photo handler (rather than awaited inline), so the bot keeps responding to other users in the group while one or more generations are in flight. Each result is pushed back to the originating user/chat whenever their generation finishes, independent of anyone else's.

## 8. Folder Structure

```
pedtalks-insta-bot/
├── bot/
│   ├── main.py                  # Application builder, registers handlers, starts polling
│   ├── config.py                # loads the 3 env vars + hardcoded constants
│   ├── handlers/
│   │   ├── start.py             # /start, /help
│   │   ├── conversation.py      # text -> layout choice -> photo -> generate
│   │   └── cancel.py
│   ├── generation/
│   │   ├── ai_generator.py      # gpt-image-2 images.edit wrapper
│   │   └── prompt_builder.py    # builds the full brand-styled prompt from headline + emphasis + layout side
│   └── utils/
│       ├── image_utils.py       # download photo, resize 1024->1080, format handling
│       └── text_utils.py        # parses **emphasis** markup out of the raw headline
├── storage/temp/                 # per-request scratch files, cleaned after send
├── .env.example
├── requirements.txt
├── README.md
└── run.py
```

`generation/prompt_builder.py` is the most important file in this design — it encodes the entire brand style guide from §2 as a reusable prompt template, with the headline text, emphasis-marked words, and chosen layout side substituted in per request.

## 9. Dependencies

`python-telegram-bot` (v21+, async), `openai` (v1.x SDK), `python-dotenv`, `Pillow` (only for the final 1024→1080 resize, not for compositing).

## 10. Known Risks / Edge Cases

- `gpt-image-2` may occasionally refuse or flag an edit involving a real person's likeness — surfaced to the user as a retry-friendly error, not a crash.
- Exact pixel-perfect brand consistency (logo shape, exact footer kerning) is **not guaranteed** every single time since this is prompt-driven generation, not template compositing (this was the tradeoff explicitly accepted when choosing Option A over Option B) — spot-check outputs before posting to Instagram, especially early on.
- Telegram photo compression: photos sent via Telegram's normal photo attachment get compressed; if downstream image quality matters, consider asking users to send the photo as a "file/document" instead of a compressed photo — worth deciding once you see real output quality.

## 11. Next Steps

Ready to implement against this spec. Confirm the two judgment calls made above (emphasis via `**bold**` markup, quote-card-only for v1) — or adjust — before code is written.
