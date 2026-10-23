---
name: modeling
description: Setting up run_modeling for a radiomics study - target, task, feature sets, folds, estimators, and reading the results honestly.
---
# Modeling with `run_modeling`

Modeling is a tool node, not an agent: `run_modeling` trains, evaluates and
selects models on the scikit-learn engine (with LightGBM, XGBoost and SHAP).
It takes the frozen feature sets of the feature extraction step. Its schema
(`registry_describe run_modeling`) is authoritative where it differs from this
guidance.

## Parameters

- **`feature_set_ids`**: usually every frozen set of the feature extraction
  step (for example `top_10` and `all_features`), so the compact and the full
  sets are compared. The node's input port fills it.
- **`target`**: the label the cohort carries (by default `has_target_disease`).
- **`task`**: `classification` for a binary or categorical label,
  `regression` for a continuous one; `auto` decides from the target's type.
- **`comparisons`**: which estimators to compare. When none is named the
  engine compares its default set; for a classification cohort under 30 rows
  it compares only `lr`, `nb`, `dt`, `ridge`, `svm`, `knn` and `rf`, because
  the larger models overfit there.
- **`folds`**: 5-fold cross-validation by default. With very few samples per
  class, fewer folds keep every fold able to hold each class; under about 6
  samples per class, cross-validation is not meaningful.
- **`seed`**: leave it unset; it is derived from the input, so a rerun is
  reproducible.
- **`include_inference`**: evaluate the selected model on the rows the
  feature selection held out.

Estimator ids for classification: `lr`, `knn`, `nb`, `dt`, `svm`, `rbfsvm`,
`ridge`, `rf`, `et`, `ada`, `gbc`, `lda`, `mlp`, `lightgbm`, `xgboost`, `dummy`
(a baseline). For regression: `lr`, `lasso`, `ridge`, `en`, `br`, `huber`,
`knn`, `dt`, `rf`, `et`, `ada`, `gbr`, `svm`, `mlp`, `lightgbm`, `xgboost`,
`dummy`.

## Reading the results honestly

- A result is a **generalisation estimate** only when it was measured on rows
  the feature selection never saw. When the cohort was too small to hold rows
  out (`label_supervised_on_full_data`), every metric is optimistic by an
  unknown amount: say so to the user and in the report, never present it as
  performance on new patients.
- An AUC under 0.6, an accuracy near the class-balance baseline, or a model no
  better than `dummy` means the features do not separate the classes here; it
  is a finding, not a failure to hide.
- Severe class imbalance makes accuracy misleading: prefer AUC, sensitivity
  and specificity.
- Never quote a metric that a tool did not return.
