"""Agent-checkpoint schemas. Blobs travel base64-encoded; the
Application API never reads them."""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import Base64Bytes, BaseModel, ConfigDict, Field, field_serializer, field_validator

CHECKPOINT_MAX_BYTES = 32 * 1024 * 1024


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CheckpointPut(_Request):
    checkpoint_ns: str = Field(default="", max_length=512)
    checkpoint_id: str = Field(min_length=1, max_length=256)
    parent_checkpoint_id: Optional[str] = Field(default=None, max_length=256)
    type: str = Field(min_length=1, max_length=64)
    checkpoint: Base64Bytes
    metadata: dict[str, Any]

    @field_validator("checkpoint")
    @classmethod
    def _bounded(cls, value: bytes) -> bytes:
        if len(value) > CHECKPOINT_MAX_BYTES:
            raise ValueError(f"a checkpoint is at most {CHECKPOINT_MAX_BYTES} bytes")
        return value

    @field_validator("metadata")
    @classmethod
    def _versioned(cls, value: dict[str, Any]) -> dict[str, Any]:
        missing = {"v", "spec_digest", "engine_version"} - value.keys()
        if missing:
            raise ValueError(f"checkpoint metadata needs {sorted(missing)}")
        return value


class CheckpointWriteIn(_Request):
    task_id: str = Field(min_length=1, max_length=256)
    idx: int
    channel: str = Field(min_length=1, max_length=256)
    type: Optional[str] = Field(default=None, max_length=64)
    value: Base64Bytes


class CheckpointWritesPut(_Request):
    checkpoint_ns: str = Field(default="", max_length=512)
    checkpoint_id: str = Field(min_length=1, max_length=256)
    writes: list[CheckpointWriteIn] = Field(min_length=1, max_length=1000)


class CheckpointWriteOut(BaseModel):
    task_id: str
    idx: int
    channel: str
    type: Optional[str] = None
    value: bytes

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("value")
    def _b64(self, value: bytes) -> str:
        return base64.b64encode(value).decode("ascii")


class CheckpointOut(BaseModel):
    run_id: UUID
    checkpoint_ns: str
    checkpoint_id: str
    parent_checkpoint_id: Optional[str] = None
    attempt_id: UUID
    type: str
    checkpoint: bytes
    metadata: dict[str, Any] = Field(validation_alias="metadata_")
    created_at: datetime
    writes: list[CheckpointWriteOut] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @field_serializer("checkpoint")
    def _b64(self, value: bytes) -> str:
        return base64.b64encode(value).decode("ascii")


class CheckpointSummary(BaseModel):
    """A list row: everything but the blob."""

    run_id: UUID
    checkpoint_ns: str
    checkpoint_id: str
    parent_checkpoint_id: Optional[str] = None
    attempt_id: UUID
    metadata: dict[str, Any] = Field(validation_alias="metadata_")
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class RunStep(BaseModel):
    """One step of a run's timeline, from checkpoint metadata (no blobs)."""

    checkpoint_ns: str
    checkpoint_id: str
    attempt_id: UUID
    step: Optional[int] = None
    node: Optional[str] = None
    tool_calls: list[Any] = Field(default_factory=list)
    pending_tools: list[Any] = Field(default_factory=list)
    created_at: datetime


class PurgeOut(BaseModel):
    runs: int
    checkpoints: int
