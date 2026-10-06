"""Blend: a voting ensemble of the tuned models.

Soft voting for classifiers when every member gives probabilities, hard voting
otherwise; a ``VotingRegressor`` for regressors. Each member is a whole
pipeline, so members may preprocess differently.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .estimators import CLASSIFICATION


def blend(task: str, members: Sequence[tuple[str, Any]], *, n_jobs: int = 1) -> Any:
    """An unfitted voting ensemble of ``(name, unfitted pipeline)`` members."""
    from sklearn.base import clone
    from sklearn.ensemble import VotingClassifier, VotingRegressor

    if len(members) < 2:
        raise ValueError("a blend needs at least two members")
    estimators = [(name, clone(model)) for name, model in members]
    if task == CLASSIFICATION:
        soft = all(hasattr(model, "predict_proba") for _, model in estimators)
        return VotingClassifier(estimators, voting="soft" if soft else "hard", n_jobs=n_jobs)
    return VotingRegressor(estimators, n_jobs=n_jobs)


__all__ = ["blend"]
