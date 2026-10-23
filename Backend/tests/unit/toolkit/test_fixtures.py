"""The shared toolkit fixtures (merged plan, Phase 1.5 step 1)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from pydantic import BaseModel

from agent_system.toolkit.agents.spec import AgentSpec
from agent_system.toolkit.artifacts.base import ArtifactRef, ArtifactSummary, ArtifactType
from agent_system.toolkit.catalog import CatalogError
from agent_system.toolkit.context import Caller, current_context
from agent_system.toolkit.ports import PortSpec
from agent_system.toolkit.tools.registry import spec_of, tool
from tests.support.toolkit_fixtures import (
    DOMAIN_API_URL,
    TEST_SESSION_ID,
    call_tool,
    make_context,
)

pytestmark = pytest.mark.unit


@tool(pack="test", side_effects="none", cost="light", execution="inline",
      visibility={"agent"}, read_only=True)  # fmt: skip
async def where_am_i() -> dict[str, Any]:
    """Report the running context."""
    ctx = current_context()
    return {"session": ctx.session_id, "tool": ctx.tool_name, "workspace": str(ctx.workspace_dir)}


SEGMENTATION = AgentSpec(
    agent_type="segmentation_agent",
    description="Segments.",
    kind="execution",
    system_prompt="Prompt.",
)


# --- contexts and events


def test_make_context_defaults_to_an_agent_in_the_test_session():
    ctx = make_context(node_id="n1")

    assert (ctx.session_id, ctx.caller, ctx.node_id) == (TEST_SESSION_ID, Caller.AGENT, "n1")
    assert ctx.event_sink is None


def test_fake_context_runs_with_the_tests_sink_and_workspace(
    fake_context, fake_event_sink, workspace_dir
):
    result = call_tool(where_am_i, {}, fake_context())

    assert result == {
        "session": TEST_SESSION_ID, "tool": "where_am_i", "workspace": str(workspace_dir)
    }
    assert fake_event_sink.types == ["tool_started", "tool_finished"]


def test_fake_context_fields_can_be_overridden(fake_context, fake_event_sink):
    ctx = fake_context(session_id="other", caller="main", event_sink=None)

    assert (ctx.session_id, ctx.caller, ctx.event_sink) == ("other", Caller.MAIN, None)
    call_tool(where_am_i, {}, ctx)
    assert fake_event_sink.events == []


def test_call_tool_needs_a_tool():
    with pytest.raises(TypeError, match="not a tool"):
        call_tool(print, {}, make_context())


# --- catalogs


class _NoteRef(ArtifactRef):
    note_id: str


class _NoteSummary(ArtifactSummary):
    words: int


NOTE = ArtifactType(
    name="Note",
    version=1,
    description="A free-text note.",
    ref_model=_NoteRef,
    summary_model=_NoteSummary,
    phi_default=False,
)


def test_fake_catalog_sorts_tools_agents_and_artifact_types(fake_catalog):
    catalog = fake_catalog(where_am_i, SEGMENTATION, NOTE)

    assert catalog.tools.names() == {"where_am_i"}
    assert catalog.agents.names() == {"segmentation_agent"}
    assert "Note" in catalog.artifact_types and "Cohort" in catalog.artifact_types
    assert catalog.resolve("where_am_i") is spec_of(where_am_i)


def test_fake_catalog_validates_unless_told_not_to(fake_catalog):
    @tool(pack="test", side_effects="none", cost="light", execution="inline",
          visibility={"agent"}, read_only=True,
          outputs={"note": PortSpec("Unknown", extract=lambda result: [])})  # fmt: skip
    async def broken() -> dict[str, Any]:
        """Writes an unknown artifact type."""
        return {}

    with pytest.raises(CatalogError, match="Unknown"):
        fake_catalog(broken)
    assert fake_catalog(broken, validate=False).problems() != []


def test_fake_catalog_refuses_anything_else(fake_catalog):
    class NotASpec(BaseModel):
        name: str = "x"

    with pytest.raises(TypeError, match="not a tool, agent or artifact type"):
        fake_catalog(NotASpec())


# --- the domain API


def test_fake_domain_api_answers_routed_requests(fake_domain_api, monkeypatch):
    import os

    fake_domain_api.get("/patients/p1").respond(json={"patient_id": "p1"})

    response = httpx.get(f"{os.environ['APP_API_URL']}/patients/p1")

    assert os.environ["APP_API_URL"] == DOMAIN_API_URL
    assert response.json() == {"patient_id": "p1"}


def test_fake_domain_api_fails_unrouted_requests(fake_domain_api):
    with pytest.raises(respx.models.AllMockedAssertionError):
        httpx.get(f"{DOMAIN_API_URL}/studies")


def test_fake_chat_model_is_not_there_yet(request):
    with pytest.raises(NotImplementedError, match="Plan 9"):
        request.getfixturevalue("fake_chat_model")
