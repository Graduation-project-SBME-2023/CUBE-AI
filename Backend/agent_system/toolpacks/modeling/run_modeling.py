"""``run_modeling``: train, evaluate and select models on frozen feature sets.

This module holds the tool's input model, ``RunModelingInput``. The arguments
are flat, so a graph node's input port can fill ``feature_set_ids`` directly
with the ids of the upstream feature tables.
"""

from __future__ import annotations

import uuid
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .engine import estimators
from .engine.task import infer_task

Task = Literal["classification", "regression", "auto"]

# numpy's seeds are unsigned 32-bit integers.
MAX_SEED = 2**32 - 1


def _unique(values: list[str], what: str) -> list[str]:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"{what} {value!r} is listed twice")
        seen.add(value)
    return values


class RunModelingInput(BaseModel):
    """The arguments of ``run_modeling``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    feature_set_ids: list[str] = Field(
        min_length=1,
        description=(
            "The frozen feature sets to model, usually every set of the feature extraction "
            "step (a compact top-N set and the full set), so they are compared. The node's "
            "input port fills it."
        ),
    )
    target: str = Field(
        default="has_target_disease",
        min_length=1,
        description="The label the cohort carries, which the models learn to predict.",
    )
    task: Task = Field(
        default="auto",
        description=(
            "classification for a binary or categorical label, regression for a continuous "
            "one; auto decides from the target's values (text, booleans, or at most 10 "
            "distinct whole numbers are a classification)."
        ),
    )
    comparisons: list[str] | None = Field(
        default=None,
        description=(
            "The estimator ids to compare (e.g. lr, rf, lightgbm). Omit it for the engine's "
            "default set, which is smaller for a small classification cohort."
        ),
    )
    folds: int = Field(default=5, ge=2, le=20, description="Cross-validation folds.")
    seed: int | None = Field(
        default=None,
        ge=0,
        le=MAX_SEED,
        description=(
            "Random seed. Leave it unset: it is derived from the input, so a rerun matches."
        ),
    )
    include_inference: bool = Field(
        default=True,
        description=(
            "Also evaluate the selected model on the rows the feature selection held out, "
            "the only honest estimate of performance on new patients."
        ),
    )

    @field_validator("feature_set_ids")
    @classmethod
    def _feature_set_ids_are_uuids(cls, values: list[str]) -> list[str]:
        canonical = []
        for value in values:
            try:
                canonical.append(str(uuid.UUID(value)))
            except ValueError:
                raise ValueError(f"feature set id {value!r} is not a UUID") from None
        return _unique(canonical, "feature set")

    @field_validator("comparisons")
    @classmethod
    def _comparisons_are_known(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        if not values:
            raise ValueError("list at least one estimator id, or omit comparisons")
        known = set(estimators.CLASSIFIERS) | set(estimators.REGRESSORS)
        for value in values:
            if value not in known:
                raise ValueError(
                    f"unknown estimator id {value!r}; known ids: {', '.join(sorted(known))}"
                )
        return _unique(values, "estimator")

    @model_validator(mode="after")
    def _comparisons_fit_the_task(self) -> RunModelingInput:
        if self.task != "auto" and self.comparisons:
            known = estimators.registry(self.task)
            wrong = [value for value in self.comparisons if value not in known]
            if wrong:
                raise ValueError(
                    f"not {self.task} estimators: {', '.join(wrong)}; "
                    f"known ids: {', '.join(sorted(known))}"
                )
        return self

    def resolve_task(self, target_values: pd.Series) -> str:
        """Return the task to train: ``task`` itself, or for ``auto`` the rule of
        ``engine.task.infer_task`` applied to the target column's values."""
        if self.task != "auto":
            return self.task
        return infer_task(target_values)


__all__ = ["MAX_SEED", "RunModelingInput"]
