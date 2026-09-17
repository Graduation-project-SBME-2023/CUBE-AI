import hashlib
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


BACKEND_DIR = Path(__file__).resolve().parents[3]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from agent_system.agents.literature_review_agent import knowledge_builder as module


class LiteratureKnowledgeCacheTests(unittest.TestCase):
    def test_cache_key_is_versioned_and_collision_resistant(self):
        key = module._cache_key("full kb!*", "shared query")
        digest = hashlib.sha256("shared query".encode("utf-8")).hexdigest()

        self.assertEqual(key, f"{module.CACHE_FORMAT_VERSION}_full_kb_{digest}.json")
        self.assertRegex(key, r"^v\d+_[A-Za-z0-9_]+_[0-9a-f]{64}\.json$")
        self.assertNotEqual(key, module._cache_key("full kb!*", "different query"))

    def test_cache_rejects_path_like_keys(self):
        with self.assertRaises(ValueError):
            module._cache_path("../outside.json")
        with self.assertRaises(ValueError):
            module._cache_path("nested/file.json")
        with self.assertRaises(ValueError):
            module._cache_path("missing-extension")

    def test_cache_write_read_uses_shared_cache_directory(self):
        original_dir = module.LITERATURE_CACHE_DIR
        try:
            with TemporaryDirectory() as temp_dir:
                cache_dir = Path(temp_dir) / "storage" / "cache" / "literature"
                module.LITERATURE_CACHE_DIR = cache_dir
                key = module._cache_key("wiki", "liver radiomics")
                payload = {"ok": True, "papers": [1, 2, 3]}

                module._set_cache(key, payload)

                self.assertEqual(module._get_cached(key), payload)
                self.assertEqual(module._cache_path(key), cache_dir / key)
                self.assertEqual(list(cache_dir.glob("*.tmp")), [])
        finally:
            module.LITERATURE_CACHE_DIR = original_dir


if __name__ == "__main__":
    unittest.main()
