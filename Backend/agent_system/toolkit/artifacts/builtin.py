"""The built-in artifact types (merged plan, Phase 1.2 step 2).

Every ref points at an Application API row by its UUID key (``cohorts``,
``segmentation_runs``, ``feature_sets``, ``model_results``,
``literature_reviews``, ``agent_runs``, ``reports``), except ``ImageSet``,
which lists DICOM series UIDs.

``phi_default`` says whether the data behind the pointer is patient-level:
cohorts, images, masks and per-subject feature rows are; importances, model
metrics, literature, result summaries and the research report are aggregate.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints

from .base import ArtifactRef, ArtifactSummary, ArtifactType, ArtifactTypeRegistry

type Count = Annotated[int, Field(ge=0)]
# A DICOM UID: digits and dots, at most 64 characters.
type DicomUid = Annotated[str, StringConstraints(pattern=r"^[0-9]+(\.[0-9]+)*$", max_length=64)]
type Name = Annotated[str, StringConstraints(min_length=1, max_length=200)]


# --- Cohort


class CohortRef(ArtifactRef):
    cohort_id: UUID


class CohortSummary(ArtifactSummary):
    size: Count
    label_counts: dict[str, Count] = {}
    frozen: bool


COHORT = ArtifactType(
    name="Cohort",
    description="A study population: the selected volumes and their labels.",
    ref_model=CohortRef,
    summary_model=CohortSummary,
    phi_default=True,
)


# --- ImageSet


class ImageSetRef(ArtifactRef):
    series_uids: Annotated[list[DicomUid], Field(min_length=1)]


class ImageSetSummary(ArtifactSummary):
    n_series: Count
    modalities: list[Name] = []


IMAGE_SET = ArtifactType(
    name="ImageSet",
    description="A set of DICOM image series in Orthanc.",
    ref_model=ImageSetRef,
    summary_model=ImageSetSummary,
    phi_default=True,
)


# --- MaskSet


class MaskSetRef(ArtifactRef):
    segmentation_run_id: UUID


class MaskSetSummary(ArtifactSummary):
    n_masks: Count
    n_failed: Count = 0
    # segmentation_runs.review_status
    review_status: Literal["not_ready", "pending", "submitting", "submitted"]


MASK_SET = ArtifactType(
    name="MaskSet",
    description="Segmentation masks for a cohort's volumes, from one segmentation run.",
    ref_model=MaskSetRef,
    summary_model=MaskSetSummary,
    phi_default=True,
)


# --- FeatureTable


class FeatureTableRef(ArtifactRef):
    feature_set_id: UUID


class FeatureTableSummary(ArtifactSummary):
    n_rows: Count
    n_features: Count


FEATURE_TABLE = ArtifactType(
    name="FeatureTable",
    description="Radiomics features: one row per subject, one column per feature.",
    ref_model=FeatureTableRef,
    summary_model=FeatureTableSummary,
    phi_default=True,
)


# --- FeatureImportance


class FeatureImportanceRef(ArtifactRef):
    feature_set_id: UUID
    method: Name  # e.g. "rf", "lasso"


class FeatureImportanceSummary(ArtifactSummary):
    top_features: list[Name] = []


FEATURE_IMPORTANCE = ArtifactType(
    name="FeatureImportance",
    description="A ranking of a feature table's features by one importance method.",
    ref_model=FeatureImportanceRef,
    summary_model=FeatureImportanceSummary,
    phi_default=False,
)


# --- ModelingOutput


class ModelingOutputRef(ArtifactRef):
    model_result_ids: Annotated[list[UUID], Field(min_length=1)]


class ModelingOutputSummary(ArtifactSummary):
    metric_name: Name
    best_metric: float | None = None
    n_models: Count


MODELING_OUTPUT = ArtifactType(
    name="ModelingOutput",
    description="Trained models and their evaluation results.",
    ref_model=ModelingOutputRef,
    summary_model=ModelingOutputSummary,
    phi_default=False,
)


# --- LiteratureContext


class LiteratureContextRef(ArtifactRef):
    literature_review_id: UUID


class LiteratureContextSummary(ArtifactSummary):
    n_papers: Count


LITERATURE_CONTEXT = ArtifactType(
    name="LiteratureContext",
    description="A literature review: the papers found and the context compiled from them.",
    ref_model=LiteratureContextRef,
    summary_model=LiteratureContextSummary,
    phi_default=False,
)


# --- ResultSummary


class ResultSummaryRef(ArtifactRef):
    agent_run_id: UUID  # the run whose output_summary holds the findings


class ResultSummarySummary(ArtifactSummary):
    n_findings: Count


RESULT_SUMMARY = ArtifactType(
    name="ResultSummary",
    description="The interpreted findings of an analysis, written by the result reporter.",
    ref_model=ResultSummaryRef,
    summary_model=ResultSummarySummary,
    phi_default=False,
)


# --- ReportDocument


class ReportDocumentRef(ArtifactRef):
    report_id: UUID


class ReportDocumentSummary(ArtifactSummary):
    source_version: Annotated[int, Field(ge=1)]  # reports.source_version
    has_pdf: bool = False


REPORT_DOCUMENT = ArtifactType(
    name="ReportDocument",
    description="A LaTeX research report, and its compiled PDF once there is one.",
    ref_model=ReportDocumentRef,
    summary_model=ReportDocumentSummary,
    phi_default=False,
)


BUILTIN_ARTIFACT_TYPES: tuple[ArtifactType, ...] = (
    COHORT,
    IMAGE_SET,
    MASK_SET,
    FEATURE_TABLE,
    FEATURE_IMPORTANCE,
    MODELING_OUTPUT,
    LITERATURE_CONTEXT,
    RESULT_SUMMARY,
    REPORT_DOCUMENT,
)


def builtin_registry() -> ArtifactTypeRegistry:
    """A fresh registry holding the built-in types; the catalog adds pack types to it."""
    return ArtifactTypeRegistry(BUILTIN_ARTIFACT_TYPES)
