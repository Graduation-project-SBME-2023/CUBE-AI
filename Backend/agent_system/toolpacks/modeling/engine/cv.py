"""Cross-validation folds and the random evaluation split.

The selection-aware holdout (``holdout.py``, the feature set's own withheld
rows) is used unchanged when there is one; ``random_split`` is only the
fallback when there is none.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from .estimators import CLASSIFICATION

# Rows kept for training by the random split (PyCaret's ``train_size``).
SMALL_DATASET_ROWS = 50
SMALL_DATASET_TRAIN_SIZE = 0.8
TRAIN_SIZE = 0.7


def train_size(n_rows: int) -> float:
    return SMALL_DATASET_TRAIN_SIZE if n_rows < SMALL_DATASET_ROWS else TRAIN_SIZE


def clamp_folds(task: str, requested: int, y: pd.Series) -> int:
    """The requested fold count, clamped so every fold is valid: for a
    classifier, no more folds than the rarest class has training rows; for a
    regressor, no more folds than training rows. Below 2 there is no
    cross-validation at all, which the caller must report."""
    folds = max(2, int(requested))
    if task == CLASSIFICATION:
        counts = y.value_counts()
        return min(folds, int(counts.min())) if len(counts) else 0
    return min(folds, int(len(y)))


def splitter(task: str, folds: int, seed: int, *, shuffle: bool = True, stratify: bool = True) -> Any:
    """``StratifiedKFold`` for a classifier (unless ``stratify`` is off),
    ``KFold`` otherwise; seeded whenever it shuffles."""
    from sklearn.model_selection import KFold, StratifiedKFold

    seed_arg = int(seed) if shuffle else None
    if task == CLASSIFICATION and stratify:
        return StratifiedKFold(n_splits=folds, shuffle=shuffle, random_state=seed_arg)
    return KFold(n_splits=folds, shuffle=shuffle, random_state=seed_arg)


def random_split(
    task: str,
    frame: pd.DataFrame,
    *,
    target: str,
    seed: int,
    stratify: bool = True,
    shuffle: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, float]:
    """Train and evaluation rows when no rows were withheld. Stratified for a
    classifier when every class can be split; returns the train size used."""
    from sklearn.model_selection import train_test_split

    size = train_size(len(frame))
    stratify_on = None
    if task == CLASSIFICATION and stratify and shuffle:
        counts = frame[target].value_counts()
        n_test = len(frame) - int(len(frame) * size)
        if counts.min() >= 2 and n_test >= len(counts):
            stratify_on = frame[target]
    train, holdout = train_test_split(
        frame,
        train_size=size,
        random_state=int(seed) if shuffle else None,
        shuffle=shuffle,
        stratify=stratify_on,
    )
    return train, holdout, size


__all__ = ["clamp_folds", "random_split", "splitter", "train_size"]
