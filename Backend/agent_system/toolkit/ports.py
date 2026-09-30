"""PortSpec: one typed input or output of a graph node (merged plan, Phase 1.1 step 3).

Tools and execution agents both declare ports, so this module sits at the top
of the toolkit rather than under ``tools/`` or ``agents/``:

- an **input** port is filled from an upstream artifact. On a tool, ``param``
  names the handler argument that receives the artifact ref (the Node Runner
  maps ``port.param`` -> args, Plan 10). Agents get their inputs rendered into
  a "Node inputs" section instead, so their input ports leave ``param`` unset.
- an **output** port is produced by the node. ``extract(result)`` pulls the
  artifact ref and its summary out of the node's result; the Node Runner then
  records them in ``graph_artifacts.ref`` / ``.summary`` (both JSONB, Plan 6).

An artifact is a pointer, never a payload: ``ref`` points at a DB row or a
``storage_paths`` file, and ``summary`` holds the few numbers that expectations
and the Main Agent's context read. ``artifact_type`` is a type *name*; the
catalog checks it against the artifact type registry (Phase 1.2 / 1.3).
"""

from __future__ import annotations

import keyword
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import KW_ONLY, dataclass, field
from types import MappingProxyType
from typing import Any

# Artifact type names are identifiers, e.g. "Cohort", "MaskSet", "FeatureTable".
ARTIFACT_TYPE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class ExtractedArtifact:
    """What an output port's ``extract`` returns for one artifact."""

    ref: Mapping[str, Any]
    summary: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.ref, Mapping) or not self.ref:
            raise ValueError(f"an artifact ref must be a non-empty mapping, not {self.ref!r}")
        if not isinstance(self.summary, Mapping):
            raise ValueError(f"an artifact summary must be a mapping, not {self.summary!r}")
        object.__setattr__(self, "ref", MappingProxyType(dict(self.ref)))
        object.__setattr__(self, "summary", MappingProxyType(dict(self.summary)))


# One artifact, several (``many=True``), or None when an optional output was not produced.
type PortExtract = Callable[
    [Any], ExtractedArtifact | Sequence[ExtractedArtifact] | None
]


def freeze_ports(
    ports: Mapping[str, PortSpec], field_name: str, *, forbidden: Sequence[str]
) -> Mapping[str, PortSpec]:
    """Check a spec's ``inputs`` or ``outputs`` and return a read-only copy.

    ``forbidden`` names the PortSpec attributes that make no sense in this
    direction, e.g. ``extract`` on a tool input or ``param`` on any output.
    """
    for name, port in ports.items():
        if not isinstance(name, str) or not name:
            raise ValueError(f"{field_name} port names must be non-empty strings: {name!r}")
        if not isinstance(port, PortSpec):
            raise TypeError(f"{field_name} port {name!r} must be a PortSpec, not {port!r}")
        for attr in forbidden:
            if getattr(port, attr) is not None:
                raise ValueError(f"{field_name} port {name!r} must not set {attr}")
    return MappingProxyType(dict(ports))


class PortExtractError(ValueError):
    """An output port's ``extract`` broke the port's contract."""


@dataclass(frozen=True)
class PortSpec:
    """One port. ``artifact_type`` is positional; everything else is keyword-only:
    ``PortSpec("Cohort", param="cohort_id")``, ``PortSpec("MaskSet", extract=...)``.
    """

    artifact_type: str
    _: KW_ONLY
    param: str | None = None  # input ports on tools: the handler argument
    many: bool = False  # a list of artifacts rather than exactly one
    required: bool = True  # False: the input may stay unbound / the output may be absent
    extract: PortExtract | None = None  # output ports: result -> artifact(s)

    def __post_init__(self) -> None:
        if not isinstance(self.artifact_type, str) or not ARTIFACT_TYPE_PATTERN.match(
            self.artifact_type
        ):
            raise ValueError(
                f"artifact type {self.artifact_type!r} must match {ARTIFACT_TYPE_PATTERN.pattern}"
            )
        if self.param is not None and (
            not isinstance(self.param, str)
            or not self.param.isidentifier()
            or keyword.iskeyword(self.param)
        ):
            raise ValueError(f"port param {self.param!r} must be a Python argument name")
        for flag in ("many", "required"):
            if not isinstance(getattr(self, flag), bool):
                raise TypeError(f"port {flag} must be a bool")
        if self.extract is not None and not callable(self.extract):
            raise TypeError("port extract must be callable")
        if self.param is not None and self.extract is not None:
            # param is for inputs, extract is for outputs: no port is both.
            raise ValueError("a port sets param (input) or extract (output), not both")

    def extract_artifacts(self, result: Any) -> tuple[ExtractedArtifact, ...]:
        """Run ``extract`` on a node result and enforce ``many`` / ``required``.

        Returns zero artifacts only for an optional port, exactly one unless
        ``many=True``, and at least one for a required ``many`` port.
        """
        if self.extract is None:
            raise PortExtractError(f"{self.artifact_type} port has no extract function")

        produced = self.extract(result)
        if produced is None:
            artifacts: tuple[Any, ...] = ()
        elif isinstance(produced, ExtractedArtifact):
            artifacts = (produced,)
        elif isinstance(produced, Sequence) and not isinstance(produced, (str, bytes)):
            artifacts = tuple(produced)
        else:
            raise PortExtractError(
                f"{self.artifact_type} extract returned {type(produced).__name__}, "
                f"not ExtractedArtifact"
            )

        for artifact in artifacts:
            if not isinstance(artifact, ExtractedArtifact):
                raise PortExtractError(
                    f"{self.artifact_type} extract returned {type(artifact).__name__}, "
                    f"not ExtractedArtifact"
                )
        if not artifacts and self.required:
            raise PortExtractError(f"required {self.artifact_type} output was not produced")
        if len(artifacts) > 1 and not self.many:
            raise PortExtractError(
                f"{self.artifact_type} port takes one artifact, extract returned {len(artifacts)}"
            )
        return artifacts
