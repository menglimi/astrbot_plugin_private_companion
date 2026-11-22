"""Native room context, real visible transcripts, and exact host synchronization."""

import asyncio
from dataclasses import replace
from datetime import timedelta
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from tests.multi_host_support import MultiFixture
from astrbot_host.mobile_together import MobileTogetherBridge
from astrbot_host.native_extensions.together.activity_methods import TogetherActivityMethods
from astrbot_host.native_extensions.together.model_methods import TogetherModelMethods
from astrbot_host.native_extensions.together.native_context import existing_host_conversation, resolve_room_context
from astrbot_host.native_extensions.together.native_transcripts import received_input
from astrbot_host.native_extensions.together.rooms_methods import TogetherRoomsMethods
from astrbot_host.native_extensions.together.settings_methods import TogetherSettingsMethods
from astrbot_host.native_extensions.together.speech_methods import TogetherSpeechMethods
from companion_v2.conversation.foreground import ContextContribution, ContributionState
from companion_v2.conversation.native_history import body, reference
from companion_v2.conversation.native_observations import TOGETHER_ASSISTANT_OWNER, TOGETHER_INPUT_OWNER
from companion_v2.execution.models import encode, require, stamp
from companion_v2.foundation.refs import ScopeKind
from companion_v2.memory.episodes import recent_dialogue_lines
from companion_v2.together.models import NativeRoomBinding, RoomMember, RoomSession
from companion_v2.together.provider import scope_key


class RoomRuntime(TogetherModelMethods, TogetherActivityMethods, TogetherRoomsMethods,
                  TogetherSpeechMethods, TogetherSettingsMethods):
    pass


class Socket:
    closed = False

    def __init__(self):
        self.fail = False
        self.payloads = []

    async def send_json(self, payload, **kwargs):
        if self.fail:
            raise RuntimeError("synthetic socket failure")
        self.payloads.append(dict(payload))


class NativeTogetherContextTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = await self.start_host()
        prepared = require(await self.controller.prepare_core_reply(self.event(identifier="room-context-seed")))
        await prepared["native_source_future"]
        self.context = prepared["context"]
        self.persona = await self.controller.session.bridge.verified_persona(
            self.context.scope, self.context.role_binding)
        self.runtime = RoomRuntime()
        self.runtime.controller, self.runtime.context = self.controller, self.native
        self.runtime.history_turns, self.runtime.enable_memory_context = 12, True
        self.runtime.record_visible_turns, self.runtime.sync_astrbot_conversation = True, False
        self.runtime.custom_system_prompt = "先听我分享。\n\n开心的地方可以分段聊。"
        self.runtime.model_hangup_enabled = False
        self.runtime.config = {}
        self.runtime._background_tasks = set()
        self.subtitles = []

        async def subtitle(text, **kwargs):
            self.subtitles.append(text)
        self.runtime._push_live_subtitle = subtitle
        self.socket = Socket()
        scope = self.context.scope
        binding = NativeRoomBinding(scope_key(json.loads(encode(self.context))), **self.persona,
            platform=scope.platform, session_id=scope.session_id, scope_kind=scope.kind.value)
        self.room = RoomSession("room-native-context", "ticket", "call", "200", self.socket, native_v2=binding)
        self.room.call_tts_enabled = False
        self.selected = (self.controller.session, self.app, self.context)
        self.admitted, self.active = True, True
        registry = self.controller.native_extensions
        original_active = registry.active_service
        self.patches = [
            patch.object(MobileTogetherBridge, "_target_context", side_effect=lambda _: self.selected),
            patch.object(MobileTogetherBridge, "_admitted", side_effect=lambda *args: object() if self.admitted else None),
            patch.object(registry, "active_service", side_effect=lambda name:
                         self.runtime if name == "together" and self.active else original_active(name)),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.before_tables = self._core_counts()

    def _core_counts(self):
        return {table: self.app.runtime.read_one("SELECT COUNT(*) AS n FROM " + table, ())["n"]
            for table in ("conversation_batches", "conversation_delivery_receipts",
                          "conversation_native_turns", "conversation_native_sends")}

    async def drain(self):
        while self.runtime._background_tasks:
            await asyncio.gather(*tuple(self.runtime._background_tasks))
        observer = self.controller.session.core_reply_observations
        while observer._pending:
            await asyncio.gather(*tuple(observer._pending))

    async def test_shared_role_personas_keep_exact_private_room_binding(self):
        bridge = self.controller.session.bridge
        selected = self.persona["persona_id"]
        projection = bridge.personas[(self.context.scope.bot_id, selected)]
        bridge.personas[(projection.bot_id, "group-persona")] = replace(
            projection, persona_id="group-persona")
        current = await bridge.verified_persona(self.context.scope, self.context.role_binding)
        self.assertEqual(current["persona_id"], selected)
        self.assertIsNotNone(await resolve_room_context(self.runtime, self.room))
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_memory_toggle_keeps_role_relationship_and_custom_prompt(self):
        reads = []

        def memory(owner, context, native_input, at, timezone_name):
            if owner is None:
                return ()
            reads.append(context.key)
            return (ContextContribution("room-memory", "memory", "room_memory", ContributionState.READY,
                                        '{"summary":"一起聊过天文书"}'),)

        role = NS(capture=lambda *args: {}, instruction=lambda *args: "角色档案仍然生效")
        relationship = NS(capture=lambda *args: {}, instruction=lambda *args: "相处称呼仍然生效")
        with patch("astrbot_host.core_reply_context._memory_materials", side_effect=memory), \
                patch.object(self.app.messages, "role_profile_context", role), \
                patch.object(self.app.messages, "relationship_profile_context", relationship):
            enabled = await self.runtime._build_system_prompt(self.room, query="那本天文书")
            self.runtime.enable_memory_context = False
            disabled = await self.runtime._build_system_prompt(self.room, query="那本天文书")
        self.assertIn("一起聊过天文书", enabled)
        self.assertNotIn("一起聊过天文书", disabled)
        self.assertEqual(reads, [self.context.key])
        self.assertIn("角色档案仍然生效", disabled)
        self.assertIn("相处称呼仍然生效", disabled)
        self.assertIn(self.runtime.custom_system_prompt, disabled)
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_stale_revoked_paused_guest_and_group_context_do_not_leak(self):
        self.admitted = False
        self.assertIsNone(await resolve_room_context(self.runtime, self.room))
        self.admitted = True
        self.active = False
        self.assertIsNone(await resolve_room_context(self.runtime, self.room))
        self.active = True
        self.native.selections["qq-main"] = "second"
        self.window_personas["qq-main:FriendMessage:200"] = "second"
        self.assertIsNone(await resolve_room_context(self.runtime, self.room))
        self.native.selections["qq-main"] = "original"
        self.window_personas["qq-main:FriendMessage:200"] = "original"
        self.room.members["201"] = RoomMember("201", "guest")
        self.assertIsNone(await resolve_room_context(self.runtime, self.room))
        received_input(self.runtime, self.room, "访客的话", turn_id="guest")
        await self.drain()
        self.assertIsNone(self.app.runtime.read_one(
            "SELECT 1 FROM conversation_native_observations WHERE event_id LIKE '%-guest'", ()))
        self.room.members.pop("201")
        self.room.native_v2.scope_kind = ScopeKind.GROUP.value
        self.assertIsNone(await resolve_room_context(self.runtime, self.room))
        self.room.native_v2 = None
        self.room.work_context = {"available": True, "observation": {"summary": "旧的屏幕上下文"}}
        self.assertEqual(await self.runtime._work_collaboration_context(self.room), {})
        self.assertEqual(self._core_counts(), self.before_tables)

    async def test_work_reads_existing_authorized_analysis_without_capture(self):
        calls = []
        captured_at = self.clock.now() - timedelta(seconds=20)

        class Repository:
            def latest_analysis(owner, **kwargs):
                from companion_v2.foundation.outcome import Outcome
                calls.append(kwargs)
                return Outcome.success((NS(analysis=NS(interpretation='{"summary":"正在修改草稿","scene":"写作"}'),
                    material=NS(capture=NS(captured_at=captured_at))),))

        with patch.object(self.app, "screen_repository", Repository()):
            self.room.mode = "work"
            result = await self.runtime._work_collaboration_context(self.room)
        self.assertEqual(result["observation"]["summary"], "正在修改草稿")
        self.assertEqual(result["observation"]["age_seconds"], 20)
        self.assertEqual(calls[0]["scope"], self.context.scope)
        self.assertEqual(calls[0]["resolved"], self.context.actor)
        self.assertFalse(result["tracking_enabled"])

    async def test_input_survives_model_failure_and_bot_requires_real_room_send(self):
        async def fail_model(*args, **kwargs):
            raise RuntimeError("synthetic model failure")
        self.runtime._generate_model_text = fail_model
        await self.runtime._reply_to_user(self.room, "这周我一直在读天文书", utterance_id="input-kept")
        await self.drain()
        self.socket.fail = True
        await self.runtime._synthesize_and_send(self.room, "没有投递的回复", display_source="reply")
        await self.drain()
        self.socket.fail = False
        await self.runtime._synthesize_and_send(self.room, "这本书的星图真漂亮", display_source="reply")
        await self.drain()

        rows = self.app.runtime.read_all("SELECT * FROM conversation_native_observations "
            "WHERE json_extract(source_json, '$.owner') IN (?, ?)",
            (TOGETHER_INPUT_OWNER, TOGETHER_ASSISTANT_OWNER))
        self.assertEqual(len(rows), 2)
        history = [body(self.app.messages, reference(row), self.context, self.clock.now()) for row in rows]
        self.assertEqual({item["text"] for item in history}, {"这周我一直在读天文书", "这本书的星图真漂亮"})
        self.assertTrue(all(item["execution_path"] == "together_room" for item in history))
        reply = next(item for item in history if item["role"] == "assistant")
        self.assertEqual(reply["send_time_basis"], "together_room_send_return")
        self.assertFalse(reply["platform_confirmed"])
        self.app.memory.query.begin(self.context, "room-sources")
        sources = require(self.app.memory.query.query(self.context, "room-sources", query_intent="", kind="sources"))
        self.assertTrue(any(item["excerpt"] == "这本书的星图真漂亮" for item in sources["items"]))
        lines = recent_dialogue_lines(self.app.runtime, self.app.memory.writer.access, self.context,
            {"start": stamp(self.clock.now() - timedelta(seconds=1)), "end": stamp(self.clock.now() + timedelta(seconds=1))})
        self.assertTrue(any(item["text"] == "这本书的星图真漂亮" for item in lines))
        self.assertEqual(self._core_counts(), self.before_tables)

    async def test_sync_uses_existing_native_id_and_never_creates_conversation(self):
        pairs = []

        async def add_pair(identifier, user, assistant):
            pairs.append((identifier, user, assistant))
        self.native.conversation_manager.add_message_pair = add_pair
        self.runtime.sync_astrbot_conversation = True
        await self.runtime._synthesize_and_send(self.room, "房间中的回复", display_source="reply",
            native_transcript={"event_id": "room-sync", "user_text": "房间中的问题"})
        await self.drain()
        self.assertEqual(pairs[0][0], "conversation-a")
        self.assertIn("一起房间", pairs[0][1]["content"])
        self.assertIn("一起房间", pairs[0][2]["content"])
        self.assertNotEqual(pairs[0][0], self.context.scope.conversation_id)
        self.native.conversations["qq-main"] = None
        manager, _, identifier = await existing_host_conversation(self.runtime, self.room)
        self.assertIsNone(manager)
        self.assertEqual(identifier, "")
        self.assertEqual(len(pairs), 1)

    def test_live_api_requires_active_service_and_settings_keep_paragraphs(self):
        registry = self.controller.native_extensions
        with patch.object(registry, "active_service", return_value=None), \
                patch.object(registry, "api", side_effect=AssertionError("paused Live must not be queried")):
            self.assertIsNone(self.runtime._live_native_api())
        prompt = "分享时像刚发现新东西一样开心。\n\n按语意分段。"
        updates = self.runtime._validate_page_settings({
            "conversation.system_prompt": prompt,
            "conversation.record_shared_experiences": True,
            "conversation.unknown_legacy_flag": True,
        })
        self.assertEqual(updates["conversation.system_prompt"], prompt)
        self.assertIs(updates["conversation.record_shared_experiences"], True)
        self.assertNotIn("conversation.unknown_legacy_flag", updates)
        self.assertIn("conversation.system_prompt", self.runtime._page_setting_values())
        self.assertIn("conversation.record_shared_experiences", self.runtime._page_setting_values())


if __name__ == "__main__":
    unittest.main()
