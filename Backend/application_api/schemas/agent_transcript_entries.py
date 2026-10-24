"""Transcript schemas: an agent node's Claude Agent SDK transcript (keyed by its
run id, which is its `sdk_session_id`) and the Main's (keyed by the CUBE.AI
`session_id`).

Entries are the SDK's own JSON objects, stored and returned unchanged; the
Application API reads only their `uuid`, which makes a re-delivered batch a
no-op."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

ENTRY_MAX_BYTES = 4 * 1024 * 1024
BATCH_MAX_ENTRIES = 1000


class TranscriptAppend(BaseModel):
    """POST .../transcript: one batch of `SessionStore.append`."""

    model_config = ConfigDict(extra="forbid")

    # '' is the main transcript; the SDK's opaque subpath for a subagent.
    subpath: str = Field(default="", max_length=512)
    # The SDK's key for the working directory; checked, not stored.
    project_key: str = Field(min_length=1, max_length=512)
    entries: list[dict[str, Any]] = Field(min_length=1, max_length=BATCH_MAX_ENTRIES)

    @field_validator("entries")
    @classmethod
    def _entries_carry_their_uuid(cls, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for entry in entries:
            uuid = entry.get("uuid")
            if not isinstance(uuid, str) or not uuid.strip() or len(uuid) > 256:
                raise ValueError("every transcript entry carries its uuid")
        return entries


class TranscriptAppendOut(BaseModel):
    stored: int
    replayed: int


class TranscriptOut(BaseModel):
    subpath: str
    entries: list[dict[str, Any]]


class TranscriptPurgeOut(BaseModel):
    """One purge batch: agent-node runs and archived sessions whose transcripts went."""

    runs: int
    entries: int
    main_sessions: int = 0
