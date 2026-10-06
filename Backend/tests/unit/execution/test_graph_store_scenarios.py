"""The GraphStore scenario table on InMemoryGraphStore.

The same scenarios run on HttpGraphStore against PostgreSQL in
tests/integration/test_v1_p_graph_store_live.py."""

from __future__ import annotations

import pytest

from agent_system.execution.in_memory_graph_store import InMemoryGraphStore
from tests.support.graph_store_scenarios import SCENARIOS

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.__name__ for s in SCENARIOS])
async def test_in_memory_store(scenario):
    await scenario(InMemoryGraphStore())
