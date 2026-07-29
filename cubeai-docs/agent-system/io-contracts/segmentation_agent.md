# Segmentation Agent

## Role
Runs ROI segmentation workflow and prepares mask outputs for feature extraction.

## Runtime identity
- Agent name: `segmentation_agent`
- Class: `SegmentationAgent`
- Environment `AGENT_ID`: `segmentation_agent`

## Upstream input
- Receives last message from `study_population_planner_agent` through team filter.
- Receives `workspace_folder` in system prompt.
- Internally runs `test_module(workspace_folder)` that executes segmentation module on workspace config.

## Tools used
- Function tool: 
  - `test_module`
- Scratchpad tools:
  - `scratchpad_register_agent`
  - `scratchpad_write_summary`
  - `scratchpad_set_status`
  - `scratchpad_raise_flag`
- Structured output:
  - `write_agent_output`

## Expected functional outputs
- One binary mask per successful volume-target pair at
  `masks/{volume_uid}__{target_id}.nii.gz`.
- `segmentation_manifest.json` is the authoritative downstream handoff. It
  records canonical target labels, mask paths, reuse/provenance, review state,
  and pair-level errors.
- The segmentation module returns the real `completed`, `partial`, or `failed`
  status and pair-level metrics.

## Mandatory structured output call
Call `write_agent_output` with:
- `agent_id`: `segmentation_agent`
- `agent_role`: `roi_segmentation`
- `status`: `completed` or `partial` or `failed`
- `inputs`: workspace folder and segmentation context
- `output_files`: manifest plus actual volume and mask paths
- `output_metrics`: volume-target pair count, target labels, mask count,
  reused/generated counts, and pair failures
- `output_summary`
- `warnings` when quality issues exist

## Handoff to next agent
- Team graph forwards to `feature_extraction_agent`.
- Feature extraction reads `segmentation_manifest.json`, emits one subject row
  per volume, and prefixes radiomic columns with the target ID.

## Failure conditions to flag
- No eligible volumes for segmentation.
- Missing or invalid config in workspace.
- Existing-mask discovery or target-plan validation fails during preflight.
- Interactive review not completed.
- Any failed or rejected volume-target pair is reported without preventing
  unaffected pairs from completing.
