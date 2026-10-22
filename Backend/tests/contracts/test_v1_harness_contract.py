"""Static contract of the harness persistence.

- every write on a fenced table declares its write path (a static
  scan): a write route either takes its session from `fenced_db` (the path the
  caller declared) or calls repository functions that open `fenced_transaction`;
- every producer in the allow-lists exists (the catalog, the legacy agents, or
  the modeling job tools still to come);
- the ORM states the constraints the repositories rely on.

The live behaviour is in tests/integration/test_v1_p_transactions_live.py.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import sys
from functools import lru_cache
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

BACKEND = Path(__file__).resolve().parents[2]
API = BACKEND / "application_api"
MIGRATION = API / "migrations" / "versions" / "p1f0a2c3d4e5_v1_p_session_graph.py"
# The fenced tables as of the latest revision that changed them.
FENCING_MIGRATION = API / "migrations" / "versions" / "t1f0a2c3d4e5_v1_t_agent_transcripts.py"
WRITE_METHODS = {"post", "put", "patch", "delete"}
# A repository function declares a path when it opens one itself, or runs on
# the shared lifecycle transition (which opens one).
DECLARES = ("fenced_transaction(", "@on_path(", "transition(db")
# A repository function writes when it commits, flushes or publishes.
WRITES = ("commit(", "flush(", "publication(", "db.add(", "_commit(", "@on_path(", "transition(db")


@lru_cache(maxsize=None)
def _load(path: Path):
    spec = importlib.util.spec_from_file_location(f"_cubeai_{path.stem}_harness", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration():
    return _load(MIGRATION)


def _fenced_tables() -> set[str]:
    return set(_load(FENCING_MIGRATION).FENCED_TABLES)


@lru_cache(maxsize=None)
def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _functions(path: Path) -> dict[str, str]:
    source = path.read_text(encoding="utf-8")
    return {
        node.name: ast.get_source_segment(source, node) or ""
        for node in _parse(path).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    } | {
        # Decorators are outside the segment; keep `@on_path(` visible.
        node.name: "".join(f"@{ast.unparse(d)}\n" for d in node.decorator_list)
        + (ast.get_source_segment(source, node) or "")
        for node in _parse(path).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.decorator_list
    }


def _fenced_model_modules() -> set[str]:
    """Model modules (named after their table) that map a fenced table."""
    fenced = _fenced_tables()
    modules = set()
    for path in (API / "db" / "models").glob("*.py"):
        module = importlib.import_module(f"application_api.db.models.{path.stem}")
        tables = {getattr(value, "__tablename__", None) for value in vars(module).values()}
        if tables & fenced:
            modules.add(path.stem)
    return modules


def _repository_imports(path: Path) -> dict[str, str]:
    """alias -> repository module name, and name -> module for `from x import fn`."""
    aliases: dict[str, str] = {}
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "application_api.repositories":
                for name in node.names:
                    aliases[name.asname or name.name] = name.name
            elif node.module.startswith("application_api.repositories."):
                module = node.module.rsplit(".", 1)[1]
                for name in node.names:
                    aliases[name.asname or name.name] = f"{module}:{name.name}"
    return aliases


def _repository_writes_fenced(module: str, fenced_models: set[str]) -> bool:
    path = API / "repositories" / f"{module}.py"
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("application_api.db.models."):
            if node.module.rsplit(".", 1)[1] in fenced_models:
                return True
    return False


def _declares(module: str, function: str, depth: int = 3) -> bool:
    path = API / "repositories" / f"{module}.py"
    functions = _functions(path)
    body = functions.get(function, "")
    if any(marker in body for marker in DECLARES):
        return True
    if depth == 0:
        return False
    aliases = _repository_imports(path)
    for call in ast.walk(ast.parse(body or "pass")):
        if not isinstance(call, ast.Call):
            continue
        target = call.func
        if isinstance(target, ast.Name) and target.id in functions and target.id != function:
            if _declares(module, target.id, depth - 1):
                return True
        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
            other = aliases.get(target.value.id)
            if other and ":" not in other and _declares(other, target.attr, depth - 1):
                return True
    return False


def _write_routes():
    for path in sorted((API / "api" / "routes").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for node in _parse(path).body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            methods = {
                d.func.attr for d in node.decorator_list
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
            }
            if methods & WRITE_METHODS:
                yield path, node, ast.get_source_segment(source, node) or ""


def test_every_fenced_write_declares_its_path():
    """A static scan, beside the database's own refusal."""
    fenced_models = _fenced_model_modules()
    missing = []
    for path, node, source in _write_routes():
        if "Depends(fenced_db)" in source:
            continue
        aliases = _repository_imports(path)
        for call in ast.walk(node):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)):
                continue
            module = aliases.get(call.func.value.id)
            if module is None or ":" in module or not _repository_writes_fenced(module, fenced_models):
                continue
            body = _functions(API / "repositories" / f"{module}.py").get(call.func.attr, "")
            if not any(marker in body for marker in WRITES):
                continue  # a read
            if not _declares(module, call.func.attr):
                missing.append(f"{path.name}:{node.name} -> {module}.{call.func.attr}")
    assert not missing, (
        "These write routes reach a repository that writes a fenced table without a "
        f"declared write path (use Depends(fenced_db) or fenced_transaction): {missing}"
    )


def test_the_scan_sees_the_fenced_repositories():
    """Guard the guard: the scan must recognise the tables it protects."""
    fenced_models = _fenced_model_modules()
    assert {"node_runs", "run_attempts", "agent_transcript_entries", "cohorts", "stored_files"} <= fenced_models
    for module in ("graph_changes", "run_attempts", "node_runs", "cohorts", "agent_transcript_entries"):
        assert _repository_writes_fenced(module, fenced_models)
    assert not _repository_writes_fenced("main_transcript_entries", fenced_models)  # not an execution table
    assert _declares("graph_changes", "submit_change")
    assert _declares("sessions", "update_settings")  # through run_attempts
    assert not _declares("chats", "accept_input")


# Producers still to come (Plan 7.3). Each must be absent from the catalog
# today; when one lands, this list shrinks. The five modeling tools landed in
# Phase 7.1.
PLAN_7_PRODUCERS = {"run_modeling"}


@pytest.mark.skipif(sys.version_info < (3, 13), reason="the tool catalog needs Python 3.13")
def test_every_producer_exists():
    from agent_system.toolkit import catalog as toolkit_catalog
    from application_api.core.agent_types import CANONICAL_AGENT_TYPES

    built = toolkit_catalog.build()
    names = {getattr(item, "name", item) for registry in (built.tools, built.agents) for item in registry}
    producers = {name for names_ in _migration().PRODUCERS.values() for name in names_}
    unknown = producers - names - set(CANONICAL_AGENT_TYPES) - PLAN_7_PRODUCERS
    assert not unknown, f"allow-listed producers that exist nowhere: {sorted(unknown)}"
    landed = PLAN_7_PRODUCERS & names
    assert not landed, f"{sorted(landed)} now exist; remove them from PLAN_7_PRODUCERS"


# ----------------------------------------------------------------------
# The ORM states the constraints the repositories rely on
# ----------------------------------------------------------------------
def _table(name: str):
    import application_api.db.models  # noqa: F401
    from application_api.db.base import Base

    return Base.metadata.tables[name]


def _index(table: str, name: str):
    return next(index for index in _table(table).indexes if index.name == name)


@pytest.mark.parametrize(
    "table,index,where",
    [
        ("run_attempts", "uq_run_attempts_one_active_per_run", "suspended"),
        ("run_attempts", "uq_run_attempts_one_active_per_session", "suspended"),
        ("graph_changes", "uq_graph_changes_one_pending", "pending"),
        ("node_runs", "uq_node_runs_one_current", "superseded_at IS NULL"),
        ("training_jobs", "uq_training_jobs_one_active", "ended_at IS NULL"),
    ],
)
def test_partial_unique_indexes(table, index, where):
    found = _index(table, index)
    assert found.unique
    assert where in str(found.dialect_options["postgresql"]["where"])


def test_the_attempt_states_match_the_migration():
    from application_api.db.models.run_attempts import ACTIVE_ATTEMPT_STATES, TERMINAL_ATTEMPT_STATES

    assert ACTIVE_ATTEMPT_STATES == _migration().ACTIVE_ATTEMPT_STATES
    assert TERMINAL_ATTEMPT_STATES == _migration().TERMINAL_ATTEMPT_STATES


@pytest.mark.parametrize(
    "table,checks",
    [
        ("graph_changes", {"status", "applied_iff_version", "applied_version_follows_base",
                           "decided_iff_decision", "reasons_need_a_user", "content_digest_is_sha256"}),
        ("run_attempts", {"state", "ended_at_iff_terminal", "dispatched_before_running",
                          "failed_has_error_code", "suspension_shape"}),
        ("sessions", {"control_state", "execution_mode", "privacy_mode"}),
        ("training_jobs", {"outcome", "ended_at_iff_outcome"}),
        # Phase 6.6 (migration "t").
        ("graph_nodes", {"approval", "start_release_needs_before"}),
        ("node_runs", {"downstream_hold"}),
        ("cohorts", {"frozen_by", "frozen_by_iff_frozen"}),
        ("agent_transcript_entries", {"entry_uuid_not_blank", "entry_bounded"}),
        ("main_transcript_entries", {"entry_uuid_not_blank", "entry_bounded"}),
    ],
)
def test_named_checks(table, checks):
    names = {
        constraint.name for constraint in _table(table).constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    assert {f"ck_{table}_{check}" for check in checks} <= names


@pytest.mark.parametrize(
    "table,columns,target",
    [
        ("graph_nodes", ("accepted_by_change_id", "session_id"), "graph_changes"),
        ("node_runs", ("accepted_by_change_id", "session_id"), "graph_changes"),
        ("node_runs", ("hold_released_by_change_id", "session_id"), "graph_changes"),
        ("run_attempts", ("run_id", "session_id"), "node_runs"),
        ("graph_nodes", ("start_released_by_change_id", "session_id"), "graph_changes"),
        ("agent_transcript_entries", ("run_id", "session_id"), "node_runs"),
        ("agent_transcript_entries", ("attempt_id", "run_id"), "run_attempts"),
    ],
)
def test_composite_foreign_keys(table, columns, target):
    fks = {
        (tuple(c.name for c in fk.columns), fk.referred_table.name)
        for fk in _table(table).foreign_key_constraints
    }
    assert (columns, target) in fks


def test_the_graph_content_digest_is_the_one_the_migration_uses():
    from application_api.core.canonical_json import content_digest
    from application_api.core.graph_content import ChangeContent

    content = ChangeContent.model_validate({"v": 1, "nodes": []})
    assert content.digest() == content_digest(content.document())
