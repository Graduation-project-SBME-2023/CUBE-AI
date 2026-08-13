from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


class WorkflowStoreError(RuntimeError):
    """Raised when persisted dynamic-model state is invalid or unreadable."""


def _parse_labels(value: object) -> dict[str, int]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("labels must be a JSON object") from exc
    if not isinstance(value, Mapping) or not value:
        raise ValueError("labels must be a non-empty object")

    parsed: dict[str, int] = {}
    for label, index in value.items():
        name = str(label).strip()
        if not name:
            raise ValueError("label names cannot be empty")
        if isinstance(index, bool):
            raise ValueError("label indexes must be integers")
        try:
            parsed_index = int(index)
        except (TypeError, ValueError) as exc:
            raise ValueError("label indexes must be integers") from exc
        if isinstance(index, float) and index != parsed_index:
            raise ValueError("label indexes must be integers")
        if parsed_index < 0:
            raise ValueError("label indexes cannot be negative")
        parsed[name] = parsed_index

    if len(set(parsed.values())) != len(parsed):
        raise ValueError("label indexes must be unique")
    return parsed


@dataclass(frozen=True)
class DynamicModelSpec:
    model_name: str
    labels: dict[str, int]
    fold: int
    plan_type: str

    @classmethod
    def create(
        cls,
        *,
        model_name: object,
        labels: object,
        fold: object,
        plan_type: object,
    ) -> "DynamicModelSpec":
        name = str(model_name).strip()
        if not name or name in {".", ".."} or any(char in name for char in "/\\"):
            raise ValueError("model_name must be a safe, non-empty identifier")

        try:
            parsed_fold = int(fold)
        except (TypeError, ValueError) as exc:
            raise ValueError("fold must be an integer") from exc
        if parsed_fold < 0:
            raise ValueError("fold cannot be negative")

        parsed_plan = "" if plan_type is None else str(plan_type).strip()
        if not parsed_plan:
            raise ValueError("plan_type cannot be empty")

        return cls(
            model_name=name,
            labels=_parse_labels(labels),
            fold=parsed_fold,
            plan_type=parsed_plan,
        )

    @classmethod
    def from_record(cls, key: str, record: object) -> "DynamicModelSpec":
        if not isinstance(record, Mapping):
            raise ValueError(f"workflow {key!r} must be an object")
        return cls.create(
            model_name=record.get("model_name", key),
            labels=record.get("labels"),
            fold=record.get("fold", 0),
            plan_type=record.get("plan_type"),
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "labels": self.labels,
            "fold": self.fold,
            "plan_type": self.plan_type,
        }


class WorkflowRepository:
    """Atomic JSON persistence for dynamically-created radiology models."""

    def __init__(self, path: Path, *, legacy_paths: Iterable[Path] = ()):
        self.path = Path(path)
        self.legacy_paths = tuple(Path(item) for item in legacy_paths)
        self._lock = threading.RLock()

    def load(self) -> list[DynamicModelSpec]:
        with self._lock:
            with self._process_lock():
                source = self._source_path()
                if source is None:
                    return []
                records = self._read_records(source)
                specs = []
                for key, record in records.items():
                    try:
                        specs.append(DynamicModelSpec.from_record(str(key), record))
                    except ValueError as exc:
                        raise WorkflowStoreError(
                            f"Invalid workflow definition: {key}"
                        ) from exc
                if source != self.path:
                    self._write_records(
                        {spec.model_name: spec.to_record() for spec in specs}
                    )
                return specs

    def upsert(self, spec: DynamicModelSpec) -> None:
        with self._lock:
            with self._process_lock():
                source = self._source_path()
                records = self._read_records(source) if source else {}
                records[spec.model_name] = spec.to_record()
                self._write_records(records)

    @contextmanager
    def _process_lock(self):
        """Prevent lost updates when more than one server process is active."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(f"{self.path.suffix}.lock")
        with lock_path.open("a+b") as lock_stream:
            if os.name == "nt":
                import msvcrt

                lock_stream.seek(0, os.SEEK_END)
                if lock_stream.tell() == 0:
                    lock_stream.write(b"\0")
                    lock_stream.flush()
                lock_stream.seek(0)
                msvcrt.locking(lock_stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    lock_stream.seek(0)
                    msvcrt.locking(lock_stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)

    def _source_path(self) -> Path | None:
        if self.path.is_file():
            return self.path
        return next((path for path in self.legacy_paths if path.is_file()), None)

    @staticmethod
    def _read_records(path: Path) -> dict[str, Any]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkflowStoreError(f"Could not read workflow state: {path}") from exc
        if not isinstance(raw, dict):
            raise WorkflowStoreError(f"Workflow state must be a JSON object: {path}")
        return raw

    def _write_records(self, records: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temp_path = stream.name
                json.dump(records, stream, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self.path)
        except OSError as exc:
            raise WorkflowStoreError(
                f"Could not write workflow state: {self.path}"
            ) from exc
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
