"""A small research catalog and graph builders for the planning tests (Plan 8).

The catalog is a sequential radiomics line with the shapes the D23 rule cares
about: two producers of one type (``run_modeling`` and ``refit_model`` both
produce ``ModelingOutput``), one node with two compatible ports
(``split_masks``), a tool that is not graph-visible, a control agent, a
non-resumable execution agent and a GPU tool.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel

from agent_system.planning.graph_model import (
    GraphState,
    NodeDef,
    NodeState,
    RunSummary,
)
from agent_system.toolkit.agents.spec import AgentSpec
from agent_system.toolkit.catalog import Catalog
from agent_system.toolkit.ports import ExtractedArtifact, PortSpec
from agent_system.toolkit.tools.spec import ToolSpec
from tests.support.toolkit_fixtures import make_catalog

POOLS = frozenset({"llm", "cpu_heavy", "gpu", "highmem", "monai_inference", "orthanc_io"})


def _extract(result: Any) -> ExtractedArtifact:
    return ExtractedArtifact(ref=result)


def _out(artifact_type: str, **flags: Any) -> PortSpec:
    return PortSpec(artifact_type, extract=_extract, **flags)


async def _handler(**_: Any) -> dict[str, Any]:
    return {"status": "ok"}


def _tool(name: str, args: type[BaseModel], *, inputs=None, outputs=None, **manifest: Any) -> ToolSpec:
    fields: dict[str, Any] = {
        "side_effects": "writes", "cost": "light", "execution": "inline", "visibility": {"graph"},
    }
    fields.update(manifest)
    return ToolSpec(name=name, description=f"{name}.", pack="research", handler=_handler,
                    input_model=args, inputs=inputs or {}, outputs=outputs or {}, **fields)


def _agent(name: str, *, kind: str = "execution", inputs=None, outputs=None, **fields: Any) -> AgentSpec:
    return AgentSpec(agent_type=name, description=f"{name}.", kind=kind, system_prompt="Work.",
                     inputs=inputs or {}, outputs=outputs or {}, **fields)


class CohortArgs(BaseModel):
    criteria: str
    min_size: int = 1


class FeatureArgs(BaseModel):
    cohort_id: str
    segmentation_batch_id: str
    bin_width: float = 25.0


class ModelArgs(BaseModel):
    feature_set_id: str
    target: str


class ReportArgs(BaseModel):
    model_result_id: str


class MaskArgs(BaseModel):
    segmentation_batch_id: str


class CountArgs(BaseModel):
    modality: Optional[str] = None


class GpuArgs(BaseModel):
    cohort_id: str


def research_catalog() -> Catalog:
    return make_catalog(
        _tool("build_cohort", CohortArgs, outputs={"cohort": _out("Cohort")}),
        _agent("population_agent", outputs={"cohort": _out("Cohort")}),
        _agent("segmentation_agent", inputs={"cohort": PortSpec("Cohort")},
               outputs={"masks": _out("MaskSet")}),
        _tool("split_masks", MaskArgs, inputs={"masks": PortSpec("MaskSet", param="segmentation_batch_id")},
              outputs={"left": _out("MaskSet"), "right": _out("MaskSet")}),
        _tool("extract_features", FeatureArgs,
              inputs={"cohort": PortSpec("Cohort", param="cohort_id"),
                      "masks": PortSpec("MaskSet", param="segmentation_batch_id")},
              outputs={"features": _out("FeatureTable")}),
        _tool("run_modeling", ModelArgs, inputs={"features": PortSpec("FeatureTable", param="feature_set_id")},
              outputs={"modeling": _out("ModelingOutput")}),
        _tool("refit_model", ModelArgs, inputs={"features": PortSpec("FeatureTable", param="feature_set_id")},
              outputs={"modeling": _out("ModelingOutput")}),
        _tool("report_results", ReportArgs, inputs={"modeling": PortSpec("ModelingOutput", param="model_result_id")},
              outputs={"summary": _out("ResultSummary")}),
        _agent("literature_agent", resumable=False, outputs={"literature": _out("LiteratureContext")}),
        _agent("composer", kind="control"),
        _tool("count_studies", CountArgs, side_effects="none", read_only=True, visibility={"main"}),
        _tool("gpu_segment", GpuArgs, cost="heavy", execution="job", resources={"gpu"},
              inputs={"cohort": PortSpec("Cohort", param="cohort_id")}, outputs={"masks": _out("MaskSet")}),
    )


# ----------------------------------------------------------------------
# Nodes and states
# ----------------------------------------------------------------------
_DEFAULT_PARAMS: dict[str, dict[str, Any]] = {
    "build_cohort": {"criteria": "chest CT"},
    "run_modeling": {"target": "label"},
    "refit_model": {"target": "label"},
}


def node(ref: str, *, kind: Optional[str] = None, node_id: Optional[UUID] = None, **fields: Any) -> NodeDef:
    """A node with valid default params for its ref."""
    agent = ref.endswith("_agent") or ref == "composer"
    params = fields.pop("params", {"task": "do it"} if agent else _DEFAULT_PARAMS.get(ref, {}))
    return NodeDef(node_id=node_id or uuid.uuid4(), kind=kind or ("agent" if agent else "tool"), ref=ref,
                   ref_version="1", title=ref, params=params, **fields)


def live(definition: NodeDef, status: Optional[str] = None, *, hold: bool = False,
         history: tuple[UUID, ...] = ()) -> NodeState:
    """A live node, with a current run in ``status`` when given."""
    run = RunSummary(run_id=uuid.uuid4(), status=status, holds_downstream=hold) if status else None
    run_ids = frozenset(history) | ({run.run_id} if run else set())
    return NodeState(definition=definition, run=run, run_ids=run_ids)


def state(*nodes: NodeState, version: int = 3, removed: tuple[UUID, ...] = ()) -> GraphState:
    return GraphState(version=version, nodes=list(nodes), removed_node_ids=frozenset(removed))
