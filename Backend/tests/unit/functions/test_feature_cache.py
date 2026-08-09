from __future__ import annotations

import os

import pytest

from tests.support.mcp_loader import load_mcp_tool


module = load_mcp_tool("feature_extraction.cache_utils")


@pytest.mark.unit
def test_cache_key_is_stable_and_changes_with_inputs(tmp_path):
    image = tmp_path / "image.nii.gz"
    mask = tmp_path / "mask.nii.gz"
    image.write_bytes(b"image")
    mask.write_bytes(b"mask")

    first = module.get_cache_key(str(image), str(mask), 25.0)
    second = module.get_cache_key(str(image), str(mask), 25.0)
    changed_width = module.get_cache_key(str(image), str(mask), 50.0)

    assert len(first) == 64
    assert first == second
    assert changed_width != first

    next_mtime = mask.stat().st_mtime + 10
    os.utime(mask, (next_mtime, next_mtime))
    assert module.get_cache_key(str(image), str(mask), 25.0) != first


@pytest.mark.unit
def test_cache_round_trip_and_corrupt_entry_fallback(tmp_path):
    cache_dir = tmp_path / "cache"
    module.save_to_cache(str(cache_dir), "abc", {"feature": 1.25})
    assert module.load_from_cache(str(cache_dir), "abc") == {"feature": 1.25}

    (cache_dir / "broken.json").write_text("{bad-json", encoding="utf-8")
    assert module.load_from_cache(str(cache_dir), "broken") is None
    assert module.load_from_cache(str(cache_dir), "") is None


@pytest.mark.unit
def test_missing_files_disable_cache_without_raising(tmp_path):
    assert module.get_cache_key(
        str(tmp_path / "missing-image"),
        str(tmp_path / "missing-mask"),
        25.0,
    ) == ""

