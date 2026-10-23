"""The skills: the ``cubeai`` SDK plugin in agent_system/skills/cubeai/ (merged
plan, Phase 3.5).

The SDK loads the skills; nothing of ours reads them at run time. These tests
check the plugin's shape (only ``skills/<name>/SKILL.md``, front matter
``name`` and ``description`` only, no shell lines), that every skill an agent
lists exists, and each skill's facts against the code they describe, so a
skill cannot quietly drift from the system it advises on.
"""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path

import pytest

from agent_system.agents.clinical_roi_policy import ROI_IDENTITY_POLICY
from agent_system.toolkit.catalog import build
from agent_system.toolkit.frontmatter import read_front_matter
from agent_system.toolpacks.modeling import training
from agent_system.toolpacks.modeling.engine import estimators

pytestmark = pytest.mark.unit

AGENT_SYSTEM = Path(build.__code__.co_filename).resolve().parents[1]
PLUGIN = AGENT_SYSTEM / "skills" / "cubeai"
SKILLS = ("cohort_building", "modeling", "reporting", "research_study", "segmentation_choices")
# Phase 3.5, item 2: who gets which skill.
USED_BY = {
    "research_study": {"main_agent", "composer"},
    "modeling": {"main_agent", "composer"},
    "cohort_building": {"study_population_planner_agent"},
    "segmentation_choices": {"segmentation_agent"},
    "reporting": {"result_reporter_agent", "latex_reporter_agent"},
}


@functools.cache
def _skill(name: str) -> tuple[dict, str]:
    return read_front_matter(PLUGIN / "skills" / name / "SKILL.md")


def _body(name: str) -> str:
    return _skill(name)[1]


@functools.cache
def _catalog():
    built = build()
    built.validate()
    return built


# --- the plugin


def test_the_plugin_holds_only_skills():
    assert sorted(path.name for path in PLUGIN.iterdir()) == [".claude-plugin", "skills"]
    assert [path.name for path in (PLUGIN / ".claude-plugin").iterdir()] == ["plugin.json"]
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "cubeai"
    # No agents/, hooks/, commands/ or MCP servers: a skill is text only.
    assert not {"agents", "hooks", "commands", "mcpServers", "lspServers"} & manifest.keys()


def test_the_initial_skills():
    assert sorted(path.name for path in (PLUGIN / "skills").iterdir()) == list(SKILLS)
    for name in SKILLS:
        assert [path.name for path in (PLUGIN / "skills" / name).iterdir()] == ["SKILL.md"]


@pytest.mark.parametrize("name", SKILLS)
def test_a_skill_has_a_name_and_a_description_only(name):
    front, body = _skill(name)

    assert set(front) == {"name", "description"}, name  # no allowed-tools or other permissions
    assert front["name"] == name
    assert front["description"].strip() and "\n" not in front["description"].strip()
    assert body.strip()


@pytest.mark.parametrize("name", SKILLS)
def test_a_skill_runs_no_command(name):
    """A ``!`command` `` line runs a shell command before the model sees the skill."""
    assert not re.search(r"!`", _body(name)), name


@pytest.mark.parametrize("name", SKILLS)
def test_a_skill_is_guidance_not_a_script(name):
    """A skill never routes and never forces a workflow (Phase 3.5 rule 3)."""
    body = _body(name)

    assert not re.search(r"\b(MUST|ALWAYS|MANDATORY|REQUIRED)\b", body), name
    assert "pycaret" not in body.lower(), name
    assert "skill_load" not in body and "applies_to" not in body, name


# --- who uses which skill


def test_every_skill_an_agent_lists_exists():
    for agent in _catalog().agents:
        for name in agent.skills:
            assert (PLUGIN / "skills" / name / "SKILL.md").is_file(), (agent.name, name)


def test_who_gets_which_skill():
    used_by: dict[str, set[str]] = {}
    for agent in _catalog().agents:
        for name in agent.skills:
            used_by.setdefault(name, set()).add(agent.name)
    assert used_by == USED_BY


def test_internal_agents_list_no_skills_except_the_mains_subagents():
    for agent in _catalog().agents:
        if agent.parent and agent.parent != "main_agent":
            assert agent.skills == (), agent.name


# --- the facts the skills state


def test_segmentation_carries_the_roi_policy_verbatim():
    assert ROI_IDENTITY_POLICY.strip() in _body("segmentation_choices")


def test_the_study_graph_names_real_agents():
    body = _body("research_study")
    named = set(re.findall(r"`([a-z_]+_agent)`", body))

    assert named == {
        "literature_review_agent", "study_population_planner_agent", "segmentation_agent",
        "feature_extraction_agent", "result_reporter_agent", "latex_reporter_agent",
    }  # fmt: skip
    assert named <= _catalog().agents.names()


def test_approvals_are_what_the_user_asks_for():
    """D28: no step waits for a person unless the user asked for it."""
    for name in ("cohort_building", "research_study", "segmentation_choices"):
        assert "user asked" in _body(name), name


def _ids_after(body: str, heading: str) -> set[str]:
    sentence = body.split(heading, 1)[1].split(".", 1)[0]
    return set(re.findall(r"`([a-z]+)`", sentence))


def test_the_modeling_estimator_ids_are_the_engines():
    body = _body("modeling")

    assert _ids_after(body, "Estimator ids for classification:") == set(estimators.CLASSIFIERS)
    assert _ids_after(body, "For regression:") == set(estimators.REGRESSORS)


def test_the_small_cohort_rule_is_the_engines():
    body = _body("modeling")
    sentence = body.split("for a classification cohort under", 1)[1].split("because", 1)[0]

    assert sentence.startswith(f" {training.SMALL_COHORT_ROWS} rows")
    assert tuple(re.findall(r"`([a-z]+)`", sentence)) == estimators.SMALL_COHORT_CLASSIFIERS


def test_the_report_templates_exist():
    templates = AGENT_SYSTEM / "toolpacks" / "reporting" / "templates"
    body = _body("reporting")

    for name in ("one_page_abstract", "short_study", "ieee_research_paper"):
        assert f"`{name}`" in body
        assert (templates / f"{name}.tex").is_file(), name
