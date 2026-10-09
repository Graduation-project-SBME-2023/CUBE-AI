"""Radiomics pack: feature extraction and feature selection.

- ``extract_features`` — ``radiomics_extract_features`` (the steps are in
  ``extraction/``): one provenance row per reviewed mask;
- ``analyze_importance`` — ``radiomics_analyze_importance`` (the steps are in
  ``selection/``): the ranking, the charts and the frozen feature sets;
- shared by both: ``digests`` (content identity), ``feature_set_builder``
  (rows to the labelled wide table), ``selection_split`` (the leakage-safe
  holdout), ``cache_utils`` and ``validation_utils``.
"""
