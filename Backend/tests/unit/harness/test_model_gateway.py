"""The Model Gateway and config/models.yaml (merged plan, Phase 9.2)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent_system.harness.model_gateway import (
    PROFILES,
    FakeModelGateway,
    Locality,
    ModelConfigError,
    ModelGateway,
    ModelUnavailable,
    alias_choices,
    check_user_aliases,
    load_model_config,
    model_gateway,
)
from agent_system.toolkit.context import ToolContext

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[4]
CTX = ToolContext(session_id="s1", caller="agent", agent_type="segmentation_agent")
BASE = {
    "v": 1,
    "aliases": {
        "ext-a": {"locality": "external", "claude_native": False, "context_window": 131072},
        "ext-b": {"locality": "external", "claude_native": True},
        "loc-a": {"locality": "local", "context_window": 131072},
    },
    "profiles": {name: {"alias": "ext-a"} for name in PROFILES},
}


def _config(tmp_path: Path, document: dict | None = None):
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(document or BASE), encoding="utf-8")
    return load_model_config(path)


def _gateway(tmp_path: Path, document: dict | None = None, **kwargs) -> ModelGateway:
    return ModelGateway(_config(tmp_path, document), base_url="http://litellm:4000",
                        api_key="sk-test", **kwargs)  # fmt: skip


def _with(**profiles) -> dict:
    return BASE | {"profiles": BASE["profiles"] | profiles}


# --- the shipped configuration


def test_the_shipped_models_yaml_loads_with_every_profile():
    config = load_model_config()

    assert set(PROFILES) <= set(config.profiles)
    for profile in config.profiles.values():
        assert profile.alias in config.aliases


def test_every_alias_is_served_by_the_litellm_config():
    litellm = yaml.safe_load((REPO / "deploy" / "litellm" / "config.yaml").read_text(encoding="utf-8"))
    served = {entry["model_name"] for entry in litellm["model_list"]}

    assert set(load_model_config().aliases) <= served


def test_gpt_oss_cloud_counts_as_external():
    """It runs on Ollama's cloud (merged plan, decision 14)."""
    config = load_model_config()

    assert config.aliases["cubeai-gpt-oss"].locality is Locality.EXTERNAL


# --- options_for


def test_a_non_claude_alias_gets_the_gateway_env_and_its_context_window(tmp_path):
    options = _gateway(tmp_path).options_for("agent_default", CTX)

    assert (options.model, options.fallback_model, options.effort) == ("ext-a", None, None)
    assert options.env == {
        "ANTHROPIC_BASE_URL": "http://litellm:4000",
        "ANTHROPIC_AUTH_TOKEN": "sk-test",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "ext-a",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "ext-a",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "ext-a",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
        "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "131072",
    }


def test_a_claude_alias_keeps_the_betas_and_the_attribution_header(tmp_path):
    options = _gateway(tmp_path, _with(main={"alias": "ext-b"})).options_for("main", CTX)

    assert options.model == "ext-b"
    assert not {"CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS", "CLAUDE_CODE_ATTRIBUTION_HEADER",
                "CLAUDE_CODE_AUTO_COMPACT_WINDOW"} & options.env.keys()  # fmt: skip


def test_the_fallback_and_the_effort_come_from_the_profile(tmp_path):
    document = _with(main={"alias": "ext-a", "fallback_alias": "ext-b", "effort": "high"})

    options = _gateway(tmp_path, document).options_for("main", CTX)

    assert (options.fallback_model, options.effort) == ("ext-b", "high")


def test_the_users_alias_replaces_the_profiles_and_drops_a_fallback_of_another_locality(tmp_path):
    document = _with(main={"alias": "ext-a", "fallback_alias": "ext-b"})
    gateway = _gateway(tmp_path, document, user_aliases=lambda: {"main": "loc-a"})

    options = gateway.options_for("main", CTX)

    assert (options.model, options.fallback_model) == ("loc-a", None)
    assert options.env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "loc-a"


def test_an_unknown_user_alias_falls_back_to_the_profiles(tmp_path):
    gateway = _gateway(tmp_path, user_aliases=lambda: {"main": "gone"})

    assert gateway.options_for("main", CTX).model == "ext-a"


def test_the_user_choice_is_read_on_every_call(tmp_path):
    chosen: dict[str, str] = {}
    gateway = _gateway(tmp_path, user_aliases=lambda: chosen)

    assert gateway.options_for("main", CTX).model == "ext-a"
    chosen["main"] = "ext-b"
    assert gateway.options_for("main", CTX).model == "ext-b"


def test_an_unknown_profile_is_refused(tmp_path):
    with pytest.raises(ModelUnavailable) as caught:
        _gateway(tmp_path).options_for("summarizer", CTX)

    assert caught.value.code == "unknown_profile"


def test_the_locality_filter_limits_the_aliases(tmp_path):
    """The hook PHI-Local fills (Plan 13.3)."""
    local_only = lambda ctx: frozenset({Locality.LOCAL})  # noqa: E731
    document = _with(main={"alias": "loc-a"}, agent_default={"alias": "ext-a"})
    gateway = _gateway(tmp_path, document, locality_filter=local_only)

    assert gateway.options_for("main", CTX).model == "loc-a"
    with pytest.raises(ModelUnavailable) as caught:
        gateway.options_for("agent_default", CTX)
    assert caught.value.code == "no_allowed_model"


# --- models.yaml validation


@pytest.mark.parametrize(
    ("document", "match"),
    [
        ({"v": 2}, "v: 1"),
        (BASE | {"aliases": {}}, "'aliases' must be a non-empty mapping"),
        (BASE | {"profiles": {"main": {"alias": "ext-a"}}}, "missing profiles"),
        (_with(main={"alias": "nope"}), "unknown alias 'nope'"),
        (_with(main={"alias": "ext-a", "fallback_alias": "loc-a"}), "fallback must have the alias's locality"),
        (_with(main={"alias": "ext-a", "effort": "huge"}), "effort="),
        (_with(main={"alias": "ext-a", "summarize": True}), "unknown fields"),
        (BASE | {"aliases": {"x": {"locality": "nearby"}}}, "locality="),
        (BASE | {"aliases": {"x": {"locality": "local", "context_window": 0}}}, "positive integer"),
    ],
)
def test_a_malformed_models_yaml_is_refused(tmp_path, document, match):
    with pytest.raises(ModelConfigError, match=match):
        _config(tmp_path, document)


def test_a_window_the_cli_does_not_apply_is_logged(tmp_path, caplog):
    aliases = BASE["aliases"] | {"ext-a": {"locality": "external", "context_window": 32768}}

    _config(tmp_path, BASE | {"aliases": aliases})

    assert "compacted too late" in caplog.text


# --- the user's choices (/client-config)


def test_user_choices_are_checked_and_an_empty_alias_clears_one(tmp_path):
    config = _config(tmp_path)

    assert check_user_aliases(config, {"main": "ext-b", "composer": ""}) == {"main": "ext-b"}
    with pytest.raises(ValueError, match="unknown model profile"):
        check_user_aliases(config, {"planner": "ext-a"})
    with pytest.raises(ValueError, match="unknown model alias"):
        check_user_aliases(config, {"main": "gpt-9"})


def test_the_choices_screen_lists_each_profile_with_its_aliases(tmp_path):
    rows = alias_choices(_config(tmp_path), {"main": "ext-b"})
    main = next(row for row in rows if row["profile"] == "main")

    assert (main["alias"], main["default_alias"]) == ("ext-b", "ext-a")
    assert {"name": "loc-a", "locality": "local"} in main["aliases"]
    assert {row["profile"] for row in rows} == set(PROFILES)


# --- the process gateway and the fake


def test_the_process_gateway_needs_litellm_address_and_key(monkeypatch):
    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    monkeypatch.setenv("LITELLM_API_KEY", "sk-test")

    with pytest.raises(ModelUnavailable) as caught:
        model_gateway()
    assert caught.value.code == "gateway_not_configured"


def test_the_process_gateway_reads_the_env_and_the_shipped_config(monkeypatch):
    monkeypatch.setenv("LITELLM_BASE_URL", "http://litellm:4000")
    monkeypatch.setenv("LITELLM_API_KEY", "sk-test")

    options = model_gateway().options_for("main", CTX)

    assert options.env["ANTHROPIC_BASE_URL"] == "http://litellm:4000"
    assert options.model in load_model_config().aliases


def test_the_fake_gateway_records_calls():
    fake = FakeModelGateway({"main": "cubeai-test"})

    assert fake.options_for("main", CTX).model == "cubeai-test"
    assert fake.options_for("composer", CTX).model == "fake-composer"
    assert [call.profile for call in fake.calls] == ["main", "composer"]
