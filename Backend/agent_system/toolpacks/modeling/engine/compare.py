"""Compare: cross-validate each candidate, sort the table, keep the top ``n``."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from . import fitting, metrics
from .estimators import Estimator


@dataclass
class Comparison:
    rows: list[dict[str, Any]]  # best first: model_id, Model, the metrics, TT (Sec)
    failures: dict[str, str] = field(default_factory=dict)  # model_id -> why it failed

    def top(self, n: int) -> list[dict[str, Any]]:
        return self.rows[: max(0, int(n))]


def compare(
    task: str,
    candidates: Sequence[Estimator],
    X: pd.DataFrame,
    y: Any,
    *,
    cv: Any,
    make_pipeline: Callable[[Estimator], Any],
    balance: bool = False,
    n_classes: int = 0,
    on_candidate: Callable[[int, Estimator], None] | None = None,
) -> Comparison:
    """``make_pipeline(entry)`` builds a fresh, unfitted pipeline of a
    candidate. A candidate that fails is left out of the table and named in
    ``failures``, as PyCaret skipped a model that raised."""
    rows: list[dict[str, Any]] = []
    failures: dict[str, str] = {}
    for index, entry in enumerate(candidates):
        if on_candidate is not None:
            on_candidate(index, entry)
        started = time.perf_counter()
        try:
            scores = fitting.cv_scores(
                task, make_pipeline(entry), X, y, cv, balance=balance, n_classes=n_classes
            )
        except Exception as exc:
            failures[entry.model_id] = f"{type(exc).__name__}: {exc}"[:500]
            continue
        rows.append({
            "model_id": entry.model_id,
            "Model": entry.name,
            **scores,
            "TT (Sec)": round(time.perf_counter() - started, 3),
        })
    rows.sort(key=metrics.sort_key(task))
    return Comparison(rows=rows, failures=failures)


__all__ = ["Comparison", "compare"]
