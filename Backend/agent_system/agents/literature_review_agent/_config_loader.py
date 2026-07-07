"""
_config_loader.py
─────────────────
Loads literature_review_config.json once at import time and exposes typed
accessors.  All values fall back to safe hardcoded defaults when the file is
absent or malformed, so existing callers are never broken.

Public API (all read-only):
    LIT_CFG            — raw parsed config dict (top-level keys)
    get_max_results()  — int  — default max results per search tool call
    get_year_from()    — int | None
    get_year_to()      — int | None
    get_min_quality_score() — float — minimum paper quality score (0-100)
    get_min_connection_weight() — float
    get_active_databases()  — List[str] — databases that actually have tools
    get_peer_reviewed_only() — bool
    get_impact_factor_min() — float
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Config file location (same directory as this file) ───────────────────────
_CONFIG_FILE = Path(__file__).parent / "literature_review_config.json"

# ── Databases that have actual tool implementations ───────────────────────────
# (google_scholar and sciencedirect listed in config but not implemented)
_IMPLEMENTED_DATABASES = {"pubmed", "arxiv", "semantic_scholar", "europe_pmc"}

# ── Hardcoded fallbacks (match what was previously inlined in agent code) ─────
_DEFAULTS: Dict[str, Any] = {
    "max_results"              : 8,
    "year_from"                : None,   # no date filter by default
    "year_to"                  : None,
    "minimum_study_quality_score": 0.0,
    "min_connection_weight"    : 0.3,
    "peer_reviewed_only"       : False,
    "impact_factor_min"        : 0.0,
    "databases"                : list(_IMPLEMENTED_DATABASES),
}


def _load_config() -> Dict[str, Any]:
    """Load and flatten the config JSON.  Returns defaults on any error."""
    if not _CONFIG_FILE.exists():
        logger.warning(
            f"[ConfigLoader] {_CONFIG_FILE.name} not found — using defaults."
        )
        return _DEFAULTS.copy()

    try:
        raw = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"[ConfigLoader] Failed to parse config: {exc} — using defaults.")
        return _DEFAULTS.copy()

    cfg = raw.get("literature_review_config", raw)
    search   = cfg.get("search_settings", {})
    quality  = cfg.get("quality_assessment", {})
    graph    = cfg.get("graph_settings", {})

    loaded: Dict[str, Any] = {
        "max_results"              : int(search.get("max_results", _DEFAULTS["max_results"])),
        "year_from"                : search.get("year_from"),
        "year_to"                  : search.get("year_to"),
        "minimum_study_quality_score": float(
            quality.get("minimum_study_quality_score",
                        _DEFAULTS["minimum_study_quality_score"])
        ),
        "min_connection_weight"    : float(
            graph.get("min_connection_weight",
                      _DEFAULTS["min_connection_weight"])
        ),
        "peer_reviewed_only"       : bool(search.get("peer_reviewed_only",
                                                       _DEFAULTS["peer_reviewed_only"])),
        "impact_factor_min"        : float(search.get("impact_factor_min",
                                                        _DEFAULTS["impact_factor_min"])),
        # Only keep databases that have actual search tool implementations
        "databases": [
            db for db in search.get("databases", _DEFAULTS["databases"])
            if db in _IMPLEMENTED_DATABASES
        ] or list(_IMPLEMENTED_DATABASES),
    }

    logger.info(
        f"[ConfigLoader] Loaded from {_CONFIG_FILE.name}: "
        f"max_results={loaded['max_results']}, "
        f"year_from={loaded['year_from']}, "
        f"databases={loaded['databases']}"
    )
    return loaded


# ── Module-level singleton (loaded once at import time) ───────────────────────
LIT_CFG: Dict[str, Any] = _load_config()


# ── Typed accessor functions ──────────────────────────────────────────────────

def get_max_results() -> int:
    """Default max_results per search tool call (from config or fallback 8)."""
    return int(LIT_CFG.get("max_results", _DEFAULTS["max_results"]))


def get_year_from() -> Optional[int]:
    """Earliest publication year to include, or None for no filter."""
    v = LIT_CFG.get("year_from")
    return int(v) if v is not None else None


def get_year_to() -> Optional[int]:
    """Latest publication year to include, or None for no filter."""
    v = LIT_CFG.get("year_to")
    return int(v) if v is not None else None


def get_min_quality_score() -> float:
    """Minimum relevance score threshold (0–100) for paper inclusion."""
    return float(LIT_CFG.get("minimum_study_quality_score",
                              _DEFAULTS["minimum_study_quality_score"]))


def get_min_connection_weight() -> float:
    """Minimum connection weight for knowledge graph edges."""
    return float(LIT_CFG.get("min_connection_weight",
                              _DEFAULTS["min_connection_weight"]))


def get_active_databases() -> List[str]:
    """Databases that have tool implementations and are enabled in config."""
    return list(LIT_CFG.get("databases", _DEFAULTS["databases"]))


def get_peer_reviewed_only() -> bool:
    """Whether to restrict to peer-reviewed journals (advisory — not enforced at API level)."""
    return bool(LIT_CFG.get("peer_reviewed_only", _DEFAULTS["peer_reviewed_only"]))


def get_impact_factor_min() -> float:
    """Minimum journal impact factor (advisory — not enforced at API level)."""
    return float(LIT_CFG.get("impact_factor_min", _DEFAULTS["impact_factor_min"]))
