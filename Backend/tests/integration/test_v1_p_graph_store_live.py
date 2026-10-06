"""The GraphStore scenario table on HttpGraphStore, through the real
Application API and PostgreSQL. The same scenarios run on
InMemoryGraphStore in tests/unit/execution/test_graph_store_scenarios.py, so the
fake is never looser than the database.

Needs V1_LIVE_DATABASE_URL (a disposable PostgreSQL 18 database); the module
fixture of test_v1_p_transactions_live migrates it to head.
"""

from __future__ import annotations

import pytest

from tests.integration.test_v1_p_transactions_live import LIVE_DSN, httpx, migrated_database  # noqa: F401
from tests.support.graph_store_scenarios import SCENARIOS

pytestmark = [
    pytest.mark.integration, pytest.mark.docker, pytest.mark.slow, pytest.mark.asyncio,
    pytest.mark.skipif(not LIVE_DSN, reason="set V1_LIVE_DATABASE_URL to a disposable PostgreSQL 18 database"),
]
pytest_asyncio = pytest.importorskip("pytest_asyncio")


@pytest_asyncio.fixture
async def store():
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool

    from agent_system.clients.harness_client import HarnessClient
    from agent_system.execution.graph_store import HttpGraphStore
    from application_api.db.session import get_db
    from application_api.main import app
    from application_api.repositories import users

    users._ids.clear()
    engine = create_async_engine(LIVE_DSN, poolclass=NullPool)
    sessions = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)

    async def _db():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = _db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api") as client:
        yield HttpGraphStore(HarnessClient(base_url="http://api", client=client))
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.__name__ for s in SCENARIOS])
async def test_http_store(store, scenario):
    await scenario(store)
