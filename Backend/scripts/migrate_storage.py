#!/usr/bin/env python3
"""Migrate legacy source-adjacent runtime data into Backend/storage.

Cross-platform port of ``migrate_storage.ps1`` so the same migration runs from
``scripts/launcher.linux.sh`` and ``scripts/launch.ps1``.  PowerShell is not available on the
Linux/macOS machines collaborators use, but Python already is.

Modes
-----
--dry-run   Build the plan and print it.  Default when no mode is given.
--execute   Build the plan, write a manifest + rollback script, then move files.
--verify    Re-check a manifest against what is on disk.
--auto      Silent no-op unless legacy data is present; otherwise --execute
            followed by an inline verify.  This is what the launchers call.

Moves stay on one volume, so they are metadata-only and effectively instant.
Nothing is deleted: name collisions and unrecognised entries are routed into
``storage/_migrated-legacy/<origin>/`` with their provenance preserved.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
STORAGE_ROOT = BACKEND_ROOT / "storage"
LEGACY_ROOT = STORAGE_ROOT / "_migrated-legacy"
HASH_LIMIT_BYTES = 8 * 1024 * 1024

# Placeholder files that git tracks — never relocate them.
PLACEHOLDER_NAMES = {".keep", ".gitkeep"}

# Logical -> physical workspace map.  Backend/storage_paths.py is the source of
# truth; these are duplicated so the script runs standalone, and checked against
# it at startup when it is importable.
STATE_FILES = (
    "state.json",
    "scratchpad.json",
    "config.json",
    "segmentation_manifest.json",
    "review_draft.json",
    "review_result.json",
    "literature_context.json",
)
WORKSPACE_DIR_MAP: dict[str, tuple[str, ...]] = {
    "populations": ("data", "populations"),
    "volumes": ("data", "volumes"),
    "masks": ("data", "masks"),
    "outputs": ("outputs", "agents"),
    "literature_review_output": ("outputs", "literature"),
    "overlays": ("outputs", "overlays"),
    "features": ("outputs", "features"),
    "modeling": ("outputs", "modeling"),
    "models": ("outputs", "modeling"),
    "reports": ("outputs", "reports"),
    ".knowledge_cache": ("cache", "literature"),
    "temp_segmentations": ("temp", "segmentations"),
}
CLINICAL_FILES = ("clinical_data.json", "clinical_data.csv")

WORKSPACE_SKELETON = (
    ("state",),
    ("data", "populations"),
    ("data", "volumes"),
    ("data", "masks"),
    ("outputs", "agents"),
    ("outputs", "literature"),
    ("outputs", "overlays"),
    ("outputs", "features"),
    ("outputs", "modeling"),
    ("outputs", "reports"),
    ("cache", "literature"),
    ("temp", "segmentations"),
)

# Roots emptied by a successful migration; probed by --auto.
LEGACY_PROBES = (
    BACKEND_ROOT / "agents_workspace",
    BACKEND_ROOT / "apps" / "radiology" / "General_Data",
    BACKEND_ROOT / "apps" / "radiology" / "Labels",
    BACKEND_ROOT / "apps" / "radiology" / "Data" / "output",
    BACKEND_ROOT / "apps" / "radiology" / "NN_Unet",
    BACKEND_ROOT / "apps" / "radiology" / "inference_jobs",
    BACKEND_ROOT / "agent_system" / "logs",
    BACKEND_ROOT / "agent_system" / "agents" / "literature_review_agent" / ".knowledge_cache",
    BACKEND_ROOT / "agent_system" / "agents" / "literature_review_agent" / "literature_review_output",
)

CLEANUP_ROOTS = (
    BACKEND_ROOT / "agents_workspace",
    BACKEND_ROOT / "apps" / "radiology" / "General_Data",
    BACKEND_ROOT / "apps" / "radiology" / "Labels",
    BACKEND_ROOT / "apps" / "radiology" / "Data" / "output",
    BACKEND_ROOT / "apps" / "radiology" / "NN_Unet",
    BACKEND_ROOT / "apps" / "radiology" / "inference_jobs",
    BACKEND_ROOT / "agent_system" / "logs",
)


# ---------------------------------------------------------------- helpers


def human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"


def iter_files(root: Path):
    """Yield every file under *root*, skipping tracked placeholders."""
    if not root.is_dir():
        return
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in sorted(filenames):
            if name in PLACEHOLDER_NAMES:
                continue
            yield Path(dirpath) / name


def has_payload(root: Path) -> bool:
    """True if *root* holds at least one non-placeholder file. Short-circuits."""
    for _ in iter_files(root):
        return True
    return False


def small_file_hash(path: Path, size: int) -> str | None:
    if size >= HASH_LIMIT_BYTES:
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def check_map_drift() -> None:
    """Warn if storage_paths.py and this script have diverged."""
    sys.path.insert(0, str(BACKEND_ROOT))
    try:
        import storage_paths  # type: ignore
    except Exception:
        return
    finally:
        sys.path.pop(0)

    if set(storage_paths._STATE_FILES) != set(STATE_FILES):
        print("[!] state-file map differs from storage_paths.py", file=sys.stderr)
    if storage_paths._WORKSPACE_DIR_MAP != WORKSPACE_DIR_MAP:
        print("[!] workspace dir map differs from storage_paths.py", file=sys.stderr)


# ---------------------------------------------------------------- planning


class Plan:
    def __init__(self) -> None:
        self.operations: list[dict] = []
        self.reserved: set[str] = set()
        self.workspaces: list[Path] = []

    # Case-insensitive on Windows, exact on POSIX — matches the filesystem.
    @staticmethod
    def _key(path: Path) -> str:
        return os.path.normcase(str(path))

    def _conflict_destination(self, origin: str, relative: str) -> Path:
        candidate = LEGACY_ROOT / origin / relative
        index = 1
        while candidate.exists() or self._key(candidate) in self.reserved:
            candidate = candidate.with_name(f"{candidate.name}.conflict-{index}")
            index += 1
        return candidate

    def add_file(self, source: Path, destination: Path, origin: str, action: str = "Move") -> None:
        if not source.is_file() or source.name in PLACEHOLDER_NAMES:
            return
        stat = source.stat()
        relative = os.path.relpath(source, BACKEND_ROOT)
        final = destination.resolve() if destination.is_absolute() else destination
        conflict = False
        if final.exists() or self._key(final) in self.reserved:
            final = self._conflict_destination(origin, relative)
            conflict = True
        self.reserved.add(self._key(final))
        self.operations.append(
            {
                "action": action,
                "source": str(source),
                "destination": str(final),
                "origin": origin,
                "relative_path": relative,
                "size": stat.st_size,
                "mtime_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                "sha256": small_file_hash(source, stat.st_size),
                "conflict": conflict,
            }
        )

    def add_tree(self, source_root: Path, destination_root: Path, origin: str, action: str = "Move") -> None:
        for source in iter_files(source_root):
            relative = source.relative_to(source_root)
            self.add_file(source, destination_root / relative, origin, action)

    def add_workspace(self, source: Path) -> None:
        destination = STORAGE_ROOT / "hypotheses" / source.name
        self.workspaces.append(destination)
        known: set[str] = set()

        for name in STATE_FILES:
            known.add(name)
            self.add_file(source / name, destination / "state" / name, source.name)

        for logical, physical in WORKSPACE_DIR_MAP.items():
            known.add(logical)
            self.add_tree(source / logical, destination.joinpath(*physical), source.name)

        for name in CLINICAL_FILES:
            known.add(name)
            self.add_file(source / name, destination / "data" / "populations" / name, source.name)

        for entry in sorted(source.iterdir(), key=lambda p: p.name):
            if entry.name in known or entry.name in PLACEHOLDER_NAMES:
                continue
            target = LEGACY_ROOT / source.name / entry.name
            if entry.is_dir():
                self.add_tree(entry, target, source.name)
            else:
                self.add_file(entry, target, source.name)


def build_plan() -> Plan:
    plan = Plan()
    radiology = BACKEND_ROOT / "apps" / "radiology"

    workspace_root = BACKEND_ROOT / "agents_workspace"
    if workspace_root.is_dir():
        for entry in sorted(workspace_root.iterdir(), key=lambda p: p.name):
            if entry.is_dir() and entry.name.startswith("workspace_"):
                plan.add_workspace(entry)
            elif entry.name not in PLACEHOLDER_NAMES:
                target = LEGACY_ROOT / "agents_workspace" / entry.name
                if entry.is_dir():
                    plan.add_tree(entry, target, "agents_workspace")
                else:
                    plan.add_file(entry, target, "agents_workspace")

    plan.add_tree(radiology / "General_Data", STORAGE_ROOT / "data" / "medical" / "images", "General_Data")
    plan.add_tree(radiology / "Labels", STORAGE_ROOT / "data" / "medical" / "labels", "Labels")
    plan.add_tree(radiology / "Data" / "output", STORAGE_ROOT / "data" / "medical" / "exports", "Data-output")
    plan.add_tree(radiology / "inference_jobs", STORAGE_ROOT / "jobs" / "inference", "inference_jobs")
    plan.add_tree(radiology / "logs", STORAGE_ROOT / "logs" / "radiology", "radiology-logs")

    nnunet_source = radiology / "NN_Unet"
    for name in ("nnUNet_raw", "nnUNet_preprocessed", "nnUNet_results"):
        plan.add_tree(nnunet_source / name, STORAGE_ROOT / "data" / "nnunet" / name, "NN_Unet")
    if nnunet_source.is_dir():
        for entry in sorted(nnunet_source.iterdir(), key=lambda p: p.name):
            if entry.is_file():
                plan.add_file(entry, STORAGE_ROOT / "data" / "nnunet" / "mappings" / entry.name, "NN_Unet")

    models_source = BACKEND_ROOT / "models"
    # .cache/huggingface/... -> cache/huggingface/... (mapping the parent avoids
    # the doubled cache/huggingface/huggingface/ the PowerShell version produced)
    plan.add_tree(models_source / ".cache", STORAGE_ROOT / "cache", "models-cache")
    plan.add_tree(models_source / "eval", STORAGE_ROOT / "temp" / "vista" / "models-eval", "models-eval")
    plan.add_tree(models_source / "configs", STORAGE_ROOT / "models" / "configs", "models-configs", action="Copy")
    if models_source.is_dir():
        for entry in sorted(models_source.iterdir(), key=lambda p: p.name):
            if entry.name in {".cache", "eval", "configs"} or entry.name in PLACEHOLDER_NAMES:
                continue
            target = STORAGE_ROOT / "models" / entry.name
            if entry.is_dir():
                plan.add_tree(entry, target, "models")
            else:
                plan.add_file(entry, target, "models")

    agent_system = BACKEND_ROOT / "agent_system"
    literature = agent_system / "agents" / "literature_review_agent"
    plan.add_tree(agent_system / "logs", STORAGE_ROOT / "logs" / "agent-system", "agent-system-logs")
    plan.add_tree(literature / ".knowledge_cache", STORAGE_ROOT / "cache" / "literature", "literature-cache")
    plan.add_tree(literature / "literature_review_output", LEGACY_ROOT / "literature_review_output", "literature-output")

    vista_root = radiology / "vista3d"
    if vista_root.is_dir():
        for entry in sorted(vista_root.iterdir(), key=lambda p: p.name):
            if entry.is_dir() and entry.name.startswith("tmp"):
                plan.add_tree(entry, STORAGE_ROOT / "temp" / "vista" / entry.name, "vista3d")
        vista_eval = vista_root / "eval"
        if vista_eval.is_dir():
            for entry in sorted(vista_eval.iterdir(), key=lambda p: p.name):
                if entry.is_dir() and entry.name.startswith("tmp"):
                    plan.add_tree(entry, STORAGE_ROOT / "temp" / "vista" / f"eval-{entry.name}", "vista3d-eval")

    return plan


# ---------------------------------------------------------------- execution


def write_manifest(plan: Plan, timestamp: str) -> Path:
    LEGACY_ROOT.mkdir(parents=True, exist_ok=True)
    manifest_path = LEGACY_ROOT / f"manifest-{timestamp}.json"
    manifest = {
        "version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "backend_root": str(BACKEND_ROOT),
        "storage_root": str(STORAGE_ROOT),
        "manifest_path": str(manifest_path),
        "workspace_count": len(plan.workspaces),
        "file_count": len(plan.operations),
        "total_bytes": sum(op["size"] for op in plan.operations),
        "hash_limit_bytes": HASH_LIMIT_BYTES,
        "operations": plan.operations,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path


def write_rollback(plan: Plan, timestamp: str) -> Path:
    rollback_path = LEGACY_ROOT / f"rollback-{timestamp}.py"
    lines = [
        "#!/usr/bin/env python3",
        '"""Generated by migrate_storage.py. Run only after stopping all backend writers."""',
        "import shutil",
        "from pathlib import Path",
        "",
        "OPERATIONS = [",
    ]
    for op in reversed(plan.operations):
        lines.append(f"    ({op['action']!r}, {op['source']!r}, {op['destination']!r}),")
    lines += [
        "]",
        "",
        "for action, source, destination in OPERATIONS:",
        "    src, dst = Path(source), Path(destination)",
        "    if not dst.exists():",
        "        continue",
        "    if action == 'Copy':",
        "        dst.unlink()",
        "        continue",
        "    src.parent.mkdir(parents=True, exist_ok=True)",
        "    shutil.move(str(dst), str(src))",
        "",
        "print(f'Reverted {len(OPERATIONS)} operations.')",
        "",
    ]
    rollback_path.write_text("\n".join(lines), encoding="utf-8")
    return rollback_path


def execute(plan: Plan) -> tuple[Path, Path]:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    manifest_path = write_manifest(plan, timestamp)
    rollback_path = write_rollback(plan, timestamp)

    for workspace in plan.workspaces:
        for parts in WORKSPACE_SKELETON:
            workspace.joinpath(*parts).mkdir(parents=True, exist_ok=True)

    for op in plan.operations:
        destination = Path(op["destination"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        if op["action"] == "Copy":
            shutil.copy2(op["source"], destination)
        else:
            shutil.move(op["source"], str(destination))

    prune_empty_dirs()
    return manifest_path, rollback_path


def prune_empty_dirs() -> None:
    for root in CLEANUP_ROOTS:
        if not root.is_dir():
            continue
        for dirpath, _dirnames, _filenames in sorted(os.walk(root), reverse=True):
            path = Path(dirpath)
            try:
                if not any(path.iterdir()):
                    path.rmdir()
            except OSError:
                pass


def verify(manifest_path: Path | None) -> int:
    if manifest_path is None:
        candidates = sorted(LEGACY_ROOT.glob("manifest-*.json"), key=lambda p: p.stat().st_mtime)
        if not candidates:
            print(f"[ERROR] No migration manifest found under {LEGACY_ROOT}", file=sys.stderr)
            return 1
        manifest_path = candidates[-1]

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    failures: list[str] = []
    verified_bytes = 0

    for op in manifest["operations"]:
        destination = Path(op["destination"])
        if not destination.is_file():
            failures.append(f"Missing destination: {destination}")
            continue
        if destination.stat().st_size != op["size"]:
            failures.append(f"Size mismatch: {destination}")
            continue
        if op["sha256"]:
            actual = small_file_hash(destination, destination.stat().st_size)
            if actual != op["sha256"]:
                failures.append(f"Checksum mismatch: {destination}")
                continue
        if op["action"] == "Move" and Path(op["source"]).exists():
            failures.append(f"Move source still exists: {op['source']}")
            continue
        verified_bytes += op["size"]

    if failures:
        for failure in failures[:20]:
            print(f"[ERROR] {failure}", file=sys.stderr)
        if len(failures) > 20:
            print(f"[ERROR] ... {len(failures) - 20} more", file=sys.stderr)
        print(f"[ERROR] Storage verification failed for {len(failures)} operation(s).", file=sys.stderr)
        return 1

    print(
        f"Verified {len(manifest['operations'])} files ({human(verified_bytes)}) "
        f"against {manifest_path.name}."
    )
    return 0


def summarize(plan: Plan, mode: str) -> None:
    total = sum(op["size"] for op in plan.operations)
    conflicts = sum(1 for op in plan.operations if op["conflict"])
    print(f"Mode: {mode}")
    print(f"Workspaces: {len(plan.workspaces)}")
    print(f"Files: {len(plan.operations)}")
    print(f"Bytes: {total} ({human(total)})")
    print(f"Conflicts routed to _migrated-legacy: {conflicts}")


# ---------------------------------------------------------------- entrypoint


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="print the plan without touching files (default)")
    group.add_argument("--execute", action="store_true", help="perform the migration")
    group.add_argument("--verify", action="store_true", help="verify a previous migration against its manifest")
    group.add_argument("--auto", action="store_true", help="no-op unless legacy data exists; used by the launchers")
    parser.add_argument("--manifest-path", type=Path, default=None, help="manifest to verify (defaults to newest)")
    args = parser.parse_args(argv)

    if args.verify:
        return verify(args.manifest_path)

    if args.auto:
        # Fast probe: a handful of stat calls on an already-migrated machine.
        if not any(has_payload(root) for root in LEGACY_PROBES):
            return 0
        # ASCII only: Windows consoles default to cp1252 and would mangle or
        # raise UnicodeEncodeError on box-drawing/dash characters.
        print("[storage] Legacy runtime data detected - migrating into Backend/storage ...")

    check_map_drift()
    plan = build_plan()

    if args.auto and not any(op["action"] == "Move" for op in plan.operations):
        return 0

    if not plan.operations:
        print("Nothing to migrate.")
        return 0

    if not args.execute and not args.auto:
        summarize(plan, "DryRun")
        for op in plan.operations[:30]:
            flag = " [conflict]" if op["conflict"] else ""
            print(f"  {op['action']:<4} {op['relative_path']} -> {op['destination']}{flag}")
        if len(plan.operations) > 30:
            print(f"  ... {len(plan.operations) - 30} additional operations")
        return 0

    summarize(plan, "Execute")
    manifest_path, rollback_path = execute(plan)
    print(f"Migration complete. Manifest: {manifest_path}")
    print(f"Rollback script: python {rollback_path}")

    result = verify(manifest_path)
    if result == 0 and args.auto:
        print("[storage] Migration verified. Continuing startup.")
    return result


if __name__ == "__main__":
    sys.exit(main())
