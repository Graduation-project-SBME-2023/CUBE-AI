"""The modeling engine (merged plan, Phase 7.0): scikit-learn, LightGBM and
XGBoost in place of PyCaret.

Plain functions over pandas and scikit-learn, with no tool or HTTP code:

- ``estimators``: the classification and regression registries, keyed by the
  short model ids PyCaret used (``lr``, ``rf``, ``lightgbm``, ...);
- ``preprocessing``: the ``Pipeline`` that replaces ``setup()``;
- ``cv``: fold clamping, the splitter and the random evaluation split;
- ``metrics``: the metric names of the result tables (``Accuracy``, ``AUC``,
  ``Prec.``, ... / ``MAE``, ``RMSE``, ``R2``, ...);
- ``compare``, ``tune``, ``blend``: what ``compare_models``, ``tune_model``
  and ``blend_models`` did;
- ``plots``: matplotlib figures (no pyplot, no global state) and the SHAP
  summary that replaces ``interpret_model``;
- ``persistence``: the saved model bundle, written atomically.

Nothing here prints, changes directory or reconfigures logging.
"""
