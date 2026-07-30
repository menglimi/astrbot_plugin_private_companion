# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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

    def get_result(self):
        return self._result

    def set_result(self, result) -> None:
        self._result = result


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

    @staticmethod
    def has_enabled_assets() -> bool:
        return True

    def find(self, query: str, *, context: str = "", scope: str = "private"):
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
        self.enable_reaction_expression_experiment = False
        self.reaction_expression_private_enabled = True
        self.reaction_expression_group_enabled = False
        self.reaction_expression_trigger_probability = 0.2
        self.reaction_expression_cooldown_seconds = 180
        self.reaction_expression_low_latency_mode = True
        self.reaction_expression_candidate_limit = 6
        self.enabled = True
        self._owned_reaction_library = _OwnedReactionLibraryAdapter(api)

    def _reaction_asset_library(self):
        return self._owned_reaction_library

    async def _deliver_generated_image_to_event(self, *_args, **_kwargs):
        self.deliveries += 1
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

    def _save_data_sync(self) -> None:
        self.saved = True

    @staticmethod
    def _proactive_only_blocks_passive_event(*_args, **_kwargs) -> bool:
        return False

    def enable_reaction_experiment(self, **overrides) -> None:
        self.enable_reaction_expression_experiment = True
        self.reaction_expression_trigger_probability = 1.0
        for key, value in overrides.items():
            setattr(self, key, value)


class SmartImageChatIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        image_path = os.path.join(self.temp_dir.name, "reaction  image.png")
        with open(image_path, "wb") as handle:
            handle.write(b"image")
        self.image_path = image_path

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

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
        self.assertEqual(1, sum(isinstance(item, Image) for item in event.get_result().chain))

        event._has_send_oper = True
        await PrivateCompanionPlugin.settle_reaction_expression_attachment_after_send(
            harness,
            event,
        )

        self.assertTrue(pending["settled"])
        self.assertTrue(pending["sent"])
        self.assertEqual("reaction-1", state["recent_images"][-1]["image_id"])

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
                )
            )

        self.assertTrue(payload["success"])
        self.assertTrue(payload["sent"])
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
                await harness._pc_find_reaction_image_impl(event, query="无语反应图")
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
        self.assertNotIn("spontaneous=true", instruction)
        self.assertNotIn("pc_generate_photo", instruction)

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

    def test_request_tool_scope_hides_unavailable_media_actions(self) -> None:
        authorized = SimpleNamespace(
            func_tool=_FakeToolSet(
                "pc_generate_photo",
                "pc_find_reaction_image",
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
            ["pc_find_reaction_image", "pc_generate_photo"],
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
            ["pc_find_reaction_image", "pc_generate_photo"],
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
            ["pc_generate_photo", "pc_find_reaction_image", "safe_tool"],
            [tool.name for tool in explicit.func_tool.tools],
        )

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


if __name__ == "__main__":
    unittest.main()
