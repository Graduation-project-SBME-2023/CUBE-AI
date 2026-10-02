"""PortSpec and output extraction (merged plan, Phase 1.1 step 3)."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from agent_system.toolkit.ports import ExtractedArtifact, PortExtractError, PortSpec

pytestmark = pytest.mark.unit


def _cohort(result: dict[str, Any]) -> ExtractedArtifact:
    return ExtractedArtifact(
        ref={"cohort_id": result["cohort_id"]},
        summary={"size": result["size"], "frozen": True},
    )


def _masks(result: dict[str, Any]) -> list[ExtractedArtifact]:
    return [ExtractedArtifact(ref={"segmentation_batch_id": run}) for run in result["runs"]]


# --- PortSpec


def test_defaults():
    port = PortSpec("Cohort")

    assert port.param is None
    assert port.many is False
    assert port.required is True
    assert port.extract is None


def test_only_artifact_type_is_positional():
    with pytest.raises(TypeError):
        PortSpec("Cohort", "cohort_id")  # type: ignore[misc]


def test_port_is_immutable_and_comparable():
    port = PortSpec("Cohort", param="cohort_id")

    with pytest.raises(AttributeError):
        port.param = "other"  # type: ignore[misc]
    assert port == PortSpec("Cohort", param="cohort_id")
    assert copy.replace(port, required=False).required is False


@pytest.mark.parametrize(
    ("kwargs", "error", "match"),
    [
        ({"artifact_type": ""}, ValueError, "artifact type"),
        ({"artifact_type": "Mask Set"}, ValueError, "artifact type"),
        ({"artifact_type": "1Cohort"}, ValueError, "artifact type"),
        ({"artifact_type": "Cohort", "param": "cohort-id"}, ValueError, "argument name"),
        ({"artifact_type": "Cohort", "param": "class"}, ValueError, "argument name"),
        ({"artifact_type": "Cohort", "param": ""}, ValueError, "argument name"),
        ({"artifact_type": "Cohort", "many": 1}, TypeError, "many"),
        ({"artifact_type": "Cohort", "required": "yes"}, TypeError, "required"),
        ({"artifact_type": "Cohort", "extract": "cohort_id"}, TypeError, "extract"),
        (
            {"artifact_type": "Cohort", "param": "cohort_id", "extract": _cohort},
            ValueError,
            "not both",
        ),
    ],
)
def test_malformed_ports_are_refused(kwargs, error, match):
    with pytest.raises(error, match=match):
        PortSpec(**kwargs)


# --- ExtractedArtifact


def test_extracted_artifact_is_frozen_and_copied():
    ref = {"cohort_id": "c1"}
    artifact = ExtractedArtifact(ref=ref)

    ref["cohort_id"] = "changed"

    assert artifact.ref == {"cohort_id": "c1"}
    assert artifact.summary == {}
    with pytest.raises(TypeError):
        artifact.ref["cohort_id"] = "x"  # type: ignore[index]


@pytest.mark.parametrize(
    ("ref", "summary"),
    [({}, {}), (None, {}), ("c1", {}), ({"cohort_id": "c1"}, None)],
)
def test_extracted_artifact_needs_a_pointer_and_a_mapping_summary(ref, summary):
    with pytest.raises(ValueError):
        ExtractedArtifact(ref=ref, summary=summary)


# --- extract_artifacts


def test_extracts_one_artifact():
    port = PortSpec("Cohort", extract=_cohort)

    (artifact,) = port.extract_artifacts({"cohort_id": "c1", "size": 40})

    assert artifact.ref == {"cohort_id": "c1"}
    assert artifact.summary == {"size": 40, "frozen": True}


def test_many_port_accepts_a_list():
    port = PortSpec("MaskSet", many=True, extract=_masks)

    artifacts = port.extract_artifacts({"runs": ["r1", "r2"]})

    assert [a.ref["segmentation_batch_id"] for a in artifacts] == ["r1", "r2"]


def test_single_port_accepts_a_one_item_list():
    port = PortSpec("MaskSet", extract=_masks)

    assert len(port.extract_artifacts({"runs": ["r1"]})) == 1


def test_single_port_refuses_several_artifacts():
    port = PortSpec("MaskSet", extract=_masks)

    with pytest.raises(PortExtractError, match="takes one artifact, extract returned 2"):
        port.extract_artifacts({"runs": ["r1", "r2"]})


@pytest.mark.parametrize("produced", [None, []])
def test_optional_port_may_produce_nothing(produced):
    port = PortSpec("FeatureImportance", required=False, extract=lambda _: produced)

    assert port.extract_artifacts({}) == ()


@pytest.mark.parametrize("many", [False, True])
@pytest.mark.parametrize("produced", [None, []])
def test_required_port_must_produce_something(many, produced):
    port = PortSpec("Cohort", many=many, extract=lambda _: produced)

    with pytest.raises(PortExtractError, match="required Cohort output was not produced"):
        port.extract_artifacts({})


@pytest.mark.parametrize(
    "produced",
    [{"cohort_id": "c1"}, "c1", [{"cohort_id": "c1"}], [ExtractedArtifact(ref={"a": 1}), 3]],
)
def test_extract_must_return_extracted_artifacts(produced):
    port = PortSpec("Cohort", many=True, extract=lambda _: produced)

    with pytest.raises(PortExtractError, match="not ExtractedArtifact"):
        port.extract_artifacts({})


def test_input_port_cannot_extract():
    with pytest.raises(PortExtractError, match="no extract function"):
        PortSpec("Cohort", param="cohort_id").extract_artifacts({})


def test_extract_errors_propagate():
    port = PortSpec("Cohort", extract=_cohort)

    with pytest.raises(KeyError):
        port.extract_artifacts({})
