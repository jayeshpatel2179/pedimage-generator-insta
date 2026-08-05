BRAND_TEMPLATE = """Create a square 1:1 Instagram graphic in the exact visual style of a sports commentary brand called "PedTalkSports".

LAYOUT:
- The image is split vertically into two halves: a text panel and the athlete photo.
- {text_side} half: a solid cream/tan textured background (~#E8DCC3) with a rough, torn/spray-paint style vertical edge where it meets the photo half.
- {photo_side} half: the provided athlete photo, cropped and composed to bleed to the outer edges of that half, full height, sharp sports-action photography look. Keep the athlete clearly recognizable and do not alter their face.

HEADLINE TEXT (place in the text panel, stacked across several short line breaks, ALL UPPERCASE, in a bold heavy condensed sans-serif display font such as Anton or Archivo Black):
{headline}

{emphasis_instruction}
All other headline words must be near-black (~#1A1A1A).

Beneath the headline, add a thin maroon horizontal divider line with a small diamond glyph centered on it.

FIXED FOOTER (bottom of the text panel, small text, must appear exactly as described and nowhere else in the image):
- "PedTalkSports" in bold maroon, immediately followed by a small blue circular verified-checkmark badge.
- Below that, "@PedTalkSports" in black.
- A thin black horizontal divider line.
- A small black circular badge containing a white "X" (Twitter/X) logo, next to two lines of small black text: "Want our live reactions before Instagram?" and "Follow @PedTalkSports on X." with "@PedTalkSports" in bold.

Do not add any other text, captions, watermarks, or logos beyond what is described above. Clean, professional sports-media graphic design quality, sharp focus, high production value, photorealistic athlete photo.
"""


def build_prompt(headline: str, emphasis: list[str], layout_side: str) -> str:
    if layout_side == "left":
        text_side, photo_side = "Left", "Right"
    else:
        text_side, photo_side = "Right", "Left"

    if emphasis:
        joined = " | ".join(emphasis)
        emphasis_instruction = (
            f"Render exactly these words/phrases from the headline in dark maroon/red "
            f"(~#7B1E23), separated here by ' | ': {joined}."
        )
    else:
        emphasis_instruction = (
            "Use your judgment to render one or two of the most impactful words or "
            "phrases in dark maroon/red (~#7B1E23)."
        )

    return BRAND_TEMPLATE.format(
        text_side=text_side,
        photo_side=photo_side,
        headline=headline,
        emphasis_instruction=emphasis_instruction,
    )
