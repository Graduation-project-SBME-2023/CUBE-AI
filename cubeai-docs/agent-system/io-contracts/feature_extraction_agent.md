# Feature Extraction Agent

## Role
Extracts radiomics features from image-mask pairs, then computes feature importance outputs.

## Runtime identity
- Agent name: `feature_extraction_agent`
- Class: `FeatureExtractionAgent`
- Environment `AGENT_ID`: `feature_extraction_agent`

## Upstream input
- Receives last message from `segmentation_agent` through team filter.
- Reads upstream context via scratchpad.
- Expected practical inputs:
  - Volumes directory
  - Masks directory
  - Targets CSV

## Tools used
- `radiomics_extract_features`
- `radiomics_analyze_importance`
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
- Feature CSV files in workspace features folder.
- Importance ranking outputs including top-N feature CSVs.
- Typical generated files include:
  - `features/radiomic_features_label_<label>.csv`
  - `features/top_10_features.csv` or equivalent top-N selection files
  - `features/feature_importance_scores.csv`

## Mandatory structured output call
Call `write_agent_output` with:
- `agent_id`: recommended `feature_extraction_agent`
- `agent_role`: `feature_extraction`
- `status`
- `inputs`: upstream source, targets path, image and mask paths
- `output_files`: radiomics features and top feature outputs
- `output_metrics`: sample count, feature count, classes, top features
- `output_summary`
- `warnings`

## Handoff to next agent
- Team graph forwards to `modeling_agent`.
- Modeling requires a valid feature table and target column.

## Failure conditions to flag
- Empty feature table.
- Label mismatch between masks and targets.
- Missing target column in exported feature CSV.

## Integration note
- Ensure scratchpad reads use the correct argument key expected by the tool schema (`scratchpad_read_agent(target_agent_id=...)`).
