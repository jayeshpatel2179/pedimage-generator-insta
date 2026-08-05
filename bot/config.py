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

# Hardcoded by design (see PROJECT_SPEC.md) - not meant to vary at runtime.
GENERATION_SIZE = "1024x1024"
FINAL_SIZE = (1080, 1080)
CONVERSATION_TIMEOUT_SECONDS = 300
TEMP_DIR = Path(__file__).resolve().parent.parent / "storage" / "temp"
LOG_LEVEL = "INFO"

TEMP_DIR.mkdir(parents=True, exist_ok=True)
