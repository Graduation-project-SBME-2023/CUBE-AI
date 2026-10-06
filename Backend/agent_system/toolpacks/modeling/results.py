"""Helpers for recording modeling comparisons."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from ..radiomics.digests import bytes_digest, document_digest
from ..common import domain_api
from ..common.values import json_safe

MODEL_RESULT_SCHEMA_VERSION = 1
# exception 2: fitted research pipelines stay on disk, under this logical
# directory of the workspace and nowhere else (the storage guard's allowlist
# names it by path and purpose).
MODEL_BINARY_LOGICAL_ROOT = "modeling/models"
_SLUG = re.compile(r"[^A-Za-z0-9_.-]+")


def versioned(document: Mapping[str, Any]) -> dict[str, Any]:
    return {"schema_version": MODEL_RESULT_SCHEMA_VERSION, **json_safe(dict(document))}


def slug(value: str) -> str:
    return _SLUG.sub("_", str(value)).strip("_") or "item"


def check_one_source(
    feature_set_id: Optional[str], input_path: Optional[str], holdout_path: Optional[str]
) -> None:
    """A training run names exactly one input: a frozen feature set — the handoff from feature extraction — or a generic CSV that will be captured. A feature set carries its own holdout."""
    if bool(feature_set_id) == bool(input_path):
        raise ValueError("Pass exactly one of feature_set_id (the normal case) or input_path.")
    if feature_set_id and holdout_path:
        raise ValueError(
            "holdout_path applies to input_path only; a feature set carries its own holdout."
        )


def deterministic_seed(dataset_digest: str) -> int:
    """The seed a caller did not choose, derived from the data it trains on —
    so a replay of the same step trains with the same seed."""
    return int(dataset_digest[:8], 16) % 10_000 + 1


def comparison_key(task: str, source_label: str, config: Mapping[str, Any]) -> str:
    """Deterministic: the same input and configuration name the same
    comparison, which is what makes a replay collide instead of duplicating."""
    return f"{task}:{slug(source_label)}:{document_digest(dict(config))[:12]}"


def model_file_logical_name(comparison: str, variant: str) -> str:
    return f"{MODEL_BINARY_LOGICAL_ROOT}/{slug(comparison)}/{slug(variant)}.pkl"


def model_file_base(workspace_dir: str, comparison: str, variant: str) -> str:
    """A variant's model file path without its `.pkl` suffix (the form the
    tools' `model_path` arguments and replies use)."""
    from storage_paths import workspace_subpath

    target = workspace_subpath(workspace_dir, model_file_logical_name(comparison, variant))
    target.parent.mkdir(parents=True, exist_ok=True)
    return str(target)[: -len(".pkl")]


def file_digest(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def environment_document() -> dict[str, Any]:
    """What a refit needs to know it is (or is not) running the same stack:
    `engine: "sklearn"` and the library versions. Bit-identical refits across
    libraries and hardware are not promised."""
    from .engine.persistence import environment

    return versioned(environment())


def _native(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    return isinstance(value, (list, tuple)) and all(_native(item) for item in value)


def estimator_spec(model: Any) -> dict[str, Any]:
    """The estimator as a refit can rebuild it: its import path and the
    JSON-native part of its parameters (lists of plain values included)."""
    estimator = model
    steps = getattr(model, "steps", None)
    if steps:
        estimator = steps[-1][1]
    cls = type(estimator)
    params: dict[str, Any] = {}
    try:
        for name, value in estimator.get_params(deep=False).items():
            if _native(value):
                params[name] = json_safe(value)
    except Exception:
        pass
    return {"estimator": f"{cls.__module__}.{cls.__qualname__}", "params": params}


def predictions_logical_key(run_id: str, comparison: str, variant: str, payload: bytes) -> str:
    """Content-addressed: a replay with identical predictions reuses the
    artifact, and different bytes land under a new key instead of a 409."""
    return (
        f"modeling/{run_id}/{slug(comparison)}/{slug(variant)}/"
        f"predictions_{bytes_digest(payload)[:12]}.csv"
    )


async def publish_predictions(
    frame: Any, *, run_id: str, comparison: str, variant: str, metadata: Mapping[str, Any]
) -> dict[str, Any]:
    payload = frame.to_csv(index=False).encode("utf-8")
    return await domain_api.publish_artifact(
        logical_key=predictions_logical_key(run_id, comparison, variant, payload),
        kind="export_csv",
        payload=payload,
        filename=f"{slug(variant)}_predictions.csv",
        mime_type="text/csv",
        metadata=versioned(metadata),
    )


async def publish_figures(
    plots_dir: str, *, run_id: str, comparison: str, variant: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """Every PNG a plot helper left in `plots_dir`, as retained artifacts."""
    figures: list[dict[str, Any]] = []
    warnings: list[str] = []
    if not os.path.isdir(plots_dir):
        return figures, warnings
    for name in sorted(os.listdir(plots_dir)):
        if not name.lower().endswith(".png"):
            continue
        payload = Path(plots_dir, name).read_bytes()
        if not payload:
            continue
        key = (
            f"modeling/{run_id}/{slug(comparison)}/{slug(variant)}/"
            f"{slug(Path(name).stem)}_{bytes_digest(payload)[:12]}.png"
        )
        try:
            artifact = await domain_api.publish_artifact(
                logical_key=key,
                kind="figure_png",
                payload=payload,
                filename=name,
                mime_type="image/png",
                metadata=versioned({"variant_key": variant, "comparison_key": comparison}),
            )
        except domain_api.DomainApiError as exc:
            warnings.append(f"figure {name} was not published: {exc}")
            continue
        figures.append(
            {
                "artifact_id": str(artifact["file_id"]),
                "logical_key": artifact["logical_key"],
                "display_filename": artifact.get("display_filename"),
                "variant_key": variant,
            }
        )
    return figures, warnings


@dataclass
class ModelingInput:
    """What a comparison trains on, and how that is proven afterwards."""

    source_label: str
    cohort_id: str
    dataset_digest: str
    train_path: str
    holdout_path: Optional[str]
    feature_set_id: Optional[str] = None
    input_file_id: Optional[str] = None
    holdout_artifact_id: Optional[str] = None
    label_supervised_on_full_data: Optional[bool] = None
    holdout_groups: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


async def _captured_input(path: str, run_id: str, role: str) -> tuple[bytes, dict[str, Any]]:
    payload = Path(path).read_bytes()
    if not payload:
        raise ValueError(f"The {role} CSV {path} is empty")
    artifact = await domain_api.publish_artifact(
        logical_key=f"modeling/{run_id}/inputs/{bytes_digest(payload)[:16]}_{slug(Path(path).name)}",
        kind="input_csv",
        payload=payload,
        filename=Path(path).name,
        mime_type="text/csv",
        metadata=versioned({"role": role, "source_path": Path(path).name}),
    )
    return payload, artifact


async def prepare_input(
    *,
    feature_set_id: Optional[str],
    input_path: Optional[str],
    holdout_path: Optional[str],
    work_dir: str,
) -> ModelingInput:
    """Materialize the training data in disposable storage."""
    os.makedirs(work_dir, exist_ok=True)
    run_id = domain_api.require_run()

    if feature_set_id:
        feature_set = await domain_api.get_feature_set(feature_set_id)
        if feature_set.get("status") != "frozen":
            raise ValueError(f"Feature set {feature_set_id} is not frozen; modeling trains only on frozen sets.")
        selection = feature_set.get("selection") or {}
        everything = await domain_api.export_feature_set(feature_set_id, part="all", fmt="csv")
        payload = everything.encode("utf-8") if isinstance(everything, str) else everything
        holdout_groups = [str(group) for group in selection.get("holdout_groups") or []]
        honest_holdout = bool(holdout_groups) and not selection.get("label_supervised_on_full_data")
        train_path = os.path.join(work_dir, "train.csv")
        holdout_file = None
        if honest_holdout:
            train = await domain_api.export_feature_set(feature_set_id, part="train", fmt="csv")
            holdout = await domain_api.export_feature_set(feature_set_id, part="holdout", fmt="csv")
            Path(train_path).write_text(train, encoding="utf-8")
            holdout_file = os.path.join(work_dir, "holdout.csv")
            Path(holdout_file).write_text(holdout, encoding="utf-8")
        else:
            Path(train_path).write_bytes(payload)
        return ModelingInput(
            source_label=str(feature_set.get("set_key") or "feature_set"),
            cohort_id=str(feature_set["cohort_id"]),
            dataset_digest=bytes_digest(payload),
            train_path=train_path,
            holdout_path=holdout_file,
            feature_set_id=str(feature_set_id),
            label_supervised_on_full_data=bool(selection.get("label_supervised_on_full_data")),
            holdout_groups=holdout_groups,
        )

    if not input_path:
        raise ValueError("Pass feature_set_id (the normal case) or input_path.")
    if not os.path.exists(input_path):
        raise ValueError(f"Input file not found: {input_path}")
    cohort = await domain_api.require_population_cohort()
    payload, artifact = await _captured_input(input_path, run_id, "input")
    prepared = ModelingInput(
        source_label="input",
        cohort_id=str(cohort["cohort_id"]),
        dataset_digest=bytes_digest(payload),
        train_path=input_path,
        holdout_path=None,
        input_file_id=str(artifact["file_id"]),
        notes=["trained on a generic CSV; its exact bytes are the captured input artifact"],
    )
    if holdout_path and os.path.exists(holdout_path):
        holdout_payload, holdout_artifact = await _captured_input(holdout_path, run_id, "holdout")
        prepared.holdout_path = holdout_path
        prepared.holdout_artifact_id = str(holdout_artifact["file_id"])
        prepared.dataset_digest = document_digest(
            {"input": bytes_digest(payload), "holdout": bytes_digest(holdout_payload)}
        )
    return prepared


def comparison_payload(
    prepared: ModelingInput,
    *,
    preprocessing: Mapping[str, Any],
    features: list[str],
    split_definition: Mapping[str, Any],
    seeds: Mapping[str, Any],
    variants: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "cohort_id": prepared.cohort_id,
        "feature_set_id": prepared.feature_set_id,
        "input_file_id": prepared.input_file_id,
        "dataset_digest": prepared.dataset_digest,
        "preprocessing": versioned(preprocessing),
        "feature_order": versioned({"features": [str(name) for name in features], "target": "target"}),
        "split_definition": versioned(
            {
                **split_definition,
                "feature_set_id": prepared.feature_set_id,
                "holdout_groups": prepared.holdout_groups,
                "holdout_artifact_id": prepared.holdout_artifact_id,
            }
        ),
        "seeds": versioned(seeds),
        "environment": environment_document(),
        "variants": variants,
    }


def evaluation_kind(prepared: ModelingInput, holdout_used: bool) -> str:
    if holdout_used and prepared.feature_set_id:
        return "selection_holdout"
    if holdout_used:
        return "explicit_holdout"
    return "random_split"


def generalisation_estimate(prepared: ModelingInput, holdout_used: bool) -> bool:
    """Metrics are a generalisation estimate only when the evaluated rows never reached the feature selector."""
    if prepared.feature_set_id is None:
        return bool(holdout_used)
    return bool(holdout_used) and not prepared.label_supervised_on_full_data


def model_path_for(variant: Mapping[str, Any]) -> Optional[str]:
    """A recorded variant's model file path, without its `.pkl` suffix."""
    from storage_paths import workspace_subpath

    from ..common.workspace import get_workspace

    name = variant.get("model_file_name")
    if not name:
        return None
    return str(workspace_subpath(get_workspace(), name))[: -len(".pkl")]


def recorded_response(
    recorded: Mapping[str, Any],
    *,
    experiment_name: str,
    prepared: Optional[ModelingInput],
    reused: bool,
    figures: Any = (),
    warnings: Any = (),
    chart_data: Optional[dict] = None,
) -> dict[str, Any]:
    """A training tool's reply, built from the recorded rows — so a replay that
    reuses a recorded comparison answers exactly like the run that wrote it."""
    variants = list(recorded.get("variants") or [])
    selected = next((variant for variant in variants if variant.get("selected")), None)
    split = (variants[0].get("split_definition") or {}) if variants else {}
    evaluation = ((selected or {}).get("metrics") or {}).get("evaluation") or {}
    predictions = {
        variant["variant_key"]: str(variant["predictions_file_id"])
        for variant in variants
        if variant.get("predictions_file_id")
    }
    return {
        "status": "success",
        "experiment_name": experiment_name,
        "comparison_key": recorded["comparison_key"],
        # True when a replay found this comparison already recorded and read
        # it back instead of training a second time.
        "reused_recorded_results": reused,
        "feature_set_id": recorded.get("feature_set_id"),
        "input_artifact_id": recorded.get("input_file_id"),  # tool result key, kept (plan.md: no success result shape changes)
        "dataset_digest": recorded.get("dataset_digest"),
        "selected_variant": recorded.get("selected_variant_key"),
        "model_results": [
            {
                "model_result_id": str(variant["model_result_id"]),
                "variant_key": variant["variant_key"],
                "algorithm": variant["algorithm"],
                "status": variant["status"],
                "selected": variant["selected"],
                "model_file_name": variant.get("model_file_name"),
                "predictions_artifact_id": variant.get("predictions_file_id"),  # tool result key, kept (plan.md: no success result shape changes)
            }
            for variant in variants
        ],
        "final_model_path": model_path_for(selected) if selected else None,
        "individual_model_paths": {
            variant["variant_key"]: model_path_for(variant)
            for variant in variants
            if variant.get("model_file_name")
        },
        "model_test_predictions": predictions,
        "exports": {
            f"predictions_{key}": f"/files/{file_id}/content?download=true"
            for key, file_id in predictions.items()
        },
        # True when evaluation ran on rows feature selection never saw. When
        # False the holdout metrics are optimistic by an unknown amount.
        "selection_holdout_used": split.get("kind") in ("selection_holdout", "explicit_holdout"),
        # Whether these metrics may be presented as
        # generalisation performance at all.
        "generalisation_estimate": bool(evaluation.get("generalisation_estimate")),
        "label_supervised_on_full_data": (
            prepared.label_supervised_on_full_data if prepared is not None else None
        ),
        "figures": list(figures),
        "warnings": list(warnings),
        "chart_data": chart_data,
    }


async def recorded_variant_for(model_path: str) -> Optional[dict[str, Any]]:
    """The recorded model result whose fitted pipeline is this file."""
    from storage_paths import workspace_subpath

    from ..common.workspace import get_workspace

    target = os.path.normcase(os.path.abspath(model_path + ".pkl"))
    for row in await domain_api.session_model_results():
        name = row.get("model_file_name")
        if name and os.path.normcase(
            os.path.abspath(str(workspace_subpath(get_workspace(), name)))
        ) == target:
            return row
    return None


def model_file_mismatch(model_path: str, variant: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Identify a pipeline by the digest recorded for it, never trust it."""
    if variant is None or not os.path.exists(model_path + ".pkl"):
        return None
    if file_digest(model_path + ".pkl") == variant.get("model_file_digest"):
        return None
    return (
        "The model file does not match the digest recorded for "
        f"{variant.get('variant_key')!r}; refusing to use a swapped or corrupted "
        "pipeline. Refit it from its recorded split and seeds."
    )


async def record_independent_evaluation(
    *,
    base_variant: Mapping[str, Any],
    input_bytes: bytes,
    input_name: str,
    predictions_file_id: str,
    n_cases: int,
    metrics: Mapping[str, Any],
) -> dict[str, str]:
    """A later evaluation on independent data, as a NEW variant of the model's comparison — the recorded result is never rewritten."""
    run_id = domain_api.require_run()
    full = await domain_api.get_model_result(str(base_variant["model_result_id"]))
    digest = bytes_digest(input_bytes)
    captured = await domain_api.publish_artifact(
        logical_key=f"modeling/{run_id}/inputs/{digest[:16]}_{slug(input_name)}",
        kind="input_csv",
        payload=input_bytes,
        filename=input_name,
        mime_type="text/csv",
        metadata=versioned({"role": "independent_evaluation_input"}),
    )
    variant_key = f"{full['variant_key']}__independent_{digest[:12]}"
    await domain_api.record_model_comparison(
        full["comparison_key"],
        {
            "cohort_id": str(full["cohort_id"]),
            "feature_set_id": None,
            "input_file_id": str(captured["file_id"]),
            "dataset_digest": digest,
            "preprocessing": full["preprocessing"],
            "feature_order": full["feature_order"],
            "split_definition": versioned(
                {
                    "kind": "independent_evaluation",
                    "base_model_result_id": str(full["model_result_id"]),
                    "base_variant_key": full["variant_key"],
                    "n_cases": int(n_cases),
                }
            ),
            "seeds": full["seeds"],
            "environment": environment_document(),
            "variants": [
                {
                    "variant_key": variant_key,
                    "algorithm": full["algorithm"],
                    "parameters": full["parameters"],
                    "metrics": versioned(
                        {
                            "stage": "independent_evaluation",
                            "evaluation": {
                                "kind": "independent",
                                "n_cases": int(n_cases),
                                "generalisation_estimate": True,
                                "metrics": json_safe(dict(metrics)),
                            },
                        }
                    ),
                    "predictions_file_id": predictions_file_id,
                    "model_file_name": full["model_file_name"],
                    "model_file_digest": full["model_file_digest"],
                    "selected": False,
                    "status": "ready",
                }
            ],
        },
    )
    return {"comparison_key": full["comparison_key"], "variant_key": variant_key}


__all__ = [
    "MODEL_BINARY_LOGICAL_ROOT",
    "ModelingInput",
    "model_file_mismatch",
    "model_path_for",
    "record_independent_evaluation",
    "recorded_response",
    "recorded_variant_for",
    "comparison_key",
    "comparison_payload",
    "deterministic_seed",
    "environment_document",
    "estimator_spec",
    "evaluation_kind",
    "file_digest",
    "generalisation_estimate",
    "json_safe",
    "model_file_base",
    "model_file_logical_name",
    "prepare_input",
    "publish_figures",
    "publish_predictions",
    "slug",
    "versioned",
]
