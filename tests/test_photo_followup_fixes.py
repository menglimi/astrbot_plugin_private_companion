# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import time
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from astrbot.api.star import Context
from astrbot_plugin_private_companion.daily_state import DailyStateMixin
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi
from astrbot_plugin_private_companion.private_image import PrivateImageMixin
from astrbot_plugin_private_companion.proactive_engine import ProactiveEngineMixin
from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin


class _PhotoActionHarness(ProactiveMessageMixin):
    def __init__(self, reference_path: str = "C:/reference/persona.png") -> None:
        self.enable_photo_text_action = True
        self._data_lock = asyncio.Lock()
        self.reference_received = ""
        self.prompt_format_received = ""
        self.reference_path = reference_path

    def _private_user_role(self, _user) -> str:
        return "owner"

    def _photo_text_load_defer_note(self, *_args, **_kwargs) -> str:
        return ""

    def _photo_text_available(self, *_args, **_kwargs) -> bool:
        return True

    async def _build_photo_scene_prompt(self, *_args, **_kwargs):
        return {
            "kind": "text2img",
            "prompt": "single anime girl writing at a desk",
            "caption": "我坐在窗边书桌前认真写字。",
            "use_persona_reference": True,
            "prompt_format": "nai",
        }

    async def _photo_persona_reference_image_for_kind_async(
        self, *_args, **_kwargs
    ) -> str:
        return self.reference_path

    async def _generate_photo_image(self, **kwargs):
        self.reference_received = kwargs.get("reference_image_path", "")
        self.prompt_format_received = kwargs.get("prompt_format", "")
        return "在线图片 API", "C:/generated/result.png", "ok"

    def _note_photo_generation_attempt(self, *_args, **_kwargs) -> None:
        return None

    def _save_data_sync(self, **_kwargs) -> None:
        return None


class _SnapshotHarness(DailyStateMixin):
    pass


class _FakeToolSet:
    def __init__(self) -> None:
        self.tools = [SimpleNamespace(name="AIsearch"), SimpleNamespace(name="safe_tool")]

    def remove_tool(self, name: str) -> None:
        self.tools = [tool for tool in self.tools if tool.name != name]


class _FrameworkHarness(ProactiveMessageMixin):
    def __init__(self) -> None:
        self.context = object.__new__(Context)


class _FrameworkAgentHarness(ProactiveMessageMixin):
    def __init__(self, context) -> None:
        self.context = context
        self._framework_captured_send_cache = {}
        self._framework_deferred_photo_cache = {}
        self.captured_tool_sends = []

    async def _get_current_conversation_safely(self, *_args, **_kwargs):
        return None

    async def _capture_framework_send_message_calls(self, *, runner_factory, **_kwargs):
        return await runner_factory(), list(self.captured_tool_sends)


class _FrameworkRunner:
    def __init__(self, completion_text: str = "原生上下文主链正常") -> None:
        self.completion_text = completion_text

    def get_final_llm_resp(self):
        return SimpleNamespace(completion_text=self.completion_text)


class _DeferredPhotoGenerationHarness(ProactiveMessageMixin):
    def __init__(self) -> None:
        self.enable_llm_proactive_message = True
        self._framework_deferred_photo_cache = {}
        self.direct_fallback_calls = 0

    def _clear_proactive_reaction_intent(self, _umo: str) -> None:
        return None

    async def _generate_proactive_message_via_framework(self, user, *_args, **_kwargs):
        self._framework_deferred_photo_cache[str(user.get("umo") or "")] = {
            "path": "C:/generated/image.png",
            "caption": "",
            "intent_kind": "sticker",
        }
        return ""

    async def _generate_proactive_message_direct_fallback(self, *_args, **_kwargs):
        self.direct_fallback_calls += 1
        return "不应该出现的生成成功回执"


class _DeferredPhotoRenderHarness(ProactiveEngineMixin, ProactiveMessageMixin):
    def __init__(self, image_path: str) -> None:
        self.default_nickname = "测试角色"
        self.image_path = image_path
        self._framework_captured_send_cache = {}
        self._framework_deferred_photo_cache = {}

    def _has_due_llm_timer(self, _user) -> bool:
        return False

    def _is_reason_allowed_now(self, _reason: str, user=None) -> bool:
        return True

    def _should_use_name_only_opener(self, *_args, **_kwargs) -> bool:
        return False

    async def _execute_proactive_action(self, *_args, **_kwargs):
        return {
            "success": True,
            "context": "message：准备分享一件小事",
            "extra_components": [],
            "summary": "分享一件小事",
            "effective_action": "message",
        }

    async def _narrate_action_context(self, _action: str, context: str) -> str:
        return context

    async def _generate_proactive_message_with_llm(self, user, *_args, **_kwargs):
        self._framework_deferred_photo_cache[str(user.get("umo") or "")] = {
            "path": self.image_path,
            "caption": "窗边的光正好，拍给你看看。",
            "intent_kind": "selfie",
        }
        return ""

    async def _maybe_run_pre_message_poke(self, *_args, **_kwargs):
        return 0, ""


class _PrivateImageHarness(PrivateImageMixin):
    pass


class PhotoFollowupFixTests(unittest.IsolatedAsyncioTestCase):
    def _optional_image_download_target(self, value: str):
        try:
            image_runtime = importlib.import_module(
                "astrbot_plugin_image_companion.image_runtime"
            )
        except ImportError:
            self.skipTest("optional Image Companion runtime is not installed")
        runtime_type = image_runtime.ProactiveMessageMixin
        return runtime_type._external_image_download_target(
            object.__new__(runtime_type),
            value,
        )

    def test_reference_metadata_accepts_confirmed_submission_without_pre_resolved_path(self) -> None:
        self.assertTrue(
            PrivateCompanionPageApi._image_generation_result_used_reference(
                workflow_kind="selfie",
                image_path="generated.png",
                image_exists=True,
                note="ok；已提交参考图；已使用云智",
            )
        )
        self.assertFalse(
            PrivateCompanionPageApi._image_generation_result_used_reference(
                workflow_kind="selfie",
                image_path="generated.png",
                image_exists=True,
                note="ok；已使用云智",
            )
        )

    def test_signed_image_download_url_keeps_original_encoding(self) -> None:
        signed_url = (
            "https://aoss.example/image/a%2Fb.png?"
            "X-Amz-Credential=AK%2F20260711%2Fcn-sh-01%2Fs3%2Faws4_request&"
            "X-Amz-Signature=abc%2Bdef%2F123"
        )
        request_target, preserved = self._optional_image_download_target(signed_url)
        self.assertTrue(preserved)
        self.assertEqual(str(request_target), signed_url)

    def test_plain_image_download_url_stays_a_string(self) -> None:
        image_url = "https://cdn.example/image.png?width=1024"
        request_target, preserved = self._optional_image_download_target(image_url)
        self.assertFalse(preserved)
        self.assertIsInstance(request_target, str)
        self.assertEqual(request_target, image_url)

    def test_daily_outfit_weather_prompt_reads_cached_weather(self) -> None:
        harness = _PhotoActionHarness()
        harness.data = {
            "daily_weather": {
                "prompt": "今天有阵雨，气温 18 到 23 度",
                "source": "private_companion",
            }
        }
        self.assertEqual(
            harness._format_weather_for_prompt(),
            "今天有阵雨，气温 18 到 23 度",
        )

    def test_daily_outfit_rotation_does_not_request_two_visible_outfits(self) -> None:
        harness = _PhotoActionHarness()
        harness.data = {"daily_state": {}}
        harness.daily_outfit_photo_prompt = ""
        harness._daily_outfit_role_appearance_text = lambda: "silver hair, green eyes"
        harness._get_photo_style_instruction = lambda: ("真实", "")
        harness._photo_style_prompt_en = lambda *_args: "realistic photography"
        harness._format_weather_for_prompt = lambda: "cool weather"
        harness._daily_outfit_schedule_text = lambda: "at home"
        harness._daily_outfit_visual_state_text = lambda _state: "relaxed"
        harness._normalize_daily_outfit_profile = lambda profile: profile or {}
        harness._daily_outfit_outfit_hint = lambda **_kwargs: "light jacket and skirt"
        harness._daily_outfit_rotation_reference = lambda: "blue jacket"
        harness._daily_outfit_scene_hint = lambda *_args, **_kwargs: "bedroom window"
        prompt = harness._build_daily_outfit_photo_prompt({}, outfit_profile={"palette": "gray"})
        self.assertIn("exactly one character wearing one coherent new outfit", prompt)
        self.assertIn("multiple outfits", prompt)
        self.assertIn("side-by-side panels", prompt)
        self.assertNotIn("make at least two visible outfit changes", prompt)

    def test_daily_outfit_structured_contract_is_fixed_and_field_bounded(self) -> None:
        harness = _PhotoActionHarness()
        harness.data = {"daily_state": {}}
        harness.daily_outfit_photo_prompt = ""
        harness._daily_outfit_role_appearance_text = lambda: "silver hair, green eyes"
        harness._get_photo_style_instruction = lambda: ("二次元", "")
        harness._photo_style_prompt_en = lambda *_args: "anime illustration"
        harness._format_weather_for_prompt = lambda: "cool windy weather"
        harness._daily_outfit_schedule_text = lambda: "school and evening commute"
        harness._daily_outfit_visual_state_text = lambda _state: "bright expression"
        harness._normalize_daily_outfit_profile = lambda profile: profile or {}
        harness._daily_outfit_outfit_hint = lambda **_kwargs: "layered school outfit"
        harness._daily_outfit_rotation_reference = lambda: "blue jacket and black skirt"
        harness._daily_outfit_scene_hint = lambda *_args, **_kwargs: "classroom window"

        sections = harness._build_daily_outfit_photo_prompt(
            {},
            memory_context=("发色银白；绿色眼睛；教室窗边；自然微笑；" * 20),
            outfit_profile={"palette": "gray"},
            structured=True,
        )

        by_name = {section.name: section for section in sections}
        self.assertEqual(by_name["daily_outfit_contract"].source, "fixed_prompt")
        self.assertTrue(by_name["daily_outfit_contract"].protected)
        self.assertLessEqual(len(by_name["user_request"].positive), 1400)
        self.assertLessEqual(len(by_name["daily_outfit_contract"].negative), 760)
        visual_chars = sum(
            len(section.positive) + len(section.negative)
            for section in sections
            if section.source not in {"user_request", "fixed_prompt"}
        )
        self.assertLessEqual(visual_chars, 500)

    def test_anime_daily_outfit_prompt_does_not_inject_real_person_style(self) -> None:
        harness = _PhotoActionHarness()
        harness.data = {"daily_state": {}}
        harness.daily_outfit_photo_prompt = ""
        harness._daily_outfit_role_appearance_text = lambda: "blue-purple short hair, violet eyes"
        harness._get_photo_style_instruction = lambda: ("二次元", "日系二次元插画风")
        harness._format_weather_for_prompt = lambda: "cool weather"
        harness._daily_outfit_schedule_text = lambda: "at home"
        harness._daily_outfit_visual_state_text = lambda _state: "relaxed"
        harness._normalize_daily_outfit_profile = lambda profile: profile or {}
        harness._daily_outfit_outfit_hint = lambda **_kwargs: "blue and white casual outfit"
        harness._daily_outfit_rotation_reference = lambda: ""
        harness._daily_outfit_scene_hint = lambda *_args, **_kwargs: "home interior"

        prompt = harness._build_daily_outfit_photo_prompt(
            {},
            outfit_profile={"palette": "blue and white"},
        )
        positive, negative = harness._photo_prompt_split_formatted(prompt)
        composition, _ = harness._photo_generation_composition_sections("selfie", positive)

        self.assertIn("2D anime illustration style", positive)
        self.assertIn("selfie-inspired outfit portrait composition", positive)
        self.assertNotIn("natural phone snapshot", positive)
        self.assertNotIn("lifelike daily atmosphere", positive)
        self.assertNotIn("realistic photography", positive)
        self.assertIn("photorealistic", negative)
        self.assertIn("real person", negative)
        self.assertIn("live-action", negative)
        self.assertFalse(harness._photo_generation_explicit_mirror_request(positive))
        self.assertNotIn("mirror reflection", composition)
        self.assertNotIn("real character", composition)

        self.assertTrue(harness._photo_generation_explicit_mirror_request("请生成一张对镜自拍"))
        mirror_composition, _ = harness._photo_generation_composition_sections(
            "selfie",
            "请生成一张对镜自拍",
        )
        self.assertIn("mirror reflection", mirror_composition)
        self.assertNotIn("real character", mirror_composition)

    def test_selfie_guard_blocks_comparison_layouts(self) -> None:
        harness = _FrameworkHarness()
        prompt = harness._apply_photo_generation_selfie_composition_guard(
            "Positive prompt: daily outfit portrait.",
            "selfie",
        )
        self.assertIn("exactly one character wearing exactly one coherent outfit", prompt)
        self.assertIn("split screen", prompt)
        self.assertIn("side-by-side panels", prompt)
        self.assertIn("multiple outfits", prompt)

    def test_explicit_back_view_selfie_guard_does_not_require_a_visible_face(self) -> None:
        harness = _FrameworkHarness()
        composition, negative = harness._photo_generation_composition_sections(
            "selfie",
            "测试角色站在阳台上背对镜头看晚霞",
        )
        guarded = harness._apply_photo_generation_selfie_composition_guard(
            "测试角色站在阳台上背对镜头看晚霞",
            "selfie",
        )

        self.assertIn("Back-view character composition", composition)
        self.assertIn("without requiring the face to be visible", composition)
        self.assertNotIn("back view", negative)
        self.assertIn("explicitly requested back-view pose is allowed", guarded)
        self.assertNotIn("keep the face visible", guarded)

    def test_image_only_reply_prefers_conversation_over_description(self) -> None:
        harness = _PrivateImageHarness()
        objective = harness._private_image_reply_objective(
            "图像归属判断：无法判断",
            vision_text="图片类型：照片；可见内容：两个人物穿着不同外套",
        )
        self.assertIn("自然评价、接梗", objective)
        self.assertIn("最多顺带提一个", objective)
        self.assertIn("不要输出看图报告", objective)

        content_objective = harness._private_image_reply_objective(
            "图像归属判断：无法判断",
            vision_text="图片类型：照片；可见内容：两个人物穿着不同外套",
            user_text="图里有几个人？",
        )
        self.assertIn("用户在问图片内容", content_objective)

    def test_private_image_framework_reuses_native_context_resolver(self) -> None:
        harness = _PrivateImageHarness()
        native_context = object.__new__(Context)
        harness.context = SimpleNamespace(context_obj=native_context)
        harness._proactive_framework_context = lambda: native_context

        self.assertIs(harness._private_image_framework_context(), native_context)

    async def test_character_text2img_receives_persona_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            reference = Path(temp_dir) / "persona.png"
            reference.write_bytes(b"png")
            harness = _PhotoActionHarness(str(reference))
            result = await harness._run_photo_text_action(
                {"user_id": "10001", "umo": "default:FriendMessage:10001"},
                "主人",
                "quiet_care",
            )
        self.assertEqual(harness.reference_received, str(reference.resolve()))
        self.assertEqual(harness.prompt_format_received, "nai")
        self.assertIn("人物参考图：已使用", result)
        self.assertIn("图片主体归属：bot", result)

    def test_recent_photo_snapshot_answers_short_followup(self) -> None:
        harness = _SnapshotHarness()
        user: dict = {}
        harness._remember_recent_photo_share_snapshot(
            user,
            caption="我坐在窗边书桌前认真写字。",
            topic="安静写字的午前",
            motive="想把这一小段拍下来分享",
            reason="quiet_care",
            subject_owner="bot",
            sent_at=time.time(),
        )
        context = harness._format_recent_photo_share_snapshot_for_reply(user, "？")
        self.assertIn("最近一次真实图片分享", context)
        self.assertIn("窗边书桌前认真写字", context)
        self.assertIn("不要用旧梦境", context)
        self.assertIn("用户的短句通常是在评价图中画面", context)
        self.assertIn("不得把画面事故反过来责怪用户", context)
        self.assertIn("画面主体归属：Bot/当前人格", context)
        self.assertEqual(user["last_photo_share_snapshot"]["subject_owner"], "bot")

    def test_scene_photo_keeps_non_user_scene_ownership(self) -> None:
        harness = _SnapshotHarness()
        user: dict = {}
        harness._remember_recent_photo_share_snapshot(
            user,
            caption="玻璃杯里的淡紫色液体沿桌面洒开。",
            subject_owner="scene",
            sent_at=time.time(),
        )

        context = harness._format_recent_photo_share_snapshot_for_reply(user, "洒出来了")

        self.assertIn("物体、动物或环境主体", context)
        self.assertNotIn("描述中的“我/她/角色本人”", context)

    def test_framework_uses_real_context_and_filters_request_tools(self) -> None:
        harness = _FrameworkHarness()
        event = harness._proactive_synthetic_event(
            "default:FriendMessage:10001",
            prompt="主动消息测试",
            name="测试角色",
        )
        self.assertIsNotNone(event)
        self.assertIs(event.context_obj, harness.context)
        req = SimpleNamespace(func_tool=_FakeToolSet())
        removed = harness._filter_incompatible_proactive_framework_tools(req)
        self.assertEqual(removed, ["AIsearch"])
        self.assertEqual([tool.name for tool in req.func_tool.tools], ["safe_tool"])

    async def test_framework_unwraps_legacy_proxy_before_building_main_agent(self) -> None:
        native_context = object.__new__(Context)
        native_context.get_config = lambda **_kwargs: {"provider_settings": {}}
        legacy_proxy = SimpleNamespace(context_obj=native_context)
        harness = _FrameworkAgentHarness(legacy_proxy)
        built = SimpleNamespace(agent_runner=_FrameworkRunner())

        with patch(
            "astrbot_plugin_private_companion.proactive_message.build_main_agent",
            new=AsyncMock(return_value=built),
        ) as mocked_build:
            text = await harness._run_framework_agent_text(
                umo="default:FriendMessage:10001",
                prompt="主动消息测试",
                name="测试角色",
                label="proactive_context_regression",
            )

        self.assertEqual(text, "原生上下文主链正常")
        self.assertIs(mocked_build.await_args.kwargs["plugin_context"], native_context)
        self.assertIs(mocked_build.await_args.kwargs["event"].context_obj, native_context)

    async def test_framework_discards_photo_receipt_and_caches_deferred_delivery(self) -> None:
        native_context = object.__new__(Context)
        native_context.get_config = lambda **_kwargs: {"provider_settings": {}}
        harness = _FrameworkAgentHarness(native_context)
        handle = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        handle.write(b"test-image")
        handle.close()
        try:
            async def build_with_deferred_photo(**kwargs):
                event = kwargs["event"]
                event._private_companion_photo_tool_deferred = True
                event._private_companion_photo_tool_deferred_path = handle.name
                event._private_companion_photo_tool_deferred_caption = "窗边的光正好，拍给你看看。"
                event._private_companion_photo_tool_deferred_intent_kind = "selfie"
                return SimpleNamespace(
                    agent_runner=_FrameworkRunner("图片生成成功，已经发给你了。")
                )

            harness.captured_tool_sends = [
                SimpleNamespace(messages=[{"type": "plain", "text": "图片已发送"}])
            ]

            with patch(
                "astrbot_plugin_private_companion.proactive_message.build_main_agent",
                new=AsyncMock(side_effect=build_with_deferred_photo),
            ):
                text = await harness._run_framework_agent_text(
                    umo="default:FriendMessage:10001",
                    prompt="主动消息测试",
                    name="测试角色",
                    label="proactive_photo_receipt_regression",
                )

            self.assertEqual(text, "窗边的光正好，拍给你看看。")
            payload = harness._pop_framework_deferred_photo_payload(
                "default:FriendMessage:10001"
            )
            self.assertEqual(payload["path"], handle.name)
            self.assertEqual(payload["caption"], "窗边的光正好，拍给你看看。")
            self.assertEqual(payload["intent_kind"], "selfie")
            self.assertNotIn("生成成功", text)
            self.assertFalse(harness._framework_captured_send_cache)
            self.assertFalse(harness._pop_framework_deferred_photo_payload("default:FriendMessage:10001"))
        finally:
            if os.path.exists(handle.name):
                os.unlink(handle.name)

    async def test_captionless_deferred_photo_does_not_trigger_text_fallback(self) -> None:
        harness = _DeferredPhotoGenerationHarness()
        user = {
            "user_id": "10001",
            "umo": "default:FriendMessage:10001",
        }

        text = await harness._generate_proactive_message_with_llm(
            user,
            "测试角色",
            "check_in",
        )

        self.assertEqual(text, "")
        self.assertEqual(harness.direct_fallback_calls, 0)
        self.assertIn(user["umo"], harness._framework_deferred_photo_cache)

    async def test_render_message_promotes_deferred_photo_into_normal_delivery(self) -> None:
        handle = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        handle.write(b"test-image")
        handle.close()
        try:
            harness = _DeferredPhotoRenderHarness(handle.name)
            user = {
                "user_id": "10001",
                "umo": "default:FriendMessage:10001",
                "nickname": "测试角色",
                "planned_proactive_reason": "check_in",
                "planned_proactive_action": "message",
                "planned_proactive_motive": "想分享窗边的光",
            }

            reason, text, image_path, components, summary, action = (
                await harness._render_message(user)
            )

            self.assertEqual(reason, "check_in")
            self.assertEqual(text, "窗边的光正好，拍给你看看。")
            self.assertEqual(image_path, handle.name)
            self.assertEqual(components, [])
            self.assertEqual(summary, "发图：窗边的光正好，拍给你看看。")
            self.assertEqual(action, "photo_text")
            self.assertEqual(user["_proactive_photo_subject_owner"], "bot")
            self.assertFalse(harness._framework_deferred_photo_cache)
        finally:
            if os.path.exists(handle.name):
                os.unlink(handle.name)

    def test_archive_keeps_photo_caption(self) -> None:
        harness = _FrameworkHarness()
        archived = harness._build_proactive_archive_assistant_text(
            text="",
            image_path="C:/generated/result.png",
            action_summary="发图：我坐在窗边书桌前认真写字。",
            photo_subject_owner="bot",
        )
        self.assertIn('<pc_history_media images="1" />', archived)
        self.assertNotIn("随消息发送了一张图片", archived)
        self.assertIn("图片画面：我坐在窗边书桌前认真写字", archived)
        self.assertIn("图片主体：Bot/当前人格", archived)


if __name__ == "__main__":
    unittest.main()
