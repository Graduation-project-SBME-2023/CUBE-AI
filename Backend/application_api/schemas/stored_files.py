"""Stored-file schemas"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from application_api.core.stored_file_limits import (
    RETENTION_CLASSES,
    STORED_FILE_KINDS,
    STORED_FILE_METADATA_MAX_BYTES,
)
from application_api.core.json_bounds import JsonBoundsError, check_json

StoredFileKind = Literal["figure_png", "figure_jpeg", "figure_gif", "export_csv", "input_csv"]
RetentionClass = Literal["retained", "render"]


class StoredFileOut(BaseModel):
    """A stored file's catalogue row. The bytes are fetched separately."""

    file_id: UUID
    session_id: UUID
    created_by_run_id: Optional[UUID] = None
    kind: str
    logical_key: str
    display_filename: str
    mime_type: str
    byte_size: int
    sha256: str
    render_path: Optional[str] = None
    metadata: Optional[dict[str, Any]] = Field(default=None, alias="metadata_")
    schema_version: int
    retention_class: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class RenderRegistration(BaseModel):
    """Catalogue a regenerable render that already exists on disk."""

    session_id: UUID
    logical_key: str = Field(min_length=1, max_length=512)
    render_path: str = Field(min_length=1, max_length=1024)
    kind: StoredFileKind = "figure_gif"
    display_filename: Optional[str] = None
    mime_type: str = "image/gif"
    created_by_run_id: Optional[UUID] = None
    metadata: Optional[dict[str, Any]] = None

    @field_validator("render_path")
    @classmethod
    def _relative(cls, value: str) -> str:
        normalized = value.replace("\\", "/").strip()
        if normalized.startswith("/") or ":" in normalized.split("/")[0]:
            raise ValueError("render_path must be relative, never a host absolute path")
        if any(part == ".." for part in normalized.split("/")):
            raise ValueError("render_path must not traverse out of the workspace")
        return normalized

    @field_validator("metadata")
    @classmethod
    def _bounded(cls, value):
        if value is None:
            return value
        try:
            return check_json(
                value, max_bytes=STORED_FILE_METADATA_MAX_BYTES, what="stored file metadata"
            )
        except JsonBoundsError as exc:
            raise ValueError(str(exc)) from exc
