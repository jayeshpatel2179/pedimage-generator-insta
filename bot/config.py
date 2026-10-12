import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


TELEGRAM_BOT_TOKEN = _require_env("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = _require_env("OPENAI_API_KEY")
OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2")

# Not required at startup: the existing generation flow must keep working even
# before these are configured. Missing values are only surfaced as an in-chat
# error when Go Live is actually used (see go_live/uploadpost_client.py).
UPLOAD_POST_API_KEY = os.getenv("UPLOAD_POST_API_KEY")
UPLOAD_POST_PROFILE = os.getenv("UPLOAD_POST_PROFILE")

# Same posture as UPLOAD_POST_API_KEY above: not required at startup, only
# surfaced as an in-chat error when "Add Website Summary" is actually used
# (see website/website_client.py).
WEBSITE_UPLOAD_API_KEY = os.getenv("WEBSITE_UPLOAD_API_KEY")
WEBSITE_UPLOAD_URL = os.getenv("WEBSITE_UPLOAD_URL") or "https://www.pedtalkssports.com/api/instagram/upload"


def _parse_user_ids(raw: str | None) -> frozenset[int]:
    if not raw:
        return frozenset()
    return frozenset(int(part.strip()) for part in raw.split(",") if part.strip())


# Comma-separated Telegram user IDs allowed to publish (the merged Instagram +
# website Go Live flow). Empty/unset means unrestricted - anyone in the group can
# use it, same as image generation (decided this way for PedTalkSports: the group
# is small and trusted, and the feature is meant for whichever editor is around,
# not one fixed person). Env var name kept as GO_LIVE_ALLOWED_USER_IDS for
# deployment compatibility even though it now gates the whole publish pipeline.
GO_LIVE_ALLOWED_USER_IDS = _parse_user_ids(os.getenv("GO_LIVE_ALLOWED_USER_IDS"))

# Hardcoded by design (see PROJECT_SPEC.md) - not meant to vary at runtime.
GENERATION_SIZE = "1024x1024"
FINAL_SIZE = (1080, 1080)
CONVERSATION_TIMEOUT_SECONDS = 300
TEMP_DIR = Path(__file__).resolve().parent.parent / "storage" / "temp"
LOG_LEVEL = "INFO"
CAPTION_MODEL = "gpt-4o-mini"

# Merged publish flow (bot/publish/, bot/handlers/publish.py): caption+hashtags
# review, then website summary review, then simultaneous Instagram+website Go Live.
PUBLISH_DRAFT_EXPIRY_SECONDS = 30 * 60

# Publish drafts are persisted here (JSON) so a draft awaiting its Instagram caption - or
# sitting in review - survives a bot restart. Lives next to TEMP_DIR on purpose: the draft
# is useless without its generated image, so both share the same lifetime.
PUBLISH_DRAFTS_FILE = TEMP_DIR.parent / "publish_drafts.json"

# Manual Instagram caption (bot/go_live/ig_caption.py). Limits verified against Meta's IG
# media docs ("Maximum 2200 characters, 30 hashtags, and 20 @ tags"):
# https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media
IG_CAPTION_MAX_CHARS = 2200
IG_CAPTION_MAX_HASHTAGS = 30
IG_CAPTION_MAX_MENTIONS = 20
# Appended to every manual caption at post time. Empty by default - the caption is posted
# exactly as the editor typed it.
IG_CAPTION_SUFFIX = ""

# DRY_RUN=1 makes the posting layer (Instagram + website clients) log the exact outgoing
# payload instead of posting. Off by default.
DRY_RUN = os.getenv("DRY_RUN", "").strip().lower() in ("1", "true", "yes", "on")

# Website summary generation (bot/website/summary_generator.py).
WEBSITE_SUMMARY_WORD_MIN = 50
WEBSITE_SUMMARY_WORD_MAX = 60
WEBSITE_SUMMARY_HISTORY_LIMIT = 3  # how many previous rewrites get fed back in to steer regenerations away from repeats

TEMP_DIR.mkdir(parents=True, exist_ok=True)
