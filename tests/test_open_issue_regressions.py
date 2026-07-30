# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace

from astrbot_plugin_private_companion.forward_message import ForwardMessageMixin
from astrbot_plugin_private_companion.helpers import (
    _normalize_timezone_setting,
    _resolve_timezone_setting,
)
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi
from astrbot_plugin_private_companion.user_memory import UserMemoryMixin


class _ReviewHarness(UserMemoryMixin):
    response_review_max_chars = 260
    enable_passive_topic_suppression = False
    proactive_reply_context_hours = 2

    @staticmethod
    def _response_has_invalid_current_time_anchor(_text):
        return False

    @staticmethod
    def _response_has_false_no_reply_claim(_text, _inbound, _user):
        return False

    @staticmethod
    def _active_private_fact_correction(_user, _inbound):
        return None

    @staticmethod
    def _looks_like_private_fact_correction(_inbound):
        return False

    @staticmethod
    def _response_claims_user_prior_action(_text, _user):
        return False

    @staticmethod
    def _response_reverses_recent_proactive_media_ownership(_text, _user, _inbound):
        return False

    @staticmethod
    def _expression_style_review_enabled():
        return False

    @staticmethod
    def _proactive_topic_signature(_text):
        return ""


class OpenIssueRegressionTests(unittest.IsolatedAsyncioTestCase):
    def test_qq_image_hosts_are_recognized_without_accepting_lookalikes(self):
        harness = ForwardMessageMixin()
        image_urls = (
            "https://multimedia.nt.qq.com.cn/download?appid=1407&fileid=x&rkey=y",
            "https://gchat.qpic.cn/gchatpic_new/0/0-0-X/0?term=2",
            "https://c2cpicdw.qpic.cn/offpic_new/0/0-0-X/0?term=2",
        )
        for url in image_urls:
            with self.subTest(url=url):
                info = harness._extract_reply_rich_card_info({"type": "image", "data": {"url": url}})
                self.assertIn(url, info["images"])

        rejected_urls = (
            "https://multimedia.nt.qq.com.cn/download?appid=1403&format=amr&fileid=x",
            "https://evil-qpic.cn.example/download?appid=1407&fileid=x",
        )
        for url in rejected_urls:
            with self.subTest(url=url):
                info = harness._extract_reply_rich_card_info({"type": "image", "data": {"url": url}})
                self.assertNotIn(url, info["images"])

    async def test_tts_placeholder_reply_skips_destructive_rewrite(self):
        harness = UserMemoryMixin()
        response = "[[PCTTS:0123456789abcdef]]\n测试结束。"

        reviewed = await harness._review_and_rewrite_response({}, "发条语音", response)

        self.assertEqual(response, reviewed)

    def test_tts_placeholder_is_not_counted_as_visible_paragraph(self):
        harness = _ReviewHarness()

        flags = harness._response_review_flags(
            "[[PCTTS:0123456789abcdef]]\n测试结束。",
            {},
            inbound_text="发条语音",
        )

        self.assertNotIn("casual_overexplained", flags)

    def test_global_timezone_follows_astrbot_and_preserves_custom_values(self):
        self.assertEqual("global", _normalize_timezone_setting("follow_global"))
        self.assertEqual(
            "America/New_York",
            _resolve_timezone_setting("global", global_timezone="America/New_York"),
        )
        self.assertEqual(
            "Europe/London",
            _resolve_timezone_setting(
                "global",
                global_timezone="invalid/timezone",
                system_timezone="Europe/London",
            ),
        )
        self.assertEqual(
            "Asia/Shanghai",
            _resolve_timezone_setting("Asia/Shanghai", global_timezone="America/New_York"),
        )

    def test_page_keeps_global_setting_while_applying_effective_timezone(self):
        plugin = SimpleNamespace(
            config={},
            _resolve_environment_perception_timezone=lambda value: (
                "America/New_York" if value == "global" else value
            ),
        )
        api = PrivateCompanionPageApi(plugin)

        normalized = api._normalize_setting_value(
            "environment_perception_timezone",
            "follow_global",
        )
        api._apply_config_value("environment_perception_timezone", normalized)

        self.assertEqual("global", plugin.environment_perception_timezone_setting)
        self.assertEqual("America/New_York", plugin.environment_perception_timezone)


if __name__ == "__main__":
    unittest.main()
