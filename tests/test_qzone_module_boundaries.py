# -*- coding: utf-8 -*-
"""Structural contracts for the decomposed QQ Zone integration."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from astrbot_plugin_private_companion import qzone_integration as qzone_facade
from astrbot_plugin_private_companion.qzone_auth import QzoneAuthMixin
from astrbot_plugin_private_companion.qzone_comments import QzoneCommentMixin
from astrbot_plugin_private_companion.qzone_errors import QzoneIntegrationError
from astrbot_plugin_private_companion.qzone_feed import QzoneFeedMixin
from astrbot_plugin_private_companion.qzone_integration import QzoneIntegrationError as FacadeIntegrationError
from astrbot_plugin_private_companion.qzone_integration import QzoneMixin
from astrbot_plugin_private_companion.qzone_media import QzoneIntegrationError as MediaIntegrationError
from astrbot_plugin_private_companion.qzone_media import QzoneMediaMixin
from astrbot_plugin_private_companion.qzone_publish import QzonePublishMixin
from astrbot_plugin_private_companion.qzone_runtime import QzoneRuntimeMixin
from astrbot_plugin_private_companion.qzone_schedule import QzoneScheduleMixin


class QzoneModuleBoundaryTests(unittest.TestCase):
    def test_facade_composes_focused_mixins_in_dependency_order(self) -> None:
        self.assertEqual(
            QzoneMixin.__bases__,
            (
                QzoneCommentMixin,
                QzoneScheduleMixin,
                QzonePublishMixin,
                QzoneFeedMixin,
                QzoneRuntimeMixin,
                QzoneMediaMixin,
            ),
        )
        self.assertIn(QzoneAuthMixin, QzoneMixin.__mro__)

    def test_behavior_ownership_stays_visible_at_module_boundaries(self) -> None:
        expected_modules = {
            "_qzone_get_cookies": "qzone_runtime",
            "_qzone_query_feeds": "qzone_feed",
            "_qzone_reply_my_comment": "qzone_comments",
            "_qzone_record_published_post": "qzone_publish_part01",
            "_qzone_life_publish_daily_plan": "qzone_schedule_part01",
            "_qzone_preflight_auto_publish": "qzone_auth",
            "_publish_qzone_text": "qzone_media",
        }

        for method_name, module_suffix in expected_modules.items():
            with self.subTest(method=method_name):
                self.assertTrue(
                    getattr(QzoneMixin, method_name).__module__.endswith(module_suffix),
                    f"{method_name} should be owned by {module_suffix}",
                )

    def test_error_type_keeps_legacy_media_import_compatible(self) -> None:
        self.assertIs(QzoneIntegrationError, MediaIntegrationError)
        self.assertIs(QzoneIntegrationError, FacadeIntegrationError)

    def test_legacy_facade_keeps_media_mixin_in_star_exports(self) -> None:
        self.assertIn("QzoneMediaMixin", qzone_facade.__all__)

    def test_legacy_facade_constant_patch_reaches_schedule_logic(self) -> None:
        with patch.object(qzone_facade, "QZONE_LENGTH_HARD_LIMIT", 5):
            self.assertFalse(QzoneMixin._qzone_text_length_ok("123456", "short"))

    def test_facade_method_patch_is_used_by_class_helpers(self) -> None:
        expected = [(1, 2)]
        with patch.object(
            QzoneMixin,
            "_qzone_merge_windows",
            return_value=expected,
        ) as merge:
            actual = QzoneMixin._qzone_parse_windows("07:00-08:00")

        self.assertEqual(expected, actual)
        merge.assert_called_once_with([(7 * 60, 8 * 60)])


if __name__ == "__main__":
    unittest.main()
