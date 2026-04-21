# Hypothesis Architect

## Role
Transforms user clinical intent into a structured PICO hypothesis.

## Runtime identity
- Agent key in planner: `hypothesis_architect`
- Agent class type: `AssistantAgent`

## Inputs
- Source: user message provided by planner.
- Main prompt payload:
  - Free text clinical observation or research idea.
  - During refinement, user feedback text.

## Required output format
- Must return valid JSON.
- Top-level fields:
  - `hypothesis`
  - `message`
- `hypothesis` object fields:
  - `title`
  - `population`
  - `intervention`
  - `comparison`
  - `outcome`
  - `hypothesis`
  - `question`
  - `clinical_context`
  - `relationship_type`

## Approval signal
- Planner treats user response containing `APPROVE` as confirmation.

## Persisted outputs
- Planner saves confirmed hypothesis into planner state.
- Emitted to UI as `hypothesis` event.

## Downstream consumers
- Pipeline Architect receives confirmed hypothesis JSON.
- Context compiler uses hypothesis fields to produce per-agent task messages.

## Success criteria
- Output is parseable JSON.
- PICO is complete and clinically specific.
- Hypothesis is confirmed by user.
