"""The manifests of the modeling tools.

All five stay ``INTERIM_HEAVY`` (inline) until the job platform (Plan 5),
but each states its own time budget and what it touches: dispatch enforces
``timeout_s``, and the 120 s default would end any real training.
"""

from __future__ import annotations

from typing import Any

from ..common.manifests import INTERIM_HEAVY

# Compare, tune and blend on a cohort's whole feature set.
TRAINING: dict[str, Any] = {
    **INTERIM_HEAVY,
    "timeout_s": 2 * 3600,
    "resources": {"cpu_heavy"},
    "data_scopes": {"phi_read", "db_write", "fs_write"},
}
# Load one recorded model and predict a CSV.
INFERENCE: dict[str, Any] = {
    **INTERIM_HEAVY,
    "timeout_s": 15 * 60,
    "resources": {"cpu_heavy"},
    "data_scopes": {"phi_read", "db_write"},
}
# Fit one recorded estimator again.
REFIT: dict[str, Any] = {
    **INTERIM_HEAVY,
    "timeout_s": 3600,
    "resources": {"cpu_heavy"},
    "data_scopes": {"phi_read", "db_write", "fs_write"},
}
