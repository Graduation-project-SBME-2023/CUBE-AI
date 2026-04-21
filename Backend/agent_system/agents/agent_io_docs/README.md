# Agent Input Output Contracts

This folder documents the input and output contract for each agent required by the planning and execution pipeline.

## Covered agents
- Hypothesis Architect
- Pipeline Architect
- Study Population Planner Agent
- Segmentation Agent
- Feature Extraction Agent
- Modeling Agent
- Result Reporter Agent
- LaTeX Reporter Agent

## Recommended execution order
1. Hypothesis Architect
2. Pipeline Architect
3. Study Population Planner Agent
4. Segmentation Agent
5. Feature Extraction Agent
6. Modeling Agent
7. Result Reporter Agent
8. LaTeX Reporter Agent

## Important cross-agent conventions
- Workspace outputs are written under `WORKSPACE_DIR`.
- Structured outputs are written by `write_agent_output` into `WORKSPACE_DIR/outputs/{agent_id}_output.json`.
- Scratchpad data is shared in `WORKSPACE_DIR/scratchpad.json`.
- Agent IDs must stay consistent across:
  - Team wiring
  - Scratchpad reads and writes
  - `write_agent_output` payloads

## Known naming mismatches to avoid
- `result_reporter_agent` is the runtime agent name in the team.
- Some prompts use `result_agent` in scratchpad and output calls.
- Pick one naming strategy and keep it consistent end to end.
