from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping, Sequence


def parse_bool(value: object, *, default: bool = False) -> bool:
    """Parse the string-style boolean values used by MONAI Label configs."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


def _parse_triplet(value: object, *, name: str, default: Sequence[float]) -> tuple:
    raw = default if value is None else json.loads(str(value))
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        raise ValueError(f"{name} must contain exactly three values")
    try:
        return tuple(float(item) for item in raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain numeric values") from exc


@dataclass(frozen=True)
class RadiologySettings:
    """Validated application settings derived from MONAI Label ``--conf``."""

    requested_models: tuple[str, ...]
    scribbles: bool
    heuristic_planner: bool
    spatial_size: tuple[float, float, float]
    target_spacing: tuple[float, float, float]

    @classmethod
    def from_conf(
        cls,
        conf: Mapping[str, object],
        *,
        available_models: Sequence[str],
    ) -> "RadiologySettings":
        models_value = str(conf.get("models", "")).strip()
        if not models_value:
            available = ", ".join(available_models)
            raise ValueError(
                "No radiology models were configured. "
                f"Set --conf models <name>; available values: all, {available}"
            )

        requested = tuple(
            dict.fromkeys(name.strip().lower() for name in models_value.split(",") if name.strip())
        )
        if not requested:
            raise ValueError("The radiology model list is empty")

        invalid = sorted(
            name for name in requested if name != "all" and name not in available_models
        )
        if invalid:
            available = ", ".join(available_models)
            raise ValueError(
                f"Unknown radiology model(s): {', '.join(invalid)}. "
                f"Available values: all, {available}"
            )

        return cls(
            requested_models=requested,
            scribbles=parse_bool(conf.get("scribbles"), default=True),
            heuristic_planner=parse_bool(
                conf.get("heuristic_planner"), default=False
            ),
            spatial_size=_parse_triplet(
                conf.get("spatial_size"),
                name="spatial_size",
                default=(48, 48, 32),
            ),
            target_spacing=_parse_triplet(
                conf.get("target_spacing"),
                name="target_spacing",
                default=(1.0, 1.0, 1.0),
            ),
        )
