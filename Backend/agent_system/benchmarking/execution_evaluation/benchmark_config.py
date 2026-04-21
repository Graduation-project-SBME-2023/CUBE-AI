"""Configuration model for execution benchmarking."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .benchmark_execution_setup import (
    AGENT_IDS,
    DEFAULT_BACKEND_API_BASE,
    DEFAULT_OUTPUT_ROOT,
    normalize_agent_id,
)


BENCHMARK_MODE_FULL_SYSTEM = "full_system"
BENCHMARK_MODE_SINGLE_AGENT = "single_agent"
VALID_BENCHMARK_MODES = {BENCHMARK_MODE_FULL_SYSTEM, BENCHMARK_MODE_SINGLE_AGENT}


@dataclass
class ExecutionBenchmarkConfig:
    # Data source
    hypothesis_id: Optional[str] = None
    use_fallback: bool = False
    fallback_case: Optional[str] = None
    base_url: str = DEFAULT_BACKEND_API_BASE

    # Run mode
    mode: str = BENCHMARK_MODE_FULL_SYSTEM
    target_agent_id: Optional[str] = None

    # Runtime
    output_root: str = str(DEFAULT_OUTPUT_ROOT)
    workspace_dir: Optional[str] = None
    prompt_prefix: str = (
        "You are running in execution benchmark mode. "
        "Be deterministic, log assumptions, and produce structured outputs."
    )

    def validate(self) -> None:
        if self.mode not in VALID_BENCHMARK_MODES:
            allowed = ", ".join(sorted(VALID_BENCHMARK_MODES))
            raise ValueError(f"Invalid mode '{self.mode}'. Allowed: {allowed}")

        if self.use_fallback:
            if not self.fallback_case:
                raise ValueError("fallback_case is required when use_fallback=True")
        elif not self.hypothesis_id:
            raise ValueError("hypothesis_id is required when use_fallback=False")

        if self.mode == BENCHMARK_MODE_SINGLE_AGENT:
            if not self.target_agent_id:
                raise ValueError("target_agent_id is required for single_agent mode")
            self.target_agent_id = normalize_agent_id(self.target_agent_id)
            if self.target_agent_id not in AGENT_IDS:
                allowed = ", ".join(AGENT_IDS)
                raise ValueError(
                    f"Unknown target_agent_id '{self.target_agent_id}'. Allowed: {allowed}"
                )


DEFAULT_FULL_SYSTEM_CONFIG = ExecutionBenchmarkConfig(
    mode=BENCHMARK_MODE_FULL_SYSTEM,
)

DEFAULT_SINGLE_AGENT_CONFIG = ExecutionBenchmarkConfig(
    mode=BENCHMARK_MODE_SINGLE_AGENT,
    target_agent_id="segmentation_agent",
)
