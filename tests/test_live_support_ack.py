"""Live support acknowledgements cover both Super Chat and guard purchases."""

from types import SimpleNamespace
import unittest

from astrbot_host.native_extensions.live.activity_methods import LiveActivityMethods
from astrbot_host.native_extensions.live.bilibili_live import LiveDanmakuEvent


class LiveSupportAckTests(unittest.TestCase):
    def runtime(self, config=None):
        runtime = SimpleNamespace(
            config=config or {},
            _single_line_text=lambda value, limit=120: " ".join(str(value or "").split())[:limit],
            _bili_reply_has_named_thanks=lambda _reply, _name: False,
        )
        runtime._bili_guaranteed_support_types = (
            lambda: LiveActivityMethods._bili_guaranteed_support_types(runtime))
        return runtime

    def test_guard_purchase_is_acknowledged_by_default(self):
        runtime = self.runtime()
        event = LiveDanmakuEvent("buy_guard", "舰长甲", "成为了舰长 x1", amount=138)
        reply = LiveActivityMethods._ensure_bili_support_acknowledgement(runtime, "谢谢大家", [event])
        self.assertIn("舰长甲", reply)
        self.assertIn("支持", reply)

    def test_explicit_config_can_keep_only_super_chat(self):
        runtime = self.runtime({"bili_live_auto_reply_guaranteed_event_types": ["super_chat"]})
        event = LiveDanmakuEvent("buy_guard", "舰长甲", "成为了舰长 x1", amount=138)
        reply = LiveActivityMethods._ensure_bili_support_acknowledgement(runtime, "谢谢大家", [event])
        self.assertNotIn("舰长甲", reply)


if __name__ == "__main__":
    unittest.main()
