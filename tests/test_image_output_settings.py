from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest

from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.conversation.media.output_settings import (
    DEFAULTS, GROUP, ImageOutputConfigurationOwner, apply_prompts, configuration_fields,
    effective_settings, image_request_denial, is_rule_image_request, parse_scene_presets,
    prompt_instruction,
)
from companion_v2.execution.models import TaskError
from companion_v2.foundation.refs import ScopeKind, SubjectKind, SubjectRef
from companion_v2.storage.runtime import StorageRuntime


class ImageOutputSettingsTests(unittest.TestCase):
    def test_image_output_settings_keep_gallery_attachment_cap_separate_from_image_quota(self):
        self.assertEqual(DEFAULTS["proactive_probability"], 0.35)
        self.assertEqual(DEFAULTS["proactive_max_daily"], 1)
        self.assertIs(DEFAULTS["group_enabled"], True)
        self.assertEqual(DEFAULTS["request_permission"], "everyone")
        field_ids = {field.field_id for field in configuration_fields()}
        self.assertNotIn("conversation.image_output.rule_max_daily", field_ids)
        self.assertIn("conversation.image_output.proactive_max_daily", field_ids)
        self.assertIn("conversation.image_output.group_enabled", field_ids)
        self.assertIn("conversation.image_output.request_permission", field_ids)

    def test_group_switch_and_request_permission_are_enforced_before_generation(self):
        class Runtime:
            def __init__(self, tier=None):
                self.tier = tier

            def read_one(self, _sql, _params):
                return {"tier": self.tier} if self.tier else None

        standard = NS(subject=NS(kind=SubjectKind.HUMAN, subject_id="standard"))
        core = NS(subject=NS(kind=SubjectKind.HUMAN, subject_id="core"))
        private = NS(scope=NS(kind=ScopeKind.PRIVATE), actor=standard)
        group = NS(scope=NS(kind=ScopeKind.GROUP), actor=standard)
        core_private = NS(scope=NS(kind=ScopeKind.PRIVATE), actor=core)
        core_group = NS(scope=NS(kind=ScopeKind.GROUP), actor=core)

        group_off = {**DEFAULTS, "group_enabled": False}
        self.assertEqual(image_request_denial(Runtime(), group, group_off)[0],
                         "group_image_requests_disabled")
        self.assertIsNone(image_request_denial(Runtime(), private, group_off))

        core_only = {**DEFAULTS, "request_permission": "core_only"}
        self.assertEqual(image_request_denial(Runtime(), private, core_only)[0],
                         "image_requests_core_only")
        self.assertEqual(image_request_denial(Runtime(), group, core_only)[0],
                         "image_requests_core_only")
        self.assertIsNone(image_request_denial(Runtime("core"), core_private, core_only))
        self.assertIsNone(image_request_denial(Runtime("core"), core_group, core_only))
        self.assertEqual(image_request_denial(
            Runtime(), private, {**DEFAULTS, "trigger_mode": "off"})[0],
            "image_requests_disabled")

    def test_scene_marker_injects_the_selected_role_preset(self):
        values = dict(DEFAULTS)
        result = apply_prompts(
            {"prompt": "portrait at home [场景预设:睡前居家]"}, "image.generate", values)
        self.assertIn("cozy bedroom", result["prompt"])
        self.assertIn("portrait at home", result["prompt"])
        self.assertNotIn("[场景预设", result["prompt"])

    def test_scene_prompt_instruction_uses_configured_names(self):
        self.assertIn("校园日常", prompt_instruction(DEFAULTS))
        self.assertIn("[场景预设:名称]", prompt_instruction(DEFAULTS))

    def test_natural_language_requests_include_selfies_and_pending_followups(self):
        for mode in ("tool", "rules"):
            with self.subTest(mode=mode):
                instruction = prompt_instruction({**DEFAULTS, "trigger_mode": mode, "prompt_format": "natural"})
                self.assertIn("来张自拍看看", instruction)
                self.assertIn("尚未完成的同一请求", instruction)
                self.assertIn("本轮用户要求重试时，重新调用工具", instruction)
                self.assertIn("unknown 表示结果未确认", instruction)
                self.assertIn("已完成的请求", instruction)
                self.assertIn("自拍画面在 prompt 中写明 selfie", instruction)
                self.assertIn("连贯的自然语言", instruction)
                self.assertNotIn("只在本轮", instruction)

    def test_rule_mode_only_matches_clear_image_requests(self):
        for text in ("来张自拍看看", "画一只白猫", "我想画一只猫", "可以画一张吗",
                     "我想看看你现在的穿搭", "你的自拍呢", "把图片改成蓝色", "再来一张", "继续", "看看",
                     "额度取消了，再试试",
                     "draw a portrait", "generate an image of a cat"):
            with self.subTest(text=text):
                self.assertTrue(is_rule_image_request(text))
        for text in ("今天天气不错", "这张图是什么意思", "今天好看吗", ""):
            with self.subTest(text=text):
                self.assertFalse(is_rule_image_request(text))
        self.assertTrue(is_rule_image_request("帮我改一下", has_images=True))
        self.assertFalse(is_rule_image_request("帮我改一下"))

    def test_disabled_image_requests_do_not_invite_a_tool_call(self):
        for values in ({**DEFAULTS, "trigger_mode": "off"}, {**DEFAULTS, "allow_user_requests": False}):
            self.assertIn("已关闭用户请求生图和改图", prompt_instruction(values))
            self.assertNotIn("场景预设", prompt_instruction(values))

    def test_selfie_prompt_preserves_the_selected_format_and_role_defaults(self):
        values = {**DEFAULTS, "prompt_format": "natural", "selfie_fixed_prompt": "角色外观锚点",
                  "style": "anime", "style_prompt": "柔和夜间光线", "selfie_negative_prompt": "错误人物"}
        result = apply_prompts({"prompt": "selfie，穿家居服坐在书桌边"}, "image.generate", values)
        self.assertEqual(result["prompt"], "角色外观锚点\nanime illustration\n柔和夜间光线\nselfie，穿家居服坐在书桌边")
        self.assertEqual(result["negative_prompt"], "错误人物")

    def test_blank_negative_prompt_falls_back_to_the_built_in_safe_default(self):
        cases = (("image.generate", "text2img", "a quiet street at night"),
                 ("image.generate", "selfie", "selfie in a cozy room"),
                 ("image.edit", "edit", "repaint the background"))
        for action, kind, prompt in cases:
            for blank in ("", "   ", "\n"):
                with self.subTest(kind=kind, blank=repr(blank)):
                    values = {**DEFAULTS, kind + "_negative_prompt": blank}
                    result = apply_prompts({"prompt": prompt}, action, values)
                    self.assertEqual(result["negative_prompt"], DEFAULTS[kind + "_negative_prompt"])

    def test_explicit_negative_prompt_still_wins_over_the_built_in_default(self):
        values = {**DEFAULTS, "text2img_negative_prompt": "bad hands, extra fingers"}
        result = apply_prompts({"prompt": "a cat on a windowsill"}, "image.generate", values)
        self.assertEqual(result["negative_prompt"], "bad hands, extra fingers")

    def test_unset_negative_prompt_uses_the_built_in_default(self):
        values = {key: value for key, value in DEFAULTS.items() if key != "selfie_negative_prompt"}
        result = apply_prompts({"prompt": "selfie by the window"}, "image.generate", values)
        self.assertEqual(result["negative_prompt"], DEFAULTS["selfie_negative_prompt"])

    def test_effective_settings_treat_stored_blank_negative_prompts_as_unset(self):
        temporary = TemporaryDirectory(prefix="companion-v2-image-output-")
        self.addCleanup(temporary.cleanup)
        installation = "installation-image-output"
        runtime = StorageRuntime.open(data_root=Path(temporary.name) / "v2", installation_id=installation)
        self.addCleanup(runtime.close)
        role = SubjectRef(installation, SubjectKind.BOT, "role-a")
        target = ConfigTarget(installation, ConfigLayer.ROLE, role, bot_id="bot-a")
        ImageOutputConfigurationOwner(runtime).write(
            target, configuration_fields(), reset=(), expected_revision=0,
            observed_at=datetime(2026, 10, 31, tzinfo=timezone.utc),
            changes={GROUP + "." + kind + "_negative_prompt": ""
                     for kind in ("text2img", "selfie", "edit")})
        context = NS(scope=NS(installation_id=installation, bot_id="bot-a"),
                     role_binding=NS(role=role))
        values = effective_settings(runtime, context)
        for kind in ("text2img", "selfie", "edit"):
            self.assertEqual(values[kind + "_negative_prompt"], DEFAULTS[kind + "_negative_prompt"])

    def test_scene_presets_reject_duplicate_and_unbounded_values(self):
        duplicate = '[{"id":"home","name":"居家","prompt":"room"},{"id":"home","name":"卧室","prompt":"bedroom"}]'
        with self.assertRaises(TaskError):
            parse_scene_presets(duplicate)
        with self.assertRaises(TaskError):
            parse_scene_presets("[] " + " " * 4000)


if __name__ == "__main__":
    unittest.main()
