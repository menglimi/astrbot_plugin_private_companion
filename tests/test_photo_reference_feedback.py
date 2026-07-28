from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "astrbot_plugin_private_companion"
package = sys.modules.setdefault(PACKAGE_NAME, types.ModuleType(PACKAGE_NAME))
package.__path__ = [str(PLUGIN_ROOT)]
package.__package__ = PACKAGE_NAME

from astrbot_plugin_private_companion.photo_reference_feedback import (
    analyze_photo_reference_feedback,
)


class PhotoReferenceFeedbackTests(unittest.TestCase):
    def test_feedback_labels_and_regeneration_request_are_structured(self) -> None:
        feedback = analyze_photo_reference_feedback(
            "脸不像，衣服也不对，场景根本没换，重新生成一张"
        )

        self.assertTrue(feedback.regenerate_requested)
        self.assertEqual(
            feedback.issues,
            ("face_mismatch", "outfit_mismatch", "scene_not_changed"),
        )
        self.assertGreaterEqual(feedback.confidence, 0.9)
        self.assertEqual(feedback.source, "rule")


if __name__ == "__main__":
    unittest.main()
