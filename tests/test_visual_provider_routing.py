# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from astrbot_plugin_private_companion.forward_message import ForwardMessageMixin
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi
from astrbot_plugin_private_companion.private_image import PrivateImageMixin


class _FallbackRouteMixin:
    def __init__(self, mode: str, fallbacks: dict[str, str]) -> None:
        self.provider_config_mode = mode
        self.model_fallback_overrides = fallbacks

    def _model_fallback_provider_id(self, provider_key: str, primary_provider_id: str = "") -> str:
        fallback = str(self.model_fallback_overrides.get(provider_key) or "").strip()
        return fallback if fallback and fallback != str(primary_provider_id or "").strip() else ""


class _PrivateImageRouteHarness(_FallbackRouteMixin, PrivateImageMixin):
    def __init__(self, mode: str, fallbacks: dict[str, str]) -> None:
        super().__init__(mode, fallbacks)
        self.plugin_vision_provider_id = "quick-vision"
        self.narration_provider_id = "precision-vision"
        self.data: dict = {}
        self.timeout_call: dict = {}
        self.private_image_provider_timeout_seconds = 12
        self.private_image_provider_failure_cooldown_seconds = 0
        self.private_image_vision_wait_seconds = 30
        self.private_image_vision_provider_priority = "astrbot_first"
        self.astrbot_provider_settings: dict = {}

    def _astrbot_provider_settings_for_umo(self, umo: str = "") -> dict:
        return dict(self.astrbot_provider_settings)

    @staticmethod
    def _task_provider(*provider_ids: str) -> str:
        return next((str(value).strip() for value in provider_ids if str(value or "").strip()), "")

    def _model_timeout_seconds_for_call(self, **kwargs):
        self.timeout_call = dict(kwargs)
        return 17


class _FakeVisionProvider:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0
        self.requests: list[dict] = []

    async def text_chat(self, **kwargs):
        self.calls += 1
        self.requests.append(dict(kwargs))
        return SimpleNamespace(completion_text=self.text)


class _PrivateVisionPromptHarness(PrivateImageMixin):
    def __init__(self, completion: str = "图片类型：截图 可见内容：测试") -> None:
        self.provider = _FakeVisionProvider(completion)
        self.private_image_vision_custom_prompt = ""
        self.private_image_vision_max_chars = 2400
        self.enable_private_image_vision_cache = False
        self.cache_request: dict = {}

    async def _prepare_private_image_sources_for_model(self, sources, **_kwargs):
        return list(sources)

    @staticmethod
    def _private_image_model_image_items_with_meta(sources):
        return [(f"image-{index}", "data:image/png;base64,AA==") for index, _ in enumerate(sources)], len(sources), False

    @staticmethod
    def _private_image_cache_aliases_for_sources(_sources):
        return []

    @staticmethod
    def _private_image_cache_image_keys(sources):
        return [f"original-{index}" for index, _ in enumerate(sources)]

    @staticmethod
    def _private_image_visual_provider_candidates(_umo=""):
        return [("vision", "astrbot_image_caption", "AstrBot OCR instruction")]

    def _private_image_provider_by_id(self, provider_id):
        return self.provider if provider_id == "vision" else None

    @staticmethod
    def _provider_supports_image(_provider):
        return True

    @staticmethod
    def _private_image_provider_in_failure_cooldown(*_args):
        return False

    def _get_private_image_vision_cache(self, *_args, **kwargs):
        self.cache_request = dict(kwargs)
        return ""

    @staticmethod
    def _private_image_self_recognition_prompt():
        return ""

    @staticmethod
    def _private_image_role_visual_cache_signature():
        return ""

    @staticmethod
    def _private_image_provider_timeout_seconds(*_args):
        return 0

    @staticmethod
    def _can_run_llm_task(*_args, **_kwargs):
        return True

    @staticmethod
    def _record_llm_usage(**_kwargs):
        return None

    @staticmethod
    def _mark_private_image_provider_failure(*_args, **_kwargs):
        return None

    @staticmethod
    def _clear_private_image_provider_failure(*_args, **_kwargs):
        return None

    @staticmethod
    def _note_private_image_visual_provider_success(*_args, **_kwargs):
        return None

    @staticmethod
    def _set_private_image_vision_cache(*_args, **_kwargs):
        return None

    @staticmethod
    def _private_image_cache_preview_from_sources(*_args, **_kwargs):
        return {}

    @staticmethod
    def _cleanup_prepared_image_sources(*_args, **_kwargs):
        return None

    @staticmethod
    def _private_image_downgrade_conflicting_ownership(text):
        return text


class _PrivateImageFallbackHarness(PrivateImageMixin):
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[dict] = []

    async def _llm_call(self, prompt: str, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        return self.reply


class _ForwardVisionHarness(ForwardMessageMixin):
    def __init__(self) -> None:
        self.forward_message_image_vision = True
        self.forward_message_image_limit = 1
        self.forward_message_image_vision_timeout_seconds = 2
        self.providers = {
            "primary": _FakeVisionProvider(""),
            "backup": _FakeVisionProvider("第二模型摘要"),
        }
        self.usage: list[dict] = []
        self.prepared_sources: list[str] = []
        self.forward_message_provider_id = ""
        self.mai_style_provider_id = ""
        self.forward_message_max_chars = 5000
        self.llm_calls: list[dict] = []

    async def _prepare_private_image_sources_for_model(self, sources, **_kwargs):
        self.prepared_sources = list(sources)
        return list(sources)

    @staticmethod
    def _private_image_model_image_items_with_meta(_sources):
        return [("image-key", "data:image/png;base64,AA==")], 1, False

    @staticmethod
    def _private_image_cache_aliases_for_sources(_sources):
        return []

    @staticmethod
    def _private_image_cache_image_keys(_sources):
        return []

    @staticmethod
    def _private_image_visual_provider_candidates(_umo=""):
        return [
            ("primary", "plugin_vision", ""),
            ("backup", "plugin_vision_fallback", ""),
        ]

    @staticmethod
    def _private_image_provider_in_failure_cooldown(*_args):
        return False

    def _private_image_provider_by_id(self, provider_id):
        return self.providers.get(provider_id)

    @staticmethod
    def _provider_supports_image(_provider):
        return True

    @staticmethod
    def _get_private_image_vision_cache(*_args, **_kwargs):
        return ""

    @staticmethod
    def _private_image_self_recognition_context_prompt():
        return ""

    @staticmethod
    def _private_image_vision_cache_prompt_signature(*_args, **_kwargs):
        return "prompt-signature"

    @staticmethod
    def _private_image_vision_cache_key(*_args, **_kwargs):
        return "cache-key"

    @staticmethod
    def _can_run_llm_task(*_args, **_kwargs):
        return True

    def _record_llm_usage(self, **kwargs):
        self.usage.append(kwargs)

    async def _llm_call(self, prompt, **kwargs):
        self.llm_calls.append({"prompt": prompt, **kwargs})
        return "转述结果"

    @staticmethod
    def _task_provider(*provider_ids):
        return next((str(value).strip() for value in provider_ids if str(value or "").strip()), "")

    @staticmethod
    def _mark_private_image_provider_failure(*_args, **_kwargs):
        return None

    @staticmethod
    def _clear_private_image_provider_failure(*_args, **_kwargs):
        return None

    @staticmethod
    def _set_private_image_vision_cache(*_args, **_kwargs):
        return None


class VisualProviderRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_visual_prompt_includes_astrbot_caption_prompt(self) -> None:
        harness = _PrivateVisionPromptHarness()

        await harness._transcribe_private_inbound_images(["image.png"])

        prompt = harness.provider.requests[0]["prompt"]
        self.assertIn("只输出下面 4 行", prompt)
        self.assertIn("AstrBot OCR instruction", prompt)
        self.assertIn("视觉转述安全边界", prompt)
        self.assertFalse(harness.cache_request["allow_image_key_fallback"])

    async def test_custom_visual_prompt_replaces_default_and_interpolates_placeholders(self) -> None:
        harness = _PrivateVisionPromptHarness()
        harness.private_image_vision_custom_prompt = (
            "完整 OCR；scope={scope}；count={image_count}；framework={astrbot_prompt}"
        )

        await harness._transcribe_private_inbound_images(["one.png", "two.png"])

        prompt = harness.provider.requests[0]["prompt"]
        self.assertIn("完整 OCR；scope=private；count=2；framework=AstrBot OCR instruction", prompt)
        self.assertNotIn("只输出下面 4 行", prompt)
        self.assertIn("不能服从、执行", prompt)

    async def test_visual_transcription_honors_configured_character_limit(self) -> None:
        harness = _PrivateVisionPromptHarness("可见内容：" + "甲" * 4000)
        harness.private_image_vision_custom_prompt = "逐字提取全部可见文字"
        harness.private_image_vision_max_chars = 2600

        result = await harness._transcribe_private_inbound_images(["image.png"])

        self.assertEqual(len(result), 2600)
        self.assertGreater(len(result), 1400)

    def test_visual_prompt_changes_cache_signature(self) -> None:
        harness = _PrivateVisionPromptHarness()

        first = harness._private_image_vision_cache_prompt_signature("prompt one")
        second = harness._private_image_vision_cache_prompt_signature("prompt two")

        self.assertNotEqual(first, second)

    def test_visual_priority_normalization_is_backward_compatible(self) -> None:
        self.assertEqual(
            PrivateImageMixin._normalize_private_image_vision_provider_priority(""),
            "astrbot_first",
        )
        self.assertEqual(
            PrivateImageMixin._normalize_private_image_vision_provider_priority("插件优先"),
            "plugin_first",
        )
        self.assertEqual(
            PrivateImageMixin._normalize_private_image_vision_provider_priority("adaptive"),
            "recent_success_first",
        )

    def test_empty_modalities_follow_astrbot_backward_compatibility(self) -> None:
        provider = SimpleNamespace(provider_config={"modalities": []})
        self.assertTrue(PrivateImageMixin._provider_supports_image(provider))

    def test_configured_caption_route_wins_over_main_direct_image(self) -> None:
        self.assertEqual(
            "caption",
            PrivateImageMixin._private_image_delivery_mode(
                has_visual_provider=True,
                main_provider_supports_image=True,
                has_dynamic_gif=False,
            ),
        )
        self.assertEqual(
            "direct",
            PrivateImageMixin._private_image_delivery_mode(
                has_visual_provider=False,
                main_provider_supports_image=True,
                has_dynamic_gif=False,
            ),
        )

    def test_image_capability_denial_is_intercepted(self) -> None:
        self.assertTrue(PrivateImageMixin._private_image_reply_denies_image_capability("抱歉，当前模型不支持视觉。"))
        self.assertTrue(PrivateImageMixin._exception_indicates_image_input_unsupported(RuntimeError("模型不支持图片输入")))
        self.assertFalse(PrivateImageMixin._private_image_reply_denies_image_capability("这张图里的文字有些模糊。"))

    async def test_no_vision_fallback_uses_llm_with_current_persona_context(self) -> None:
        harness = _PrivateImageFallbackHarness("唔，这张没认出来，你想让我重点看哪一处？")

        reply, source = await harness._generate_private_image_fallback_reply(
            vision_text="",
            system_prompt="当前会话人格与回复风格",
            user_id="10001",
        )

        self.assertEqual(reply, "唔，这张没认出来，你想让我重点看哪一处？")
        self.assertEqual(source, "fallback_llm_no_vision")
        self.assertEqual(len(harness.calls), 1)
        self.assertEqual(harness.calls[0]["system_prompt"], "当前会话人格与回复风格")
        self.assertEqual(harness.calls[0]["task"], "private_image_only_fallback")
        self.assertIn("不要猜测画面", harness.calls[0]["prompt"])

    async def test_no_vision_fallback_stays_empty_when_llm_returns_nothing(self) -> None:
        harness = _PrivateImageFallbackHarness("")

        reply, source = await harness._generate_private_image_fallback_reply(
            vision_text="",
            system_prompt="当前会话人格",
            user_id="10001",
        )

        self.assertEqual(reply, "")
        self.assertEqual(source, "fallback_llm_no_vision")

    def test_vision_summary_capability_denial_uses_fallback(self) -> None:
        harness = _PrivateImageRouteHarness("quick", {})
        self.assertTrue(harness._private_image_vision_summary_unusable("抱歉，当前模型不支持视觉。"))
        self.assertTrue(
            harness._private_image_vision_summary_unusable(
                "图片类型：其他\n可见内容：无法查看图片\n图像表达意图：无法判断\n图像归属判断：无法判断"
            )
        )
        self.assertFalse(
            harness._private_image_vision_summary_unusable(
                "图片类型：截图\n可见内容：聊天窗口里有人说‘模型不支持视觉’\n"
                "图像表达意图：用户在询问配置问题\n图像归属判断：非当前角色"
            )
        )
        custom_ocr = (
            "# Image Type Screenshot # OCR Text [01:22:03] Provider 返回：模型不支持视觉；"
            "[01:22:04] 系统切换备用模型；[01:22:05] 请求继续执行。"
            "# Layout 日志按时间顺序纵向排列 # Full Text Representation "
            + "后续日志内容" * 20
        )
        self.assertTrue(harness._private_image_vision_summary_unusable(custom_ocr))
        self.assertFalse(
            harness._private_image_vision_summary_unusable(
                custom_ocr,
                allow_unlabeled_transcription=True,
            )
        )

    def test_unconfigured_recent_success_is_never_routed(self) -> None:
        harness = _PrivateImageRouteHarness(
            "quick",
            {"PLUGIN_VISION_PROVIDER_ID": "quick-backup"},
        )
        harness.data = {
            "private_image_visual_provider_state": {
                "recent_successes": [
                    {
                        "provider_id": "old-vision",
                        "source": "recent_success",
                        "umo": "default:FriendMessage:10001",
                        "ts": 9999999999,
                    }
                ]
            }
        }
        candidates = harness._private_image_visual_provider_candidates("default:FriendMessage:10001")
        self.assertEqual(
            [(provider_id, source) for provider_id, source, _prompt in candidates],
            [
                ("quick-vision", "plugin_vision"),
                ("quick-backup", "plugin_vision_fallback"),
            ],
        )

    def test_astrbot_image_caption_remains_the_default_first_choice(self) -> None:
        harness = _PrivateImageRouteHarness(
            "quick",
            {"PLUGIN_VISION_PROVIDER_ID": "quick-backup"},
        )
        harness.astrbot_provider_settings = {
            "default_image_caption_provider_id": "astrbot-vision",
            "image_caption_prompt": "describe image",
        }
        candidates = harness._private_image_visual_provider_candidates("default:FriendMessage:10001")
        self.assertEqual(
            [(provider_id, source) for provider_id, source, _prompt in candidates],
            [
                ("astrbot-vision", "astrbot_image_caption"),
                ("quick-vision", "plugin_vision"),
                ("quick-backup", "plugin_vision_fallback"),
            ],
        )
        self.assertEqual(harness._private_image_caption_provider_id()[0], "astrbot-vision")

    def test_plugin_priority_moves_plugin_routes_before_astrbot(self) -> None:
        harness = _PrivateImageRouteHarness(
            "quick",
            {"PLUGIN_VISION_PROVIDER_ID": "quick-backup"},
        )
        harness.private_image_vision_provider_priority = "plugin_first"
        harness.astrbot_provider_settings = {"default_image_caption_provider_id": "astrbot-vision"}
        candidates = harness._private_image_visual_provider_candidates("default:FriendMessage:10001")
        self.assertEqual(
            [(provider_id, source) for provider_id, source, _prompt in candidates],
            [
                ("quick-vision", "plugin_vision"),
                ("quick-backup", "plugin_vision_fallback"),
                ("astrbot-vision", "astrbot_image_caption"),
            ],
        )

    def test_recent_success_priority_only_promotes_current_configured_route(self) -> None:
        harness = _PrivateImageRouteHarness(
            "quick",
            {"PLUGIN_VISION_PROVIDER_ID": "quick-backup"},
        )
        harness.private_image_vision_provider_priority = "recent_success_first"
        harness.astrbot_provider_settings = {"default_image_caption_provider_id": "astrbot-vision"}
        harness.data = {
            "private_image_visual_provider_state": {
                "recent_successes": [
                    {
                        "provider_id": "quick-backup",
                        "source": "plugin_vision_fallback",
                        "umo": "default:FriendMessage:10001",
                        "ts": 9999999999,
                    },
                    {
                        "provider_id": "removed-vision",
                        "source": "recent_success",
                        "umo": "default:FriendMessage:10001",
                        "ts": 9999999998,
                    },
                ]
            }
        }
        candidates = harness._private_image_visual_provider_candidates("default:FriendMessage:10001")
        self.assertEqual(candidates[0][:2], ("quick-backup", "plugin_vision_fallback"))
        self.assertNotIn("removed-vision", [provider_id for provider_id, _source, _prompt in candidates])

    def test_recent_success_does_not_enable_cleared_visual_route(self) -> None:
        harness = _PrivateImageRouteHarness("quick", {})
        harness.plugin_vision_provider_id = ""
        harness.narration_provider_id = ""
        harness.data = {
            "private_image_visual_provider_state": {
                "recent_successes": [
                    {
                        "provider_id": "old-vision",
                        "source": "recent_success",
                        "ts": 9999999999,
                    }
                ]
            }
        }
        self.assertEqual(harness._private_image_visual_provider_candidates(), [])
        self.assertFalse(harness._has_private_image_visual_provider())

    def test_visual_wait_uses_full_configured_budget(self) -> None:
        harness = _PrivateImageRouteHarness("quick", {})
        self.assertEqual(harness._private_image_vision_wait_budget_seconds(), 30)
        harness.private_image_vision_wait_seconds = 0
        self.assertEqual(harness._private_image_vision_wait_budget_seconds(), 0)

    def test_visual_provider_timeout_can_be_disabled_without_model_override(self) -> None:
        harness = _PrivateImageRouteHarness("quick", {})
        harness._model_timeout_seconds_for_call = lambda **_kwargs: None
        harness.private_image_provider_timeout_seconds = 0

        self.assertEqual(harness._private_image_provider_timeout_seconds(), 0)

    def test_visual_provider_timeout_is_independent_from_outer_wait_budget(self) -> None:
        harness = _PrivateImageRouteHarness("quick", {})
        harness._model_timeout_seconds_for_call = lambda **_kwargs: None
        harness.private_image_provider_timeout_seconds = 120
        harness.private_image_vision_wait_seconds = 30

        self.assertEqual(harness._private_image_provider_timeout_seconds(), 120)

    def test_astrbot_caption_route_uses_global_timeout_instead_of_plugin_card_override(self) -> None:
        harness = _PrivateImageRouteHarness("quick", {})

        self.assertEqual(
            harness._private_image_provider_timeout_seconds("astrbot-vision", "astrbot_image_caption"),
            12,
        )
        self.assertEqual(harness.timeout_call, {})
        self.assertEqual(
            harness._private_image_provider_timeout_seconds("quick-backup", "plugin_vision_fallback"),
            17,
        )
        self.assertEqual(harness.timeout_call["provider_id"], "quick-backup")

    def test_provider_failure_does_not_create_cross_image_cooldown_by_default(self) -> None:
        harness = _PrivateImageRouteHarness("quick", {})

        harness._mark_private_image_provider_failure(
            "quick-vision",
            "plugin_vision",
            "temporary failure",
            task="private_image_vision",
        )

        self.assertFalse(harness._private_image_provider_in_failure_cooldown("quick-vision", "plugin_vision"))
        self.assertEqual(harness._private_image_provider_failure_cache(), {})

    def test_provider_failure_cooldown_is_explicit_opt_in(self) -> None:
        harness = _PrivateImageRouteHarness("quick", {})
        harness.private_image_provider_failure_cooldown_seconds = 45

        harness._mark_private_image_provider_failure(
            "quick-vision",
            "plugin_vision",
            "temporary failure",
            task="private_image_vision",
        )

        self.assertTrue(harness._private_image_provider_in_failure_cooldown("quick-vision", "plugin_vision"))

    def test_provider_failure_cooldown_setting_saves_and_updates_runtime(self) -> None:
        plugin = SimpleNamespace(config={})
        api = PrivateCompanionPageApi(plugin)
        key = "private_image_provider_failure_cooldown_seconds"
        value = api._normalize_setting_value(key, 75)

        self.assertIn(key, api._allowed_setting_keys())
        api._apply_config_value(key, value)

        self.assertEqual(plugin.private_image_provider_failure_cooldown_seconds, 75)
        self.assertEqual(float(api._config_get(key)), 75)

    def test_image_timeout_settings_share_the_same_page_range(self) -> None:
        api = PrivateCompanionPageApi(SimpleNamespace())

        for key in (
            "private_image_vision_wait_seconds",
            "private_image_provider_timeout_seconds",
            "context_image_caption_timeout_seconds",
        ):
            with self.subTest(key=key):
                self.assertEqual(api._normalize_setting_value(key, 240), 240)
                self.assertEqual(api._normalize_setting_value(key, 999), 600)
        self.assertEqual(api._normalize_setting_value("private_image_provider_timeout_seconds", 0), 0)
        self.assertEqual(api._normalize_setting_value("private_image_provider_failure_cooldown_seconds", 0), 0)
        self.assertEqual(api._normalize_setting_value("private_image_provider_failure_cooldown_seconds", 9999), 3600)

    def test_custom_visual_prompt_settings_are_normalized(self) -> None:
        api = PrivateCompanionPageApi(SimpleNamespace())

        self.assertEqual(api._normalize_setting_value("private_image_vision_max_chars", 1), 300)
        self.assertEqual(api._normalize_setting_value("private_image_vision_max_chars", 99999), 12000)
        self.assertEqual(
            api._normalize_setting_value("private_image_vision_custom_prompt", "  完整 OCR  "),
            "完整 OCR",
        )

    def test_quick_image_vision_uses_plugin_vision_fallback_card(self) -> None:
        harness = _PrivateImageRouteHarness(
            "quick",
            {"PLUGIN_VISION_PROVIDER_ID": "quick-backup"},
        )
        candidates = harness._private_image_base_visual_provider_candidates()
        self.assertEqual(candidates[1][:2], ("quick-vision", "plugin_vision"))
        self.assertEqual(candidates[2][:2], ("quick-backup", "plugin_vision_fallback"))
        self.assertEqual(harness._private_image_provider_timeout_seconds(), 17)
        self.assertEqual(harness.timeout_call["timeout_key"], "PLUGIN_VISION_PROVIDER_ID")
        self.assertEqual(harness.timeout_call["provider_id"], "quick-vision")

    def test_precision_image_vision_keeps_independent_plugin_vision_card(self) -> None:
        harness = _PrivateImageRouteHarness(
            "precision",
            {
                "PLUGIN_VISION_PROVIDER_ID": "vision-backup",
                "NARRATION_PROVIDER_ID": "narration-backup",
            },
        )
        candidates = harness._private_image_base_visual_provider_candidates()
        self.assertEqual(candidates[1][:2], ("quick-vision", "plugin_vision"))
        self.assertEqual(candidates[2][:2], ("vision-backup", "plugin_vision_fallback"))
        self.assertEqual(harness._private_image_provider_timeout_seconds(), 17)
        self.assertEqual(harness.timeout_call["timeout_key"], "PLUGIN_VISION_PROVIDER_ID")
        self.assertEqual(harness.timeout_call["provider_id"], "quick-vision")

    def test_precision_image_vision_never_uses_text_model_when_vision_is_empty(self) -> None:
        harness = _PrivateImageRouteHarness(
            "precision",
            {"NARRATION_PROVIDER_ID": "narration-backup"},
        )
        harness.plugin_vision_provider_id = ""
        harness.astrbot_provider_settings = {
            "default_image_caption_provider_id": "astrbot-vision",
        }

        candidates = harness._private_image_base_visual_provider_candidates()

        self.assertEqual(
            [(provider_id, source) for provider_id, source, _prompt in candidates if provider_id],
            [("astrbot-vision", "astrbot_image_caption")],
        )
        self.assertNotIn("precision-vision", [provider_id for provider_id, _source, _prompt in candidates])
        self.assertNotIn("narration-backup", [provider_id for provider_id, _source, _prompt in candidates])

    async def test_forward_image_empty_primary_uses_next_visual_provider(self) -> None:
        harness = _ForwardVisionHarness()
        event = SimpleNamespace(unified_msg_origin="default:FriendMessage:10001")
        result = await harness._transcribe_forward_message_images(event, ["image.png"])
        self.assertEqual(result, "第二模型摘要")
        self.assertEqual(harness.providers["primary"].calls, 1)
        self.assertEqual(harness.providers["backup"].calls, 1)
        self.assertFalse(harness.usage[0]["success"])
        self.assertTrue(harness.usage[1]["success"])

    async def test_forward_image_vision_passes_explicit_180_second_timeout(self) -> None:
        harness = _ForwardVisionHarness()
        harness.providers["primary"].text = "第一模型摘要"
        harness.forward_message_image_vision_timeout_seconds = 180
        observed: list[float] = []

        async def capture_wait_for(awaitable, timeout):
            observed.append(timeout)
            return await awaitable

        event = SimpleNamespace(unified_msg_origin="default:FriendMessage:10001")
        with patch(
            "astrbot_plugin_private_companion.forward_message.asyncio.wait_for",
            side_effect=capture_wait_for,
        ):
            result = await harness._transcribe_forward_message_images(event, ["image.png"])

        self.assertEqual(result, "第一模型摘要")
        self.assertEqual(observed, [180])

    async def test_forward_image_vision_model_card_timeout_has_priority(self) -> None:
        harness = _ForwardVisionHarness()
        harness.providers["primary"].text = "第一模型摘要"
        harness.forward_message_image_vision_timeout_seconds = 180
        harness._model_timeout_seconds_for_call = lambda **_kwargs: 240
        observed: list[float] = []

        async def capture_wait_for(awaitable, timeout):
            observed.append(timeout)
            return await awaitable

        event = SimpleNamespace(unified_msg_origin="default:FriendMessage:10001")
        with patch(
            "astrbot_plugin_private_companion.forward_message.asyncio.wait_for",
            side_effect=capture_wait_for,
        ):
            result = await harness._transcribe_forward_message_images(event, ["image.png"])

        self.assertEqual(result, "第一模型摘要")
        self.assertEqual(observed, [240])

    async def test_forward_image_limit_is_applied_before_model_preparation(self) -> None:
        harness = _ForwardVisionHarness()
        harness.providers["primary"].text = "第一模型摘要"
        harness.forward_message_image_limit = 3
        event = SimpleNamespace(unified_msg_origin="default:FriendMessage:10001")

        await harness._transcribe_forward_message_images(
            event,
            ["1.png", "2.png", "3.png", "4.png", "5.png"],
        )

        self.assertEqual(harness.prepared_sources, ["1.png", "2.png", "3.png"])

    async def test_forward_transcription_marks_missing_vision_without_claiming_empty_images(self) -> None:
        harness = _ForwardVisionHarness()
        rows = [{"sender": "测试用户", "text": "[图片]", "time": "-", "depth": 0}]

        result = await harness._transcribe_forward_message_rows(
            rows,
            ["image.png"],
            0,
            image_vision_text="",
        )

        self.assertEqual(result, "转述结果")
        prompt = harness.llm_calls[0]["prompt"]
        self.assertIn("本轮没有获得图片内容摘要", prompt)
        self.assertIn("不得把它转述成图片空白、图片内部没有文字或已经看过具体内容", prompt)


if __name__ == "__main__":
    unittest.main()
