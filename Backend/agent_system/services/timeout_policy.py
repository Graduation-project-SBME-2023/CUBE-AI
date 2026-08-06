import os

import httpx


def _positive_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


# Session state, logs, and database metadata must fail quickly. They are never
# allowed to inherit an inference-sized timeout.
CONTROL_CONNECT_SECONDS = _positive_float("CONTROL_CONNECT_SECONDS", 3.0)
CONTROL_READ_SECONDS = _positive_float("CONTROL_READ_SECONDS", 8.0)
CONTROL_WRITE_SECONDS = _positive_float("CONTROL_WRITE_SECONDS", 8.0)
CONTROL_POOL_SECONDS = _positive_float("CONTROL_POOL_SECONDS", 3.0)
CONTROL_HTTP_TIMEOUT = httpx.Timeout(
    connect=CONTROL_CONNECT_SECONDS,
    read=CONTROL_READ_SECONDS,
    write=CONTROL_WRITE_SECONDS,
    pool=CONTROL_POOL_SECONDS,
)
BEST_EFFORT_TIMEOUT_SECONDS = _positive_float(
    "BEST_EFFORT_TIMEOUT_SECONDS", 10.0
)
WS_SEND_TIMEOUT_SECONDS = _positive_float("WS_SEND_TIMEOUT_SECONDS", 3.0)

# Long tools have an explicit wall-clock budget in addition to their transport
# timeout. The orchestration heartbeat remains responsive inside this window.
SEGMENTATION_JOB_TIMEOUT_SECONDS = _positive_float(
    "SEGMENTATION_JOB_TIMEOUT_SECONDS", 1800.0
)
SEGMENTATION_POLL_SECONDS = _positive_float("SEGMENTATION_POLL_SECONDS", 3.0)
SEGMENTATION_UNAVAILABLE_GRACE_SECONDS = _positive_float(
    "SEGMENTATION_UNAVAILABLE_GRACE_SECONDS", 400.0
)
SEGMENTATION_RESULT_READ_SECONDS = _positive_float(
    "SEGMENTATION_RESULT_READ_SECONDS", 180.0
)
LONG_TOOL_TIMEOUT_SECONDS = _positive_float("LONG_TOOL_TIMEOUT_SECONDS", 2100.0)
TOOL_HEARTBEAT_SECONDS = _positive_float("TOOL_HEARTBEAT_SECONDS", 30.0)
CANCELLATION_GRACE_SECONDS = _positive_float("CANCELLATION_GRACE_SECONDS", 10.0)

# MCP transport limits are tiered by expected work rather than copied literals.
MCP_STANDARD_READ_SECONDS = _positive_float("MCP_STANDARD_READ_SECONDS", 2000.0)
MCP_COMPUTE_READ_SECONDS = _positive_float("MCP_COMPUTE_READ_SECONDS", 3600.0)
