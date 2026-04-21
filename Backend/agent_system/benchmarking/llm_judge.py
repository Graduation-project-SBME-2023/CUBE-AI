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
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


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

        client = OpenAIChatCompletionClient(
            model=config.model,
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
        }

    except Exception as exc:
        return {
            "ok": False,
            "error": f"AutoGen request failed: {type(exc).__name__}: {exc}",
        }


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

            # Run async function in event loop
            try:
                loop = asyncio.get_running_loop()
                # If we're already in an async context, create a new loop in a thread
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(
                        lambda: asyncio.run(_post_chat_autogen(config, messages))
                    )
                    result = future.result(timeout=config.timeout_seconds)
            except RuntimeError:
                # No running loop, create one
                result = asyncio.run(_post_chat_autogen(config, messages))

            # If AutoGen succeeded, return result
            if result.get("ok"):
                return result

            # If AutoGen failed for non-import reasons, fall back to raw HTTP
            error_msg = result.get("error", "")
            if "not available" not in error_msg.lower():
                # Log the AutoGen error and try raw HTTP
                print(f"AutoGen request failed, falling back to raw HTTP: {error_msg}")

        except ImportError:
            # AutoGen not installed, fall back to raw HTTP silently
            pass

    # Use raw HTTP (tries /v1/chat/completions first, then /api/chat)
    return _post_chat(config, messages)


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

    call_result = _post_chat_sync(config, messages)
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

    call_result = _post_chat_sync(config, messages)
    if not call_result.get("ok"):
        return EvaluationResult(
            ok=False,
            error=call_result.get("error", "Judge request failed."),
            usage={
                "prompt_tokens": prompt_est,
                "completion_tokens": 0,
                "total_tokens": prompt_est,
                "token_source": "estimated",
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
            usage={**usage, "token_source": token_source},
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
        usage={**usage, "token_source": token_source},
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

        with open(case_dir / "judge_result.json", "w", encoding="utf-8") as f:
            json.dump(case_output, f, ensure_ascii=False, indent=2)

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
        "cases_evaluated": len(summaries),
        "cases_ok": sum(1 for s in summaries if s.get("ok")),
        "average_overall_score": avg_score,
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
        f"- Cases evaluated: {report['cases_evaluated']}",
        f"- Cases OK: {report['cases_ok']}",
        f"- Average overall score: {report['average_overall_score']}",
        "",
        "| Case | Score | Status |",
        "|------|-------|--------|",
    ]

    for s in summaries:
        score = s.get("overall_score")
        score_text = f"{float(score):.3f}" if score is not None else "-"
        status = "ok" if s.get("ok") else f"error: {s.get('error') or 'unknown'}"
        md_lines.append(f"| {s.get('case_id')} | {score_text} | {status} |")

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


def main() -> None:
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
            "Judge model name. Examples: qwen2.5-coder:7b, gpt-oss:20b, gpt-oss:120b."
        ),
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("JUDGE_BASE_URL", "http://localhost:11434"),
        help="Ollama base URL (default: http://localhost:11434).",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("JUDGE_API_KEY", os.getenv("OLLAMA_API_KEY", "ollama")),
        help="API key for OpenAI-compatible endpoint (Ollama can use any placeholder).",
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

    args = parser.parse_args()

    config = JudgeConfig(
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        timeout_seconds=args.timeout,
        use_autogen=not args.no_autogen,
    )

    rubrics_path = _resolve_rubrics_path(args.rubrics_path)
    report = evaluate_output_folder(
        input_dir=args.input_folder,
        config=config,
        rubrics_path=rubrics_path,
        preset=args.preset,
        max_text_length=args.max_text_length,
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
