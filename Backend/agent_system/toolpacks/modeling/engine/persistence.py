"""The saved model: a joblib bundle, written atomically.

The bundle is a plain dict around the fitted scikit-learn ``Pipeline``, so it
holds no class of ours and loads under either import root of the packs:

    {"engine": "sklearn", "format": 1, "task": ..., "pipeline": <Pipeline>,
     "features": [input columns, in order], "classes": [labels] | None}

A classifier's pipeline predicts label codes ``0 … k-1``; ``classes`` maps them
back to the labels. A model saved by PyCaret is not a bundle and is refused
(``LegacyModelError``); ``refit_model`` rebuilds it from its record.
"""

from __future__ import annotations

import os
import platform
import tempfile
from collections.abc import Mapping, Sequence
from typing import Any

ENGINE = "sklearn"
BUNDLE_FORMAT = 1
_PACKAGES = ("sklearn", "numpy", "pandas", "scipy", "joblib", "lightgbm", "xgboost", "shap")


class LegacyModelError(ValueError):
    """The file is not a model bundle of this engine (e.g. a PyCaret pipeline)."""


def make_bundle(
    task: str, pipeline: Any, features: Sequence[str], classes: Sequence[Any] | None
) -> dict[str, Any]:
    return {
        "engine": ENGINE,
        "format": BUNDLE_FORMAT,
        "task": task,
        "pipeline": pipeline,
        "features": [str(name) for name in features],
        "classes": list(classes) if classes is not None else None,
    }


def save_bundle(bundle: Mapping[str, Any], path: str | os.PathLike[str]) -> str:
    """``joblib.dump`` to a temp file beside ``path``, then an atomic rename,
    so a reader never sees half a file. Returns the final path."""
    import joblib

    target = os.fspath(path)
    directory = os.path.dirname(target) or "."
    os.makedirs(directory, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=directory, prefix=".tmp_", suffix=".pkl")
    os.close(handle)
    try:
        joblib.dump(dict(bundle), temp)
        os.replace(temp, target)
    except BaseException:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass
        raise
    return target


def load_bundle(path: str | os.PathLike[str]) -> dict[str, Any]:
    """The bundle at ``path``; ``LegacyModelError`` for anything else."""
    import joblib

    try:
        bundle = joblib.load(os.fspath(path))
    except ModuleNotFoundError as exc:
        # A PyCaret pipeline unpickles only with PyCaret installed.
        raise LegacyModelError(f"the model file needs a package that is not installed ({exc.name})") from exc
    if not isinstance(bundle, dict) or bundle.get("engine") != ENGINE or "pipeline" not in bundle:
        raise LegacyModelError("the model file is not a scikit-learn engine bundle")
    return bundle


def environment() -> dict[str, Any]:
    """``model_results.environment``: the engine and the library versions."""
    versions: dict[str, str | None] = {}
    for module in _PACKAGES:
        try:
            imported = __import__(module)
            versions[module] = str(getattr(imported, "__version__", "unknown"))
        except Exception:
            versions[module] = None
    return {
        "engine": ENGINE,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": versions,
    }


def is_engine_record(record: Mapping[str, Any] | None) -> bool:
    """Whether a recorded model result was trained by this engine."""
    return ((record or {}).get("environment") or {}).get("engine") == ENGINE


__all__ = [
    "BUNDLE_FORMAT",
    "ENGINE",
    "LegacyModelError",
    "environment",
    "is_engine_record",
    "load_bundle",
    "make_bundle",
    "save_bundle",
]
