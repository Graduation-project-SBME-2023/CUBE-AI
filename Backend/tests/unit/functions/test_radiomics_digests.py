"""Content digests for radiomics provenance."""

from __future__ import annotations

import pytest

from tests.support.mcp_loader import load_mcp_tool

np = pytest.importorskip("numpy")
digests = load_mcp_tool("feature_extraction.digests")

pytestmark = pytest.mark.unit

GEOMETRY = {"spacing": (1.0, 1.0, 2.5), "origin": (0.0, -10.0, 5.0), "direction": (1, 0, 0, 0, 1, 0, 0, 0, 1)}


def _image(value: float = 1.0):
    return np.full((2, 3, 4), value, dtype=np.float32)


def test_the_same_content_digests_identically():
    first = digests.array_digest(_image(), role="image", **GEOMETRY)
    assert len(first) == 64
    assert digests.array_digest(_image().copy(), role="image", **GEOMETRY) == first


def test_a_changed_intensity_is_detected():
    """A changed source intensity is detected, never reinterpreted."""
    base = _image()
    changed = base.copy()
    changed[0, 0, 0] += 1e-3
    assert digests.array_digest(base, role="image", **GEOMETRY) != digests.array_digest(
        changed, role="image", **GEOMETRY
    )


def test_geometry_is_part_of_the_identity():
    base = digests.array_digest(_image(), role="image", **GEOMETRY)
    moved = dict(GEOMETRY, spacing=(1.0, 1.0, 3.0))
    assert digests.array_digest(_image(), role="image", **moved) != base


def test_byte_order_does_not_change_the_digest():
    little = _image(2.0)
    big = little.astype(little.dtype.newbyteorder(">"))
    assert digests.array_digest(little, role="image", **GEOMETRY) == digests.array_digest(
        big, role="image", **GEOMETRY
    )


def test_the_segment_identity_is_part_of_a_segmentation_digest():
    mask = np.ones((2, 2, 2), dtype=np.uint8)
    one = digests.array_digest(mask, role="segmentation", identity={"segment_number": 1}, **GEOMETRY)
    two = digests.array_digest(mask, role="segmentation", identity={"segment_number": 2}, **GEOMETRY)
    assert one != two


def test_every_setting_changes_the_config_digest():
    """Changing bin width or preprocessing cannot return a cached earlier extraction — the key moves with every setting and the extractor version."""
    settings = {"setting": {"binWidth": 25.0, "normalize": True}, "imageType": {"Original": {}}}
    base = digests.config_digest("pyradiomics", "3.1.0", settings)
    assert digests.config_digest("pyradiomics", "3.1.0", settings) == base
    wider = {"setting": {"binWidth": 50.0, "normalize": True}, "imageType": {"Original": {}}}
    assert digests.config_digest("pyradiomics", "3.1.0", wider) != base
    assert digests.config_digest("pyradiomics", "3.2.0", settings) != base
    reordered = {"imageType": {"Original": {}}, "setting": {"normalize": True, "binWidth": 25.0}}
    assert digests.config_digest("pyradiomics", "3.1.0", reordered) == base


def test_a_config_with_nan_cannot_be_digested():
    with pytest.raises(ValueError):
        digests.config_digest("pyradiomics", "3.1.0", {"binWidth": float("nan")})


def test_the_source_digest_moves_with_every_part():
    parts = dict(
        image_digest="a" * 64,
        segmentation_digest="b" * 64,
        config_digest="c" * 64,
        label_series_instance_uid="1.2.3",
        segment_number=1,
    )
    base = digests.source_digest(**parts)
    for field, other in (
        ("image_digest", "d" * 64),
        ("segmentation_digest", "e" * 64),
        ("config_digest", "f" * 64),
        ("label_series_instance_uid", "1.2.4"),
        ("segment_number", 2),
    ):
        assert digests.source_digest(**{**parts, field: other}) != base, field


def test_a_feature_sets_source_digest_is_order_independent():
    rows = ["a" * 64, "b" * 64, "c" * 64]
    assert digests.rows_source_digest(rows) == digests.rows_source_digest(list(reversed(rows)))
    assert digests.rows_source_digest(rows) != digests.rows_source_digest(rows[:2])
