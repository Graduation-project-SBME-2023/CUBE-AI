"""
Workspace Evaluator Module - Reads and extracts artifacts from workspace folders.

This module provides utilities to load planning outputs, literature review outputs,
and other artifacts from a workspace folder for LLM-as-judge evaluation.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class WorkspaceArtifacts:
    """Container for all artifacts extracted from a workspace."""

    # Workspace metadata
    workspace_path: Path
    session_id: Optional[str] = None

    # Planning outputs
    hypothesis: Optional[Dict[str, Any]] = None
    plan: Optional[Dict[str, Any]] = None
    task_messages: Optional[List[Dict[str, Any]]] = None
    planner_state: Optional[Dict[str, Any]] = None

    # Literature review outputs
    literature_context: Optional[Dict[str, Any]] = None
    ranked_papers: Optional[List[Dict[str, Any]]] = None
    literature_report: Optional[str] = None
    knowledge_base: Optional[str] = None

    # Raw content for fallback
    raw_files: Dict[str, str] = field(default_factory=dict)

    # Validation info
    missing_files: List[str] = field(default_factory=list)
    loaded_files: List[str] = field(default_factory=list)
    parse_errors: List[str] = field(default_factory=list)

    def is_valid(self) -> bool:
        """Check if minimum required artifacts are present."""
        return self.hypothesis is not None or self.plan is not None

    def has_literature_review(self) -> bool:
        """Check if literature review outputs are present."""
        return (
            self.literature_context is not None
            or self.ranked_papers is not None
            or self.literature_report is not None
        )

    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of loaded artifacts."""
        return {
            "workspace_path": str(self.workspace_path),
            "session_id": self.session_id,
            "has_hypothesis": self.hypothesis is not None,
            "has_plan": self.plan is not None,
            "has_task_messages": self.task_messages is not None
            and len(self.task_messages or []) > 0,
            "num_task_messages": len(self.task_messages or []),
            "has_literature_context": self.literature_context is not None,
            "has_ranked_papers": self.ranked_papers is not None,
            "num_ranked_papers": len(self.ranked_papers or []),
            "has_literature_report": self.literature_report is not None,
            "literature_report_length": len(self.literature_report or ""),
            "knowledge_base_length": len(self.knowledge_base or ""),
            "loaded_files": self.loaded_files,
            "missing_files": self.missing_files,
            "parse_errors": self.parse_errors,
        }


def _safe_load_json(
    path: Path,
    artifacts: Optional[WorkspaceArtifacts] = None,
) -> Optional[Any]:
    """Safely load a JSON file, returning None on failure.

    Includes a tolerant fallback for common formatting issues in generated JSON
    such as trailing commas or JS-style comments.
    """
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except json.JSONDecodeError as e:
        # Tolerant fallback for malformed generated files.
        try:
            raw = path.read_text(encoding="utf-8")
            cleaned = re.sub(r"/\*.*?\*/", "", raw, flags=re.DOTALL)
            cleaned = re.sub(r"//.*?$", "", cleaned, flags=re.MULTILINE)
            cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)
            parsed = json.loads(cleaned)
            logger.warning(
                f"Recovered malformed JSON from {path} using tolerant parser: {e}"
            )
            if artifacts is not None:
                artifacts.parse_errors.append(
                    f"Recovered malformed JSON: {path.name} ({e})"
                )
            return parsed
        except (json.JSONDecodeError, OSError) as fallback_error:
            logger.warning(f"Failed to load JSON from {path}: {fallback_error}")
            if artifacts is not None:
                artifacts.parse_errors.append(
                    f"Failed JSON parse: {path.name} ({fallback_error})"
                )
    except OSError as e:
        logger.warning(f"Failed to load JSON from {path}: {e}")
        if artifacts is not None:
            artifacts.parse_errors.append(f"Failed JSON read: {path.name} ({e})")
    return None


def _safe_load_text(path: Path) -> Optional[str]:
    """Safely load a text file, returning None on failure."""
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
    except OSError as e:
        logger.warning(f"Failed to load text from {path}: {e}")
    return None


def load_workspace(workspace_path: str | Path) -> WorkspaceArtifacts:
    """
    Load all artifacts from a workspace folder.

    Supports two workspace structures:
    1. Direct workspace folder (workspace_{session_id}/)
    2. Benchmark output folder (benchmark_results/run_*/case_id/)

    Args:
        workspace_path: Path to the workspace folder

    Returns:
        WorkspaceArtifacts containing all loaded data
    """
    workspace = Path(workspace_path).resolve()

    if not workspace.exists():
        raise FileNotFoundError(f"Workspace path does not exist: {workspace}")

    if not workspace.is_dir():
        raise ValueError(f"Workspace path is not a directory: {workspace}")

    artifacts = WorkspaceArtifacts(workspace_path=workspace)

    # Try to extract session ID from folder name
    folder_name = workspace.name
    if folder_name.startswith("workspace_"):
        artifacts.session_id = folder_name[len("workspace_") :]
    elif folder_name.startswith("benchmark_"):
        artifacts.session_id = folder_name

    # Keep loading focused on files expected for each structure to avoid
    # unnecessary "missing file" noise.
    is_benchmark_case = (workspace / "final_state.json").exists() or (
        workspace.parent.name.startswith("run_")
        and "benchmark_results" in str(workspace.parent.parent)
    )

    if is_benchmark_case:
        file_mappings = {
            "final_state.json": ("planner_state", _safe_load_json),
            "literature_review_output/context.json": (
                "literature_context",
                _safe_load_json,
            ),
            "literature_review_output/ranked_papers.json": (
                "ranked_papers",
                _safe_load_json,
            ),
            "literature_review_output/literature_report.md": (
                "literature_report",
                _safe_load_text,
            ),
        }
    else:
        file_mappings = {
            # Planning outputs - direct workspace structure
            "state.json": ("planner_state", _safe_load_json),
            "hypothesis.json": ("hypothesis", _safe_load_json),
            "plan.json": ("plan", _safe_load_json),
            "task_messages.json": ("task_messages", _safe_load_json),
            # Literature review outputs - direct workspace structure
            "literature_context.json": ("literature_context", _safe_load_json),
            "literature_review_output/context.json": (
                "literature_context",
                _safe_load_json,
            ),
            "literature_review_output/ranked_papers.json": (
                "ranked_papers",
                _safe_load_json,
            ),
            "literature_review_output/literature_report.md": (
                "literature_report",
                _safe_load_text,
            ),
        }

    # Load files based on mappings
    for rel_path, (attr_name, loader) in file_mappings.items():
        full_path = workspace / rel_path
        if full_path.exists():
            if loader is _safe_load_json:
                data = loader(full_path, artifacts)
            else:
                data = loader(full_path)
            if data is not None:
                # Handle special case where attribute might already be set
                current_value = getattr(artifacts, attr_name)
                if current_value is None:
                    setattr(artifacts, attr_name, data)
                    artifacts.loaded_files.append(rel_path)
                    logger.debug(f"Loaded {rel_path} -> {attr_name}")
        else:
            artifacts.missing_files.append(rel_path)

    # Try to extract hypothesis and plan from planner_state if not loaded directly
    if artifacts.planner_state:
        if artifacts.hypothesis is None and "hypothesis" in artifacts.planner_state:
            artifacts.hypothesis = artifacts.planner_state["hypothesis"]
            artifacts.loaded_files.append("hypothesis (from state)")

        if artifacts.plan is None and "plan" in artifacts.planner_state:
            artifacts.plan = artifacts.planner_state["plan"]
            artifacts.loaded_files.append("plan (from state)")

        if (
            artifacts.task_messages is None
            and "task_messages" in artifacts.planner_state
        ):
            artifacts.task_messages = artifacts.planner_state["task_messages"]
            artifacts.loaded_files.append("task_messages (from state)")

        if "knowledge_base" in artifacts.planner_state:
            artifacts.knowledge_base = artifacts.planner_state.get("knowledge_base", "")

    # Try to load knowledge base from separate file if not in state
    if not artifacts.knowledge_base:
        kb_path = workspace / "knowledge_base.txt"
        if kb_path.exists():
            artifacts.knowledge_base = _safe_load_text(kb_path)
            artifacts.loaded_files.append("knowledge_base.txt")

    logger.info(
        f"Loaded workspace from {workspace}: {len(artifacts.loaded_files)} files"
    )
    return artifacts


def prepare_judge_artifacts(
    artifacts: WorkspaceArtifacts,
    rubric_names: Optional[List[str]] = None,
    max_text_length: int = 8000,
) -> Dict[str, Any]:
    """
    Prepare artifacts for LLM judge evaluation.

    Formats and truncates artifacts to fit within context limits while preserving
    the most important information.

    Args:
        artifacts: Loaded workspace artifacts
        rubric_names: List of rubric names to prepare artifacts for (None = all)
        max_text_length: Maximum length for text fields

    Returns:
        Dict of formatted artifacts ready for judge evaluation
    """

    def truncate(text: Optional[str], max_len: int = max_text_length) -> str:
        if not text:
            return ""
        if len(text) <= max_len:
            return text
        return text[:max_len] + f"\n...[truncated, {len(text) - max_len} chars omitted]"

    judge_artifacts = {
        "workspace_summary": artifacts.get_summary(),
    }

    # Planning artifacts
    if artifacts.hypothesis:
        judge_artifacts["hypothesis"] = {
            "id": artifacts.hypothesis.get("id", ""),
            "title": artifacts.hypothesis.get("title", ""),
            "population": artifacts.hypothesis.get("population", ""),
            "intervention": artifacts.hypothesis.get("intervention", ""),
            "comparison": artifacts.hypothesis.get("comparison", ""),
            "outcome": artifacts.hypothesis.get("outcome", ""),
            "hypothesis": artifacts.hypothesis.get("hypothesis", ""),
            "question": artifacts.hypothesis.get("question", ""),
            "clinical_context": truncate(
                artifacts.hypothesis.get("clinical_context", ""), 1500
            ),
            "state": artifacts.hypothesis.get("state", ""),
        }

    if artifacts.plan:
        judge_artifacts["plan"] = {
            "hypothesis_id": artifacts.plan.get("hypothesis_id", ""),
            "study_goal": artifacts.plan.get("study_goal", ""),
            "state": artifacts.plan.get("state", ""),
            "steps": [
                {
                    "name": step.get("name", ""),
                    "description": truncate(step.get("description", ""), 500),
                    "module": step.get("module"),
                    "status": step.get("status", ""),
                }
                for step in (artifacts.plan.get("steps") or [])
            ],
            "num_steps": len(artifacts.plan.get("steps") or []),
        }

    if artifacts.task_messages:
        judge_artifacts["task_messages"] = [
            {
                "task_id": tm.get("task_id", ""),
                "phase": tm.get("phase", ""),
                "agent_id": tm.get("agent_id", ""),
                "agent_title": tm.get("agent_title", ""),
                "instructions": truncate(tm.get("instructions", ""), 800),
                "hypothesis_summary": truncate(tm.get("hypothesis_summary", ""), 500),
                "has_context_augmentation": bool(tm.get("context_augmentation")),
                "context_augmentation_fields": list(
                    (tm.get("context_augmentation") or {}).keys()
                ),
            }
            for tm in artifacts.task_messages[:10]  # Limit to first 10
        ]
        judge_artifacts["num_task_messages"] = len(artifacts.task_messages)

    # Literature review artifacts
    if artifacts.literature_context:
        lit_ctx = artifacts.literature_context
        judge_artifacts["literature_context"] = {
            "metadata": lit_ctx.get("metadata", {}),
            "key_findings": (lit_ctx.get("literature_context") or {}).get(
                "key_findings", []
            ),
            "consensus_points": (lit_ctx.get("literature_context") or {}).get(
                "consensus_points", []
            ),
            "contested_areas": (lit_ctx.get("literature_context") or {}).get(
                "contested_areas", []
            ),
            "research_gaps": (lit_ctx.get("literature_context") or {}).get(
                "research_gaps", []
            ),
            "recommended_methods": (lit_ctx.get("literature_context") or {}).get(
                "recommended_methods", []
            ),
            "key_metrics": (lit_ctx.get("literature_context") or {}).get(
                "key_metrics", []
            ),
            "agent_hints": lit_ctx.get("agent_hints", {}),
        }

        # Include top papers summary
        top_papers = lit_ctx.get("top_papers", [])[:5]
        judge_artifacts["top_papers"] = [
            {
                "rank": p.get("rank"),
                "title": p.get("title", ""),
                "year": p.get("year", ""),
                "journal": p.get("journal", ""),
                "relevance_score": p.get("relevance_score"),
                "key_finding": p.get("key_finding", ""),
            }
            for p in top_papers
        ]

    if artifacts.ranked_papers:
        ranked_items: List[Dict[str, Any]] = []
        if isinstance(artifacts.ranked_papers, list):
            ranked_items = [p for p in artifacts.ranked_papers if isinstance(p, dict)]
        elif isinstance(artifacts.ranked_papers, dict):
            nested = artifacts.ranked_papers.get("ranked_papers", [])
            if isinstance(nested, list):
                ranked_items = [p for p in nested if isinstance(p, dict)]

        judge_artifacts["num_ranked_papers"] = len(ranked_items)
        judge_artifacts["paper_tier_distribution"] = _count_paper_tiers(ranked_items)

    if artifacts.literature_report:
        judge_artifacts["literature_report_excerpt"] = truncate(
            artifacts.literature_report, 3000
        )
        judge_artifacts["literature_report_length"] = len(artifacts.literature_report)

    if artifacts.knowledge_base:
        judge_artifacts["knowledge_base_excerpt"] = truncate(
            artifacts.knowledge_base, 2000
        )
        judge_artifacts["knowledge_base_length"] = len(artifacts.knowledge_base)

    return judge_artifacts


def _count_paper_tiers(papers: List[Dict[str, Any]]) -> Dict[str, int]:
    """Count papers by relevance tier."""
    tiers: Dict[str, int] = {}
    for paper in papers:
        tier = paper.get("tier", "unknown")
        tiers[tier] = tiers.get(tier, 0) + 1
    return tiers


def list_available_workspaces(base_path: str | Path) -> List[Dict[str, Any]]:
    """
    List all available workspaces in a directory.

    Args:
        base_path: Base directory to search

    Returns:
        List of workspace info dicts
    """
    base = Path(base_path).resolve()
    workspaces = []

    # Look for workspace_* folders
    for path in base.glob("workspace_*"):
        if path.is_dir():
            workspaces.append(
                {
                    "path": str(path),
                    "name": path.name,
                    "type": "workspace",
                    "has_state": (path / "state.json").exists(),
                    "has_literature": (path / "literature_context.json").exists()
                    or (path / "literature_review_output").exists(),
                }
            )

    # Look for benchmark result folders
    benchmark_results = base / "benchmark_results"
    if benchmark_results.exists():
        for run_dir in benchmark_results.glob("run_*"):
            if run_dir.is_dir():
                for case_dir in run_dir.iterdir():
                    if case_dir.is_dir():
                        workspaces.append(
                            {
                                "path": str(case_dir),
                                "name": f"{run_dir.name}/{case_dir.name}",
                                "type": "benchmark",
                                "has_state": (case_dir / "final_state.json").exists(),
                                "has_literature": (
                                    case_dir / "literature_review_output"
                                ).exists(),
                            }
                        )

    return workspaces


if __name__ == "__main__":
    # Simple test when run directly
    import sys

    if len(sys.argv) > 1:
        workspace_path = sys.argv[1]
        try:
            artifacts = load_workspace(workspace_path)
            print(json.dumps(artifacts.get_summary(), indent=2))
        except Exception as e:
            print(f"Error: {e}")
    else:
        print("Usage: python workspace_evaluator.py <workspace_path>")
