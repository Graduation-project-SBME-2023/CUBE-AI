"""Locating the evaluation set that feature selection deliberately withheld.

Kept dependency-light (stdlib only) so the rule can be unit-tested without PyCaret,
sklearn or a fitted model, and so both the classification and regression tools share
one definition instead of drifting apart.
"""

from __future__ import annotations

import os
from typing import Optional

_TRAIN_SUFFIX = "_train"
_HOLDOUT_SUFFIX = "_holdout"


def resolve_selection_holdout(input_path: str, holdout_path: Optional[str] = None) -> Optional[str]:
    """Return the holdout CSV for ``input_path``, or None when there isn't one.

    Feature selection writes ``<name>_train.csv`` / ``<name>_holdout.csv`` beside its
    top-N export whenever it managed an honest split. Letting PyCaret re-split at
    random would put rows whose labels shaped the feature set back into the test half,
    which is exactly the leak the split exists to prevent.

    An explicit ``holdout_path`` wins when it exists; otherwise the sibling file is
    used when present. A missing file returns None so the caller can fall back and say
    so, rather than failing a training run over a convention.
    """
    if holdout_path:
        return holdout_path if os.path.exists(holdout_path) else None
    if not input_path:
        return None

    root, ext = os.path.splitext(str(input_path))
    if root.endswith(_TRAIN_SUFFIX):
        root = root[: -len(_TRAIN_SUFFIX)]
    if root.endswith(_HOLDOUT_SUFFIX):
        # Already the holdout file; there is no holdout-of-a-holdout.
        return None

    candidate = f"{root}{_HOLDOUT_SUFFIX}{ext or '.csv'}"
    return candidate if os.path.exists(candidate) else None
