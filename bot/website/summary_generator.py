import base64
import json
import logging
from pathlib import Path

from openai import AsyncOpenAI

from bot.config import CAPTION_MODEL, OPENAI_API_KEY, WEBSITE_SUMMARY_WORD_MAX, WEBSITE_SUMMARY_WORD_MIN
from bot.utils.text_utils import parse_emphasis

logger = logging.getLogger(__name__)

_client = AsyncOpenAI(api_key=OPENAI_API_KEY)


class SummaryGenerationError(Exception):
    """Raised when the website-summary generation call fails or returns something unusable."""


def _image_content(image_path: Path) -> dict:
    # resize_to_final always writes a PNG - see bot/utils/image_utils.py.
    encoded = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}}


SYSTEM_PROMPT = f"""You are a copy editor for PedTalkSports, a sports news/commentary brand. You'll be \
shown a finished Instagram post graphic, the headline it was built from, and the Instagram caption \
already written for it. Your job is to write a {WEBSITE_SUMMARY_WORD_MIN}-{WEBSITE_SUMMARY_WORD_MAX} \
word summary of the same story for the PedTalks website - a straightforward, informative recap, not \
a social caption.

Rules:
- The headline is the primary source of truth for facts - never invent new factual claims (scores, \
dates, quotes, statistics, outcomes) beyond what the headline and caption support.
- Use the Instagram caption for tone/context and the image for supporting visual/descriptive detail \
(what's shown, the mood, visible context like team colors or setting) to help write natural, \
informative prose - but never contradict the headline with something inferred only from the image.
- Target length is {WEBSITE_SUMMARY_WORD_MIN}-{WEBSITE_SUMMARY_WORD_MAX} words. Stay in that range.
- Neutral, clear, third-person news-recap tone - no first-person "we/I", no hot-take voice, no emoji, \
no hashtags, no quotation marks around the whole thing.
- Plain prose, 2-4 sentences.

Respond with STRICT JSON only, no markdown fences, no commentary, exactly this shape:
{{"summary": "..."}}
"""


def _parse_response(content: str) -> str:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise SummaryGenerationError(f"Model returned non-JSON output: {content[:200]!r}") from exc

    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise SummaryGenerationError(f"Model response missing a usable summary: {data!r}")

    summary = summary.strip()
    word_count = len(summary.split())
    if not (WEBSITE_SUMMARY_WORD_MIN <= word_count <= WEBSITE_SUMMARY_WORD_MAX):
        logger.warning(
            "Website summary came back at %d words (target %d-%d): %r",
            word_count, WEBSITE_SUMMARY_WORD_MIN, WEBSITE_SUMMARY_WORD_MAX, summary,
        )

    return summary


async def generate_website_summary(
    image_path: Path,
    headline: str,
    caption: str,
    *,
    is_regeneration: bool,
    history: list[str],
) -> str:
    """Write a WEBSITE_SUMMARY_WORD_MIN-MAX word website summary from the same headline, Instagram
    caption, and generated image already used for the Instagram post. Returns the summary text.
    """
    plain_headline, _ = parse_emphasis(headline)

    if is_regeneration:
        instruction = (
            "Write the summary again - genuinely different wording/structure from your previous "
            f"attempt(s) below, while preserving the same facts, staying "
            f"{WEBSITE_SUMMARY_WORD_MIN}-{WEBSITE_SUMMARY_WORD_MAX} words.\n\n"
            "Your previous attempt(s) (do not repeat this exact wording):\n"
            + "\n".join(f"- {c}" for c in history)
            + f"\n\nHeadline: {plain_headline}\n\nInstagram caption already written for this post:\n{caption.strip()}"
        )
    else:
        instruction = (
            f"Headline: {plain_headline}\n\n"
            f"Instagram caption already written for this post:\n{caption.strip()}\n\n"
            "Write the website summary."
        )

    user_parts = [
        {"type": "text", "text": instruction},
        _image_content(image_path),
    ]

    try:
        response = await _client.chat.completions.create(
            model=CAPTION_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_parts},
            ],
            temperature=0.9 if is_regeneration else 0.7,
            max_tokens=250,
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        raise SummaryGenerationError(f"Vision API call failed: {exc}") from exc

    content = response.choices[0].message.content
    if not content:
        raise SummaryGenerationError("Model returned an empty response")

    return _parse_response(content)
