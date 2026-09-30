"""The manifest vocabulary: the enums and constants a tool manifest is written in.

Kept apart from ``ToolSpec`` because other parts of the toolkit speak the same
vocabulary without needing the class: ``AgentSpec`` declares ``resources``, the
sandbox policy (Plan 4) maps ``TrustLevel`` to ``IsolationLevel``, the job
platform (Plan 5) routes by resource, and the Guardrails (Plan 11) read
``DataScope`` and ``Visibility``.

No AutoGen, LangGraph, or MCP imports: the toolkit is framework-neutral.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum


class Execution(StrEnum):
    """Where the handler runs."""

    INLINE = "inline"  # awaited on the agent-system event loop
    JOB = "job"  # Celery job on a compute worker (Plan 5)
    EXTERNAL = "external"  # outbound MCP edge (Plan 14)


class SideEffects(StrEnum):
    NONE = "none"
    WRITES = "writes"
    COMPUTE = "compute"


class Cost(StrEnum):
    LIGHT = "light"
    MEDIUM = "medium"
    HEAVY = "heavy"


class Resource(StrEnum):
    """Named capacity pools a call occupies. Executor pool sizes come from
    ``config/resources.yaml`` (Plan 10)."""

    LLM = "llm"
    CPU_HEAVY = "cpu_heavy"
    GPU = "gpu"
    HIGHMEM = "highmem"
    MONAI_INFERENCE = "monai_inference"
    ORTHANC_IO = "orthanc_io"


class TrustLevel(StrEnum):
    FIRST_PARTY = "first_party"
    THIRD_PARTY = "third_party"
    GENERATED_CODE = "generated_code"


class IsolationLevel(StrEnum):
    """Ordered weakest to strongest. The minimum per trust level is the
    sandbox policy's job (Plan 4), not this module's."""

    INLINE = "inline"
    PROCESS = "process"
    CONTAINER = "container"
    MICROVM = "microvm"


class DataScope(StrEnum):
    PHI_READ = "phi_read"
    PHI_WRITE = "phi_write"
    DB_WRITE = "db_write"
    NETWORK_EGRESS = "network_egress"
    FS_WRITE = "fs_write"


class Visibility(StrEnum):
    """Who may reach the tool. A tool declares a set of these."""

    MAIN = "main"  # the Main Agent may call it directly
    GRAPH = "graph"  # it may be submitted as a Tool Node
    AGENT = "agent"  # it may appear in an agent's allowlist
    HARNESS = "harness"  # harness code only (e.g. the Node Runner's bookkeeping)


PHI_SCOPES: frozenset[DataScope] = frozenset({DataScope.PHI_READ, DataScope.PHI_WRITE})
WRITE_SCOPES: frozenset[DataScope] = frozenset(
    {DataScope.PHI_WRITE, DataScope.DB_WRITE, DataScope.FS_WRITE}
)

# `resources` is the manifest field; the Celery queue is derived from it.
# Checked in order, so a tool needing both a GPU and a heavy CPU goes to the GPU queue.
RESOURCE_QUEUES: tuple[tuple[Resource, str], ...] = (
    (Resource.GPU, "compute.gpu"),
    (Resource.HIGHMEM, "compute.highmem"),
    (Resource.CPU_HEAVY, "compute.cpu"),
)

# Celery's Redis transport supports priorities 0-9.
MIN_PRIORITY = 0
MAX_PRIORITY = 9


def coerce_enum[E: StrEnum](enum_cls: type[E], value: object, field_name: str) -> E:
    """Parse one manifest value, accepting either the enum member or its string."""
    try:
        return enum_cls(value)
    except ValueError:
        allowed = ", ".join(member.value for member in enum_cls)
        raise ValueError(f"{field_name}={value!r} is not one of: {allowed}") from None


def coerce_enum_set[E: StrEnum](
    enum_cls: type[E], values: Iterable[object], field_name: str
) -> frozenset[E]:
    """Parse a set-valued manifest field (``resources``, ``data_scopes``, ``visibility``)."""
    if isinstance(values, (str, bytes)):
        # A bare string would otherwise be split into characters.
        raise TypeError(f"{field_name} must be a collection, not a string: {values!r}")
    return frozenset(coerce_enum(enum_cls, value, field_name) for value in values)
