import uuid
from pathlib import Path

from PIL import Image
from telegram import File

from bot.config import FINAL_SIZE, TEMP_DIR


async def save_telegram_photo(file: File, chat_id: int) -> Path:
    dest = TEMP_DIR / f"{chat_id}_{uuid.uuid4().hex}_input.jpg"
    await file.download_to_drive(custom_path=str(dest))
    return dest


def resize_to_final(image_bytes: bytes, chat_id: int) -> Path:
    dest = TEMP_DIR / f"{chat_id}_{uuid.uuid4().hex}_output.png"
    tmp_path = dest.with_suffix(".raw.png")
    tmp_path.write_bytes(image_bytes)

    with Image.open(tmp_path) as img:
        img = img.convert("RGB").resize(FINAL_SIZE, Image.Resampling.LANCZOS)
        img.save(dest, format="PNG")

    tmp_path.unlink(missing_ok=True)
    return dest


def cleanup(*paths: Path) -> None:
    for path in paths:
        if path is not None:
            Path(path).unlink(missing_ok=True)
