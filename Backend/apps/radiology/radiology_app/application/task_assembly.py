from __future__ import annotations

import logging
from typing import Any, Mapping

from radiology_app.application.settings import parse_bool

logger = logging.getLogger(__name__)


def normalize_tasks(name: str, task: Any) -> dict[str, Any]:
    """Normalize MONAI's single-task/dictionary return convention."""
    if task is None:
        return {}
    return dict(task) if isinstance(task, dict) else {name: task}


def _scribble_labels(models: Mapping[str, Any]):
    # Preserve the former deterministic behavior (the final configured model)
    # without relying on a loop variable that may not exist.
    for task_config in reversed(tuple(models.values())):
        labels = getattr(task_config, "labels", None)
        if labels:
            return labels
    return None


def build_infers(
    *,
    models: Mapping[str, Any],
    bundles: Mapping[str, Any] | None,
    conf: Mapping[str, str],
    scribbles: bool,
) -> dict[str, Any]:
    infers: dict[str, Any] = {}
    for name, task_config in models.items():
        for task_name, task in normalize_tasks(name, task_config.infer()).items():
            logger.info("+++ Adding Inferer:: %s => %s", task_name, task)
            infers[task_name] = task

    if bundles:
        from monailabel.tasks.infer.bundle import BundleInferTask

        for name, bundle in bundles.items():
            task = BundleInferTask(bundle, conf)
            logger.info("+++ Adding Bundle Inferer:: %s => %s", name, task)
            infers[name] = task

    if scribbles:
        labels = _scribble_labels(models)
        if labels:
            from monailabel.scribbles.infer import (
                GMMBasedGraphCut,
                HistogramBasedGraphCut,
            )

            infers.update(
                {
                    "Histogram+GraphCut": HistogramBasedGraphCut(
                        intensity_range=(-300, 200, 0.0, 1.0, True),
                        pix_dim=(2.5, 2.5, 5.0),
                        lamda=1.0,
                        sigma=0.1,
                        num_bins=64,
                        labels=labels,
                    ),
                    "GMM+GraphCut": GMMBasedGraphCut(
                        intensity_range=(-300, 200, 0.0, 1.0, True),
                        pix_dim=(2.5, 2.5, 5.0),
                        lamda=5.0,
                        sigma=0.5,
                        num_mixtures=20,
                        labels=labels,
                    ),
                }
            )
        else:
            logger.warning("Scribble tasks were requested, but no model exposes labels")

    if infers.get("deepgrow_2d") and infers.get("deepgrow_3d"):
        from lib.infers.deepgrow_pipeline import InferDeepgrowPipeline

        infers["deepgrow_pipeline"] = InferDeepgrowPipeline(
            path=models["deepgrow_2d"].path,
            network=models["deepgrow_2d"].network,
            model_3d=infers["deepgrow_3d"],
            description="Combines Clara Deepgrow 2D and 3D models",
        )

    logger.info("Inference tasks: %s", list(infers))
    return infers


def build_trainers(
    *,
    models: Mapping[str, Any],
    bundles: Mapping[str, Any] | None,
    conf: Mapping[str, str],
) -> dict[str, Any]:
    trainers: dict[str, Any] = {}
    if parse_bool(conf.get("skip_trainers"), default=False):
        return trainers

    for name, task_config in models.items():
        task = task_config.trainer()
        if task:
            logger.info("+++ Adding Trainer:: %s => %s", name, task)
            trainers[name] = task

    if bundles:
        from monailabel.tasks.train.bundle import BundleTrainTask

        for name, bundle in bundles.items():
            task = BundleTrainTask(bundle, conf)
            if task and task.is_valid():
                logger.info("+++ Adding Bundle Trainer:: %s => %s", name, task)
                trainers[name] = task
    return trainers


def build_strategies(*, models: Mapping[str, Any], conf: Mapping[str, str]) -> dict[str, Any]:
    from lib.activelearning import Last
    from monailabel.tasks.activelearning.first import First
    from monailabel.tasks.activelearning.random import Random

    strategies: dict[str, Any] = {
        "random": Random(),
        "first": First(),
        "last": Last(),
    }
    if parse_bool(conf.get("skip_strategies"), default=True):
        return strategies

    for name, task_config in models.items():
        for task_name, task in normalize_tasks(name, task_config.strategy()).items():
            strategies[task_name] = task
    logger.info("Active learning strategies: %s", list(strategies))
    return strategies


def build_scoring_methods(
    *, models: Mapping[str, Any], conf: Mapping[str, str]
) -> dict[str, Any]:
    methods: dict[str, Any] = {}
    if parse_bool(conf.get("skip_scoring"), default=True):
        return methods

    for name, task_config in models.items():
        for task_name, task in normalize_tasks(
            name, task_config.scoring_method()
        ).items():
            methods[task_name] = task
    logger.info("Active learning scoring methods: %s", list(methods))
    return methods
