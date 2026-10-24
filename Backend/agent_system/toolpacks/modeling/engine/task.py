"""Which task a target column asks for, when the caller left it to ``auto``.

One fixed rule, read from the target's values alone, so the same data always
gives the same task:

- a column that is not numeric (text, categories) is a classification target;
- a numeric column whose values are all whole numbers and that has at most
  ``MAX_CLASSES`` distinct values is a classification target too: labels
  stored as ``0 / 1`` or ``1 / 2 / 3``, including as floats (``1.0``) or as
  booleans;
- any other numeric column (fractions, or more distinct whole numbers) is a
  regression target.

Missing values are left out before the rule looks. A column with no value at
all cannot decide, and raises ``ValueError``. The rule does not judge whether
the task can be trained (one class, too few rows): training refuses that.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .estimators import CLASSIFICATION, REGRESSION

# Above this many distinct whole numbers a column reads as a count or a score
# (a regression target) rather than as class labels.
MAX_CLASSES = 10


def infer_task(target: pd.Series) -> str:
    """Return ``CLASSIFICATION`` or ``REGRESSION`` for this target column.

    The rule is in the module docstring.
    """
    values = target.dropna()
    if values.empty:
        raise ValueError(
            f"the target column {target.name!r} has no values, so its task cannot be decided"
        )
    # Booleans count as numeric here: as 0 / 1 they fall under the rule below.
    if not pd.api.types.is_numeric_dtype(values):
        return CLASSIFICATION
    numbers = values.to_numpy(dtype=float)
    whole = bool(np.all(np.isfinite(numbers)) and np.all(numbers == np.round(numbers)))
    if whole and values.nunique() <= MAX_CLASSES:
        return CLASSIFICATION
    return REGRESSION


__all__ = ["MAX_CLASSES", "infer_task"]
