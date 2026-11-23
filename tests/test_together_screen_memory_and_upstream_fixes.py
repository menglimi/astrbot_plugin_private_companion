"""Regression suite for Together/Screen memory continuity, upstream issues #832/#835, and cross-group awareness."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from astrbot_host.core_reply_context import build_core_reply_prompt
from astrbot_host.core_reply_mobile import screen_scene_snapshot
from companion_v2.product import PRODUCT_VERSION
from astrbot_host.integration_registry import IntegrationRegistry
from astrbot_host.mobile_together import MobileTogetherBridge
from astrbot_host.native_extensions.together.activity_methods import TogetherActivityMethods
from astrbot_host.native_extensions.together.media_methods import TogetherMediaMethods
from astrbot_host.native_extensions.together.model_methods import TogetherModelMethods
from astrbot_host.native_extensions.together.rooms_methods import TogetherRoomsMethods
from astrbot_host.native_extensions.together.settings_methods import TogetherSettingsMethods
from astrbot_host.native_extensions.together.speech_methods import TogetherSpeechMethods
from companion_v2.adapters.astrbot.memory_push import (
    AstrBotMemoryPush,
    PUSH_SOURCE_PLUGIN,
    memory_push_provider,
)
from companion_v2.capabilities.memory_push import (
    PUSH_SHARED_EXPERIENCE,
    MemoryPushRecord,
    push_memory_id,
)
from companion_v2.capabilities.models import CapabilityPackageDescriptor
from companion_v2.capabilities.native_catalog import register_native_replacement
from companion_v2.capabilities.repositories import CapabilityRepository
from companion_v2.conversation.awareness_context import ConversationAwarenessContext, MAX_ITEMS
from companion_v2.conversation.screen_context import (
    MAX_AGE_SECONDS,
    SCREEN_CONTEXT_INSTRUCTION,
    project_screen_context,
)
from companion_v2.execution.models import TaskContext, TaskError, encode, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import (
    AudienceRef,
    RoleOwnerRef,
    ScopeKind,
    ScopeRef,
    SubjectKind,
    SubjectRef,
)
from companion_v2.identity_policy.models import (
    AuthenticatedHostPrincipal,
    ResolvedIdentity,
    RoleBinding,
)
from companion_v2.references.models import ReferenceKind, ReferenceRole
from companion_v2.references.prompts import image_reference_instruction, text_wardrobe_instruction
from companion_v2.references.service import OWNED_REFERENCE_PREFIX, ReferenceAssetService
from companion_v2.role_work.awareness.service import RoleAwareness
from companion_v2.storage.runtime import StorageRuntime
from companion_v2.together.models import NativeRoomBinding, RoomMember, RoomSession
from companion_v2.together.provider import (
    NativeTogetherError,
    NativeTogetherProvider,
    OperationStore,
    scope_key,
)
from tests.multi_host_support import MultiFixture
from tests.test_memory_linkage import MemoryPluginDeclaration
from tests.test_reality_together_native import FakeTogether, context as native_context_dict, request as native_request
from tests import test_reference_assets as _reference_asset_tests
from tests.test_role_awareness import FakeAcquaintance, FakeSettings, NOW as AWARENESS_NOW


AT = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)


class _RoomRuntime(
    TogetherModelMethods,
    TogetherMediaMethods,
    TogetherActivityMethods,
    TogetherRoomsMethods,
    TogetherSpeechMethods,
    TogetherSettingsMethods,
):
    pass


class _Socket:
    closed = False

    def __init__(self):
        self.payloads = []

    async def send_json(self, payload, **kwargs):
        self.payloads.append(dict(payload))

    async def close(self, **kwargs):
        self.closed = True


class _RecordingEventBridge:
    def __init__(self):
        self.events = []

    def bridge_lifecycle_status(self):
        return {"active": True}

    async def record_event(self, *, content, **kwargs):
        self.events.append({"content": content, "kwargs": kwargs})
        return "mc-event-" + str(len(self.events))


class TogetherVisualAndSharedExperienceTests(MemoryPluginDeclaration, MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        super().setUp()
        self.app = await self.start_host()
        prepared = require(await self.controller.prepare_core_reply(self.event(identifier="together-seed")))
        await prepared["native_source_future"]
        self.context = prepared["context"]
        self.persona = await self.controller.session.bridge.verified_persona(
            self.context.scope, self.context.role_binding
        )
        self.runtime = _RoomRuntime()
        self.runtime.controller = self.controller
        self.runtime.context = self.native
        self.runtime.primary_user_id = ""
        self.runtime.persona_id = ""
        self.runtime.history_turns = 12
        self.runtime.enable_memory_context = True
        self.runtime.record_visible_turns = False
        self.runtime.record_shared_experiences = True
        self.runtime.sync_astrbot_conversation = True
        self.runtime.model_hangup_enabled = False
        self.runtime.watch_prepare_knowledge = True
        self.runtime.watch_memory_refresh_seconds = 240
        self.runtime.custom_system_prompt = ""
        self.runtime.config = {}
        self.runtime.rooms = {}
        self.runtime.detached_rooms = {}
        self.runtime.media_sources = {}
        self.runtime.ticket_store = NS(revoke=lambda _tok: None, revoke_room=lambda _rid: 0)
        self.runtime._background_tasks = set()
        self.runtime._astrbot_room_conversations = {}
        self.runtime._push_live_subtitle = self._noop_async
        self.runtime._stop_live_mouth_sync = self._noop_async
        self.runtime._drop_room_media = lambda _rid: None

        self.socket = _Socket()
        scope = self.context.scope
        binding = NativeRoomBinding(
            scope_key(json.loads(encode(self.context))),
            **self.persona,
            platform=scope.platform,
            session_id=scope.session_id,
            scope_kind=scope.kind.value,
        )
        self.room = RoomSession("room-watch-1", "ticket-watch-1", "watch", "200", self.socket, native_v2=binding)
        self.room.watch_tts_enabled = False
        self.room.call_tts_enabled = False
        self.runtime.rooms[self.room.room_id] = self.room
        self.selected = (self.controller.session, self.app, self.context)
        registry = self.controller.native_extensions
        original_active = registry.active_service
        for item in (
            patch.object(MobileTogetherBridge, "_target_context", side_effect=lambda _: self.selected),
            patch.object(MobileTogetherBridge, "_admitted", return_value=object()),
            patch.object(
                registry,
                "active_service",
                side_effect=lambda name: self.runtime if name == "together" else original_active(name),
            ),
        ):
            item.start()
            self.addCleanup(item.stop)

    @staticmethod
    async def _noop_async(*args, **kwargs):
        return None

    def test_visual_digest_ring_sanitizes_deduplicates_and_bounds_length(self):
        raw = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg== 画面里主角站在雪地钢琴旁弹奏白色相簿旋律" + "。" * 60
        first = self.room.record_visual_digest(raw, mode="watch", source="frame_caption")
        self.assertNotIn("base64", first)
        self.assertLessEqual(len(first), 80)
        self.assertEqual(len(self.room.visual_digest_ring), 1)
        # Identical repeat does not bloat ring.
        self.room.record_visual_digest(raw, mode="watch", source="frame_caption")
        self.assertEqual(len(self.room.visual_digest_ring), 1)
        # Distinct digest appends cleanly.
        self.room.record_visual_digest("镜头切到排练室的黑板和乐谱", mode="watch", source="frame_caption")
        self.assertEqual(len(self.room.visual_digest_ring), 2)

    async def test_record_astrbot_turns_uses_real_visual_digest_across_watch_call_work(self):
        pairs = []

        async def add_pair(conversation_id, user, assistant):
            pairs.append((conversation_id, user["content"], assistant["content"]))

        self.native.conversation_manager.add_message_pair = add_pair
        for mode, digest_text in (
            ("watch", "动画里主角在雪夜琴房练习钢琴"),
            ("call", "镜头里桌面上放着一杯热咖啡和键盘"),
            ("work", "编辑器里正停留在 Python 单元测试报错处"),
        ):
            self.room.mode = mode
            ok = await self.runtime._record_astrbot_turns(
                self.room,
                "你看这个怎么样",
                "挺不错的",
                visual_context_used=True,
                visual_digest=digest_text,
            )
            self.assertTrue(ok)
        self.assertEqual(len(pairs), 3)
        self.assertIn("【画面简述】动画里主角在雪夜琴房练习钢琴", pairs[0][1])
        self.assertIn("【画面简述】镜头里桌面上放着一杯热咖啡和键盘", pairs[1][1])
        self.assertIn("【画面简述】编辑器里正停留在 Python 单元测试报错处", pairs[2][1])
        self.assertNotIn("只同步文字语义，不保存画面", "\n".join(item[1] for item in pairs))

    async def test_watch_silent_observation_and_room_close_persist_to_v2_and_memory_companion(self):
        import sys
        from companion_v2.adapters.astrbot.memory_owner import MEMORY_OWNER_MODULE_NAMES
        from tests.test_memory_linkage import FakeModule

        bridge = _RecordingEventBridge()
        sys.modules[MEMORY_OWNER_MODULE_NAMES[0]] = FakeModule(bridge)
        self.app.memory_push = memory_push_provider(None)

        # Simulate silent watch observation (speak=False) + media metadata + plot note + dialogue
        self.room.media_state = {"title": "白色相簿2 音乐会片段", "current_time": 125.0}
        self.room.watch_knowledge = "来源：B站视频页公开信息（标题）\n这是一段冬季学园祭音乐会演出。"
        self.room.watch_memory = "两位主角在后台核对曲目并登台合奏。"
        self.room.record_visual_digest("舞台聚光灯亮起，黑发少女在三角钢琴前开始演奏", mode="watch", source="watch_observation")
        self.room.append_turn("user", "这一段钢琴进场太震撼了", history_turns=12)
        self.room.append_turn("assistant", "灯光亮起来那一下确实很有氛围", history_turns=12)

        await self.runtime.close_room(self.room)

        # 1. Persisted in V2 memory_views (experiences)
        experiences = require(self.app.memory.experiences.list(self.context))
        self.assertEqual(len(experiences["items"]), 1)
        exp = experiences["items"][0]
        self.assertIn("白色相簿2 音乐会片段", exp["title"])
        statement_text = exp["statements"][0]["text"]
        self.assertIn("三角钢琴", statement_text)
        self.assertIn("两位主角在后台核对曲目", statement_text)

        # 2. Persisted in V2 private role.awareness.v1 and strictly hidden from group chat
        private_awareness = self.app.cross_window.snapshot(self.context, self.clock.now())
        self.assertIsNotNone(private_awareness)
        self.assertIn("recent_together_activity", private_awareness)
        self.assertIn("白色相簿2 音乐会片段", private_awareness["recent_together_activity"][0]["topic"])
        group_scope = replace(
            self.context.scope,
            kind=ScopeKind.GROUP,
            native_scope_id="999888",
            conversation_id="group-999888",
            session_id="999888",
        )
        group_context = replace(
            self.context,
            role_binding=replace(self.context.role_binding, scope=group_scope),
            audience=AudienceRef(group_scope, (self.context.actor.subject,)),
        )
        group_awareness = self.app.cross_window.snapshot(group_context, self.clock.now()) or {}
        self.assertNotIn("recent_together_activity", group_awareness)

        # 3. Pushed one-way to MemoryCompanion with private-only visibility & together_room source
        self.assertEqual(len(bridge.events), 1)
        pushed = bridge.events[0]
        self.assertIn("三角钢琴", pushed["content"])
        kw = pushed["kwargs"]
        self.assertEqual(kw["scope"], "private")
        self.assertEqual(kw["visibility"], "private_pair")
        self.assertEqual(kw["memory_type"], "shared_watch")
        self.assertEqual(kw["source_plugin"], PUSH_SOURCE_PLUGIN)
        self.assertEqual(kw["metadata"]["source"], "together_room")
        self.assertEqual(kw["metadata"]["visibility"], "private_owner")
        self.assertEqual(kw["metadata"]["owner_bot_id"], self.persona["account_id"])
        self.assertEqual(kw["subject"]["kind"], "bot")
        self.assertEqual(kw["subject"]["id"], self.persona["bot_id"])
        self.assertEqual(kw["object"]["id"], "200")
        self.assertEqual(kw["memory_id"], push_memory_id(PUSH_SHARED_EXPERIENCE, "together_room", self.room.room_id))

        # Idempotent: second call never writes duplicates
        self.assertEqual(await self.runtime._record_shared_experience(self.room), "")
        self.assertEqual(len(bridge.events), 1)

    async def test_revoked_v2_room_skips_v2_and_external_memory_persistence(self):
        import sys
        from companion_v2.adapters.astrbot.memory_owner import MEMORY_OWNER_MODULE_NAMES
        from tests.test_memory_linkage import FakeModule

        bridge = _RecordingEventBridge()
        sys.modules[MEMORY_OWNER_MODULE_NAMES[0]] = FakeModule(bridge)
        self.app.memory_push = memory_push_provider(None)

        self.room.record_visual_digest("授权撤销前看到的画面摘要", mode="watch", source="watch_observation")
        self.room.append_turn("user", "刚才这段不错", history_turns=12)

        with patch.object(MobileTogetherBridge, "_admitted", return_value=None):
            await self.runtime.close_room(self.room)

        self.assertEqual(bridge.events, [])
        self.assertEqual(require(self.app.memory.experiences.list(self.context))["items"], [])
        self.assertIsNone(self.app.cross_window.snapshot(self.context, self.clock.now()))

    async def test_v2_access_denied_blocks_both_v2_and_memory_companion_writes(self):
        import sys
        from companion_v2.adapters.astrbot.memory_owner import MEMORY_OWNER_MODULE_NAMES
        from tests.test_memory_linkage import FakeModule

        bridge = _RecordingEventBridge()
        sys.modules[MEMORY_OWNER_MODULE_NAMES[0]] = FakeModule(bridge)
        self.app.memory_push = memory_push_provider(None)

        self.room.record_visual_digest("被拒权时不应沉淀的画面", mode="watch", source="watch_observation")
        self.room.append_turn("user", "不应该落库", history_turns=12)

        with patch.object(
            self.app.tasks,
            "check_context_access",
            return_value=Outcome.failed("authorization_denied", "context revoked"),
        ):
            await self.runtime.close_room(self.room)

        self.assertEqual(bridge.events, [])
        self.assertEqual(require(self.app.memory.experiences.list(self.context))["items"], [])
        self.assertIsNone(self.app.cross_window.snapshot(self.context, self.clock.now()))

    async def test_watch_ending_then_post_discussion_and_second_video_are_all_retained(self):
        import sys
        from companion_v2.adapters.astrbot.memory_owner import MEMORY_OWNER_MODULE_NAMES
        from tests.test_memory_linkage import FakeModule

        bridge = _RecordingEventBridge()
        sys.modules[MEMORY_OWNER_MODULE_NAMES[0]] = FakeModule(bridge)
        self.app.memory_push = memory_push_provider(None)

        # Video 1 finishes mid-room (player_state ended / watch ending trigger)
        self.room.media_state = {"title": "第一部音乐会", "current_time": 180.0}
        self.room.record_visual_digest("第一部雪夜钢琴独奏画面", mode="watch", source="watch_observation")
        self.room.append_turn("user", "第一部结尾太好听了", history_turns=12)
        first_summary = await self.runtime._record_shared_experience(self.room, final=False)
        self.assertIn("第一部雪夜钢琴独奏画面", first_summary)
        self.assertFalse(self.room.shared_experience_finalized)
        self.assertEqual(len(bridge.events), 1)
        first_memory_id = bridge.events[0]["kwargs"]["memory_id"]

        # Unchanged repeat is idempotent (0 extra writes)
        self.assertEqual(await self.runtime._record_shared_experience(self.room, final=False), "")
        self.assertEqual(len(bridge.events), 1)

        # Post-ending discussion adds new dialogue before switching videos -> updates Video 1 cleanly
        self.room.append_turn("user", "而且安可曲的弦乐进场绝了", history_turns=12)
        updated_first = await self.runtime._record_shared_experience(self.room, final=False)
        self.assertIn("安可曲的弦乐进场绝了", updated_first)
        self.assertEqual(len(bridge.events), 2)
        self.assertEqual(bridge.events[1]["kwargs"]["memory_id"], first_memory_id)
        items_after_v1 = require(self.app.memory.experiences.list(self.context))["items"]
        self.assertEqual(len(items_after_v1), 1)
        self.assertEqual(items_after_v1[0]["revision"], 2)
        self.assertIn("安可曲的弦乐进场绝了", items_after_v1[0]["statements"][0]["text"])

        # Switch to Video 2 via reset_watch -> watch -> close_room
        self.room.reset_watch(media_token="video-2")
        self.room.media_state = {"title": "第二部纪录片", "current_time": 95.0}
        self.room.record_visual_digest("第二部极光与雪山延时画面", mode="watch", source="watch_observation")
        self.room.append_turn("user", "极光这段镜头好震撼", history_turns=12)
        await self.runtime.close_room(self.room)

        self.assertTrue(self.room.shared_experience_finalized)
        items_final = require(self.app.memory.experiences.list(self.context))["items"]
        self.assertEqual(len(items_final), 2)
        titles = {item["title"] for item in items_final}
        self.assertTrue(any("第一部音乐会" in title for title in titles))
        self.assertTrue(any("第二部纪录片" in title for title in titles))
        self.assertEqual(len(bridge.events), 3)
        self.assertNotEqual(bridge.events[2]["kwargs"]["memory_id"], first_memory_id)
        self.assertIn("极光与雪山延时画面", bridge.events[2]["content"])

        # Finalized room never duplicates on repeated calls
        self.assertEqual(await self.runtime._record_shared_experience(self.room, final=True), "")
        self.assertEqual(len(bridge.events), 3)

    async def test_multi_user_invite_room_skips_private_memory_persistence(self):
        import sys
        from companion_v2.adapters.astrbot.memory_owner import MEMORY_OWNER_MODULE_NAMES
        from tests.test_memory_linkage import FakeModule

        bridge = _RecordingEventBridge()
        sys.modules[MEMORY_OWNER_MODULE_NAMES[0]] = FakeModule(bridge)
        self.app.memory_push = memory_push_provider(None)

        self.room.members["200"] = RoomMember("200", "owner")
        self.room.members["301"] = RoomMember("301", "guest", display_name="好友")
        self.room.record_visual_digest("私人桌面笔记内容", mode="call", source="frame_caption")
        self.room.append_turn("user", "这是私人话题", history_turns=12)

        await self.runtime.close_room(self.room)

        self.assertEqual(bridge.events, [])
        self.assertEqual(require(self.app.memory.experiences.list(self.context))["items"], [])
        self.assertIsNone(self.app.cross_window.snapshot(self.context, self.clock.now()))

    def test_resolve_primary_user_id_supports_multihost_and_refuses_ambiguity(self):
        self.runtime.primary_user_id = ""
        with patch.object(MobileTogetherBridge, "core_pairing_candidates", return_value=("100001",)):
            self.assertEqual(self.runtime._resolve_primary_user_id(), "100001")
        with patch.object(MobileTogetherBridge, "core_pairing_candidates", return_value=()), \
             patch.object(MobileTogetherBridge, "pairing_candidates", return_value=("200", "201")):
            self.assertEqual(self.runtime._resolve_primary_user_id(), "")

    async def test_separate_vision_caption_and_silent_watch_observation_populate_visual_digest_ring(self):
        chat_provider = NS(text_chat=self._fake_chat_reply)
        vision_provider = NS(text_chat=self._fake_vision_caption)
        self.runtime._room_chat_provider = lambda _room: chat_provider
        self.runtime._room_vision_provider = lambda _room: vision_provider
        self.runtime._build_system_prompt = self._fake_system_prompt
        self.runtime._provider_usage_id = lambda _p: "test-provider"
        self.runtime._token_usage_prompt = lambda *_a, **_k: ""

        self.room.mode = "call"
        reply = await self.runtime._generate_model_text(
            self.room,
            "帮我看看桌上这个杯子",
            image_data_url="data:image/png;base64,AAAA",
        )
        self.assertEqual(reply, "看起来是白色的陶瓷杯")
        self.assertEqual(len(self.room.visual_digest_ring), 1)
        self.assertIn("白色陶瓷咖啡杯", self.room.visual_digest_ring[0]["summary"])

        # Passive watch observation with speak=False still records visual digest in ring
        self.room.mode = "watch"
        self.runtime._generate_model_text = self._fake_silent_watch_decision
        self.runtime._record_current_subtitle = lambda _r: ""
        self.runtime._schedule_watch_memory_refresh = lambda *_a, **_k: None
        await self.runtime._generate_watch_comment(
            self.room,
            "data:image/png;base64,BBBB",
            trigger="scene_change",
            captured_at=42.0,
        )
        self.assertEqual(len(self.room.visual_digest_ring), 2)
        self.assertIn("主角推开琴房门走进雪地", self.room.visual_digest_ring[-1]["summary"])

    @staticmethod
    async def _fake_system_prompt(*_args, **_kwargs):
        return "system"

    @staticmethod
    async def _fake_chat_reply(**_kwargs):
        return NS(completion_text="看起来是白色的陶瓷杯")

    @staticmethod
    async def _fake_vision_caption(**_kwargs):
        return NS(completion_text="画面中桌子上放着一个白色陶瓷咖啡杯和一本翻开的乐谱")

    @staticmethod
    async def _fake_silent_watch_decision(*_args, **_kwargs):
        return json.dumps({
            "speak": False,
            "utterance": "",
            "observation": "主角推开琴房门走进雪地，天空正在飘雪",
            "expires_in": 12.0,
        }, ensure_ascii=False)

    async def test_resolve_native_v2_binding_attaches_for_authorized_private_user(self):
        self.runtime._get_chat_provider = lambda: NS(meta=lambda: NS(id="main/chat"))
        self.runtime._provider_usage_id = lambda _p: "main/chat"
        self.runtime.tts_timeout_seconds = 45
        self.runtime.chat_provider_id = ""
        self.runtime.tts_provider_id = ""
        binding = await self.runtime._resolve_native_v2_binding("200")
        self.assertIsNotNone(binding)
        self.assertEqual(binding["scope_kind"], "private")
        self.assertEqual(binding["session_id"], self.context.scope.session_id)
        self.assertEqual(binding["bot_id"], self.persona["bot_id"])

    def test_screen_context_enabled_round_trips_through_perception_management(self):
        from companion_v2.conversation.perception_management import PerceptionManagement
        from astrbot_host.management_identity import dashboard_operator

        operator = dashboard_operator(self.app.runtime, "admin", at=self.clock.now())
        manager = PerceptionManagement(self.app, self.context)
        before = require(manager.status(operator))
        self.assertIs(before["settings"]["screen_context_enabled"], True)
        updated = require(manager.configure(operator, {
            "operation_key": "toggle-screen-context",
            "edit_token": before["edit_token"],
            "changes": {"screen_context_enabled": False},
        }))
        self.assertIs(updated["status"]["settings"]["screen_context_enabled"], False)


class ScreenContextCoreReplyTests(unittest.TestCase):
    def _private_context(self, *, user_id="100001", scope_id="100001"):
        return NS(
            scope=NS(kind=ScopeKind.PRIVATE, native_scope_id=scope_id),
            actor=NS(principal=NS(native_account_id=user_id)),
            role_binding=NS(role=NS()),
            key="screen-private",
        )

    def _app(self, settings, *, interpretation='{"summary":"正在调试 Python 代码","scene":"IDE","resource_label":"VS Code"}', age_seconds=120):
        captured_at = AT - timedelta(seconds=age_seconds)
        calls = []

        class Repo:
            def latest_analysis(inner, **kwargs):
                calls.append(kwargs)
                return Outcome.success((
                    NS(
                        analysis=NS(interpretation=interpretation),
                        material=NS(capture=NS(captured_at=captured_at)),
                    ),
                ))

        messages = NS(
            acquaintance=NS(card_contribution=lambda *_a, **_k: None),
            cross_window_context=None,
            knowledge=None,
            role_profile_context=None,
            relationship_profile_context=None,
            memory_context=None,
            proactive_inspirations=None,
            role_state_context=None,
        )
        return NS(
            messages=messages,
            capabilities=NS(require_role_enabled=lambda **_kw: Outcome.success(NS(enabled=True))),
            screen_repository=Repo(),
            perception_settings=NS(capture=lambda *_args: {"settings": settings}),
            reply_settings=NS(snapshot=lambda *_a: NS(values={})),
            router=NS(profiles={}),
            runtime=None,
        ), calls

    def test_screen_context_injects_within_15_minutes_for_owner_private_only(self):
        settings = {
            "guidance": "", "calendar_timezone": "Asia/Shanghai", "calendar_enabled": False,
            "health_enabled": False, "screen_context_enabled": True,
            "platform_enabled": True, "model_route_enabled": True,
            "configured_modalities_enabled": True,
        }
        app, calls = self._app(settings, age_seconds=180)
        prompt = build_core_reply_prompt(app, self._private_context(), NS(text="刚给你看的代码报错在哪", event_id="scr-1"), at=AT)
        perception = json.loads(prompt.perception_context)
        self.assertEqual(perception["screen_context"]["summary"], "正在调试 Python 代码")
        self.assertEqual(perception["screen_context"]["privacy"], "private_only")
        self.assertIn(SCREEN_CONTEXT_INSTRUCTION, prompt.perception_instructions)
        self.assertEqual(len(calls), 1)

        # Older than 15 minutes -> dropped
        stale_app, _ = self._app(settings, age_seconds=MAX_AGE_SECONDS + 10)
        stale_prompt = build_core_reply_prompt(stale_app, self._private_context(), NS(text="刚才的屏幕", event_id="scr-2"), at=AT)
        self.assertNotIn("screen_context", json.loads(stale_prompt.perception_context))

        # Switch OFF -> never queries screen_repository
        off_app, off_calls = self._app({**settings, "screen_context_enabled": False}, age_seconds=30)
        off_prompt = build_core_reply_prompt(off_app, self._private_context(), NS(text="刚才的屏幕", event_id="scr-3"), at=AT)
        self.assertNotIn("screen_context", json.loads(off_prompt.perception_context))
        self.assertEqual(off_calls, [])

        # Group chat -> strictly blocked
        group_ctx = self._private_context()
        group_ctx.scope.kind = ScopeKind.GROUP
        group_app, group_calls = self._app(settings, age_seconds=30)
        group_prompt = build_core_reply_prompt(group_app, group_ctx, NS(text="刚才的屏幕", event_id="scr-4"), at=AT)
        self.assertNotIn("screen_context", json.loads(group_prompt.perception_context))
        self.assertEqual(group_calls, [])

        # Different user account / empty / wildcard -> strictly blocked (fail closed)
        other_app, other_calls = self._app(settings, age_seconds=30)
        self.assertIsNone(screen_scene_snapshot(other_app, self._private_context(user_id="999999", scope_id="100001"), AT))
        self.assertIsNone(screen_scene_snapshot(other_app, self._private_context(user_id="", scope_id="100001"), AT))
        self.assertIsNone(screen_scene_snapshot(other_app, self._private_context(user_id="100001", scope_id="*"), AT))
        self.assertEqual(other_calls, [])

        # Another role without Screen enabled -> strictly blocked
        role_denied_app, role_calls = self._app(settings, age_seconds=30)
        role_denied_app.capabilities = NS(
            require_role_enabled=lambda **_kw: Outcome.failed("role_extension_disabled", "disabled for this role")
        )
        self.assertIsNone(screen_scene_snapshot(role_denied_app, self._private_context(), AT))
        self.assertEqual(role_calls, [])

        # Missing capabilities or missing role binding -> strictly fail-closed (never skip role check)
        no_cap_app, no_cap_calls = self._app(settings, age_seconds=30)
        no_cap_app.capabilities = None
        self.assertIsNone(screen_scene_snapshot(no_cap_app, self._private_context(), AT))
        self.assertEqual(no_cap_calls, [])

        no_role_app, no_role_calls = self._app(settings, age_seconds=30)
        no_role_ctx = self._private_context()
        no_role_ctx.role_binding = NS(role=None)
        self.assertIsNone(screen_scene_snapshot(no_role_app, no_role_ctx, AT))
        self.assertEqual(no_role_calls, [])


class Issue832TogetherOperationRetryAndHashTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_not_committed_invocation_is_reclaimable_while_succeeded_and_unknown_stay_protected(self):
        with TemporaryDirectory(prefix="v2-issue832-") as tmp:
            plugin = FakeTogether()
            provider = NativeTogetherProvider(plugin, Path(tmp), version=PRODUCT_VERSION)

            # 1. First attempt fails before commit (missing model)
            req = native_request(provider, "together.room.create", invocation="mobile-retry-1", arguments={"mode": "call"})
            failing = copy.deepcopy(req)
            failing["host_execution"]["model_provider_id"] = "missing"
            failed_receipt = await provider.execute(failing, before_dispatch=lambda: True)
            self.assertEqual((failed_receipt["state"], failed_receipt["effect"]), ("failed", "not_committed"))
            self.assertEqual(plugin.issued, 0)

            # 2. Retry SAME invocation_id after fixing model (different identity!) -> MUST SUCCEED, not 409!
            succeeded = await provider.execute(req, before_dispatch=lambda: True)
            self.assertEqual((succeeded["state"], succeeded["effect"]), ("succeeded", "committed"))
            self.assertEqual(plugin.issued, 1)

            # 3. Once succeeded, reusing same invocation_id with DIFFERENT arguments MUST still raise 409 mismatch
            conflict = copy.deepcopy(req)
            conflict["arguments"] = {"mode": "watch"}
            with self.assertRaisesRegex(NativeTogetherError, "native_original_invocation_mismatch"):
                await provider.execute(conflict, before_dispatch=lambda: True)

            # 4. Once succeeded, identical replay returns cached receipt without opening a second room
            replayed = await provider.execute(req, before_dispatch=lambda: True)
            self.assertEqual(replayed, succeeded)
            self.assertEqual(plugin.issued, 1)

            # 5. Unknown/unconfirmed state MUST remain protected against identity conflict too
            req_unknown = native_request(provider, "together.room.create", invocation="mobile-unknown-1", arguments={"mode": "call"})
            scope = scope_key(req_unknown["context"])
            op, fresh = provider.store.begin(scope, req_unknown)
            self.assertTrue(fresh)
            provider.store.update(scope, op["id"], state="unknown", effect="unknown", error="execution_interrupted")
            different_unknown = copy.deepcopy(req_unknown)
            different_unknown["arguments"] = {"mode": "watch"}
            with self.assertRaisesRegex(NativeTogetherError, "native_original_invocation_mismatch"):
                await provider.execute(different_unknown, before_dispatch=lambda: True)

    def test_package_hash_conflict_message_is_clear_and_actionable(self):
        tmp = TemporaryDirectory(prefix="v2-issue832-hash-")
        self.addCleanup(tmp.cleanup)
        runtime = StorageRuntime.open(data_root=Path(tmp.name), installation_id="hash-msg")
        self.addCleanup(runtime.close)
        repo = CapabilityRepository(runtime)
        first = CapabilityPackageDescriptor(
            extension_id="together",
            package_id="astrbot_plugin_private_companion.together",
            publisher_id="menglimi",
            package_version="7.3.0",
            artifact_hash="hash-aaa",
            capabilities=("together.room.create",),
            dataset_owners=("together.native",),
        )
        require(repo.register_descriptor(first, registered_at=AT))
        changed = replace(first, artifact_hash="hash-bbb")
        outcome = register_native_replacement(
            runtime,
            changed,
            previous_package_id=first.package_id,
            observed_at=AT,
        )
        self.assertFalse(outcome.is_success)
        self.assertEqual(outcome.failure.code, "capability_descriptor_conflict")
        self.assertIn("代码校验哈希", outcome.failure.reason)
        self.assertIn("递增插件版本号", outcome.failure.reason)


class Issue835ReferenceSwitchDecouplingTests(unittest.TestCase):
    def setUp(self):
        self._base = _reference_asset_tests.ReferenceAssetServiceTests()
        self._base.setUp()
        self.addCleanup(self._base.doCleanups)
        self.runtime = self._base.runtime
        self.tasks = self._base.tasks
        self.context = self._base.context
        self.bytes_ = self._base.bytes_
        self.digest = self._base.digest

    def test_reference_switch_off_gates_only_images_and_keeps_text_wardrobe_and_daily_outfit(self):
        ref_opts = _reference_asset_tests.SettingsStub(
            enabled=True, daily_outfit_enabled=True, daily_outfit_rotation_days=14
        )
        wardrobe_opts = _reference_asset_tests.WardrobeSettingsStub(
            enabled=True, prompt_enabled=True, tendency="偏爱深色风衣", image_prompt="自然光"
        )
        svc = ReferenceAssetService(
            self.runtime,
            self.tasks,
            settings=ref_opts,
            authorize=lambda context, **kwargs: True,
            wardrobe=wardrobe_opts,
        )
        asset = svc.register_asset(
            self.context,
            asset_id="outfit-img-1",
            kind=ReferenceKind.REFERENCE_IMAGE,
            role=ReferenceRole.OUTFIT,
            content_hash=self.digest,
            size_bytes=len(self.bytes_),
            mime_type="image/png",
            media_ref=OWNED_REFERENCE_PREFIX + "test:outfit-img-1",
            payload=self.bytes_,
        ).unwrap()
        svc.register_item(
            self.context,
            item_id="coat-1",
            name="黑色长风衣",
            description="微凉天气常穿的纯文字衣柜外套",
            tags=("通勤",),
            asset_id=asset.asset_id,
        ).unwrap()
        # Turn OFF reference images; keep wardrobe + daily_outfit enabled
        ref_opts.options["enabled"] = False
        chosen = svc.prepare_daily_outfit(self.context, local_date="2026-11-22")
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.item_id, "coat-1")

        material = svc.prompt_material(self.context, local_date="2026-11-22")
        self.assertEqual(material["references"], ())
        self.assertIsNotNone(material["wardrobe"])
        self.assertEqual(material["wardrobe"]["tendency"], "偏爱深色风衣")
        self.assertEqual(material["wardrobe"]["items"][0]["name"], "黑色长风衣")
        self.assertIsNone(material["wardrobe"]["items"][0]["asset_id"])
        self.assertIsNotNone(material["daily_outfit"])
        self.assertEqual(material["daily_outfit"]["name"], "黑色长风衣")
        self.assertIsNone(material["daily_outfit"]["asset_id"])
        rendered_prompt = image_reference_instruction(material)
        self.assertIn("偏爱深色风衣", rendered_prompt)
        self.assertIn("黑色长风衣", rendered_prompt)

        # Group scope: default group_enabled=False blocks reference access; even when
        # group_enabled=True, private wardrobe/daily_outfit never leak into group chats.
        group_scope = replace(
            self.context.scope,
            kind=ScopeKind.GROUP,
            native_scope_id="grp-1",
            conversation_id="grp-1",
            session_id="grp-1",
        )
        group_ctx = replace(
            self.context,
            role_binding=replace(self.context.role_binding, scope=group_scope),
            audience=AudienceRef(group_scope, self.context.audience.recipients),
        )
        with self.assertRaises(TaskError) as caught:
            svc.prompt_material(group_ctx, local_date="2026-11-22")
        self.assertEqual(caught.exception.code, "photo_reference_group_disabled")
        ref_opts.options["group_enabled"] = True
        group_material = svc.prompt_material(group_ctx, local_date="2026-11-22")
        self.assertEqual(group_material, {"references": (), "wardrobe": None, "daily_outfit": None})

        # Generate-only route (no image.edit) with photo_reference.enabled=False still injects
        # text wardrobe and daily outfit without offering edit-only reference_ids instructions.
        from astrbot.core.provider.entities import ProviderRequest
        from astrbot_host.core_reply_image_prompt import inject_core_image_prompt
        from companion_v2.conversation.media.output_settings import DEFAULTS as IMAGE_DEFAULTS

        state = {}
        prepared = {
            "context": self.context,
            "native_input": NS(text="今天穿什么好，画张图看看", at=AT),
            "perception_context": json.dumps({"current_local_time": {"timezone": "Asia/Shanghai"}}),
        }
        session = NS(
            application=NS(
                runtime=self.runtime,
                tasks=self.tasks,
                messages=NS(reference_materials=svc, timezone_name="Asia/Shanghai"),
            ),
            context=self.context,
            prepared=prepared,
            state=state,
        )
        request = ProviderRequest(prompt="今天穿什么好", system_prompt="人格提示。")
        offered = (NS(spec=NS(action="image.generate")),)
        inject_core_image_prompt(session, request, offered, IMAGE_DEFAULTS)
        self.assertIn("文字衣柜与每日穿搭参考", request.system_prompt)
        self.assertNotIn("reference_ids", request.system_prompt)
        self.assertEqual(state["_image_reference_material"]["references"], ())
        self.assertEqual(state["_image_reference_material"]["wardrobe"]["tendency"], "偏爱深色风衣")
        self.assertEqual(state["_image_reference_material"]["daily_outfit"]["name"], "黑色长风衣")
        self.assertNotIn("asset_id", state["_image_reference_material"]["wardrobe"]["items"][0])
        self.assertNotIn("asset_id", state["_image_reference_material"]["daily_outfit"])
        text_prompt = text_wardrobe_instruction(material)
        self.assertIn("偏爱深色风衣", text_prompt)
        self.assertIn("黑色长风衣", text_prompt)


class CrossGroupAwarenessFixTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="v2-awareness-test-")
        self.addCleanup(self._tmp.cleanup)
        self.runtime = StorageRuntime.open(data_root=Path(self._tmp.name), installation_id="aw-test")
        self.addCleanup(self.runtime.close)
        role = SubjectRef("aw-test", SubjectKind.BOT, "role-kazusa")
        human = SubjectRef("aw-test", SubjectKind.HUMAN, "human-owner")
        principal = AuthenticatedHostPrincipal("aw-test", "astrbot", "aiocqhttp", "100001", "bot-1", "200001")
        actor = ResolvedIdentity(principal, human, "bind-1", 1, 1)

        def make_group(gid):
            scope = ScopeRef("aw-test", "aiocqhttp", ScopeKind.GROUP, gid, "bot-1", "200001", f"conv-{gid}", gid)
            return TaskContext(actor, RoleBinding(scope, role, 1), AudienceRef(scope, (human,)))

        self.group_a = make_group("1001")
        self.group_b = make_group("1002")
        self.acquaintance = FakeAcquaintance()
        self.acquaintance.private = True
        self.service = RoleAwareness(self.runtime, acquaintance=self.acquaintance)
        self.prompt = ConversationAwarenessContext(self.service)

    def test_max_items_5_to_8_validates_and_preserves_private_contact(self):
        self.assertEqual(MAX_ITEMS, 8)
        self.acquaintance.settings = FakeSettings(**{
            "person.acquaintance_enabled": True,
            "person.cross_window_awareness_enabled": True,
            "person.cross_window_cross_group_enabled": True,
            "person.cross_window_max_items": 7,
        })
        with self.runtime.transaction() as conn:
            for idx in range(7):
                self.service.record_input(
                    conn,
                    self.group_a,
                    AWARENESS_NOW + timedelta(seconds=idx),
                    f"群A第{idx}条公开话题",
                    f"ev-{idx}",
                )
        contribution = self.prompt.contribution(self.group_b, AWARENESS_NOW + timedelta(minutes=1))
        self.assertIsNotNone(contribution)
        payload = json.loads(contribution.payload)
        self.assertTrue(payload["private_contact"])
        self.assertEqual(len(payload["cross_group_activity"]), 7)

    def test_cross_group_independent_off_switch_keeps_private_contact_and_drops_cross_group(self):
        self.acquaintance.settings = FakeSettings(**{
            "person.acquaintance_enabled": True,
            "person.cross_window_awareness_enabled": True,
            "person.cross_window_cross_group_enabled": False,
            "person.cross_window_max_items": 6,
        })
        with self.runtime.transaction() as conn:
            self.service.record_input(conn, self.group_a, AWARENESS_NOW, "另一个群里聊的话题", "ev-off-1")
        contribution = self.prompt.contribution(self.group_b, AWARENESS_NOW + timedelta(minutes=1))
        self.assertIsNotNone(contribution)
        payload = json.loads(contribution.payload)
        self.assertEqual(payload, {"private_contact": True})

        # Without private_contact and with cross_group off -> returns None cleanly
        self.acquaintance.private = False
        self.assertIsNone(self.prompt.contribution(self.group_b, AWARENESS_NOW + timedelta(minutes=1)))


if __name__ == "__main__":
    unittest.main()
