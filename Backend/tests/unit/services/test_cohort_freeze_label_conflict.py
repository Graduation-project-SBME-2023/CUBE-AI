"""A cohort must not be approvable with a patient labelled both ways.

Regression test for a real production incident: a 7-volume cohort ran through
segmentation (~45 min) and radiomics extraction (~3 min) before
`radiomics_extract_features` rejected it because one subject had members
labelled both 0 and 1. `freeze_cohort` is the one-way approval gate — the
last checkpoint before that compute runs — so this conflict must be caught
there, not ~50 minutes later deep inside feature extraction.
"""

from __future__ import annotations

import pytest

from application_api.repositories.cohorts import _label_conflict

pytestmark = pytest.mark.unit


def _row(subject_key: str, ordinal: int, target: int) -> dict:
    return {
        "member_id": f"member-{ordinal}",
        "ordinal": ordinal,
        "subject_key": subject_key,
        "study_uid": f"study-{ordinal}",
        "volume_series_instance_uid": f"volume-{ordinal}",
        "target": target,
        "label_source": "llm_extraction",
        "label_confidence": "high",
    }


def test_no_conflict_when_every_subject_is_labelled_consistently():
    rows = [
        _row("patient-a", 1, 1),
        _row("patient-a", 2, 1),  # same patient, second series, same outcome
        _row("patient-b", 3, 0),
    ]
    assert _label_conflict(rows) is None


def test_flags_the_subject_whose_series_disagree():
    """One patient, two series, contradictory labels — exactly the shape from
    the production log (subject 'ce6f35ca-...' labelled both 1 and 0)."""
    rows = [
        _row("patient-a", 1, 1),
        _row("patient-a", 2, 0),
        _row("patient-b", 3, 0),
    ]
    assert _label_conflict(rows) == "patient-a"


def test_single_volume_per_subject_can_never_conflict():
    rows = [_row("patient-a", 1, 1), _row("patient-b", 2, 0)]
    assert _label_conflict(rows) is None


def test_empty_rows_have_no_conflict():
    assert _label_conflict([]) is None
