"""Extraction result cache — disposable and content-addressed."""

import hashlib
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_KEY_VERSION = "radiomics-cache-v2"


def _is_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in "0123456789abcdef" for ch in value)
    )


def get_cache_key(image_digest: str, segmentation_digest: str, config_digest: str) -> str:
    """SHA-256 over the three content digests of one extraction."""
    parts = (image_digest, segmentation_digest, config_digest)
    if not all(_is_digest(part) for part in parts):
        return ""
    return hashlib.sha256("|".join((_KEY_VERSION, *parts)).encode("ascii")).hexdigest()


def load_from_cache(cache_dir: str, cache_key: str) -> Optional[dict]:
    """Return the cached feature dict for cache_key if it exists; None otherwise."""
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
    """Persist the feature dict to {cache_dir}/{cache_key}.json; never raises."""
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
