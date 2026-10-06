"""The estimator registries, keyed by the model ids PyCaret used.

``model_results.parameters.pycaret_model_id`` already stores these ids, so old
records and prompts keep their meaning. Each entry knows its class, its default
parameters, its tuning search space and what it can report (probabilities,
feature importances, a tree SHAP explanation). There is no CatBoost entry.
"""

from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

CLASSIFICATION = "classification"
REGRESSION = "regression"
TASKS = (CLASSIFICATION, REGRESSION)

# The candidates a small classification cohort compares when the caller names
# none (the tools' rule since the PyCaret version).
SMALL_COHORT_CLASSIFIERS = ("lr", "nb", "dt", "ridge", "svm", "knn", "rf")


@dataclass(frozen=True)
class Estimator:
    model_id: str
    name: str  # the "Model" column of the comparison table
    class_path: str
    params: Mapping[str, Any] = field(default_factory=dict)
    search_space: Mapping[str, Sequence[Any]] = field(default_factory=dict)
    seeded: bool = False  # accepts random_state
    parallel: bool = False  # accepts n_jobs
    tree: bool = False  # explained with shap.TreeExplainer
    default: bool = True  # compared when the caller names no models (PyCaret's turbo set)

    @property
    def package(self) -> str:
        return self.class_path.split(".", 1)[0]

    def available(self) -> bool:
        return importlib.util.find_spec(self.package) is not None


_C = [0.01, 0.1, 1.0, 10.0, 100.0]
_ALPHA = [0.0001, 0.001, 0.01, 0.1, 1.0, 10.0]
_FOREST = {
    "n_estimators": [100, 200, 500],
    "max_depth": [None, 3, 5, 10],
    "min_samples_leaf": [1, 2, 4],
    "max_features": ["sqrt", "log2", None],
}
_GB = {
    "n_estimators": [50, 100, 200],
    "learning_rate": [0.01, 0.05, 0.1, 0.2],
    "max_depth": [2, 3, 4],
    "subsample": [0.7, 1.0],
}
_ADA = {"n_estimators": [50, 100, 200], "learning_rate": [0.01, 0.1, 0.5, 1.0]}
_TREE = {"max_depth": [None, 2, 3, 5, 8], "min_samples_leaf": [1, 2, 4, 8]}
_KNN = {"n_neighbors": [3, 5, 7, 9, 11], "weights": ["uniform", "distance"]}
_MLP = {
    "hidden_layer_sizes": [(50,), (100,), (50, 50)],
    "alpha": [0.0001, 0.001, 0.01],
    "learning_rate_init": [0.001, 0.01],
}
_LGBM = {
    "n_estimators": [100, 200, 500],
    "learning_rate": [0.01, 0.05, 0.1],
    "num_leaves": [7, 15, 31],
    "min_child_samples": [3, 5, 10, 20],
}
_XGB = {
    "n_estimators": [100, 200, 500],
    "learning_rate": [0.01, 0.05, 0.1, 0.3],
    "max_depth": [2, 3, 4, 6],
    "subsample": [0.7, 1.0],
    "colsample_bytree": [0.7, 1.0],
}


def _registry(*entries: Estimator) -> dict[str, Estimator]:
    return {entry.model_id: entry for entry in entries}


CLASSIFIERS = _registry(
    Estimator("lr", "Logistic Regression", "sklearn.linear_model.LogisticRegression",
              {"max_iter": 1000}, {"C": _C}, seeded=True),
    Estimator("knn", "K Neighbors Classifier", "sklearn.neighbors.KNeighborsClassifier",
              search_space=_KNN, parallel=True),
    Estimator("nb", "Naive Bayes", "sklearn.naive_bayes.GaussianNB",
              search_space={"var_smoothing": [1e-9, 1e-8, 1e-7, 1e-6, 1e-5]}),
    Estimator("dt", "Decision Tree Classifier", "sklearn.tree.DecisionTreeClassifier",
              search_space={**_TREE, "criterion": ["gini", "entropy"]}, seeded=True, tree=True),
    Estimator("svm", "SVM - Linear Kernel", "sklearn.linear_model.SGDClassifier",
              {"loss": "hinge", "max_iter": 1000, "tol": 0.001},
              {"alpha": _ALPHA, "penalty": ["l2", "l1", "elasticnet"]}, seeded=True),
    Estimator("rbfsvm", "SVM - Radial Kernel", "sklearn.svm.SVC",
              {"kernel": "rbf", "probability": True},
              {"C": _C, "gamma": ["scale", 0.01, 0.1, 1.0]}, seeded=True, default=False),
    Estimator("ridge", "Ridge Classifier", "sklearn.linear_model.RidgeClassifier",
              search_space={"alpha": [0.01, 0.1, 1.0, 10.0, 100.0]}, seeded=True),
    Estimator("rf", "Random Forest Classifier", "sklearn.ensemble.RandomForestClassifier",
              search_space=_FOREST, seeded=True, parallel=True, tree=True),
    Estimator("et", "Extra Trees Classifier", "sklearn.ensemble.ExtraTreesClassifier",
              search_space=_FOREST, seeded=True, parallel=True, tree=True),
    Estimator("ada", "Ada Boost Classifier", "sklearn.ensemble.AdaBoostClassifier",
              search_space=_ADA, seeded=True),
    Estimator("gbc", "Gradient Boosting Classifier", "sklearn.ensemble.GradientBoostingClassifier",
              search_space=_GB, seeded=True, tree=True),
    Estimator("lda", "Linear Discriminant Analysis",
              "sklearn.discriminant_analysis.LinearDiscriminantAnalysis",
              search_space={"solver": ["lsqr"], "shrinkage": [None, "auto", 0.1, 0.5, 0.9]}),
    Estimator("mlp", "MLP Classifier", "sklearn.neural_network.MLPClassifier",
              {"max_iter": 500}, _MLP, seeded=True, default=False),
    Estimator("lightgbm", "Light Gradient Boosting Machine", "lightgbm.LGBMClassifier",
              {"verbose": -1}, _LGBM, seeded=True, parallel=True, tree=True),
    Estimator("xgboost", "Extreme Gradient Boosting", "xgboost.XGBClassifier",
              {"verbosity": 0}, _XGB, seeded=True, parallel=True, tree=True),
    Estimator("dummy", "Dummy Classifier", "sklearn.dummy.DummyClassifier",
              {"strategy": "prior"}, seeded=True),
)

REGRESSORS = _registry(
    Estimator("lr", "Linear Regression", "sklearn.linear_model.LinearRegression",
              search_space={"fit_intercept": [True, False]}, parallel=True),
    Estimator("lasso", "Lasso Regression", "sklearn.linear_model.Lasso",
              {"max_iter": 5000}, {"alpha": _ALPHA}, seeded=True),
    Estimator("ridge", "Ridge Regression", "sklearn.linear_model.Ridge",
              search_space={"alpha": [0.01, 0.1, 1.0, 10.0, 100.0]}, seeded=True),
    Estimator("en", "Elastic Net", "sklearn.linear_model.ElasticNet",
              {"max_iter": 5000}, {"alpha": _ALPHA, "l1_ratio": [0.1, 0.5, 0.9]}, seeded=True),
    Estimator("br", "Bayesian Ridge", "sklearn.linear_model.BayesianRidge",
              search_space={"alpha_1": [1e-6, 1e-4, 1e-2], "lambda_1": [1e-6, 1e-4, 1e-2]}),
    Estimator("huber", "Huber Regressor", "sklearn.linear_model.HuberRegressor",
              {"max_iter": 1000}, {"epsilon": [1.1, 1.35, 1.5, 2.0], "alpha": [0.0001, 0.001, 0.01]}),
    Estimator("knn", "K Neighbors Regressor", "sklearn.neighbors.KNeighborsRegressor",
              search_space=_KNN, parallel=True),
    Estimator("dt", "Decision Tree Regressor", "sklearn.tree.DecisionTreeRegressor",
              search_space=_TREE, seeded=True, tree=True),
    Estimator("rf", "Random Forest Regressor", "sklearn.ensemble.RandomForestRegressor",
              search_space=_FOREST, seeded=True, parallel=True, tree=True),
    Estimator("et", "Extra Trees Regressor", "sklearn.ensemble.ExtraTreesRegressor",
              search_space=_FOREST, seeded=True, parallel=True, tree=True),
    Estimator("ada", "AdaBoost Regressor", "sklearn.ensemble.AdaBoostRegressor",
              search_space=_ADA, seeded=True),
    Estimator("gbr", "Gradient Boosting Regressor", "sklearn.ensemble.GradientBoostingRegressor",
              search_space=_GB, seeded=True, tree=True),
    Estimator("svm", "Support Vector Regression", "sklearn.svm.SVR",
              search_space={"C": _C, "epsilon": [0.01, 0.1, 0.5]}),
    Estimator("mlp", "MLP Regressor", "sklearn.neural_network.MLPRegressor",
              {"max_iter": 500}, _MLP, seeded=True, default=False),
    Estimator("lightgbm", "Light Gradient Boosting Machine", "lightgbm.LGBMRegressor",
              {"verbose": -1}, _LGBM, seeded=True, parallel=True, tree=True),
    Estimator("xgboost", "Extreme Gradient Boosting", "xgboost.XGBRegressor",
              {"verbosity": 0}, _XGB, seeded=True, parallel=True, tree=True),
    Estimator("dummy", "Dummy Regressor", "sklearn.dummy.DummyRegressor", {"strategy": "mean"}),
)


def registry(task: str) -> dict[str, Estimator]:
    if task == CLASSIFICATION:
        return CLASSIFIERS
    if task == REGRESSION:
        return REGRESSORS
    raise ValueError(f"unknown modeling task {task!r}; expected one of {TASKS}")


def entry(task: str, model_id: str) -> Estimator:
    entries = registry(task)
    if model_id not in entries:
        raise ValueError(
            f"unknown {task} model id {model_id!r}; known ids: {', '.join(sorted(entries))}"
        )
    return entries[model_id]


def select(
    task: str,
    *,
    include: Iterable[str] | None = None,
    exclude: Iterable[str] | None = None,
    small_cohort: bool = False,
) -> list[Estimator]:
    """The candidates to compare: ``include`` when given, else the default set
    (the small-cohort set for a small classification cohort), minus
    ``exclude``, minus any whose package is not installed. An unknown id is an
    error, never silently skipped."""
    entries = registry(task)
    excluded = {entry(task, model_id).model_id for model_id in exclude or ()}
    if include:
        chosen = [entry(task, model_id) for model_id in include]
    elif small_cohort and task == CLASSIFICATION:
        chosen = [entries[model_id] for model_id in SMALL_COHORT_CLASSIFIERS]
    else:
        chosen = [candidate for candidate in entries.values() if candidate.default]
    return [c for c in chosen if c.model_id not in excluded and c.available()]


def build(task: str, model_id: str, *, seed: int, n_jobs: int = 1, **overrides: Any) -> Any:
    """A fresh, unfitted estimator of the registry entry."""
    return instantiate(entry(task, model_id), seed=seed, n_jobs=n_jobs, **overrides)


def instantiate(spec: Estimator, *, seed: int, n_jobs: int = 1, **overrides: Any) -> Any:
    params: dict[str, Any] = dict(spec.params)
    if spec.seeded:
        params["random_state"] = int(seed)
    if spec.parallel:
        params["n_jobs"] = int(n_jobs)
    params.update(overrides)
    return load_class(spec.class_path)(**params)


def load_class(class_path: str) -> type:
    module_name, _, class_name = str(class_path).rpartition(".")
    if not module_name or not class_name:
        raise ValueError(f"cannot load an estimator class from {class_path!r}")
    return getattr(importlib.import_module(module_name), class_name)


def by_class_path(task: str, class_path: str) -> Estimator | None:
    """The registry entry of a recorded estimator class, if any."""
    return next((e for e in registry(task).values() if e.class_path == class_path), None)


__all__ = [
    "CLASSIFICATION",
    "CLASSIFIERS",
    "Estimator",
    "REGRESSION",
    "REGRESSORS",
    "SMALL_COHORT_CLASSIFIERS",
    "TASKS",
    "build",
    "by_class_path",
    "entry",
    "instantiate",
    "load_class",
    "registry",
    "select",
]
