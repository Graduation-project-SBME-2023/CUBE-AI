from __future__ import annotations

import pytest

from tests.support.mcp_loader import load_mcp_tool


module = load_mcp_tool("feature_extraction.cache_utils")

IMAGE = "a" * 64
SEGMENT = "b" * 64
CONFIG = "c" * 64


@pytest.mark.unit
def test_cache_key_is_content_addressed():
    first = module.get_cache_key(IMAGE, SEGMENT, CONFIG)
    assert len(first) == 64
    assert module.get_cache_key(IMAGE, SEGMENT, CONFIG) == first


@pytest.mark.unit
def test_any_changed_input_misses_the_cache():
    """A changed bin width or preprocessing (config), a changed intensity (image) or a different segment must never return an earlier extraction."""
    first = module.get_cache_key(IMAGE, SEGMENT, CONFIG)
    assert module.get_cache_key(IMAGE, SEGMENT, "d" * 64) != first
    assert module.get_cache_key("e" * 64, SEGMENT, CONFIG) != first
    assert module.get_cache_key(IMAGE, "f" * 64, CONFIG) != first


@pytest.mark.unit
def test_paths_and_times_are_not_part_of_the_key():
    source = module.get_cache_key.__code__.co_varnames
    assert "image_path" not in source and "mask_path" not in source


@pytest.mark.unit
def test_malformed_digests_disable_the_cache_without_raising():
    assert module.get_cache_key("", SEGMENT, CONFIG) == ""
    assert module.get_cache_key(IMAGE, "not-a-digest", CONFIG) == ""
    assert module.get_cache_key(IMAGE, SEGMENT, None) == ""


@pytest.mark.unit
def test_cache_round_trip_and_corrupt_entry_fallback(tmp_path):
    cache_dir = tmp_path / "cache"
    module.save_to_cache(str(cache_dir), "abc", {"feature": 1.25})
    assert module.load_from_cache(str(cache_dir), "abc") == {"feature": 1.25}

    (cache_dir / "broken.json").write_text("{bad-json", encoding="utf-8")
    assert module.load_from_cache(str(cache_dir), "broken") is None
    assert module.load_from_cache(str(cache_dir), "") is None
