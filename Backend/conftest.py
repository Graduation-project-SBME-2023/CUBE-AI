from __future__ import annotations

import atexit
import json
import os
import shutil
import sys
import tempfile
from fnmatch import fnmatchcase
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent
AGENT_SYSTEM_ROOT = BACKEND_ROOT / "agent_system"
RADIOLOGY_ROOT = BACKEND_ROOT / "apps" / "radiology"

# In the MONAI runtime, preload the installed framework before keeping Backend/
# on sys.path. Pytest adds its root directory before loading conftest, and this
# repository also contains Backend/monailabel; temporarily removing Backend/
# prevents that service package from shadowing monailabel.transform.
_backend_entries = [
    entry
    for entry in list(sys.path)
    if entry and Path(entry).resolve() == BACKEND_ROOT.resolve()
]
for entry in _backend_entries:
    sys.path.remove(entry)
_shadowed_monailabel = {
    name: module
    for name, module in list(sys.modules.items())
    if name == "monailabel" or name.startswith("monailabel.")
}
for name in _shadowed_monailabel:
    sys.modules.pop(name, None)
try:  # pragma: no cover - available only in the radiology container
    import monailabel.transform.writer  # noqa: F401
except ImportError:
    for name, module in _shadowed_monailabel.items():
        sys.modules[name] = module
finally:
    sys.path[:0] = _backend_entries

# The application currently supports both ``agent_system.*`` imports and legacy
# imports such as ``agents.*``/``services.*``.  Make both forms collect reliably
# when pytest is launched from the repository root or from Backend/.
_IMPORT_ROOTS = (
    str(BACKEND_ROOT),
    str(AGENT_SYSTEM_ROOT),
    str(RADIOLOGY_ROOT),
)
for import_root in reversed(_IMPORT_ROOTS):
    if import_root in sys.path:
        sys.path.remove(import_root)
    sys.path.insert(0, import_root)

# storage_paths resolves its constants at import time.  Force every writable
# backend location into a process-specific temporary tree before test modules
# import it.  Explicit assignment is intentional: a developer may have runtime
# variables pointing at real data, and tests must never write there.
_RUNTIME_ROOT = Path(tempfile.mkdtemp(prefix="cubeai-backend-pytest-"))
atexit.register(shutil.rmtree, _RUNTIME_ROOT, ignore_errors=True)

_ISOLATED_PATHS = {
    "BACKEND_STORAGE_ROOT": _RUNTIME_ROOT,
    "AGENTS_WORKSPACE_DIR": _RUNTIME_ROOT / "hypotheses",
    "MODELS_DIR": _RUNTIME_ROOT / "models",
    "GENERAL_DATA_DIR": _RUNTIME_ROOT / "data" / "medical",
    "NNUNET_DIR": _RUNTIME_ROOT / "data" / "nnunet",
    "INFERENCE_JOB_DIR": _RUNTIME_ROOT / "jobs" / "inference",
    "INGEST_JOB_DIR": _RUNTIME_ROOT / "jobs" / "ingestion",
    "BACKEND_TEMP_DIR": _RUNTIME_ROOT / "temp",
    "BACKEND_LOG_DIR": _RUNTIME_ROOT / "logs",
}
for variable, isolated_path in _ISOLATED_PATHS.items():
    os.environ[variable] = str(isolated_path)

# services/internal_auth.py refuses to let agent_system's app.py import
# unless WS_INTERNAL_TOKEN is set (or the dev bypass is). Tests that need to
# exercise the unset/bypass paths (e.g. test_internal_auth.py) monkeypatch
# this away for the duration of that one test; every other test gets a fixed
# synthetic token regardless of what a developer's real deploy/.env has.
os.environ["WS_INTERNAL_TOKEN"] = "test-internal-token"
# The Application API reads its settings at import. Deterministic tests never
# connect: these placeholders only let the app and its repositories import.
for _name, _value in (
    ("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:9/unused"),
    ("ORTHANC_URL", "http://orthanc.invalid"),
    ("ORTHANC_USER", "test"),
    ("ORTHANC_PASSWORD", "test"),
):
    os.environ.setdefault(_name, _value)
os.environ.pop("WS_INTERNAL_TOKEN_DEV_BYPASS", None)


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    """Return an empty, isolated workspace root for one test."""
    workspace = tmp_path / "workspace_test-session"
    workspace.mkdir()
    return workspace


@pytest.fixture
def write_workspace_json() -> Callable[[Path, str, Any], Path]:
    """Write JSON through the application's logical workspace mapping."""
    from storage_paths import workspace_subpath

    def _write(workspace: Path, logical_path: str, value: Any) -> Path:
        destination = workspace_subpath(workspace, logical_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(value, indent=2),
            encoding="utf-8",
        )
        return destination

    return _write


@pytest.fixture
def named_tools() -> Callable[..., list[SimpleNamespace]]:
    """Build lightweight MCP tool descriptors without starting a subprocess."""
    def _build(*names: str) -> list[SimpleNamespace]:
        return [SimpleNamespace(name=name) for name in names]

    return _build


@pytest.fixture
def capture_agent_init(monkeypatch: pytest.MonkeyPatch):
    """Capture an agent factory's constructor arguments.

    AutoGen validates a real model client in ``AssistantAgent.__init__``. Factory
    unit tests are concerned with wiring, so bypass that constructor and record
    exactly what the factory selected.
    """
    def _install(agent_class):
        captured: dict[str, Any] = {}

        def _capturing_init(self, *args, **kwargs):
            captured["args"] = args
            captured.update(kwargs)

        monkeypatch.setattr(agent_class, "__init__", _capturing_init)
        return captured

    return _install


# Tests of the AutoGen pipeline the toolkit replaces; the deterministic suite
# leaves them out. Patterns are relative to Backend/tests; ``::Class`` limits a
# file to the test classes that drive AutoGen agents or the GraphFlow monitor.
LEGACY_AUTOGEN_TESTS = (
    "unit/agents/test_*_agent.py",
    "pipeline/test_pipeline_builder.py",
    "pipeline/test_pipeline_orchestration.py",
    "regression/agent_system/test_pipeline_failure_guard.py",
    "regression/agent_system/test_segmentation_hardening.py::SegmentationPlannerTests",
    "regression/agent_system/test_segmentation_hardening.py::PipelineFailureTests",
)
TESTS_ROOT = BACKEND_ROOT / "tests"


def _is_legacy_autogen(item: pytest.Item) -> bool:
    try:
        relative = Path(item.path).resolve().relative_to(TESTS_ROOT.resolve()).as_posix()
    except ValueError:
        return False
    test_class = getattr(item, "cls", None)
    for pattern in LEGACY_AUTOGEN_TESTS:
        path, _, class_name = pattern.partition("::")
        if fnmatchcase(relative, path) and (
            not class_name or (test_class is not None and test_class.__name__ == class_name)
        ):
            return True
    return False


# The tool platform uses Python 3.13 syntax (PEP 695), so these paths cannot even
# be imported by an older interpreter. The images and CI run 3.13; a local venv
# that is still on 3.11 leaves them out of collection (the report header says
# so) rather than failing it. Paths are relative to Backend/.
PY313_ONLY_TESTS = (
    "tests/unit/toolkit",
    "tests/unit/toolpacks",
    "tests/unit/functions",
    "tests/contracts/test_agent_output_contract.py",
    # Plan 8: the planning code reads the toolkit catalog.
    "tests/unit/planning/test_graph_model.py",
    "tests/unit/planning/test_inputs.py",
    "tests/unit/planning/test_validator.py",
    "tests/unit/execution/test_affected.py",
    "tests/unit/execution/test_diff_engine.py",
    "tests/contracts/test_graph_change_contract.py",
    # Plan 5: compares jobs_core's copies with the toolkit's rules.
    "tests/unit/jobs/test_contract_matches_toolkit.py",
    # Plans 3 and 12: the agent packs and the harness load the toolkit catalog.
    "tests/unit/agentpacks",
    "tests/unit/harness",
)
collect_ignore = list(PY313_ONLY_TESTS) if sys.version_info < (3, 13) else []


def pytest_report_header() -> str | None:
    if not collect_ignore:
        return None
    return f"Python < 3.13: not collecting {', '.join(collect_ignore)}"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Classify consolidated legacy suites without editing every old file."""
    for item in items:
        if _is_legacy_autogen(item):
            item.add_marker(pytest.mark.legacy_autogen)
        normalized_path = str(item.path).replace("\\", "/")
        if "/tests/regression/agent_system/" in normalized_path:
            item.add_marker(pytest.mark.regression)
        elif "/tests/regression/radiology/" in normalized_path:
            item.add_marker(pytest.mark.regression)
            item.add_marker(pytest.mark.radiology)
        elif "/tests/unit/radiology/" in normalized_path:
            item.add_marker(pytest.mark.radiology)
