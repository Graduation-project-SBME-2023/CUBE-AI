"""The anatomy an uploaded volume may declare.

A NIfTI header carries geometry and nothing else -- no patient, no anatomy, not
even a free-text field that tools reliably fill in. So a converted series has no
BodyPartExamined unless the person uploading it says what they uploaded, and
without it the volume reaches validate_anatomy with only its series description
to go on.

The values below are what that check already looks for: it lowercases
BodyPartExamined, concatenates it with the series description and searches for
the keywords in ANATOMY_GROUPS (toolpacks/population/validate_anatomy.py). Each
value here contains the keyword for its group, so declaring one is enough to
place the volume in the right region. They are also DICOM defined terms for
BodyPartExamined, which is a CS -- uppercase, 16 characters at most.

Kept as data here rather than imported from validate_anatomy, so ingestion does
not depend on a tool pack (the packs are loaded by the tool catalog).
"""

from __future__ import annotations

# value -> the anatomy group it resolves to, for the uploader to display.
BODY_PARTS: dict[str, str] = {
    "CHEST": "chest",
    "LUNG": "chest",
    "MEDIASTINUM": "chest",
    "ABDOMEN": "abdomen",
    "LIVER": "abdomen",
    "SPLEEN": "abdomen",
    "KIDNEY": "abdomen",
    "PANCREAS": "abdomen",
    "GALLBLADDER": "abdomen",
    "ADRENAL": "abdomen",
    "COLON": "abdomen",
    "PELVIS": "abdomen",
    "HEAD": "head",
    "BRAIN": "head",
    "NECK": "neck",
    "THYROID": "neck",
    "SPINE": "spine",
    "HEART": "cardiac",
    "BREAST": "breast",
    "EXTREMITY": "extremity",
}


def normalise_body_part(value: str | None) -> str | None:
    """Return the canonical BodyPartExamined for a declared value.

    ``None`` when nothing was declared -- the tag is then left off entirely
    rather than written empty, so a reader can tell "not stated" from "stated
    as blank".
    """
    if value is None:
        return None
    candidate = str(value).strip().upper()
    if not candidate:
        return None
    if candidate not in BODY_PARTS:
        raise ValueError(
            f"Unsupported body part {value!r}; expected one of "
            f"{', '.join(sorted(BODY_PARTS))}"
        )
    return candidate
