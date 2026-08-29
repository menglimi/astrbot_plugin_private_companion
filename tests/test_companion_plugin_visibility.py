# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace

from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


class CompanionPluginVisibilityTests(unittest.TestCase):
    def _summary(self, plugin: object) -> dict[str, dict[str, bool]]:
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api.plugin = plugin
        return api._companion_plugins_summary()

    def test_missing_plugins_are_reported_as_not_installed(self) -> None:
        summary = self._summary(SimpleNamespace())

        self.assertEqual(summary["boundary_feedback"], {"installed": True, "enabled": True, "available": False})
        self.assertEqual(summary["temp_emotion"], {"installed": True, "enabled": True, "available": False})
        self.assertEqual(summary["content"], {"installed": False, "enabled": False, "available": False, "reason": "content_companion_unavailable"})
        self.assertEqual(
            summary["image"],
            {"installed": False, "enabled": False, "available": False, "reason": ""},
        )
        self.assertEqual(summary["reality"], {"installed": False, "enabled": False, "available": False})

    def test_loaded_plugins_remain_installed_when_disabled(self) -> None:
        image_api = SimpleNamespace(status=lambda: {"enabled": False})
        reality_api = SimpleNamespace(status=lambda: {"available": True, "enabled": False})
        plugin = SimpleNamespace(
            _image_companion_api=lambda: image_api,
            _reality_companion_api=lambda: reality_api,
        )

        summary = self._summary(plugin)

        self.assertEqual(
            summary["image"],
            {"installed": True, "enabled": False, "available": True, "reason": ""},
        )
        self.assertEqual(
            summary["reality"],
            {"installed": True, "enabled": False, "available": True},
        )

    def test_builtin_affect_capabilities_follow_core_switches(self) -> None:
        plugin = SimpleNamespace(
            enable_relationship_boundary_feedback=False,
            enable_emotion_simulation=False,
            _enrich_boundary_feedback_intent=lambda *_args, **_kwargs: {},
            _record_interaction_emotion_event=lambda *_args, **_kwargs: {},
        )

        summary = self._summary(plugin)

        self.assertEqual(
            summary["boundary_feedback"],
            {"installed": True, "enabled": False, "available": True},
        )
        self.assertEqual(
            summary["temp_emotion"],
            {"installed": True, "enabled": False, "available": True},
        )

    def test_status_failure_does_not_hide_a_loaded_plugin(self) -> None:
        def broken_status() -> dict[str, bool]:
            raise RuntimeError("status unavailable")

        plugin = SimpleNamespace(
            _image_companion_api=lambda: SimpleNamespace(status=broken_status),
            _reality_companion_api=lambda: SimpleNamespace(status=broken_status),
        )

        summary = self._summary(plugin)

        self.assertTrue(summary["image"]["installed"])
        self.assertTrue(summary["image"]["available"])
        self.assertFalse(summary["image"]["enabled"])
        self.assertTrue(summary["reality"]["installed"])
        self.assertTrue(summary["reality"]["available"])
        self.assertFalse(summary["reality"]["enabled"])


if __name__ == "__main__":
    unittest.main()
