import base64
import json
import logging
from pathlib import Path

from openai import AsyncOpenAI

from bot.config import CAPTION_MODEL, OPENAI_API_KEY, WEBSITE_SUMMARY_WORD_MAX, WEBSITE_SUMMARY_WORD_MIN

logger = logging.getLogger(__name__)

_client = AsyncOpenAI(api_key=OPENAI_API_KEY)


class SummaryGenerationError(Exception):
    """Raised when the website-summary rewrite call fails or returns something unusable."""


def _image_content(image_path: Path) -> dict:
    # save_telegram_photo always writes a Telegram-compressed "_input.jpg" file - see
    # bot/utils/image_utils.py - so the mime type here is always image/jpeg.
    encoded = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}}


SYSTEM_PROMPT = f"""You are a copy editor for PedTalkSports, a sports news/commentary brand. You'll be \
shown an Instagram post graphic that has already been published, plus a short draft (about 30-40 \
words) the user wrote describing what they want the PedTalks website copy to say about it. Your job \
is to rewrite that draft into a {WEBSITE_SUMMARY_WORD_MIN}-{WEBSITE_SUMMARY_WORD_MAX} word summary \
for the website - a straightforward, informative recap of the story, not a social caption.

This is an EXPANSION, not a light polish - the draft is deliberately shorter than the target length, \
so you must genuinely add natural elaboration, context, and descriptive detail to reach \
{WEBSITE_SUMMARY_WORD_MIN}-{WEBSITE_SUMMARY_WORD_MAX} words. Do not pad with filler or repeat the \
same point in different words just to hit the count.

Rules:
- Preserve every fact, name, number, and claim from the draft exactly - never invent new factual \
claims (scores, dates, quotes, statistics, outcomes) that aren't in the draft.
- Use the image for supporting visual/descriptive detail (what's shown, the mood, visible context \
like team colors or setting) to help expand the writing - but the draft's stated facts always win; \
never contradict the draft with something you infer from the image alone.
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
    raw_summary: str,
    *,
    is_regeneration: bool,
    history: list[str],
) -> str:
    """Expand the user's 30-40 word draft into a WEBSITE_SUMMARY_WORD_MIN-MAX word website summary.

    raw_summary is the user's own text, always the primary source of truth for facts; the image is
    only used for supporting descriptive detail. Returns the rewritten summary text.
    """
    if is_regeneration:
        instruction = (
            "Rewrite the draft again - genuinely different wording/structure from your previous "
            f"attempt(s) below, while preserving the exact same facts, staying "
            f"{WEBSITE_SUMMARY_WORD_MIN}-{WEBSITE_SUMMARY_WORD_MAX} words.\n\n"
            "Your previous rewrite(s) (do not repeat this exact wording):\n"
            + "\n".join(f"- {c}" for c in history)
            + f"\n\nUser's original draft:\n{raw_summary.strip()}"
        )
    else:
        instruction = f"User's draft to expand and rewrite:\n{raw_summary.strip()}"

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
