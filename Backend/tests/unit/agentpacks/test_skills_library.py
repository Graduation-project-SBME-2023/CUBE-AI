"""The skills in agent_system/skills/ (merged plan, Phase 3.5).

Besides the format, these tests hold each skill's facts against the code they
describe, so a skill cannot quietly drift from the system it advises on.
"""

from __future__ import annotations

import functools
import re

import pytest

from agent_system.agents.clinical_roi_policy import ROI_IDENTITY_POLICY
from agent_system.toolkit.catalog import build
from agent_system.toolkit.skills import SKILLS_DIR, SkillLibrary
from agent_system.toolpacks.modeling import training
from agent_system.toolpacks.modeling.engine import estimators

pytestmark = pytest.mark.unit

SKILLS = ("cohort_building", "modeling", "reporting", "research_study", "segmentation_choices")


@functools.cache
def _library() -> SkillLibrary:
    return SkillLibrary.load()


def _body(name: str) -> str:
    return _library().get(name).body


def test_the_initial_skills():
    assert sorted(skill.name for skill in _library()) == list(SKILLS)


def test_every_skill_is_for_the_deciders_and_names_known_agents():
    library = _library()

    library.validate(agent_types=build().agents.names())
    for skill in library:
        assert skill.applies_to == ("main", "composer"), skill.name
    assert [entry["name"] for entry in library.index("main")] == list(SKILLS)


@pytest.mark.parametrize("name", SKILLS)
def test_a_skill_is_guidance_not_a_script(name):
    """A skill never routes and never forces a workflow (Phase 3.5 rule 3)."""
    body = _body(name)

    assert not re.search(r"\b(MUST|ALWAYS|MANDATORY|REQUIRED)\b", body), name
    assert "pycaret" not in body.lower(), name


def test_segmentation_carries_the_roi_policy_verbatim():
    assert ROI_IDENTITY_POLICY.strip() in _body("segmentation_choices")


def test_the_study_graph_names_real_agents():
    body = _body("research_study")
    named = set(re.findall(r"`([a-z_]+_agent)`", body))

    assert named == {
        "literature_review_agent", "study_population_planner_agent", "segmentation_agent",
        "feature_extraction_agent", "result_reporter_agent", "latex_reporter_agent",
    }  # fmt: skip
    assert named <= build().agents.names()


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
    templates = SKILLS_DIR.parent / "toolpacks" / "reporting" / "templates"
    body = _body("reporting")

    for name in ("one_page_abstract", "short_study", "ieee_research_paper"):
        assert f"`{name}`" in body
        assert (templates / f"{name}.tex").is_file(), name
