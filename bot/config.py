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


def _parse_user_ids(raw: str | None) -> frozenset[int]:
    if not raw:
        return frozenset()
    return frozenset(int(part.strip()) for part in raw.split(",") if part.strip())


# Comma-separated Telegram user IDs allowed to tap "Go Live". Empty/unset means
# unrestricted - anyone in the group can use it, same as image generation
# (decided this way for PedTalkSports: the group is small and trusted, and the
# feature is meant for whichever editor is around, not one fixed person).
GO_LIVE_ALLOWED_USER_IDS = _parse_user_ids(os.getenv("GO_LIVE_ALLOWED_USER_IDS"))

# Hardcoded by design (see PROJECT_SPEC.md) - not meant to vary at runtime.
GENERATION_SIZE = "1024x1024"
FINAL_SIZE = (1080, 1080)
CONVERSATION_TIMEOUT_SECONDS = 300
TEMP_DIR = Path(__file__).resolve().parent.parent / "storage" / "temp"
LOG_LEVEL = "INFO"
CAPTION_MODEL = "gpt-4o-mini"
GO_LIVE_DRAFT_EXPIRY_SECONDS = 30 * 60

TEMP_DIR.mkdir(parents=True, exist_ok=True)
