"""The @tool decorator and ToolRegistry (merged plan, Phase 1.3 step 1)."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from types import ModuleType
from typing import Annotated, Any

import pytest
from pydantic import BaseModel, Field

from agent_system.toolkit.agents.spec import AgentSpec
from agent_system.toolkit.context import ToolContext
from agent_system.toolkit.tools.registry import (
    ToolNotAllowedError,
    ToolRegistry,
    UnknownToolError,
    spec_of,
    tool,
    tools_in,
)
from agent_system.toolkit.tools.spec import ToolSpec

pytestmark = pytest.mark.unit

CTX = ToolContext(session_id="s1", caller="agent")
READ = {
    "side_effects": "none",
    "cost": "light",
    "execution": "inline",
    "visibility": {"main", "agent"},
    "read_only": True,
}


class TrainParams(BaseModel):
    input_path: str = Field(description="CSV of features.")
    fold: int = 5


# --- @tool


async def get_patient(
    patient_id: Annotated[str, Field(description="The patient's id.")],
    ctx: ToolContext,
    include_studies: bool = False,
) -> dict[str, Any]:
    """Read one patient."""
    return {"patient_id": patient_id, "session": ctx.session_id, "studies": include_studies}


def _declare(fn: Any = get_patient, **overrides: Any) -> ToolSpec:
    options = {"pack": "db_reads", **READ, **overrides}
    return spec_of(tool(**options)(fn))  # type: ignore[return-value]


def test_decorator_returns_the_function_unchanged_and_callable():
    decorated = tool(pack="db_reads", **READ)(get_patient)

    assert decorated is get_patient
    assert asyncio.run(decorated("p1", CTX)) == {
        "patient_id": "p1",
        "session": "s1",
        "studies": False,
    }


def test_defaults_come_from_the_function():
    spec = _declare()

    assert spec.name == "get_patient"
    assert spec.description == "Read one patient."
    assert spec.handler is get_patient
    assert spec.visibility == {"main", "agent"}


def test_explicit_name_and_description_win():
    spec = _declare(name="read_patient", description="Read a patient record.")

    assert (spec.name, spec.description) == ("read_patient", "Read a patient record.")


def test_argument_model_mirrors_the_signature():
    spec = _declare()
    schema = spec.args_schema()

    assert spec.input_model.__name__ == "get_patientArguments"
    assert spec.arg_names == {"patient_id", "include_studies"}
    assert schema["required"] == ["patient_id"]
    assert schema["properties"]["patient_id"]["description"] == "The patient's id."
    assert schema["properties"]["include_studies"]["default"] is False


def test_the_tool_context_parameter_is_not_an_argument():
    spec = _declare()

    assert spec.context_param == "ctx"
    assert "ctx" not in spec.args_schema()["properties"]


async def train(params: TrainParams) -> dict[str, Any]:
    """Train a model."""
    return {"fold": params.fold}


def test_a_params_model_keeps_its_nested_schema():
    spec = _declare(train)
    schema = spec.args_schema()

    assert spec.context_param is None
    assert schema["properties"]["params"] == {"$ref": "#/$defs/TrainParams"}
    assert schema["$defs"]["TrainParams"]["properties"]["input_path"]["description"] == (
        "CSV of features."
    )


def test_pack_is_taken_from_the_toolpacks_module():
    async def search(query: str) -> dict[str, Any]:
        """Search."""
        return {}

    search.__module__ = "agent_system.toolpacks.literature.search"
    assert spec_of(tool(**READ)(search)).pack == "literature"  # type: ignore[union-attr]

    search.__module__ = "toolpacks.literature"  # the container's import path, a pack __init__
    assert spec_of(tool(**READ)(search)).pack == "literature"  # type: ignore[union-attr]


def test_pack_is_required_outside_toolpacks():
    with pytest.raises(TypeError, match="pass pack="):
        tool(**READ)(get_patient)


def test_manifest_errors_surface_at_declaration():
    with pytest.raises(ValueError, match="cost="):
        _declare(cost="free")
    with pytest.raises(TypeError, match="visibility"):
        tool(pack="db_reads", side_effects="none", cost="light", execution="inline")(get_patient)


def _sync(patient_id: str) -> dict[str, Any]:
    """Sync."""
    return {}


async def _two_contexts(a: ToolContext, b: ToolContext) -> None:
    """Two contexts."""


async def _var_args(*ids: str) -> None:
    """Var args."""


async def _var_kwargs(**filters: str) -> None:
    """Var kwargs."""


async def _positional_only(patient_id: str, /) -> None:
    """Positional only."""


async def _unannotated(patient_id) -> None:  # type: ignore[no-untyped-def]
    """Unannotated."""


async def _private_name(_patient_id: str) -> None:
    """Private."""


async def _shadowing_name(schema: str) -> None:
    """Shadows BaseModel.schema."""


async def _unresolvable(patient: UndefinedModel) -> None:  # type: ignore[name-defined]  # noqa: F821
    """Unresolvable."""


@pytest.mark.parametrize(
    ("fn", "match"),
    [
        (_sync, "must be an async function"),
        (_two_contexts, "at most one ToolContext"),
        (_var_args, "passed by name"),
        (_var_kwargs, "passed by name"),
        (_positional_only, "passed by name"),
        (_unannotated, "needs a type annotation"),
        (_private_name, "reserved"),
        (_shadowing_name, "reserved"),
        (_unresolvable, "cannot resolve the annotations"),
    ],
)
def test_unsupported_signatures_are_refused(fn, match):
    with pytest.raises(TypeError, match=match):
        _declare(fn)


def test_context_param_cannot_also_be_an_argument():
    with pytest.raises(ValueError, match="context_param 'patient_id'"):
        ToolSpec(
            name="get_patient",
            description="Read one patient.",
            pack="db_reads",
            handler=get_patient,
            input_model=_declare().input_model,
            context_param="patient_id",
            **READ,
        )


def test_spec_of_and_tools_in():
    first, second = _declare(), _declare(train)
    module = ModuleType("agent_system.toolpacks.fake")
    module.get_patient = first.handler  # type: ignore[attr-defined]
    module.alias = first.handler  # type: ignore[attr-defined]
    module.train = second.handler  # type: ignore[attr-defined]
    module.helper = _sync  # type: ignore[attr-defined]
    module.CONSTANT = 3  # type: ignore[attr-defined]

    assert spec_of(_sync) is None
    assert tools_in(module) == [first, second]


def test_schema_matches_fastmcp():
    func_metadata = pytest.importorskip("mcp.server.fastmcp.utilities.func_metadata")

    for fn, skip in ((get_patient, ["ctx"]), (train, [])):
        fastmcp = func_metadata.func_metadata(fn, skip_names=skip).arg_model
        assert _declare(fn).args_schema() == fastmcp.model_json_schema()


# --- ToolRegistry


async def _noop(**_: Any) -> dict[str, Any]:
    return {}


def _spec(name: str, visibility: set[str], pack: str = "db_reads") -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"{name}.",
        pack=pack,
        handler=_noop,
        input_schema={"type": "object"},
        side_effects="none",
        cost="light",
        execution="inline",
        visibility=visibility,
        read_only=True,
    )


REGISTRY_SPECS = [
    _spec("get_patient", {"main", "agent"}),
    _spec("list_models", {"agent"}, pack="segmentation"),
    _spec("execute", {"main"}, pack="control"),
    _spec("scratchpad_set_status", {"harness"}, pack="bookkeeping"),
    _spec("run_segmentation_with_guard", {"graph", "agent"}, pack="segmentation"),
]


def _registry() -> ToolRegistry:
    return ToolRegistry(REGISTRY_SPECS)


def test_register_and_get():
    registry = _registry()

    assert registry.get("list_models") is REGISTRY_SPECS[1]
    assert len(registry) == 5
    assert "execute" in registry


def test_unknown_tool():
    with pytest.raises(UnknownToolError, match="unknown tool 'get_volume'"):
        _registry().get("get_volume")


def test_duplicate_names_are_refused():
    registry = _registry()
    registry.register(REGISTRY_SPECS[0])  # the same object again is harmless

    with pytest.raises(ValueError, match="tool 'get_patient' is already registered"):
        registry.register(_spec("get_patient", {"agent"}))


def test_only_tool_specs_are_registered():
    with pytest.raises(TypeError, match="ToolSpec expected"):
        ToolRegistry().register(object())  # type: ignore[arg-type]


def _names(specs: Sequence[ToolSpec]) -> list[str]:
    return [spec.name for spec in specs]


def test_views_by_visibility_and_pack():
    registry = _registry()

    assert _names(registry.for_main()) == ["get_patient", "execute"]
    assert _names(registry.graph_tools()) == ["run_segmentation_with_guard"]
    assert {pack: _names(specs) for pack, specs in registry.by_pack().items()} == {
        "bookkeeping": ["scratchpad_set_status"],
        "control": ["execute"],
        "db_reads": ["get_patient"],
        "segmentation": ["list_models", "run_segmentation_with_guard"],
    }


def _agent(kind: str, allowed_tools: Any) -> AgentSpec:
    return AgentSpec(
        agent_type="main_agent" if kind == "control" else "segmentation_agent",
        description="An agent.",
        kind=kind,
        system_prompt="Prompt.",
        allowed_tools=allowed_tools,
    )


def test_for_agent_keeps_the_allowlist_order():
    agent = _agent("execution", ["list_models", "get_patient", "run_segmentation_with_guard"])

    assert _names(_registry().for_agent(agent, CTX)) == [
        "list_models",
        "get_patient",
        "run_segmentation_with_guard",
    ]


@pytest.mark.parametrize(
    ("kind", "tools", "refused"),
    [
        ("execution", ["list_models", "scratchpad_set_status"], "scratchpad_set_status"),
        ("execution", ["execute"], "execute"),  # a main-only control tool
        ("control", ["get_patient", "list_models"], "list_models"),  # agent-only
    ],
)
def test_for_agent_refuses_tools_the_agent_kind_cannot_see(kind, tools, refused):
    with pytest.raises(ToolNotAllowedError, match=f"may not use \\['{refused}'\\]"):
        _registry().for_agent(_agent(kind, tools), CTX)


def test_for_agent_reports_unknown_tools_first():
    with pytest.raises(UnknownToolError, match="unknown tool 'get_volume'"):
        _registry().for_agent(_agent("execution", ["get_volume"]), CTX)


def test_a_function_allowlist_only_sees_the_agents_audience():
    seen: list[str] = []

    def everything(ctx: ToolContext, tools: Sequence[ToolSpec]) -> list[str]:
        seen.extend(_names(tools))
        return _names(tools)

    assert _names(_registry().for_agent(_agent("control", everything), CTX)) == [
        "get_patient",
        "execute",
    ]
    assert seen == ["get_patient", "execute"]
