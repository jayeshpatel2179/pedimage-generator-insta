import re

from bot.config import (
    IG_CAPTION_MAX_CHARS,
    IG_CAPTION_MAX_HASHTAGS,
    IG_CAPTION_MAX_MENTIONS,
    IG_CAPTION_SUFFIX,
)
from bot.publish import texts

# A "#"/"@" not glued to a preceding word character, followed by at least one tag character.
# The lookbehind keeps emails ("a@b.com") and the like from counting as tags.
_HASHTAG_PATTERN = re.compile(r"(?<!\w)#[^\s#@]")
_MENTION_PATTERN = re.compile(r"(?<![\w.])@[A-Za-z0-9._]")


def count_hashtags(text: str) -> int:
    return len(_HASHTAG_PATTERN.findall(text))


def count_mentions(text: str) -> int:
    return len(_MENTION_PATTERN.findall(text))


def final_caption(caption: str) -> str:
    """The exact text sent to Instagram: the manual caption plus IG_CAPTION_SUFFIX (empty by default)."""
    return caption + IG_CAPTION_SUFFIX


def validation_error(caption: str | None) -> str | None:
    """Returns a user-facing error for a caption Instagram would reject, or None if it's postable.

    Never alters the caption - the editor has to fix it and resend.
    """
    if caption is None or not caption.strip():
        return texts.CAPTION_EMPTY

    # Room the suffix takes up comes out of the editor's allowance.
    char_limit = IG_CAPTION_MAX_CHARS - len(IG_CAPTION_SUFFIX)
    hashtag_limit = IG_CAPTION_MAX_HASHTAGS - count_hashtags(IG_CAPTION_SUFFIX)
    mention_limit = IG_CAPTION_MAX_MENTIONS - count_mentions(IG_CAPTION_SUFFIX)

    if len(caption) > char_limit:
        return texts.CAPTION_TOO_LONG.format(count=len(caption), limit=char_limit)
    hashtags = count_hashtags(caption)
    if hashtags > hashtag_limit:
        return texts.CAPTION_TOO_MANY_HASHTAGS.format(count=hashtags, limit=hashtag_limit)
    mentions = count_mentions(caption)
    if mentions > mention_limit:
        return texts.CAPTION_TOO_MANY_MENTIONS.format(count=mentions, limit=mention_limit)
    return None
