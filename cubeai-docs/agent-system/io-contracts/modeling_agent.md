# Modeling Agent

## Role
Trains and evaluates machine learning classifiers on radiomics features.

## Runtime identity
- Agent name: `modeling_agent`
- Class: `ModelingAgent`
- Environment `AGENT_ID`: `modeling_agent`

## Upstream input
- Receives last message from `feature_extraction_agent` through team filter.
- Reads feature extraction output from scratchpad.
- Required data input:
  - Features CSV path
  - Target column, typically `target`

## Tools used
- `pycaret_train_classifier`
- `pycaret_run_inference`
- Scratchpad tools:
  - `scratchpad_register_agent`
  - `scratchpad_read_agent`
  - `scratchpad_write_note`
  - `scratchpad_raise_flag`
  - `scratchpad_write_summary`
  - `scratchpad_set_status`
- Structured output:
  - `write_agent_output`

## Expected functional outputs
- Modeling artifacts in workspace modeling folder.
- Typical files:
  - `modeling/model_comparison_results.csv`
  - `modeling/model_summary.csv`
  - Saved model files
  - Holdout prediction CSVs

## Mandatory structured output call
Call `write_agent_output` with:
- `agent_id`: `modeling_agent`
- `agent_role`: `ml_modeling`
- `status`
- `inputs`: upstream agent and features path
- `output_files`: comparison, summary, and best-model artifacts
- `output_metrics`: best model, accuracy, AUC, sensitivity, specificity, F1, validation details
- `output_summary`
- `warnings` for overfitting or low quality

## Handoff to next agent
- Team graph forwards to `result_reporter_agent`.
- Result reporter consumes metrics and model outputs from files and structured output.

## Failure conditions to flag
- Missing target column.
- Not enough samples.
- Training completes but evaluation metrics are invalid.

## Integration note
- Keep output file paths relative to workspace where possible for report portability.
