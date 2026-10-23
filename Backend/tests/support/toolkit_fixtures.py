"""Shared fakes for toolkit and tool-pack tests (merged plan, Phase 1.5 step 1).

A test directory opts in from its ``conftest.py`` by importing the fixtures it
uses (``tests/unit/toolkit/conftest.py`` does). They are not loaded from
``Backend/conftest.py`` because the toolkit needs Python 3.13 and that
conftest also runs in the MONAI image (Python 3.10).

The plain classes and functions work without pytest fixtures too:

- ``FakeEventSink`` records what dispatch emits;
- ``make_context`` / ``make_catalog`` build a ``ToolContext`` / ``Catalog``
  with test defaults;
- ``call_tool`` runs one call through dispatch, synchronously.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
import respx

from agent_system.toolkit.agents.spec import AgentSpec
from agent_system.toolkit.artifacts.base import ArtifactType
from agent_system.toolkit.catalog import Catalog
from agent_system.toolkit.context import ToolContext
from agent_system.toolkit.events import ToolEvent, ViewEvent
from agent_system.toolkit.runtime.cancel import CancelToken
from agent_system.toolkit.runtime.dispatch import Dispatcher
from agent_system.toolkit.tools.registry import spec_of

TEST_SESSION_ID = "test-session"  # matches the ``workspace_dir`` fixture's folder
TEST_AGENT_TYPE = "test_agent"
# Never resolves: a request respx does not route fails instead of leaving the machine.
DOMAIN_API_URL = "http://application-api.test"


class FakeEventSink:
    """Records every event and view; satisfies ``EventSink``."""

    def __init__(self) -> None:
        self.events: list[ToolEvent] = []
        self.views: list[ViewEvent] = []

    async def emit(self, event: ToolEvent) -> None:
        self.events.append(event)

    async def emit_view(self, event: ViewEvent) -> None:
        self.views.append(event)

    @property
    def types(self) -> list[str]:
        return [event.event_type for event in self.events]


def make_context(**fields: Any) -> ToolContext:
    """A context for one agent's tool call in the test session; ``fields`` override."""
    defaults: dict[str, Any] = {
        "session_id": TEST_SESSION_ID,
        "caller": "agent",
        "agent_type": TEST_AGENT_TYPE,
    }
    return ToolContext(**(defaults | fields))


def make_catalog(*items: Any, validate: bool = True) -> Catalog:
    """A catalog of ``items``: tools (``@tool`` functions or ``ToolSpec``s),
    ``AgentSpec``s and extra ``ArtifactType``s on top of the built-in ones.
    Validated unless ``validate=False``, so a broken fixture fails loudly."""
    catalog = Catalog()
    for item in items:
        if spec := spec_of(item):
            catalog.tools.register(spec)
        elif isinstance(item, AgentSpec):
            catalog.agents.register(item)
        elif isinstance(item, ArtifactType):
            catalog.artifact_types.register(item)
        else:
            raise TypeError(f"not a tool, agent or artifact type: {item!r}")
    if validate:
        catalog.validate()
    return catalog


def call_tool(
    tool: Any,
    args: Mapping[str, Any],
    ctx: ToolContext,
    cancel: CancelToken | None = None,
    *,
    dispatcher: Dispatcher | None = None,
) -> Any:
    """Call ``tool`` (an ``@tool`` function or a ``ToolSpec``) through dispatch."""
    spec = spec_of(tool)
    if spec is None:
        raise TypeError(f"not a tool: {tool!r}")
    return asyncio.run((dispatcher or Dispatcher()).invoke(spec, args, ctx, cancel))


# --- fixtures


@pytest.fixture
def fake_event_sink() -> FakeEventSink:
    return FakeEventSink()


@pytest.fixture
def fake_context(
    fake_event_sink: FakeEventSink, workspace_dir: Path
) -> Callable[..., ToolContext]:
    """``make_context`` with this test's sink and workspace as defaults."""

    def build(**fields: Any) -> ToolContext:
        live = {"event_sink": fake_event_sink, "workspace_dir": workspace_dir}
        return make_context(**(live | fields))

    return build


@pytest.fixture
def fake_catalog() -> Callable[..., Catalog]:
    return make_catalog


@pytest.fixture
def fake_domain_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[respx.MockRouter]:
    """The Application API, mocked: register routes on the returned router
    (``fake_domain_api.get("/patients/p1").respond(json=...)``). Any request
    without a route fails, so a test never reaches the network."""
    monkeypatch.setenv("APP_API_URL", DOMAIN_API_URL)
    with respx.mock(base_url=DOMAIN_API_URL, assert_all_called=False) as router:
        yield router


@pytest.fixture
def fake_chat_model() -> Any:
    """A scripted chat model for the LangGraph engine. Filled in Plan 9."""
    raise NotImplementedError("fake_chat_model arrives with the LangGraph engine (Plan 9)")
