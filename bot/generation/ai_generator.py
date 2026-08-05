import base64
from pathlib import Path

from openai import AsyncOpenAI

from bot.config import GENERATION_SIZE, OPENAI_API_KEY, OPENAI_IMAGE_MODEL

_client = AsyncOpenAI(api_key=OPENAI_API_KEY)


async def generate_image(photo_path: Path, prompt: str) -> bytes:
    with open(photo_path, "rb") as photo_file:
        result = await _client.images.edit(
            model=OPENAI_IMAGE_MODEL,
            image=[photo_file],
            prompt=prompt,
            size=GENERATION_SIZE,
            quality="high",
        )

    return base64.b64decode(result.data[0].b64_json)
