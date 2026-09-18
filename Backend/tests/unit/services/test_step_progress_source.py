"""Per-step execution status is durable now."""

from __future__ import annotations

import pytest

from services.execution_state import execution_has_started, infer_recovered_session_state
from services.run_context import (
    agent_type_of,
    build_compiled_prompt,
    build_input_refs,
    first_incomplete,
    planned_runs,
)

pytestmark = pytest.mark.unit


def _api_run(order: int, agent_type: str, status: str, summary: str | None = None):
    return {
        "agent_run_id": f"run-{order}",
        "step_id": f"step-{order}",
        "session_id": "11111111-1111-4111-8111-111111111111",
        "status": status,
        "compiled_prompt": None,
        "output_summary": (
            {
                "schema_version": 1,
                "agent_output": {
                    "status": "completed",
                    "outputs": {
                        "summary": summary,
                        "metrics": {"cohort_size": 14},
                        "files": {"targets_csv": "targets.csv"},
                    },
                },
            }
            if summary
            else None
        ),
        "step": {
            "step_id": f"step-{order}",
            "step_order": order,
            "agent_type": agent_type,
            "title": f"Step {order}",
            "instructions": {
                "description": f"do {agent_type} work",
                "task_message": {
                    "schema_version": 1,
                    "task_id": f"task-{order}",
                    "phase": "phase",
                    "agent_title": f"Step {order}",
                    "instructions": f"compiled instructions {order}",
                    "hypothesis_summary": "spleen hypothesis",
                    "payload": {},
                    "context_augmentation": {},
                },
            },
        },
    }


def test_runs_are_ordered_by_step_and_keep_run_identity():
    runs = planned_runs(
        [
            _api_run(2, "segmentation_agent", "pending"),
            _api_run(1, "study_population_planner_agent", "completed", "14 volumes"),
        ]
    )

    assert [run.step_order for run in runs] == [1, 2]
    assert [run.agent_run_id for run in runs] == ["run-1", "run-2"]


def test_two_steps_sharing_an_agent_type_are_two_distinct_nodes():
    """The legacy runner de-duplicated by agent id, so the second step silently never executed."""
    runs = planned_runs(
        [
            _api_run(1, "segmentation_agent", "completed", "first pass"),
            _api_run(2, "segmentation_agent", "pending"),
        ]
    )

    names = [run.node_name for run in runs]
    assert len(set(names)) == 2
    assert all(agent_type_of(name) == "segmentation_agent" for name in names)


def test_resume_continues_the_first_incomplete_run():
    runs = planned_runs(
        [
            _api_run(1, "study_population_planner_agent", "completed", "14 volumes"),
            _api_run(2, "segmentation_agent", "running"),
            _api_run(3, "modeling_agent", "pending"),
        ]
    )

    resume_point = first_incomplete(runs)

    assert resume_point is not None
    assert resume_point.step_id == "step-2"
    assert execution_has_started([{"status": run.status} for run in runs])


def test_a_fully_completed_plan_has_nothing_to_resume():
    runs = planned_runs(
        [
            _api_run(1, "study_population_planner_agent", "completed", "done"),
            _api_run(2, "modeling_agent", "completed", "done"),
        ]
    )
    assert first_incomplete(runs) is None


def test_a_fresh_process_reads_progress_from_the_runs_not_from_memory():
    """The E→F gap: a restart used to report every step pending. It reports
    what the database says now, and a persisted RUNNING row still hydrates as
    PAUSED because no asyncio task survived the restart."""
    runs = [
        {"status": "completed"},
        {"status": "running"},
        {"status": "pending"},
    ]
    assert execution_has_started(runs)
    assert infer_recovered_session_state(runs) == "PAUSED"


def test_compiled_prompt_carries_the_step_and_its_upstream_results():
    runs = planned_runs(
        [
            _api_run(1, "study_population_planner_agent", "completed", "14 volumes"),
            _api_run(2, "segmentation_agent", "pending"),
        ]
    )

    prompt = build_compiled_prompt(runs[1], runs[:1])

    assert "compiled instructions 2" in prompt
    # The upstream result only exists once step 1 ran — which is exactly why
    # this cannot be resolved at admission time.
    assert "14 volumes" in prompt
    assert "targets.csv" in prompt


def test_input_refs_record_source_ids_not_their_contents():
    runs = planned_runs(
        [
            _api_run(1, "study_population_planner_agent", "completed", "14 volumes"),
            _api_run(2, "segmentation_agent", "pending"),
        ]
    )

    refs = build_input_refs(runs[1], runs[:1], {"prompt_kind": "general", "intent": None})

    assert refs["schema_version"] == 1
    assert refs["step"]["step_id"] == "step-2"
    assert refs["upstream_runs"][0]["agent_run_id"] == "run-1"
    assert refs["session"]["prompt_kind"] == "general"
    assert refs["session"]["intent_present"] is False
