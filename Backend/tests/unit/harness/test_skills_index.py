"""What ``/skills`` lists (merged plan, Phase 12.6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_system.harness.skills_index import PLUGIN_DIR, SkillsIndexError, skills_index
from agent_system.toolkit.agents.spec import AgentSpec
from agent_system.toolkit.catalog import build

pytestmark = pytest.mark.unit


def _agent(name: str, skills: list[str]) -> AgentSpec:
    return AgentSpec(
        agent_type=name, description=f"{name}.", kind="execution", system_prompt="P.", skills=skills
    )


def _plugin(tmp_path: Path, skills: dict[str, str]) -> Path:
    for name, front in skills.items():
        path = tmp_path / "skills" / name / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(f"---\n{front}\n---\nGuidance.\n", encoding="utf-8")
    return tmp_path


def test_the_real_plugin():
    entries = {entry.name: entry for entry in skills_index(build().agents)}

    assert sorted(entries) == [
        "cohort_building", "modeling", "reporting", "research_study", "segmentation_choices",
    ]  # fmt: skip
    assert entries["modeling"].agents == ("composer", "main_agent")
    assert entries["reporting"].agents == ("latex_reporter_agent", "result_reporter_agent")
    assert all(entry.description for entry in entries.values())
    assert PLUGIN_DIR.name == "cubeai"


def test_each_skill_with_the_agents_that_list_it(tmp_path):
    plugin = _plugin(
        tmp_path,
        {
            "modeling": "name: modeling\ndescription: Setting up run_modeling.",
            "reporting": "name: reporting\ndescription: Writing the report.",
        },
    )
    agents = [_agent("b_agent", ["modeling"]), _agent("a_agent", ["modeling", "reporting"])]

    entries = skills_index(agents, plugin)

    assert [entry.as_dict() for entry in entries] == [
        {"name": "modeling", "description": "Setting up run_modeling.", "agents": ["a_agent", "b_agent"]},
        {"name": "reporting", "description": "Writing the report.", "agents": ["a_agent"]},
    ]  # fmt: skip


def test_a_skill_no_agent_lists_is_still_shown(tmp_path):
    plugin = _plugin(tmp_path, {"modeling": "name: modeling\ndescription: Modeling."})

    (entry,) = skills_index([], plugin)

    assert entry.agents == ()


def test_every_broken_skill_is_reported_at_once(tmp_path):
    plugin = _plugin(tmp_path, {"modeling": "name: modeling", "reporting": "name: [unclosed"})

    with pytest.raises(SkillsIndexError) as caught:
        skills_index([], plugin)

    assert len(caught.value.problems) == 2
    assert "no description" in caught.value.problems[0]
