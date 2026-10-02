"""Artifact types, the registry and the built-in types (merged plan, Phase 1.2)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from agent_system.toolkit.artifacts.base import (
    MAX_SUMMARY_BYTES,
    ArtifactRef,
    ArtifactSummary,
    ArtifactType,
    ArtifactTypeRegistry,
    ArtifactValidationError,
    UnknownArtifactTypeError,
)
from agent_system.toolkit.artifacts.builtin import BUILTIN_ARTIFACT_TYPES, builtin_registry
from agent_system.toolkit.ports import ExtractedArtifact

pytestmark = pytest.mark.unit

ID = "7b0e1f8a-3c2d-4e5f-8a9b-0c1d2e3f4a5b"


class _Ref(ArtifactRef):
    cohort_id: uuid.UUID


class _Summary(ArtifactSummary):
    size: int


def _type(**overrides: Any) -> ArtifactType:
    fields: dict[str, Any] = {
        "name": "LabelledCohort",
        "description": "A cohort whose every member has a label.",
        "ref_model": _Ref,
        "summary_model": _Summary,
        "phi_default": True,
        "parents": {"Cohort"},
    }
    fields.update(overrides)
    return ArtifactType(**fields)


# --- the built-in types match the plan's table


PLAN_TABLE = {
    # type: (ref fields, summary fields, phi_default)
    "Cohort": ({"cohort_id"}, {"size", "label_counts", "frozen"}, True),
    "ImageSet": ({"series_uids"}, {"n_series", "modalities"}, True),
    "MaskSet": ({"segmentation_batch_id"}, {"n_masks", "n_failed", "review_status"}, True),
    "FeatureTable": ({"feature_set_id"}, {"n_rows", "n_features"}, True),
    "FeatureImportance": ({"feature_set_id", "method"}, {"top_features"}, False),
    "ModelingOutput": ({"model_result_ids"}, {"metric_name", "best_metric", "n_models"}, False),
    "LiteratureContext": ({"literature_review_id"}, {"n_papers"}, False),
    "ResultSummary": ({"run_id"}, {"n_findings"}, False),
    "ReportDocument": ({"report_id"}, {"source_version", "has_pdf"}, False),
}


def test_builtin_types_are_the_planned_nine():
    assert builtin_registry().names() == set(PLAN_TABLE)
    assert len(BUILTIN_ARTIFACT_TYPES) == len(PLAN_TABLE)


@pytest.mark.parametrize("name", sorted(PLAN_TABLE))
def test_builtin_type_fields(name):
    ref_fields, summary_fields, phi = PLAN_TABLE[name]
    artifact_type = builtin_registry().get(name)

    assert set(artifact_type.ref_model.model_fields) == ref_fields
    assert set(artifact_type.summary_model.model_fields) == summary_fields
    assert artifact_type.phi_default is phi
    assert artifact_type.version == 1
    assert artifact_type.parents == frozenset()


def test_builtin_registry_is_fresh_each_time():
    first, second = builtin_registry(), builtin_registry()
    first.register(_type())

    assert "LabelledCohort" in first
    assert "LabelledCohort" not in second


# --- validate_artifact


@pytest.mark.parametrize(
    ("name", "ref", "summary"),
    [
        ("Cohort", {"cohort_id": ID}, {"size": 40, "label_counts": {"true": 12}, "frozen": True}),
        ("ImageSet", {"series_uids": ["1.2.840.113619.2.55"]}, {"n_series": 1, "modalities": ["CT"]}),
        ("MaskSet", {"segmentation_batch_id": ID}, {"n_masks": 40, "review_status": "submitted"}),
        ("FeatureTable", {"feature_set_id": ID}, {"n_rows": 40, "n_features": 1409}),
        ("FeatureImportance", {"feature_set_id": ID, "method": "rf"}, {"top_features": ["a"]}),
        ("ModelingOutput", {"model_result_ids": [ID]}, {"metric_name": "AUC", "n_models": 3}),
        ("LiteratureContext", {"literature_review_id": ID}, {"n_papers": 25}),
        ("ResultSummary", {"run_id": ID}, {"n_findings": 4}),
        ("ReportDocument", {"report_id": ID}, {"source_version": 2, "has_pdf": True}),
    ],
)
def test_valid_builtin_artifacts(name, ref, summary):
    artifact = builtin_registry().get(name).validate_artifact(ExtractedArtifact(ref, summary))

    assert set(artifact.ref) == set(ref)


def test_validation_normalises_to_json():
    artifact = builtin_registry().get("ModelingOutput").validate_artifact(
        ExtractedArtifact(
            ref={"model_result_ids": [uuid.UUID(ID)]},
            summary={"metric_name": "AUC", "n_models": 1},
        )
    )

    assert artifact.ref == {"model_result_ids": [ID]}
    assert artifact.summary == {"metric_name": "AUC", "best_metric": None, "n_models": 1}


@pytest.mark.parametrize(
    ("name", "ref", "summary"),
    [
        ("Cohort", {"cohort_id": "not-a-uuid"}, {"size": 1, "frozen": False}),
        ("Cohort", {"cohort_id": ID, "members": []}, {"size": 1, "frozen": False}),  # a payload
        ("Cohort", {"cohort_id": ID}, {"size": -1, "frozen": False}),
        ("Cohort", {"cohort_id": ID}, {"size": 1}),
        ("ImageSet", {"series_uids": []}, {"n_series": 0}),
        ("ImageSet", {"series_uids": ["not a uid"]}, {"n_series": 1}),
        ("MaskSet", {"segmentation_batch_id": ID}, {"n_masks": 1, "review_status": "approved"}),
        ("ModelingOutput", {"model_result_ids": []}, {"metric_name": "AUC", "n_models": 0}),
        ("ReportDocument", {"report_id": ID}, {"source_version": 0}),
    ],
)
def test_invalid_builtin_artifacts(name, ref, summary):
    with pytest.raises(ArtifactValidationError, match=f"invalid {name} artifact"):
        builtin_registry().get(name).validate_artifact(ExtractedArtifact(ref, summary))


def test_a_summary_is_not_a_payload():
    features = [f"original_glcm_feature_{i:05d}" for i in range(MAX_SUMMARY_BYTES // 20)]

    with pytest.raises(ArtifactValidationError, match="a pointer, not a payload"):
        builtin_registry().get("FeatureImportance").validate_artifact(
            ExtractedArtifact({"feature_set_id": ID, "method": "rf"}, {"top_features": features})
        )


def test_a_ref_is_not_a_payload():
    uids = [f"1.2.840.{i}.{'1' * 50}" for i in range(2000)]

    with pytest.raises(ArtifactValidationError, match="ref is .* bytes"):
        builtin_registry().get("ImageSet").validate_artifact(
            ExtractedArtifact({"series_uids": uids}, {"n_series": len(uids)})
        )


def test_describe_gives_the_composer_the_schemas():
    described = builtin_registry().get("MaskSet").describe()

    assert described["name"] == "MaskSet"
    assert described["phi"] is True
    assert "segmentation_batch_id" in described["ref"]["properties"]
    assert described["summary"]["properties"]["review_status"]["enum"] == [
        "not_ready", "pending", "submitting", "submitted",
    ]


# --- ArtifactType checks


class _EmptyRef(ArtifactRef):
    pass


class _OptionalRef(ArtifactRef):
    cohort_id: uuid.UUID | None = None


@pytest.mark.parametrize(
    ("overrides", "error", "match"),
    [
        ({"name": ""}, ValueError, "name"),
        ({"name": "Labelled Cohort"}, ValueError, "name"),
        ({"version": 0}, ValueError, "version"),
        ({"description": " "}, ValueError, "description"),
        ({"ref_model": _Summary}, TypeError, "ArtifactRef"),
        ({"ref_model": dict}, TypeError, "ArtifactRef"),
        ({"summary_model": _Ref}, TypeError, "ArtifactSummary"),
        ({"ref_model": _EmptyRef}, ValueError, "at least one field"),
        ({"ref_model": _OptionalRef}, ValueError, r"required, not \['cohort_id'\]"),
        ({"phi_default": "yes"}, TypeError, "phi_default"),
        ({"parents": "Cohort"}, TypeError, "not a string"),
        ({"parents": {"not a name"}}, ValueError, "parent"),
        ({"parents": {"LabelledCohort"}}, ValueError, "its own parent"),
    ],
)
def test_malformed_types_are_refused(overrides, error, match):
    with pytest.raises(error, match=match):
        _type(**overrides)


def test_ref_and_summary_models_forbid_extra_fields():
    assert _Ref.model_config["extra"] == "forbid"
    assert _Summary.model_config["extra"] == "forbid"


# --- registry


def test_register_get_and_iterate():
    registry = ArtifactTypeRegistry()
    labelled = registry.register(_type())

    assert registry.get("LabelledCohort") is labelled
    assert "LabelledCohort" in registry
    assert list(registry) == [labelled]
    assert len(registry) == 1


def test_duplicate_names_are_refused_but_re_registering_is_harmless():
    registry = ArtifactTypeRegistry()
    labelled = registry.register(_type())

    registry.register(labelled)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(_type())


def test_unknown_type():
    with pytest.raises(UnknownArtifactTypeError, match="unknown artifact type 'Scan'"):
        ArtifactTypeRegistry().get("Scan")


def test_register_needs_an_artifact_type():
    with pytest.raises(TypeError):
        ArtifactTypeRegistry().register("Cohort")  # type: ignore[arg-type]


def _family() -> ArtifactTypeRegistry:
    registry = builtin_registry()
    registry.register(_type())  # LabelledCohort -> Cohort
    registry.register(_type(name="BalancedCohort", parents={"LabelledCohort"}))
    return registry


@pytest.mark.parametrize(
    ("produced", "expected", "compatible"),
    [
        ("Cohort", "Cohort", True),
        ("LabelledCohort", "Cohort", True),  # parent
        ("BalancedCohort", "Cohort", True),  # grandparent
        ("BalancedCohort", "LabelledCohort", True),
        ("Cohort", "LabelledCohort", False),  # never downwards
        ("MaskSet", "ImageSet", False),  # "expected ImageSet, received MaskSet"
        ("FeatureTable", "FeatureImportance", False),
    ],
)
def test_is_compatible(produced, expected, compatible):
    assert _family().is_compatible(produced, expected) is compatible


def test_ancestors():
    assert _family().ancestors("BalancedCohort") == {"LabelledCohort", "Cohort"}
    assert _family().ancestors("Cohort") == frozenset()


def test_is_compatible_with_an_unknown_produced_type():
    with pytest.raises(UnknownArtifactTypeError):
        _family().is_compatible("Scan", "Cohort")


def test_a_consistent_registry_has_no_problems():
    assert builtin_registry().problems() == []
    assert _family().problems() == []


def test_parent_registered_after_its_child_is_fine():
    registry = ArtifactTypeRegistry()
    registry.register(_type())
    assert registry.problems() == ["artifact type 'LabelledCohort' names unknown parent 'Cohort'"]

    registry.register(builtin_registry().get("Cohort"))
    assert registry.problems() == []


def test_inheritance_cycles_are_reported():
    registry = ArtifactTypeRegistry()
    registry.register(_type(name="A", parents={"B"}))
    registry.register(_type(name="B", parents={"A"}))
    registry.register(_type(name="C", parents={"A"}))

    assert registry.problems() == [
        "artifact type 'A' is in an inheritance cycle",
        "artifact type 'B' is in an inheritance cycle",
    ]
    assert registry.is_compatible("C", "B")  # the walk still terminates
