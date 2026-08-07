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
- 1-3 short sentences, no more than ~280 characters, no hashtags in this part.
- No emoji spam - at most one, and only if it fits naturally.
- Do not repeat the headline verbatim - riff on it in the page's voice.
- Do not wrap the caption in quotation marks.
- After the caption, leave one blank line, then exactly 3-4 SEO-optimized Instagram hashtags \
relevant to the headline's sport/topic/players, space-separated, each starting with "#", all on \
one line.
- Output nothing else - no labels, no extra commentary.
"""


async def generate_caption(headline: str) -> str:
    plain_headline, _ = parse_emphasis(headline)

    response = await _client.chat.completions.create(
        model=CAPTION_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Headline: {plain_headline}\n\nWrite the Instagram caption."},
        ],
        max_tokens=220,
        temperature=0.9,
    )
    return response.choices[0].message.content.strip()


POSTER_SYSTEM_PROMPT = """You are the Instagram voice of PedTalkSports, a sports hot-take/commentary \
brand. Given a headline that was used to build a graphic, write an engaging Instagram caption to post \
alongside that graphic, plus hashtags.

Voice: confident, punchy, first-person as the page itself (use "we"/"I" as PedTalkSports, not \
third person about it). Sounds like a hot take dropped in the group chat, not a press release.

Rules:
- Caption: 1-3 short sentences, no more than ~280 characters, no hashtags in this part.
- Do not repeat the headline verbatim - riff on it in the page's voice.
- Do not wrap the caption in quotation marks.
- No emoji spam - at most one, and only if it fits naturally.
- After the caption, leave one blank line, then exactly 3-4 SEO-optimized Instagram hashtags \
relevant to the headline's sport/topic/players, space-separated, each starting with "#", all on \
one line.
- Output nothing else - no labels, no extra commentary.
"""


async def generate_caption_with_hashtags(headline: str) -> tuple[str, list[str]]:
    """Used for the automatic post-generation caption suggestion (not the Go Live flow)."""
    plain_headline, _ = parse_emphasis(headline)

    response = await _client.chat.completions.create(
        model=CAPTION_MODEL,
        messages=[
            {"role": "system", "content": POSTER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Headline: {plain_headline}\n\nWrite the Instagram caption and hashtags.",
            },
        ],
        max_tokens=220,
        temperature=0.9,
    )
    content = response.choices[0].message.content.strip()

    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if lines and lines[-1].startswith("#"):
        hashtags = lines[-1].split()
        caption = "\n".join(lines[:-1]).strip()
    else:
        hashtags = []
        caption = content

    return caption, hashtags
