"""Feature selection, as ``radiomics_analyze_importance`` runs it:

- ``matrix`` — the labelled feature matrix, its encodings and the
  leakage-safe selection split (everything that consumes labels fits on the
  training rows only);
- ``ranking`` — the importance methods and the alternative rankings;
- ``charts`` — the ``chart_data`` the Features view draws, and the
  label-free QC a feature set stores;
- ``plots`` — the archival PNG figures;
- ``feature_sets`` — the frozen sets modeling trains on.

The computing modules are synchronous; the tool runs them off the event loop.
"""
