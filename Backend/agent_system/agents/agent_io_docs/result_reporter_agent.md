# Result Reporter Agent

## Role
Synthesizes population, feature, and modeling outcomes into a scientific decision on hypothesis acceptance or rejection.

## Runtime identity
- Agent name: `result_reporter_agent`
- Class: `ResultReporterAgent`
- Environment `AGENT_ID`: `result_reporter_agent`

## Upstream input
- Receives first user message and last `modeling_agent` message by team filter.
- Reads upstream data via scratchpad and result tools.
- Inputs needed:
  - Population summary
  - Feature extraction summary
  - Modeling metrics and artifacts
  - Original hypothesis context

## Tools used
- `get_modeling_results`
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
- Decision: accepted or rejected.
- Scientific reasoning summary tied to quantitative evidence.
- Report body content suitable for manuscript stage.

## Mandatory structured output call
Call `write_agent_output` with:
- `agent_id`: keep consistent with pipeline naming
- `agent_role`: `result_analysis`
- `status`
- `inputs`: list of upstream agents
- `output_files`: optional
- `output_metrics`: decision, key evidence, confidence, primary metric and value, limitations
- `output_summary`
- `warnings` where methodology concerns exist

## Handoff to next agent
- Team graph forwards to `latex_reporter_agent`.
- LaTeX agent reads this output as key source for conclusion and hypothesis evaluation sections.

## Failure conditions to flag
- Missing upstream summaries.
- Contradictory metrics and narrative.
- Incomplete evaluation preventing reliable decision.

## Critical consistency note
- Team uses `result_reporter_agent`.
- Some system prompt sections use `result_agent`.
- Use one ID consistently in scratchpad and `write_agent_output` to avoid broken reads.
