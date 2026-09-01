from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from astrbot_plugin_private_companion.astrbot_knowledge import AstrBotKnowledgeMixin
from astrbot_plugin_private_companion.conversation_prompt_section import (
    prompt_section,
    render_prompt_sections,
)
from astrbot_plugin_private_companion.integration_status import IntegrationStatusMixin
from astrbot_plugin_private_companion.llm_tool_actions import LlmToolActionsMixin
from astrbot_plugin_private_companion.main import PrivateCompanionPlugin
from astrbot_plugin_private_companion.passive_state_pipeline import (
    _persona_core_emphasis_prompt_section,
)
from astrbot_plugin_private_companion.platform_compat import PlatformCompatibilityMixin
from astrbot_plugin_private_companion.private_image import PrivateImageMixin
from astrbot_plugin_private_companion.qzone_publish import QzonePublishMixin
from astrbot_plugin_private_companion.reading_archive import ReadingArchiveMixin


LEGACY_HEADINGS = (
    "【人格核心强调】",
    "【QQ 官方机器人平台边界】",
    "【群聊人格降噪】",
    "【群聊玩笑边界】",
    "【长期记忆检索】",
    "【书柜夹层】",
    "【AstrBot 知识库世界观参考】",
    "【QQ 空间动态工具】",
    "【Bot 自己最近成功发布的 QQ 空间记录】",
    "【用户刚刚在群里的近况】",
)


class _PlatformHarness(PlatformCompatibilityMixin):
    @staticmethod
    def _platform_profile(**_kwargs):
        return {"kind": "qq_official"}


class _LivingMemoryHarness(IntegrationStatusMixin):
    enable_livingmemory_integration = True
    livingmemory_tool_name = "recall_long_term_memory"

    @staticmethod
    def _livingmemory_available() -> bool:
        return True


class _WorldviewHarness(IntegrationStatusMixin):
    def persona_setting(self, key, default=None):
        values = {
            "worldview_adaptation_mode": "modern",
            "worldview_adaptation_prompt": "",
            "schedule_persona_prompt": "",
            "schedule_worldview_prompt": "",
            "bot_name": "测试人格",
        }
        return values.get(key, default)

    @staticmethod
    def _format_roleplay_knowledge_context(**_kwargs) -> str:
        return "【AstrBot 知识库世界观参考】\n世界资料"

    @staticmethod
    def _format_roleplay_knowledge_context_section(**_kwargs):
        return prompt_section("AstrBot 知识库世界观参考", "世界资料")


class _KnowledgeHarness(AstrBotKnowledgeMixin):
    roleplay_knowledge_source_ids = ["kb:world"]

    @staticmethod
    def _astrbot_knowledge_sources():
        return [
            {
                "kb_id": "world",
                "name": "世界",
                "documents": [{"doc_id": "doc", "id": "doc:world:doc", "name": "设定"}],
            }
        ]

    @staticmethod
    def _read_roleplay_knowledge_chunks(*_args, **_kwargs):
        return [{"kb_doc_id": "doc", "text": "天空城漂浮在云层上。"}]


class _QzoneHarness(LlmToolActionsMixin, QzonePublishMixin):
    enabled = True
    enable_qzone_integration = True

    def __init__(self):
        self.data = {
            "qzone_integration": {
                "recent_life_publish_texts": [{"text": "今天看了晚霞", "image_count": 1}]
            }
        }


class _BookshelfHarness(ReadingArchiveMixin):
    data = {"bookshelf_secret": {"password": "2468", "basis": "manual"}}

    @staticmethod
    def _bookshelf_secret_signal_info(_text):
        return {"likely": True}

    @staticmethod
    def _private_user_role(_user, *_args):
        return "owner"

    @staticmethod
    async def _ensure_bookshelf_password_async() -> str:
        return "2468"


class _PrivateImageHarness(PrivateImageMixin):
    def _get_user(self, _user_id):
        return {
            "recent_group_messages": [
                {"ts": 1_000.0, "group_id": "group-1", "text": "刚才在群里说的话"}
            ]
        }

    @staticmethod
    def _format_elapsed(_seconds):
        return "1分钟"


class ConversationPromptStructureSupplementTests(unittest.IsolatedAsyncioTestCase):
    def assert_no_legacy_headings(self, rendered: str) -> None:
        for heading in LEGACY_HEADINGS:
            self.assertNotIn(heading, rendered)

    def test_persona_core_is_named_section_without_nested_heading(self) -> None:
        rendered = render_prompt_sections([_persona_core_emphasis_prompt_section()])

        self.assertIn('<section title="人格核心强调">', rendered)
        self.assertNotIn('<section title="提示词片段">', rendered)
        self.assert_no_legacy_headings(rendered)

    def test_platform_boundary_keeps_legacy_text_and_structures_main_chain(self) -> None:
        harness = _PlatformHarness()

        self.assertTrue(harness._platform_capability_prompt(None).startswith("【QQ 官方机器人平台边界】\n"))
        rendered = render_prompt_sections(
            [
                prompt_section("能力边界", "通用能力约束"),
                harness._platform_capability_prompt_section(None),
            ]
        )

        self.assertIn('<section title="QQ 官方机器人平台边界">', rendered)
        self.assert_no_legacy_headings(rendered)

    def test_group_denoise_splits_joke_boundary_and_preserves_legacy_output(self) -> None:
        plugin = object.__new__(PrivateCompanionPlugin)
        plugin.enable_group_persona_denoise = True
        plugin.data = {"users": {}}
        plugin._sender_display_name = lambda _event: "群友"
        plugin._private_user_id_for_event = lambda _event, sender_id: sender_id
        plugin._is_target_private_user = lambda *_args, **_kwargs: False
        event = SimpleNamespace(
            get_sender_id=lambda: "member-1",
            private_companion_group_scene={},
            private_companion_group_high_intensity=None,
        )

        legacy = plugin._format_group_persona_denoise_prompt(event)
        rendered = render_prompt_sections(
            plugin._format_group_persona_denoise_prompt_sections(event)
        )

        self.assertTrue(legacy.startswith("【群聊人格降噪】\n"))
        self.assertIn("【群聊玩笑边界】", legacy)
        self.assertIn('<section title="群聊人格降噪">', rendered)
        self.assertIn('<section title="群聊玩笑边界">', rendered)
        self.assert_no_legacy_headings(rendered)

    def test_livingmemory_splits_joke_boundary_and_preserves_legacy_output(self) -> None:
        harness = _LivingMemoryHarness()

        legacy = harness._format_livingmemory_guidance(scope="group")
        rendered = render_prompt_sections(
            harness._format_livingmemory_guidance_sections(scope="group")
        )

        self.assertTrue(legacy.startswith("【长期记忆检索】\n"))
        self.assertIn("【群聊玩笑边界】", legacy)
        self.assertIn('<section title="长期记忆检索">', rendered)
        self.assertIn('<section title="群聊玩笑边界">', rendered)
        self.assert_no_legacy_headings(rendered)

    async def test_bookshelf_uses_named_section_and_keeps_legacy_output(self) -> None:
        harness = _BookshelfHarness()

        legacy = await harness._format_bookshelf_secret_for_prompt("密码是多少", {})
        rendered = render_prompt_sections(
            [await harness._format_bookshelf_secret_prompt_section("密码是多少", {})]
        )

        self.assertTrue(legacy.startswith("【书柜夹层】\n"))
        self.assertIn('<section title="资料柜夹层">', rendered)
        self.assert_no_legacy_headings(rendered)

    def test_worldview_splits_knowledge_reference_and_preserves_legacy_output(self) -> None:
        harness = _WorldviewHarness()

        legacy = harness._format_worldview_adaptation_prompt()
        rendered = render_prompt_sections(
            harness._format_worldview_adaptation_prompt_sections()
        )

        self.assertTrue(legacy.startswith("【世界观适配】\n"))
        self.assertIn("【AstrBot 知识库世界观参考】", legacy)
        self.assertIn('<section title="世界观适配">', rendered)
        self.assertIn('<section title="AstrBot 知识库世界观参考">', rendered)
        self.assert_no_legacy_headings(rendered)

    def test_knowledge_context_keeps_legacy_heading_outside_structured_body(self) -> None:
        harness = _KnowledgeHarness()

        legacy = harness._format_roleplay_knowledge_context(purpose="worldview")
        rendered = render_prompt_sections(
            [harness._format_roleplay_knowledge_context_section(purpose="worldview")]
        )

        self.assertTrue(legacy.startswith("【AstrBot 知识库世界观参考】\n"))
        self.assertIn("天空城漂浮在云层上", rendered)
        self.assert_no_legacy_headings(rendered)

    def test_qzone_splits_recent_posts_and_preserves_legacy_output(self) -> None:
        harness = _QzoneHarness()

        legacy = harness._qzone_tool_instruction()
        rendered = render_prompt_sections(harness._qzone_tool_prompt_sections())

        self.assertTrue(legacy.startswith("【QQ 空间动态工具】\n"))
        self.assertIn("【Bot 自己最近成功发布的 QQ 空间记录】", legacy)
        self.assertIn('<section title="QQ 空间动态工具">', rendered)
        self.assertIn('<section title="Bot 自己最近成功发布的 QQ 空间记录">', rendered)
        self.assert_no_legacy_headings(rendered)

    def test_private_image_recent_group_context_is_a_sibling_section(self) -> None:
        harness = _PrivateImageHarness()

        with patch(
            "astrbot_plugin_private_companion.private_image._now_ts",
            return_value=1_060.0,
        ):
            legacy = harness._format_recent_group_messages_for_private_image_prompt("user-1")
            rendered = render_prompt_sections(
                [
                    prompt_section("本轮图片回复边界", "优先回应当前图片。"),
                    harness._format_recent_group_messages_for_private_image_prompt_section("user-1"),
                ]
            )

        self.assertTrue(legacy.startswith("【用户刚刚在群里的近况】\n"))
        self.assertIn('<section title="本轮图片回复边界">', rendered)
        self.assertIn('<section title="用户刚刚在群里的近况">', rendered)
        self.assert_no_legacy_headings(rendered)

    def test_user_bracket_text_is_preserved(self) -> None:
        rendered = render_prompt_sections(
            [prompt_section("引用内容", "用户原话是【这个括号要保留】")]
        )

        self.assertIn("【这个括号要保留】", rendered)


if __name__ == "__main__":
    unittest.main()
