"""Deterministic train/holdout planning for supervised feature selection.

Feature importance is fitted with the labels, so fitting it on every row leaks the
holdout labels into the selected feature set: any metric later measured on those rows
is optimistic by an unknown amount, and nothing downstream can detect it.

This module plans the split *before* selection runs. It is deliberately dependency-light
(stdlib + numpy) so the decision is unit-testable without sklearn, PyCaret or a fitted
model, and so the same plan can be replayed from its recorded seed.

Two properties matter more than the split itself:

* **Grouping.** Rows belonging to one patient must not straddle the split, or selection
  still sees a near-duplicate of every holdout row.
* **Honesty when it cannot be done.** A cohort too small to hold anything out does not
  silently fall back to leaking. It degrades to ``label_supervised_on_full_data=True``
  with a reason, so the caller can record that the selection was not blind and the
  report can say so.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Sequence

# A cohort smaller than this cannot give up rows without making selection meaningless.
MIN_GROUPS_FOR_HOLDOUT = 10
# Training must keep at least this many groups after the holdout is taken.
MIN_TRAIN_GROUPS = 8

# Columns that identify a subject rather than describe one.
_SUBJECT_COLUMN_CANDIDATES = (
    "subject_id",
    "subjectid",
    "subject_key",
    "patient_id",
    "patientid",
    "pat_id",
    "case_id",
    "caseid",
)


@dataclass
class SelectionSplit:
    """The plan, plus everything needed to replay or audit it."""

    train_index: List[int]
    holdout_index: List[int]
    strategy: str
    group_column: Optional[str]
    n_groups: int
    holdout_size: float
    seed: int
    label_supervised_on_full_data: bool
    degraded_reason: Optional[str] = None
    holdout_groups: List[str] = field(default_factory=list)

    @property
    def has_holdout(self) -> bool:
        return bool(self.holdout_index)

    def to_manifest(self) -> Dict[str, Any]:
        """JSON-safe record for `feature_sets.selection` and the split artifact."""
        payload = asdict(self)
        # Row indices are positional and meaningless once the CSV is re-ordered;
        # the group keys are the durable identity of the holdout.
        payload["train_row_count"] = len(self.train_index)
        payload["holdout_row_count"] = len(self.holdout_index)
        return payload


def detect_group_column(columns: Sequence[str]) -> Optional[str]:
    """Return the subject-identifying column, or None if the rows are ungrouped."""
    lowered = {str(c).lower(): str(c) for c in columns}
    for candidate in _SUBJECT_COLUMN_CANDIDATES:
        if candidate in lowered:
            return lowered[candidate]
    return None


def _group_rows(
    group_values: Optional[Sequence[Any]],
    n_rows: int,
) -> "Dict[str, List[int]]":
    """Map group key -> row positions, preserving first-seen order."""
    groups: Dict[str, List[int]] = {}
    if group_values is None:
        for i in range(n_rows):
            groups[f"__row_{i}"] = [i]
        return groups
    for i, value in enumerate(group_values):
        groups.setdefault(str(value), []).append(i)
    return groups


def _degraded(
    reason: str,
    n_rows: int,
    n_groups: int,
    group_column: Optional[str],
    holdout_size: float,
    seed: int,
) -> SelectionSplit:
    return SelectionSplit(
        train_index=list(range(n_rows)),
        holdout_index=[],
        strategy="none",
        group_column=group_column,
        n_groups=n_groups,
        holdout_size=holdout_size,
        seed=seed,
        label_supervised_on_full_data=True,
        degraded_reason=reason,
    )


def plan_selection_split(
    n_rows: int,
    labels: Optional[Sequence[Any]] = None,
    group_values: Optional[Sequence[Any]] = None,
    group_column: Optional[str] = None,
    task_type: str = "classification",
    holdout_size: float = 0.2,
    seed: int = 42,
    min_groups_for_holdout: int = MIN_GROUPS_FOR_HOLDOUT,
    min_train_groups: int = MIN_TRAIN_GROUPS,
) -> SelectionSplit:
    """Plan a grouped, stratified holdout to be excluded from feature selection.

    Returns a plan that is always usable: when an honest holdout is impossible the
    plan keeps every row in training and sets ``label_supervised_on_full_data``.
    """
    if n_rows <= 0:
        return _degraded("empty dataset", 0, 0, group_column, holdout_size, seed)

    if not (0.0 < holdout_size < 1.0):
        return _degraded(
            f"holdout disabled (holdout_size={holdout_size})",
            n_rows, 0, group_column, holdout_size, seed,
        )

    groups = _group_rows(group_values, n_rows)
    keys = list(groups.keys())
    n_groups = len(keys)

    if n_groups < min_groups_for_holdout:
        return _degraded(
            f"only {n_groups} group(s); need {min_groups_for_holdout} to hold any out "
            f"without making selection meaningless",
            n_rows, n_groups, group_column, holdout_size, seed,
        )

    n_holdout = int(round(n_groups * holdout_size))
    n_holdout = max(1, min(n_holdout, n_groups - min_train_groups))
    if n_holdout < 1:
        return _degraded(
            f"{n_groups} group(s) cannot yield a holdout while keeping "
            f"{min_train_groups} in training",
            n_rows, n_groups, group_column, holdout_size, seed,
        )

    rng = random.Random(seed)

    stratify = task_type == "classification" and labels is not None
    if stratify:
        # One label per group: the majority label among its rows. A group whose rows
        # disagree is a data defect, not a split problem — majority keeps it usable.
        group_label: Dict[str, Any] = {}
        for key, rows in groups.items():
            counts: Dict[Any, int] = {}
            for r in rows:
                counts[labels[r]] = counts.get(labels[r], 0) + 1
            group_label[key] = max(sorted(counts, key=str), key=lambda v: counts[v])

        buckets: Dict[Any, List[str]] = {}
        for key in keys:
            buckets.setdefault(group_label[key], []).append(key)

        # Every class must survive in training, or selection has nothing to separate.
        if any(len(members) < 2 for members in buckets.values()):
            smallest = min(len(m) for m in buckets.values())
            return _degraded(
                f"a class has only {smallest} group(s); holding one out would leave "
                f"training without that class",
                n_rows, n_groups, group_column, holdout_size, seed,
            )

        chosen: List[str] = []
        # Largest class first, so rounding favours the class that can spare a group.
        for label in sorted(buckets, key=lambda k: (-len(buckets[k]), str(k))):
            members = sorted(buckets[label])
            rng.shuffle(members)
            take = int(round(len(members) * holdout_size))
            take = max(0, min(take, len(members) - 1))
            chosen.extend(members[:take])

        # Rounding can under- or over-shoot the requested size; correct it without
        # ever emptying a class from training.
        if not chosen:
            largest = max(buckets, key=lambda k: (len(buckets[k]), str(k)))
            chosen = [sorted(buckets[largest])[0]]
        strategy = "grouped_stratified" if group_values is not None else "stratified"
    else:
        shuffled = sorted(keys)
        rng.shuffle(shuffled)
        chosen = shuffled[:n_holdout]
        strategy = "grouped" if group_values is not None else "random"

    holdout_keys = set(chosen)
    holdout_index = sorted(i for key in holdout_keys for i in groups[key])
    train_index = sorted(set(range(n_rows)) - set(holdout_index))

    if not holdout_index or not train_index:
        return _degraded(
            "split produced an empty side", n_rows, n_groups, group_column, holdout_size, seed
        )

    return SelectionSplit(
        train_index=train_index,
        holdout_index=holdout_index,
        strategy=strategy,
        group_column=group_column,
        n_groups=n_groups,
        holdout_size=holdout_size,
        seed=seed,
        label_supervised_on_full_data=False,
        degraded_reason=None,
        holdout_groups=sorted(holdout_keys),
    )
