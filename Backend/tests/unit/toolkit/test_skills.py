"""The skill format and library (merged plan, Phase 3.5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_system.toolkit.skills import (
    SkillError,
    SkillLibrary,
    SkillLibraryError,
    describe,
    load_skill,
)

pytestmark = pytest.mark.unit

MODELING = """\
---
name: modeling
description: How to set up run_modeling.
applies_to: [main, composer]
---
Pick the target first.
"""


def _write(directory: Path, name: str, text: str) -> Path:
    path = directory / f"{name}.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_skill(tmp_path):
    skill = load_skill(_write(tmp_path, "modeling", MODELING))

    assert (skill.name, skill.description) == ("modeling", "How to set up run_modeling.")
    assert skill.applies_to == ("main", "composer")
    assert skill.body == "Pick the target first."
    assert describe(skill) == {
        "name": "modeling",
        "description": "How to set up run_modeling.",
        "applies_to": ["main", "composer"],
        "body": "Pick the target first.",
    }


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        ("name: modeling", "name: Modeling", "name: String should match pattern"),
        ("name: modeling", "name: model_setup", "must match the file name 'modeling'"),
        ("applies_to: [main, composer]", "applies_to: []", "applies_to: List should have at least 1 item"),
        ("applies_to: [main, composer]", "applies_to: main", "applies_to: Input should be a valid list"),
        ("applies_to: [main, composer]", "applies_to: [main, main]", "more than once"),
        ("applies_to: [main, composer]", "applies_to: [main]\nroutes_to: [composer]", "routes_to: Extra inputs"),
        ("description: How to set up run_modeling.\n", "", "description: Field required"),
        ("description: How to set up run_modeling.", "description: |\n  Line one.\n  Line two.", "one line"),
        ("Pick the target first.\n", "", "no guidance text"),
    ],
)  # fmt: skip
def test_a_bad_skill_is_refused_with_its_file(tmp_path, old, new, match):
    path = _write(tmp_path, "modeling", MODELING.replace(old, new))

    with pytest.raises(SkillError, match=match) as caught:
        load_skill(path)

    assert caught.value.path == path


def test_the_library_indexes_by_audience(tmp_path):
    _write(tmp_path, "modeling", MODELING)
    _write(tmp_path, "cohort_building", MODELING.replace("name: modeling", "name: cohort_building")
           .replace("[main, composer]", "[composer, study_population_planner_agent]"))  # fmt: skip
    (tmp_path / "notes.txt").write_text("not a skill", encoding="utf-8")

    library = SkillLibrary.load(tmp_path)

    assert len(library) == 2
    assert [entry["name"] for entry in library.index("composer")] == ["cohort_building", "modeling"]
    assert [entry["name"] for entry in library.index("main")] == ["modeling"]
    assert library.get("modeling").body == "Pick the target first."
    with pytest.raises(KeyError, match="unknown skill 'segmentation'"):
        library.get("segmentation")


def test_applies_to_must_name_an_audience_or_a_known_agent(tmp_path):
    _write(tmp_path, "modeling", MODELING.replace("[main, composer]", "[main, modeling_agent]"))
    library = SkillLibrary.load(tmp_path)

    assert library.problems(agent_types=["segmentation_agent"]) == [
        "skill 'modeling': applies_to names unknown 'modeling_agent'"
    ]
    with pytest.raises(SkillLibraryError):
        library.validate(agent_types=["segmentation_agent"])
    library.validate(agent_types=["modeling_agent"])


def test_every_broken_skill_is_reported_at_once(tmp_path):
    _write(tmp_path, "modeling", "no front matter")
    _write(tmp_path, "reporting", MODELING)  # its name says modeling

    with pytest.raises(SkillLibraryError) as caught:
        SkillLibrary.load(tmp_path)

    assert len(caught.value.problems) == 2
