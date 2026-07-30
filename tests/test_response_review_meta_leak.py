import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from astrbot.api.message_components import Plain

from astrbot_plugin_private_companion.main import PrivateCompanionPlugin
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


ROOT = Path(__file__).resolve().parents[1]


class _Event:
    unified_msg_origin = "default:FriendMessage:10001"

    def __init__(self, text: str, fallback: str, *, review_guard_active: bool = True):
        self.result = SimpleNamespace(chain=[Plain(text)])
        self._private_companion_response_review_guard_active = review_guard_active
        self._private_companion_response_review_fallback_text = fallback
        self.stopped = False

    def get_result(self):
        return self.result

    def set_result(self, result):
        self.result = result

    def stop_event(self):
        self.stopped = True


class ResponseReviewMetaLeakTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        self.plugin.enabled = True
        self.plugin.enable_framework_error_leak_guard = True
        self.plugin._record_passive_no_reply = lambda *args, **kwargs: None
        self.plugin._schedule_reply_interception_forward = lambda *args, **kwargs: None

    async def test_final_guard_restores_reply_before_review(self):
        leaked = (
            "属于正常人无法容忍的一字废话，存在严重的格式化表达问题，"
            "无法通过清洗正常规整，需要重写\n"
            "Maybe 1% of the time you will leave this at the very end of a run"
        )
        event = _Event(leaked, "原标签应该是假小子，大一女学生吧。")

        await self.plugin.suppress_framework_error_leak_before_send(event)

        self.assertEqual(1, len(event.result.chain))
        self.assertEqual("原标签应该是假小子，大一女学生吧。", event.result.chain[0].text)
        self.assertFalse(event.stopped)

    async def test_final_guard_keeps_sendable_line_and_drops_meta_tail(self):
        event = _Event(
            "好，我重新说清楚。\nMaybe 1% of the time you will leave this at the very end of a run",
            "复核前正文",
        )

        await self.plugin.suppress_framework_error_leak_before_send(event)

        self.assertEqual(1, len(event.result.chain))
        self.assertEqual("好，我重新说清楚。", event.result.chain[0].text)

    async def test_final_guard_drops_unmarked_group_review_commentary(self):
        event = _Event(
            "属于正常人无法容忍的一字废话，无法通过清洗正常规整，需要重写",
            "",
            review_guard_active=False,
        )

        await self.plugin.suppress_framework_error_leak_before_send(event)

        self.assertEqual([], list(event.result.chain or []))
        self.assertTrue(event.stopped)

    async def test_framework_error_guard_can_be_disabled_independently(self):
        self.plugin.enable_framework_error_leak_guard = False
        event = _Event("Provider API Error：这是一段需要在群里讨论的技术文本", "")

        await self.plugin.suppress_framework_error_leak_before_send(event)

        self.assertEqual("Provider API Error：这是一段需要在群里讨论的技术文本", event.result.chain[0].text)
        self.assertFalse(event.stopped)

    async def test_framework_error_guard_remains_enabled_by_default(self):
        event = _Event("Provider API Error: upstream timeout", "")

        await self.plugin.suppress_framework_error_leak_before_send(event)

        self.assertEqual([], list(event.result.chain or []))
        self.assertTrue(event.stopped)

    def test_group_interjection_fails_closed_on_non_json_review_text(self):
        leaked = "属于正常人无法容忍的一字废话，无法通过清洗正常规整，需要重写"

        should_reply, reply, reason = self.plugin._parse_group_interjection_decision(leaked)

        self.assertFalse(should_reply)
        self.assertEqual("", reply)
        self.assertEqual("invalid_json", reason)

    def test_group_interjection_rejects_meta_text_inside_valid_json(self):
        raw = '{"should_reply":true,"text":"无法通过清洗，需要重写","reason":"ok"}'

        should_reply, reply, reason = self.plugin._parse_group_interjection_decision(raw)

        self.assertFalse(should_reply)
        self.assertEqual("", reply)
        self.assertEqual("review_meta_leak", reason)

    def test_framework_error_guard_is_exposed_as_feature_switch(self):
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        item = schema["emotion_relationship_config"]["items"]["enable_framework_error_leak_guard"]
        self.assertTrue(item["default"])

        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api.plugin = self.plugin
        api._schema_key_index_cache = None
        self.assertIn("enable_framework_error_leak_guard", api._allowed_feature_keys())

        script = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
        self.assertIn('key: "enable_framework_error_leak_guard"', script)


if __name__ == "__main__":
    unittest.main()
