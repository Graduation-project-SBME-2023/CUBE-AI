"""Bounded, finite JSONB documents"""

from __future__ import annotations

import json
import math
from typing import Any

KIB = 1024
MIB = 1024 * KIB

# agent_runs.durable_notes / warnings / output_summary / error / input_refs
AGENT_RUN_JSON_MAX_BYTES = 2 * MIB
# agent_runs.compiled_prompt (TEXT, but bounded for the same reason)
COMPILED_PROMPT_MAX_BYTES = 1 * MIB
# ws_log.payload — tool events can carry chart payloads.
WS_LOG_PAYLOAD_MAX_BYTES = 4 * MIB
# Default for smaller domain documents (criteria, specifications, metrics…).
DOMAIN_JSON_MAX_BYTES = 1 * MIB
# Per-member clinical evidence.
CLINICAL_DATA_MAX_BYTES = 256 * KIB
# feature_sets.feature_matrix.
FEATURE_MATRIX_MAX_BYTES = 16 * MIB

DEFAULT_MAX_DEPTH = 32


class JsonBoundsError(ValueError):
    """A JSON document is too large, too deep, or not finite."""


def _walk(value: Any, depth: int, max_depth: int, path: str) -> None:
    if depth > max_depth:
        raise JsonBoundsError(f"JSON nesting deeper than {max_depth} at {path or '$'}")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise JsonBoundsError(
                f"non-finite number at {path or '$'}; record an explicit missing "
                "reason instead of NaN/Infinity"
            )
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise JsonBoundsError(f"non-string object key at {path or '$'}")
            _walk(item, depth + 1, max_depth, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _walk(item, depth + 1, max_depth, f"{path}[{index}]")


def encoded_size(value: Any) -> int:
    """UTF-8 size of the canonical JSON encoding (an upper bound on JSONB)."""
    return len(
        json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode(
            "utf-8"
        )
    )


def check_json(
    value: Any,
    *,
    max_bytes: int,
    max_depth: int = DEFAULT_MAX_DEPTH,
    what: str = "document",
) -> Any:
    """Validate and return ``value`` unchanged, or raise :class:`JsonBoundsError`."""
    if value is None:
        return value
    _walk(value, 0, max_depth, "")
    try:
        size = encoded_size(value)
    except (TypeError, ValueError) as exc:
        raise JsonBoundsError(f"{what} is not JSON-serialisable: {exc}") from exc
    if size > max_bytes:
        raise JsonBoundsError(f"{what} is {size} bytes; the bound is {max_bytes} bytes")
    return value


def check_versioned_document(
    value: Any,
    *,
    max_bytes: int,
    what: str = "document",
) -> Any:
    """A JSONB object that must declare its own ``schema_version``."""
    if value is None:
        return value
    if not isinstance(value, dict):
        raise JsonBoundsError(f"{what} must be a JSON object")
    version = value.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise JsonBoundsError(f"{what} must carry an integer schema_version >= 1")
    return check_json(value, max_bytes=max_bytes, what=what)
