"""The ML libraries' routine warnings, silenced once, for the whole process.

Fitting many models on small cohorts makes scikit-learn, LightGBM and
XGBoost warn constantly (a solver that did not converge, a fold whose
predictions miss a class, a constant feature, a tree with no further split).
The modeling engine and feature selection never print, so those warnings must
not reach stderr either.

``warnings.catch_warnings()`` would scope them to one fit, but it swaps the
process-wide filter list and is not thread-safe before Python 3.14, and the
packs fit in worker threads, possibly for two sessions at once. So the
filters are installed once, narrowly: only these categories, only when
raised from these libraries' own modules.
"""

from __future__ import annotations

import warnings

_LIBRARY_MODULES = r"^(sklearn|lightgbm|xgboost)(\.|$)"
_installed = False


def install() -> None:
    """Install the filters (idempotent)."""
    global _installed
    if _installed:
        return
    from sklearn.exceptions import ConvergenceWarning, UndefinedMetricWarning

    for category in (ConvergenceWarning, UndefinedMetricWarning, UserWarning, FutureWarning, RuntimeWarning):
        warnings.filterwarnings("ignore", category=category, module=_LIBRARY_MODULES)
    _installed = True


__all__ = ["install"]
