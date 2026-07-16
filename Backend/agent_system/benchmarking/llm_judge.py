"""
LLM-as-Judge Evaluation Module with Custom Rubrics Support.

This module provides utilities for LLM-based evaluation of workspace outputs,
supporting custom rubrics defined in configuration files.

Supports two modes:
1. AutoGen client mode (recommended): Uses autogen_ext.models.openai.OpenAIChatCompletionClient
   with Ollama's OpenAI-compatible /v1 endpoint (same as rest of project)
2. Raw HTTP mode: Direct HTTP requests for compatibility (falls back to /api/chat if /v1 fails)

Usage:
    from benchmarking.llm_judge import JudgeConfig, RubricConfig, evaluate_with_rubric

    # Using AutoGen client (recommended for Ollama)
    config = JudgeConfig(model="qwen2.5-coder:7b", use_autogen=True)
    result = evaluate_with_rubric(config, rubric, artifacts)

    # Using raw HTTP
    config = JudgeConfig(model="gpt-4", base_url="https://api.openai.com/v1", api_key="...")
    result = evaluate_with_rubric(config, rubric, artifacts)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


_AUTOGEN_LOOP: Optional[asyncio.AbstractEventLoop] = None
_AUTOGEN_LOOP_THREAD: Optional[threading.Thread] = None
_AUTOGEN_LOOP_LOCK = threading.Lock()


def _parse_env_file(env_path: Path) -> Dict[str, str]:
    """Parse a simple .env file into key/value pairs."""
    parsed: Dict[str, str] = {}
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return parsed

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if not key:
            continue

        # Strip optional surrounding single/double quotes.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]

        parsed[key] = value

    return parsed


def _discover_env_files() -> List[Path]:
    """Discover candidate .env files from cwd and parent folders."""
    candidates: List[Path] = []
    cwd = Path.cwd().resolve()

    # Prioritize closer files first: cwd/.env then parent/.env ...
    for directory in [cwd, *cwd.parents]:
        env_file = directory / ".env"
        if env_file.exists():
            candidates.append(env_file)

    # Optional local override files at cwd.
    for name in (".env.local",):
        env_file = cwd / name
        if env_file.exists():
            candidates.insert(0, env_file)

    # De-duplicate while preserving order.
    seen = set()
    deduped: List[Path] = []
    for path in candidates:
        resolved = str(path)
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(path)
    return deduped


def _load_env_into_process() -> None:
    """Load environment variables from discovered .env files without overriding existing vars."""
    loaded_files: List[str] = []
    for env_file in _discover_env_files():
        parsed = _parse_env_file(env_file)
        if not parsed:
            continue
        for key, value in parsed.items():
            os.environ.setdefault(key, value)
        loaded_files.append(str(env_file))

    if loaded_files:
        print(f"Loaded env vars from: {', '.join(loaded_files)}")


@dataclass
class JudgeConfig:
    """Configuration for the LLM judge."""

    model: str
    base_url: str = "http://localhost:11434"
    api_key: str = "ollama"
    temperature: float = 0.0
    timeout_seconds: int = 180
    max_tokens: Optional[int] = None
    use_autogen: bool = True  # Use AutoGen client instead of raw HTTP
    fallback_models: List[str] = field(default_factory=list)


@dataclass
class RubricDimension:
    """A single evaluation dimension within a rubric."""

    name: str
    description: str
    weight: float
    criteria: List[str]


@dataclass
class RubricConfig:
    """Configuration for an evaluation rubric."""

    name: str
    description: str
    weight: float
    dimensions: Dict[str, RubricDimension]
    scoring_guide: Dict[str, str]

    @classmethod
    def from_dict(cls, name: str, data: Dict[str, Any]) -> "RubricConfig":
        """Create a RubricConfig from a dictionary."""
        dimensions = {}
        for dim_name, dim_data in data.get("dimensions", {}).items():
            dimensions[dim_name] = RubricDimension(
                name=dim_name,
                description=dim_data.get("description", ""),
                weight=dim_data.get("weight", 1.0),
                criteria=dim_data.get("criteria", []),
            )

        return cls(
            name=data.get("name", name),
            description=data.get("description", ""),
            weight=data.get("weight", 1.0),
            dimensions=dimensions,
            scoring_guide=data.get("scoring_guide", {}),
        )


@dataclass
class EvaluationResult:
    """Result of an LLM evaluation."""

    ok: bool
    error: Optional[str] = None
    overall_score: Optional[float] = None
    rubric_scores: Dict[str, float] = field(default_factory=dict)
    dimension_scores: Dict[str, Dict[str, float]] = field(default_factory=dict)
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    verdict: str = ""
    raw_content: str = ""
    usage: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "ok": self.ok,
            "error": self.error,
            "overall_score": self.overall_score,
            "rubric_scores": self.rubric_scores,
            "dimension_scores": self.dimension_scores,
            "strengths": self.strengths,
            "weaknesses": self.weaknesses,
            "recommendations": self.recommendations,
            "verdict": self.verdict,
            "raw_content": self.raw_content,
            "usage": self.usage,
        }


def approx_tokens_from_text(text: str) -> int:
    """Rough token approximation for fallback accounting when usage is missing."""
    if not text:
        return 0
    # Rule-of-thumb for English text and JSON payloads.
    return max(1, int(round(len(text) / 4)))


def _extract_usage(usage_payload: Any) -> Dict[str, int]:
    """Normalize usage payload variants to prompt/completion/total token fields."""
    usage = usage_payload or {}

    def _get(obj: Any, keys: List[str], default: int = 0) -> int:
        for key in keys:
            if isinstance(obj, dict) and key in obj and obj[key] is not None:
                try:
                    return int(obj[key])
                except (TypeError, ValueError):
                    pass
            if hasattr(obj, key):
                val = getattr(obj, key)
                if val is not None:
                    try:
                        return int(val)
                    except (TypeError, ValueError):
                        pass
        return default

    prompt_tokens = _get(usage, ["prompt_tokens", "input_tokens", "prompt_eval_count"])
    completion_tokens = _get(
        usage, ["completion_tokens", "output_tokens", "eval_count"]
    )
    total_tokens = _get(usage, ["total_tokens"])
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens

    return {
        "prompt_tokens": max(0, prompt_tokens),
        "completion_tokens": max(0, completion_tokens),
        "total_tokens": max(0, total_tokens),
    }


def _parse_json_loose(content: str) -> Optional[Dict[str, Any]]:
    """Parse judge JSON with tolerant fallbacks for fenced output."""
    if not content:
        return None

    candidates = [content.strip()]
    fence_match = re.search(r"```json\s*(\{.*\})\s*```", content, re.DOTALL)
    if fence_match:
        candidates.append(fence_match.group(1).strip())
    brace_match = re.search(r"\{.*\}", content, re.DOTALL)
    if brace_match:
        candidates.append(brace_match.group(0).strip())

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    return None


def _normalize_base_url(base_url: str) -> str:
    url = base_url.rstrip("/")
    if url.endswith("/v1"):
        return url
    if url.endswith("/v1/"):
        return url[:-1]
    return f"{url}/v1"


def _normalize_model_for_autogen(model_name: str) -> str:
    """Normalize cloud aliases for AutoGen model mapping to avoid mismatch warnings."""
    normalized = (model_name or "").strip()
    if normalized.endswith("-cloud"):
        return normalized[: -len("-cloud")]
    return normalized


def _score_band(score: Optional[float]) -> str:
    """Human-friendly score interpretation used in markdown reports."""
    if score is None:
        return "N/A"
    if score >= 0.9:
        return "excellent"
    if score >= 0.75:
        return "strong"
    if score >= 0.6:
        return "adequate"
    if score >= 0.4:
        return "needs improvement"
    return "poor"


def _extract_content(response_json: Dict[str, Any]) -> str:
    """Extract chat content from OpenAI-compatible or Ollama-native responses."""
    # OpenAI-compatible shape
    choices = (
        response_json.get("choices", []) if isinstance(response_json, dict) else []
    )
    if choices:
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        if isinstance(message, dict):
            content = message.get("content", "")
            if isinstance(content, str):
                return content

    # Ollama-native shape: {"message": {"role": "assistant", "content": "..."}}
    message = (
        response_json.get("message", {}) if isinstance(response_json, dict) else {}
    )
    if isinstance(message, dict):
        content = message.get("content", "")
        if isinstance(content, str):
            return content

    return ""


def _post_chat(
    config: JudgeConfig,
    messages: List[Dict[str, str]],
) -> Dict[str, Any]:
    """
    Post a chat request.

    Tries OpenAI-compatible /v1/chat/completions first, then falls back to
    Ollama-native /api/chat when /v1 returns 404.
    """
    normalized = _normalize_base_url(config.base_url)
    endpoint_v1 = f"{normalized}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"

    payload_v1: Dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "temperature": config.temperature,
    }
    if config.max_tokens:
        payload_v1["max_tokens"] = config.max_tokens

    request_v1 = urllib.request.Request(
        endpoint_v1,
        data=json.dumps(payload_v1).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request_v1, timeout=config.timeout_seconds
        ) as response:
            body = response.read().decode("utf-8")
        parsed = json.loads(body)
        return {
            "ok": True,
            "response_json": parsed,
            "api_format": "openai-compatible",
        }
    except urllib.error.HTTPError as exc:
        # Common on older Ollama versions or when OpenAI compatibility is disabled.
        if exc.code != 404:
            return {
                "ok": False,
                "error": f"Judge request failed: HTTPError: {exc}",
            }
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "error": f"Judge request failed: {type(exc).__name__}: {exc}",
        }

    # Fallback: Ollama native API.
    base_no_v1 = normalized[:-3] if normalized.endswith("/v1") else normalized
    endpoint_ollama = f"{base_no_v1}/api/chat"
    payload_ollama: Dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": config.temperature,
        },
    }

    request_ollama = urllib.request.Request(
        endpoint_ollama,
        data=json.dumps(payload_ollama).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request_ollama, timeout=config.timeout_seconds
        ) as response:
            body = response.read().decode("utf-8")
        parsed = json.loads(body)

        # Normalize Ollama usage fields into a usage-like object.
        if isinstance(parsed, dict):
            parsed["usage"] = {
                "prompt_eval_count": parsed.get("prompt_eval_count", 0),
                "eval_count": parsed.get("eval_count", 0),
                "total_tokens": int(parsed.get("prompt_eval_count", 0))
                + int(parsed.get("eval_count", 0)),
            }

        return {
            "ok": True,
            "response_json": parsed,
            "api_format": "ollama-native",
        }
    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
    ) as exc:
        return {
            "ok": False,
            "error": f"Judge request failed: {type(exc).__name__}: {exc}",
        }


async def _post_chat_autogen(
    config: JudgeConfig,
    messages: List[Dict[str, str]],
) -> Dict[str, Any]:
    """
    Post a chat request using AutoGen's OpenAIChatCompletionClient.

    This uses the OpenAI-compatible endpoint that Ollama exposes at /v1,
    which is the same approach used by the rest of this project.
    """
    client = None
    try:
        from autogen_ext.models.openai import OpenAIChatCompletionClient
        from autogen_core.models import UserMessage, SystemMessage, AssistantMessage
    except ImportError:
        return {
            "ok": False,
            "error": "autogen_ext.models.openai not available. Install autogen-ext.",
        }

    try:
        # Create client using OpenAI-compatible endpoint
        model_info = {
            "vision": False,
            "function_calling": False,
            "json_output": True,
            "structured_output": False,
            "family": "unknown",
        }

        # Normalize base URL - ensure /v1 suffix is present
        base_url = config.base_url.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"

        autogen_model = _normalize_model_for_autogen(config.model)

        client = OpenAIChatCompletionClient(
            model=autogen_model,
            model_info=model_info,
            base_url=base_url,
            api_key=config.api_key or "ollama",
            temperature=config.temperature,
        )

        # Convert messages to AutoGen format
        autogen_messages = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                autogen_messages.append(SystemMessage(content=content))
            elif role == "assistant":
                autogen_messages.append(
                    AssistantMessage(content=content, source="assistant")
                )
            else:
                autogen_messages.append(UserMessage(content=content, source="user"))

        # Make the request
        response = await client.create(autogen_messages)

        # Extract content
        content = ""
        if hasattr(response, "content"):
            content = (
                response.content
                if isinstance(response.content, str)
                else str(response.content)
            )

        # Extract usage
        usage = {}
        if hasattr(response, "usage"):
            usage_obj = response.usage
            if hasattr(usage_obj, "prompt_tokens"):
                usage["prompt_tokens"] = usage_obj.prompt_tokens
            if hasattr(usage_obj, "completion_tokens"):
                usage["completion_tokens"] = usage_obj.completion_tokens
            if hasattr(usage_obj, "total_tokens"):
                usage["total_tokens"] = usage_obj.total_tokens

        return {
            "ok": True,
            "response_json": {
                "choices": [{"message": {"content": content}}],
                "usage": usage,
            },
            "api_format": "autogen-openai",
            "model_used": autogen_model,
        }

    except Exception as exc:
        return {
            "ok": False,
            "error": f"AutoGen request failed: {type(exc).__name__}: {exc}",
        }
    finally:
        # Explicitly close the model client before the loop ends to avoid
        # "Event loop is closed" errors from deferred transport cleanup.
        if client is not None:
            try:
                close_result = None
                if hasattr(client, "close"):
                    close_result = client.close()
                elif hasattr(client, "aclose"):
                    close_result = client.aclose()

                if asyncio.iscoroutine(close_result):
                    await close_result
            except Exception:
                pass


def _ensure_autogen_loop() -> asyncio.AbstractEventLoop:
    """Create a single background event loop used for sync AutoGen calls."""
    global _AUTOGEN_LOOP, _AUTOGEN_LOOP_THREAD

    with _AUTOGEN_LOOP_LOCK:
        if _AUTOGEN_LOOP is not None and _AUTOGEN_LOOP.is_running():
            return _AUTOGEN_LOOP

        loop = asyncio.new_event_loop()

        def _run_loop() -> None:
            asyncio.set_event_loop(loop)
            loop.run_forever()

        thread = threading.Thread(target=_run_loop, name="llm-judge-autogen-loop", daemon=True)
        thread.start()

        _AUTOGEN_LOOP = loop
        _AUTOGEN_LOOP_THREAD = thread
        return loop


def _run_autogen_sync(config: JudgeConfig, messages: List[Dict[str, str]]) -> Dict[str, Any]:
    """Run async AutoGen request from sync code on a persistent event loop."""
    loop = _ensure_autogen_loop()
    future = asyncio.run_coroutine_threadsafe(
        _post_chat_autogen(config, messages),
        loop,
    )
    return future.result(timeout=config.timeout_seconds + 5)


def _post_chat_sync(
    config: JudgeConfig,
    messages: List[Dict[str, str]],
) -> Dict[str, Any]:
    """
    Synchronous wrapper for chat requests.

    Uses AutoGen client if use_autogen=True and available, otherwise falls back to raw HTTP.
    The raw HTTP implementation tries OpenAI-compatible /v1/chat/completions first,
    then falls back to Ollama's native /api/chat endpoint.
    """
    if config.use_autogen:
        # Try AutoGen first
        try:
            # Check if AutoGen OpenAI is available before attempting async call
            from autogen_ext.models.openai import OpenAIChatCompletionClient  # noqa: F401

            result = _run_autogen_sync(config, messages)

            # If AutoGen succeeded, return result
            if result.get("ok"):
                result["model"] = result.get("model_used", config.model)
                return result

            # If AutoGen failed for non-import reasons, fall back to raw HTTP
            error_msg = result.get("error", "")
            normalized_error = error_msg.lower()
            # Missing-model errors are expected during model fallback probing; avoid noisy logs.
            is_model_not_found = (
                "notfounderror" in normalized_error
                or "model \"" in normalized_error and "not found" in normalized_error
                or "model '" in normalized_error and "not found" in normalized_error
                or "404" in normalized_error and "not found" in normalized_error
            )

            if "not available" not in normalized_error and not is_model_not_found:
                # Log the AutoGen error and try raw HTTP
                print(f"AutoGen request failed, falling back to raw HTTP: {error_msg}")

        except ImportError:
            # AutoGen not installed, fall back to raw HTTP silently
            pass

    # Use raw HTTP (tries /v1/chat/completions first, then /api/chat)
    result = _post_chat(config, messages)
    if result.get("ok"):
        result["model"] = config.model
    return result


def _candidate_models(config: JudgeConfig) -> List[str]:
    """Build ordered model candidates, including cloud/local alias fallbacks."""
    ordered: List[str] = [config.model]
    ordered.extend(config.fallback_models or [])

    aliases: List[str] = []
    for model_name in list(ordered):
        if model_name.endswith("-cloud"):
            aliases.append(model_name[: -len("-cloud")])
        else:
            aliases.append(f"{model_name}-cloud")

    ordered.extend(aliases)

    deduped: List[str] = []
    seen = set()
    for model_name in ordered:
        normalized = (model_name or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _post_chat_with_fallbacks(
    config: JudgeConfig,
    messages: List[Dict[str, str]],
) -> Dict[str, Any]:
    """Try configured model candidates in order until one succeeds."""
    errors: List[str] = []
    for model_name in _candidate_models(config):
        attempt_config = JudgeConfig(
            model=model_name,
            base_url=config.base_url,
            api_key=config.api_key,
            temperature=config.temperature,
            timeout_seconds=config.timeout_seconds,
            max_tokens=config.max_tokens,
            use_autogen=config.use_autogen,
            fallback_models=[],
        )
        result = _post_chat_sync(attempt_config, messages)
        if result.get("ok"):
            result["tried_models"] = _candidate_models(config)
            return result
        errors.append(f"{model_name}: {result.get('error', 'unknown error')}")

    return {
        "ok": False,
        "error": "All model candidates failed. " + " | ".join(errors),
        "tried_models": _candidate_models(config),
    }


def load_rubrics(rubrics_path: str | Path) -> Dict[str, RubricConfig]:
    """
    Load evaluation rubrics from a JSON configuration file.

    Args:
        rubrics_path: Path to the rubrics JSON file

    Returns:
        Dict mapping rubric names to RubricConfig objects
    """
    path = Path(rubrics_path)
    if not path.exists():
        raise FileNotFoundError(f"Rubrics file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    rubrics = {}
    for name, rubric_data in data.get("rubrics", {}).items():
        rubrics[name] = RubricConfig.from_dict(name, rubric_data)

    return rubrics


def get_preset_rubrics(
    rubrics_path: str | Path,
    preset_name: str,
) -> List[str]:
    """
    Get the list of rubric names for a preset.

    Args:
        rubrics_path: Path to the rubrics JSON file
        preset_name: Name of the preset (e.g., "full_evaluation", "planning_only")

    Returns:
        List of rubric names
    """
    path = Path(rubrics_path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    presets = data.get("evaluation_presets", {})
    preset = presets.get(preset_name)

    if not preset:
        available = list(presets.keys())
        raise ValueError(f"Unknown preset '{preset_name}'. Available: {available}")

    return preset.get("rubrics", [])


def _build_rubric_prompt(rubric: RubricConfig) -> str:
    """Build a detailed prompt section for a rubric."""
    lines = [
        f"## Rubric: {rubric.name}",
        f"Description: {rubric.description}",
        f"Weight: {rubric.weight}",
        "",
        "### Evaluation Dimensions:",
    ]

    for dim_name, dim in rubric.dimensions.items():
        lines.append(f"\n**{dim_name}** (weight: {dim.weight})")
        lines.append(f"  {dim.description}")
        lines.append("  Criteria:")
        for criterion in dim.criteria:
            lines.append(f"    - {criterion}")

    lines.append("\n### Scoring Guide:")
    for score_range, description in rubric.scoring_guide.items():
        lines.append(f"  {score_range}: {description}")

    return "\n".join(lines)


def _build_system_prompt(rubrics: List[RubricConfig]) -> str:
    """Build the system prompt for multi-rubric evaluation."""
    return """You are an expert evaluator for medical AI research planning systems. 
Your task is to evaluate the quality of planning outputs and literature reviews using the provided rubrics.

IMPORTANT INSTRUCTIONS:
1. Evaluate each rubric and dimension independently based on the criteria provided
2. Assign scores between 0.0 (poor) and 1.0 (excellent) for each dimension
3. Be strict but fair - use the full range of scores
4. Provide specific, actionable feedback
5. Reference specific artifacts when noting strengths or weaknesses

You MUST return your evaluation as valid JSON only, with no additional text or explanation outside the JSON.
"""


def _build_user_prompt(
    rubrics: List[RubricConfig],
    artifacts: Dict[str, Any],
    context: Optional[str] = None,
) -> str:
    """Build the user prompt with rubrics and artifacts."""
    lines = ["# Evaluation Task", ""]

    if context:
        lines.extend(["## Context", context, ""])

    lines.append("## Rubrics to Apply")
    for rubric in rubrics:
        lines.append(_build_rubric_prompt(rubric))
        lines.append("")

    lines.extend(
        [
            "## Artifacts to Evaluate",
            "",
            "```json",
            json.dumps(artifacts, ensure_ascii=False, indent=2),
            "```",
            "",
            "## Required Output Format",
            "",
            "Return a JSON object with the following structure:",
            "```json",
            """{
  "overall_score": <float 0.0-1.0>,
  "rubric_scores": {
    "<rubric_name>": <float 0.0-1.0>,
    ...
  },
  "dimension_scores": {
    "<rubric_name>": {
      "<dimension_name>": <float 0.0-1.0>,
      ...
    },
    ...
  },
  "strengths": ["<specific strength 1>", "<specific strength 2>", ...],
  "weaknesses": ["<specific weakness 1>", "<specific weakness 2>", ...],
  "recommendations": ["<actionable recommendation 1>", ...],
  "verdict": "<2-3 sentence summary of the evaluation>"
}""",
            "```",
            "",
            "Evaluate the artifacts now and return ONLY the JSON response:",
        ]
    )

    return "\n".join(lines)


def call_judge(
    config: JudgeConfig,
    rubric_name: str,
    case_id: str,
    case_name: str,
    artifacts: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Run LLM-as-judge on a case and return parsed JSON + token usage metadata.

    This is the legacy single-rubric interface for backward compatibility.

    Expected model output JSON fields:
      - overall_score: float in [0, 1]
      - dimension_scores: object of float scores in [0, 1]
      - strengths: list[str]
      - weaknesses: list[str]
      - verdict: short text
    """
    system_msg = (
        "You are a strict and fair benchmark judge for medical AI pipeline planning. "
        "Score only based on provided artifacts. Return valid JSON only."
    )

    user_msg = (
        f"Rubric: {rubric_name}\n"
        "Scoring scale: 0.0 (poor) to 1.0 (excellent).\n"
        "Return JSON with keys: overall_score, dimension_scores, strengths, weaknesses, verdict.\n"
        f"Case ID: {case_id}\n"
        f"Case Name: {case_name}\n"
        f"Artifacts:\n{json.dumps(artifacts, ensure_ascii=False)}"
    )

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]

    # Pre-compute fallback token estimates.
    prompt_est = approx_tokens_from_text(system_msg) + approx_tokens_from_text(user_msg)

    call_result = _post_chat_with_fallbacks(config, messages)
    if not call_result.get("ok"):
        return {
            "ok": False,
            "error": call_result.get("error", "Judge request failed."),
            "result": None,
            "usage": {
                "prompt_tokens": prompt_est,
                "completion_tokens": 0,
                "total_tokens": prompt_est,
                "token_source": "estimated",
                "model_used": None,
                "tried_models": call_result.get("tried_models", []),
            },
        }

    response_json = call_result.get("response_json", {})

    content = _extract_content(response_json if isinstance(response_json, dict) else {})

    parsed = _parse_json_loose(content)

    usage = _extract_usage(
        response_json.get("usage") if isinstance(response_json, dict) else None
    )
    token_source = "actual"
    if usage["total_tokens"] <= 0:
        completion_est = approx_tokens_from_text(content)
        usage = {
            "prompt_tokens": prompt_est,
            "completion_tokens": completion_est,
            "total_tokens": prompt_est + completion_est,
        }
        token_source = "estimated"

    return {
        "ok": parsed is not None,
        "error": None if parsed is not None else "Judge output was not valid JSON.",
        "result": parsed,
        "raw_content": content,
        "usage": {
            **usage,
            "token_source": token_source,
            "model_used": call_result.get("model", config.model),
            "tried_models": call_result.get("tried_models", []),
        },
    }


def evaluate_with_rubrics(
    config: JudgeConfig,
    rubrics: List[RubricConfig],
    artifacts: Dict[str, Any],
    context: Optional[str] = None,
) -> EvaluationResult:
    """
    Evaluate artifacts using multiple rubrics.

    Args:
        config: Judge configuration
        rubrics: List of rubrics to apply
        artifacts: Artifacts to evaluate (from prepare_judge_artifacts)
        context: Optional context string (e.g., original prompt, ground truth)

    Returns:
        EvaluationResult with scores and feedback
    """
    system_msg = _build_system_prompt(rubrics)
    user_msg = _build_user_prompt(rubrics, artifacts, context)

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]

    prompt_est = approx_tokens_from_text(system_msg) + approx_tokens_from_text(user_msg)

    call_result = _post_chat_with_fallbacks(config, messages)
    if not call_result.get("ok"):
        return EvaluationResult(
            ok=False,
            error=call_result.get("error", "Judge request failed."),
            usage={
                "prompt_tokens": prompt_est,
                "completion_tokens": 0,
                "total_tokens": prompt_est,
                "token_source": "estimated",
                "model_used": None,
                "tried_models": call_result.get("tried_models", []),
            },
        )

    response_json = call_result.get("response_json", {})

    content = _extract_content(response_json if isinstance(response_json, dict) else {})

    parsed = _parse_json_loose(content)

    usage = _extract_usage(
        response_json.get("usage") if isinstance(response_json, dict) else None
    )
    token_source = "actual"
    if usage["total_tokens"] <= 0:
        completion_est = approx_tokens_from_text(content)
        usage = {
            "prompt_tokens": prompt_est,
            "completion_tokens": completion_est,
            "total_tokens": prompt_est + completion_est,
        }
        token_source = "estimated"

    if parsed is None:
        return EvaluationResult(
            ok=False,
            error="Judge output was not valid JSON.",
            raw_content=content,
            usage={
                **usage,
                "token_source": token_source,
                "model_used": call_result.get("model", config.model),
                "tried_models": call_result.get("tried_models", []),
            },
        )

    # Calculate weighted overall score if not provided
    overall_score = parsed.get("overall_score")
    if overall_score is None and "rubric_scores" in parsed:
        rubric_scores = parsed["rubric_scores"]
        total_weight = sum(r.weight for r in rubrics if r.name in rubric_scores)
        if total_weight > 0:
            overall_score = (
                sum(rubric_scores.get(r.name, 0) * r.weight for r in rubrics)
                / total_weight
            )

    return EvaluationResult(
        ok=True,
        overall_score=overall_score,
        rubric_scores=parsed.get("rubric_scores", {}),
        dimension_scores=parsed.get("dimension_scores", {}),
        strengths=parsed.get("strengths", []),
        weaknesses=parsed.get("weaknesses", []),
        recommendations=parsed.get("recommendations", []),
        verdict=parsed.get("verdict", ""),
        raw_content=content,
        usage={
            **usage,
            "token_source": token_source,
            "model_used": call_result.get("model", config.model),
            "tried_models": call_result.get("tried_models", []),
        },
    )


def evaluate_single_rubric(
    config: JudgeConfig,
    rubric: RubricConfig,
    artifacts: Dict[str, Any],
    context: Optional[str] = None,
) -> EvaluationResult:
    """
    Evaluate artifacts using a single rubric.

    Args:
        config: Judge configuration
        rubric: Rubric to apply
        artifacts: Artifacts to evaluate
        context: Optional context string

    Returns:
        EvaluationResult with scores and feedback
    """
    return evaluate_with_rubrics(config, [rubric], artifacts, context)


def _read_optional_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _read_optional_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _discover_case_dirs(input_dir: Path) -> List[Path]:
    """
    Discover case folders from a benchmark run directory.

    Priority:
    1) Explicit case folders containing known benchmark artifacts.
    2) Fallback to treating the input folder itself as one evaluable case/workspace.
    """
    candidate_dirs: List[Path] = []
    for child in sorted(input_dir.iterdir()):
        if not child.is_dir():
            continue
        has_case_markers = any(
            (child / name).exists()
            for name in (
                "summary.json",
                "final_state.json",
                "plan.json",
                "hypothesis.json",
                "task_messages.json",
                "state.json",
            )
        )
        if has_case_markers:
            candidate_dirs.append(child)

    if candidate_dirs:
        return candidate_dirs
    return [input_dir]


def _build_eval_context(case_dir: Path) -> str:
    """Build an optional context string from benchmark inputs/ground truth files."""
    prompt_text = _read_optional_text(case_dir / "input_prompt.txt").strip()
    ground_truth = _read_optional_json(case_dir / "ground_truth.json")

    lines: List[str] = []
    if prompt_text:
        lines.append("Original user prompt:")
        lines.append(prompt_text)

    if ground_truth:
        lines.append("")
        lines.append("Ground truth reference:")
        lines.append(json.dumps(ground_truth, ensure_ascii=False, indent=2))

    return "\n".join(lines).strip()


def evaluate_output_folder(
    input_dir: str | Path,
    config: JudgeConfig,
    rubrics_path: str | Path,
    preset: str = "full_evaluation",
    max_text_length: int = 8000,
    keep_case_results: bool = False,
) -> Dict[str, Any]:
    """
    Evaluate a benchmark output folder (or a single case/workspace folder).

    Saves per-case judge outputs as judge_result.json and writes aggregate reports:
      - judge_report.json
      - judge_report.md
    """
    try:
        from .workspace_evaluator import load_workspace, prepare_judge_artifacts
    except ImportError:
        from workspace_evaluator import load_workspace, prepare_judge_artifacts

    in_dir = Path(input_dir).resolve()
    if not in_dir.exists() or not in_dir.is_dir():
        raise ValueError(f"Input folder does not exist or is not a directory: {in_dir}")

    rubrics = load_rubrics(rubrics_path)
    rubric_names = get_preset_rubrics(rubrics_path, preset)
    selected_rubrics = [rubrics[name] for name in rubric_names if name in rubrics]
    if not selected_rubrics:
        raise ValueError(
            f"No rubrics selected from preset '{preset}'. Check {rubrics_path}."
        )

    case_dirs = _discover_case_dirs(in_dir)
    summaries: List[Dict[str, Any]] = []

    for case_dir in case_dirs:
        artifacts = load_workspace(case_dir)
        judge_artifacts = prepare_judge_artifacts(
            artifacts, rubric_names=rubric_names, max_text_length=max_text_length
        )
        context = _build_eval_context(case_dir)

        result = evaluate_with_rubrics(
            config=config,
            rubrics=selected_rubrics,
            artifacts=judge_artifacts,
            context=context or None,
        )

        case_output = {
            "case_id": case_dir.name,
            "case_path": str(case_dir),
            "ok": result.ok,
            "error": result.error,
            "overall_score": result.overall_score,
            "rubric_scores": result.rubric_scores,
            "dimension_scores": result.dimension_scores,
            "strengths": result.strengths,
            "weaknesses": result.weaknesses,
            "recommendations": result.recommendations,
            "verdict": result.verdict,
            "usage": result.usage,
            "artifacts_summary": judge_artifacts.get("workspace_summary", {}),
        }

        case_result_path = case_dir / "judge_result.json"
        if keep_case_results:
            with open(case_result_path, "w", encoding="utf-8") as f:
                json.dump(case_output, f, ensure_ascii=False, indent=2)
        elif case_result_path.exists():
            # Keep run output lean unless explicit per-case files are requested.
            try:
                case_result_path.unlink()
            except OSError:
                pass

        summaries.append(case_output)

    valid_scores = [
        s.get("overall_score") for s in summaries if s.get("overall_score") is not None
    ]
    avg_score = (
        round(sum(float(v) for v in valid_scores) / len(valid_scores), 3)
        if valid_scores
        else None
    )

    report = {
        "input_dir": str(in_dir),
        "model": config.model,
        "base_url": _normalize_base_url(config.base_url),
        "preset": preset,
        "rubrics": [r.name for r in selected_rubrics],
        "case_result_files": "kept" if keep_case_results else "removed",
        "cases_evaluated": len(summaries),
        "cases_ok": sum(1 for s in summaries if s.get("ok")),
        "average_overall_score": avg_score,
        "average_overall_band": _score_band(avg_score),
        "report_metadata": {
            "field_explanations": {
                "overall_score": "Weighted score from 0.0 to 1.0 where higher is better.",
                "rubric_scores": "Per-rubric scores (0.0-1.0) returned by the judge.",
                "dimension_scores": "Per-dimension scores inside each rubric for detailed diagnosis.",
                "strengths": "Concrete aspects the output did well.",
                "weaknesses": "Concrete shortcomings observed in output artifacts.",
                "recommendations": "Actionable changes likely to improve future runs.",
                "usage": "Token accounting (actual when provided by provider, otherwise estimated).",
                "artifacts_summary": "Which workspace files were loaded, missing, or had parse issues.",
            },
            "score_bands": {
                "0.90-1.00": "excellent",
                "0.75-0.89": "strong",
                "0.60-0.74": "adequate",
                "0.40-0.59": "needs improvement",
                "0.00-0.39": "poor",
            },
        },
        "summaries": summaries,
        "token_usage": {
            "prompt_tokens": sum(
                int((s.get("usage") or {}).get("prompt_tokens", 0)) for s in summaries
            ),
            "completion_tokens": sum(
                int((s.get("usage") or {}).get("completion_tokens", 0))
                for s in summaries
            ),
            "total_tokens": sum(
                int((s.get("usage") or {}).get("total_tokens", 0)) for s in summaries
            ),
        },
    }

    report_json_path = in_dir / "judge_report.json"
    with open(report_json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    report_md_path = in_dir / "judge_report.md"
    md_lines = [
        "# LLM Judge Report",
        "",
        f"- Input folder: {in_dir}",
        f"- Model: {config.model}",
        f"- Endpoint: {_normalize_base_url(config.base_url)}",
        f"- Preset: {preset}",
        f"- Per-case judge_result.json files: {'kept' if keep_case_results else 'removed'}",
        f"- Cases evaluated: {report['cases_evaluated']}",
        f"- Cases OK: {report['cases_ok']}",
        f"- Average overall score: {report['average_overall_score']} ({report['average_overall_band']})",
        "",
        "## Metric Guide",
        "",
        "- overall_score: weighted quality score from 0.0 to 1.0.",
        "- rubric_scores: score for each selected rubric.",
        "- dimension_scores: fine-grained scores under each rubric.",
        "- strengths / weaknesses: evidence-based findings from artifacts.",
        "- recommendations: prioritized actions to improve future runs.",
        "- usage: prompt/completion tokens (actual if provider returns usage, otherwise estimated).",
        "",
        "### Score Bands",
        "- 0.90-1.00: excellent",
        "- 0.75-0.89: strong",
        "- 0.60-0.74: adequate",
        "- 0.40-0.59: needs improvement",
        "- 0.00-0.39: poor",
        "",
        "## Run Token Usage",
        "",
        f"- Prompt tokens: {report['token_usage']['prompt_tokens']:,}",
        f"- Completion tokens: {report['token_usage']['completion_tokens']:,}",
        f"- Total tokens: {report['token_usage']['total_tokens']:,}",
        "",
        "| Case | Score | Status |",
        "|------|-------|--------|",
    ]

    for s in summaries:
        score = s.get("overall_score")
  
        score_text = f"{float(score):.3f}" if score is not None else "-"
        status = "ok" if s.get("ok") else f"error: {s.get('error') or 'unknown'}"
        md_lines.append(f"| {s.get('case_id')} | {score_text} | {status} |")

    md_lines.extend(["", "## Case Details", ""])

    for s in summaries:
        score = s.get("overall_score")
        score_text = f"{float(score):.3f}" if score is not None else "N/A"
        md_lines.append(f"### {s.get('case_id')}")
        md_lines.append(f"- Status: {'ok' if s.get('ok') else 'error'}")
        md_lines.append(f"- Overall score: {score_text} ({_score_band(score)})")

        usage = s.get("usage") or {}
        md_lines.append(
            "- Tokens: "
            f"prompt={int(usage.get('prompt_tokens', 0)):,}, "
            f"completion={int(usage.get('completion_tokens', 0)):,}, "
            f"total={int(usage.get('total_tokens', 0)):,} "
            f"[{usage.get('token_source', 'unknown')}]"
        )
        md_lines.append(f"- Model used: {usage.get('model_used', config.model)}")

        rubric_scores = s.get("rubric_scores") or {}
        if rubric_scores:
            md_lines.append("- Rubric scores:")
            for rubric_name, rubric_score in rubric_scores.items():
                md_lines.append(
                    f"  - {rubric_name}: {float(rubric_score):.3f} ({_score_band(float(rubric_score))})"
                )

        dimension_scores = s.get("dimension_scores") or {}
        if dimension_scores:
            md_lines.append("- Dimension scores:")
            for rubric_name, dims in dimension_scores.items():
                md_lines.append(f"  - {rubric_name}:")
                if isinstance(dims, dict):
                    for dim_name, dim_score in dims.items():
                        md_lines.append(
                            f"    - {dim_name}: {float(dim_score):.3f} ({_score_band(float(dim_score))})"
                        )

        verdict = (s.get("verdict") or "").strip()
        if verdict:
            md_lines.append(f"- Verdict: {verdict}")

        strengths = s.get("strengths") or []
        if strengths:
            md_lines.append("- Strengths:")
            for item in strengths:
                md_lines.append(f"  - {item}")

        weaknesses = s.get("weaknesses") or []
        if weaknesses:
            md_lines.append("- Weaknesses:")
            for item in weaknesses:
                md_lines.append(f"  - {item}")

        recommendations = s.get("recommendations") or []
        if recommendations:
            md_lines.append("- Recommendations:")
            for item in recommendations:
                md_lines.append(f"  - {item}")

        artifacts_summary = s.get("artifacts_summary") or {}
        parse_errors = artifacts_summary.get("parse_errors") or []
        if parse_errors:
            md_lines.append("- Artifact parse issues:")
            for item in parse_errors:
                md_lines.append(f"  - {item}")

        md_lines.append("")

    with open(report_md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines) + "\n")

    return report


def _resolve_rubrics_path(user_path: Optional[str]) -> Path:
    if user_path:
        return Path(user_path).resolve()
    return Path(__file__).resolve().parent / "evaluation_rubrics.json"


def _default_ollama_model() -> str:
    # Preference order: explicit env override, qwen coder, then OSS variants.
    return os.getenv("JUDGE_MODEL", "qwen2.5-coder:7b")


def _default_base_url() -> str:
    """Prefer explicit judge override, then shared Ollama env, then localhost."""
    return os.getenv("JUDGE_BASE_URL", os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))


def _default_fallback_models() -> List[str]:
    """Read optional comma-separated fallback models from env."""
    raw = os.getenv("JUDGE_FALLBACK_MODELS", "")
    if not raw.strip():
        return []
    return [m.strip() for m in raw.split(",") if m.strip()]


def _default_temperature() -> float:
    """Read judge temperature from env with safe fallback."""
    raw = os.getenv("JUDGE_TEMPERATURE", "0.0").strip()
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _default_max_tokens() -> Optional[int]:
    """Read optional max tokens from env (empty means unlimited)."""
    raw = os.getenv("JUDGE_MAX_TOKENS", "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _default_use_autogen() -> bool:
    """Read judge client mode from env. True unless explicitly disabled."""
    raw = os.getenv("JUDGE_USE_AUTOGEN", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def main() -> None:
    # Load .env values first so argparse defaults can read JUDGE_* vars.
    _load_env_into_process()

    parser = argparse.ArgumentParser(
        description=(
            "Evaluate benchmark outputs in a folder using LLM-as-judge "
            "(Ollama-compatible /v1 chat completions)."
        )
    )
    parser.add_argument(
        "input_folder",
        type=Path,
        help="Path to run folder (e.g. benchmark_results/litreview_...) or a single case/workspace folder.",
    )
    parser.add_argument(
        "--model",
        default=_default_ollama_model(),
        help=(
            "Judge model name. Examples: qwen2.5-coder:7b, gpt-oss:20b, gpt-oss:120b-cloud."
        ),
    )
    parser.add_argument(
        "--base-url",
        default=_default_base_url(),
        help="Ollama base URL (default: http://localhost:11434).",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("JUDGE_API_KEY", os.getenv("OLLAMA_API_KEY", "ollama")),
        help="API key for OpenAI-compatible endpoint (Ollama can use any placeholder).",
    )
    parser.add_argument(
        "--fallback-models",
        default=",".join(_default_fallback_models()),
        help=(
            "Comma-separated fallback models to try if the primary model fails. "
            "Example: deepseek-v3.1:671b-cloud,qwen2.5-coder:32b"
        ),
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=_default_temperature(),
        help="Sampling temperature (env: JUDGE_TEMPERATURE, default: 0.0).",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=_default_max_tokens(),
        help="Optional max completion tokens (env: JUDGE_MAX_TOKENS).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.getenv("JUDGE_TIMEOUT", "180")),
        help="Request timeout in seconds.",
    )
    parser.add_argument(
        "--no-autogen",
        action="store_true",
        help="Use raw HTTP requests instead of AutoGen Ollama client.",
    )
    parser.add_argument(
        "--preset",
        default="full_evaluation",
        help="Rubric preset from evaluation_rubrics.json (e.g., full_evaluation, quick_check).",
    )
    parser.add_argument(
        "--rubrics-path",
        default=None,
        help="Optional path to a custom evaluation_rubrics.json file.",
    )
    parser.add_argument(
        "--max-text-length",
        type=int,
        default=8000,
        help="Maximum chars per text artifact passed to the judge.",
    )
    parser.add_argument(
        "--keep-case-results",
        action="store_true",
        help="Keep per-case judge_result.json files (default removes them to keep only aggregate reports).",
    )

    args = parser.parse_args()

    fallback_models = [m.strip() for m in args.fallback_models.split(",") if m.strip()]
    use_autogen = _default_use_autogen() and (not args.no_autogen)

    config = JudgeConfig(
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        timeout_seconds=args.timeout,
        use_autogen=use_autogen,
        fallback_models=fallback_models,
    )

    rubrics_path = _resolve_rubrics_path(args.rubrics_path)
    report = evaluate_output_folder(
        input_dir=args.input_folder,
        config=config,
        rubrics_path=rubrics_path,
        preset=args.preset,
        max_text_length=args.max_text_length,
        keep_case_results=args.keep_case_results,
    )

    print("\nLLM Judge completed")
    print(f"Input: {report['input_dir']}")
    print(f"Model: {report['model']}")
    print(f"Cases: {report['cases_evaluated']} (ok={report['cases_ok']})")
    print(f"Average score: {report['average_overall_score']}")
    print(f"Report JSON: {Path(report['input_dir']) / 'judge_report.json'}")
    print(f"Report MD:   {Path(report['input_dir']) / 'judge_report.md'}")


if __name__ == "__main__":
    main()
