"""The maintenance cache action clears only regenerable in-process caches."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from companion_v2.host.application import CompanionApplication


class ClearCachesTests(unittest.TestCase):
    def test_only_regenerable_caches_are_cleared(self):
        narration = SimpleNamespace(cache={"a": 1, "b": 2}, failures={"x": 1})
        diagnostics = SimpleNamespace(_reports={"r": 1, "s": 2})
        app = SimpleNamespace(image_narration=narration, diagnostics=diagnostics)
        cleared = CompanionApplication.clear_caches(app)
        self.assertEqual(cleared, {
            "image_narration": 2,
            "image_narration_failures": 1,
            "diagnostics": 2,
        })
        self.assertEqual(narration.cache, {})
        self.assertEqual(narration.failures, {})
        self.assertEqual(diagnostics._reports, {})

    def test_app_without_optional_services_is_ignored(self):
        self.assertEqual(CompanionApplication.clear_caches(SimpleNamespace()), {})

    def test_partial_services_do_not_raise(self):
        narration = SimpleNamespace(cache={"a": 1})
        app = SimpleNamespace(image_narration=narration, diagnostics=None)
        self.assertEqual(CompanionApplication.clear_caches(app), {"image_narration": 1})
        self.assertEqual(narration.cache, {})


if __name__ == "__main__":
    unittest.main()
