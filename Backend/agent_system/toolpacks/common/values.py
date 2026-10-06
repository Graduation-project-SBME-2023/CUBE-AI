"""Value conversions the packs share: JSON-safe results and list arguments.

Models often send a list argument as one comma-separated string ("10,20"),
so the list helpers accept both forms.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


def json_safe(value: Any) -> Any:
    """JSON-native, finite values: numpy scalars become Python numbers, NaN
    and infinities become ``None``, tuples and sets become lists, and anything
    else becomes its string form."""
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(item) for item in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if hasattr(value, "item") and not isinstance(value, (bytes, bytearray)):
        try:
            value = value.item()
        except Exception:
            return str(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return str(value)


def finite_float(value: Any, digits: int = 6) -> float | None:
    """``value`` as a rounded float, or ``None`` when it is not a finite number."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits) if math.isfinite(number) else None


def str_list(value: str | list[Any] | None) -> list[str] | None:
    """A list of non-empty strings from a list or a comma-separated string."""
    if value is None:
        return None
    items = value.split(",") if isinstance(value, str) else value
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    return cleaned or None


def int_list(value: int | str | list[Any] | None) -> list[int] | None:
    if isinstance(value, bool):
        raise ValueError(f"Expected integers, got: {value!r}")
    if isinstance(value, int):
        return [value]
    items = str_list(value)
    if items is None:
        return None
    try:
        return [int(item) for item in items]
    except ValueError as exc:
        raise ValueError(f"Expected comma-separated integers, got: {value!r}") from exc


def float_list(value: float | int | str | list[Any] | None) -> list[float] | None:
    if isinstance(value, bool):
        raise ValueError(f"Expected numbers, got: {value!r}")
    if isinstance(value, (int, float)):
        return [float(value)]
    items = str_list(value)
    if items is None:
        return None
    try:
        return [float(item) for item in items]
    except ValueError as exc:
        raise ValueError(f"Expected comma-separated numbers, got: {value!r}") from exc


__all__ = ["finite_float", "float_list", "int_list", "json_safe", "str_list"]
