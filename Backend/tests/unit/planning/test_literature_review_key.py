"""Regression test for a production incident: `literature_reviews` had zero
rows across every session because `review_key` was the raw PICO JSON dump,
which blew past the schema's 500-char cap
(`LiteratureReviewCreate.review_key`) for any real clinical hypothesis, so
every `create_literature_review` call failed with 422.
"""

from __future__ import annotations

import pytest

from agents.planning_agents.planner import literature_review_key

pytestmark = pytest.mark.unit

# A realistic hypothesis PICO — each field alone is well over 100 chars,
# which is exactly the shape that broke the raw-JSON key.
REALISTIC_PICO = {
    "population": (
        "In adult patients (>=18 years) undergoing contrast-enhanced "
        "abdominal CT for suspected or known splenic pathology"
    ),
    "intervention": (
        "Radiomic features extracted from the whole-spleen region of "
        "interest using standardized PyRadiomics extraction settings"
    ),
    "comparison": (
        "Standard clinical parameters alone, without radiomic feature "
        "augmentation, as currently used in routine radiological assessment"
    ),
    "outcome": (
        "Improved classification accuracy of healthy spleens versus "
        "spleens with malignant involvement, measured by AUC-ROC"
    ),
    "hypothesis": (
        "Do radiomic features extracted from the whole-spleen ROI improve "
        "classification of healthy spleens versus spleens with malignant "
        "involvement compared with standard clinical parameters?"
    ),
    "question": (
        "Do radiomic features extracted from the whole-spleen ROI improve "
        "classification of healthy spleens versus spleens with malignant "
        "involvement compared with standard clinical parameters?"
    ),
}


def test_review_key_stays_under_the_schema_cap_for_a_realistic_pico():
    """The exact failure mode from the incident: json.dumps(pico) for a real
    hypothesis is comfortably over the 500-char schema limit."""
    import json

    raw_length = len(json.dumps(REALISTIC_PICO, sort_keys=True))
    assert raw_length > 500, "fixture should reproduce the original overflow"

    key = literature_review_key(REALISTIC_PICO)
    assert len(key) <= 500
    assert len(key) == 64  # sha256 hexdigest — fixed-length regardless of input


def test_review_key_is_stable_for_the_same_pico():
    """Idempotency (UNIQUE(session_id, review_key)) requires the same PICO to
    produce the same key every time, independent of dict insertion order."""
    reordered = dict(reversed(list(REALISTIC_PICO.items())))
    assert literature_review_key(REALISTIC_PICO) == literature_review_key(reordered)


def test_review_key_differs_for_a_different_pico():
    changed = dict(REALISTIC_PICO, outcome="A completely different outcome measure")
    assert literature_review_key(REALISTIC_PICO) != literature_review_key(changed)
