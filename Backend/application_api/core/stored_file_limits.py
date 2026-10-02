"""Stored-file admission limits"""

from __future__ import annotations

import os

MIB = 1024 * 1024

# The column-level bound (the migration's named CHECK uses the same number).
STORED_FILE_MAX_BYTES = 64 * MIB
STORED_FILE_METADATA_MAX_BYTES = 1 * MIB

IMAGE_MAX_BYTES = 32 * MIB
CSV_MAX_BYTES = 64 * MIB

STORED_FILE_KINDS = (
    "figure_png",
    "figure_jpeg",
    "figure_gif",
    "export_csv",
    "input_csv",
)
RETENTION_CLASSES = ("retained", "render")

# Which mime types each kind admits. A `figure_png` carrying a CSV is a bug in
# the writer, and the cheapest place to catch it is admission.
KIND_MIME_TYPES = {
    "figure_png": ("image/png",),
    "figure_jpeg": ("image/jpeg", "image/jpg"),
    "figure_gif": ("image/gif",),
    "export_csv": ("text/csv", "text/plain"),
    "input_csv": ("text/csv", "text/plain"),
}

# Image types a report may reference.
REPORT_IMAGE_MIME_TYPES = ("image/png", "image/jpeg", "image/jpg")


def max_bytes_for(kind: str) -> int:
    return CSV_MAX_BYTES if kind.endswith("_csv") else IMAGE_MAX_BYTES


# How many binary reads may be in flight per API process, and how long a caller
# waits before it is told to come back. This is a per-process memory ceiling:
# a bounded 503 + Retry-After is safer than an unbounded queue of BYTEA values.
BINARY_READ_CONCURRENCY = int(os.environ.get("ARTIFACT_READ_CONCURRENCY", "2"))
BINARY_READ_QUEUE_TIMEOUT_SECONDS = float(
    os.environ.get("ARTIFACT_READ_QUEUE_TIMEOUT_SECONDS", "5")
)
BINARY_READ_RETRY_AFTER_SECONDS = int(
    os.environ.get("ARTIFACT_READ_RETRY_AFTER_SECONDS", "5")
)

# The dedicated read pool: small, no overflow, and a statement timeout long
# enough for a 64 MiB detoast but far shorter than "forever". The interactive
# engine keeps its own 30 s timeout.
BINARY_POOL_SIZE = int(os.environ.get("ARTIFACT_READ_POOL_SIZE", "3"))
BINARY_POOL_MAX_OVERFLOW = 0
BINARY_STATEMENT_TIMEOUT_MS = int(
    os.environ.get("ARTIFACT_READ_STATEMENT_TIMEOUT_MS", "120000")
)
