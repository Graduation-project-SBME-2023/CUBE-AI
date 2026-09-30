"""Artifact types and their registry (merged plan, Phase 1.2 step 1).

An artifact is what a graph node produces on an output port and what a
downstream node receives on an input port. It is a **pointer**, never a
payload: its ``ref`` names a DB row (or a ``storage_paths`` file), and its
``summary`` holds the few values that expectations and the Main Agent's
context read. Both are stored as JSONB in ``graph_artifacts`` (Plan 6).

An ``ArtifactType`` says what a valid ref and summary look like. Ports name
types by their name; the catalog refuses a port whose type is not registered,
and the Validator (Plan 8) refuses a binding whose types are not compatible.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from ..ports import ARTIFACT_TYPE_PATTERN, ExtractedArtifact
from ..registry import Registry, UnknownNameError

# Summaries go into the Main Agent's context; refs are pointers. Both stay small.
MAX_REF_BYTES = 64 * 1024
MAX_SUMMARY_BYTES = 4 * 1024


class ArtifactRef(BaseModel):
    """Base class of every ref model: the fields that point at the data."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ArtifactSummary(BaseModel):
    """Base class of every summary model: small values about the data."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ArtifactValidationError(ValueError):
    """An extracted artifact does not match its type."""


class UnknownArtifactTypeError(UnknownNameError):
    kind = "artifact type"


@dataclass(frozen=True, eq=False, kw_only=True)
class ArtifactType:
    """One artifact type. Compared by identity: a registry holds one per name."""

    name: str
    version: int = 1
    description: str  # one line, shown to the Composer next to the ports
    ref_model: type[ArtifactRef]
    summary_model: type[ArtifactSummary]
    phi_default: bool  # whether the data behind the pointer is patient data
    parents: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not ARTIFACT_TYPE_PATTERN.match(self.name):
            raise ValueError(
                f"artifact type name {self.name!r} must match {ARTIFACT_TYPE_PATTERN.pattern}"
            )
        what = f"artifact type {self.name!r}"
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError(f"{what}: version must be an int >= 1")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError(f"{what}: description must not be empty")
        if not (isinstance(self.ref_model, type) and issubclass(self.ref_model, ArtifactRef)):
            raise TypeError(f"{what}: ref_model must subclass ArtifactRef")
        if not (
            isinstance(self.summary_model, type) and issubclass(self.summary_model, ArtifactSummary)
        ):
            raise TypeError(f"{what}: summary_model must subclass ArtifactSummary")
        ref_fields = self.ref_model.model_fields
        if not ref_fields:
            raise ValueError(f"{what}: ref_model must have at least one field (it is a pointer)")
        optional = sorted(name for name, info in ref_fields.items() if not info.is_required())
        if optional:
            raise ValueError(f"{what}: ref fields must all be required, not {optional}")
        if not isinstance(self.phi_default, bool):
            raise TypeError(f"{what}: phi_default must be a bool")

        if isinstance(self.parents, (str, bytes)):
            raise TypeError(f"{what}: parents must be a collection of names, not a string")
        parents = frozenset(self.parents)
        for parent in parents:
            if not isinstance(parent, str) or not ARTIFACT_TYPE_PATTERN.match(parent):
                raise ValueError(f"{what}: parent {parent!r} is not a valid type name")
        if self.name in parents:
            raise ValueError(f"{what}: a type cannot be its own parent")
        object.__setattr__(self, "parents", parents)

    def validate_artifact(self, artifact: ExtractedArtifact) -> ExtractedArtifact:
        """Check an extracted artifact against this type and return it normalised
        to JSON (e.g. UUIDs as strings), ready for ``graph_artifacts``."""
        try:
            ref = self.ref_model.model_validate(dict(artifact.ref)).model_dump(mode="json")
            summary = self.summary_model.model_validate(dict(artifact.summary)).model_dump(
                mode="json"
            )
        except ValidationError as exc:
            raise ArtifactValidationError(f"invalid {self.name} artifact: {exc}") from exc
        limits = (("ref", ref, MAX_REF_BYTES), ("summary", summary, MAX_SUMMARY_BYTES))
        for part, value, limit in limits:
            size = len(json.dumps(value, separators=(",", ":")).encode())
            if size > limit:
                raise ArtifactValidationError(
                    f"{self.name} {part} is {size} bytes (limit {limit}); "
                    f"an artifact is a pointer, not a payload"
                )
        return ExtractedArtifact(ref=ref, summary=summary)

    def describe(self) -> dict[str, Any]:
        """The type as the Composer sees it: name, description and JSON schemas."""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "parents": sorted(self.parents),
            "phi": self.phi_default,
            "ref": self.ref_model.model_json_schema(),
            "summary": self.summary_model.model_json_schema(),
        }

    def __repr__(self) -> str:
        return f"ArtifactType(name={self.name!r}, version={self.version})"


class ArtifactTypeRegistry(Registry[ArtifactType]):
    """The artifact types known to one catalog. Built-in types come from
    ``builtin.py``; packs may register more. Parents may be registered after
    their children; ``problems()`` reports any that never were."""

    item_type = ArtifactType
    unknown_error = UnknownArtifactTypeError

    def ancestors(self, name: str) -> frozenset[str]:
        """Every type ``name`` can stand in for, excluding itself (parents,
        their parents, ...). Unknown parents are included; ``problems()`` flags them."""
        seen: set[str] = set()
        pending = list(self.get(name).parents)
        while pending:
            parent = pending.pop()
            if parent in seen or parent == name:
                continue
            seen.add(parent)
            if parent in self._items:
                pending.extend(self._items[parent].parents)
        return frozenset(seen)

    def is_compatible(self, produced: str, expected: str) -> bool:
        """Can an artifact of type ``produced`` fill a port expecting ``expected``?
        Yes when the names are the same or ``expected`` is an ancestor of ``produced``."""
        return produced == expected or expected in self.ancestors(produced)

    def problems(self) -> list[str]:
        """Unknown parents and inheritance cycles; ``catalog.validate()`` raises on them."""
        found: list[str] = []
        for artifact_type in self:
            for parent in sorted(artifact_type.parents - self.names()):
                found.append(f"artifact type {artifact_type.name!r} names unknown parent {parent!r}")
        for artifact_type in self:
            if self._reaches_itself(artifact_type.name):
                found.append(f"artifact type {artifact_type.name!r} is in an inheritance cycle")
        return found

    def _reaches_itself(self, name: str) -> bool:
        seen: set[str] = set()
        pending = list(self._items[name].parents)
        while pending:
            parent = pending.pop()
            if parent == name:
                return True
            if parent in seen or parent not in self._items:
                continue
            seen.add(parent)
            pending.extend(self._items[parent].parents)
        return False
