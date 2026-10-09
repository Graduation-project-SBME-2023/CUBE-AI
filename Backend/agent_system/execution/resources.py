"""Executor resource pools, read from ``config/resources.yaml``.

The pools are runtime only (D19). This module reads their names and sizes;
the Validator (Plan 8) refuses a target that declares a pool missing from the
file, and the Executor (Plan 10) sizes one semaphore per pool from it.
``CUBEAI_RESOURCES_FILE`` points at another file (tests, deployments).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

import yaml

DEFAULT_RESOURCES_FILE = Path(__file__).resolve().parent.parent / "config" / "resources.yaml"


class ResourceConfigError(ValueError):
    """``resources.yaml`` is missing or malformed."""


def resources_file() -> Path:
    return Path(os.environ.get("CUBEAI_RESOURCES_FILE") or DEFAULT_RESOURCES_FILE)


def load_pools(path: Path | None = None) -> Mapping[str, int]:
    """``{pool: size}`` from the file; read when called, never at import."""
    path = path or resources_file()
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ResourceConfigError(f"cannot read {path}: {exc}") from exc
    if not isinstance(document, dict) or document.get("v") != 1:
        raise ResourceConfigError(f"{path}: expected a mapping with v: 1")
    pools = document.get("pools")
    if not isinstance(pools, dict) or not pools:
        raise ResourceConfigError(f"{path}: 'pools' must be a non-empty mapping")
    for name, size in pools.items():
        if not isinstance(name, str) or isinstance(size, bool) or not isinstance(size, int) or size < 1:
            raise ResourceConfigError(f"{path}: pool {name!r} needs a size >= 1, not {size!r}")
    return MappingProxyType(dict(pools))


def pool_names(path: Path | None = None) -> frozenset[str]:
    return frozenset(load_pools(path))
