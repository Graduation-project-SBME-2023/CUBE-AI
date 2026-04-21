#!/usr/bin/env python
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                    LLM-as-Judge Workspace Evaluation CLI                      ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  Evaluates planning outputs and literature reviews from workspace folders    ║
║  using LLM-as-judge with customizable rubrics.                               ║
║                                                                              ║
║  Usage:                                                                      ║
║      python -m benchmarking.evaluate_workspace <workspace_path>              ║
║                                                                              ║
║  Examples:                                                                   ║
║      # Evaluate with default rubrics (full evaluation)                       ║
║      python -m benchmarking.evaluate_workspace ./workspace_session123        ║
║                                                                              ║
║      # Evaluate planning only                                                 ║
║      python -m benchmarking.evaluate_workspace ./workspace_* --preset planning_only
║                                                                              ║
║      # Use specific rubrics                                                   ║
║      python -m benchmarking.evaluate_workspace ./workspace_*                 ║
║          --rubrics hypothesis_quality literature_review_quality              ║
║                                                                              ║
║      # Use custom rubrics file                                                ║
║      python -m benchmarking.evaluate_workspace ./workspace_*                 ║
║          --rubrics-file my_rubrics.json                                      ║
║                                                                              ║
║      # List available workspaces                                              ║
║      python -m benchmarking.evaluate_workspace --list ./                     ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Path setup
SCRIPT_DIR = Path(__file__).parent.resolve()
AGENT_SYSTEM_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(AGENT_SYSTEM_DIR))

from benchmarking.llm_judge import (
    JudgeConfig,
    RubricConfig,
    EvaluationResult,
    evaluate_with_rubrics,
    load_rubrics,
    get_preset_rubrics,
)
from benchmarking.workspace_evaluator import (
    WorkspaceArtifacts,
    load_workspace,
    prepare_judge_artifacts,
    list_available_workspaces,
)


# Default rubrics file path
DEFAULT_RUBRICS_FILE = SCRIPT_DIR / "evaluation_rubrics.json"


def setup_logging(verbose: bool = False) -> logging.Logger:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%H:%M:%S",
    )
    return logging.getLogger("evaluate_workspace")


def print_header():
    """Print CLI header."""
    print("\n" + "═" * 72)
    print("  LLM-as-Judge Workspace Evaluation")
    print("═" * 72)


def print_workspace_summary(artifacts: WorkspaceArtifacts):
    """Print a summary of loaded workspace artifacts."""
    summary = artifacts.get_summary()

    print("\n┌─ Workspace Summary ─────────────────────────────────────────────────┐")
    print(f"│  Path: {str(artifacts.workspace_path)[:60]:<60} │")
    if summary.get("session_id"):
        print(f"│  Session: {summary['session_id']:<56} │")
    print("├─────────────────────────────────────────────────────────────────────┤")

    # Planning outputs
    print("│  Planning Outputs:                                                  │")
    print(f"│    Hypothesis: {'✓' if summary['has_hypothesis'] else '✗':<52} │")
    print(f"│    Plan: {'✓' if summary['has_plan'] else '✗':<58} │")
    print(
        f"│    Task Messages: {'✓' if summary['has_task_messages'] else '✗'} ({summary['num_task_messages']} messages){' ':<36} │"
    )

    # Literature outputs
    print("│  Literature Review:                                                 │")
    print(f"│    Context: {'✓' if summary['has_literature_context'] else '✗':<54} │")
    print(
        f"│    Ranked Papers: {'✓' if summary['has_ranked_papers'] else '✗'} ({summary['num_ranked_papers']} papers){' ':<35} │"
    )
    print(
        f"│    Report: {'✓' if summary['has_literature_report'] else '✗'} ({summary['literature_report_length']:,} chars){' ':<34} │"
    )

    if summary.get("knowledge_base_length", 0) > 0:
        print(
            f"│    Knowledge Base: {summary['knowledge_base_length']:,} chars{' ':<40} │"
        )

    print("└─────────────────────────────────────────────────────────────────────┘")


def print_evaluation_result(result: EvaluationResult, rubric_names: List[str]):
    """Print evaluation results in a formatted way."""

    if not result.ok:
        print(
            "\n┌─ Evaluation Failed ────────────────────────────────────────────────┐"
        )
        print(f"│  Error: {result.error[:60]:<60} │")
        print("└─────────────────────────────────────────────────────────────────────┘")
        return

    print("\n┌─ Evaluation Results ───────────────────────────────────────────────┐")

    # Overall score
    score = result.overall_score
    if score is not None:
        bar_len = 40
        filled = int(score * bar_len)
        bar = "█" * filled + "░" * (bar_len - filled)
        print(f"│  Overall Score: {score:.3f} [{bar}] │")

    print("├─────────────────────────────────────────────────────────────────────┤")

    # Rubric scores
    print("│  Rubric Scores:                                                     │")
    for name in rubric_names:
        score = result.rubric_scores.get(name, 0)
        bar_len = 30
        filled = int(score * bar_len)
        bar = "█" * filled + "░" * (bar_len - filled)
        print(f"│    {name[:25]:<25} {score:.3f} [{bar}] │")

    # Dimension scores
    if result.dimension_scores:
        print("├─────────────────────────────────────────────────────────────────────┤")
        print("│  Dimension Scores:                                                  │")
        for rubric_name, dimensions in result.dimension_scores.items():
            print(
                f"│    {rubric_name}:                                                  │"[
                    :72
                ]
            )
            for dim_name, score in dimensions.items():
                print(f"│      {dim_name[:20]:<20} {score:.3f}{' ':<40} │")

    print("├─────────────────────────────────────────────────────────────────────┤")

    # Strengths
    if result.strengths:
        print("│  Strengths:                                                         │")
        for strength in result.strengths[:5]:
            lines = [strength[i : i + 60] for i in range(0, len(strength), 60)]
            for line in lines:
                print(f"│    ✓ {line:<62} │")

    # Weaknesses
    if result.weaknesses:
        print("│  Weaknesses:                                                        │")
        for weakness in result.weaknesses[:5]:
            lines = [weakness[i : i + 60] for i in range(0, len(weakness), 60)]
            for line in lines:
                print(f"│    ✗ {line:<62} │")

    # Recommendations
    if result.recommendations:
        print("│  Recommendations:                                                   │")
        for rec in result.recommendations[:3]:
            lines = [rec[i : i + 60] for i in range(0, len(rec), 60)]
            for line in lines:
                print(f"│    → {line:<62} │")

    print("├─────────────────────────────────────────────────────────────────────┤")

    # Verdict
    if result.verdict:
        print("│  Verdict:                                                           │")
        verdict_lines = [
            result.verdict[i : i + 64] for i in range(0, len(result.verdict), 64)
        ]
        for line in verdict_lines:
            print(f"│    {line:<64} │")

    print("├─────────────────────────────────────────────────────────────────────┤")

    # Token usage
    usage = result.usage
    print(
        f"│  Tokens: prompt={usage.get('prompt_tokens', 0):,} completion={usage.get('completion_tokens', 0):,} ({usage.get('token_source', 'unknown')}) │"
    )

    print("└─────────────────────────────────────────────────────────────────────┘")


def save_evaluation_result(
    result: EvaluationResult,
    workspace_path: Path,
    output_path: Optional[Path] = None,
) -> Path:
    """Save evaluation result to a JSON file."""
    if output_path is None:
        output_path = workspace_path / "evaluation_result.json"
    else:
        output_path = Path(output_path)

    output_data = {
        "timestamp": datetime.now().isoformat(),
        "workspace_path": str(workspace_path),
        **result.to_dict(),
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    return output_path


def evaluate_workspace(
    workspace_path: str | Path,
    config: JudgeConfig,
    rubrics: List[RubricConfig],
    context: Optional[str] = None,
    save_result: bool = True,
    output_path: Optional[Path] = None,
    logger: Optional[logging.Logger] = None,
) -> EvaluationResult:
    """
    Evaluate a single workspace.

    Args:
        workspace_path: Path to the workspace folder
        config: Judge configuration
        rubrics: List of rubrics to apply
        context: Optional context (e.g., original prompt)
        save_result: Whether to save result to file
        output_path: Custom output path for result
        logger: Logger instance

    Returns:
        EvaluationResult
    """
    logger = logger or logging.getLogger("evaluate_workspace")

    # Load workspace artifacts
    logger.info(f"Loading workspace: {workspace_path}")
    artifacts = load_workspace(workspace_path)

    if not artifacts.is_valid():
        return EvaluationResult(
            ok=False,
            error="Workspace does not contain required artifacts (hypothesis or plan)",
        )

    # Print summary
    print_workspace_summary(artifacts)

    # Prepare artifacts for judge
    logger.info("Preparing artifacts for evaluation...")
    judge_artifacts = prepare_judge_artifacts(
        artifacts,
        rubric_names=[r.name for r in rubrics],
    )

    # Run evaluation
    logger.info(f"Running evaluation with {len(rubrics)} rubrics...")
    print(f"\n  Evaluating with rubrics: {', '.join(r.name for r in rubrics)}")
    print("  This may take a minute...")

    result = evaluate_with_rubrics(config, rubrics, judge_artifacts, context)

    # Save result
    if save_result and result.ok:
        result_path = save_evaluation_result(
            result,
            Path(workspace_path),
            output_path,
        )
        logger.info(f"Saved result to: {result_path}")

    return result


async def evaluate_workspace_async(
    workspace_path: str | Path,
    config: JudgeConfig,
    rubrics: List[RubricConfig],
    context: Optional[str] = None,
    save_result: bool = True,
    output_path: Optional[Path] = None,
) -> EvaluationResult:
    """Async wrapper for evaluate_workspace (runs sync code in executor)."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: evaluate_workspace(
            workspace_path, config, rubrics, context, save_result, output_path
        ),
    )


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Evaluate workspace outputs using LLM-as-judge with custom rubrics.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Evaluate a workspace with full evaluation preset
  python -m benchmarking.evaluate_workspace ./workspace_session123

  # Evaluate planning only
  python -m benchmarking.evaluate_workspace ./workspace_* --preset planning_only

  # Use specific rubrics
  python -m benchmarking.evaluate_workspace ./workspace_* \\
      --rubrics hypothesis_quality pipeline_plan_quality

  # Use custom rubrics file
  python -m benchmarking.evaluate_workspace ./workspace_* \\
      --rubrics-file my_rubrics.json

  # List available workspaces in a directory
  python -m benchmarking.evaluate_workspace --list ./

  # Show available presets and rubrics
  python -m benchmarking.evaluate_workspace --show-rubrics
        """,
    )

    # Positional arguments
    parser.add_argument(
        "workspace",
        nargs="?",
        help="Path to workspace folder to evaluate",
    )

    # Rubric options
    parser.add_argument(
        "--preset",
        choices=["full_evaluation", "planning_only", "literature_only", "quick_check"],
        default="full_evaluation",
        help="Evaluation preset to use (default: full_evaluation)",
    )
    parser.add_argument(
        "--rubrics",
        nargs="+",
        help="Specific rubric names to apply (overrides --preset)",
    )
    parser.add_argument(
        "--rubrics-file",
        type=Path,
        default=DEFAULT_RUBRICS_FILE,
        help=f"Path to rubrics configuration file (default: {DEFAULT_RUBRICS_FILE})",
    )

    # Judge configuration
    parser.add_argument(
        "--model",
        default=os.getenv("JUDGE_MODEL", "qwen2.5-coder:7b"),
        help="LLM model name for judge (env: JUDGE_MODEL)",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("JUDGE_BASE_URL", "http://localhost:11434"),
        help="Base URL for Ollama (env: JUDGE_BASE_URL)",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("JUDGE_API_KEY", os.getenv("OLLAMA_API_KEY", "ollama")),
        help="API key for judge (env: JUDGE_API_KEY)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.getenv("JUDGE_TIMEOUT", "180")),
        help="Request timeout in seconds (default: 180)",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Temperature for judge LLM (default: 0.0)",
    )
    parser.add_argument(
        "--no-autogen",
        action="store_true",
        help="Use raw HTTP requests instead of AutoGen Ollama client",
    )

    # Output options
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="Custom output path for evaluation result",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Don't save evaluation result to file",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output result as JSON to stdout",
    )

    # Utility options
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available workspaces in the specified directory",
    )
    parser.add_argument(
        "--show-rubrics",
        action="store_true",
        help="Show available rubrics and presets",
    )
    parser.add_argument(
        "--context",
        help="Additional context to provide to the judge (e.g., original prompt)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()
    logger = setup_logging(args.verbose)

    # Handle --show-rubrics
    if args.show_rubrics:
        try:
            all_rubrics = load_rubrics(args.rubrics_file)
            print("\n═══ Available Rubrics ═══")
            for name, rubric in all_rubrics.items():
                print(f"\n  {name}:")
                print(f"    Description: {rubric.description}")
                print(f"    Weight: {rubric.weight}")
                print(f"    Dimensions: {', '.join(rubric.dimensions.keys())}")

            print("\n═══ Available Presets ═══")
            with open(args.rubrics_file, "r") as f:
                data = json.load(f)
            for name, preset in data.get("evaluation_presets", {}).items():
                print(f"\n  {name}:")
                print(f"    Description: {preset.get('description', '')}")
                print(f"    Rubrics: {', '.join(preset.get('rubrics', []))}")
        except Exception as e:
            print(f"Error loading rubrics: {e}")
            sys.exit(1)
        return

    # Handle --list
    if args.list:
        search_path = args.workspace or "."
        workspaces = list_available_workspaces(search_path)
        if not workspaces:
            print(f"No workspaces found in {search_path}")
        else:
            print(f"\nFound {len(workspaces)} workspace(s) in {search_path}:\n")
            for ws in workspaces:
                status = []
                if ws["has_state"]:
                    status.append("state")
                if ws["has_literature"]:
                    status.append("literature")
                status_str = ", ".join(status) if status else "empty"
                print(f"  {ws['name']:<40} [{ws['type']}] ({status_str})")
        return

    # Require workspace path for evaluation
    if not args.workspace:
        parser.error("Workspace path is required for evaluation")

    # Validate workspace path
    workspace_path = Path(args.workspace).resolve()
    if not workspace_path.exists():
        print(f"Error: Workspace path does not exist: {workspace_path}")
        sys.exit(1)

    # Load rubrics
    try:
        all_rubrics = load_rubrics(args.rubrics_file)
    except FileNotFoundError:
        print(f"Error: Rubrics file not found: {args.rubrics_file}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in rubrics file: {e}")
        sys.exit(1)

    # Determine which rubrics to use
    if args.rubrics:
        rubric_names = args.rubrics
    else:
        try:
            rubric_names = get_preset_rubrics(args.rubrics_file, args.preset)
        except ValueError as e:
            print(f"Error: {e}")
            sys.exit(1)

    # Validate rubric names
    selected_rubrics = []
    for name in rubric_names:
        if name not in all_rubrics:
            print(f"Warning: Unknown rubric '{name}', skipping")
        else:
            selected_rubrics.append(all_rubrics[name])

    if not selected_rubrics:
        print("Error: No valid rubrics selected")
        sys.exit(1)

    # Create judge config
    judge_config = JudgeConfig(
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        temperature=args.temperature,
        timeout_seconds=args.timeout,
        use_autogen=not args.no_autogen,
    )

    # Allow placeholder key for Ollama/OpenAI-compatible local endpoints.

    # Print header
    if not args.json:
        print_header()
        print(f"\n  Workspace: {workspace_path}")
        print(f"  Model: {judge_config.model}")
        print(f"  Rubrics: {', '.join(rubric_names)}")

    # Run evaluation
    try:
        result = evaluate_workspace(
            workspace_path=workspace_path,
            config=judge_config,
            rubrics=selected_rubrics,
            context=args.context,
            save_result=not args.no_save,
            output_path=args.output,
            logger=logger,
        )
    except Exception as e:
        logger.error(f"Evaluation failed: {e}", exc_info=args.verbose)
        if args.json:
            print(json.dumps({"ok": False, "error": str(e)}))
        else:
            print(f"\nError: Evaluation failed: {e}")
        sys.exit(1)

    # Output result
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        print_evaluation_result(result, rubric_names)

        if not args.no_save and result.ok:
            output_file = args.output or (workspace_path / "evaluation_result.json")
            print(f"\n  Result saved to: {output_file}")

    # Exit with appropriate code
    sys.exit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
