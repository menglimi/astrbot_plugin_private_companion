import unittest
from datetime import datetime
from types import SimpleNamespace

from astrbot_plugin_private_companion.main import PrivateCompanionPlugin
from astrbot_plugin_private_companion.user_memory import UserMemoryMixin


class Reply:
    def __init__(self, message_id: str):
        self.id = message_id


class Plain:
    def __init__(self, text: str):
        self.text = text


class _Result:
    def __init__(self, chain):
        self.chain = list(chain)

    @staticmethod
    def is_llm_result():
        return True


class _PrivateEvent:
    unified_msg_origin = "default:FriendMessage:10001"

    def __init__(self, chain, *, message_id="current-message"):
        self.result = _Result(chain)
        self.message_obj = SimpleNamespace(message_id=message_id, raw_message={"message_id": message_id})

    @staticmethod
    def is_private_chat():
        return True

    def get_result(self):
        return self.result

    def set_result(self, result):
        self.result = result


class _TimeAnchorHarness(UserMemoryMixin):
    @staticmethod
    def _environment_now():
        return datetime(2026, 7, 14, 8, 43)


class PrivateReplyScopeAndTimeAnchorTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_passive_llm_reply_drops_unexpected_quote_component(self):
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        plugin.enabled = True
        event = _PrivateEvent([Reply("other-message"), Plain("接住当前私聊")])

        await plugin.strip_unexpected_private_passive_reply(event)

        self.assertEqual(len(event.result.chain), 1)
        self.assertEqual(event.result.chain[0].text, "接住当前私聊")

    async def test_private_passive_llm_reply_keeps_current_message_quote(self):
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        plugin.enabled = True
        event = _PrivateEvent([Reply("current-message"), Plain("正常引用当前消息")])

        await plugin.strip_unexpected_private_passive_reply(event)

        self.assertEqual(len(event.result.chain), 2)
        self.assertIsInstance(event.result.chain[0], Reply)

    async def test_proactive_framework_keeps_explicit_private_quote_component(self):
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        plugin.enabled = True
        event = _PrivateEvent([Reply("planned-trigger"), Plain("预约消息")])
        event.private_companion_proactive_framework = True

        await plugin.strip_unexpected_private_passive_reply(event)

        self.assertEqual(len(event.result.chain), 2)

    def test_morning_rejects_implicit_late_night_anchor(self):
        harness = _TimeAnchorHarness()

        self.assertTrue(harness._response_has_invalid_current_time_anchor("时间不早了，真的要歇息了吧？"))
        self.assertTrue(harness._response_has_invalid_current_time_anchor("都这么晚了，早点睡吧。"))
        self.assertFalse(harness._response_has_invalid_current_time_anchor("这一大早的，在想什么呢？"))

    def test_local_fallback_removes_invalid_morning_sleep_tail(self):
        harness = _TimeAnchorHarness()
        response = "啊，我是说刚才话题跳太快了。那测试用户～时间不早了，真的要歇息了吧？"

        cleaned = harness._fallback_temporal_or_continuity_confused_reply(
            "什么",
            response,
            flags=["invalid_current_time_anchor"],
            user={},
        )

        self.assertIn("话题跳太快", cleaned)
        self.assertNotIn("时间不早", cleaned)
        self.assertNotIn("歇息", cleaned)

    def test_local_fallback_removes_invalid_time_after_generic_address(self):
        harness = _TimeAnchorHarness()

        cleaned = harness._fallback_temporal_or_continuity_confused_reply(
            "什么",
            "我刚才把话题接偏了。小林，快十一点了，困不困？",
            flags=["invalid_current_time_anchor"],
            user={},
        )

        self.assertEqual("我刚才把话题接偏了", cleaned)

    def test_routine_check_boundary_requires_evidence_and_one_focus(self):
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)

        boundary = plugin._format_private_routine_check_boundary("那……例行检查")

        self.assertIn("最多提出一个问题", boundary)
        self.assertIn("不要假定用户正在服药", boundary)
        self.assertIn("今天想先检查哪一项", boundary)
        self.assertEqual("", plugin._format_private_routine_check_boundary("上次例行检查结果是什么"))

    def test_routine_check_segment_count_is_limited_to_two(self):
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        chunks = [[Plain("先接住")], [Plain("第一问")], [Plain("第二问")], [Plain("第三问")]]

        limited = plugin._limit_private_routine_check_segments("嗯，那就晚间检查一下", chunks)

        self.assertEqual(2, len(limited))
        self.assertEqual(["第一问", "第二问", "第三问"], [part.text for part in limited[1]])
        self.assertEqual(chunks, plugin._limit_private_routine_check_segments("检查一下这个报错", chunks))

if __name__ == "__main__":
    unittest.main()
