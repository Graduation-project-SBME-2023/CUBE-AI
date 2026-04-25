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
- Segmentation masks in workspace mask or labels directories.
- Segmentation module returns completion status.

## Mandatory structured output call
Call `write_agent_output` with:
- `agent_id`: `segmentation_agent`
- `agent_role`: `roi_segmentation`
- `status`: `completed` or `partial` or `failed`
- `inputs`: workspace folder and segmentation context
- `output_files`: at least labels or masks directory
- `output_metrics`: method, volume count, ROI description
- `output_summary`
- `warnings` when quality issues exist

## Handoff to next agent
- Team graph forwards to `feature_extraction_agent`.
- Downstream must be able to find masks and volume-mask pairing in workspace.

## Failure conditions to flag
- No eligible volumes for segmentation.
- Missing or invalid config in workspace.
- Interactive review not completed.
