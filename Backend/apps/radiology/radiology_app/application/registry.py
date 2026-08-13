from __future__ import annotations

import importlib
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


_MODEL_CLASS_PATHS = MappingProxyType(
    {
        "deepedit": "lib.configs.deepedit.DeepEdit",
        "deepgrow_2d": "lib.configs.deepgrow_2d.Deepgrow2D",
        "deepgrow_3d": "lib.configs.deepgrow_3d.Deepgrow3D",
        "generic_segmentation": "lib.configs.generic_segmentation.GenericSegmentation",
        "localization_vertebra": "lib.configs.localization_vertebra.LocalizationVertebra",
        "nninteractive": "lib.configs.nnInteractive.NNINTERACTIVE",
        "sam": "lib.configs.sam.sam",
        "segmentation_spleen": "lib.configs.segmentation_spleen.SegmentationSpleen",
        "vista": "lib.configs.vista.VISTAPOINT3D",
        "voxtell": "lib.configs.voxtell.voxtell",
    }
)


@dataclass(frozen=True)
class ModelRegistry:
    """Explicit, auditable mapping of public model IDs to TaskConfig classes."""

    class_paths: Mapping[str, str] = _MODEL_CLASS_PATHS
    dynamic_model: str = "generic_segmentation"

    @property
    def available_names(self) -> tuple[str, ...]:
        return tuple(sorted(self.class_paths))

    def startup_names(self, requested: tuple[str, ...]) -> tuple[str, ...]:
        if "all" in requested:
            return tuple(
                name for name in self.available_names if name != self.dynamic_model
            )
        return tuple(name for name in requested if name != self.dynamic_model)

    def load_class(self, name: str):
        try:
            class_path = self.class_paths[name]
        except KeyError as exc:
            raise ValueError(f"Unknown radiology model: {name}") from exc

        module_name, class_name = class_path.rsplit(".", 1)
        module = importlib.import_module(module_name)
        model_class = getattr(module, class_name)

        from monailabel.interfaces.config import TaskConfig

        if not isinstance(model_class, type) or not issubclass(model_class, TaskConfig):
            raise TypeError(f"Registered model is not a TaskConfig: {class_path}")
        return model_class

    def create_startup_config(self, name: str):
        if name == self.dynamic_model:
            raise ValueError(
                f"{self.dynamic_model} requires workflow-specific constructor arguments"
            )
        return self.load_class(name)()

    def create_dynamic_config(self, *, labels, model_name: str, fold: int, plan_type: str):
        model_class = self.load_class(self.dynamic_model)
        return model_class(
            labels=labels,
            model_name=model_name,
            fold=fold,
            plan_type=plan_type,
        )


DEFAULT_MODEL_REGISTRY = ModelRegistry()
