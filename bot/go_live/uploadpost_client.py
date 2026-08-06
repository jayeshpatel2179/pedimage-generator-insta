import asyncio
from pathlib import Path

from upload_post import UploadPostClient
from upload_post import UploadPostError as _SDKUploadPostError

from bot.config import UPLOAD_POST_API_KEY, UPLOAD_POST_PROFILE


class UploadPostError(Exception):
    """Raised when publishing to Instagram via Upload-Post fails."""


def _upload_sync(image_path: Path, caption: str) -> dict:
    if not UPLOAD_POST_API_KEY or not UPLOAD_POST_PROFILE:
        raise UploadPostError(
            "Go Live isn't configured yet - missing UPLOAD_POST_API_KEY / UPLOAD_POST_PROFILE."
        )

    client = UploadPostClient(api_key=UPLOAD_POST_API_KEY)
    return client.upload_photos(
        [str(image_path)],
        title=caption,
        user=UPLOAD_POST_PROFILE,
        platforms=["instagram"],
        media_type="IMAGE",
    )


def _extract_instagram_url(response: dict) -> str:
    results = response.get("results") if isinstance(response, dict) else None
    instagram_result = results.get("instagram") if isinstance(results, dict) else None

    if not isinstance(instagram_result, dict):
        raise UploadPostError(f"Unexpected response from Upload-Post: {response}")

    if instagram_result.get("success") is False:
        error = instagram_result.get("error") or "unknown error"
        raise UploadPostError(f"Instagram publish failed: {error}")

    url = instagram_result.get("url")
    if not url:
        raise UploadPostError(f"Upload-Post didn't return a post URL: {response}")

    return url


async def upload_photo_to_instagram(image_path: Path, caption: str) -> str:
    """Publish an already-generated image to the PedTalkSports Instagram feed.

    Runs the (blocking, requests-based) SDK call in a thread so the bot's
    event loop keeps serving other chats while it's in flight.
    """
    try:
        response = await asyncio.to_thread(_upload_sync, image_path, caption)
    except UploadPostError:
        raise
    except _SDKUploadPostError as exc:
        raise UploadPostError(str(exc)) from exc
    except Exception as exc:
        raise UploadPostError(f"Upload-Post request failed: {exc}") from exc

    return _extract_instagram_url(response)
