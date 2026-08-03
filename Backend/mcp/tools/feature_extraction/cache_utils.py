"""
cache_utils.py — Extraction result caching helpers.

# ADDED FOR UI ENHANCEMENT — Task 4: Caching Layer
Provides a simple file-based cache keyed by image/mask modification times and
the active bin_width. Every cache operation is wrapped in try/except so that
a missing cache directory or permission error NEVER blocks the extraction pipeline.
"""

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def get_cache_key(image_path: str, mask_path: str, bin_width: float) -> str:
    """
    Compute a deterministic SHA-256 cache key from:
      - Image file modification time
      - Mask file modification time
      - Active bin_width (6 decimal places)

    Returns an empty string on any failure so callers can safely skip caching.
    """
    try:
        img_mtime  = str(Path(image_path).stat().st_mtime)
        mask_mtime = str(Path(mask_path).stat().st_mtime)
        raw = f"{image_path}:{img_mtime}|{mask_path}:{mask_mtime}|bw={bin_width:.6f}"
        return hashlib.sha256(raw.encode()).hexdigest()
    except Exception as exc:
        logger.debug(f"[cache] Key computation failed: {exc}")
        return ""


def load_from_cache(cache_dir: str, cache_key: str) -> Optional[dict]:
    """
    Return the cached feature dict for cache_key if it exists; None otherwise.

    Safety: returns None on any I/O or JSON error — caller falls through to
    full extraction automatically.
    """
    if not cache_key:
        return None
    try:
        cache_file = os.path.join(cache_dir, f"{cache_key}.json")
        if os.path.isfile(cache_file):
            with open(cache_file, "r", encoding="utf-8") as fh:
                return json.load(fh)
    except Exception as exc:
        logger.debug(f"[cache] Load failed for key={cache_key[:8]}…: {exc}")
    return None


def save_to_cache(cache_dir: str, cache_key: str, features: dict) -> None:
    """
    Persist the feature dict to {cache_dir}/{cache_key}.json.

    Safety: silently skips on any I/O or serialisation error. The extraction
    result is never lost — only the cache write is abandoned.
    """
    if not cache_key:
        return
    try:
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir, f"{cache_key}.json")
        with open(cache_file, "w", encoding="utf-8") as fh:
            json.dump(features, fh, default=str)
        logger.debug(f"[cache] Saved key={cache_key[:8]}… → {cache_file}")
    except Exception as exc:
        logger.debug(f"[cache] Save failed for key={cache_key[:8]}…: {exc}")
