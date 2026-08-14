import base64
from pathlib import Path

from openai import AsyncOpenAI

from bot.config import CAPTION_MODEL, OPENAI_API_KEY
from bot.utils.text_utils import parse_emphasis

_client = AsyncOpenAI(api_key=OPENAI_API_KEY)


def _image_content(image_path: Path) -> dict:
    encoded = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}}


SYSTEM_PROMPT = """You are the Instagram voice of PedTalkSports, a sports hot-take/commentary \
brand. You're given the headline used to build a graphic AND the finished graphic image itself - \
look at the image for context (who/what is actually shown, the mood, any visible detail) and write \
ONE short Instagram caption to post alongside it.

Voice: confident, punchy, first-person as the page itself (use "we"/"I" as PedTalkSports, not \
third person about it). Sounds like a hot take dropped in the group chat, not a press release.

CRITICAL - match the headline's tone and opinion, not just its wording: if the headline is \
critical, sarcastic, mocking, angry, or otherwise negative about the subject, the caption must land \
the same way - keep the sting, don't file it down. If it's hyped, proud, or celebratory, match that \
instead. Do not default to a positive/supportive spin just because that's the safer or more typical \
sports-page tone - inventing enthusiasm the headline doesn't contain is as wrong as misrepresenting \
a fact. Example: a headline mocking a player's ego over a transfer should produce a caption that \
mocks that ego, in PedTalkSports' voice - not one that celebrates the move.

Rules:
- 1-3 short sentences, no more than ~280 characters, no hashtags in this part.
- Ground the caption in what's actually visible in the image, not just the headline wording.
- No emoji spam - at most one, and only if it fits naturally.
- Do not repeat the headline verbatim - riff on it in the page's voice.
- Do not wrap the caption in quotation marks.
- After the caption, leave one blank line, then exactly 3-4 SEO-optimized Instagram hashtags \
relevant to what's shown in the image and the headline's sport/topic/players, space-separated, \
each starting with "#", all on one line.
- Output nothing else - no labels, no extra commentary.
"""


async def generate_caption(headline: str, image_path: Path) -> str:
    plain_headline, _ = parse_emphasis(headline)

    response = await _client.chat.completions.create(
        model=CAPTION_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"Headline: {plain_headline}\n\nWrite the Instagram caption.",
                    },
                    _image_content(image_path),
                ],
            },
        ],
        max_tokens=220,
        temperature=0.9,
    )
    return response.choices[0].message.content.strip()
