"""A separate, small engine for binary reads"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from application_api.core.artifact_limits import (
    BINARY_POOL_MAX_OVERFLOW,
    BINARY_POOL_SIZE,
    BINARY_READ_CONCURRENCY,
    BINARY_STATEMENT_TIMEOUT_MS,
)
from application_api.core.config import settings

binary_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=BINARY_POOL_SIZE,
    max_overflow=BINARY_POOL_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_recycle=1800,
    connect_args={
        "server_settings": {"statement_timeout": str(BINARY_STATEMENT_TIMEOUT_MS)}
    },
)

BinarySessionLocal = sessionmaker(
    bind=binary_engine,
    class_=AsyncSession,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)

# The admission bound. It is a semaphore rather than a queue length so a caller
# that cannot be served learns that immediately.
binary_read_slots = asyncio.Semaphore(BINARY_READ_CONCURRENCY)


async def get_binary_db():
    """One short transaction per request; the connection is released before the
    response is streamed (the bytes are already in memory by then — that is
    what the cap bounds)."""
    async with BinarySessionLocal() as session:
        yield session
