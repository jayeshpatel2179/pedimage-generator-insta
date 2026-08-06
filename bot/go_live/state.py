import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from bot.config import GO_LIVE_DRAFT_EXPIRY_SECONDS

logger = logging.getLogger(__name__)


@dataclass
class GoLiveDraft:
    """In-memory record of one generated image waiting on a Go Live decision.

    Lost on redeploy by design - acceptable per spec, the image just needs
    to be regenerated if that happens mid-flow.
    """

    id: str
    image_path: Path
    headline: str
    chat_id: int
    message_id: Optional[int] = None
    caption: Optional[str] = None
    caption_message_id: Optional[int] = None
    status: str = "awaiting_golive"
    created_at: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_drafts: dict[str, GoLiveDraft] = {}
_by_caption_message: dict[tuple[int, int], str] = {}


def create_draft(image_path: Path, headline: str, chat_id: int) -> GoLiveDraft:
    draft_id = uuid.uuid4().hex[:8]
    while draft_id in _drafts:
        draft_id = uuid.uuid4().hex[:8]
    draft = GoLiveDraft(id=draft_id, image_path=image_path, headline=headline, chat_id=chat_id)
    _drafts[draft_id] = draft
    return draft


def get_draft(draft_id: str) -> Optional[GoLiveDraft]:
    return _drafts.get(draft_id)


def is_expired(draft: GoLiveDraft) -> bool:
    return (time.monotonic() - draft.created_at) > GO_LIVE_DRAFT_EXPIRY_SECONDS


def pop_draft(draft_id: str) -> Optional[GoLiveDraft]:
    draft = _drafts.pop(draft_id, None)
    if draft and draft.caption_message_id is not None:
        _by_caption_message.pop((draft.chat_id, draft.caption_message_id), None)
    return draft


def attach_image_message(draft: GoLiveDraft, message_id: int) -> None:
    draft.message_id = message_id


def set_caption_message(draft: GoLiveDraft, message_id: int) -> None:
    if draft.caption_message_id is not None:
        _by_caption_message.pop((draft.chat_id, draft.caption_message_id), None)
    draft.caption_message_id = message_id
    _by_caption_message[(draft.chat_id, message_id)] = draft.id


def get_draft_by_caption_message(chat_id: int, message_id: int) -> Optional[GoLiveDraft]:
    draft_id = _by_caption_message.get((chat_id, message_id))
    return _drafts.get(draft_id) if draft_id else None


def schedule_expiry_cleanup(application, draft_id: str) -> None:
    application.create_task(_expire_after_timeout(draft_id))


async def _expire_after_timeout(draft_id: str) -> None:
    await asyncio.sleep(GO_LIVE_DRAFT_EXPIRY_SECONDS)
    draft = _drafts.get(draft_id)
    if draft is None or draft.status == "posted":
        return
    logger.info("Go Live draft %s expired without resolving; cleaning up temp file", draft_id)
    pop_draft(draft_id)
    draft.image_path.unlink(missing_ok=True)
