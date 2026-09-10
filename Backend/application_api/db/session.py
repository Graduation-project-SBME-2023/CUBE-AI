"""Async PostgreSQL engine and request-scoped session dependency."""

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from application_api.core.config import settings


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.SQL_ECHO,
    pool_pre_ping=True,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    # Recycle connections periodically so a stale connection behind a NAT or
    # idle-connection-killing proxy is replaced proactively instead of only
    # being caught (with an extra round trip) by pool_pre_ping.
    pool_recycle=1800,
    # A server-side statement timeout stops a single runaway query from
    # holding a pool connection indefinitely.
    connect_args={"server_settings": {"statement_timeout": "30000"}},
)

AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


async def get_db():
    # Do not eagerly acquire a pooled connection here: SQLAlchemy checks
    # one out lazily on first use. Acquiring one unconditionally meant every
    # request opened a transaction — even ones that never touch the
    # database — and, combined with routes that hold the session open across
    # a slow Orthanc call, made the pool the de facto request-concurrency
    # ceiling for the whole API.
    async with AsyncSessionLocal() as session:
        yield session
