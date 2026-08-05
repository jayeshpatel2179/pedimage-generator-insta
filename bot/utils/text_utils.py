import re

EMPHASIS_PATTERN = re.compile(r"\*\*(.+?)\*\*")


def parse_emphasis(raw_text: str) -> tuple[str, list[str]]:
    """Split a headline into (plain_text, emphasized_phrases).

    Emphasis is marked by the user wrapping words in **double asterisks**.
    The returned plain_text has the asterisks stripped.
    """
    emphasized = [m.group(1).strip() for m in EMPHASIS_PATTERN.finditer(raw_text)]
    plain_text = EMPHASIS_PATTERN.sub(lambda m: m.group(1), raw_text)
    return plain_text.strip(), emphasized
