import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from bot.config import PUBLISH_DRAFT_EXPIRY_SECONDS

logger = logging.getLogger(__name__)


@dataclass
class PublishDraft:
    """In-memory record of one generated image moving through the merged publish pipeline:
    auto caption+hashtags generation -> Save -> auto website summary generation -> simultaneous
    Instagram + website Go Live.

    Lost on redeploy by design - acceptable per spec, the image just needs to be regenerated
    if that happens mid-flow.
    """

    id: str
    image_path: Path
    headline: str
    chat_id: int
    image_message_id: Optional[int] = None
    status: str = "generating_caption"
    # generating_caption | awaiting_caption_review | regenerating_caption | failed_caption |
    # generating_summary | awaiting_summary_review | regenerating_summary | failed_summary |
    # posting | posted

    caption: Optional[str] = None
    caption_message_id: Optional[int] = None

    summary: Optional[str] = None
    summary_message_id: Optional[int] = None
    summary_history: list[str] = field(default_factory=list)

    instagram_posted: bool = False
    instagram_url: Optional[str] = None
    website_posted: bool = False

    created_at: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_drafts: dict[str, PublishDraft] = {}
_by_caption_message: dict[tuple[int, int], str] = {}


def create_draft(image_path: Path, headline: str, chat_id: int) -> PublishDraft:
    draft_id = uuid.uuid4().hex[:8]
    while draft_id in _drafts:
        draft_id = uuid.uuid4().hex[:8]
    draft = PublishDraft(id=draft_id, image_path=image_path, headline=headline, chat_id=chat_id)
    _drafts[draft_id] = draft
    return draft


def get_draft(draft_id: str) -> Optional[PublishDraft]:
    return _drafts.get(draft_id)


def is_expired(draft: PublishDraft) -> bool:
    return (time.monotonic() - draft.created_at) > PUBLISH_DRAFT_EXPIRY_SECONDS


def pop_draft(draft_id: str) -> Optional[PublishDraft]:
    draft = _drafts.pop(draft_id, None)
    if draft and draft.caption_message_id is not None:
        _by_caption_message.pop((draft.chat_id, draft.caption_message_id), None)
    return draft


def attach_image_message(draft: PublishDraft, message_id: int) -> None:
    draft.image_message_id = message_id


def set_caption_message(draft: PublishDraft, message_id: int) -> None:
    if draft.caption_message_id is not None:
        _by_caption_message.pop((draft.chat_id, draft.caption_message_id), None)
    draft.caption_message_id = message_id
    _by_caption_message[(draft.chat_id, message_id)] = draft.id


def get_draft_by_caption_message(chat_id: int, message_id: int) -> Optional[PublishDraft]:
    draft_id = _by_caption_message.get((chat_id, message_id))
    return _drafts.get(draft_id) if draft_id else None


def schedule_expiry_cleanup(application, draft_id: str) -> None:
    application.create_task(_expire_after_timeout(draft_id))


async def _expire_after_timeout(draft_id: str) -> None:
    await asyncio.sleep(PUBLISH_DRAFT_EXPIRY_SECONDS)
    draft = _drafts.get(draft_id)
    if draft is None or draft.status == "posted":
        return
    logger.info("Publish draft %s expired without resolving; cleaning up temp file", draft_id)
    pop_draft(draft_id)
    draft.image_path.unlink(missing_ok=True)
