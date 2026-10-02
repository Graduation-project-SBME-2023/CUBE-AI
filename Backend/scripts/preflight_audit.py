"""current build-order this migration — preflight audit."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

MIGRATIONS_DIR = BACKEND_ROOT / "application_api" / "migrations"

# Generated-data roots that sit OUTSIDE the PG+Orthanc barrier and therefore
# need their own backup policy.
_EXTERNAL_AUTHORITATIVE_ROOTS = (
    "jobs",  # MONAI inference/training receipts — authoritative
    "models",  # MONAI weights store
)


# ----------------------------------------------------------------------
# On-disk Alembic graph (no database needed)
# ----------------------------------------------------------------------
def _script_directory():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(BACKEND_ROOT / "application_api" / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return ScriptDirectory.from_config(config)


def disk_heads() -> list[str]:
    return sorted(_script_directory().get_heads())


# ----------------------------------------------------------------------
# ORM expectations (no database needed)
# ----------------------------------------------------------------------
def orm_foreign_keys() -> list[dict[str, Any]]:
    """Every FK the ORM declares, with the ondelete action it asks for."""
    import application_api.db.models  # noqa: F401  (registers every mapper)
    from application_api.db.base import Base

    rows: list[dict[str, Any]] = []
    for table in Base.metadata.sorted_tables:
        for fk in sorted(table.foreign_keys, key=lambda f: str(f.parent)):
            rows.append(
                {
                    "table": table.name,
                    "column": fk.parent.name,
                    "references": f"{fk.column.table.name}.{fk.column.name}",
                    "ondelete": (fk.ondelete or "NO ACTION").upper(),
                }
            )
    return rows


def orm_indexes() -> list[dict[str, Any]]:
    import application_api.db.models  # noqa: F401
    from application_api.db.base import Base

    rows: list[dict[str, Any]] = []
    for table in Base.metadata.sorted_tables:
        for index in sorted(table.indexes, key=lambda i: i.name or ""):
            rows.append(
                {
                    "table": table.name,
                    "name": index.name,
                    "columns": [c.name for c in index.columns],
                    "unique": bool(index.unique),
                }
            )
    return rows


# ----------------------------------------------------------------------
# Live database facts
# ----------------------------------------------------------------------
_CONFDELTYPE = {
    "a": "NO ACTION",
    "r": "RESTRICT",
    "c": "CASCADE",
    "n": "SET NULL",
    "d": "SET DEFAULT",
}

_FK_SQL = """
SELECT
    c.conname                       AS name,
    src.relname                     AS table_name,
    tgt.relname                     AS referenced_table,
    c.confdeltype                   AS delete_action,
    c.confupdtype                   AS update_action,
    pg_get_constraintdef(c.oid)     AS definition
FROM pg_constraint c
JOIN pg_class src ON src.oid = c.conrelid
JOIN pg_class tgt ON tgt.oid = c.confrelid
JOIN pg_namespace n ON n.oid = src.relnamespace
WHERE c.contype = 'f' AND n.nspname = 'public'
ORDER BY src.relname, c.conname
"""

_INDEX_SQL = """
SELECT schemaname, tablename, indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
ORDER BY tablename, indexname
"""

_CHECK_SQL = """
SELECT c.conname AS name, src.relname AS table_name,
       pg_get_constraintdef(c.oid) AS definition
FROM pg_constraint c
JOIN pg_class src ON src.oid = c.conrelid
JOIN pg_namespace n ON n.oid = src.relnamespace
WHERE c.contype = 'c' AND n.nspname = 'public'
ORDER BY src.relname, c.conname
"""

_ROWCOUNT_TABLES = (
    "patients",
    "studies",
    "volume_series",
    "segmentation_masks",
    "radiomics_features",
    "chat_sessions",
    "chat_messages",
    "chat_study_association",
    "hypotheses",
    "session_graphs",
    "graph_nodes",
    "task_messages",
    "context_augmentations",
    "session_events",
    "workflow",
    "workflow_details",
    "inference_models",
)


async def live_facts() -> dict[str, Any]:
    from sqlalchemy import text

    from application_api.db.session import AsyncSessionLocal, engine

    facts: dict[str, Any] = {}
    try:
        async with AsyncSessionLocal() as db:
            stamped = (
                await db.execute(text("SELECT version_num FROM alembic_version"))
            ).scalars().all()
            facts["stamped_revisions"] = sorted(stamped)

            facts["foreign_keys"] = [
                {
                    "name": row.name,
                    "table": row.table_name,
                    "references": row.referenced_table,
                    "on_delete": _CONFDELTYPE.get(row.delete_action, row.delete_action),
                    "on_update": _CONFDELTYPE.get(row.update_action, row.update_action),
                    "definition": row.definition,
                }
                for row in (await db.execute(text(_FK_SQL))).all()
            ]

            facts["indexes"] = [
                {
                    "table": row.tablename,
                    "name": row.indexname,
                    "definition": row.indexdef,
                }
                for row in (await db.execute(text(_INDEX_SQL))).all()
            ]

            facts["check_constraints"] = [
                {
                    "name": row.name,
                    "table": row.table_name,
                    "definition": row.definition,
                }
                for row in (await db.execute(text(_CHECK_SQL))).all()
            ]

            counts: dict[str, int | None] = {}
            for table in _ROWCOUNT_TABLES:
                try:
                    counts[table] = (
                        await db.execute(text(f'SELECT count(*) FROM "{table}"'))
                    ).scalar_one()
                except Exception:  # table absent on this database
                    counts[table] = None
            facts["row_counts"] = counts
    finally:
        await engine.dispose()
    return facts


# ----------------------------------------------------------------------
# Drift analysis
# ----------------------------------------------------------------------
def fk_drift(orm: list[dict[str, Any]], live: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """FKs whose live ON DELETE action differs from the ORM declaration."""
    by_pair: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in live:
        by_pair.setdefault((row["table"], row["references"]), []).append(row)

    drift: list[dict[str, Any]] = []
    for declared in orm:
        pair = (declared["table"], declared["references"].split(".")[0])
        candidates = by_pair.get(pair, [])
        if not candidates:
            drift.append({**declared, "live": None, "issue": "missing_in_database"})
            continue
        if len(candidates) > 1:
            drift.append({**declared, "live": candidates, "issue": "ambiguous"})
            continue
        live_action = candidates[0]["on_delete"]
        if live_action != declared["ondelete"]:
            drift.append(
                {
                    **declared,
                    "live": candidates[0]["name"],
                    "live_ondelete": live_action,
                    "issue": "ondelete_mismatch",
                }
            )
    return drift


# ----------------------------------------------------------------------
# Storage inventory
# ----------------------------------------------------------------------
def _digest(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def storage_inventory(hash_files: bool) -> dict[str, Any]:
    """Inventory Backend/storage, flagging the roots outside the PG barrier."""
    from storage_paths import BACKEND_STORAGE_ROOT  # type: ignore[import-not-found]

    root = Path(BACKEND_STORAGE_ROOT)
    tree: dict[str, Any] = {"root": str(root), "exists": root.exists(), "subtrees": {}}
    if not root.exists():
        return tree

    for child in sorted(p for p in root.iterdir() if p.is_dir()):
        files = [p for p in child.rglob("*") if p.is_file()]
        entry: dict[str, Any] = {
            "file_count": len(files),
            "byte_size": sum(p.stat().st_size for p in files),
            "outside_pg_orthanc_barrier": child.name in _EXTERNAL_AUTHORITATIVE_ROOTS,
        }
        if hash_files:
            entry["files"] = {
                str(p.relative_to(root)).replace(os.sep, "/"): _digest(p) for p in files
            }
        tree["subtrees"][child.name] = entry

    tree["backup_set_must_include"] = [
        name for name in _EXTERNAL_AUTHORITATIVE_ROOTS if name in tree["subtrees"]
    ]
    return tree


# ----------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------
def render(report: dict[str, Any]) -> str:
    lines: list[str] = ["V1 preflight audit (build-order step A)", "=" * 42, ""]

    lines.append(f"Alembic heads on disk: {', '.join(report['disk_heads']) or '(none)'}")
    stamped = report.get("stamped_revisions")
    if stamped is None:
        lines.append("Alembic stamped in database: (offline — not checked)")
    else:
        lines.append(f"Alembic stamped in database: {', '.join(stamped) or '(none)'}")
        if sorted(stamped) != sorted(report["disk_heads"]):
            lines.append("  !! database is NOT at head — upgrade before step B")
    lines.append("")

    drift = report.get("fk_drift")
    if drift is None:
        lines.append("FK drift: (offline — not checked)")
    elif not drift:
        lines.append("FK drift: none — pg_constraint matches the ORM")
    else:
        lines.append(f"FK drift: {len(drift)} finding(s)")
        for row in drift:
            lines.append(
                f"  - {row['table']}.{row['column']} -> {row['references']}: "
                f"ORM says {row['ondelete']}, database says "
                f"{row.get('live_ondelete', row['issue'])}"
            )
    lines.append("")

    inventory = report["storage"]
    lines.append(f"Storage root: {inventory['root']} (exists={inventory['exists']})")
    for name, entry in sorted(inventory.get("subtrees", {}).items()):
        flag = " [OUTSIDE PG+ORTHANC BACKUP BARRIER]" if entry["outside_pg_orthanc_barrier"] else ""
        lines.append(
            f"  {name}: {entry['file_count']} files, {entry['byte_size']} bytes{flag}"
        )
    must = inventory.get("backup_set_must_include") or []
    if must:
        lines.append(
            "  Backup set MUST include and hash-verify: " + ", ".join(must)
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="skip every database query")
    parser.add_argument("--json", type=Path, help="also write the full report as JSON")
    parser.add_argument(
        "--hash-files",
        action="store_true",
        help="digest every file under Backend/storage (slow; needed to prove a restore)",
    )
    args = parser.parse_args(argv)

    report: dict[str, Any] = {
        "disk_heads": disk_heads(),
        "orm_foreign_keys": orm_foreign_keys(),
        "orm_indexes": orm_indexes(),
        "storage": storage_inventory(args.hash_files),
        "stamped_revisions": None,
        "fk_drift": None,
    }

    if not args.offline:
        live = asyncio.run(live_facts())
        report.update(live)
        report["fk_drift"] = fk_drift(report["orm_foreign_keys"], live["foreign_keys"])

    print(render(report))
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"\nJSON report written to {args.json}")

    # Non-zero only when a live audit found the database off-head; FK drift is
    # expected before step B and is informational, not a failure.
    if not args.offline and sorted(report.get("stamped_revisions") or []) != sorted(
        report["disk_heads"]
    ):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
