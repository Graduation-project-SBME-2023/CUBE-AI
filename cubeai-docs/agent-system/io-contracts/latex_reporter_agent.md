# LaTeX Reporter Agent

## Role
Builds final manuscript from all upstream outputs and compiles PDF.

## Runtime identity
- Agent name: `latex_reporter_agent`
- Class: `ReporterAgent`
- Environment `AGENT_ID`: `latex_reporter_agent`

## Upstream input
- Receives last message from `result_reporter_agent` through team filter.
- Must pull structured data from tools, not only chat text.

## Tools used
- `read_all_agent_outputs`
- `read_literature_context`
- `scratchpad_read_report`
- `list_latex_templates`
- `get_latex_template`
- `write_agent_output`

## Expected functional outputs
- Selected template filled with study content.
- Structured manuscript output saved via `write_agent_output`.
- LaTeX source available in the structured output for frontend compilation.
- Terminal completion signal in response text, typically `task_done`.

## Template input expectations
- Structured agent outputs available at `WORKSPACE_DIR/outputs/*_output.json`.
- Literature context available at `WORKSPACE_DIR/literature_context.json` when lit review was enabled.
- Scratchpad report available in `WORKSPACE_DIR/scratchpad.json`.

## Output contract
- Mandatory `write_agent_output` call is enforced for this agent.
- Success is determined by successful structured output publication.

## Failure conditions to flag
- Missing upstream output files.
- Missing literature context when citations are required.
- LaTeX compilation errors.

## Handoff
- This is the terminal stage of the execution chain.
