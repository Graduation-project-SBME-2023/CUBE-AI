"""Markdown files with a YAML front matter (merged plan, Phases 3.1 and 3.5).

``agent.md`` files and skills share one shape: a ``---`` line, a YAML
mapping, a closing ``---`` line, then a Markdown body::

    ---
    agent_type: result_reporter_agent
    kind: execution
    ---
    You are ...

The YAML is read with a safe loader that also refuses a key given twice: a
plain ``yaml.safe_load`` keeps the last value silently, so a second
``allowed_tools:`` line would replace the first without a word.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

FENCE = "---"
BOM = chr(0xFEFF)  # some Windows editors start UTF-8 files with it


class FrontMatterError(ValueError):
    """The file has no front matter, or its front matter is not a YAML mapping."""


class _UniqueKeyLoader(yaml.SafeLoader):
    """``SafeLoader`` that refuses duplicate mapping keys."""


def _mapping_without_duplicates(loader: _UniqueKeyLoader, node: yaml.MappingNode) -> Any:
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key {key!r}", key_node.start_mark
            )
        seen.add(key)
    return loader.construct_mapping(node)


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping_without_duplicates
)


def split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """Split ``text`` into its front matter mapping and its body (stripped)."""
    lines = text.removeprefix(BOM).replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != FENCE:
        raise FrontMatterError(f"must start with a {FENCE!r} line")
    try:
        end = next(i for i, line in enumerate(lines[1:], start=1) if line.strip() == FENCE)
    except StopIteration:
        raise FrontMatterError(f"front matter has no closing {FENCE!r} line") from None

    try:
        data = yaml.load("\n".join(lines[1:end]), Loader=_UniqueKeyLoader)  # noqa: S506 - SafeLoader subclass
    except yaml.YAMLError as exc:
        raise FrontMatterError(f"front matter is not valid YAML: {exc}") from None
    if not isinstance(data, dict):
        raise FrontMatterError("front matter must be a YAML mapping of fields")
    return data, "\n".join(lines[end + 1 :]).strip()


def read_front_matter(path: Path) -> tuple[dict[str, Any], str]:
    """``split_front_matter`` on a UTF-8 file."""
    return split_front_matter(path.read_text(encoding="utf-8"))
