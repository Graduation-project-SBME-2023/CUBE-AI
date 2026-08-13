from __future__ import annotations

import pickle
import sys
from pathlib import Path

from lib.configs.voxtell_supported_labels import VOXTELL_SUPPORTED_LABELS
from radiology_app.integrations.voxtell.cache import (
    build_voxtell_cache,
    load_exact_cache,
    save_cache_atomically,
)


def test_complete_cache_skips_heavy_ml_imports(tmp_path: Path) -> None:
    output_dir = tmp_path / "voxtell"
    output_dir.mkdir()
    cache_path = output_dir / "text_embeddings_cache.pkl"
    cache_path.write_bytes(
        pickle.dumps({label.lower().strip(): 1 for label in VOXTELL_SUPPORTED_LABELS})
    )
    torch_was_loaded = "torch" in sys.modules

    count = build_voxtell_cache(
        text_model_path=tmp_path / "unused-model",
        voxtell_dir=output_dir,
    )

    assert count == len(VOXTELL_SUPPORTED_LABELS)
    assert ("torch" in sys.modules) is torch_was_loaded


def test_cache_is_atomically_replaced_beside_destination(tmp_path: Path) -> None:
    cache_path = tmp_path / "mounted-models" / "text_embeddings_cache.pkl"

    save_cache_atomically(cache_path, {"liver": 1})

    assert load_exact_cache(cache_path) == {"liver": 1}
    assert list(cache_path.parent.glob(".voxtell-cache-*.tmp")) == []
