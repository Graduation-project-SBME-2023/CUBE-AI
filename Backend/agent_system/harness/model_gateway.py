"""The Model Gateway: which model an SDK session uses (merged plan, Phase 9.2).

Every model is reached through the LiteLLM service. ``config/models.yaml``
names the aliases LiteLLM serves and the profiles that use them (``main``,
``composer``, ``recovery``, ``agent_default``, ``extraction``);
``options_for(profile, ctx)`` turns a profile into a ``ModelOptions``: the
alias as the session's model, its fallback, the effort, and the environment the
Claude Code CLI needs to talk to LiteLLM instead of Anthropic.

The user may pick another alias per profile in the model settings
(``/client-config``); the profile's own alias is the default. ``locality_filter``
is the hook PHI-Local fills (Plan 13.3): it limits a session to the aliases of
the localities it allows. It allows everything for now.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from ..toolkit.agents.spec import Effort
from ..toolkit.context import ToolContext
from ..toolkit.manifest import coerce_enum

logger = logging.getLogger(__name__)

DEFAULT_MODELS_FILE = Path(__file__).resolve().parent.parent / "config" / "models.yaml"
PROFILES: tuple[str, ...] = ("main", "composer", "recovery", "agent_default", "extraction")
# The CLI does not apply a compaction window below this (Phase 9.0 finding 5).
MIN_APPLIED_CONTEXT_WINDOW = 100_000


class Locality(StrEnum):
    LOCAL = "local"  # served on our own machines
    EXTERNAL = "external"  # anything else, Ollama's cloud models included


class ModelConfigError(ValueError):
    """``models.yaml`` is missing or malformed."""


class ModelUnavailable(LookupError):
    """No alias the session may use serves this profile (``code`` says why)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ModelAlias:
    name: str
    locality: Locality
    claude_native: bool = False
    context_window: int | None = None


@dataclass(frozen=True)
class ModelProfile:
    name: str
    alias: str
    fallback_alias: str | None = None
    effort: Effort | None = None


@dataclass(frozen=True)
class ModelConfig:
    aliases: Mapping[str, ModelAlias]
    profiles: Mapping[str, ModelProfile]


@dataclass(frozen=True, kw_only=True)
class ModelOptions:
    """What one SDK session needs: ``model`` and ``fallback_model`` are
    LiteLLM aliases, ``env`` goes into the CLI's environment."""

    model: str
    fallback_model: str | None = None
    effort: Effort | None = None
    env: Mapping[str, str] = field(default_factory=dict)


# Which localities a session may use; None allows all (Plan 13.3 fills it).
type LocalityFilter = Callable[[ToolContext], frozenset[Locality] | None]


def no_locality_filter(ctx: ToolContext) -> frozenset[Locality] | None:
    return None


def load_model_config(path: Path | None = None) -> ModelConfig:
    """The aliases and profiles of ``models.yaml``; read when called, never at import."""
    path = path or Path(os.environ.get("CUBEAI_MODELS_FILE", DEFAULT_MODELS_FILE))
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ModelConfigError(f"cannot read {path}: {exc}") from exc
    if not isinstance(document, dict) or document.get("v") != 1:
        raise ModelConfigError(f"{path}: expected a mapping with v: 1")
    aliases = {name: _alias(path, name, raw) for name, raw in _section(path, document, "aliases")}
    profiles = {name: _profile(path, name, raw) for name, raw in _section(path, document, "profiles")}
    missing = [name for name in PROFILES if name not in profiles]
    if missing:
        raise ModelConfigError(f"{path}: missing profiles {missing}")
    for profile in profiles.values():
        _check_profile(path, profile, aliases)
    return ModelConfig(MappingProxyType(aliases), MappingProxyType(profiles))


def _section(path: Path, document: Mapping[str, Any], key: str) -> list[tuple[str, Any]]:
    section = document.get(key)
    if not isinstance(section, dict) or not section:
        raise ModelConfigError(f"{path}: {key!r} must be a non-empty mapping")
    return list(section.items())


def _alias(path: Path, name: str, raw: Any) -> ModelAlias:
    if not isinstance(raw, dict):
        raise ModelConfigError(f"{path}: alias {name!r} must be a mapping")
    unknown = set(raw) - {"locality", "claude_native", "context_window"}
    if unknown:
        raise ModelConfigError(f"{path}: alias {name!r} has unknown fields {sorted(unknown)}")
    try:
        locality = coerce_enum(Locality, raw.get("locality"), "locality")
    except ValueError as exc:
        raise ModelConfigError(f"{path}: alias {name!r}: {exc}") from exc
    claude_native = raw.get("claude_native", False)
    window = raw.get("context_window")
    if not isinstance(claude_native, bool):
        raise ModelConfigError(f"{path}: alias {name!r}: claude_native must be true or false")
    if window is not None and (isinstance(window, bool) or not isinstance(window, int) or window < 1):
        raise ModelConfigError(f"{path}: alias {name!r}: context_window must be a positive integer")
    if window is not None and window < MIN_APPLIED_CONTEXT_WINDOW:
        logger.warning(
            "%s: alias %r has a %d-token context window; the CLI applies none below %d, "
            "so its sessions are compacted too late",
            path, name, window, MIN_APPLIED_CONTEXT_WINDOW,
        )
    return ModelAlias(name, locality, claude_native, window)


def _profile(path: Path, name: str, raw: Any) -> ModelProfile:
    if not isinstance(raw, dict) or not isinstance(raw.get("alias"), str):
        raise ModelConfigError(f"{path}: profile {name!r} needs an alias")
    unknown = set(raw) - {"alias", "fallback_alias", "effort"}
    if unknown:
        raise ModelConfigError(f"{path}: profile {name!r} has unknown fields {sorted(unknown)}")
    effort = raw.get("effort")
    try:
        effort = None if effort is None else coerce_enum(Effort, effort, "effort")
    except ValueError as exc:
        raise ModelConfigError(f"{path}: profile {name!r}: {exc}") from exc
    return ModelProfile(name, raw["alias"], raw.get("fallback_alias"), effort)


def _check_profile(path: Path, profile: ModelProfile, aliases: Mapping[str, ModelAlias]) -> None:
    for alias in (profile.alias, profile.fallback_alias):
        if alias is not None and alias not in aliases:
            raise ModelConfigError(f"{path}: profile {profile.name!r} names unknown alias {alias!r}")
    if profile.fallback_alias is not None and (
        aliases[profile.fallback_alias].locality is not aliases[profile.alias].locality
    ):
        raise ModelConfigError(
            f"{path}: profile {profile.name!r}: the fallback must have the alias's locality"
        )


class ModelGateway:
    """``options_for()`` for the engine. ``user_aliases`` returns the user's
    choices (profile -> alias) and is read on every call, so a change in the
    model settings applies to the next session."""

    def __init__(
        self,
        config: ModelConfig,
        *,
        base_url: str,
        api_key: str,
        user_aliases: Callable[[], Mapping[str, str]] = dict,
        locality_filter: LocalityFilter = no_locality_filter,
    ) -> None:
        self.config = config
        self.base_url = base_url
        self.api_key = api_key
        self.user_aliases = user_aliases
        self.locality_filter = locality_filter

    def options_for(self, profile: str, ctx: ToolContext) -> ModelOptions:
        spec = self.config.profiles.get(profile)
        if spec is None:
            raise ModelUnavailable("unknown_profile", f"no model profile {profile!r}")
        allowed = self.locality_filter(ctx)
        alias = self._chosen_alias(spec)
        if allowed is not None and alias.locality not in allowed:
            raise ModelUnavailable(
                "no_allowed_model",
                f"profile {profile!r}: alias {alias.name!r} is {alias.locality}, "
                f"this session may only use {sorted(allowed)}",
            )
        fallback = self._fallback(spec, alias, allowed)
        return ModelOptions(
            model=alias.name,
            fallback_model=fallback.name if fallback else None,
            effort=spec.effort,
            env=self._env(alias),
        )

    def _chosen_alias(self, profile: ModelProfile) -> ModelAlias:
        chosen = self.user_aliases().get(profile.name)
        if chosen is not None and chosen not in self.config.aliases:
            logger.warning("the chosen alias %r for %s is unknown; using %r",
                           chosen, profile.name, profile.alias)
            chosen = None
        return self.config.aliases[chosen or profile.alias]

    def _fallback(
        self, profile: ModelProfile, alias: ModelAlias, allowed: frozenset[Locality] | None
    ) -> ModelAlias | None:
        if profile.fallback_alias is None or profile.fallback_alias == alias.name:
            return None
        fallback = self.config.aliases[profile.fallback_alias]
        if fallback.locality is not alias.locality:  # the user's alias has another locality
            return None
        if allowed is not None and fallback.locality not in allowed:
            return None
        return fallback

    def _env(self, alias: ModelAlias) -> dict[str, str]:
        env = {
            "ANTHROPIC_BASE_URL": self.base_url,
            "ANTHROPIC_AUTH_TOKEN": self.api_key,
            # The CLI's own background requests (titles, summaries) use these
            # names; pointing them at the session's alias keeps every request
            # of the session on its model.
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": alias.name,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": alias.name,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": alias.name,
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        }
        if not alias.claude_native:
            env["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"] = "1"
            env["CLAUDE_CODE_ATTRIBUTION_HEADER"] = "0"
        if alias.context_window is not None:
            env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = str(alias.context_window)
        return env


def check_user_aliases(config: ModelConfig, chosen: Mapping[str, Any]) -> dict[str, str]:
    """The user's choices (profile -> alias), refused with a ``ValueError`` if
    a profile or an alias is unknown. An empty alias clears the choice."""
    if not isinstance(chosen, Mapping):
        raise ValueError("model_aliases must be an object of profile -> alias")
    checked: dict[str, str] = {}
    for profile, alias in chosen.items():
        if profile not in config.profiles:
            raise ValueError(f"unknown model profile {profile!r}")
        if alias in (None, ""):
            continue
        if alias not in config.aliases:
            raise ValueError(f"unknown model alias {alias!r} for profile {profile!r}")
        checked[profile] = alias
    return checked


def alias_choices(config: ModelConfig, chosen: Mapping[str, str]) -> list[dict[str, Any]]:
    """Per profile: its default alias, the alias in use and the aliases it may
    use, with their locality (the model settings screen)."""
    aliases = [
        {"name": alias.name, "locality": alias.locality.value}
        for alias in sorted(config.aliases.values(), key=lambda alias: alias.name)
    ]
    return [
        {
            "profile": name,
            "default_alias": profile.alias,
            "alias": chosen[name] if chosen.get(name) in config.aliases else profile.alias,
            "aliases": aliases,
        }
        for name, profile in config.profiles.items()
    ]


def _client_config_aliases() -> Mapping[str, str]:
    """The user's choices, from the same ``client_config`` module the
    ``/client-config`` route updates (it is imported bare)."""
    try:
        import client_config
    except ImportError:
        from .. import client_config
    return dict(client_config.get_client_config().model_aliases)


def model_gateway() -> ModelGateway:
    """The gateway for this process: ``models.yaml``, LiteLLM's address and key
    from ``LITELLM_BASE_URL`` / ``LITELLM_API_KEY``, and the user's choices."""
    base_url = os.environ.get("LITELLM_BASE_URL")
    api_key = os.environ.get("LITELLM_API_KEY")
    if not base_url or not api_key:
        raise ModelUnavailable(
            "gateway_not_configured", "LITELLM_BASE_URL and LITELLM_API_KEY must be set"
        )
    return ModelGateway(
        load_model_config(), base_url=base_url, api_key=api_key, user_aliases=_client_config_aliases
    )


@dataclass(frozen=True)
class GatewayCall:
    profile: str
    ctx: ToolContext


class FakeModelGateway:
    """A gateway for tests: each profile maps to a fixed alias (default
    ``fake-<profile>``), with no environment. Every call is recorded."""

    def __init__(self, models: Mapping[str, str] | None = None) -> None:
        self.models = dict(models or {})
        self.calls: list[GatewayCall] = []

    def options_for(self, profile: str, ctx: ToolContext) -> ModelOptions:
        self.calls.append(GatewayCall(profile, ctx))
        return ModelOptions(model=self.models.get(profile, f"fake-{profile}"))
