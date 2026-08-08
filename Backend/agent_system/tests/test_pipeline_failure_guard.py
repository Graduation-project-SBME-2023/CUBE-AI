import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2]
AGENT_SYSTEM_DIR = BACKEND_DIR / "agent_system"
for import_root in (str(BACKEND_DIR), str(AGENT_SYSTEM_DIR)):
    if import_root not in sys.path:
        sys.path.insert(0, import_root)

from services.pipeline_monitor import (
    is_fatal_tool_failure,
    raise_for_fatal_tool_failure,
)


class PipelineFailureGuardTests(unittest.TestCase):
    def test_runtime_segmentation_wrapper_failure_is_fatal(self):
        self.assertTrue(
            is_fatal_tool_failure(
                "segmentation_agent",
                "run_segmentation_with_guard",
                True,
            )
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "run_segmentation_with_guard failed",
        ):
            raise_for_fatal_tool_failure(
                "segmentation_agent",
                "run_segmentation_with_guard",
                True,
                "segmentation failed",
            )

    def test_non_failure_is_not_fatal(self):
        self.assertFalse(
            is_fatal_tool_failure(
                "segmentation_agent",
                "run_segmentation_with_guard",
                False,
            )
        )


if __name__ == "__main__":
    unittest.main()
