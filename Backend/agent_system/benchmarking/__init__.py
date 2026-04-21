"""
Benchmarking Module - LLM-as-Judge Evaluation System

This module provides tools for evaluating planning pipeline outputs and
literature reviews using LLM-as-judge with customizable rubrics.

Components:
- evaluate_workspace: Standalone CLI for workspace evaluation
- workspace_evaluator: Load and prepare workspace artifacts
- llm_judge: LLM-as-judge evaluation with custom rubrics
- evaluation_rubrics.json: Default rubric configurations

Usage:
    # As CLI
    python -m benchmarking.evaluate_workspace <workspace_path>

    # As library
    from benchmarking.workspace_evaluator import load_workspace, prepare_judge_artifacts
    from benchmarking.llm_judge import JudgeConfig, evaluate_with_rubrics, load_rubrics
"""

from benchmarking.llm_judge import (
    JudgeConfig,
    RubricConfig,
    RubricDimension,
    EvaluationResult,
    evaluate_with_rubrics,
    evaluate_single_rubric,
    load_rubrics,
    get_preset_rubrics,
    call_judge,
    approx_tokens_from_text,
)

from benchmarking.workspace_evaluator import (
    WorkspaceArtifacts,
    load_workspace,
    prepare_judge_artifacts,
    list_available_workspaces,
)

__all__ = [
    # llm_judge
    "JudgeConfig",
    "RubricConfig",
    "RubricDimension",
    "EvaluationResult",
    "evaluate_with_rubrics",
    "evaluate_single_rubric",
    "load_rubrics",
    "get_preset_rubrics",
    "call_judge",
    "approx_tokens_from_text",
    # workspace_evaluator
    "WorkspaceArtifacts",
    "load_workspace",
    "prepare_judge_artifacts",
    "list_available_workspaces",
]
