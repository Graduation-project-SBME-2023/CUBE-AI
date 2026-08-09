import unittest
from datetime import datetime

from agent_system.agents.execution_agents.segmentation.naming import (
    build_segmentation_series_description,
)


class SegmentationSeriesDescriptionTests(unittest.TestCase):
    def test_matches_assisted_segmentation_name_format(self):
        description = build_segmentation_series_description(
            "voxtell",
            "spleen lesion",
            datetime(2026, 8, 8, 21, 30),
        )

        self.assertEqual(description, "08/08/2026 - voxtell - spleen lesion")


if __name__ == "__main__":
    unittest.main()
