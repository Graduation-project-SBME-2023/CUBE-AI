# Pipeline Architect

## Role
Builds a study-specific ordered execution plan using confirmed hypothesis and literature-informed knowledge summary.

## Runtime identity
- Agent key in planner: `pipeline_architect`
- Agent class type: `AssistantAgent`

## Inputs
- Confirmed hypothesis JSON from planner state.
- Summarized knowledge text generated from literature sections.
- Optional user refinement instructions during plan approval loop.

## Required output format
- Must return valid JSON.
- Top-level fields:
  - `steps`
  - `rationale`
  - `message`
- Each `steps[]` item:
  - `id` as one of allowed agent IDs
  - `title`
  - `description`
  - `instructions`

## Allowed step IDs
- `study_population_planner_agent`
- `segmentation_agent`
- `feature_extraction_agent`
- `modeling_agent`
- `result_reporter_agent`
- `latex_reporter_agent`

## Persisted outputs
- Planner converts each step into internal study plan entries.
- Planner caches per-agent custom instructions for context compiler.

## Downstream consumers
- Context compiler creates `TaskMessage` objects for each selected step.
- Execution team uses ordered step IDs to run corresponding agents.

## Success criteria
- Output is parseable JSON.
- Step IDs map to real agents.
- Step order is executable end to end.
