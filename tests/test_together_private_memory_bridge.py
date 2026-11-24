"""Together Room cross-session private memory bridge, recall tool, and privacy boundaries."""

import asyncio
import json
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from astrbot_host.mobile_together import MobileTogetherBridge
from astrbot_host.native_extensions.together.activity_methods import TogetherActivityMethods
from astrbot_host.native_extensions.together.media_methods import TogetherMediaMethods
from astrbot_host.native_extensions.together.model_methods import (
    MAX_ROOM_MEMORY_TOOL_STEPS,
    ROOM_MEMORY_RECALL_TOOL_NAME,
    TogetherModelMethods,
)
from astrbot_host.native_extensions.together.native_context import (
    canonical_private_session_id,
    recall_room_memory,
    resolve_room_context,
    room_background,
)
from astrbot_host.native_extensions.together.rooms_methods import TogetherRoomsMethods
from astrbot_host.native_extensions.together.settings_methods import TogetherSettingsMethods
from astrbot_host.native_extensions.together.speech_methods import TogetherSpeechMethods
from companion_v2.adapters.astrbot.memory_owner import MEMORY_OWNER_MODULE_NAMES
from companion_v2.adapters.astrbot.memory_providers import ExternalMemoryProviderRegistry
from companion_v2.adapters.astrbot.memory_push import memory_push_provider
from companion_v2.execution.models import encode, require
from companion_v2.foundation.refs import ScopeKind
from companion_v2.together.models import NativeRoomBinding, RoomMember, RoomSession, RoomTicketStore
from companion_v2.together.provider import scope_key
from tests.multi_host_support import MultiFixture
from tests.test_memory_linkage import FakeModule


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


class _FakeMemoryBridge:
    def __init__(self, *, compose_text="", search_items=None, delay=0.0, fail=False):
        self.compose_text = compose_text
        self.search_items = list(search_items or [])
        self.delay = delay
        self.fail = fail
        self.compose_calls = []
        self.search_calls = []
        self.events = []

    async def compose_context(self, *, query="", session_context=None, top_k=None, max_chars=None, **kwargs):
        self.compose_calls.append({"query": query, "session_context": dict(session_context or {}), "max_chars": max_chars})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("synthetic bridge failure")
        return self.compose_text

    async def search(self, query, *, session_context=None, top_k=None, **kwargs):
        self.search_calls.append({"query": query, "session_context": dict(session_context or {}), "top_k": top_k})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("synthetic search failure")
        return list(self.search_items)

    async def record_event(self, content, **kwargs):
        self.events.append({"content": content, "kwargs": dict(kwargs)})
        return kwargs.get("memory_id") or "pushed-mem-1"


class _FakeToolResponse:
    def __init__(self, text="", *, tool_names=None, tool_args=None, tool_ids=None):
        self.completion_text = text
        self.tools_call_name = list(tool_names or [])
        self.tools_call_args = list(tool_args or [])
        self.tools_call_ids = list(tool_ids or [])

    def to_openai_tool_calls_model(self):
        return []


class TogetherPrivateMemoryBridgeTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = await self.start_host()
        prepared = require(await self.controller.prepare_core_reply(self.event(identifier="room-mem-seed")))
        await prepared["native_source_future"]
        self.context = prepared["context"]
        self.persona = await self.controller.session.bridge.verified_persona(
            self.context.scope, self.context.role_binding
        )
        self.runtime = _RoomRuntime()
        self.runtime.controller = self.controller
        self.runtime.context = self.native
        self.runtime.history_turns = 12
        self.runtime.enable_memory_context = True
        self.runtime.record_visible_turns = True
        self.runtime.record_shared_experiences = True
        self.runtime.sync_astrbot_conversation = False
        self.runtime.custom_system_prompt = ""
        self.runtime.model_hangup_enabled = False
        self.runtime.config = {}
        self.runtime._background_tasks = set()
        self.runtime.rooms = {}
        self.runtime.detached_rooms = {}
        self.runtime.media_sources = {}
        self.runtime.ticket_store = RoomTicketStore(600)
        self.runtime.chat_provider_id = ""
        self.runtime.vision_provider_id = ""
        self.runtime.token_usage = NS(record=lambda **kwargs: None)
        self.socket = _Socket()
        scope = self.context.scope
        binding = NativeRoomBinding(
            scope_key(json.loads(encode(self.context))),
            **self.persona,
            platform=scope.platform,
            session_id=scope.session_id,
            scope_kind=scope.kind.value,
        )
        self.room = RoomSession("room-memory-bridge", "ticket", "watch", "200", self.socket, native_v2=binding)
        self.room.watch_tts_enabled = False
        self.room.call_tts_enabled = False
        self.selected = (self.controller.session, self.app, self.context)
        self.admitted = True
        self.active = True
        registry = self.controller.native_extensions
        original_active = registry.active_service
        self.patches = [
            patch.object(MobileTogetherBridge, "_target_context", side_effect=lambda _: self.selected),
            patch.object(MobileTogetherBridge, "_admitted", side_effect=lambda *args: object() if self.admitted else None),
            patch.object(
                registry,
                "active_service",
                side_effect=lambda name: self.runtime if name == "together" and self.active else original_active(name),
            ),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self._saved_modules = {name: sys.modules.get(name) for name in MEMORY_OWNER_MODULE_NAMES}
        self.addCleanup(self._restore_modules)

    def _restore_modules(self):
        for name, module in self._saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def _install_memory_companion(self, bridge):
        sys.modules[MEMORY_OWNER_MODULE_NAMES[0]] = FakeModule(bridge)

    def test_canonical_private_session_id(self):
        self.assertEqual(canonical_private_session_id("onebot_qq", "123456"), "onebot_qq:FriendMessage:123456")
        self.assertEqual(
            canonical_private_session_id("onebot_qq", "onebot_qq:FriendMessage:123456"),
            "onebot_qq:FriendMessage:123456",
        )
        self.assertEqual(canonical_private_session_id("", "123456"), "123456")

    async def test_delegated_memory_companion_bridges_private_context_and_respects_toggle(self):
        bridge = _FakeMemoryBridge(compose_text="<core_memory>上次一起约定周末去听钢琴音乐会。</core_memory>")
        self._install_memory_companion(bridge)

        prompt = await self.runtime._build_system_prompt(self.room, query="还记得周末约定吗")
        self.assertIn("周末去听钢琴音乐会", prompt)
        self.assertEqual(len(bridge.compose_calls), 1)
        ctx = bridge.compose_calls[0]["session_context"]
        self.assertEqual(ctx["scope"], "private")
        self.assertEqual(ctx["session_id"], f"{self.context.scope.platform}:FriendMessage:{self.context.scope.session_id}")
        self.assertEqual(ctx["user_id"], self.context.scope.native_scope_id)
        self.assertEqual(ctx["bot_id"], self.context.scope.account_id)
        self.assertEqual(ctx["persona_id"], self.persona["persona_id"])
        self.assertEqual(bridge.compose_calls[0]["max_chars"], 1600)

        # Disabling room memory toggle suppresses delegated memory completely
        self.runtime.enable_memory_context = False
        disabled = await self.runtime._build_system_prompt(self.room, query="还记得周末约定吗")
        self.assertNotIn("周末去听钢琴音乐会", disabled)
        self.assertEqual(len(bridge.compose_calls), 1)

    async def test_external_memory_provider_fallback_works_without_duplicating_memory_companion(self):
        plugin = object()
        existing_stars = list(self.native.get_all_stars()) if hasattr(self.native, "get_all_stars") else []
        with patch.object(
            self.native,
            "get_all_stars",
            return_value=[*existing_stars, NS(star_cls=plugin, activated=True)],
            create=True,
        ):
            registry = ExternalMemoryProviderRegistry(self.native, install=True)
            self.addCleanup(registry.close)
            read_calls = []

            async def ext_read(request):
                read_calls.append(request)
                return {"items": [{"id": "ext-1", "content": "第三方记忆里记着喜欢焦糖布丁", "occurred_at": "2026-10-01"}]}

            reg_id = registry.register("third_party_mem", plugin=plugin, read=ext_read)
            prompt = await self.runtime._build_system_prompt(self.room, query="喜欢吃什么甜点")
            self.assertIn("喜欢焦糖布丁", prompt)
            self.assertEqual(len(read_calls), 1)
            registry.unregister(reg_id)

            # When memory_companion is both delegated (non-empty) and registered, avoid duplicate injection
            mc_read_calls = []

            async def mc_ext_read(request):
                mc_read_calls.append(request)
                return {"items": [{"id": "mc-1", "content": "联动兜底记忆", "occurred_at": "2026-10-02"}]}

            registry.register("memory_companion", plugin=plugin, read=mc_ext_read)
            bridge = _FakeMemoryBridge(compose_text="已通过主桥接注入核心记忆。")
            self._install_memory_companion(bridge)
            prompt_dedup = await self.runtime._build_system_prompt(self.room, query="聊点什么")
            self.assertIn("已通过主桥接注入核心记忆", prompt_dedup)
            self.assertEqual(mc_read_calls, [])

            # If compose_context returns empty, gracefully fall back to registered memory_companion provider
            empty_bridge = _FakeMemoryBridge(compose_text="")
            self._install_memory_companion(empty_bridge)
            prompt_fallback = await self.runtime._build_system_prompt(self.room, query="聊点什么")
            self.assertIn("联动兜底记忆", prompt_fallback)
            self.assertEqual(len(mc_read_calls), 1)

    async def test_persona_prompt_edit_keeps_room_memory_while_persona_switch_still_blocks(self):
        bridge = _FakeMemoryBridge(compose_text="私聊里提过喜欢下雨天。")
        self._install_memory_companion(bridge)
        registry = self.controller.native_extensions
        edited = {**self.persona, "prompt": self.persona["prompt"] + "\n轻微润色补充一句。"}
        with patch.object(registry, "persona_guard", return_value=edited):
            resolved = await resolve_room_context(self.runtime, self.room)
            self.assertIsNotNone(resolved)
            bg = await room_background(self.runtime, self.room, "今天下雨了吗")
            self.assertIn("私聊里提过喜欢下雨天", bg)

        switched = {**self.persona, "persona_id": "another-persona"}
        with patch.object(registry, "persona_guard", return_value=switched):
            self.assertIsNone(await resolve_room_context(self.runtime, self.room))
            self.assertEqual(await room_background(self.runtime, self.room, "今天下雨了吗"), "")

    async def test_guest_group_and_timeout_never_leak_or_stall(self):
        bridge = _FakeMemoryBridge(compose_text="绝对不能泄露给访客的私聊秘密")
        self._install_memory_companion(bridge)

        # Guest blocks room_background and recall_room_memory completely
        self.room.members["guest-1"] = RoomMember("guest-1", "guest")
        self.assertEqual(await room_background(self.runtime, self.room, "秘密"), "")
        self.assertEqual(await recall_room_memory(self.runtime, self.room, "秘密"), "")
        self.assertEqual(bridge.compose_calls, [])
        self.assertEqual(bridge.search_calls, [])
        self.room.members.pop("guest-1")

        # V2 memory access denial blocks delegated room background and recall completely
        with patch.object(self.app.memory.writer.access, "check", side_effect=PermissionError("denied")):
            bg_denied = await room_background(self.runtime, self.room, "秘密")
            self.assertNotIn("绝对不能泄露给访客的私聊秘密", bg_denied)
            self.assertEqual(await recall_room_memory(self.runtime, self.room, "秘密"), "")
            self.assertEqual(bridge.compose_calls, [])
            self.assertEqual(bridge.search_calls, [])

        # Slow bridge times out gracefully without raising
        slow_bridge = _FakeMemoryBridge(compose_text="很慢的记忆", delay=0.5)
        self._install_memory_companion(slow_bridge)
        with patch("astrbot_host.native_extensions.together.native_context.ROOM_MEMORY_TIMEOUT_SECONDS", 0.05):
            bg = await room_background(self.runtime, self.room, "超时测试")
            self.assertNotIn("很慢的记忆", bg)
            self.assertEqual(await recall_room_memory(self.runtime, self.room, "超时测试", timeout_seconds=0.05), "")

    async def test_bounded_readonly_memory_recall_tool_loop_and_guest_exclusion(self):
        bridge = _FakeMemoryBridge(
            compose_text="基础核心记忆。",
            search_items=[{"content": "7月3日聊过看牙和约定", "occurred_at": "2026-07-03"}],
        )
        self._install_memory_companion(bridge)

        tool_calls_seen = []

        class _ToolCapableProvider:
            def meta(self):
                return {"id": "mock-tool-llm", "model": "mock-tool-llm"}

            async def text_chat(self, *, prompt=None, contexts=None, system_prompt=None,
                                image_urls=None, func_tool=None, tool_calls_result=None, **kwargs):
                tool_calls_seen.append({
                    "has_tool": func_tool is not None,
                    "tool_names": [t.name for t in getattr(func_tool, "tools", ()) or ()],
                    "results_len": len(tool_calls_result or []),
                })
                if len(tool_calls_seen) == 1:
                    return _FakeToolResponse(
                        "",
                        tool_names=[ROOM_MEMORY_RECALL_TOOL_NAME],
                        tool_args=[{"query": "7月看牙约定"}],
                        tool_ids=["call-1"],
                    )
                return _FakeToolResponse("记得呀，7月3日聊过看牙那次约定。")

        provider = _ToolCapableProvider()
        self.runtime._room_chat_provider = lambda _room: provider
        reply = await self.runtime._generate_model_text(self.room, "你还记得7月看牙那件事吗？")
        self.assertIn("7月3日", reply)
        self.assertEqual(len(bridge.search_calls), 1)
        self.assertEqual(bridge.search_calls[0]["query"], "7月看牙约定")
        self.assertEqual(tool_calls_seen[0]["tool_names"], [ROOM_MEMORY_RECALL_TOOL_NAME])
        self.assertEqual(tool_calls_seen[1]["results_len"], 1)

        # Max step cap prevents endless tool loops: step MAX_ROOM_MEMORY_TOOL_STEPS removes func_tool
        loop_seen = []

        class _AlwaysToolProvider:
            def meta(self):
                return {"id": "loop-llm", "model": "loop-llm"}

            async def text_chat(self, *, prompt=None, contexts=None, system_prompt=None,
                                image_urls=None, func_tool=None, tool_calls_result=None, **kwargs):
                loop_seen.append(func_tool is not None)
                if func_tool is not None:
                    return _FakeToolResponse(
                        "",
                        tool_names=[ROOM_MEMORY_RECALL_TOOL_NAME],
                        tool_args=[{"query": f"第{len(loop_seen)}次查询"}],
                        tool_ids=[f"call-{len(loop_seen)}"],
                    )
                return _FakeToolResponse("查完直接回答你。")

        self.runtime._room_chat_provider = lambda _room: _AlwaysToolProvider()
        bounded_reply = await self.runtime._generate_model_text(self.room, "帮我查很多次")
        self.assertEqual(bounded_reply, "查完直接回答你。")
        self.assertEqual(loop_seen, [True] * MAX_ROOM_MEMORY_TOOL_STEPS + [False])

        # 3 parallel tool calls in one turn must all get matching tool_call_ids while capping execution at 2
        bridge.search_calls.clear()
        parallel_results_seen = []

        class _ParallelToolProvider:
            def meta(self):
                return {"id": "parallel-llm", "model": "parallel-llm"}

            async def text_chat(self, *, prompt=None, contexts=None, system_prompt=None,
                                image_urls=None, func_tool=None, tool_calls_result=None, **kwargs):
                if not tool_calls_result:
                    return _FakeToolResponse(
                        "",
                        tool_names=[ROOM_MEMORY_RECALL_TOOL_NAME] * 3,
                        tool_args=[{"query": "词一"}, {"query": "词二"}, {"query": "词三"}],
                        tool_ids=["p-1", "p-2", "p-3"],
                    )
                last = tool_calls_result[-1]
                segs = getattr(last, "tool_calls_result", None) or last.get("tool_outputs", [])
                parallel_results_seen.append([
                    getattr(seg, "tool_call_id", None) or (seg.get("tool_call_id") if isinstance(seg, dict) else None)
                    for seg in segs
                ])
                return _FakeToolResponse("三条都对齐返回了。")

        self.runtime._room_chat_provider = lambda _room: _ParallelToolProvider()
        parallel_reply = await self.runtime._generate_model_text(self.room, "一次问三个主题")
        self.assertEqual(parallel_reply, "三条都对齐返回了。")
        self.assertEqual(parallel_results_seen, [["p-1", "p-2", "p-3"]])
        self.assertEqual(len(bridge.search_calls), 2)

        # Guest room never gets memory recall tool attached
        guest_seen = []

        class _GuestCheckProvider:
            def meta(self):
                return {"id": "guest-llm", "model": "guest-llm"}

            async def text_chat(self, *, prompt=None, contexts=None, system_prompt=None,
                                image_urls=None, func_tool=None, tool_calls_result=None, **kwargs):
                guest_seen.append(func_tool is not None)
                return _FakeToolResponse("普通回复")

        self.room.members["guest-2"] = RoomMember("guest-2", "guest")
        self.runtime._room_chat_provider = lambda _room: _GuestCheckProvider()
        await self.runtime._generate_model_text(self.room, "问一下私聊的事")
        self.assertEqual(guest_seen, [False])
        self.room.members.pop("guest-2")

    async def test_shared_experience_push_uses_canonical_private_umo_and_keeps_raw_object_id(self):
        bridge = _FakeMemoryBridge()
        self._install_memory_companion(bridge)
        self.app.memory_push = memory_push_provider(None)
        self.room.media_state = {"title": "钢琴独奏会", "current_time": 60.0}
        self.room.record_visual_digest("聚光灯下的三角钢琴演奏", mode="watch", source="watch_observation")
        self.room.append_turn("user", "这一段弹得真好", history_turns=12)
        self.room.append_turn("assistant", "灯光和琴声都很搭", history_turns=12)

        await self.runtime.close_room(self.room)
        self.assertEqual(len(bridge.events), 1)
        kw = bridge.events[0]["kwargs"]
        self.assertEqual(kw["session_id"], f"{self.context.scope.platform}:FriendMessage:{self.context.scope.session_id}")
        self.assertEqual(kw["object"]["id"], "200")


if __name__ == "__main__":
    unittest.main()
