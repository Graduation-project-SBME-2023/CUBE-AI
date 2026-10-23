"""What a run freezes about its engine and its agent (Phase 9.1)."""

from __future__ import annotations

import re

import pytest

from agent_system.toolkit.engines.versions import engine_version, spec_digest

pytestmark = pytest.mark.unit

SPEC = {"prompt": "Count the cohort.", "tool_names": ["get_cohort_size", "ask_user"],
        "delegates": ["mask_resolution_agent"], "version": 1}  # fmt: skip


def test_the_engine_version_names_the_sdk_and_its_cli():
    assert re.fullmatch(r"claude-agent-sdk \d+\.\d+\.\d+ / cli \d+\.\d+\.\d+", engine_version())


def test_the_digest_is_stable_and_ignores_the_order_of_tools_and_delegates():
    reordered = SPEC | {"tool_names": ["ask_user", "get_cohort_size", "ask_user"]}

    assert spec_digest(**SPEC) == spec_digest(**reordered)
    assert re.fullmatch(r"[0-9a-f]{64}", spec_digest(**SPEC))


@pytest.mark.parametrize(
    "change",
    [
        {"prompt": "Count the cohort twice."},
        {"tool_names": ["get_cohort_size"]},
        {"delegates": []},
        {"version": 2},
    ],
)
def test_any_change_to_what_the_agent_ran_with_changes_the_digest(change):
    assert spec_digest(**SPEC | change) != spec_digest(**SPEC)
