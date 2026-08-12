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


def _extract_instagram_url(response: dict) -> str | None:
    """Returns the published post URL, or None if Upload-Post accepted the post but only
    confirms it asynchronously (see the "backgrounded" shape below - no URL available yet)."""
    if not isinstance(response, dict):
        raise UploadPostError(f"Unexpected response from Upload-Post: {response}")

    results = response.get("results")
    if isinstance(results, dict):
        instagram_result = results.get("instagram")
        if not isinstance(instagram_result, dict):
            raise UploadPostError(f"Unexpected response from Upload-Post: {response}")

        if instagram_result.get("success") is False:
            error = instagram_result.get("error") or "unknown error"
            raise UploadPostError(f"Instagram publish failed: {error}")

        url = instagram_result.get("url")
        if not url:
            raise UploadPostError(f"Upload-Post didn't return a post URL: {response}")

        return url

    # Upload-Post sometimes hands a slow request off to a background worker instead of
    # returning the synchronous {"results": {...}} shape above - it still reports success
    # (per its own "success": true), just without a URL yet. See request_id/job_id for
    # status polling (https://docs.upload-post.com/api/upload-status), which itself
    # doesn't expose a post URL either - so there's nothing further to wait for here.
    if response.get("success") is True and (response.get("request_id") or response.get("job_id")):
        return None

    raise UploadPostError(f"Unexpected response from Upload-Post: {response}")


async def upload_photo_to_instagram(image_path: Path, caption: str) -> str | None:
    """Publish an already-generated image to the PedTalkSports Instagram feed.

    Runs the (blocking, requests-based) SDK call in a thread so the bot's
    event loop keeps serving other chats while it's in flight. Returns the
    published post URL, or None if Upload-Post only confirmed the post
    asynchronously (see _extract_instagram_url).
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
