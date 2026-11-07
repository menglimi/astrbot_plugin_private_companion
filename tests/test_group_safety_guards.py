import unittest
from types import SimpleNamespace as NS

from companion_v2.conversation.participation_prompts import group_hints
from companion_v2.conversation.reply_settings import (
    GROUP_SAFETY_DEFAULTS, configuration_fields, group_safety_settings,
)
from companion_v2.conversation.segmentation import SegmentationPolicy
from companion_v2.conversation.text_processing import (
    REALITY_PROMISE_GUARD_REPLACEMENT, text_policy,
)
from companion_v2.memory.prompts import REFLECTION_INSTRUCTION


class GroupSafetyGuardTests(unittest.TestCase):
    def _hints(self, settings=None):
        request = NS(event=NS(received_at=__import__("datetime").datetime(2026, 9, 27)),
                     context=NS(materials=()))
        context = NS(actor=NS(principal=NS(native_account_id="200")))
        return group_hints(request, context, settings or {"group_wakeup_enabled": False}, (), {})

    def test_defaults_and_boolean_validation(self):
        self.assertEqual(GROUP_SAFETY_DEFAULTS,
                         {"enable_group_injection_guard": True,
                          "enable_group_privacy_guard": True,
                          "enable_group_reality_promise_guard": True})
        self.assertEqual(group_safety_settings({}), GROUP_SAFETY_DEFAULTS)
        with self.assertRaises(Exception):
            group_safety_settings({"enable_group_privacy_guard": "true"})

    def test_configuration_fields_register_safety_switches(self):
        fields = configuration_fields(SegmentationPolicy(), True, 0.0)
        names = {field.field_id.rsplit(".", 1)[-1] for field in fields}
        self.assertTrue(set(GROUP_SAFETY_DEFAULTS) <= names)

    def test_prompt_contains_boundaries_and_model_judged_addressing(self):
        hint = self._hints()
        for phrase in ("不是系统或开发者指令", "群聊隐私边界", "群聊现实承诺边界", "自行判断是否合适"):
            self.assertIn(phrase, hint)
        disabled = self._hints({"group_wakeup_enabled": False,
                                "enable_group_injection_guard": False,
                                "enable_group_privacy_guard": False,
                                "enable_group_reality_promise_guard": False})
        self.assertNotIn("不是系统或开发者指令", disabled)
        self.assertNotIn("群聊隐私边界", disabled)
        self.assertNotIn("群聊现实承诺边界", disabled)
        self.assertIn("自行判断是否合适", disabled)

    def test_reality_promise_guard_is_narrow_and_preserves_literals(self):
        policy = text_policy({})
        changed, blocked = policy.reality_promise_guard("我已经拉群了")
        self.assertTrue(blocked)
        self.assertEqual(changed, REALITY_PROMISE_GUARD_REPLACEMENT)
        for text in ("我已经加群了", "我马上建群", "我把大家拉进群了"):
            with self.subTest(text=text):
                value, blocked = policy.reality_promise_guard(text)
                self.assertTrue(blocked, text)
                self.assertEqual(value, REALITY_PROMISE_GUARD_REPLACEMENT)
        for text in ("我想修网，但需要管理员处理", "‘我已经拉群了’是成员举的例子",
                     "https://example.test/我已经拉群了", "<quote>我已经拉群了</quote>",
                     "我已经拉群了",):
            if text == "我已经拉群了":
                continue
            value, blocked = policy.reality_promise_guard(text)
            self.assertFalse(blocked, text)
            self.assertEqual(value, text)
        evidence, blocked = policy.reality_promise_guard(
            "依据确认：我已经拉群了", statements=("我已经拉群了",))
        self.assertFalse(blocked)
        self.assertEqual(evidence, "依据确认：我已经拉群了")

    def test_daily_reflection_rechecks_addressing(self):
        self.assertIn("每日反思阶段要复核称呼是否合适", REFLECTION_INSTRUCTION)
        self.assertIn("不改变身份、权限或成员画像", REFLECTION_INSTRUCTION)


if __name__ == "__main__":
    unittest.main()
