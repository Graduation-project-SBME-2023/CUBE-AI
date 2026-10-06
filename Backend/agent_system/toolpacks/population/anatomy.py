"""Anatomy rules of the population step: which body region a hypothesis names,
and whether a candidate volume lies in it. Pure functions, no I/O."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

# High-level regions and the words that name them in a DICOM
# series_description or body_part_examined.
ANATOMY_GROUPS: dict[str, list[str]] = {
    "chest": [
        "chest", "thorax", "lung", "pulmonary", "thoracic", "mediastin",
        "bronch", "pleura", "trachea",
    ],
    "abdomen": [
        "abdomen", "abdominal", "abd", "kidney", "renal", "liver",
        "hepatic", "spleen", "splenic", "pancrea", "bowel", "intestin",
        "colon", "pelvis", "pelvic", "gallbladder", "biliary", "adrenal",
        "retroperitoneal", "mesentery",
    ],
    "head": [
        "head", "brain", "cranial", "neuro", "cerebr", "skull",
        "intracranial", "orbit", "sella", "temporal",
    ],
    "neck": [
        "neck", "cervical", "thyroid", "laryn", "pharyn", "parotid",
        "submandibular",
    ],
    "spine": [
        "spine", "spinal", "vertebr", "lumbar", "sacral", "thoracolumbar",
        "disc", "cord",
    ],
    "extremity": [
        "extremity", "arm", "leg", "femur", "tibia", "humerus", "knee",
        "ankle", "wrist", "hand", "foot", "shoulder", "hip", "elbow",
        "musculoskeletal",
    ],
    "cardiac": [
        "cardiac", "heart", "coronary", "aort", "myocard", "pericardi",
    ],
    "breast": [
        "breast", "mammog", "mammo", "axilla",
    ],
}

# A region shorter than this names nothing: "" is a substring of every keyword
# and "a" of most of them, so a short text would match the first group (L5).
MIN_REGION_LENGTH = 3


def resolve_anatomy_group(text: str) -> tuple[str | None, list[str]]:
    """The anatomy group a free-text body region names, and its keywords."""
    text_lower = text.lower().strip()
    if len(text_lower) < MIN_REGION_LENGTH:
        return None, []
    for group, keywords in ANATOMY_GROUPS.items():
        if group in text_lower:
            return group, keywords
        # A keyword in the text, or the text as a keyword prefix.
        if any(keyword in text_lower or text_lower in keyword for keyword in keywords):
            return group, keywords
    return None, []


def _volume_text(volume: dict[str, Any]) -> str:
    description = (volume.get("series_description") or "").lower()
    body_part = (volume.get("body_part_examined") or "").lower()
    return f"{description} {body_part}"


def _conflicting_group(text: str, group: str) -> str | None:
    """The first other group the text clearly names, if any."""
    for other, keywords in ANATOMY_GROUPS.items():
        if other != group and any(keyword in text for keyword in keywords):
            return other
    return None


@dataclass
class Classification:
    validated: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    conflicting_groups: set[str] = field(default_factory=set)  # the regions the rejected volumes lie in

    @property
    def validated_uids(self) -> list[str]:
        return [volume["uid"] for volume in self.validated]


def classify(volumes: Iterable[dict[str, Any]], group: str, keywords: list[str]) -> Classification:
    """Keep the volumes in ``group``; reject only those that clearly lie in
    another region. A volume with no anatomy signal is kept (ambiguous, not wrong)."""
    result = Classification()
    for volume in volumes:
        text = _volume_text(volume)
        if any(keyword in text for keyword in keywords):
            result.validated.append(volume)
            continue
        conflict = _conflicting_group(text, group)
        if conflict is None:
            result.validated.append(volume)
            continue
        result.conflicting_groups.add(conflict)
        result.rejected.append({
            "uid": volume.get("uid", ""),
            "series_description": volume.get("series_description", ""),
            "body_part_examined": volume.get("body_part_examined", ""),
            "reason": (
                f"Anatomy MISMATCH: volume belongs to '{conflict}' "
                f"region, but hypothesis requires '{group}'"
            ),
        })
    return result


__all__ = [
    "ANATOMY_GROUPS",
    "Classification",
    "MIN_REGION_LENGTH",
    "classify",
    "resolve_anatomy_group",
]
