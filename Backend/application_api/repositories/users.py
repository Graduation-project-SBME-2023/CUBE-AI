"""User repository: one configured default user until authentication exists."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.models.sessions import Session as SessionModel
from application_api.db.models.users import User

logger = logging.getLogger(__name__)

# Users are never deleted, so a resolved id stays valid for the process.
_ids: dict[str, UUID] = {}

_ENSURE = text(
    "INSERT INTO users (username, display_name) VALUES (:username, :username) "
    "ON CONFLICT (username) DO NOTHING"
)


async def resolve_user_id(db: AsyncSession, username: str) -> UUID:
    username = username.strip().lower()
    if username in _ids:
        return _ids[username]
    user_id = (await db.execute(select(User.user_id).where(User.username == username))).scalar()
    if user_id is None:
        await db.execute(_ENSURE, {"username": username})
        user_id = (await db.execute(select(User.user_id).where(User.username == username))).scalar_one()
        await db.commit()
        logger.info("Created the default user %r", username)
    _ids[username] = user_id
    return user_id


async def require_owner(db: AsyncSession, session_id: UUID, user_id: UUID) -> None:
    """404, not 403, for a session of another user: its existence is private."""
    owner = (
        await db.execute(select(SessionModel.user_id).where(SessionModel.session_id == session_id))
    ).scalar()
    if owner is None or owner != user_id:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
