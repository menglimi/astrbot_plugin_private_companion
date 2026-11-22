"""Tests for private-chat tool profile pruning (#812) and turn-merge/repeat guard (#611)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import json
from types import SimpleNamespace as NS
import unittest

from astrbot.core.agent.tool import FunctionTool, ToolSet

from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA
from astrbot_host.core_reply_cleanup import clean_reply_text
from astrbot_host.core_reply_perception import _compact_private_reply_context
from astrbot_host.core_reply_tools import (
    CoreReplyToolSession,
    TOOL_INSTRUCTION,
    _OfferedTool,
    inject_core_reply_tools,
    private_tool_group,
    private_tool_manifest,
)
from companion_v2.configuration.models import ConfigLayer, ConfigurationError
from companion_v2.conversation.diagnostic_tools import DIAGNOSTIC_INSTRUCTION
from companion_v2.conversation.private_reply_context import (
    BEFORE_TURNS,
    INSTRUCTION as PRIVATE_REPLY_CONTEXT_INSTRUCTION,
    capture_private_reply_context,
)
from companion_v2.conversation.relay_settings import DEFAULTS as RELAY_DEFAULTS, GROUP as RELAY_GROUP, target_for as relay_target_for
from companion_v2.conversation.relay_tools import RELAY_ACTIONS, tool_descriptors as relay_tool_descriptors
from companion_v2.conversation.reply_settings import (
    GROUP as REPLY_GROUP,
    LEAN_PRIVATE_TOOL_GROUPS,
    PRIVATE_TOOL_DEFAULTS,
    PRIVATE_TOOL_GROUPS,
    PRIVATE_TOOL_GROUP_ORDER,
    PRIVATE_TOOL_PROFILES,
    private_tool_groups_set,
    private_tool_settings,
    target_for as reply_target_for,
    validate as validate_reply_settings,
)
from companion_v2.conversation.segmentation import SegmentationPolicy
from companion_v2.execution.models import TaskError, encode, stamp
from companion_v2.model_gateway.cache_diagnostics import signature_for_payload
from companion_v2.model_gateway.models import ToolSpec
from dataclasses import asdict
from tests.multi_host_support import MultiFixture


def _dummy_spec(action: str) -> ToolSpec:
    return ToolSpec(action, f"dummy {action}", encode({"type": "object", "properties": {}, "additionalProperties": False}))


class _HostPersonaTool(FunctionTool):
    def __init__(self, name: str):
        super().__init__(
            name=name,
            description=f"host persona tool {name}",
            parameters={"type": "object", "properties": {}},
        )

    async def call(self, context, **kwargs):
        return "{}"


def _tools_wire_list(func_tool) -> list[dict]:
    tools = list(getattr(func_tool, "tools", ()))
    return [{"name": tool.name, "description": tool.description, "parameters": tool.parameters} for tool in tools]


class PrivateToolProfileSettingsTests(unittest.TestCase):
    def test_private_tool_defaults_and_group_parsing(self):
        self.assertEqual(PRIVATE_TOOL_DEFAULTS["private_tool_profile"], "full")
        self.assertEqual(private_tool_groups_set(PRIVATE_TOOL_DEFAULTS["private_tool_groups"]), PRIVATE_TOOL_GROUPS)
        self.assertEqual(private_tool_groups_set("agenda、calendar"), frozenset(("agenda", "calendar")))
        self.assertEqual(private_tool_groups_set("agenda, calendar memory"), frozenset(("agenda", "calendar", "memory")))
        self.assertEqual(private_tool_groups_set('["agenda", "relay"]'), frozenset(("agenda", "relay")))
        self.assertEqual(private_tool_groups_set(""), frozenset())

    def test_private_tool_validation_rejects_invalid_values(self):
        with self.assertRaises(ConfigurationError):
            private_tool_settings({"private_tool_profile": "unknown"})
        with self.assertRaises(ConfigurationError):
            private_tool_groups_set("agenda、not_a_group")
        with self.assertRaises(ConfigurationError):
            private_tool_groups_set("agenda、agenda")
        with self.assertRaises(ConfigurationError):
            private_tool_groups_set('{"agenda": true}')

    def test_legacy_reply_snapshot_without_private_tool_fields_remains_valid(self):
        legacy = {**asdict(SegmentationPolicy()), "streaming": False, "part_gap_ms": 0}
        validate_reply_settings(legacy)
        with_private = {**legacy, "private_tool_profile": "custom", "private_tool_groups": "agenda、calendar"}
        validate_reply_settings(with_private)

    def test_private_tool_manifest_filters_by_profile_and_functional_groups(self):
        offered = (
            _OfferedTool(_dummy_spec("memo.create"), "agenda"),
            _OfferedTool(_dummy_spec("calendar.query"), "calendar"),
            _OfferedTool(_dummy_spec("memory.query"), "memory"),
            _OfferedTool(_dummy_spec("role.timeline.query"), "timeline"),
            _OfferedTool(_dummy_spec("interaction.poke"), "interaction"),
            _OfferedTool(_dummy_spec("image.generate"), "external"),
            _OfferedTool(_dummy_spec("content.authoring.plan"), "authoring"),
            _OfferedTool(_dummy_spec("content.work.create"), "external"),
            _OfferedTool(_dummy_spec("diagnostics.inspect"), "diagnostics"),
            _OfferedTool(_dummy_spec("relay_message"), "external"),
            _OfferedTool(_dummy_spec("phone.task.start"), "phone"),
            _OfferedTool(_dummy_spec("turn.evidence"), "evidence"),
        )
        self.assertEqual(private_tool_group("diagnostics", "diagnostics.inspect"), "diagnostics")
        self.assertEqual(private_tool_group("external", "relay_message"), "relay")
        self.assertEqual(private_tool_group("external", "image.generate"), "media")
        self.assertEqual(private_tool_group("authoring", "content.authoring.plan"), "extensions")
        self.assertEqual(private_tool_group("external", "content.work.create"), "extensions")
        self.assertEqual(private_tool_group("phone", "phone.task.start"), "extensions")

        media_only = {item.spec.action for item in private_tool_manifest(offered, "custom", "media")}
        self.assertEqual(media_only, {"image.generate"})
        extensions_only = {item.spec.action for item in private_tool_manifest(offered, "custom", "extensions")}
        self.assertEqual(extensions_only, {"content.authoring.plan", "content.work.create", "phone.task.start"})

        full = private_tool_manifest(offered, "full")
        self.assertEqual(len(full), len(offered))

        lean_actions = {item.spec.action for item in private_tool_manifest(offered, "lean")}
        self.assertEqual(
            lean_actions,
            {"memo.create", "calendar.query", "memory.query", "interaction.poke", "turn.evidence"},
        )
        self.assertEqual(LEAN_PRIVATE_TOOL_GROUPS, frozenset(("agenda", "calendar", "memory", "interaction")))

        custom_actions = {item.spec.action for item in private_tool_manifest(offered, "custom", "agenda、calendar")}
        self.assertEqual(custom_actions, {"memo.create", "calendar.query", "turn.evidence"})

        interaction_only = {item.spec.action for item in private_tool_manifest(offered, "custom", "interaction")}
        self.assertEqual(interaction_only, {"interaction.poke"})
        self.assertNotIn("turn.evidence", interaction_only)

        self.assertEqual(private_tool_manifest(offered, "none"), ())
        self.assertEqual(set(PRIVATE_TOOL_GROUP_ORDER), PRIVATE_TOOL_GROUPS)
        self.assertEqual(PRIVATE_TOOL_PROFILES, frozenset(("full", "lean", "custom", "none")))


class PrivateToolProfileHostTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = await self.start_host()
        self.observer = self.controller.session.core_reply_observations

    async def flush(self):
        while pending := self.observer._pending - self.observer._intervention_tasks:
            await asyncio.gather(*tuple(pending))

    async def _prepare(self, text: str, event_id: str, **kwargs):
        event = self.event(text=text, identifier=event_id, **kwargs)
        prepared = (await self.controller.prepare_core_reply(event)).unwrap()
        await self.flush()
        async def _authorize(_prepared):
            from companion_v2.foundation.outcome import Outcome
            return Outcome.success(True)

        event.set_extra(CORE_REPLY_CONTEXT_EXTRA, {
            "prepared": prepared,
            "authorize": _authorize,
        })
        return event, prepared

    def _set_role_reply_settings(self, context, changes: dict):
        target = reply_target_for(context, ConfigLayer.ROLE)
        fields = self.app.configuration.registry.group(REPLY_GROUP)
        owner = self.app.configuration._owners[REPLY_GROUP]
        current = owner.read(target, fields)
        prefixed = {REPLY_GROUP + "." + k: v for k, v in changes.items()}
        owner.write(
            target,
            fields,
            changes=prefixed,
            reset=(),
            expected_revision=current.revision,
            observed_at=self.clock.now(),
        )

    def _set_window_relay_settings(self, context, changes: dict):
        target = relay_target_for(context)
        fields = self.app.configuration.registry.group(RELAY_GROUP)
        owner = self.app.configuration._owners[RELAY_GROUP]
        current = owner.read(target, fields)
        merged = {**RELAY_DEFAULTS, **{k.removeprefix(RELAY_GROUP + "."): v.value for k, v in current.fields.items()}}
        merged.update(changes)
        prefixed = {RELAY_GROUP + "." + k: v for k, v in merged.items()}
        owner.write(
            target,
            fields,
            changes=prefixed,
            reset=(),
            expected_revision=current.revision,
            observed_at=self.clock.now(),
        )

    async def test_private_tool_profiles_prune_companion_tools_and_preserve_persona_tools(self):
        event, prepared = await self._prepare("你好呀", "priv-full-1")
        persona_tools = [_HostPersonaTool("persona_web_search"), _HostPersonaTool("persona_mcp_notes")]

        # 1. Default full profile mounts Companion tools + diagnostics + persona tools.
        req_full = NS(prompt="你好呀", system_prompt="原人格", func_tool=ToolSet(tools=list(persona_tools)))
        actions_full = inject_core_reply_tools(event, req_full, application=self.app)
        self.assertTrue(any(action.startswith("diagnostics.") for action in actions_full))
        self.assertTrue(any(action.startswith("calendar.") for action in actions_full))
        self.assertTrue(any(action.startswith("memory.") for action in actions_full))
        self.assertIn(TOOL_INSTRUCTION, req_full.system_prompt)
        self.assertIn(DIAGNOSTIC_INSTRUCTION, req_full.system_prompt)
        full_names = {tool.name for tool in req_full.func_tool.tools}
        self.assertTrue({"persona_web_search", "persona_mcp_notes"} <= full_names)

        # 2. Role-level custom profile (#812 scenario: keep calendar+memory, drop diagnostics/relay/records/etc.)
        self._set_role_reply_settings(prepared["context"], {
            "private_tool_profile": "custom",
            "private_tool_groups": "calendar、memory",
        })
        event_c, prepared_c = await self._prepare("帮我记一下明天的安排", "priv-custom-1")
        self.assertEqual(prepared_c["private_tool_profile"], "custom")
        self.assertEqual(set(prepared_c["private_tool_groups"]), {"calendar", "memory"})
        req_custom = NS(prompt="帮我记一下明天的安排", system_prompt="原人格", func_tool=ToolSet(tools=list(persona_tools)))
        actions_custom = inject_core_reply_tools(event_c, req_custom, application=self.app)
        self.assertTrue(any(action.startswith("calendar.") for action in actions_custom))
        self.assertTrue(any(action.startswith("memory.") for action in actions_custom))
        self.assertIn("turn.evidence", actions_custom)
        self.assertFalse(any(action.startswith("diagnostics.") for action in actions_custom))
        self.assertFalse(any(action in RELAY_ACTIONS for action in actions_custom))
        self.assertNotIn(DIAGNOSTIC_INSTRUCTION, req_custom.system_prompt)
        custom_names = {tool.name for tool in req_custom.func_tool.tools}
        self.assertTrue({"persona_web_search", "persona_mcp_notes"} <= custom_names)
        self.assertLess(len(actions_custom), len(actions_full))

        # Execution-time guard also rejects pruned tool actions.
        session_c = event_c.get_extra(CORE_REPLY_CONTEXT_EXTRA)["_tool_session"]
        denied_payload = json.loads(await session_c.call(
            "diagnostics.explain", NS(context=NS(event=event_c)), {"topic": "memory"}))
        self.assertEqual(denied_payload["state"], "failed")
        self.assertEqual(denied_payload["code"], "core_tool_unavailable")

        # 3. Lean profile keeps core groups + preserves persona tools + shrinks tool count.
        self._set_role_reply_settings(prepared["context"], {"private_tool_profile": "lean"})
        event_l, _ = await self._prepare("聊聊今天", "priv-lean-1")
        req_lean = NS(prompt="聊聊今天", system_prompt="原人格", func_tool=ToolSet(tools=list(persona_tools)))
        actions_lean = inject_core_reply_tools(event_l, req_lean, application=self.app)
        self.assertFalse(any(action.startswith("diagnostics.") for action in actions_lean))
        self.assertFalse(any(action in RELAY_ACTIONS for action in actions_lean))
        self.assertNotIn(DIAGNOSTIC_INSTRUCTION, req_lean.system_prompt)
        lean_names = {tool.name for tool in req_lean.func_tool.tools}
        self.assertTrue({"persona_web_search", "persona_mcp_notes"} <= lean_names)

        # 4. None profile removes all Companion tools & TOOL_INSTRUCTION while preserving persona tools.
        self._set_role_reply_settings(prepared["context"], {"private_tool_profile": "none"})
        event_n, _ = await self._prepare("纯聊天", "priv-none-1")
        req_none = NS(prompt="纯聊天", system_prompt="原人格", func_tool=ToolSet(tools=list(persona_tools)))
        actions_none = inject_core_reply_tools(event_n, req_none, application=self.app)
        self.assertEqual(actions_none, ())
        self.assertEqual(req_none.system_prompt, "原人格")
        self.assertEqual([tool.name for tool in req_none.func_tool.tools],
                         ["persona_mcp_notes", "persona_web_search"])

    async def test_private_tools_hash_stays_byte_stable_across_turns(self):
        _, seed = await self._prepare("初始化", "priv-hash-seed")
        self._set_role_reply_settings(seed["context"], {"private_tool_profile": "lean"})
        persona_tools = [_HostPersonaTool("persona_b"), _HostPersonaTool("persona_a")]
        signatures = []
        for idx, text in enumerate(("今天天气怎么样", "顺便看看刚才那张照片", "好的明天提醒我"), 1):
            self.clock.advance(timedelta(seconds=30))
            event, _ = await self._prepare(text, f"priv-hash-{idx}")
            req = NS(prompt=text, system_prompt="固定人格", func_tool=ToolSet(tools=list(persona_tools)))
            inject_core_reply_tools(event, req, application=self.app)
            sig = signature_for_payload(
                "test-model",
                [{"role": "system", "content": req.system_prompt}, {"role": "user", "content": text}],
                _tools_wire_list(req.func_tool),
            )
            signatures.append(sig)
        self.assertEqual(len({sig.tools_hash for sig in signatures}), 1)
        self.assertEqual(len({sig.system_hash for sig in signatures}), 1)

    async def test_disabled_window_relay_omits_relay_tools_in_private_catalog(self):
        event, prepared = await self._prepare("检查递话工具", "priv-relay-on")
        session_on = CoreReplyToolSession(event, event.get_extra(CORE_REPLY_CONTEXT_EXTRA), self.app)
        # Ensure relay tools appear when external catalog provides them and relay is enabled.
        offered_with_relay = session_on._prune_disabled_private_relay([
            _OfferedTool(desc.tool, "external") for desc in relay_tool_descriptors()
        ])
        self.assertEqual({item.spec.action for item in offered_with_relay}, set(RELAY_ACTIONS))

        # Disable both admin relay and member relay on the window.
        self._set_window_relay_settings(prepared["context"], {
            "enabled": False,
            "member_relay_enabled": False,
        })
        offered_pruned = session_on._prune_disabled_private_relay([
            _OfferedTool(desc.tool, "external") for desc in relay_tool_descriptors()
        ])
        self.assertEqual(offered_pruned, [])

        # Re-enabling member_relay_enabled alone restores relay tools.
        self._set_window_relay_settings(prepared["context"], {
            "enabled": False,
            "member_relay_enabled": True,
        })
        offered_restored = session_on._prune_disabled_private_relay([
            _OfferedTool(desc.tool, "external") for desc in relay_tool_descriptors()
        ])
        self.assertEqual({item.spec.action for item in offered_restored}, set(RELAY_ACTIONS))

    async def test_pruned_media_group_skips_image_access_provisioning_and_diagnostics(self):
        from unittest.mock import patch

        _, seed = await self._prepare("初始化", "priv-media-seed")
        persona_tools = [_HostPersonaTool("persona_search")]
        for label, cfg in (
            ("lean", {"private_tool_profile": "lean"}),
            ("none", {"private_tool_profile": "none"}),
            ("custom-no-media", {"private_tool_profile": "custom", "private_tool_groups": "calendar、memory"}),
        ):
            with self.subTest(profile=label):
                self._set_role_reply_settings(seed["context"], cfg)
                event, _ = await self._prepare("普通聊天", f"priv-media-skip-{label}")
                req = NS(prompt="普通聊天", system_prompt="原人格", func_tool=ToolSet(tools=list(persona_tools)))
                with patch("astrbot_host.image_access.ensure_image_access") as ensure_mock, \
                        patch("astrbot_host.core_reply_image_prompt.core_image_tools") as image_tools_mock:
                    inject_core_reply_tools(event, req, application=self.app)
                    ensure_mock.assert_not_called()
                    image_tools_mock.assert_not_called()

        for label, cfg in (
            ("full", {"private_tool_profile": "full"}),
            ("custom-with-media", {"private_tool_profile": "custom", "private_tool_groups": "media"}),
        ):
            with self.subTest(profile=label):
                self._set_role_reply_settings(seed["context"], cfg)
                event, _ = await self._prepare("画一张图", f"priv-media-keep-{label}")
                req = NS(prompt="画一张图", system_prompt="原人格", func_tool=ToolSet(tools=list(persona_tools)))
                with patch("astrbot_host.image_access.ensure_image_access") as ensure_mock, \
                        patch("astrbot_host.core_reply_image_prompt.core_image_tools",
                              side_effect=lambda session, offered: (offered, None)) as image_tools_mock:
                    inject_core_reply_tools(event, req, application=self.app)
                    ensure_mock.assert_called_once()
                    image_tools_mock.assert_called_once()

    async def test_content_provisioning_follows_the_extensions_tool_group(self):
        from unittest.mock import patch

        _, seed = await self._prepare("初始化", "priv-content-seed")
        for profile, groups, expected_calls in (
            ("full", "", 1),
            ("lean", "", 0),
            ("none", "", 0),
            ("custom", "media", 0),
            ("custom", "extensions", 1),
        ):
            with self.subTest(profile=profile, groups=groups):
                self._set_role_reply_settings(seed["context"], {
                    "private_tool_profile": profile, "private_tool_groups": groups,
                })
                event, _ = await self._prepare("普通聊天", f"priv-content-{profile}-{groups}")
                request = NS(prompt="普通聊天", system_prompt="原人格", func_tool=ToolSet(tools=[]))
                with patch("astrbot_host.content_access.ensure_content_access") as ensure_content:
                    inject_core_reply_tools(event, request, application=self.app)
                self.assertEqual(ensure_content.call_count, expected_calls)


class PrivateTurnMergeAndRepeatGuardTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = await self.start_host()
        self.observer = self.controller.session.core_reply_observations

    async def flush(self):
        while pending := self.observer._pending - self.observer._intervention_tasks:
            await asyncio.gather(*tuple(pending))

    async def prepare(self, text: str, identifier: str, **kwargs):
        event = self.event(text=text, identifier=identifier, **kwargs)
        prepared = (await self.controller.prepare_core_reply(event)).unwrap()
        await self.flush()
        return event, prepared

    async def sent(self, prepared, text: str):
        await self.observer.output(prepared, text, state="send_returned")
        await self.flush()

    def capture(self, prepared, **kwargs):
        return capture_private_reply_context(
            self.app.messages,
            prepared["context"],
            prepared["native_input"],
            at=self.clock.now(),
            **kwargs,
        )

    async def test_intra_turn_duplicate_substantial_bubbles_collapse_without_mutating_sources(self):
        _, first = await self.prepare("你今天怎么样", "intra-1")
        self.clock.advance(timedelta(seconds=2))
        repeated_bubble = "我刚刚把桌上的书整理好了，顺便泡了一杯热茶放在旁边等你回来。"
        await self.sent(first, repeated_bubble)
        self.clock.advance(timedelta(seconds=1))
        await self.sent(first, " " + repeated_bubble)
        self.clock.advance(timedelta(seconds=1))
        await self.sent(first, "不过外面好像又开始下雨了。")
        self.clock.advance(timedelta(seconds=2))
        _, second = await self.prepare("那记得关窗", "intra-2")

        ctx = self.capture(second)
        assistant = [item for item in ctx["before_input"] if item["speaker"] == "assistant"]
        self.assertEqual(len(assistant), 1)
        self.assertEqual(assistant[0]["text"].splitlines(), [repeated_bubble, "不过外面好像又开始下雨了。"])

        # Sources table remains faithful: all 3 send callbacks are preserved untouched.
        raw_sources = [
            row for row in self.app.runtime.read_all("SELECT payload_json FROM sources", ())
            if repeated_bubble in row["payload_json"] or "不过外面好像又开始下雨了。" in row["payload_json"]
        ]
        self.assertEqual(len(raw_sources), 3)

    async def test_multipart_proactive_contact_counts_as_one_logical_turn_slot(self):
        # Create 22 user/assistant single-turn messages (11 pairs = 22 turns) plus 1 user turn = 23 turns,
        # then add a 6-part proactive contact (1 logical turn) -> total 24 logical turns == BEFORE_TURNS.
        for idx in range(11):
            _, p = await self.prepare(f"话题{idx}", f"slot-u-{idx}")
            self.clock.advance(timedelta(seconds=2))
            await self.sent(p, f"回应{idx}")
            self.clock.advance(timedelta(seconds=2))
        await self.prepare("第十一次后补充", "slot-u-extra")
        self.clock.advance(timedelta(seconds=2))

        confirmed_contacts = []
        for seq in range(1, 7):
            confirmed_contacts.append({
                "owner": "proactive.contact",
                "ref": "sent_multipart_1",
                "delivered_at": stamp(self.clock.now()),
                "text": f"主动气泡{seq}",
                "part": {"sequence": seq, "part_count": 6},
            })
            self.clock.advance(timedelta(seconds=1))

        _, current = await self.prepare("收到了", "slot-current")
        ctx = self.capture(current, confirmed_contacts=confirmed_contacts)
        before = ctx["before_input"]
        # The earliest user turn ("话题0") must NOT be evicted by the 6 proactive bubbles.
        user_texts = [item["text"] for item in before if item["speaker"] == "user"]
        self.assertIn("话题0", user_texts)
        proactive_parts = [item for item in before if item.get("sent_ref") == "sent_multipart_1"]
        self.assertEqual([item["part"]["sequence"] for item in proactive_parts], [1, 2, 3, 4, 5, 6])
        distinct_turns = []
        for item in before:
            key = (item["speaker"], item["turn_id"])
            if not distinct_turns or distinct_turns[-1] != key:
                distinct_turns.append(key)
        self.assertEqual(len(distinct_turns), BEFORE_TURNS)

    async def test_read_side_repeat_suppression_breaks_loop_without_write_dedup(self):
        long_reply = "我刚刚把桌上的书整理好了，顺便泡了一杯热茶放在旁边，等你忙完回来就能直接喝到啦。"
        long_reply_variant = "我刚刚把桌上的书整理好了，顺便泡了一杯热茶放在旁边，等你忙完回来就能直接喝到啦！"
        for idx, (user_msg, bot_msg) in enumerate((
            ("在干嘛呀", long_reply),
            ("嗯嗯然后呢", long_reply),
            ("怎么又说一遍", long_reply_variant),
        ), 1):
            _, p = await self.prepare(user_msg, f"loop-{idx}")
            self.clock.advance(timedelta(seconds=2))
            await self.sent(p, bot_msg)
            self.clock.advance(timedelta(seconds=2))

        _, current = await self.prepare("换个话题吧", "loop-current")
        with self.assertLogs("companion_v2.conversation.private_reply_context", level="WARNING") as logs:
            ctx = self.capture(current)
        self.assertTrue(any("suppressed repeated assistant turns count=2" in msg for msg in logs.output))
        self.assertEqual(ctx.get("repeat_suppression"), {"detected": True, "suppressed_turns": 2})

        assistant = [item for item in ctx["before_input"] if item["speaker"] == "assistant"]
        self.assertEqual(len(assistant), 3)
        self.assertEqual(assistant[0]["text"], long_reply)
        self.assertNotIn("repeat_suppressed", assistant[0])
        for dup in assistant[1:]:
            self.assertTrue(dup["repeat_suppressed"])
            self.assertNotIn("text", dup)
            self.assertTrue(dup["at"])
            self.assertTrue(dup["turn_id"])
        self.assertEqual(ctx["last_sent_reply_turn_id"], assistant[2]["turn_id"])

        # Compaction preserves repeat_suppressed cleanly and hides internal turn_id values.
        compacted = _compact_private_reply_context(ctx, NS(contexts=[], prompt="换个话题吧"))
        compacted_assistant = [item for item in compacted["before_input"] if item["speaker"] == "assistant"]
        self.assertTrue(compacted_assistant[1]["repeat_suppressed"])
        self.assertTrue(compacted_assistant[2]["repeat_suppressed"])
        self.assertTrue(compacted_assistant[1]["turn_id"].startswith("turn_"))
        self.assertIn("repeat_suppressed", PRIVATE_REPLY_CONTEXT_INSTRUCTION)

        # Verify write-side sources were NEVER deduplicated (all 3 outputs still stored).
        raw_sources = [
            row for row in self.app.runtime.read_all("SELECT payload_json FROM sources", ())
            if long_reply[:-1] in row["payload_json"]
        ]
        self.assertEqual(len(raw_sources), 3)

    async def test_short_repeated_assistant_turns_are_never_suppressed(self):
        for idx, user_msg in enumerate(("在吗", "听到了吗", "好不好"), 1):
            _, p = await self.prepare(user_msg, f"short-{idx}")
            self.clock.advance(timedelta(seconds=2))
            await self.sent(p, "好啊")
            self.clock.advance(timedelta(seconds=2))
        _, current = await self.prepare("走吧", "short-current")
        ctx = self.capture(current)
        self.assertNotIn("repeat_suppression", ctx)
        assistant = [item for item in ctx["before_input"] if item["speaker"] == "assistant"]
        self.assertEqual([item["text"] for item in assistant], ["好啊", "好啊", "好啊"])


class NonAdjacentCleanupTests(unittest.TestCase):
    def test_non_adjacent_substantial_duplicate_paragraph_is_removed(self):
        para_a = "今天下午去窗边看了很久的雨，玻璃上的水珠顺着边角慢慢滑下去，突然就想起你上次说过的那家咖啡店。"
        para_b = "对了，你晚饭想吃点什么热乎的吗？"
        cleaned, removed = clean_reply_text(f"{para_a}\n\n{para_b}\n\n{para_a}")
        self.assertEqual(cleaned, f"{para_a}\n\n{para_b}")
        self.assertEqual(removed, 1)

    def test_non_adjacent_substantial_duplicate_line_is_removed(self):
        line_a = "刚才在整理书架的时候翻出了去年秋天夹在里面的那张银杏书签，叶脉都还很清楚呢。"
        line_b = "待会儿拍照给你看呀。"
        cleaned, removed = clean_reply_text(f"{line_a}\n{line_b}\n{line_a}")
        self.assertEqual(cleaned, f"{line_a}\n{line_b}")
        self.assertEqual(removed, 1)

    def test_short_non_adjacent_phrases_stay_intact(self):
        text = "好呀\n\n等等我一下\n\n好呀"
        self.assertEqual(clean_reply_text(text), (text, 0))


if __name__ == "__main__":
    unittest.main()
