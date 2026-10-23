"""Skills: guidance text the deciding agents load when they need it (merged
plan, Phase 3.5; the loader the Main uses is Phase 12.6).

A skill is ``agent_system/skills/<name>.md``: a front matter with ``name``,
``description`` and ``applies_to``, and a Markdown body of guidance::

    ---
    name: modeling
    description: How to set up run_modeling for a radiomics study.
    applies_to: [main, composer]
    ---
    ...

``applies_to`` names audiences: ``main`` (the Main Agent) and ``composer``,
the two that decide what work to submit, or an agent type. A skill is
guidance only: nothing routes on it and nothing forces a workflow from it.
The Main sees the index (name and description) and loads a body with
``skill_load`` when it needs it, so the context holds only what is used.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .frontmatter import FrontMatterError, read_front_matter

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
SKILL_SUFFIX = ".md"
AUDIENCES = ("main", "composer")
SKILL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class SkillError(ValueError):
    """A skill file cannot be loaded."""

    def __init__(self, path: Path, problem: str) -> None:
        self.path = path
        self.problem = problem
        super().__init__(f"{path}: {problem}")


class SkillLibraryError(ValueError):
    """The skills cannot be loaded or are not valid; ``problems`` lists every reason."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(
            f"{len(problems)} skill problem(s):\n" + "\n".join(f"- {p}" for p in problems)
        )


class _FrontMatter(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    name: str = Field(pattern=SKILL_NAME_PATTERN.pattern)
    description: str = Field(min_length=1, max_length=300)
    applies_to: list[str] = Field(min_length=1)


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    applies_to: tuple[str, ...]
    body: str
    path: Path

    def entry(self) -> dict[str, str]:
        """What the index shows: the name and the one-line description."""
        return {"name": self.name, "description": self.description}


def load_skill(path: Path) -> Skill:
    """One skill file, checked on its own; ``SkillError`` names the file."""
    path = Path(path)
    try:
        data, body = read_front_matter(path)
    except (OSError, UnicodeDecodeError, FrontMatterError) as exc:
        raise SkillError(path, str(exc)) from None
    try:
        front = _FrontMatter.model_validate(data)
    except ValidationError as exc:
        raise SkillError(path, _describe(exc)) from None
    if front.name != path.stem:
        raise SkillError(path, f"name {front.name!r} must match the file name {path.stem!r}")
    if "\n" in front.description.strip():
        raise SkillError(path, "description must be one line")
    if len(set(front.applies_to)) != len(front.applies_to):
        raise SkillError(path, f"applies_to lists a name more than once: {front.applies_to}")
    if not body:
        raise SkillError(path, "the skill has no guidance text")
    return Skill(front.name, front.description.strip(), tuple(front.applies_to), body, path)


def _describe(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or 'front matter'}: {error['msg']}"
        for error in exc.errors()
    )


@dataclass(frozen=True)
class SkillLibrary:
    """Every skill, by name."""

    skills: dict[str, Skill]

    @classmethod
    def load(cls, directory: Path = SKILLS_DIR) -> SkillLibrary:
        """Every ``*.md`` in ``directory``; every broken file is reported at once."""
        skills: dict[str, Skill] = {}
        problems: list[str] = []
        for path in sorted(Path(directory).glob(f"*{SKILL_SUFFIX}")):
            try:
                skill = load_skill(path)
            except SkillError as exc:
                problems.append(str(exc))
                continue
            skills[skill.name] = skill
        if problems:
            raise SkillLibraryError(problems)
        return cls(skills)

    def __iter__(self) -> Iterator[Skill]:
        return iter(self.skills.values())

    def __len__(self) -> int:
        return len(self.skills)

    def get(self, name: str) -> Skill:
        try:
            return self.skills[name]
        except KeyError:
            raise KeyError(f"unknown skill {name!r}") from None

    def index(self, audience: str) -> list[dict[str, str]]:
        """The entries ``audience`` sees, by name."""
        ordered = sorted(self, key=lambda skill: skill.name)
        return [skill.entry() for skill in ordered if audience in skill.applies_to]

    def problems(self, agent_types: Iterable[str] = ()) -> list[str]:
        """``applies_to`` names that are neither an audience nor a known agent."""
        known = {*AUDIENCES, *agent_types}
        return [
            f"skill {skill.name!r}: applies_to names unknown {name!r}"
            for skill in self
            for name in skill.applies_to
            if name not in known
        ]

    def validate(self, agent_types: Iterable[str] = ()) -> None:
        if problems := self.problems(agent_types):
            raise SkillLibraryError(problems)


def describe(skill: Skill) -> dict[str, Any]:
    """A skill as ``skill_load`` returns it (Phase 12.2)."""
    return {**skill.entry(), "applies_to": list(skill.applies_to), "body": skill.body}
