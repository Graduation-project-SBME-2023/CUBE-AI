"""The shared front matter parser (merged plan, Phases 3.1 and 3.5)."""

from __future__ import annotations

import pytest

from agent_system.toolkit.frontmatter import FrontMatterError, read_front_matter, split_front_matter

pytestmark = pytest.mark.unit


def test_splits_the_mapping_from_the_body():
    data, body = split_front_matter("---\nname: modeling\napplies_to: [main]\n---\n\nAdvice.\n")

    assert data == {"name": "modeling", "applies_to": ["main"]}
    assert body == "Advice."


def test_windows_line_endings_and_a_bom_are_accepted(tmp_path):
    path = tmp_path / "agent.md"
    path.write_bytes("\ufeff---\r\nkind: execution\r\n---\r\nLine one.\r\nLine two.\r\n".encode())

    data, body = read_front_matter(path)

    assert data == {"kind": "execution"}
    assert body == "Line one.\nLine two."


def test_a_fence_inside_the_body_is_body_text():
    _, body = split_front_matter("---\na: 1\n---\nBefore.\n---\nAfter.")

    assert body == "Before.\n---\nAfter."


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("name: x\n---\nBody", "must start with a '---' line"),
        ("", "must start with a '---' line"),
        ("---\nname: x\nBody", "no closing '---' line"),
        ("---\n---\nBody", "must be a YAML mapping"),
        ("---\n- a\n- b\n---\nBody", "must be a YAML mapping"),
        ("---\njust text\n---\nBody", "must be a YAML mapping"),
        ("---\nname: [unclosed\n---\nBody", "not valid YAML"),
        ("---\nallowed_tools: [a]\nallowed_tools: [b]\n---\nBody", "duplicate key 'allowed_tools'"),
        ("---\nx: !!python/object/apply:os.system [echo]\n---\nBody", "not valid YAML"),
    ],
)
def test_malformed_front_matter_is_refused(text, match):
    with pytest.raises(FrontMatterError, match=match):
        split_front_matter(text)
