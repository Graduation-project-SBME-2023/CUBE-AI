from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Dict

import monailabel
from monailabel.interfaces.app import MONAILabelApp
from monailabel.interfaces.config import TaskConfig
from monailabel.interfaces.datastore import Datastore
from monailabel.interfaces.tasks.infer_v2 import InferTask
from monailabel.interfaces.tasks.scoring import ScoringMethod
from monailabel.interfaces.tasks.strategy import Strategy
from monailabel.interfaces.tasks.train import TrainTask
from monailabel.utils.others.generic import get_bundle_models
from monailabel.utils.others.planner import HeuristicPlanner
from storage_paths import (
    MODELS_DIR,
    RADIOLOGY_STATE_DIR,
    ensure_storage_layout,
)

from radiology_app.application.registry import DEFAULT_MODEL_REGISTRY, ModelRegistry
from radiology_app.application.settings import RadiologySettings
from radiology_app.application.task_assembly import (
    build_infers,
    build_scoring_methods,
    build_strategies,
    build_trainers,
    normalize_tasks,
)
from radiology_app.datastore.factory import create_dicomweb_datastore
from radiology_app.workflows import DynamicModelSpec, WorkflowRepository
from radiology_app.workflows.repository import WorkflowStoreError

logger = logging.getLogger(__name__)


class RadiologyApp(MONAILabelApp):
    """CUBE.AI radiology application with explicit lifecycle boundaries."""

    def __init__(self, app_dir: str, studies: str, conf: Dict[str, str]):
        ensure_storage_layout()
        self.registry: ModelRegistry = DEFAULT_MODEL_REGISTRY
        self.settings = RadiologySettings.from_conf(
            conf,
            available_models=self.registry.available_names,
        )
        self.model_dir = os.getenv("MODELS_DIR", str(MODELS_DIR))
        self.heuristic_planner = self.settings.heuristic_planner
        self.planner = HeuristicPlanner(
            spatial_size=self.settings.spatial_size,
            target_spacing=self.settings.target_spacing,
        )
        self.scribbles = self.settings.scribbles
        self.models: Dict[str, TaskConfig] = {}
        self._task_lock = threading.RLock()
        self._dynamic_infer_names: dict[str, set[str]] = {}

        for name in self.registry.startup_names(self.settings.requested_models):
            model_config = self.registry.create_startup_config(name)
            logger.info("+++ Adding Model: %s => %s", name, type(model_config))
            model_config.init(name, self.model_dir, conf, self.planner)
            self.models[name] = model_config
        logger.info("+++ Using Models: %s", list(self.models))

        self.bundles = (
            get_bundle_models(app_dir, conf, conf_key="bundles")
            if conf.get("bundles")
            else None
        )
        self._workflow_repository = WorkflowRepository(
            RADIOLOGY_STATE_DIR / "workflows.json",
            legacy_paths=(
                Path(app_dir) / "workflows_data.json",
                Path.cwd() / "workflows_data.json",
            ),
        )

        super().__init__(
            app_dir=app_dir,
            studies=studies,
            conf=conf,
            name=f"MONAILabel - Radiology ({monailabel.__version__})",
            description="Deep-learning models for radiology",
            version=monailabel.__version__,
        )
        self._restore_dynamic_models()

    def _restore_dynamic_models(self) -> None:
        try:
            specs = self._workflow_repository.load()
        except WorkflowStoreError:
            logger.exception("Dynamic radiology workflow state could not be loaded")
            return

        for spec in specs:
            try:
                self._install_dynamic_model(spec, persist=False)
                logger.info("+++ Restored Workflow: %s", spec.model_name)
            except Exception:
                # One stale workflow must not make every static model unavailable.
                logger.exception(
                    "Could not restore dynamic radiology model %s",
                    spec.model_name,
                )

    def create_task(self, model_name, labels, fold, plan_type):
        """Create and persist a dynamic nnUNet workflow."""
        spec = DynamicModelSpec.create(
            model_name=model_name,
            labels=labels,
            fold=fold,
            plan_type=plan_type,
        )
        if spec.model_name.lower() in self.registry.available_names:
            raise ValueError(
                f"Dynamic model name conflicts with a built-in model: {spec.model_name}"
            )
        self._install_dynamic_model(spec, persist=True)
        return f"Successfully registered model {spec.model_name}"

    def _install_dynamic_model(
        self,
        spec: DynamicModelSpec,
        *,
        persist: bool,
    ) -> None:
        model_config = self.registry.create_dynamic_config(
            labels=spec.labels,
            model_name=spec.model_name,
            fold=spec.fold,
            plan_type=spec.plan_type,
        )
        model_config.init(
            name=spec.model_name,
            model_dir=self.model_dir,
            conf=self.conf,
            planner=self.planner,
        )

        trainer = model_config.trainer()
        infers = normalize_tasks(spec.model_name, model_config.infer())
        if not infers:
            raise RuntimeError(
                f"Dynamic model {spec.model_name} did not create an inference task"
            )

        with self._task_lock:
            if persist:
                self._workflow_repository.upsert(spec)

            for old_name in self._dynamic_infer_names.get(spec.model_name, set()):
                self._infers.pop(old_name, None)
            self.models[spec.model_name] = model_config
            if trainer is None:
                self._trainers.pop(spec.model_name, None)
            else:
                self._trainers[spec.model_name] = trainer
            self._infers.update(infers)
            self._dynamic_infer_names[spec.model_name] = set(infers)

        logger.info("Successfully registered dynamic model %s", spec.model_name)

    def initialize_inference_task(self, model_name: str) -> str:
        """Refresh inference tasks after a dynamic model finishes training."""
        try:
            model_config = self.models[model_name]
        except KeyError as exc:
            raise ValueError(f"Unknown model: {model_name}") from exc

        infers = normalize_tasks(model_name, model_config.infer())
        if not infers:
            raise RuntimeError(f"Model {model_name} did not create an inference task")
        with self._task_lock:
            for old_name in self._dynamic_infer_names.get(model_name, set()):
                self._infers.pop(old_name, None)
            self._infers.update(infers)
            self._dynamic_infer_names[model_name] = set(infers)
        logger.info("Refreshed inference tasks for %s: %s", model_name, list(infers))
        return f"Successfully initialized inference task for model {model_name}"

    def _init_dicomweb_datastore(self) -> Datastore:
        return create_dicomweb_datastore(self)

    def init_datastore(self) -> Datastore:
        datastore = super().init_datastore()
        if self.heuristic_planner:
            self.planner.run(datastore)
        return datastore

    def init_infers(self) -> Dict[str, InferTask]:
        return build_infers(
            models=self.models,
            bundles=self.bundles,
            conf=self.conf,
            scribbles=self.scribbles,
        )

    def init_trainers(self) -> Dict[str, TrainTask]:
        return build_trainers(
            models=self.models,
            bundles=self.bundles,
            conf=self.conf,
        )

    def init_strategies(self) -> Dict[str, Strategy]:
        return build_strategies(models=self.models, conf=self.conf)

    def init_scoring_methods(self) -> Dict[str, ScoringMethod]:
        return build_scoring_methods(models=self.models, conf=self.conf)
