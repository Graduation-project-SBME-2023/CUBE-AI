"""Where the Claude Code CLI behind each SDK session keeps its files (Phase 9.1)."""

from __future__ import annotations

import pytest

import storage_paths
from storage_paths import (
    AGENT_CLI_CONFIG_ROOT,
    AGENT_CLI_CWD,
    CACHE_DIR,
    TEMP_DIR,
    ensure_storage_layout,
    new_agent_cli_config_dir,
)
from storage_write_guard import check_write

pytestmark = pytest.mark.unit


def test_one_fixed_working_directory_under_cache_and_config_dirs_under_temp():
    assert AGENT_CLI_CWD.parent == CACHE_DIR
    assert AGENT_CLI_CONFIG_ROOT.parent == TEMP_DIR


def test_the_layout_creates_both_trees():
    ensure_storage_layout()

    assert AGENT_CLI_CWD.is_dir()
    assert AGENT_CLI_CONFIG_ROOT.is_dir()


def test_every_session_gets_a_new_empty_config_dir():
    first, second = new_agent_cli_config_dir(), new_agent_cli_config_dir()

    assert first != second
    assert first.parent == second.parent == AGENT_CLI_CONFIG_ROOT
    assert list(first.iterdir()) == []


def test_the_write_guard_lets_the_cli_trees_be_written():
    config_dir = new_agent_cli_config_dir()
    for path in (AGENT_CLI_CWD / "notes.txt", config_dir / "projects" / "s.jsonl"):
        check_write(path)  # raises for a retired workspace path only
    assert "new_agent_cli_config_dir" in storage_paths.__all__
