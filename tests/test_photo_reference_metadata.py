from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "astrbot_plugin_private_companion"
if PACKAGE not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        PACKAGE, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = package
    spec.loader.exec_module(package)

from astrbot_plugin_private_companion.photo_reference_metadata import (
    compile_reference_metadata,
)


class PhotoReferenceMetadataTests(unittest.TestCase):
    def test_outfit_behaviors_compile_to_distinct_contracts(self) -> None:
        base = {"preserve": ["identity", "outfit"], "outfit_category": "sleepwear"}
        ignored = compile_reference_metadata({**base, "outfit_behavior": "ignore"})
        unlocked = compile_reference_metadata(
            {**base, "outfit_behavior": "reference_without_lock"}
        )
        locked = compile_reference_metadata(
            {**base, "outfit_behavior": "preserve_unless_explicit_change"}
        )
        self.assertEqual(ignored.metadata["outfit_category"], "")
        self.assertFalse(ignored.metadata["outfit_lock_default"])
        self.assertEqual(unlocked.metadata["outfit_category"], "sleepwear")
        self.assertFalse(unlocked.metadata["outfit_lock_default"])
        self.assertTrue(locked.metadata["outfit_lock_default"])

    def test_compile_reports_sources_differences_conflicts_and_trials(self) -> None:
        result = compile_reference_metadata(
            {
                "preserve": ["identity", "scene"],
                "prefer": {"scenes": ["bedroom"], "times": ["night"]},
                "avoid": {"scenes": ["bedroom"], "times": []},
                "preferred_preset": "missing",
            },
            ["home"],
            saved={"scene_categories": ["home"]},
        )
        payload = result.to_dict()
        self.assertTrue(payload["fields"])
        self.assertTrue(payload["differences"])
        self.assertTrue(payload["conflicts"])
        self.assertTrue(payload["recommended_trials"])
        self.assertEqual(result.metadata["metadata_source"], "guided_editor")

    def test_compile_does_not_mutate_saved_mapping(self) -> None:
        saved = {"reference_roles": ["identity"], "scene_categories": ["home"]}
        compile_reference_metadata({"preserve": ["scene"]}, saved=saved)
        self.assertEqual(saved, {"reference_roles": ["identity"], "scene_categories": ["home"]})


if __name__ == "__main__":
    unittest.main()
