import hashlib
import os
import pickle
from pathlib import Path
from storage_paths import MODELS_DIR

from fastapi import APIRouter


router = APIRouter(prefix="/voxtell", tags=["VoxTell"])


def _cache_path() -> Path:
    model_dir = Path(os.environ.get("VOXTELL_DIR", str(MODELS_DIR / "voxtell_v1.1")))
    return model_dir / "text_embeddings_cache.pkl"


@router.get("/cache-prompts")
def read_voxtell_cache_inventory():
    """Return an exact, read-only inventory of VoxTell embedding-cache keys."""
    cache_path = _cache_path()
    if not cache_path.is_file():
        return {
            "available": False,
            "keys": [],
            "version": None,
            "message": f"VoxTell cache not found at {cache_path}",
        }

    try:
        with cache_path.open("rb") as stream:
            cache = pickle.load(stream)
        keys = sorted(
            {
                str(key).strip()
                for key in cache.keys()
                if str(key).strip()
            },
            key=str.casefold,
        )
        stat = cache_path.stat()
        fingerprint_payload = "\n".join(keys).encode("utf-8")
        version = hashlib.sha256(fingerprint_payload).hexdigest()
        return {
            "available": True,
            "keys": keys,
            "version": version,
            "modified_at_ns": stat.st_mtime_ns,
        }
    except Exception as exc:
        return {
            "available": False,
            "keys": [],
            "version": None,
            "message": f"Unable to inspect VoxTell cache: {exc}",
        }
