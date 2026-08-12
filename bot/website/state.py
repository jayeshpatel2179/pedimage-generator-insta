import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from bot.config import WEBSITE_DRAFT_EXPIRY_SECONDS, WEBSITE_PENDING_EXPIRY_SECONDS

logger = logging.getLogger(__name__)


@dataclass
class WebsiteDraft:
    """In-memory record of one image+summary moving through the website posting flow.

    Lost on redeploy by design, same as go_live.state.GoLiveDraft - acceptable per
    that precedent, the user just resends the photo + description if it happens.
    """

    id: str
    chat_id: int
    user_id: int
    image_path: Path
    raw_summary: str  # the user's own 30-40 word draft, primary source of truth throughout
    review_message_id: Optional[int] = None
    summary: Optional[str] = None
    summary_history: list[str] = field(default_factory=list)  # prior rewrites, steers regens away from repeats
    status: str = "summarizing"  # summarizing | ready | regenerating | posting | posted | failed
    created_at: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_drafts: dict[str, WebsiteDraft] = {}


def create_draft(chat_id: int, user_id: int, image_path: Path, raw_summary: str) -> WebsiteDraft:
    draft_id = uuid.uuid4().hex[:8]
    while draft_id in _drafts:
        draft_id = uuid.uuid4().hex[:8]
    draft = WebsiteDraft(id=draft_id, chat_id=chat_id, user_id=user_id, image_path=image_path, raw_summary=raw_summary)
    _drafts[draft_id] = draft
    return draft


def get_draft(draft_id: str) -> Optional[WebsiteDraft]:
    return _drafts.get(draft_id)


def pop_draft(draft_id: str) -> Optional[WebsiteDraft]:
    return _drafts.pop(draft_id, None)


def is_expired(draft: WebsiteDraft) -> bool:
    return (time.monotonic() - draft.created_at) > WEBSITE_DRAFT_EXPIRY_SECONDS


def schedule_expiry_cleanup(application, draft_id: str) -> None:
    application.create_task(_expire_draft_after_timeout(draft_id))


async def _expire_draft_after_timeout(draft_id: str) -> None:
    await asyncio.sleep(WEBSITE_DRAFT_EXPIRY_SECONDS)
    draft = _drafts.get(draft_id)
    if draft is None or draft.status == "posted":
        return
    logger.info("Website draft %s expired without resolving; cleaning up temp file", draft_id)
    pop_draft(draft_id)
    draft.image_path.unlink(missing_ok=True)


# --- Pending arm: "the next photo this (chat, user) sends is a website description,
# not a new generation job" - set by tapping "Add Website Summary" on the
# Instagram-success message (see handlers/go_live.py, handlers/website.py). Keyed
# by (chat_id, user_id) rather than a draft id since there's no draft yet at arm
# time - the Go Live draft that produced the Instagram post is already popped.


@dataclass
class PendingArm:
    chat_id: int
    user_id: int
    created_at: float = field(default_factory=time.monotonic)


_pending: dict[tuple[int, int], PendingArm] = {}


def arm_pending(chat_id: int, user_id: int) -> None:
    _pending[(chat_id, user_id)] = PendingArm(chat_id=chat_id, user_id=user_id)


def get_pending(chat_id: int, user_id: int) -> Optional[PendingArm]:
    arm = _pending.get((chat_id, user_id))
    if arm is None:
        return None
    if (time.monotonic() - arm.created_at) > WEBSITE_PENDING_EXPIRY_SECONDS:
        _pending.pop((chat_id, user_id), None)
        return None
    return arm


def pop_pending(chat_id: int, user_id: int) -> None:
    _pending.pop((chat_id, user_id), None)


def schedule_pending_expiry(application, chat_id: int, user_id: int) -> None:
    application.create_task(_expire_pending_after_timeout(chat_id, user_id))


async def _expire_pending_after_timeout(chat_id: int, user_id: int) -> None:
    await asyncio.sleep(WEBSITE_PENDING_EXPIRY_SECONDS)
    _pending.pop((chat_id, user_id), None)
