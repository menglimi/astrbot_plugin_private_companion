"""Natural image instructions and role references reach the native SDK request."""

from datetime import datetime, timezone
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from tests import astrbot_data_root
from astrbot.core.agent.message import Message, TextPart, dump_messages_with_checkpoints
from astrbot.core.provider.entities import ProviderRequest

from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA
from astrbot_host.core_reply_image_prompt import (core_image_tool_contract, core_image_tools,
                                                  inject_core_image_prompt)
from astrbot_host.core_reply_perception import inject_core_reply_perception_context
from astrbot_host.core_reply_tools import native_tool_name
from astrbot_host.native_extensions.host_events import prepare_native_host_request
from companion_v2.conversation.media.output_settings import DEFAULTS
from companion_v2.execution.models import TaskError
from companion_v2.foundation.refs import ScopeKind, SubjectKind


class CoreReplyImagePromptTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.references = Mock()
        self.references.prompt_material.return_value = {
            "references": [{"id": "role-face", "label": "角色脸部", "roles": ["identity"],
                            "usage": {"base_identity": True}}],
            "wardrobe": {"tendency": "居家", "items": []},
            "daily_outfit": {"name": "米色家居服"},
        }
        self.prepared = {
            "context": NS(scope=NS(platform="qq-main")),
            "native_input": NS(text="来张自拍看看", at=datetime(2026, 10, 20, 16, 5, tzinfo=timezone.utc)),
            "perception_context": json.dumps({"current_local_time": {"timezone": "America/Los_Angeles"}}),
        }
        self.state = {"prepared": self.prepared}
        self.application = NS(runtime=object(), tasks=NS(now=Mock()), messages=NS(
            reference_materials=self.references, timezone_name="Asia/Shanghai"))
        self.session = NS(application=self.application, context=self.prepared["context"],
                          prepared=self.prepared, state=self.state)
        self.offered = tuple(NS(spec=NS(action=action)) for action in ("memory.query", "image.generate", "image.edit"))
        self.request = ProviderRequest(prompt="来张自拍看看", system_prompt="当前 AstrBot 人格。",
            extra_user_content_parts=[TextPart(text="其他插件的材料")])
        self.event = NS(get_extra=lambda key, default=None:
            self.state if key == CORE_REPLY_CONTEXT_EXTRA else default)

    def inject(self, offered=None, values=None):
        inject_core_image_prompt(self.session, self.request, offered or self.offered,
                                 values if values is not None else DEFAULTS)

    def payload(self):
        part = next(part for part in self.request.extra_user_content_parts
                    if part.text.startswith("<companion_v2_context>"))
        return json.loads(part.text.removeprefix("<companion_v2_context>\n")
                          .removesuffix("\n</companion_v2_context>"))

    def test_disabled_modes_hide_only_image_tools_in_the_request(self):
        for values in ({**DEFAULTS, "allow_user_requests": False}, {**DEFAULTS, "trigger_mode": "off"}):
            with patch("astrbot_host.core_reply_image_prompt.effective_settings", return_value=values):
                offered, preferences = core_image_tools(self.session, self.offered)
            self.assertEqual([item.spec.action for item in offered], ["memory.query"])
            self.assertIsNone(preferences)
        self.assertEqual(len(self.offered), 3)

    def test_without_image_tools_no_preferences_or_references_are_read(self):
        with patch("astrbot_host.core_reply_image_prompt.effective_settings") as settings:
            offered, values = core_image_tools(self.session, self.offered[:1])
        self.assertEqual(offered, self.offered[:1])
        self.assertIsNone(values)
        settings.assert_not_called()
        self.references.prompt_material.assert_not_called()

    def test_group_switch_and_core_only_permission_hide_image_tools_at_request_time(self):
        group_context = NS(scope=NS(kind=ScopeKind.GROUP, platform="qq-main"),
                           actor=NS(subject=NS(kind=SubjectKind.HUMAN, subject_id="standard")))
        session = NS(application=self.application, context=group_context,
                     prepared=self.prepared, state=self.state)

        group_off = {**DEFAULTS, "group_enabled": False}
        with patch("astrbot_host.core_reply_image_prompt.effective_settings", return_value=group_off):
            offered, preferences = core_image_tools(session, self.offered)
        self.assertEqual([item.spec.action for item in offered], ["memory.query"])
        self.assertIsNone(preferences)

        self.application.runtime = Mock()
        self.application.runtime.read_one.return_value = None
        core_only = {**DEFAULTS, "request_permission": "core_only"}
        with patch("astrbot_host.core_reply_image_prompt.effective_settings", return_value=core_only):
            offered, preferences = core_image_tools(session, self.offered)
        self.assertEqual([item.spec.action for item in offered], ["memory.query"])
        self.assertIsNone(preferences)

        self.application.runtime.read_one.return_value = {"tier": "core"}
        with patch("astrbot_host.core_reply_image_prompt.effective_settings", return_value=core_only):
            offered, preferences = core_image_tools(session, self.offered)
        self.assertEqual([item.spec.action for item in offered],
                         ["memory.query", "image.generate", "image.edit"])
        self.assertEqual(preferences, core_only)

    def test_rules_mode_keeps_image_tools_and_only_hints_matching_turns(self):
        values = {**DEFAULTS, "trigger_mode": "rules"}
        self.prepared["native_input"] = NS(text="今天天气不错",
                                           at=self.prepared["native_input"].at)
        with patch("astrbot_host.core_reply_image_prompt.effective_settings", return_value=values):
            offered, preferences = core_image_tools(self.session, self.offered)
        self.assertEqual([item.spec.action for item in offered],
                         ["memory.query", "image.generate", "image.edit"])
        self.assertEqual(preferences, values)
        self.inject(offered=offered, values=preferences)
        self.assertNotIn("已命中图片请求规则", self.request.system_prompt)
        self.assertNotIn("已命中图片请求规则", self.state["_turn_notes"]["image"])
        stable_system = self.request.system_prompt

        self.prepared["native_input"] = NS(text="帮我改一下", at=self.prepared["native_input"].at,
                                           images=(object(),))
        with patch("astrbot_host.core_reply_image_prompt.effective_settings", return_value=values):
            offered, preferences = core_image_tools(self.session, self.offered)
        self.assertEqual([item.spec.action for item in offered],
                         ["memory.query", "image.generate", "image.edit"])
        self.assertEqual(preferences, values)
        self.inject(offered=offered, values=preferences)
        self.assertIn("已命中图片请求规则", self.state["_turn_notes"]["image"])
        # The per-turn hint rides the user message; the cached system prefix
        # must stay byte-identical between an ordinary and an image turn.
        self.assertEqual(self.request.system_prompt, stable_system)

    def test_tool_mode_keeps_image_tools_available_on_non_image_turns(self):
        values = {**DEFAULTS, "trigger_mode": "tool"}
        self.prepared["native_input"] = NS(text="今天天气不错",
                                           at=self.prepared["native_input"].at)
        with patch("astrbot_host.core_reply_image_prompt.effective_settings", return_value=values):
            offered, preferences = core_image_tools(self.session, self.offered)
        self.assertEqual([item.spec.action for item in offered],
                         ["memory.query", "image.generate", "image.edit"])
        self.assertEqual(preferences, values)

    def test_rules_mode_allows_an_explicit_image_functional_command(self):
        values = {**DEFAULTS, "trigger_mode": "rules"}
        self.state["functional_command"] = NS(intent="image_generate")
        self.prepared["native_input"] = NS(text="今天天气不错",
                                           at=self.prepared["native_input"].at)
        with patch("astrbot_host.core_reply_image_prompt.effective_settings", return_value=values), \
                patch("astrbot_host.core_reply_image_prompt.is_rule_image_request",
                      return_value=False) as detector:
            offered, preferences = core_image_tools(self.session, self.offered)
        self.assertEqual([item.spec.action for item in offered],
                         ["memory.query", "image.generate", "image.edit"])
        self.assertEqual(preferences, values)
        detector.assert_not_called()

    def test_rules_mode_keeps_image_tools_for_a_clear_request(self):
        values = {**DEFAULTS, "trigger_mode": "rules"}
        with patch("astrbot_host.core_reply_image_prompt.effective_settings", return_value=values):
            offered, preferences = core_image_tools(self.session, self.offered)
        self.assertEqual([item.spec.action for item in offered],
                         ["memory.query", "image.generate", "image.edit"])
        self.assertEqual(preferences, values)
        self.inject(values=values)
        self.assertIn("已命中图片请求规则", self.state["_turn_notes"]["image"])
        self.assertNotIn("已命中图片请求规则", self.request.system_prompt)

    def test_unavailable_image_preferences_preserve_other_tools(self):
        with patch("astrbot_host.core_reply_image_prompt.effective_settings", side_effect=TaskError("unavailable", "settings unavailable")):
            offered, values = core_image_tools(self.session, self.offered)
        self.assertEqual([item.spec.action for item in offered], ["memory.query"])
        self.assertIsNone(values)

    def test_native_tool_names_and_actual_delivery_step_are_explained(self):
        self.inject(values={**DEFAULTS, "prompt_format": "natural"})
        prompt = self.request.system_prompt
        self.assertIn(native_tool_name("image.generate"), prompt)
        self.assertIn(native_tool_name("image.edit"), prompt)
        self.assertNotIn(native_tool_name("image.mask_edit"), prompt)
        self.assertIn("来张自拍看看", prompt)
        self.assertIn("连贯的自然语言", prompt)
        self.assertIn("send_message_to_user", prompt)
        self.assertIn('"type":"image"', prompt)
        self.assertIn("session 留空", prompt)
        self.assertIn('reference_indices=[]', prompt)
        self.assertIn("host_image_limit", prompt)
        self.assertIn("不是服务商日额度错误", prompt)
        self.assertNotIn("role-face", prompt)

    def test_identity_reference_guidance_prefers_edit_and_avoids_fixed_appearance_repetition(self):
        self.inject()
        prompt = self.request.system_prompt
        self.assertIn("优先调用 image.edit", prompt)
        self.assertIn("reference_indices=[]", prompt)
        self.assertIn('reference_roles=["identity"]', prompt)
        self.assertIn("不要重复发色、眼睛、脸型、发饰", prompt)

    def test_without_identity_reference_does_not_add_identity_route_guidance(self):
        self.references.prompt_material.return_value = {
            "references": [{"id": "outfit", "roles": ["wardrobe"]}],
            "wardrobe": {"tendency": "居家", "items": []},
        }
        self.inject()
        self.assertNotIn("当前参考材料有可用的 identity 身份参考", self.request.system_prompt)

    def test_active_reference_limit_is_explained_in_prompt_and_request_tool_schema(self):
        settings = {
            "enabled": True,
            "operation_routes": {"edit": ["online:primary"]},
            "endpoints": [{"id": "primary", "enabled": True,
                "base_url": "https://image.example", "platform": "openai",
                "max_reference_images": 1}],
        }
        api = NS(output_configuration=lambda: {"settings": settings})
        self.application.native_tool_port = NS(image_extension_api=lambda: api)

        self.inject()

        self.assertIn("最多接收 1 张参考图", self.request.system_prompt)
        description, parameters = core_image_tool_contract("image.edit", "", {
            "properties": {"reference_ids": {"description": "已保存参考素材 ID"}},
        }, reference_limit=1)
        second_description, second_parameters = core_image_tool_contract("image.edit", "", {
            "properties": {"reference_ids": {"description": "已保存参考素材 ID"}},
        }, reference_limit=4)
        self.assertNotIn("最多接收", description)
        self.assertEqual(description, second_description)
        self.assertEqual(parameters, second_parameters)

    async def test_reference_changes_only_update_temporary_user_context(self):
        self.inject()
        first_system = self.request.system_prompt
        self.references.prompt_material.assert_called_once_with(self.session.context, local_date="2026-10-20")
        prepare_native_host_request(NS(), self.event, self.request)
        await inject_core_reply_perception_context(self.event, self.request)
        self.assertEqual(self.payload()["image_reference_material"]["daily_outfit"]["name"], "米色家居服")
        self.references.prompt_material.return_value = {
            "references": [{"id": "new-face", "roles": ["identity"]}],
            "wardrobe": None, "daily_outfit": {"name": "蓝色衬衫"},
        }
        self.inject()
        await inject_core_reply_perception_context(self.event, self.request)
        self.assertEqual(self.request.system_prompt, first_system)
        self.assertEqual(self.payload()["image_reference_material"]["daily_outfit"]["name"], "蓝色衬衫")
        self.assertNotIn("new-face", self.request.system_prompt)
        self.assertEqual(len(self.request.extra_user_content_parts), 2)
        self.assertEqual(self.request.extra_user_content_parts[0].text, "其他插件的材料")
        self.assertTrue(self.state["_perception_material_injected"])
        content = [TextPart(text="来张自拍看看"), *self.request.extra_user_content_parts]
        saved = dump_messages_with_checkpoints([Message(role="user", content=content)])
        self.assertNotIn("new-face", json.dumps(saved, ensure_ascii=False))
        self.assertIn("来张自拍看看", json.dumps(saved, ensure_ascii=False))

    def test_generate_only_provider_does_not_offer_unavailable_edit_or_reference_tools(self):
        self.inject(offered=self.offered[:2])
        self.assertIn(native_tool_name("image.generate"), self.request.system_prompt)
        self.assertNotIn(native_tool_name("image.edit"), self.request.system_prompt)
        self.assertNotIn("reference_ids", self.request.system_prompt)
        self.references.prompt_material.assert_not_called()

    def test_optional_reference_failure_keeps_natural_image_generation(self):
        self.references.prompt_material.side_effect = TaskError("unavailable", "reference unavailable")
        self.inject()
        self.assertIn(native_tool_name("image.generate"), self.request.system_prompt)
        self.assertNotIn("_image_reference_material", self.state)

    async def test_disabling_images_removes_only_owned_instruction_and_reference_suffix(self):
        self.inject()
        await inject_core_reply_perception_context(self.event, self.request)
        inject_core_image_prompt(self.session, self.request, self.offered[:1], None)
        await inject_core_reply_perception_context(self.event, self.request)
        self.assertEqual(self.request.system_prompt, "当前 AstrBot 人格。")
        self.assertNotIn("image_reference_material", self.payload())
        self.assertEqual(self.request.extra_user_content_parts[0].text, "其他插件的材料")


if __name__ == "__main__":
    unittest.main()
