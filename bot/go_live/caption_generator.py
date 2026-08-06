from openai import AsyncOpenAI

from bot.config import CAPTION_MODEL, OPENAI_API_KEY
from bot.utils.text_utils import parse_emphasis

_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = """You are the Instagram voice of PedTalkSports, a sports hot-take/commentary \
brand. Given a headline that was used to build a graphic, write ONE short Instagram caption \
to post alongside that graphic.

Voice: confident, punchy, first-person as the page itself (use "we"/"I" as PedTalkSports, not \
third person about it). Sounds like a hot take dropped in the group chat, not a press release.

Rules:
- 1-3 short sentences, no more than ~280 characters total.
- No hashtags unless one genuinely adds punch (max 1).
- No emoji spam - at most one, and only if it fits naturally.
- Do not repeat the headline verbatim - riff on it in the page's voice.
- Do not wrap the caption in quotation marks.
- Output only the caption text, nothing else.
"""


async def generate_caption(headline: str) -> str:
    plain_headline, _ = parse_emphasis(headline)

    response = await _client.chat.completions.create(
        model=CAPTION_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Headline: {plain_headline}\n\nWrite the Instagram caption."},
        ],
        max_tokens=200,
        temperature=0.9,
    )
    return response.choices[0].message.content.strip()
