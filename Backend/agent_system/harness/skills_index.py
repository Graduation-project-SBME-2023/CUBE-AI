"""What ``/skills`` lists (merged plan, Phase 12.6).

The SDK loads the skills (Phases 3.5, 9.4): there is no loader of ours, no
index in any prompt and no ``skill_load`` tool. ``/skills`` is a Command,
answered without the LLM, and this module holds only its read: each
``SKILL.md`` of the ``cubeai`` plugin with its ``name`` and ``description``,
and the agents whose ``agent.md`` lists it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ..toolkit.agents.spec import AgentSpec
from ..toolkit.frontmatter import FrontMatterError, read_front_matter

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "skills" / "cubeai"
SKILL_FILE = "SKILL.md"


class SkillsIndexError(ValueError):
    """A ``SKILL.md`` cannot be read; ``problems`` lists every file at once."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(
            f"{len(problems)} skill problem(s):\n" + "\n".join(f"- {p}" for p in problems)
        )


@dataclass(frozen=True)
class SkillEntry:
    """One line of ``/skills``."""

    name: str
    description: str
    agents: tuple[str, ...]  # the agents whose agent.md lists the skill, by name

    def as_dict(self) -> dict[str, object]:
        return {"name": self.name, "description": self.description, "agents": list(self.agents)}


def skills_index(agents: Iterable[AgentSpec], plugin_dir: Path = PLUGIN_DIR) -> list[SkillEntry]:
    """Every skill of the plugin, by name, with the agents that list it.

    ``agents`` is usually ``catalog.agents``. A skill no agent lists is still
    shown, with no agents: it exists, nothing loads it yet.
    """
    users: dict[str, list[str]] = {}
    for agent in agents:
        for skill in agent.skills:
            users.setdefault(skill, []).append(agent.name)

    entries: list[SkillEntry] = []
    problems: list[str] = []
    for path in sorted(Path(plugin_dir).glob(f"skills/*/{SKILL_FILE}")):
        try:
            front, _body = read_front_matter(path)
        except (OSError, UnicodeDecodeError, FrontMatterError) as exc:
            problems.append(f"{path}: {exc}")
            continue
        name = path.parent.name
        description = front.get("description")
        if not isinstance(description, str) or not description.strip():
            problems.append(f"{path}: the front matter has no description")
            continue
        entries.append(SkillEntry(name, description.strip(), tuple(sorted(users.get(name, ())))))
    if problems:
        raise SkillsIndexError(problems)
    return entries
