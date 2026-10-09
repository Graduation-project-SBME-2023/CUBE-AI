"""``radiomics_extract_features``: measure every reviewed mask of the approved
cohort with PyRadiomics and record one provenance row per measured segment.

1. the extraction's settings and identity (``extraction.config``);
2. the approved cohort's labels and the reviewed pairs (``extraction.pairs``);
3. per pair, off the event loop, its masks (``extraction.masks``), then each
   mask in the PyRadiomics worker (``extraction.worker``) unless the cache has it;
4. the rows, recorded in all-or-nothing batches;
5. the summary, from the rows exactly as feature selection reads them.

A PostgreSQL write failure fails the call: there is no file to fall back to,
and an extraction that leaves ``radiomics_features`` empty has not happened.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from .._toolkit import tool
from ..common import domain_api
from ..common.emit import emit, emit_progress
from ..common.failures import ToolFailure, fail
from ..common.manifests import INTERIM_HEAVY
from ..common.values import float_list, int_list, str_list
from ..common.workspace import WorkspacePath
from .extraction import config as extraction_config
from .extraction import summary as extraction_summary
from .extraction import worker
from .extraction.masks import PreparedMask, PreparedPair, discard, prepare_pair, remember
from .extraction.pairs import MaskPair, find_image_dir, image_paths, normalise_id, reviewed_pairs, volume_id
from .feature_set_builder import FeatureSetBuildError, finite_or_none, wide_table_from_rows
from storage_paths import CACHE_DIR, TEMP_DIR

logger = logging.getLogger(__name__)

ROW_SCHEMA_VERSION = 1
MAX_MISSING_LISTED = 200
# The whole cohort, mask by mask; each mask has its own budget (worker.timeout_s()).
TOOL_TIMEOUT_S = 4 * 3600


class ExtractFeaturesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="ignore")

    image_types: Optional[Union[str, List[str]]] = Field(
        default=None, description="Image filters, e.g. 'Original,Wavelet' (the default) or 'LoG'."
    )
    feature_classes: Optional[Union[str, List[str]]] = Field(
        default=None, description="Feature classes, e.g. 'firstorder,shape,glcm'. Default: all seven."
    )
    specific_features: Optional[Union[str, List[str]]] = Field(
        default=None, description="Individual features instead of classes, e.g. 'original_firstorder_Mean'."
    )
    mask_labels: Optional[Union[int, str, List[Union[int, str]]]] = Field(
        default=None, description="Restrict which label values of a mask file may be measured."
    )
    pixel_spacing: Optional[Union[float, str, List[Union[float, str]]]] = Field(
        default=None, description="Resampled pixel spacing in mm (default 1,1,1)."
    )
    image_pattern: str = Field(default="*.nii.gz")
    mask_pattern: str = Field(
        default="*.nii.gz", description="Ignored: each reviewed pair names its own mask file."
    )
    normalize: bool = Field(default=True)
    bin_width: float = Field(default=25.0)
    resample: bool = Field(default=True)
    force_2d: bool = Field(default=False)
    n_workers: int = Field(
        default=1, ge=1, description="Accepted for compatibility: masks are measured one at a time."
    )
    # Deprecated and ignored since the database-backed flow: target labels come from the
    # approved cohort's members, never from a targets.csv on disk.
    targets_csv: WorkspacePath = Field(default=None)
    id_column: Optional[str] = Field(default=None)
    target_column: Optional[str] = Field(default=None)
    id_pattern: Optional[str] = Field(
        default=None, description="A regex whose first group is the volume uid in an image file name."
    )
    image_dir: WorkspacePath = Field(default=None)
    mask_dir: WorkspacePath = Field(default=None, description="Ignored: each reviewed pair names its own mask.")
    output_dir: WorkspacePath = Field(default=None, description="Ignored: the measurements are rows.")
    require_all_organs: bool = Field(default=False)


@dataclass
class Cohort:
    cohort_id: str
    analysis_rows: list[dict[str, Any]]
    targets: dict[str, Any]  # normalised volume uid -> label
    subject_of_volume: dict[str, str]


@dataclass
class Measurements:
    rows: list[dict[str, Any]] = field(default_factory=list)
    target_of_row: dict[tuple, str] = field(default_factory=dict)
    attempted: set[tuple[str, str]] = field(default_factory=set)  # (subject, target)
    warnings: dict[str, str] = field(default_factory=dict)  # volume uid -> warning
    labels_used: set[int] = field(default_factory=set)


async def _approved_cohort() -> Cohort:
    cohort = await domain_api.require_population_cohort()
    if not cohort.get("frozen_at"):
        raise fail(
            "cohort_not_approved",
            "The population cohort has not been approved. Feature extraction requires the "
            "doctor-approved cohort's labels.",
        )
    rows = await domain_api.cohort_analysis_rows(cohort["cohort_id"])
    if not rows:
        raise fail("empty_cohort", "The approved cohort has no labelled volume.")
    logger.info("Loaded %d labelled cohort member(s) from cohort %s", len(rows), cohort["cohort_id"])
    return Cohort(
        cohort_id=str(cohort["cohort_id"]),
        analysis_rows=rows,
        targets={normalise_id(row["volume_series_instance_uid"]): row["target"] for row in rows},
        subject_of_volume={str(row["volume_series_instance_uid"]): str(row["subject_key"]) for row in rows},
    )


def _row(pair: MaskPair, prepared: PreparedPair, mask: PreparedMask, features: dict, config) -> dict[str, Any]:
    """One provenance row; a non-finite value is recorded as null."""
    return {
        "label_series_instance_uid": pair.label_series_instance_uid,
        "source_volume_uid": pair.volume_uid,
        "segment_number": int(mask.segment_number),
        "segmentation_pair_id": pair.segmentation_pair_id or None,
        "features": {str(name): finite_or_none(value) for name, value in features.items()},
        "extractor_name": config.extractor_name,
        "extractor_version": config.extractor_version,
        "configuration": config.document,
        "config_digest": config.digest,
        "image_digest": prepared.image_digest,
        "segmentation_digest": mask.segmentation_digest,
        "source_digest": mask.source_digest,
        "schema_version": ROW_SCHEMA_VERSION,
    }


def _row_key(row: dict[str, Any]) -> tuple:
    return (row["label_series_instance_uid"], int(row["segment_number"]), row["source_digest"])


async def _measure_mask(pool, mask: PreparedMask, pair: MaskPair, params_file: str, cache_dir: str) -> dict:
    """The mask's features: cached, or measured in the worker; ``{}`` when it
    could not be measured (timed out, crashed, failed)."""
    if mask.cached is not None:
        logger.info("%s label %s: cached features", pair.volume_uid, mask.label)
        return mask.cached
    try:
        features = await pool.extract(pair.image, mask.path, params_file, worker.timeout_s())
    except worker.ExtractionTimeout:
        logger.error("%s label %s: extraction timed out after %.0fs; skipping", pair.volume_uid, mask.label, worker.timeout_s())
        return {}
    except worker.ExtractionCrashed as exc:
        logger.error("%s label %s: %s; skipping", pair.volume_uid, mask.label, exc)
        return {}
    except Exception as exc:
        logger.error("%s label %s: extraction failed: %s", pair.volume_uid, mask.label, exc)
        return {}
    await asyncio.to_thread(remember, cache_dir, mask, features)
    return features


async def _measure(
    pairs: dict[str, MaskPair],
    cohort: Cohort,
    config: extraction_config.ExtractionConfig,
    params_file: str,
    requested_labels: set[int] | None,
) -> Measurements:
    """Every pair's masks, one pair at a time."""
    result = Measurements()
    cache_dir = str(CACHE_DIR / "monailabel" / "feature-extraction")
    image_digests: dict[str, str] = {}
    radiology_temp = TEMP_DIR / "radiology"
    radiology_temp.mkdir(parents=True, exist_ok=True)
    temp_dir = tempfile.mkdtemp(dir=radiology_temp)
    pool = worker.ExtractorPool()
    try:
        await pool.check_settings(params_file)
        emit("tool_log", f"Extracting features for {len(pairs)} masks...")
        for index, pair in enumerate(pairs.values(), 1):
            emit_progress(current=index, total=len(pairs), label="Processing Volumes")
            logger.info("Processing %s target=%s (%d/%d)", pair.volume_uid, pair.target_key, index, len(pairs))
            try:
                prepared = await asyncio.to_thread(
                    prepare_pair,
                    pair,
                    config_digest=config.digest,
                    temp_dir=temp_dir,
                    cache_dir=cache_dir,
                    image_digests=image_digests,
                    requested_labels=requested_labels,
                )
            except Exception as exc:
                logger.error("Error reading %s: %s", pair.volume_uid, exc)
                continue
            result.attempted.add((cohort.subject_of_volume.get(pair.volume_uid, pair.volume_uid), pair.target_key))
            if prepared.warning:
                result.warnings.setdefault(pair.volume_uid, prepared.warning)
            try:
                for mask in prepared.masks:
                    features = await _measure_mask(pool, mask, pair, params_file, cache_dir)
                    if not features:
                        continue
                    row = _row(pair, prepared, mask, features, config)
                    result.rows.append(row)
                    result.target_of_row[_row_key(row)] = pair.target_key
                    result.labels_used.add(int(mask.label))
            finally:
                await asyncio.to_thread(discard, prepared)
    finally:
        await pool.close()
        await asyncio.to_thread(shutil.rmtree, temp_dir, True)
    return result


async def _record(rows: list[dict[str, Any]], target_of_row: dict[tuple, str]) -> tuple[dict, list[dict]]:
    """Record the rows in batches; each batch is all-or-nothing and replays
    onto the content-addressed key, so a retry after a crash reuses rows."""
    counts = {"created": 0, "reused": 0}
    recorded: list[dict[str, Any]] = []
    for offset in range(0, len(rows), domain_api.RADIOMICS_BATCH_SIZE):
        batch = rows[offset: offset + domain_api.RADIOMICS_BATCH_SIZE]
        try:
            result = await domain_api.record_radiomics_rows(batch)
        except domain_api.DomainApiError as exc:
            raise ToolFailure(domain_api.error_result_from(
                exc, "Radiomics rows could not be recorded", recorded_before_failure=counts
            )) from exc
        counts["created"] += int(result["created"])
        counts["reused"] += int(result["reused"])
        ids = {
            (item["label_series_instance_uid"], int(item["segment_number"]), item["source_digest"]):
                item["radiomics_feature_id"]
            for item in result["rows"]
        }
        recorded += [
            {**row, "radiomics_feature_id": ids[_row_key(row)], "target_key": target_of_row.get(_row_key(row))}
            for row in batch
        ]
    return counts, recorded


def _gpu_available() -> bool:
    """Reported for observability only: PyRadiomics has no CUDA path."""
    try:
        import torch
    except ImportError:
        return False
    try:
        return bool(torch.cuda.is_available())
    except Exception:
        return False


async def _extract(params: ExtractFeaturesInput) -> dict[str, Any]:
    try:
        import SimpleITK  # noqa: F401
        version = extraction_config.extractor_version()
    except ImportError:
        raise fail("dependency_missing", "PyRadiomics or SimpleITK is not installed.")

    try:
        choice = extraction_config.choose_features(
            str_list(params.image_types), str_list(params.feature_classes), str_list(params.specific_features)
        )
        requested_labels = set(int_list(params.mask_labels) or []) or None
        pixel_spacing = float_list(params.pixel_spacing) or list(extraction_config.DEFAULT_PIXEL_SPACING)
    except ValueError as exc:
        raise fail("invalid_args", str(exc))
    for note in choice.notes:
        logger.warning(note)

    image_dir = find_image_dir(params.image_dir, params.image_pattern)
    images = image_paths(image_dir, params.image_pattern) if image_dir else []
    if not images:
        raise fail(
            "images_not_found",
            f"No images found in {image_dir!r} matching {params.image_pattern!r}. Please ensure the "
            "volumes exist before running feature extraction.",
        )
    bin_width = await asyncio.to_thread(extraction_config.adaptive_bin_width, images, params.bin_width)
    config = extraction_config.ExtractionConfig(
        settings=extraction_config.pyradiomics_settings(
            choice,
            bin_width=bin_width,
            normalize=params.normalize,
            resample=params.resample,
            pixel_spacing=pixel_spacing,
            force_2d=params.force_2d,
        ),
        extractor_version=version,
    )
    run_id = domain_api.require_run()
    params_file = await asyncio.to_thread(
        extraction_config.write_settings,
        config.settings,
        str(TEMP_DIR / "feature-extraction" / run_id),
    )

    cohort = await _approved_cohort()
    by_volume = {volume_id(path, params.id_pattern): path for path in images}
    pairs = reviewed_pairs(await domain_api.reviewed_masks(), by_volume)
    logger.info("Loaded %d reviewed segmentation mask(s)", len(pairs))
    if not pairs:
        raise fail(
            "no_reviewed_masks",
            "No reviewed segmentation mask is available to measure. Feature extraction measures the "
            "accepted or modified masks of a submitted segmentation review; submit the review first.",
        )
    paired_subjects = len({pair.volume_uid for pair in pairs.values()})
    unlabeled = sorted({p.volume_uid for p in pairs.values() if normalise_id(p.volume_uid) not in cohort.targets})
    pairs = {key: p for key, p in pairs.items() if normalise_id(p.volume_uid) in cohort.targets}
    if not pairs:
        raise fail(
            "no_labelled_masks",
            "No image-mask pair could be matched to a target label. Check that the image files are "
            "named by the cohort's volume UIDs, or provide id_pattern.",
            unlabeled_subject_ids=unlabeled,
        )
    if unlabeled:
        logger.warning("Skipping %d image-mask pair(s) with no target label: %s", len(unlabeled), unlabeled)

    started = time.monotonic()
    measured = await _measure(pairs, cohort, config, params_file, requested_labels)
    processing_time = time.monotonic() - started
    emit("tool_log", "Feature Extraction...Done")
    if not measured.rows:
        raise fail(
            "extraction_failed",
            "No radiomic features were extracted from any reviewed mask.",
            paired_subjects=paired_subjects,
            labeled_subjects=len(pairs),
        )

    counts, recorded = await _record(measured.rows, measured.target_of_row)
    rows_summary = {**counts, "total": len(recorded)}
    try:
        table = wide_table_from_rows(recorded, cohort.analysis_rows)
    except FeatureSetBuildError as exc:
        raise fail(
            "invalid_feature_set",
            f"Radiomics rows were recorded, but they cannot form a feature set: {exc}",
            radiomics_rows=rows_summary,
        )
    warnings_by_subject = {
        cohort.subject_of_volume.get(volume, volume): message for volume, message in measured.warnings.items()
    }
    report = extraction_summary.audit(table, measured.attempted, warnings_by_subject)
    if params.require_all_organs and report.missing:
        raise fail(
            "missing_organs",
            f"{len(report.missing)} required patient-organ combinations are missing",
            radiomics_rows=rows_summary,
            missing_patient_organs=report.missing[:MAX_MISSING_LISTED],
        )

    return {
        "status": "success",
        "agent_run_id": run_id,  # tool result key, kept (plan.md: no success result shape changes)
        "cohort_id": cohort.cohort_id,
        # The measurements are rows; this is their count, and the export
        # below is generated from them on request.
        "radiomics_rows": rows_summary,
        "exports": {"radiomics_features": f"/radiomics/run/{run_id}/export"},
        "num_subjects": extraction_summary.subjects_with_features(table),
        "num_volume_target_pairs": len(pairs),
        "paired_subjects": paired_subjects,
        "unlabeled_subject_ids": unlabeled,
        "num_features": extraction_summary.features_per_organ(table),
        "mask_labels": sorted(measured.labels_used),
        "processing_time_seconds": round(processing_time, 2),
        "summary": extraction_summary.summary(
            table,
            report,
            warnings=measured.warnings,
            params_used={
                "bin_width": bin_width,
                "image_types": choice.image_types,
                "feature_classes": choice.feature_classes or [],
                "config_digest": config.digest,
                "extractor": f"{config.extractor_name} {config.extractor_version}",
            },
            gpu_available=await asyncio.to_thread(_gpu_available),
        ),
        "next_step": (
            "Call radiomics_analyze_importance with no input_path; it selects from "
            "the rows this run recorded and freezes the feature sets."
        ),
    }


@tool(
    name="radiomics_extract_features",
    visibility={"agent"},
    timeout_s=TOOL_TIMEOUT_S,
    resources={"cpu_heavy"},
    data_scopes={"phi_read", "db_write", "fs_write"},
    **INTERIM_HEAVY,
)
async def radiomics_extract_features(params: ExtractFeaturesInput) -> str | dict[str, Any]:
    """Extract radiomic features from medical images and segmentation masks using PyRadiomics."""
    try:
        return json.dumps(await _extract(params), indent=2)
    except ToolFailure as failure:
        return failure.result
    except domain_api.DomainApiError as exc:
        return domain_api.error_result_from(exc, "Feature extraction could not read its inputs")
