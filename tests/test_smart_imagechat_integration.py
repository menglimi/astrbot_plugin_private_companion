# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from astrbot.api.message_components import Image, Plain
from astrbot_plugin_private_companion.llm_tool_actions import (
    LlmToolActionsMixin,
    PHOTO_TOOL_SILENT_SENTINEL,
)
from astrbot_plugin_private_companion.main import PrivateCompanionPlugin
from astrbot_plugin_private_companion.reaction_expression import (
    classify_reaction_expression_feedback,
    ensure_reaction_expression_state,
    normalize_reaction_expression_intent,
    reserve_reaction_expression_image,
    record_reaction_expression_feedback,
    record_reaction_expression_sent,
    reaction_expression_explicit_opt_out,
    reaction_expression_explicit_request,
    reaction_expression_auto_disabled,
    reaction_expression_high_frequency,
    reaction_expression_normalize_probability,
    sync_reaction_expression_auto_preference,
)
from astrbot_plugin_private_companion.scene_context import SceneContextMixin


class _FakeEvent:
    unified_msg_origin = "default:FriendMessage:10001"
    message_str = "来一张无语的表情包"

    def __init__(self) -> None:
        self.extras: dict[str, object] = {}

    def set_extra(self, key: str, value: object) -> None:
        self.extras[key] = value

    def get_sender_id(self) -> str:
        return "10001"


class _FakeResultEvent(_FakeEvent):
    def __init__(self, chain: list[object] | None = None) -> None:
        super().__init__()
        self._result = SimpleNamespace(chain=list(chain or []))
        self._has_send_oper = False
        self.sent_results: list[object] = []

    def get_result(self):
        return self._result

    def set_result(self, result) -> None:
        self._result = result

    @staticmethod
    def chain_result(chain):
        return SimpleNamespace(chain=list(chain))

    async def send(self, result):
        self.sent_results.append(result)
        self._has_send_oper = True
        return True


class _FakeGroupEvent(_FakeEvent):
    unified_msg_origin = "default:GroupMessage:20001"

    @staticmethod
    def is_private_chat() -> bool:
        return False


class _FakeSecondUserEvent(_FakeEvent):
    unified_msg_origin = "default:FriendMessage:10002"

    def get_sender_id(self) -> str:
        return "10002"


class _FakeSmartImageAPI:
    def __init__(self, image_path: str, *, image_id: str = "reaction-1") -> None:
        self.image_path = image_path
        self.image_id = image_id
        self.calls: list[dict] = []

    async def find_image(self, event, query, **kwargs):
        self.calls.append({"event": event, "query": query, **kwargs})
        return {
            "success": True,
            "status": "success",
            "image_id": self.image_id,
            "path": self.image_path,
            "tags": ["表情包", "无语", "吐槽"],
            "need": "无语但轻松的回应",
            "reason": "标签和当前吐槽语境一致",
            "confidence": 0.88,
        }


class _BlockingSmartImageAPI(_FakeSmartImageAPI):
    def __init__(self, image_path: str, *, image_id: str = "reaction-1") -> None:
        super().__init__(image_path, image_id=image_id)
        self.started = threading.Event()
        self.release = threading.Event()

    async def find_image(self, event, query, **kwargs):
        self.calls.append({"event": event, "query": query, **kwargs})
        self.started.set()
        await asyncio.to_thread(self.release.wait)
        return {
            "success": True,
            "status": "success",
            "image_id": self.image_id,
            "path": self.image_path,
            "tags": ["表情包", "无语", "吐槽"],
            "need": "无语但轻松的回应",
            "reason": "标签和当前吐槽语境一致",
            "confidence": 0.88,
        }


class _OwnedReactionLibraryAdapter:
    """Expose the historical fake lookup through the plugin-owned library contract."""

    def __init__(self, api: _FakeSmartImageAPI) -> None:
        self.api = api
        self.selection_calls: list[dict[str, object]] = []

    @staticmethod
    def has_enabled_assets() -> bool:
        return True

    def find(
        self,
        query: str,
        *,
        context: str = "",
        scope: str = "private",
        selection_preferences: object = None,
        selection_signature: str = "",
    ):
        self.selection_calls.append(
            {
                "preferences": selection_preferences,
                "signature": selection_signature,
            }
        )
        return asyncio.run(
            self.api.find_image(
                None,
                query,
                context=context,
                scope=scope,
                meme_only=True,
            )
        )

    @staticmethod
    def mark_used(_image_id: str) -> bool:
        return True


class _FakeToolSet:
    def __init__(self, *names: str) -> None:
        self.tools = [SimpleNamespace(name=name) for name in names]

    def get_tool(self, name: str):
        return next((tool for tool in self.tools if tool.name == name), None)

    def remove_tool(self, name: str) -> None:
        self.tools = [tool for tool in self.tools if tool.name != name]


class _ReactionHarness(SceneContextMixin, LlmToolActionsMixin):
    _reaction_expression_delivery_mode = (
        PrivateCompanionPlugin._reaction_expression_delivery_mode
    )
    _send_reaction_expression_component_separately = (
        PrivateCompanionPlugin._send_reaction_expression_component_separately
    )
    _reaction_expression_flatten_delivery_components = staticmethod(
        PrivateCompanionPlugin._reaction_expression_flatten_delivery_components
    )
    _reaction_expression_delivery_signature = staticmethod(
        PrivateCompanionPlugin._reaction_expression_delivery_signature
    )
    _install_reaction_expression_delivery_tracker = (
        PrivateCompanionPlugin._install_reaction_expression_delivery_tracker
    )
    _reaction_expression_primary_reply_confirmed = (
        PrivateCompanionPlugin._reaction_expression_primary_reply_confirmed
    )
    _reaction_expression_image_delivery_confirmed = (
        PrivateCompanionPlugin._reaction_expression_image_delivery_confirmed
    )
    _restore_reaction_expression_delivery_tracker = staticmethod(
        PrivateCompanionPlugin._restore_reaction_expression_delivery_tracker
    )
    _reaction_expression_attachment_present = staticmethod(
        PrivateCompanionPlugin._reaction_expression_attachment_present
    )

    def __init__(self, api: _FakeSmartImageAPI, *, sent: bool = True) -> None:
        self.api = api
        self.sent = sent
        self._data_lock = asyncio.Lock()
        self.users = {"10001": {}}
        self.data = {
            "users": self.users,
            "daily_state": {"energy": 58, "mood_bias": "轻松", "location": "家里"},
        }
        self.saved = False
        self.deliveries = 0
        self.last_delivery_kwargs: dict[str, object] = {}
        self.enable_reaction_expression_experiment = False
        self.reaction_expression_private_enabled = True
        self.reaction_expression_group_enabled = False
        self.reaction_expression_trigger_probability = 0.2
        self.reaction_expression_cooldown_seconds = 180
        self.reaction_expression_low_latency_mode = True
        self.reaction_expression_candidate_limit = 6
        self.reaction_expression_delivery_mode = "separate_after"
        self.enabled = True
        self._owned_reaction_library = _OwnedReactionLibraryAdapter(api)

    def _reaction_asset_library(self):
        return self._owned_reaction_library

    async def _deliver_generated_image_to_event(self, *_args, **kwargs):
        self.deliveries += 1
        self.last_delivery_kwargs = dict(kwargs)
        return {
            "sent": self.sent,
            "destination": "current",
            "message": "图片已发送" if self.sent else "图片发送失败：平台拒绝",
        }

    def _get_user(self, user_id: str):
        return self.users.setdefault(user_id, {})

    @staticmethod
    def _remember_recent_photo_share_snapshot(user, **kwargs) -> None:
        user["last_photo_share_snapshot"] = dict(kwargs)

    def _save_data_sync(self, **_kwargs) -> None:
        self.saved = True

    @staticmethod
    def _proactive_only_blocks_passive_event(*_args, **_kwargs) -> bool:
        return False

    def enable_reaction_experiment(self, **overrides) -> None:
        self.enable_reaction_expression_experiment = True
        self.reaction_expression_trigger_probability = 1.0
        for key, value in overrides.items():
            setattr(self, key, value)


def _reaction_log_payloads(log_info) -> list[dict[str, object]]:
    prefix = "[ReactionExpression] %s"
    payloads: list[dict[str, object]] = []
    for call in log_info.call_args_list:
        if len(call.args) < 2 or call.args[0] != prefix:
            continue
        payload = json.loads(call.args[1])
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


class SmartImageChatIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        image_path = os.path.join(self.temp_dir.name, "reaction  image.png")
        with open(image_path, "wb") as handle:
            handle.write(b"image")
        self.image_path = image_path

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_reaction_auto_preference_persists_until_explicit_resume(self) -> None:
        state = ensure_reaction_expression_state({})
        self.assertEqual(
            "",
            sync_reaction_expression_auto_preference(
                state, "别发了", now=5.0, scope_key="private:10001"
            ),
        )
        self.assertEqual(
            "disabled",
            sync_reaction_expression_auto_preference(
                state, "别再发表情包了", now=10.0, scope_key="private:10001"
            ),
        )
        self.assertTrue(reaction_expression_auto_disabled(state, "private:10001"))
        self.assertFalse(reaction_expression_auto_disabled(state, "group:20002"))
        self.assertEqual(
            "",
            sync_reaction_expression_auto_preference(
                state, "普通聊天", now=20.0, scope_key="private:10001"
            ),
        )
        self.assertTrue(reaction_expression_auto_disabled(state, "private:10001"))
        self.assertTrue(reaction_expression_explicit_request("给我来一张无语表情包"))
        self.assertEqual(
            "enabled",
            sync_reaction_expression_auto_preference(
                state, "可以继续发表情包", now=30.0, scope_key="private:10001"
            ),
        )
        self.assertFalse(reaction_expression_auto_disabled(state, "private:10001"))

    def test_legacy_global_reaction_opt_out_is_inherited_per_scope(self) -> None:
        state = ensure_reaction_expression_state({})
        state["auto_disabled"] = True

        self.assertTrue(reaction_expression_auto_disabled(state, "private:10001"))
        self.assertEqual(
            "enabled",
            sync_reaction_expression_auto_preference(
                state,
                "恢复自动表情包",
                now=30.0,
                scope_key="private:10001",
            ),
        )
        self.assertFalse(reaction_expression_auto_disabled(state, "private:10001"))
        self.assertTrue(reaction_expression_auto_disabled(state, "group:20002"))

    def test_explicit_request_is_single_turn_and_does_not_resume_automatic_reactions(self) -> None:
        harness = _ReactionHarness(_FakeSmartImageAPI(self.image_path))
        user = harness.users["10001"]
        state = ensure_reaction_expression_state(user)
        scope_key = "default:FriendMessage:10001"
        sync_reaction_expression_auto_preference(
            state,
            "别再发表情包了",
            now=10.0,
            scope_key=scope_key,
        )
        event = _FakeEvent()
        event.message_str = "给我来一张无语表情包"

        trigger = harness._reaction_expression_local_trigger(
            event,
            user,
            configured_probability=1.0,
            scope_key=scope_key,
        )

        self.assertEqual("explicit_request", trigger["mode"])
        self.assertTrue(reaction_expression_auto_disabled(state, scope_key))

    def test_one_off_reaction_requests_never_restore_automatic_sending(self) -> None:
        scope_key = "default:FriendMessage:10001"
        for index, text in enumerate(("请发个表情包", "继续发个表情包"), start=1):
            with self.subTest(text=text):
                state = ensure_reaction_expression_state({})
                self.assertEqual(
                    "disabled",
                    sync_reaction_expression_auto_preference(
                        state,
                        "别再发表情包了",
                        now=float(index),
                        scope_key=scope_key,
                    ),
                )
                self.assertTrue(reaction_expression_explicit_request(text))
                self.assertEqual(
                    "",
                    sync_reaction_expression_auto_preference(
                        state,
                        text,
                        now=10.0 + index,
                        scope_key=scope_key,
                    ),
                )
                self.assertTrue(
                    reaction_expression_auto_disabled(state, scope_key)
                )

        self.assertTrue(
            reaction_expression_explicit_request(
                "以后别自动发表情包了，不过这次给我来一张表情包"
            )
        )

    def test_percentage_probability_values_keep_their_runtime_unit(self) -> None:
        self.assertEqual(0.5, reaction_expression_normalize_probability(50))
        self.assertEqual(1.0, reaction_expression_normalize_probability(100))
        self.assertTrue(reaction_expression_high_frequency(100))
        self.assertFalse(reaction_expression_high_frequency(99))

    def test_historical_reaction_mentions_do_not_override_opt_out(self) -> None:
        harness = _ReactionHarness(_FakeSmartImageAPI(self.image_path))
        cases = (
            "别再发表情包了，你刚才发的表情包很烦",
            "别再发表情包了，为什么你刚刚还发了一个表情包",
            "别发了，你发的表情包一点也不贴切",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertTrue(reaction_expression_explicit_opt_out(text))
                self.assertFalse(reaction_expression_explicit_request(text))
                self.assertFalse(harness._photo_generation_instruction_matches(text))

        for text in (
            "请发个表情包",
            "给我找个开心反应图",
            "用这个表情包回应一下",
            "以后别自动发，不过这次给我来一张表情包",
        ):
            with self.subTest(text=text):
                self.assertTrue(reaction_expression_explicit_request(text))

    def test_hidden_reaction_intent_is_parsed_and_removed(self) -> None:
        harness = _ReactionHarness(_FakeSmartImageAPI(self.image_path))
        source = (
            "嗯，被捏得软绵绵的（\n"
            '<pc_reaction_expression>{"purpose":"接住玩笑","emotion":"害羞",'
            '"intensity":2,"candidate_queries":["害羞捂脸","软绵绵"]}'
            "</pc_reaction_expression>"
        )

        cleaned, intent = harness._extract_reaction_expression_hidden_intent(source)

        self.assertEqual("嗯，被捏得软绵绵的（", cleaned)
        self.assertEqual("接住玩笑", intent["purpose"])
        self.assertEqual("害羞", intent["emotion"])
        self.assertEqual(2, intent["intensity"])
        self.assertEqual(["害羞捂脸", "软绵绵"], intent["candidate_queries"])
        self.assertNotIn("pc_reaction_expression", cleaned)

    def test_malformed_escaped_and_truncated_hidden_tags_never_leak(self) -> None:
        harness = _ReactionHarness(_FakeSmartImageAPI(self.image_path))
        cases = (
            (
                "保留正文<pc_reaction_expression>{oops}</pc_reaction_expression>",
                {},
            ),
            (
                r'保留正文\<pc_reaction_expression\>{\"emotion\":\"开心\"}\</pc_reaction_expression\>',
                {"emotion": "开心"},
            ),
            (
                "保留正文&lt;pc_reaction_expression&gt;"
                "{&quot;purpose&quot;:&quot;回应&quot;}"
                "&lt;/pc_reaction_expression&gt;",
                {"purpose": "回应"},
            ),
            (
                '保留正文<pc_reaction_expression>{"emotion":"开心"',
                {},
            ),
            (
                '保留正文<pc_reaction_expression>{"candidate_queries":"[]"}'
                "</pc_reaction_expression>",
                {},
            ),
            ("保留正文</pc_reaction_expression>", {}),
        )

        for source, expected in cases:
            with self.subTest(source=source):
                cleaned, intent = harness._extract_reaction_expression_hidden_intent(source)
                self.assertEqual("保留正文", cleaned)
                self.assertNotRegex(cleaned.casefold(), r"pc[_-]?reaction")
                for key, value in expected.items():
                    self.assertEqual(value, intent[key])
                if not expected:
                    self.assertEqual({}, intent)

    def test_hidden_intent_without_visible_text_is_not_attachable(self) -> None:
        harness = _ReactionHarness(_FakeSmartImageAPI(self.image_path))
        source = (
            '<pc_reaction_expression>{"purpose":"回应","emotion":"开心"}'
            "</pc_reaction_expression>"
        )

        cleaned, intent = harness._extract_reaction_expression_hidden_intent(source)

        self.assertTrue(intent)
        self.assertEqual("", cleaned)
        self.assertFalse(harness._reaction_expression_has_visible_text(cleaned))

    async def test_llm_response_hook_cleans_and_stashes_single_pass_intent(self) -> None:
        harness = _ReactionHarness(_FakeSmartImageAPI(self.image_path))
        harness.enable_reaction_experiment()
        event = _FakeEvent()
        module = SimpleNamespace(get_smart_imagechat_api=lambda: harness.api)
        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            self.assertTrue(
                await harness._preauthorize_reaction_expression_prompt(event)
            )
        source = (
            "这是可以独立发送的完整文字。\n"
            '<pc_reaction_expression>{"purpose":"轻吐槽","emotion":"无语",'
            '"intensity":2,"candidate_queries":["无语摊手"]}'
            "</pc_reaction_expression>"
        )
        response = SimpleNamespace(
            completion_text=source,
            result_chain=None,
            tools_call_name=[],
        )
        harness._recover_plaintext_photo_tool_call = AsyncMock(
            return_value=(source, False)
        )
        harness._guard_unread_creative_work_response = lambda _event, text: text
        harness.protect_tts_enhancement_response_blocks = AsyncMock()

        await PrivateCompanionPlugin.normalize_tts_enhancement_response(
            harness,
            event,
            response,
        )

        self.assertEqual("这是可以独立发送的完整文字。", response.completion_text)
        self.assertEqual(
            "轻吐槽",
            event._private_companion_reaction_expression_intent["purpose"],
        )
        harness.protect_tts_enhancement_response_blocks.assert_awaited_once()

    async def test_tool_call_intermediate_response_cannot_stash_reaction_intent(self) -> None:
        harness = _ReactionHarness(_FakeSmartImageAPI(self.image_path))
        harness.enable_reaction_experiment()
        event = _FakeEvent()
        module = SimpleNamespace(get_smart_imagechat_api=lambda: harness.api)
        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(event))
        source = (
            "这是工具调用前的中间文字。\n"
            '<pc_reaction_expression>{"purpose":"中间意图","emotion":"惊讶"}'
            "</pc_reaction_expression>"
        )
        response = SimpleNamespace(
            completion_text=source,
            result_chain=None,
            tools_call_name=["some_tool"],
        )
        harness._recover_plaintext_photo_tool_call = AsyncMock(
            return_value=(source, False)
        )
        harness._guard_unread_creative_work_response = lambda _event, text: text
        harness.protect_tts_enhancement_response_blocks = AsyncMock()

        await PrivateCompanionPlugin.normalize_tts_enhancement_response(
            harness,
            event,
            response,
        )

        self.assertEqual("这是工具调用前的中间文字。", response.completion_text)
        self.assertFalse(
            hasattr(event, "_private_companion_reaction_expression_intent")
        )
        self.assertFalse(
            event.extras[
                "private_companion_reaction_expression_authorization"
            ]["model_omission_recorded"]
        )
        self.assertEqual(
            0,
            getattr(harness, "_reaction_expression_runtime", {}).get(
                "model_omissions", 0
            ),
        )

    async def test_llm_response_hook_records_model_omission_once_without_tool_call(self) -> None:
        harness = _ReactionHarness(_FakeSmartImageAPI(self.image_path))
        harness.enable_reaction_experiment(
            reaction_expression_trigger_probability=0.2
        )
        event = _FakeEvent()
        module = SimpleNamespace(get_smart_imagechat_api=lambda: harness.api)
        with (
            patch(
                "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
                return_value=module,
            ),
            patch(
                "astrbot_plugin_private_companion.llm_tool_actions.random.random",
                return_value=0.0,
            ),
        ):
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(event))
        source = "这是一条完整的纯文字回复。"
        response = SimpleNamespace(
            completion_text=source,
            result_chain=None,
            tools_call_name=[],
        )
        harness._recover_plaintext_photo_tool_call = AsyncMock(
            return_value=(source, False)
        )
        harness._guard_unread_creative_work_response = lambda _event, text: text
        harness.protect_tts_enhancement_response_blocks = AsyncMock()

        await PrivateCompanionPlugin.normalize_tts_enhancement_response(
            harness, event, response
        )
        await PrivateCompanionPlugin.normalize_tts_enhancement_response(
            harness, event, response
        )

        self.assertEqual(1, harness._reaction_expression_runtime["model_omissions"])
        self.assertTrue(
            event.extras[
                "private_companion_reaction_expression_authorization"
            ]["model_omission_recorded"]
        )
        self.assertFalse(
            hasattr(event, "_private_companion_reaction_expression_intent")
        )
        self.assertEqual(
            0,
            harness._reaction_expression_runtime.get("local_fallbacks", 0),
        )

    async def test_model_omission_uses_current_high_confidence_semantic_profile(self) -> None:
        harness = _ReactionHarness(_FakeSmartImageAPI(self.image_path))
        harness.enable_reaction_experiment()
        event = _FakeEvent()
        event.message_str = "你这次做得不错"
        harness.users["10001"]["intent_profile"] = {
            "intent": "play",
            "emotion_event": "praise",
            "emotion_intensity": 60,
            "confidence": 0.9,
            "emotion_confidence": 0.86,
            "emotion_target": "bot",
            "text": event.message_str,
        }
        module = SimpleNamespace(get_smart_imagechat_api=lambda: harness.api)
        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(event))
        source = "这是一条完整的纯文字回复。"
        response = SimpleNamespace(
            completion_text=source,
            result_chain=None,
            tools_call_name=[],
        )
        harness._recover_plaintext_photo_tool_call = AsyncMock(
            return_value=(source, False)
        )
        harness._guard_unread_creative_work_response = lambda _event, text: text
        harness.protect_tts_enhancement_response_blocks = AsyncMock()

        await PrivateCompanionPlugin.normalize_tts_enhancement_response(
            harness,
            event,
            response,
        )

        self.assertEqual(
            "回应夸奖",
            event._private_companion_reaction_expression_intent["purpose"],
        )
        self.assertEqual(1, harness._reaction_expression_runtime["local_fallbacks"])

    async def test_local_fallback_keeps_comfort_direction_when_user_soothes_the_bot(self) -> None:
        harness = _ReactionHarness(_FakeSmartImageAPI(self.image_path))
        harness.enable_reaction_experiment()
        event = _FakeEvent()
        event.message_str = "别难过，我陪着你"
        harness.users["10001"]["intent_profile"] = {
            "intent": "intimacy",
            "emotion_event": "comfort",
            "emotion_intensity": 62,
            "confidence": 0.9,
            "emotion_confidence": 0.86,
            "emotion_target": "bot",
            "text": event.message_str,
        }
        module = SimpleNamespace(get_smart_imagechat_api=lambda: harness.api)
        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(event))
        authorization = event.extras["private_companion_reaction_expression_authorization"]
        fallback = harness._reaction_expression_local_fallback_intent(
            event,
            "嗯，被你这样哄着，心里好多了。",
            authorization,
        )

        self.assertEqual("回应安抚", fallback["purpose"])
        self.assertEqual("安心", fallback["emotion"])
        self.assertNotIn("接住低落", fallback["purpose"])

    async def test_local_fallback_keeps_comfort_need_directed_to_the_user(self) -> None:
        harness = _ReactionHarness(_FakeSmartImageAPI(self.image_path))
        harness.enable_reaction_experiment()
        event = _FakeEvent()
        event.message_str = "我是不是很没用"
        harness.users["10001"]["intent_profile"] = {
            "intent": "comfort",
            "emotion_event": "comfort_need",
            "emotion_intensity": 68,
            "confidence": 0.9,
            "emotion_confidence": 0.88,
            "emotion_target": "self",
            "text": event.message_str,
        }
        module = SimpleNamespace(get_smart_imagechat_api=lambda: harness.api)
        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(event))
        authorization = event.extras["private_companion_reaction_expression_authorization"]
        fallback = harness._reaction_expression_local_fallback_intent(
            event,
            "不是这样的，先让我陪你缓一缓。",
            authorization,
        )

        self.assertEqual("接住低落", fallback["purpose"])
        self.assertEqual("温柔", fallback["emotion"])
        self.assertIn("安慰陪伴", fallback["candidate_queries"])

    async def test_llm_response_hook_does_not_count_cleaned_intent_as_omission(self) -> None:
        harness = _ReactionHarness(_FakeSmartImageAPI(self.image_path))
        harness.enable_reaction_experiment()
        event = _FakeEvent()
        module = SimpleNamespace(get_smart_imagechat_api=lambda: harness.api)
        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(event))
        source = (
            "这是完整正文。\n"
            '<pc_reaction_expression>{"purpose":"轻吐槽","emotion":"无语"}'
            "</pc_reaction_expression>"
        )
        response = SimpleNamespace(
            completion_text=source,
            result_chain=None,
            tools_call_name=[],
        )
        harness._recover_plaintext_photo_tool_call = AsyncMock(
            side_effect=lambda _event, _resp, text: (text, False)
        )
        harness._guard_unread_creative_work_response = lambda _event, text: text
        harness.protect_tts_enhancement_response_blocks = AsyncMock()

        await PrivateCompanionPlugin.normalize_tts_enhancement_response(
            harness, event, response
        )
        # AstrBot may invoke the response hook again after the hidden tag has
        # already been removed and the intent stashed on the event.
        await PrivateCompanionPlugin.normalize_tts_enhancement_response(
            harness, event, response
        )

        self.assertEqual(0, harness._reaction_expression_runtime.get("model_omissions", 0))
        self.assertEqual("轻吐槽", event._private_companion_reaction_expression_intent["purpose"])

    async def test_llm_response_tool_call_is_not_counted_as_model_omission(self) -> None:
        harness = _ReactionHarness(_FakeSmartImageAPI(self.image_path))
        harness.enable_reaction_experiment()
        event = _FakeEvent()
        module = SimpleNamespace(get_smart_imagechat_api=lambda: harness.api)
        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(event))
        source = "工具调用前的说明。"
        response = SimpleNamespace(
            completion_text=source,
            result_chain=None,
            tools_call_name=["pc_find_reaction_image"],
        )
        harness._recover_plaintext_photo_tool_call = AsyncMock(
            return_value=(source, False)
        )
        harness._guard_unread_creative_work_response = lambda _event, text: text
        harness.protect_tts_enhancement_response_blocks = AsyncMock()

        await PrivateCompanionPlugin.normalize_tts_enhancement_response(
            harness, event, response
        )

        self.assertEqual(0, harness._reaction_expression_runtime.get("model_omissions", 0))

    async def test_no_visible_reply_does_not_prepare_reaction_attachment(self) -> None:
        harness = _ReactionHarness(_FakeSmartImageAPI(self.image_path))
        harness.enable_reaction_experiment()
        event = _FakeResultEvent([Plain(" \n")])
        event._private_companion_reaction_expression_intent = {
            "purpose": "回应",
            "emotion": "开心",
            "provider_query": "开心回应",
            "candidate_queries": ["开心回应"],
        }
        prepare = AsyncMock(return_value='{"status":"prepared","decision":"attach"}')
        harness._pc_reaction_expression_impl = prepare

        await PrivateCompanionPlugin.attach_reaction_expression_image_before_send(
            harness,
            event,
        )

        prepare.assert_not_awaited()
        self.assertFalse(
            hasattr(
                event,
                "_private_companion_reaction_expression_pending_attachment",
            )
        )
        self.assertEqual(
            "missing_visible_text",
            harness._reaction_expression_runtime["last_reason"],
        )

    async def test_composed_attachment_settles_only_after_platform_send(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment()
        event = _FakeResultEvent([Plain("完整正文")])
        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)

        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(event))
            event._private_companion_reaction_expression_intent = {
                "purpose": "接住玩笑",
                "emotion": "开心",
                "intensity": 2,
                "provider_query": "开心回应",
                "candidate_queries": ["开心回应"],
            }
            await PrivateCompanionPlugin.attach_reaction_expression_image_before_send(
                harness,
                event,
            )

        pending = event._private_companion_reaction_expression_pending_attachment
        state = ensure_reaction_expression_state(harness.users["10001"])
        self.assertEqual(0, harness.deliveries)
        self.assertFalse(pending["settled"])
        self.assertEqual([], state["recent_images"])
        self.assertEqual(0, sum(isinstance(item, Image) for item in event.get_result().chain))
        self.assertEqual([], event.sent_results)

        await event.send(event.chain_result([Plain("完整正文")]))
        await PrivateCompanionPlugin.settle_reaction_expression_attachment_after_send(
            harness,
            event,
        )

        self.assertTrue(pending["settled"])
        self.assertTrue(pending["sent"])
        self.assertEqual(2, len(event.sent_results))
        self.assertIsInstance(event.sent_results[1].chain[0], Image)
        self.assertEqual("reaction-1", state["recent_images"][-1]["image_id"])

    async def test_same_message_mode_keeps_reaction_in_primary_chain(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment(
            reaction_expression_delivery_mode="same_message"
        )
        event = _FakeResultEvent([Plain("完整正文")])
        event._private_companion_reaction_expression_intent = {
            "purpose": "接住玩笑",
            "emotion": "开心",
            "provider_query": "开心回应",
            "candidate_queries": ["开心回应"],
        }

        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)
        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(event))
            await PrivateCompanionPlugin.attach_reaction_expression_image_before_send(
                harness,
                event,
            )

        pending = event._private_companion_reaction_expression_pending_attachment
        self.assertEqual("same_message", pending["delivery_mode"])
        self.assertEqual(
            1,
            sum(isinstance(item, Image) for item in event.get_result().chain),
        )
        await event.send(event.chain_result(event.get_result().chain))
        await PrivateCompanionPlugin.settle_reaction_expression_attachment_after_send(
            harness,
            event,
        )
        self.assertTrue(pending["sent"])
        self.assertEqual(1, len(event.sent_results))

    async def test_separate_before_does_not_count_image_as_primary_reply(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment(
            reaction_expression_delivery_mode="separate_before"
        )
        event = _FakeResultEvent([Plain("完整正文")])
        event._private_companion_reaction_expression_intent = {
            "purpose": "接住玩笑",
            "emotion": "开心",
            "provider_query": "开心回应",
            "candidate_queries": ["开心回应"],
        }

        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)
        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(event))
            await PrivateCompanionPlugin.attach_reaction_expression_image_before_send(
                harness,
                event,
            )

        pending = event._private_companion_reaction_expression_pending_attachment
        self.assertTrue(pending["settled"])
        self.assertTrue(pending["sent"])
        self.assertTrue(event._has_send_oper)
        self.assertFalse(
            harness._reaction_expression_primary_reply_confirmed(event, pending)
        )
        self.assertEqual(1, len(event.sent_results))
        self.assertEqual(
            0,
            sum(isinstance(item, Image) for item in event.get_result().chain),
        )

    async def test_separate_after_skips_image_when_primary_was_not_sent(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment()
        event = _FakeResultEvent([Plain("完整正文")])
        event._private_companion_reaction_expression_intent = {
            "purpose": "接住玩笑",
            "emotion": "开心",
            "provider_query": "开心回应",
            "candidate_queries": ["开心回应"],
        }

        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)
        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(event))
            await PrivateCompanionPlugin.attach_reaction_expression_image_before_send(
                harness,
                event,
            )
        await PrivateCompanionPlugin.settle_reaction_expression_attachment_after_send(
            harness,
            event,
        )

        pending = event._private_companion_reaction_expression_pending_attachment
        self.assertTrue(pending["settled"])
        self.assertFalse(pending["sent"])
        self.assertEqual("primary_not_delivered", pending["settled_reason"])
        self.assertEqual([], event.sent_results)

    async def test_separate_after_requires_every_primary_text_segment(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment()
        event = _FakeResultEvent([Plain("第一段。第二段。")])
        event._private_companion_reaction_expression_intent = {
            "purpose": "接住玩笑",
            "emotion": "开心",
            "provider_query": "开心回应",
            "candidate_queries": ["开心回应"],
        }
        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)
        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(event))
            await PrivateCompanionPlugin.attach_reaction_expression_image_before_send(
                harness,
                event,
            )

        first = Plain("第一段。")
        second = Plain("第二段。")
        event.set_result(SimpleNamespace(chain=[first, second]))
        await event.send(event.chain_result([first]))
        await PrivateCompanionPlugin.settle_reaction_expression_attachment_after_send(
            harness,
            event,
        )

        pending = event._private_companion_reaction_expression_pending_attachment
        self.assertFalse(pending["sent"])
        self.assertEqual("primary_not_delivered", pending["settled_reason"])
        self.assertEqual(1, len(event.sent_results))

    async def test_segmented_reaction_sends_all_text_bubbles_before_image(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment()
        first = Plain("第一段。")
        second = Plain("第二段。")
        event = _FakeResultEvent([first])
        event._private_companion_reaction_expression_intent = {
            "purpose": "接住玩笑",
            "emotion": "开心",
            "provider_query": "开心回应",
            "candidate_queries": ["开心回应"],
        }
        event._private_companion_reaction_expression_expected_primary_chunks = [
            [first],
            [second],
        ]
        event._private_companion_reaction_expression_segmented_remainder = {
            "chunks": [[second]],
            "previous_segment": "第一段。",
            "started_at": 10.0,
            "started": False,
            "completed": False,
        }
        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)
        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(event))
            await PrivateCompanionPlugin.attach_reaction_expression_image_before_send(
                harness,
                event,
            )

        async def send_remainder(target_event, chunks, **_kwargs):
            for chunk in chunks:
                await target_event.send(target_event.chain_result(chunk))

        harness._send_segmented_llm_chain_remainder = send_remainder
        await event.send(event.chain_result([first]))
        await PrivateCompanionPlugin.release_reaction_expression_segmented_remainder_after_send(
            harness,
            event,
        )
        await PrivateCompanionPlugin.settle_reaction_expression_attachment_after_send(
            harness,
            event,
        )

        pending = event._private_companion_reaction_expression_pending_attachment
        self.assertTrue(pending["sent"])
        self.assertTrue(
            event._private_companion_reaction_expression_segmented_remainder["completed"]
        )
        self.assertEqual(3, len(event.sent_results))
        self.assertEqual("第一段。", event.sent_results[0].chain[0].text)
        self.assertEqual("第二段。", event.sent_results[1].chain[0].text)
        self.assertIsInstance(event.sent_results[2].chain[0], Image)

    async def test_lookup_miss_still_tracks_partial_primary_for_deferred_tts(
        self,
    ) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment()
        harness._owned_reaction_library.find = lambda *_args, **_kwargs: None
        first = Plain("第一段。")
        second = Plain("第二段。")
        event = _FakeResultEvent([first, second])
        event._private_companion_reaction_expression_intent = {
            "purpose": "接住玩笑",
            "emotion": "开心",
            "provider_query": "开心回应",
            "candidate_queries": ["开心回应"],
        }
        event._private_companion_deferred_reaction_tts = {
            "normalized": "第一段。第二段。",
            "fallback_plain": "第一段。第二段。",
            "started_at": 1.0,
        }
        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)
        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(event))
            await PrivateCompanionPlugin.attach_reaction_expression_image_before_send(
                harness,
                event,
            )

        self.assertTrue(
            hasattr(
                event,
                "_private_companion_reaction_expression_delivery_tracker",
            )
        )
        await event.send(event.chain_result([first]))
        await PrivateCompanionPlugin.release_deferred_reaction_tts_after_send(
            harness,
            event,
        )

        self.assertFalse(
            hasattr(event, "_private_companion_deferred_reaction_tts")
        )
        self.assertFalse(
            harness._reaction_expression_primary_reply_confirmed(event)
        )

    async def test_same_message_does_not_record_failed_image_component(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment(
            reaction_expression_delivery_mode="same_message"
        )
        event = _FakeResultEvent([Plain("完整正文")])
        event._private_companion_reaction_expression_intent = {
            "purpose": "接住玩笑",
            "emotion": "开心",
            "provider_query": "开心回应",
            "candidate_queries": ["开心回应"],
        }
        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)
        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(event))
            await PrivateCompanionPlugin.attach_reaction_expression_image_before_send(
                harness,
                event,
            )

        await event.send(event.chain_result([event.get_result().chain[0]]))
        await PrivateCompanionPlugin.settle_reaction_expression_attachment_after_send(
            harness,
            event,
        )

        pending = event._private_companion_reaction_expression_pending_attachment
        self.assertFalse(pending["sent"])
        self.assertEqual("delivery_failed", pending["settled_reason"])

    async def test_same_message_records_delivered_image_when_primary_is_partial(
        self,
    ) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment(
            reaction_expression_delivery_mode="same_message"
        )
        event = _FakeResultEvent([Plain("第一段。第二段。")])
        event._private_companion_reaction_expression_intent = {
            "purpose": "接住玩笑",
            "emotion": "开心",
            "provider_query": "开心回应",
            "candidate_queries": ["开心回应"],
        }
        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)
        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(event))
            await PrivateCompanionPlugin.attach_reaction_expression_image_before_send(
                harness,
                event,
            )

        pending = event._private_companion_reaction_expression_pending_attachment
        image = next(
            item for item in event.get_result().chain if isinstance(item, Image)
        )
        first = Plain("第一段。")
        second = Plain("第二段。")
        event.set_result(SimpleNamespace(chain=[first, second, image]))
        await event.send(event.chain_result([first]))
        await event.send(event.chain_result([image]))
        await PrivateCompanionPlugin.settle_reaction_expression_attachment_after_send(
            harness,
            event,
        )

        self.assertFalse(
            harness._reaction_expression_primary_reply_confirmed(event, pending)
        )
        self.assertTrue(pending["sent"])
        self.assertEqual("delivered", pending["settled_reason"])
        state = ensure_reaction_expression_state(harness.users["10001"])
        self.assertEqual("reaction-1", state["recent_images"][-1]["image_id"])

    async def test_same_message_tracks_image_inside_forward_node(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment(
            reaction_expression_delivery_mode="same_message"
        )
        event = _FakeResultEvent([Plain("完整正文")])
        event._private_companion_reaction_expression_intent = {
            "purpose": "接住玩笑",
            "emotion": "开心",
            "provider_query": "开心回应",
            "candidate_queries": ["开心回应"],
        }
        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)
        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(event))
            await PrivateCompanionPlugin.attach_reaction_expression_image_before_send(
                harness,
                event,
            )

        node_type = type("Node", (), {})
        node = node_type()
        node.content = list(event.get_result().chain)
        event.set_result(SimpleNamespace(chain=[node]))
        await event.send(event.chain_result([node]))
        await PrivateCompanionPlugin.settle_reaction_expression_attachment_after_send(
            harness,
            event,
        )

        pending = event._private_companion_reaction_expression_pending_attachment
        self.assertTrue(pending["sent"])
        self.assertEqual("delivered", pending["settled_reason"])

    async def test_delivery_tracker_can_be_reinstalled_after_cleanup(self) -> None:
        harness = _ReactionHarness(_FakeSmartImageAPI(self.image_path))
        event = _FakeResultEvent([Plain("完整正文")])
        first_pending: dict[str, object] = {}
        harness._install_reaction_expression_delivery_tracker(event, first_pending)
        await event.send(event.chain_result([Plain("第一次")]))

        await PrivateCompanionPlugin.cleanup_reaction_expression_delivery_tracker_after_send(
            harness,
            event,
        )

        self.assertFalse(
            hasattr(
                event,
                "_private_companion_reaction_expression_delivery_tracker",
            )
        )
        second_pending: dict[str, object] = {}
        harness._install_reaction_expression_delivery_tracker(event, second_pending)
        await event.send(event.chain_result([Plain("第二次")]))

        tracker = second_pending["delivery_tracker"]
        self.assertIn(("plain", "第二次"), tracker["successful_signatures"])

    async def test_reaction_tool_marks_collision_and_remembers_sent_image(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api, sent=True)
        event = _FakeEvent()
        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)

        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            payload = json.loads(
                await harness._pc_find_reaction_image_impl(
                    event,
                    query="无语反应图",
                    context="对方又开了同一个玩笑",
                    caption="我给你找了张正合适的。",
                )
            )

        self.assertTrue(payload["success"])
        self.assertTrue(payload["sent"])
        self.assertTrue(harness.last_delivery_kwargs["reaction_image"])
        self.assertEqual(self.image_path, payload["path"])
        self.assertIn(PHOTO_TOOL_SILENT_SENTINEL, payload["final_response_instruction"])
        self.assertNotIn("smart_imagesender_skip_proactive_emoji", event.extras)
        self.assertTrue(event._private_companion_photo_tool_sent)
        snapshot = harness.users["10001"]["last_photo_share_snapshot"]
        self.assertIn("无语", snapshot["caption"])
        self.assertIn("标签和当前吐槽语境一致", snapshot["motive"])
        self.assertTrue(harness.saved)
        lookup_context = api.calls[0]["context"]
        self.assertIn("对方又开了同一个玩笑", lookup_context)
        self.assertIn("Bot当前情境", lookup_context)
        self.assertIn("情绪轻松", lookup_context)

    async def test_failed_delivery_does_not_create_recent_photo_snapshot(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api, sent=False)
        event = _FakeEvent()
        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)

        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            payload = json.loads(
                await harness._pc_find_reaction_image_impl(
                    event,
                    query="无语反应图",
                    caption="这张应该很贴切。",
                )
            )

        self.assertEqual("delivery_failed", payload["status"])
        self.assertFalse(payload["success"])
        self.assertFalse(payload["sent"])
        self.assertNotIn("last_photo_share_snapshot", harness.users["10001"])

    def test_prompt_prefers_library_for_existing_meme_and_generation_for_new_sticker(self) -> None:
        harness = _ReactionHarness(_FakeSmartImageAPI(self.image_path))
        harness.enabled = True
        harness.enable_photo_text_action = True
        harness.natural_language_photo_generation_mode = "tool_first"

        module = SimpleNamespace(get_smart_imagechat_api=lambda: harness.api)
        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            instruction = harness._photo_generation_tool_instruction()

        self.assertIn("优先使用 `pc_find_reaction_image`", instruction)
        self.assertIn("不能替代、缩短或省略正文", instruction)
        self.assertNotIn("图片是否优于文字", instruction)
        self.assertIn("明确要求“生成/画/制作”", instruction)
        parsed = harness._plaintext_tool_call_from_object(
            {"name": "pc_find_reaction_image", "parameters": {"query": "无语"}}
        )
        self.assertEqual("pc_find_reaction_image", parsed["name"])

    def test_library_prompt_does_not_depend_on_photo_generation_switch(self) -> None:
        harness = _ReactionHarness(_FakeSmartImageAPI(self.image_path))
        harness.enabled = True
        harness.enable_photo_text_action = False
        harness.natural_language_photo_generation_mode = "off"
        module = SimpleNamespace(get_smart_imagechat_api=lambda: harness.api)

        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            instruction = harness._photo_generation_tool_instruction()

        self.assertIn("pc_find_reaction_image", instruction)
        self.assertNotIn("普通场景/物件/风景", instruction)

    async def test_experiment_is_off_by_default_without_provider_lookup(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)

        payload = json.loads(
            await harness._pc_reaction_expression_impl(
                _FakeEvent(),
                purpose="轻松回应",
                emotion="无语",
            )
        )

        self.assertEqual("skip", payload["decision"])
        self.assertEqual("experiment_disabled", payload["skip_reason"])
        self.assertFalse(payload["sent"])
        self.assertEqual([], api.calls)

    async def test_structured_intent_uses_low_latency_lookup_and_persists_receipt(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment()
        event = _FakeEvent()
        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)

        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(event))
            payload = json.loads(
                await harness._pc_reaction_expression_impl(
                    event,
                    purpose="轻轻吐槽",
                    emotion="无语但亲近",
                    intensity=2,
                    candidate_queries="无语反应图；轻松吐槽表情包",
                )
            )

        self.assertEqual("send", payload["decision"])
        self.assertTrue(payload["sent"])
        self.assertEqual(1, len(api.calls))
        self.assertIsNone(api.calls[0]["event"])
        self.assertIn("沟通用途：轻轻吐槽", api.calls[0]["context"])
        state = harness.users["10001"]["reaction_expression"]
        self.assertEqual("reaction-1", state["last_image_id"])
        self.assertEqual("reaction-1", state["feedback_target"]["image_id"])
        self.assertEqual("sent", state["recent_outcomes"][-1]["status"])
        self.assertEqual("reaction_expression_experiment", harness.users["10001"]["last_photo_share_snapshot"]["reason"])

    async def test_proactive_reaction_passes_current_user_asset_preferences(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment()
        state = ensure_reaction_expression_state(harness.users["10001"])
        state["asset_preferences"] = {
            "reaction-1": {
                "score": 6,
                "positive_count": 2,
                "negative_count": 0,
                "intent_scores": {},
            }
        }
        event = _FakeEvent()

        self.assertTrue(await harness._preauthorize_reaction_expression_prompt(event))
        payload = json.loads(
            await harness._pc_reaction_expression_impl(
                event,
                purpose="轻轻吐槽",
                emotion="无语但亲近",
                intensity=2,
            )
        )

        self.assertTrue(payload["sent"])
        self.assertEqual(1, len(harness._owned_reaction_library.selection_calls))
        selection = harness._owned_reaction_library.selection_calls[0]
        self.assertTrue(selection["signature"])
        self.assertEqual(
            selection["signature"],
            selection["preferences"]["intent_signature"],
        )
        self.assertEqual(
            "reaction-1",
            selection["preferences"]["assets"][0]["key"],
        )

    async def test_attach_only_prepares_without_sending_and_settles_after_confirmation(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment()
        event = _FakeEvent()
        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)

        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(event))
            payload = json.loads(
                await harness._pc_reaction_expression_impl(
                    event,
                    purpose="接住玩笑",
                    emotion="开心",
                    attach_only=True,
                )
            )

        state = ensure_reaction_expression_state(harness.users["10001"])
        scoped = state["scopes"][event.unified_msg_origin]
        pending = event._private_companion_reaction_expression_pending_attachment
        self.assertEqual("prepared", payload["status"])
        self.assertEqual("attach", payload["decision"])
        self.assertFalse(payload["sent"])
        self.assertEqual(0, harness.deliveries)
        self.assertEqual([], state["recent_images"])
        self.assertTrue(scoped["reservation"])
        self.assertTrue(state["pending_images"])
        self.assertFalse(pending["settled"])

        settled = await harness._settle_reaction_expression_attachment_data(
            pending,
            sent=True,
            reason="platform_sent",
        )

        self.assertTrue(settled)
        self.assertTrue(pending["settled"])
        self.assertTrue(pending["sent"])
        self.assertEqual({}, scoped["reservation"])
        self.assertEqual({}, state["pending_images"])
        self.assertEqual("reaction-1", state["recent_images"][-1]["image_id"])
        self.assertEqual(1, harness._reaction_expression_runtime["sent"])
        self.assertEqual(0, harness.deliveries)

    async def test_attach_only_failure_releases_reservations_without_settling_send(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment()
        event = _FakeEvent()
        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)

        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(event))
            payload = json.loads(
                await harness._pc_reaction_expression_impl(
                    event,
                    purpose="接住玩笑",
                    emotion="开心",
                    attach_only=True,
                )
            )

        pending = event._private_companion_reaction_expression_pending_attachment
        settled = await harness._settle_reaction_expression_attachment_data(
            pending,
            sent=False,
            reason="append_failed",
        )
        state = ensure_reaction_expression_state(harness.users["10001"])
        scoped = state["scopes"][event.unified_msg_origin]

        self.assertEqual("prepared", payload["status"])
        self.assertTrue(settled)
        self.assertTrue(pending["settled"])
        self.assertFalse(pending["sent"])
        self.assertEqual({}, scoped["reservation"])
        self.assertEqual({}, state["pending_images"])
        self.assertEqual([], state["recent_images"])
        self.assertEqual("append_failed", state["recent_outcomes"][-1]["reason"])
        self.assertEqual(0, harness.deliveries)
        self.assertNotIn("smart_imagesender_skip_proactive_emoji", event.extras)

    async def test_non_low_latency_lookup_keeps_real_event(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment(reaction_expression_low_latency_mode=False)
        event = _FakeEvent()
        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)

        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(event))
            payload = json.loads(
                await harness._pc_reaction_expression_impl(
                    event,
                    purpose="回应",
                    emotion="开心",
                )
            )

        self.assertTrue(payload["sent"])
        self.assertIsNone(api.calls[0]["event"])

    async def test_scope_probability_and_send_gates_do_not_query_provider(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)

        group_harness = _ReactionHarness(api)
        group_harness.enable_reaction_experiment()
        probability_harness = _ReactionHarness(api)
        probability_harness.enable_reaction_experiment(
            reaction_expression_trigger_probability=0.0
        )
        send_harness = _ReactionHarness(api)
        send_harness.enable_reaction_experiment()

        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            group_event = _FakeGroupEvent()
            self.assertFalse(
                await group_harness._preauthorize_reaction_expression_prompt(group_event)
            )
            group_payload = json.loads(
                await group_harness._pc_reaction_expression_impl(
                    group_event, purpose="群聊回应"
                )
            )
            probability_event = _FakeEvent()
            self.assertFalse(
                await probability_harness._preauthorize_reaction_expression_prompt(
                    probability_event
                )
            )
            probability_payload = json.loads(
                await probability_harness._pc_reaction_expression_impl(
                    probability_event, purpose="私聊回应"
                )
            )
            send_payload = json.loads(
                await send_harness._pc_reaction_expression_impl(
                    _FakeEvent(), purpose="私聊回应", send=False
                )
            )

        self.assertEqual("group_disabled", group_payload["skip_reason"])
        self.assertEqual("probability", probability_payload["skip_reason"])
        self.assertEqual("send_disabled", send_payload["skip_reason"])
        self.assertEqual([], api.calls)

    async def test_cooldown_blocks_second_lookup(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment(reaction_expression_cooldown_seconds=180)
        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)

        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            first_event = _FakeEvent()
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(first_event))
            first = json.loads(
                await harness._pc_reaction_expression_impl(
                    first_event, purpose="第一次回应", emotion="开心"
                )
            )
            second_event = _FakeEvent()
            self.assertFalse(await harness._preauthorize_reaction_expression_prompt(second_event))
            second = json.loads(
                await harness._pc_reaction_expression_impl(
                    second_event, purpose="另一个回应", emotion="疑惑"
                )
            )

        self.assertTrue(first["sent"])
        self.assertEqual("cooldown", second["skip_reason"])
        self.assertEqual(1, len(api.calls))
        self.assertEqual(1, harness.deliveries)

    async def test_same_image_is_deduplicated_before_second_delivery(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment(reaction_expression_cooldown_seconds=0)
        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)

        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            first_event = _FakeEvent()
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(first_event))
            first = json.loads(
                await harness._pc_reaction_expression_impl(
                    first_event, purpose="庆祝", emotion="开心"
                )
            )
            second_event = _FakeEvent()
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(second_event))
            second = json.loads(
                await harness._pc_reaction_expression_impl(
                    second_event, purpose="安慰", emotion="温柔"
                )
            )

        self.assertTrue(first["sent"])
        self.assertEqual("duplicate_image", second["skip_reason"])
        self.assertEqual(2, len(api.calls))
        self.assertEqual(1, harness.deliveries)

    async def test_cooldown_is_scoped_to_the_current_conversation(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment(
            reaction_expression_cooldown_seconds=180,
            reaction_expression_group_enabled=True,
        )
        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)
        private_event = _FakeEvent()

        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            self.assertTrue(
                await harness._preauthorize_reaction_expression_prompt(private_event)
            )
            private_payload = json.loads(
                await harness._pc_reaction_expression_impl(
                    private_event,
                    purpose="私聊回应",
                    emotion="开心",
                )
            )
            group_event = _FakeGroupEvent()
            group_authorized = await harness._preauthorize_reaction_expression_prompt(
                group_event
            )

        self.assertTrue(private_payload["sent"])
        self.assertTrue(group_authorized)

    async def test_group_reaction_state_does_not_create_private_user(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment(reaction_expression_group_enabled=True)
        group_event = _FakeGroupEvent()

        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=harness._owned_reaction_library,
        ):
            self.assertTrue(
                await harness._preauthorize_reaction_expression_prompt(group_event)
            )
            payload = json.loads(
                await harness._pc_reaction_expression_impl(
                    group_event,
                    purpose="群聊回应",
                    emotion="开心",
                )
            )

        self.assertTrue(payload["sent"])
        self.assertEqual({"10001": {}}, harness.users)
        states = harness.data.get("reaction_expression_group_states")
        self.assertIsInstance(states, dict)
        self.assertTrue(states)
        self.assertTrue(
            all("20001" in key and "sender:10001" in key for key in states)
        )

    async def test_positive_and_negative_feedback_update_preference(self) -> None:
        module_api = _FakeSmartImageAPI(self.image_path)
        module = SimpleNamespace(get_smart_imagechat_api=lambda: module_api)

        positive_harness = _ReactionHarness(module_api)
        positive_harness.enable_reaction_experiment()
        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            positive_event = _FakeEvent()
            self.assertTrue(
                await positive_harness._preauthorize_reaction_expression_prompt(
                    positive_event
                )
            )
            await positive_harness._pc_reaction_expression_impl(
                positive_event, purpose="回应", emotion="开心"
            )
        positive = positive_harness._apply_reaction_expression_feedback(
            positive_harness.users["10001"], "刚才那张表情包很合适"
        )

        negative_api = _FakeSmartImageAPI(self.image_path, image_id="reaction-2")
        negative_module = SimpleNamespace(get_smart_imagechat_api=lambda: negative_api)
        negative_harness = _ReactionHarness(negative_api)
        negative_harness.enable_reaction_experiment()
        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=negative_module,
        ):
            negative_event = _FakeEvent()
            self.assertTrue(
                await negative_harness._preauthorize_reaction_expression_prompt(
                    negative_event
                )
            )
            await negative_harness._pc_reaction_expression_impl(
                negative_event, purpose="回应", emotion="疑惑"
            )
        negative = negative_harness._apply_reaction_expression_feedback(
            negative_harness.users["10001"], "刚才那张图很尴尬，别再发表情包了"
        )

        self.assertEqual("positive", positive["signal"])
        self.assertEqual(1, positive["score"])
        self.assertEqual("negative", negative["signal"])
        self.assertEqual(-1, negative["score"])
        self.assertEqual("reaction-2", negative["image_id"])
        negative_state = negative_harness.users["10001"]["reaction_expression"]
        self.assertEqual(1, negative_state["preference"]["negative_count"])
        self.assertEqual("negative", negative_state["feedback_events"][-1]["signal"])

    async def test_prompt_preauthorization_draws_probability_once_and_tool_reuses_it(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment(
            reaction_expression_trigger_probability=0.5
        )
        harness.enabled = True
        event = _FakeEvent()
        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)

        with (
            patch(
                "astrbot_plugin_private_companion.llm_tool_actions.random.random",
                return_value=0.1,
            ) as random_draw,
            patch(
                "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
                return_value=module,
            ),
        ):
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(event))
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(event))
            instruction = harness._photo_generation_tool_instruction(
                include_spontaneous=True,
                spontaneous_only=True,
            )
            payload = json.loads(
                await harness._pc_reaction_expression_impl(
                    event,
                    purpose="轻松回应",
                    emotion="开心",
                )
            )
            self.assertFalse(await harness._preauthorize_reaction_expression_prompt(event))
            event.extras["private_companion_reaction_expression_authorization"][
                "expires_at"
            ] = 0
            self.assertFalse(await harness._preauthorize_reaction_expression_prompt(event))

        self.assertTrue(payload["sent"])
        self.assertEqual(1, random_draw.call_count)
        self.assertIn("<pc_reaction_expression>", instruction)
        self.assertIn("本轮已经由插件完成概率抽样", instruction)
        self.assertIn("不要再次按概率决定", instruction)
        self.assertIn('"purpose":"轻吐槽","emotion":"无语","intensity":2', instruction)
        self.assertNotIn("spontaneous=true", instruction)
        self.assertNotIn("pc_generate_photo", instruction)

    async def test_high_frequency_model_omission_builds_a_generic_fallback_intent(self) -> None:
        harness = _ReactionHarness(_FakeSmartImageAPI(self.image_path))
        harness.enable_reaction_experiment(
            reaction_expression_trigger_probability=1.0
        )
        event = _FakeEvent()
        event.message_str = "普通闲聊"
        module = SimpleNamespace(get_smart_imagechat_api=lambda: harness.api)
        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(event))
        source = "这是一条轻松的完整回复。"
        response = SimpleNamespace(
            completion_text=source,
            result_chain=None,
            tools_call_name=[],
        )
        harness._recover_plaintext_photo_tool_call = AsyncMock(
            return_value=(source, False)
        )
        harness._guard_unread_creative_work_response = lambda _event, text: text
        harness.protect_tts_enhancement_response_blocks = AsyncMock()

        await PrivateCompanionPlugin.normalize_tts_enhancement_response(
            harness,
            event,
            response,
        )

        self.assertEqual(
            "日常回应",
            event._private_companion_reaction_expression_intent["purpose"],
        )
        self.assertEqual(1, harness._reaction_expression_runtime["local_fallbacks"])
        self.assertIn(
            "当前触发概率为 100%",
            harness._photo_generation_tool_instruction(
                include_spontaneous=True,
                spontaneous_only=True,
            ),
        )

    async def test_semantic_reaction_trigger_bypasses_probability_once(self) -> None:
        """High-confidence local intent can offer expression without another model call."""
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment(reaction_expression_trigger_probability=0.2)
        harness.users["10001"]["intent_profile"] = {
            "intent": "play",
            "emotion": "light",
            "emotion_event": "praise",
            "emotion_intensity": 42,
            "confidence": 0.86,
            "emotion_confidence": 0.82,
            "source": "play_rule",
        }
        event = _FakeEvent()
        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)

        with (
            patch(
                "astrbot_plugin_private_companion.llm_tool_actions.random.random",
                return_value=0.99,
            ) as random_draw,
            patch(
                "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
                return_value=module,
            ),
        ):
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(event))

        authorization = event.extras[
            "private_companion_reaction_expression_authorization"
        ]
        self.assertEqual("strong_emotion", authorization["trigger_mode"])
        self.assertEqual(1.0, authorization["gate_probability"])
        self.assertEqual(0, random_draw.call_count)

    async def test_semantic_switch_off_keeps_the_normal_probability_path(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment(reaction_expression_trigger_probability=1.0)
        harness.reaction_expression_semantic_trigger_enabled = False
        event = _FakeEvent()
        event.message_str = "哈哈，你真厉害"
        harness.users["10001"]["intent_profile"] = {
            "intent": "play",
            "emotion_event": "praise",
            "emotion_intensity": 60,
            "confidence": 0.95,
            "emotion_confidence": 0.9,
            "emotion_target": "bot",
            "text": event.message_str,
        }
        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)

        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(event))

        authorization = event.extras[
            "private_companion_reaction_expression_authorization"
        ]
        self.assertEqual("probability", authorization["trigger_mode"])
        self.assertEqual(1.0, authorization["gate_probability"])

    async def test_low_confidence_profile_keeps_the_normal_probability_path(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment(reaction_expression_trigger_probability=1.0)
        event = _FakeEvent()
        event.message_str = "也许是在开玩笑吧"
        harness.users["10001"]["intent_profile"] = {
            "intent": "play",
            "emotion_event": "praise",
            "emotion_intensity": 60,
            "confidence": 0.45,
            "emotion_confidence": 0.5,
            "emotion_target": "bot",
            "text": event.message_str,
        }
        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)

        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(event))

        authorization = event.extras[
            "private_companion_reaction_expression_authorization"
        ]
        self.assertEqual("probability", authorization["trigger_mode"])
        self.assertEqual(1.0, authorization["gate_probability"])

    async def test_boundary_diagnostic_and_factual_profiles_only_disable_semantic_bypass(self) -> None:
        profiles = {
            "boundary": {
                "intent": "play",
                "source": "durable_boundary_rule",
                "boundary_durable": True,
            },
            "diagnostic": {
                "intent": "play",
                "source": "diagnostic_skip",
            },
            "factual": {
                "intent": "help",
                "source": "help_rule",
            },
        }
        for label, profile in profiles.items():
            with self.subTest(profile=label):
                api = _FakeSmartImageAPI(self.image_path)
                harness = _ReactionHarness(api)
                harness.enable_reaction_experiment(
                    reaction_expression_trigger_probability=1.0
                )
                event = _FakeEvent()
                event.message_str = f"{label} current message"
                harness.users["10001"]["intent_profile"] = {
                    **profile,
                    "emotion_event": "praise",
                    "emotion_intensity": 60,
                    "confidence": 0.95,
                    "emotion_confidence": 0.9,
                    "emotion_target": "bot",
                    "text": event.message_str,
                }
                module = SimpleNamespace(get_smart_imagechat_api=lambda: api)
                with patch(
                    "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
                    return_value=module,
                ):
                    self.assertTrue(
                        await harness._preauthorize_reaction_expression_prompt(event)
                    )
                authorization = event.extras[
                    "private_companion_reaction_expression_authorization"
                ]
                self.assertEqual("probability", authorization["trigger_mode"])
                self.assertEqual(1.0, authorization["gate_probability"])

    async def test_semantic_trigger_ignores_a_stale_profile_from_the_previous_turn(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment(reaction_expression_trigger_probability=0.2)
        harness.users["10001"]["intent_profile"] = {
            "intent": "play",
            "emotion_event": "praise",
            "emotion_intensity": 60,
            "confidence": 0.95,
            "emotion_confidence": 0.9,
            "emotion_target": "bot",
            "text": "上一轮是在开玩笑",
        }
        event = _FakeEvent()
        event.message_str = "这轮只是问一个普通事实"
        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)

        with (
            patch(
                "astrbot_plugin_private_companion.llm_tool_actions.random.random",
                return_value=0.99,
            ) as random_draw,
            patch(
                "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
                return_value=module,
            ),
        ):
            self.assertFalse(await harness._preauthorize_reaction_expression_prompt(event))

        authorization = event.extras[
            "private_companion_reaction_expression_authorization"
        ]
        self.assertEqual("probability", authorization["trigger_mode"])
        self.assertEqual("probability", authorization["reason"])
        self.assertEqual(1, random_draw.call_count)

    async def test_semantic_authorization_keeps_its_profile_when_a_later_turn_overwrites_user_state(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment(reaction_expression_trigger_probability=0.2)
        event = _FakeEvent()
        event.message_str = "你这次做得真好"
        harness.users["10001"]["intent_profile"] = {
            "intent": "chat",
            "emotion_event": "praise",
            "emotion_intensity": 42,
            "confidence": 0.86,
            "emotion_confidence": 0.82,
            "emotion_target": "bot",
            "source": "praise_rule",
            "text": event.message_str,
        }
        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)

        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(event))

        authorization = event.extras[
            "private_companion_reaction_expression_authorization"
        ]
        harness.users["10001"]["intent_profile"] = {
            "intent": "intimacy",
            "emotion_event": "comfort",
            "emotion_intensity": 80,
            "confidence": 0.98,
            "emotion_confidence": 0.98,
            "emotion_target": "bot",
            "source": "later_turn",
            "text": "后到的另一条消息",
        }

        fallback = harness._reaction_expression_local_fallback_intent(
            event,
            "嗯，这句夸奖我收下啦。",
            authorization,
        )
        lookup_context = harness._reaction_expression_lookup_context(
            harness.users["10001"],
            fallback,
            profile_snapshot=authorization["profile_snapshot"],
        )

        self.assertEqual("回应夸奖", fallback["purpose"])
        self.assertIn("你这次做得真好", lookup_context)
        self.assertNotIn("后到的另一条消息", lookup_context)

    async def test_zero_probability_remains_explicit_opt_out_for_semantic_signal(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment(reaction_expression_trigger_probability=0.0)
        harness.users["10001"]["intent_profile"] = {
            "intent": "intimacy",
            "emotion_event": "comfort",
            "emotion_intensity": 60,
            "confidence": 0.95,
            "emotion_confidence": 0.9,
            "source": "intimacy_rule",
        }
        event = _FakeEvent()
        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)

        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            self.assertFalse(await harness._preauthorize_reaction_expression_prompt(event))

        authorization = event.extras[
            "private_companion_reaction_expression_authorization"
        ]
        self.assertEqual("probability", authorization["trigger_mode"])
        self.assertEqual("probability", authorization["reason"])

    async def test_explicit_reaction_opt_out_blocks_semantic_trigger(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment(reaction_expression_trigger_probability=1.0)
        harness.users["10001"]["intent_profile"] = {
            "intent": "play",
            "emotion_event": "praise",
            "emotion_intensity": 60,
            "confidence": 0.95,
            "emotion_confidence": 0.9,
        }
        event = _FakeEvent()
        event.message_str = "别再发表情包了"
        module = SimpleNamespace(get_smart_image_api=lambda: api)
        with patch("astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library", return_value=module):
            self.assertFalse(await harness._preauthorize_reaction_expression_prompt(event))
        authorization = event.extras["private_companion_reaction_expression_authorization"]
        self.assertEqual("explicit_opt_out", authorization["trigger_mode"])
        self.assertEqual("explicit_opt_out", authorization["reason"])

    async def test_persisted_opt_out_blocks_later_automatic_trigger(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment()
        user = harness.users["10001"]
        state = ensure_reaction_expression_state(user)
        sync_reaction_expression_auto_preference(
            state,
            "别再发表情包了",
            now=10.0,
            scope_key="default:FriendMessage:10001",
        )
        event = _FakeEvent()
        event.message_str = "你这次做得不错"
        user["intent_profile"] = {
            "intent": "play",
            "emotion_event": "praise",
            "emotion_intensity": 60,
            "confidence": 0.9,
            "emotion_confidence": 0.86,
            "emotion_target": "bot",
            "text": event.message_str,
        }

        self.assertFalse(await harness._preauthorize_reaction_expression_prompt(event))
        authorization = event.extras[
            "private_companion_reaction_expression_authorization"
        ]
        self.assertEqual("explicit_opt_out", authorization["trigger_mode"])

    async def test_low_latency_cache_reuses_lookup_across_users(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment(reaction_expression_cooldown_seconds=0)
        first_event = _FakeEvent()
        second_event = _FakeSecondUserEvent()
        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)

        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            self.assertTrue(
                await harness._preauthorize_reaction_expression_prompt(first_event)
            )
            first = json.loads(
                await harness._pc_reaction_expression_impl(
                    first_event,
                    purpose="轻松回应",
                    emotion="开心",
                    candidate_queries="开心回应；轻松开心",
                )
            )
            self.assertTrue(
                await harness._preauthorize_reaction_expression_prompt(second_event)
            )
            second = json.loads(
                await harness._pc_reaction_expression_impl(
                    second_event,
                    purpose="轻松回应",
                    emotion="开心",
                    candidate_queries="开心回应；轻松开心",
                )
            )

        self.assertTrue(first["sent"])
        self.assertTrue(second["sent"])
        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["cache_hit"])
        self.assertEqual(1, len(api.calls))
        self.assertEqual(2, harness.deliveries)
        runtime = harness._reaction_expression_runtime
        self.assertEqual(1, runtime["lookups"])
        self.assertEqual(1, runtime["cache_hits"])
        self.assertEqual(2, runtime["sent"])

    async def test_in_progress_lookup_blocks_a_second_send_in_same_conversation(self) -> None:
        api = _BlockingSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment(reaction_expression_cooldown_seconds=0)
        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)
        first_event = _FakeEvent()

        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            self.assertTrue(
                await harness._preauthorize_reaction_expression_prompt(first_event)
            )
            first_task = asyncio.create_task(
                harness._pc_reaction_expression_impl(
                    first_event,
                    purpose="第一次回应",
                    emotion="开心",
                )
            )
            self.assertTrue(await asyncio.to_thread(api.started.wait, 1))
            second_event = _FakeEvent()
            self.assertFalse(
                await harness._preauthorize_reaction_expression_prompt(second_event)
            )
            second = json.loads(
                await harness._pc_reaction_expression_impl(
                    second_event,
                    purpose="第二次回应",
                    emotion="疑惑",
                )
            )
            api.release.set()
            first = json.loads(await asyncio.wait_for(first_task, timeout=1))

        self.assertTrue(first["sent"])
        self.assertEqual("in_progress", second["skip_reason"])
        self.assertEqual(1, len(api.calls))
        self.assertEqual(1, harness.deliveries)

    async def test_stale_lookup_cannot_clear_or_use_a_replacement_reservation(self) -> None:
        api = _BlockingSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment(reaction_expression_cooldown_seconds=0)
        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)
        event = _FakeEvent()

        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(event))
            task = asyncio.create_task(
                harness._pc_reaction_expression_impl(
                    event,
                    purpose="慢查询回应",
                    emotion="开心",
                )
            )
            self.assertTrue(await asyncio.to_thread(api.started.wait, 1))
            state = ensure_reaction_expression_state(harness.users["10001"])
            scoped = state["scopes"][event.unified_msg_origin]
            scoped["reservation"] = {
                "token": "replacement-token",
                "signature": "replacement-intent",
                "at": 1.0,
            }
            api.release.set()
            payload = json.loads(await asyncio.wait_for(task, timeout=1))

        self.assertEqual("reservation_lost", payload["skip_reason"])
        self.assertEqual(0, harness.deliveries)
        self.assertEqual(
            "replacement-token",
            scoped["reservation"]["token"],
        )

    async def test_same_path_is_deduplicated_when_library_id_changes(self) -> None:
        api = _FakeSmartImageAPI(self.image_path, image_id="reaction-old")
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment(
            reaction_expression_cooldown_seconds=0,
            reaction_expression_low_latency_mode=False,
        )
        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)

        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            first_event = _FakeEvent()
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(first_event))
            first = json.loads(
                await harness._pc_reaction_expression_impl(
                    first_event,
                    purpose="庆祝",
                    emotion="开心",
                )
            )
            api.image_id = "reaction-new"
            second_event = _FakeEvent()
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(second_event))
            second = json.loads(
                await harness._pc_reaction_expression_impl(
                    second_event,
                    purpose="安慰",
                    emotion="温柔",
                )
            )

        self.assertTrue(first["sent"])
        self.assertEqual("duplicate_image", second["skip_reason"])
        self.assertEqual(2, len(api.calls))
        self.assertEqual(1, harness.deliveries)

    def test_media_request_detection_respects_reaction_boundaries(self) -> None:
        harness = _ReactionHarness(_FakeSmartImageAPI(self.image_path))

        self.assertFalse(
            harness._photo_generation_instruction_matches("别再发表情包了")
        )
        self.assertFalse(
            harness._photo_generation_instruction_matches("表情包以后别再发了")
        )
        self.assertFalse(
            harness._photo_generation_instruction_matches("这个表情包是什么意思")
        )
        self.assertTrue(
            harness._photo_generation_instruction_matches("请发个表情包")
        )
        self.assertTrue(
            harness._photo_generation_instruction_matches("帮我生成一张新表情包")
        )
        self.assertTrue(
            harness._photo_generation_instruction_matches(
                "别再自动发表情包了，不过这次给我来一张表情包"
            )
        )

    def test_lookup_cache_key_separates_scope_and_catalog_revision(self) -> None:
        provider = object()
        private_key = LlmToolActionsMixin._reaction_expression_lookup_cache_key(
            provider, "开心", "当前语境", True, "private", "revision-1"
        )
        group_key = LlmToolActionsMixin._reaction_expression_lookup_cache_key(
            provider, "开心", "当前语境", True, "group", "revision-1"
        )
        edited_key = LlmToolActionsMixin._reaction_expression_lookup_cache_key(
            provider, "开心", "当前语境", True, "private", "revision-2"
        )

        self.assertNotEqual(private_key, group_key)
        self.assertNotEqual(private_key, edited_key)

    def test_selection_revision_is_stable_and_changes_with_user_affinity(self) -> None:
        first = {
            "intent_signature": "intent-happy",
            "assets": [
                {"key": "asset-b", "score": -2, "intent_score": -1},
                {"key": "asset-a", "score": 5, "intent_score": 3},
            ],
        }
        reordered = {
            "intent_signature": "intent-happy",
            "assets": [
                {"key": "asset-a", "score": 5, "intent_score": 3},
                {"key": "asset-b", "score": -2, "intent_score": -1},
            ],
        }
        changed = {
            "intent_signature": "intent-happy",
            "assets": [
                {"key": "asset-a", "score": -5, "intent_score": -3},
                {"key": "asset-b", "score": 2, "intent_score": 1},
            ],
        }

        first_revision = LlmToolActionsMixin._reaction_expression_selection_revision(
            first,
            "intent-happy",
        )
        reordered_revision = LlmToolActionsMixin._reaction_expression_selection_revision(
            reordered,
            "intent-happy",
        )
        changed_revision = LlmToolActionsMixin._reaction_expression_selection_revision(
            changed,
            "intent-happy",
        )

        self.assertEqual(first_revision, reordered_revision)
        self.assertNotEqual(first_revision, changed_revision)

    def test_first_group_opt_out_creates_only_the_needed_feedback_user(self) -> None:
        harness = _ReactionHarness(_FakeSmartImageAPI(self.image_path))
        harness.users.clear()

        self.assertIsNone(
            harness._reaction_expression_feedback_user(
                "20002",
                "普通群聊消息",
                create_for_opt_out=True,
            )
        )
        self.assertEqual({}, harness.users)

        user = harness._reaction_expression_feedback_user(
            "20002",
            "表情包以后别再发了",
            create_for_opt_out=True,
        )
        self.assertIsInstance(user, dict)
        feedback = harness._apply_reaction_expression_feedback(
            user,
            "表情包以后别再发了",
            scope_key="default:GroupMessage:20001",
        )

        self.assertEqual("disabled", feedback["auto_preference"])
        self.assertTrue(
            reaction_expression_auto_disabled(
                ensure_reaction_expression_state(user),
                "default:GroupMessage:20001",
            )
        )

    def test_request_tool_scope_hides_unavailable_media_actions(self) -> None:
        authorized = SimpleNamespace(
            func_tool=_FakeToolSet(
                "pc_generate_photo",
                "pc_find_reaction_image",
                "pc_send_current_media",
                "safe_tool",
            )
        )
        removed = LlmToolActionsMixin._scope_reaction_media_tools_for_request(
            authorized,
            explicit_media_request=False,
            reaction_authorized=True,
            reaction_evaluated=True,
        )
        self.assertEqual(
            ["pc_find_reaction_image", "pc_generate_photo", "pc_send_current_media"],
            removed,
        )
        self.assertEqual(
            ["safe_tool"],
            [tool.name for tool in authorized.func_tool.tools],
        )

        denied = SimpleNamespace(
            func_tool=_FakeToolSet(
                "pc_generate_photo",
                "pc_find_reaction_image",
                "pc_send_current_media",
                "safe_tool",
            )
        )
        removed = LlmToolActionsMixin._scope_reaction_media_tools_for_request(
            denied,
            explicit_media_request=False,
            reaction_authorized=False,
            reaction_evaluated=True,
        )
        self.assertEqual(
            ["pc_find_reaction_image", "pc_generate_photo", "pc_send_current_media"],
            removed,
        )
        self.assertEqual(
            ["safe_tool"],
            [tool.name for tool in denied.func_tool.tools],
        )

        explicit = SimpleNamespace(
            func_tool=_FakeToolSet(
                "pc_generate_photo",
                "pc_find_reaction_image",
                "pc_send_current_media",
                "safe_tool",
            )
        )
        removed = LlmToolActionsMixin._scope_reaction_media_tools_for_request(
            explicit,
            explicit_media_request=True,
            reaction_authorized=True,
            reaction_evaluated=True,
        )
        self.assertEqual([], removed)
        self.assertEqual(
            ["pc_generate_photo", "pc_find_reaction_image", "pc_send_current_media", "safe_tool"],
            [tool.name for tool in explicit.func_tool.tools],
        )

        ordinary = SimpleNamespace(
            func_tool=_FakeToolSet(
                "pc_generate_photo",
                "pc_find_reaction_image",
                "pc_send_current_media",
                "safe_tool",
            )
        )
        removed = LlmToolActionsMixin._scope_reaction_media_tools_for_request(
            ordinary,
            explicit_media_request=False,
            reaction_authorized=False,
            reaction_evaluated=False,
        )
        self.assertEqual(["pc_send_current_media"], removed)
        self.assertEqual(
            ["pc_generate_photo", "pc_find_reaction_image", "safe_tool"],
            [tool.name for tool in ordinary.func_tool.tools],
        )

    def test_failed_media_followup_keeps_current_media_tool_available(self) -> None:
        message = (
            "我重启了，你再试试发过来，不要生成新图，"
            "把你刚刚生成的没发出来的发给我就可以了"
        )
        request = SimpleNamespace(
            func_tool=_FakeToolSet(
                "pc_generate_photo",
                "pc_find_reaction_image",
                "pc_send_current_media",
                "safe_tool",
            )
        )

        explicit_media_request = (
            LlmToolActionsMixin._current_media_delivery_instruction_matches(message)
        )
        removed = LlmToolActionsMixin._scope_reaction_media_tools_for_request(
            request,
            explicit_media_request=explicit_media_request,
            reaction_authorized=False,
            reaction_evaluated=False,
        )

        self.assertTrue(explicit_media_request)
        self.assertEqual([], removed)
        self.assertIsNotNone(request.func_tool.get_tool("pc_send_current_media"))

    async def test_denied_authorization_cannot_fall_through_legacy_media_tools(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment(
            reaction_expression_trigger_probability=0.0
        )
        event = _FakeEvent()
        event.message_str = "普通闲聊"
        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)
        legacy_lookup = AsyncMock(return_value='{"status":"unexpected"}')
        generated_photo = AsyncMock(return_value='{"status":"unexpected"}')
        harness._pc_find_reaction_image_impl = legacy_lookup
        harness._pc_generate_photo_impl = generated_photo

        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            self.assertFalse(await harness._preauthorize_reaction_expression_prompt(event))
            reaction = json.loads(
                await PrivateCompanionPlugin.pc_find_reaction_image(
                    harness,
                    event,
                    query="普通回应",
                )
            )
            photo = json.loads(
                await PrivateCompanionPlugin.pc_generate_photo(
                    harness,
                    event,
                    prompt="随便生成一张",
                )
            )

        self.assertEqual("probability", reaction["skip_reason"])
        self.assertEqual("skipped", photo["status"])
        legacy_lookup.assert_not_awaited()
        generated_photo.assert_not_awaited()

    async def test_current_opt_out_cannot_fall_through_unscoped_media_tools(self) -> None:
        harness = _ReactionHarness(_FakeSmartImageAPI(self.image_path))
        event = _FakeEvent()
        event.message_str = "表情包以后别再发了"
        legacy_lookup = AsyncMock(return_value='{"status":"unexpected"}')
        generated_photo = AsyncMock(return_value='{"status":"unexpected"}')
        harness._pc_find_reaction_image_impl = legacy_lookup
        harness._pc_generate_photo_impl = generated_photo

        reaction = json.loads(
            await PrivateCompanionPlugin.pc_find_reaction_image(
                harness,
                event,
                query="无语表情包",
                caption="这句话也不该发出去。",
            )
        )
        photo = json.loads(
            await PrivateCompanionPlugin.pc_generate_photo(
                harness,
                event,
                prompt="生成一张表情包",
                kind="sticker",
            )
        )

        self.assertEqual("explicit_opt_out", reaction["skip_reason"])
        self.assertEqual("explicit_opt_out", photo["skip_reason"])
        legacy_lookup.assert_not_awaited()
        generated_photo.assert_not_awaited()

    async def test_historical_mention_cannot_bypass_current_opt_out_tool_guard(self) -> None:
        harness = _ReactionHarness(_FakeSmartImageAPI(self.image_path))
        event = _FakeEvent()
        event.message_str = "别再发表情包了，为什么你刚刚还发了一个表情包"
        legacy_lookup = AsyncMock(return_value='{"status":"unexpected"}')
        generated_photo = AsyncMock(return_value='{"status":"unexpected"}')
        harness._pc_find_reaction_image_impl = legacy_lookup
        harness._pc_generate_photo_impl = generated_photo

        reaction = json.loads(
            await PrivateCompanionPlugin.pc_find_reaction_image(
                harness,
                event,
                query="无语表情包",
                caption="这句话不应被发送。",
            )
        )
        photo = json.loads(
            await PrivateCompanionPlugin.pc_generate_photo(
                harness,
                event,
                prompt="生成一张表情包",
                kind="sticker",
            )
        )

        self.assertEqual("explicit_opt_out", reaction["skip_reason"])
        self.assertEqual("explicit_opt_out", photo["skip_reason"])
        legacy_lookup.assert_not_awaited()
        generated_photo.assert_not_awaited()

    async def test_reaction_image_send_requires_complete_visible_caption(self) -> None:
        harness = _ReactionHarness(_FakeSmartImageAPI(self.image_path))
        event = _FakeEvent()

        payload = json.loads(
            await harness._pc_find_reaction_image_impl(
                event,
                query="无语表情包",
            )
        )

        self.assertEqual("missing_visible_caption", payload["status"])
        self.assertFalse(payload["sent"])
        self.assertEqual([], harness.api.calls)
        self.assertEqual(0, harness.deliveries)

    async def test_legacy_spontaneous_reaction_without_caption_is_skipped(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment()
        event = _FakeEvent()
        module = SimpleNamespace(get_smart_imagechat_api=lambda: api)

        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.get_reaction_asset_library",
            return_value=module,
        ):
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(event))
            payload = json.loads(
                await PrivateCompanionPlugin.pc_find_reaction_image(
                    harness,
                    event,
                    query="轻松回应",
                    purpose="接住玩笑",
                    emotion="开心",
                    spontaneous=True,
                    caption="",
                )
            )

        self.assertEqual("missing_visible_caption", payload["skip_reason"])
        self.assertFalse(payload["sent"])
        self.assertEqual([], api.calls)
        self.assertEqual(0, harness.deliveries)

    async def test_explicit_media_requests_keep_legacy_tool_behavior(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        harness = _ReactionHarness(api)
        event = _FakeEvent()
        legacy_lookup = AsyncMock(return_value='{"status":"success","sent":true}')
        generated_photo = AsyncMock(return_value='{"status":"success","sent":true}')
        harness._pc_find_reaction_image_impl = legacy_lookup
        harness._pc_generate_photo_impl = generated_photo

        reaction = json.loads(
            await PrivateCompanionPlugin.pc_find_reaction_image(
                harness,
                event,
                query="无语表情包",
                caption="给你找到了，这张很合适。",
            )
        )
        photo = json.loads(
            await PrivateCompanionPlugin.pc_generate_photo(
                harness,
                event,
                prompt="生成一张角色自拍",
                kind="selfie",
            )
        )

        self.assertTrue(reaction["sent"])
        self.assertTrue(photo["sent"])
        legacy_lookup.assert_awaited_once()
        generated_photo.assert_awaited_once()

    async def test_proactive_framework_allows_photo_tool_but_keeps_other_pc_tools_blocked(self) -> None:
        harness = _ReactionHarness(_FakeSmartImageAPI(self.image_path))
        harness.enable_proactive_only_mode = False
        harness._clear_proactive_only_temp_unlocks_if_mode_off = Mock()
        harness._proactive_only_blocks_passive_event = (
            PrivateCompanionPlugin._proactive_only_blocks_passive_event.__get__(harness)
        )
        generated_photo = AsyncMock(return_value='{"status":"success","sent":true}')
        viewed_feed = AsyncMock(return_value='{"status":"unexpected"}')
        harness._pc_generate_photo_impl = generated_photo
        harness._pc_qzone_view_feed_impl = viewed_feed
        event = _FakeEvent()
        event.message_str = "请生成一张清晨厨房自拍"
        event.private_companion_proactive_framework = True

        payload = json.loads(
            await PrivateCompanionPlugin.pc_generate_photo(
                harness,
                event,
                prompt="清晨厨房，端着两个刚煎好的荷包蛋，自拍视角",
                kind="selfie",
                send=True,
            )
        )
        blocked_payload = json.loads(
            await PrivateCompanionPlugin.pc_qzone_view_feed(harness, event)
        )

        self.assertEqual("success", payload["status"])
        self.assertEqual("disabled", blocked_payload["status"])
        generated_photo.assert_awaited_once()
        viewed_feed.assert_not_awaited()
        self.assertFalse(harness._proactive_only_blocks_passive_event(event, "pc_generate_photo"))
        self.assertTrue(harness._proactive_only_blocks_passive_event(event, "pc_tools"))

    async def test_proactive_framework_plaintext_photo_recovery_reaches_generator(self) -> None:
        harness = _ReactionHarness(_FakeSmartImageAPI(self.image_path))
        harness.enable_proactive_only_mode = False
        harness._clear_proactive_only_temp_unlocks_if_mode_off = Mock()
        harness._proactive_only_blocks_passive_event = (
            PrivateCompanionPlugin._proactive_only_blocks_passive_event.__get__(harness)
        )
        generated_photo = AsyncMock(return_value='{"status":"success","success":true,"sent":true}')
        harness._pc_generate_photo_impl = generated_photo
        event = _FakeEvent()
        event.message_str = "请生成一张清晨厨房自拍"
        event.private_companion_proactive_framework = True
        leaked_call = (
            '{"name":"pc_generate_photo","parameters":'
            '{"prompt":"清晨厨房自拍","kind":"selfie"}}'
        )

        cleaned, recovery = await harness._recover_plaintext_photo_tool_call(
            event,
            SimpleNamespace(tools_call_name=[]),
            leaked_call,
        )

        self.assertEqual("", cleaned)
        self.assertTrue(recovery["sent"])
        generated_photo.assert_awaited_once()

    def test_photo_feature_keeps_passive_pc_tools_unlock_semantics(self) -> None:
        harness = _ReactionHarness(_FakeSmartImageAPI(self.image_path))
        harness.enable_proactive_only_mode = True
        harness._proactive_only_temp_unlock_allows = Mock(return_value=True)
        event = _FakeEvent()

        blocked = PrivateCompanionPlugin._proactive_only_blocks_passive_event(
            harness,
            event,
            "pc_generate_photo",
        )

        self.assertFalse(blocked)
        harness._proactive_only_temp_unlock_allows.assert_called_once_with("pc_tools")

        harness._proactive_only_temp_unlock_allows.reset_mock(return_value=True)
        harness._proactive_only_temp_unlock_allows.return_value = False
        self.assertTrue(
            PrivateCompanionPlugin._proactive_only_blocks_passive_event(
                harness,
                event,
                "pc_generate_photo",
            )
        )
        harness._proactive_only_temp_unlock_allows.assert_called_once_with("pc_tools")

    def test_feedback_targets_are_isolated_by_conversation(self) -> None:
        user: dict = {}
        state = ensure_reaction_expression_state(user)
        private_scope = "default:FriendMessage:10001"
        group_scope = "default:GroupMessage:20001"
        private_intent = normalize_reaction_expression_intent(
            purpose="私聊回应",
            emotion="开心",
        )
        group_intent = normalize_reaction_expression_intent(
            purpose="群聊回应",
            emotion="疑惑",
        )
        record_reaction_expression_sent(
            state,
            private_intent,
            image_id="private-image",
            image_path="C:/images/private.png",
            image_key="id:private-image",
            now=100.0,
            candidate_limit=6,
            scope_key=private_scope,
        )
        record_reaction_expression_sent(
            state,
            group_intent,
            image_id="group-image",
            image_path="C:/images/group.png",
            image_key="id:group-image",
            now=110.0,
            candidate_limit=6,
            scope_key=group_scope,
        )

        signal = classify_reaction_expression_feedback(
            state,
            "刚才那张表情包很合适",
            now=120.0,
            scope_key=private_scope,
        )
        feedback = record_reaction_expression_feedback(
            state,
            signal,
            "刚才那张表情包很合适",
            now=120.0,
            scope_key=private_scope,
        )

        self.assertEqual("private-image", feedback["image_id"])
        self.assertNotIn(private_scope, state["feedback_targets"])
        self.assertEqual(
            "group-image",
            state["feedback_targets"][group_scope]["image_id"],
        )

    def test_recent_image_retention_is_independent_from_candidate_limit(self) -> None:
        state = ensure_reaction_expression_state({})
        intent = normalize_reaction_expression_intent(query="回应")
        for index in range(3):
            record_reaction_expression_sent(
                state,
                intent,
                image_id=f"image-{index}",
                image_path=f"C:/images/{index}.png",
                image_key=f"id:image-{index}",
                now=100.0 + index,
                candidate_limit=1,
                duplicate_window_seconds=600.0,
                scope_key="default:GroupMessage:20001",
            )

        self.assertEqual(3, len(state["recent_images"]))
        self.assertFalse(
            reserve_reaction_expression_image(
                state,
                image_key="id:image-0",
                now=104.0,
                duplicate_window_seconds=600.0,
            )
        )
        self.assertTrue(
            reserve_reaction_expression_image(
                state,
                image_key="id:new",
                now=1000.0,
                duplicate_window_seconds=600.0,
            )
        )
        self.assertEqual([], state["recent_images"])

    def test_feedback_scope_does_not_fall_back_to_other_conversation(self) -> None:
        state = ensure_reaction_expression_state({})
        state["feedback_target"] = {
            "image_id": "legacy-private",
            "image_key": "id:legacy-private",
            "sent_at": 100.0,
            "expires_at": 3600.0,
        }
        state["feedback_targets"]["default:GroupMessage:20001"] = {
            "image_id": "group-image",
            "image_key": "id:group-image",
            "sent_at": 110.0,
            "expires_at": 3600.0,
        }

        self.assertEqual(
            "",
            classify_reaction_expression_feedback(
                state,
                "刚才那张表情包很合适",
                now=120.0,
                scope_key="default:FriendMessage:10001",
            ),
        )

    async def test_reaction_runtime_logs_gate_lookup_and_delivery_without_private_inputs(
        self,
    ) -> None:
        asset_id = f"pc-local:{'a' * 32}"
        api = _FakeSmartImageAPI(self.image_path, image_id=asset_id)
        original_find_image = api.find_image

        async def find_with_private_reason(*args, **kwargs):
            lookup = await original_find_image(*args, **kwargs)
            lookup["reason"] = "PRIVATE_MATCH_REASON_CANARY_7F31"
            return lookup

        api.find_image = find_with_private_reason
        harness = _ReactionHarness(api)
        harness.enable_reaction_experiment()
        event = _FakeEvent()
        event.message_str = "PRIVATE_MESSAGE_CANARY_7F31"
        private_query = "PRIVATE_QUERY_CANARY_7F31"
        private_context = r"PRIVATE_CONTEXT_CANARY_7F31 C:\Users\secret\reaction.png"

        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.logger.info"
        ) as log_info:
            self.assertTrue(await harness._preauthorize_reaction_expression_prompt(event))
            result = json.loads(
                await harness._pc_reaction_expression_impl(
                    event,
                    query=private_query,
                    context=private_context,
                    purpose="PRIVATE_PURPOSE_CANARY_7F31",
                    emotion="PRIVATE_EMOTION_CANARY_7F31",
                )
            )

        logs = _reaction_log_payloads(log_info)
        self.assertTrue(result["sent"])
        self.assertTrue(any(row.get("stage") == "gate" and row.get("decision") == "allow" for row in logs))
        lookup = next(row for row in logs if row.get("stage") == "lookup" and row.get("decision") == "hit")
        delivery = next(row for row in logs if row.get("stage") == "delivery" and row.get("decision") == "sent")
        self.assertEqual(asset_id, lookup["asset_ref"])
        self.assertEqual("tags_emotions_intents", lookup["match_basis"])
        self.assertTrue(delivery["sent"])
        self.assertEqual(1, len({row["trace_id"] for row in logs}))
        for row in logs:
            self.assertTrue(
                {"query", "context", "intent", "path", "user_id", "scope_key"}.isdisjoint(row)
            )
        rendered = json.dumps(logs, ensure_ascii=False)
        for private_value in (
            event.message_str,
            private_query,
            private_context,
            "PRIVATE_PURPOSE_CANARY_7F31",
            "PRIVATE_EMOTION_CANARY_7F31",
            "PRIVATE_MATCH_REASON_CANARY_7F31",
            self.image_path,
            event.unified_msg_origin,
        ):
            self.assertNotIn(private_value, rendered)

    async def test_reaction_runtime_logs_lookup_miss_and_cache_hit(self) -> None:
        api = _FakeSmartImageAPI(self.image_path)
        miss_harness = _ReactionHarness(api)
        miss_harness._owned_reaction_library.find = lambda *_args, **_kwargs: None
        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.logger.info"
        ) as miss_log_info:
            miss = json.loads(
                await miss_harness._pc_find_reaction_image_impl(
                    _FakeEvent(),
                    query="PRIVATE_MISS_QUERY_CANARY_24AC",
                    send=False,
                    low_latency=True,
                )
            )

        miss_logs = _reaction_log_payloads(miss_log_info)
        self.assertEqual("not_found", miss["status"])
        self.assertTrue(
            any(
                row.get("stage") == "lookup"
                and row.get("decision") == "miss"
                and row.get("reason") == "not_found"
                for row in miss_logs
            )
        )

        cache_harness = _ReactionHarness(_FakeSmartImageAPI(self.image_path))
        cache_event = _FakeEvent()
        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.logger.info"
        ) as cache_log_info:
            first = json.loads(
                await cache_harness._pc_find_reaction_image_impl(
                    cache_event,
                    query="缓存命中测试",
                    send=False,
                    low_latency=True,
                )
            )
            second = json.loads(
                await cache_harness._pc_find_reaction_image_impl(
                    cache_event,
                    query="缓存命中测试",
                    send=False,
                    low_latency=True,
                )
            )

        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["cache_hit"])
        cache_logs = _reaction_log_payloads(cache_log_info)
        self.assertTrue(
            any(
                row.get("stage") == "lookup"
                and row.get("decision") == "hit"
                and row.get("cache_hit") is True
                for row in cache_logs
            )
        )

    async def test_reaction_runtime_logs_delivery_failure(self) -> None:
        harness = _ReactionHarness(
            _FakeSmartImageAPI(self.image_path),
            sent=False,
        )
        with patch(
            "astrbot_plugin_private_companion.llm_tool_actions.logger.info"
        ) as log_info:
            result = json.loads(
                await harness._pc_find_reaction_image_impl(
                    _FakeEvent(),
                    query="发送失败测试",
                    caption="我试着把这张发给你。",
                )
            )

        self.assertEqual("delivery_failed", result["status"])
        logs = _reaction_log_payloads(log_info)
        failure = next(
            row
            for row in logs
            if row.get("stage") == "delivery" and row.get("decision") == "failed"
        )
        self.assertEqual("delivery_failed", failure["reason"])
        self.assertFalse(failure["sent"])
        self.assertEqual("current", failure["delivery"])

    async def test_reaction_lookup_exception_log_never_contains_path_or_query(
        self,
    ) -> None:
        query_canary = "PRIVATE_EXCEPTION_QUERY_CANARY_91BD"
        path_canary = r"C:\Users\secret\reaction-private.png"
        harness = _ReactionHarness(_FakeSmartImageAPI(self.image_path))

        def fail_lookup(*_args, **_kwargs):
            raise OSError(f"cannot open {path_canary}; query={query_canary}")

        harness._owned_reaction_library.find = fail_lookup
        with (
            patch(
                "astrbot_plugin_private_companion.llm_tool_actions.logger.info"
            ) as log_info,
            patch(
                "astrbot_plugin_private_companion.llm_tool_actions.logger.warning"
            ) as log_warning,
        ):
            result = json.loads(
                await harness._pc_find_reaction_image_impl(
                    _FakeEvent(),
                    query=query_canary,
                    context=path_canary,
                    send=False,
                )
            )

        self.assertEqual("error", result["status"])
        logs = _reaction_log_payloads(log_info)
        failure = next(row for row in logs if row.get("stage") == "lookup")
        self.assertEqual("lookup_error", failure["reason"])
        self.assertEqual("OSError", failure["error_type"])
        rendered_calls = repr(log_info.call_args_list) + repr(log_warning.call_args_list)
        self.assertNotIn(query_canary, rendered_calls)
        self.assertNotIn(path_canary, rendered_calls)


if __name__ == "__main__":
    unittest.main()
