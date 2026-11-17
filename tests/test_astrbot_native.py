from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace as NS
import time
import unittest
from unittest.mock import AsyncMock, patch

from companion_v2.adapters.astrbot.native import (
    HostAccount,
    HostPersona,
    NativeInputPorts,
    bounded_native_lookup,
    persona_instruction,
    report_slow_native_lookup,
)
from companion_v2.execution.models import TaskError, digest
from companion_v2.foundation.refs import SubjectKind, SubjectRef
from companion_v2.identity_policy.personas import PersonaBinding, PersonaSelector, SelectorKind
from tests.native_adapter_support import FakeEvent, FakePlatform, NativeFixture
from tests.test_management_access import INSTALLATION, NOW, ROLE


class NativeInputTests(NativeFixture, unittest.IsolatedAsyncioTestCase):
    async def test_v2_window_persona_override_precedes_astrbot_selection(self):
        prompt = "V2 窗口指定的人格提示词。"
        selected_persona = {"name": "window-persona", "prompt": prompt}
        role = SubjectRef(INSTALLATION, SubjectKind.BOT, "window-role")
        self.native.persona_manager.get_persona_v3_by_id = lambda persona_id: (
            selected_persona if persona_id == "window-persona" else None)
        self.bridge.personas[("bot-a", "window-persona")] = HostPersona(
            "bot-a", "window-persona", role, digest(prompt))

        async def override(umo):
            self.assertEqual(umo, "qq-main:FriendMessage:200")
            return "window-persona"

        async def should_not_resolve(**kwargs):
            raise AssertionError("AstrBot's active persona must not override V2's window setting")

        self.bridge.session_persona_override_lookup = override
        with patch.object(self.native.persona_manager, "resolve_selected_persona", should_not_resolve), \
                patch.object(self.native.conversation_manager, "get_conversation",
                             side_effect=AssertionError("the V2 override needs no host persona lookup")):
            cid, selected, persona, special = await self.bridge._selected_persona(
                self.bridge.accounts[("qq-main", "100")], self.bridge._umo("qq-main", "200"))
            resolved_cid, resolved_role, instruction = await self.bridge._role_for_umo(
                self.bridge.accounts[("qq-main", "100")], self.bridge._umo("qq-main", "200"))

        self.assertEqual((cid, selected, persona, special), ("conversation-1", "window-persona", selected_persona, False))
        self.assertEqual((resolved_cid, resolved_role, instruction),
                         ("astrbot-conversation:conversation-1", role, prompt))

    async def test_preloaded_session_settings_avoid_a_second_persona_lookup(self):
        self.native.persona_manager.personas_v3 = [self.native.persona]

        async def no_override(_umo):
            return None

        async def should_not_resolve(**_kwargs):
            raise AssertionError("session settings were already read for this lookup")

        self.bridge.session_persona_override_lookup = no_override
        with patch.object(self.native.persona_manager, "resolve_selected_persona", should_not_resolve):
            result = await self.bridge._selected_persona(
                self.bridge.accounts[("qq-main", "100")], self.bridge._umo("qq-main", "200"))

        self.assertEqual(result[1:], ("original", self.native.persona, False))

    async def test_verified_persona_uses_selected_conversation_with_shared_role(self):
        self.bridge.personas[("bot-a", "group-persona")] = HostPersona(
            "bot-a", "group-persona", ROLE, digest("群聊使用不同的表达"))
        self.select_window_persona("original")
        await self.envelope()
        current = await self.bridge.verified_persona(self.native_context.scope, self.native_context.role_binding)
        self.assertEqual(current["persona_id"], "original")
        self.assertEqual(current["prompt"], persona_instruction(self.native.persona))
        self.assertEqual(current["account_id"], "100")
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_single_v2_persona_mapping_does_not_claim_an_unmapped_native_selection(self):
        account = self.bridge.accounts[("qq-main", "100")]
        self.native.persona_manager.personas_v3 = [self.native.persona]
        self.bridge.persona_snapshot_lookup = AsyncMock(return_value="group-persona")
        with self.assertRaises(TaskError) as raised:
            await self.bridge._selected_persona(account, self.bridge._umo("qq-main", "200"))
        self.assertEqual(raised.exception.code, "host_persona_window_unbound")
        self.bridge.persona_snapshot_lookup.assert_awaited_once_with(
            self.native, "qq-main:FriendMessage:200", "conversation-1")

    async def test_default_persona_mapping_follows_selected_native_persona(self):
        account = self.bridge.accounts[("qq-main", "100")]
        self.map_second_native_persona()
        self.bridge.personas = {
            ("bot-a", "[%None]"): HostPersona("bot-a", "[%None]", ROLE, digest(""))
        }
        self.bridge.session_persona_override_lookup = AsyncMock(return_value=None)
        self.bridge.persona_snapshot_lookup = AsyncMock(return_value=None)
        self.bridge.persona_selection_lookup = AsyncMock(return_value="second")

        umo = self.bridge._umo("qq-main", "200")
        cid, selected, persona, special = await self.bridge._selected_persona(account, umo)
        _, _, _, projection, instruction = await self.bridge._selected_role_snapshot(account, umo)
        _, second_role, second_instruction = await self.bridge._role_for_umo(account, umo)
        self.bridge.persona_selection_lookup = AsyncMock(return_value="original")
        _, original_role, original_instruction = await self.bridge._role_for_umo(account, umo)

        self.assertEqual((cid, selected, persona["name"], special),
                         ("conversation-1", "second", "second", False))
        self.assertEqual((projection.persona_id, projection.role, instruction), ("second", ROLE, persona["prompt"]))
        self.assertEqual((second_role, second_instruction), (ROLE, persona["prompt"]))
        self.assertEqual((original_role, original_instruction), (ROLE, self.native.persona["prompt"]))
        self.assertNotIn(("bot-a", "second"), self.bridge.personas)

    async def test_uncached_multiple_v2_personas_resolve_the_astrbot_selection_once(self):
        account = self.bridge.accounts[("qq-main", "100")]
        self.map_second_native_persona()
        async def no_override(_umo):
            return None

        async def selected_persona(_context, umo, conversation_id):
            self.assertEqual((umo, conversation_id), ("qq-main:FriendMessage:200", "conversation-1"))
            return "second"

        self.bridge.session_persona_override_lookup = no_override
        self.bridge.persona_snapshot_lookup = selected_persona
        refreshed = []
        self.bridge.persona_window_snapshot_update = lambda *args: refreshed.append(args)
        with patch.object(self.native.conversation_manager, "get_conversation",
                           side_effect=AssertionError("the projection query must not load conversation history")):
            result = await self.bridge._selected_persona(account, self.bridge._umo("qq-main", "200"))

        self.assertEqual(result[1:], ("second", self.native.persona_manager.personas_v3[1], False))
        self.assertEqual(refreshed, [("qq-main:FriendMessage:200", "100", "bot-a",
                                      "second", ROLE.subject_id, "conversation-1")])

    async def test_snapshot_without_matching_conversation_rechecks_native_persona(self):
        account = self.bridge.accounts[("qq-main", "100")]
        self.map_second_native_persona()
        self.bridge.session_persona_override_lookup = AsyncMock(return_value=None)
        self.bridge.persona_snapshot_lookup = AsyncMock(return_value="second")
        self.bridge.persona_selection_lookup = AsyncMock(return_value="second")
        self.bridge.persona_window_snapshot_lookup = lambda *_: {
            "bot_id": "bot-a", "persona_id": "original", "role_id": "stale-role",
            "conversation_id": None, "selection_source": "configured",
        }
        refreshed = []
        self.bridge.persona_window_snapshot_update = lambda *args: refreshed.append(args)

        result = await self.bridge._selected_persona(account, self.bridge._umo("qq-main", "200"))

        self.assertEqual(result[1:], ("second", self.native.persona_manager.personas_v3[1], False))
        self.assertEqual(refreshed, [("qq-main:FriendMessage:200", "100", "bot-a",
                                      "second", ROLE.subject_id, "conversation-1")])
        self.bridge.session_persona_override_lookup.assert_awaited_once()
        self.bridge.persona_snapshot_lookup.assert_awaited_once()
        self.bridge.persona_selection_lookup.assert_awaited_once()

    async def test_empty_conversation_persona_uses_local_agent_runner_default(self):
        account = self.bridge.accounts[("qq-main", "100")]
        self.map_second_native_persona()
        self.bridge.session_persona_override_lookup = AsyncMock(return_value=None)
        self.bridge.persona_snapshot_lookup = AsyncMock(return_value="")
        self.native.agent_configuration = {
            "provider_settings": {"default_personality": "original"},
            "agent_runner": {"runner_type": "local", "config": {"persona": {"persona_id": "second"}}},
        }

        result = await self.bridge._selected_persona(account, self.bridge._umo("qq-main", "200"))

        self.assertEqual(result[1:], ("second", self.native.persona_manager.personas_v3[1], False))

    async def test_empty_conversation_persona_uses_async_selection_lookup(self):
        account = self.bridge.accounts[("qq-main", "100")]
        self.map_second_native_persona()
        self.bridge.session_persona_override_lookup = AsyncMock(return_value=None)
        self.bridge.persona_snapshot_lookup = AsyncMock(return_value=None)
        self.bridge.persona_selection_lookup = AsyncMock(return_value="second")

        result = await self.bridge._selected_persona(account, self.bridge._umo("qq-main", "200"))

        self.bridge.persona_selection_lookup.assert_awaited_once_with(
            self.native, "qq-main:FriendMessage:200", None)
        self.assertEqual(result[1:], ("second", self.native.persona_manager.personas_v3[1], False))

    async def test_persona_snapshot_uses_new_host_resolver_contract(self):
        self.map_second_native_persona()
        await self.envelope()
        self.bridge.session_persona_override_lookup = AsyncMock(return_value=None)
        self.bridge.persona_snapshot_lookup = AsyncMock(return_value="original")

        async def resolver(*, umo, platform_name):
            self.assertEqual((umo, platform_name), ("qq-main:FriendMessage:200", "aiocqhttp"))
            return "second", self.native.persona_manager.personas_v3[1], False

        with patch.object(self.native.persona_manager, "resolve_selected_persona", resolver):
            snapshot = await self.bridge.persona_snapshot(self.native_context.scope)

        self.assertEqual(snapshot["persona_id"], "second")
        self.assertEqual(snapshot["text"], "第二个人格提示词。")

    async def test_multiple_v2_personas_reject_an_unmapped_astrbot_selection(self):
        account = self.bridge.accounts[("qq-main", "100")]
        self.bridge.personas[("bot-a", "second")] = HostPersona(
            "bot-a", "second", ROLE, digest("第二个人格"))
        async def no_override(_umo):
            return None

        async def unmapped(_context, _umo, _conversation_id):
            return "unmapped"

        self.bridge.session_persona_override_lookup = no_override
        self.bridge.persona_snapshot_lookup = unmapped
        with self.assertRaises(TaskError) as raised:
            await self.bridge._selected_persona(account, self.bridge._umo("qq-main", "200"))

        self.assertEqual(raised.exception.code, "host_persona_window_unbound")

    async def test_verified_persona_rechecks_conversation_during_selected_lookup(self):
        self.map_second_native_persona()
        await self.envelope()

        async def switch_conversation(_umo):
            self.native.cid = "replacement-conversation"
            return "original"

        self.bridge.session_persona_override_lookup = switch_conversation
        with self.assertRaises(TaskError) as raised:
            await self.bridge.verified_persona(self.native_context.scope, self.native_context.role_binding)
        self.assertEqual(raised.exception.code, "host_conversation_changed")

    async def test_verified_persona_accepts_prompt_and_conversation_updates_for_the_same_role(self):
        await self.envelope()
        self.native.cid = "conversation-after-reset"
        self.native.persona["prompt"] = "重置后仍属于同一角色的提示词。"

        current = await self.bridge.verified_persona(self.native_context.scope, self.native_context.role_binding)

        self.assertEqual(current["prompt"], "重置后仍属于同一角色的提示词。")

    async def test_verify_role_rejects_a_role_remap_but_accepts_prompt_edits(self):
        await self.envelope()
        self.native.persona["prompt"] = "已编辑的人格表达。"

        prompt = await self.bridge.verify_role(self.native_context.scope,
                                               self.native_context.role_binding,
                                               instruction="捕获时的旧提示词。")

        self.assertEqual(prompt, "已编辑的人格表达。")
        projection = self.bridge.personas[("bot-a", "original")]
        self.assertEqual(projection.instruction_hash, digest(prompt))
        self.bridge.personas[("bot-a", "original")] = replace(
            projection, role=SubjectRef(INSTALLATION, SubjectKind.BOT, "replacement-role"))
        with self.assertRaises(TaskError) as changed:
            await self.bridge.verify_role(self.native_context.scope, self.native_context.role_binding)
        self.assertEqual(changed.exception.code, "host_persona_changed")

    async def test_verified_persona_uses_v2_binding_without_host_persona_lookup(self):
        await self.envelope()
        lookups = self.native.role_lookups
        current = await self.bridge.verified_persona(self.native_context.scope, self.native_context.role_binding)
        self.assertEqual(current["persona_id"], "original")
        self.assertEqual(self.native.role_lookups - lookups, 0)

    async def test_model_failure_persists_only_safe_provider_error_metadata(self):
        class ProviderFailure(Exception):
            status_code = 429

        private = "private response and credential"

        async def fail_request(_payload, _tools):
            raise ProviderFailure(private)

        self.native.provider.client.state.behavior = fail_request
        response = await self.native_model.generate(await self.envelope())

        self.assertEqual(response.status, "unknown")
        self.assertEqual(response.diagnostics.error_type, "ProviderFailure")
        self.assertEqual(response.diagnostics.status_code, 429)
        self.assertEqual(response.diagnostics.error_code, None)
        self.assertNotIn(private, str(response.diagnostics.to_record()))

    async def test_native_lookup_timeout_logs_elapsed_time_and_event_loop_overrun(self):
        release = asyncio.Event()

        async def pending_lookup():
            await release.wait()

        with patch("companion_v2.adapters.astrbot.native.NATIVE_LOOKUP_TIMEOUT_SECONDS", 0.01):
            with self.assertLogs("companion_v2.adapters.astrbot.native", level="WARNING") as captured:
                task = asyncio.create_task(bounded_native_lookup(pending_lookup(), operation="persona"))
                await asyncio.sleep(0)
                time.sleep(0.03)
                with self.assertRaises(TaskError) as raised:
                    await task

        self.assertEqual(raised.exception.code, "host_native_lookup_timeout__persona")
        line = captured.output[0]
        self.assertIn("operation=persona", line)
        self.assertIn("timeout_ms=10", line)
        self.assertIn("timeout_overrun_ms=", line)
        self.assertGreaterEqual(int(line.rsplit("elapsed_ms=", 1)[1].split()[0]), 25)
        self.assertNotIn("private host details", line)

    async def test_slow_lookup_warnings_are_aggregated_per_operation(self):
        with patch("companion_v2.adapters.astrbot.native.NATIVE_LOOKUP_SLOW_LOG_INTERVAL_SECONDS", 60):
            with self.assertLogs("companion_v2.adapters.astrbot.native", level="WARNING") as captured:
                report_slow_native_lookup("conversation_aggregation_test", 2100, now=100)
                report_slow_native_lookup("conversation_aggregation_test", 2300, now=101)
                report_slow_native_lookup("conversation_aggregation_test", 2400, now=161)

        self.assertEqual(len(captured.output), 2)
        self.assertIn("occurrences=1", captured.output[0])
        self.assertIn("occurrences=2", captured.output[1])
        self.assertIn("suppressed_count=2", captured.output[1])

    async def test_v2_window_binding_timeout_is_not_reported_as_an_image_failure(self):
        self.map_second_native_persona()
        async def timeout(_umo):
            raise asyncio.TimeoutError("synthetic private host details")
        self.bridge.session_persona_override_lookup = timeout
        result = await self.bridge.capture(FakeEvent(self.native))
        self.assertEqual(result.failure.code, "host_native_lookup_timeout__session_settings")
        self.assertIn("会话服务设置", result.failure.reason)
        self.assertNotIn("private host details", result.failure.reason)

    async def test_native_fields_keep_platform_login_sender_and_original_role(self):
        snapshot = await self.capture(FakeEvent(self.native, text="我的名字是管理员，发到另一个账号"))
        scope = snapshot.route.scope
        self.assertEqual((scope.platform, scope.account_id, scope.bot_id, scope.native_scope_id, scope.session_id),
                         ("qq-main", "100", "bot-a", "200", "200"))
        self.assertEqual(snapshot.principal.native_account_id, "200")
        self.assertEqual(snapshot.role, ROLE)
        self.assertEqual(snapshot.instruction, self.native.persona["prompt"])
        self.assertEqual(snapshot.recipients.recipients, (self.important.subject,))

    async def test_native_capture_to_full_delivery_and_duplicate_intake(self):
        snapshot = await self.capture()
        host = await self.setup_host()
        batch = host.receive(snapshot).unwrap()
        self.assertEqual(host.receive(snapshot).unwrap(), batch)
        app = host.application
        self.assertEqual((await self.done(app, batch))["state"], "complete")
        sent = self.native.platform.client._api._wsr_api.calls
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][1]["self_id"], "100")
        self.assertEqual(sent[0][1]["user_id"], 200)
        history = app.messages.history(self.context(app, batch)).unwrap()["items"]
        self.assertEqual([r["text"] for r in history], ["我在这里。"])
        self.assertTrue(app.runtime.read_one("SELECT platform_ref FROM conversation_parts", ())["platform_ref"].endswith(":456"))
        self.assertEqual(self.native.provider.client.max_retries, 2)
        self.assertTrue(self.native.provider.client.state.calls[0][0]["messages"][0]["content"].startswith(
            snapshot.instruction))

    async def test_media_group_session_client_and_unmapped_account_are_explicit_failures(self):
        mutations = (
            (lambda e: setattr(e.message_obj, "message", [NS(type="Image")]), "host_media_unsupported"),
            (lambda e: setattr(e.message_obj, "group_id", "300"), "host_scene_unsupported"),
            (lambda e: setattr(e.session, "session_id", "201"), "host_route_mismatch"),
            (lambda e: setattr(e.message_obj, "self_id", "999"), "host_route_mismatch"),
            (lambda e: setattr(e.bot, "ignored", True), None),
            (lambda e: setattr(e, "bot", object()), "host_client_mismatch"),
        )
        for mutate, expected in mutations:
            with self.subTest(expected=expected):
                event = FakeEvent(self.native)
                mutate(event)
                result = await self.bridge.capture(event)
                self.assertEqual(result.failure.code if result.failure else None, expected)
        self.assertFalse(NativeInputPorts().authenticated_principal(FakeEvent(self.native)).is_success)

    async def test_v2_single_persona_binding_ignores_astrbot_selection_and_accepts_prompt_edits(self):
        self.native.selected = "other"
        captured = await self.bridge.capture(FakeEvent(self.native))
        self.assertTrue(captured.is_success)
        self.assertEqual(captured.unwrap().instruction, self.native.persona["prompt"])
        self.native.persona["prompt"] = "新人格"
        updated = await self.bridge.capture(FakeEvent(self.native))
        self.assertTrue(updated.is_success, updated.failure)
        self.assertEqual(updated.unwrap().instruction, "新人格")

    async def test_installed_persona_snapshot_accepts_prompt_updates_until_manual_refresh(self):
        self.bridge.persona_window_snapshot_lookup = lambda umo, account_id: {
            "bot_id": "bot-a", "persona_id": "original", "role_id": "stale-role",
            "conversation_id": "conversation-1",
        } if (umo, account_id) == ("qq-main:FriendMessage:200", "100") else None
        self.native.persona["prompt"] = "启动快照之后更新的人格提示词。"

        captured = await self.bridge.capture(FakeEvent(self.native))

        self.assertTrue(captured.is_success, captured.failure)
        self.assertEqual(captured.unwrap().instruction, "启动快照之后更新的人格提示词。")

    async def test_capture_has_no_open_sqlite_transaction_while_awaiting_host(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def pause(_umo):
            entered.set()
            await release.wait()
            return "original"

        self.map_second_native_persona()
        self.bridge.session_persona_override_lookup = pause
        pending = asyncio.create_task(self.bridge.capture(FakeEvent(self.native)))
        await asyncio.wait_for(entered.wait(), 1)
        # A second connection can commit while host capture is suspended.
        other = self.open_runtime()
        with other.transaction():
            other.read_one("SELECT 1", ())
        release.set()
        self.assertTrue((await pending).is_success)

    async def test_route_mutation_and_replaced_client_during_capture_do_not_rebind_input(self):
        event = FakeEvent(self.native)

        async def mutate(_umo):
            event.message_obj.sender.user_id = "201"
            event.message_obj.message[0].text = "后来改掉的消息"
            self.native.platform.metadata.id = "qq-other"
            return "original"

        self.map_second_native_persona()
        self.bridge.session_persona_override_lookup = mutate
        result = await self.bridge.capture(event)
        self.assertEqual(result.failure.code, "host_account_changed")

    async def test_replaced_connection_rejects_old_capture_and_accepts_next_event(self):
        event = FakeEvent(self.native)
        old_platform = self.native.platform

        async def replace_connection(_umo):
            self.native.platform = FakePlatform()
            return "original"

        self.bridge.session_persona_override_lookup = replace_connection
        result = await self.bridge.capture(event)
        self.assertEqual(result.failure.code, "host_connection_changed")
        self.bridge.session_persona_override_lookup = AsyncMock(return_value="original")
        result = await self.bridge.capture(FakeEvent(self.native))
        self.assertTrue(result.is_success, result.failure)
        self.assertIsNot(self.bridge.platform(result.unwrap().route.scope), old_platform)

    async def test_v2_role_mismatch_rolls_back_source_and_input_publication(self):
        snapshot = await self.capture()
        host = await self.setup_host()
        result = host.receive(replace(snapshot, role=SubjectRef(INSTALLATION, SubjectKind.BOT, "wrong")))
        self.assertEqual(result.failure.code, "host_persona_projection_mismatch")
        self.assertEqual(host.application.runtime.read_one("SELECT COUNT(*) AS n FROM conversation_inputs", ())["n"], 0)

    async def test_two_logins_same_platform_and_native_event_remain_separate(self):
        self.bind_native("101", "bot-b")
        role_b = SubjectRef(INSTALLATION, SubjectKind.BOT, "persona-b")
        self.personas.set_binding(PersonaBinding("b-role", PersonaSelector(INSTALLATION, SelectorKind.BOT, bot_id="bot-b"),
            role_b, 0, NOW, "fixture", "admin"), expected_revision=0, observed_at=NOW)
        self.bridge = self.make_bridge(accounts=(HostAccount("qq-main", "100", "bot-a"), HostAccount("qq-main", "101", "bot-b")),
            personas=(HostPersona("bot-a", "original", ROLE, digest(persona_instruction(self.native.persona))),
                      HostPersona("bot-b", "original", role_b, digest(persona_instruction(self.native.persona)))))
        self.native_sender.bridge = self.bridge
        self.native_model.bridge = self.bridge
        a, b = await self.capture(), await self.capture(FakeEvent(self.native, login="101"))
        host = await self.setup_host()
        first, second = host.receive(a).unwrap(), host.receive(b).unwrap()
        self.assertNotEqual(first, second)
        await self.done(host.application, first)
        await self.done(host.application, second)
        self.assertEqual({p["self_id"] for _, p in self.native.platform.client._api._wsr_api.calls}, {"100", "101"})

    async def test_withdrawal_while_native_model_prepares_prevents_request(self):
        captured = await self.capture()
        entered, release = asyncio.Event(), asyncio.Event()

        async def pause(_umo):
            entered.set()
            await release.wait()
            return "original"

        self.map_second_native_persona()
        self.bridge.session_persona_override_lookup = pause
        host = await self.setup_host()
        app = host.application
        batch = host.receive(captured).unwrap()
        await asyncio.wait_for(entered.wait(), 2)
        event_key = app.runtime.read_one("SELECT event_key FROM conversation_inputs WHERE batch_id = ?", (batch,))["event_key"]
        app.withdraw(self.context(app, batch), event_key).unwrap()
        release.set()
        await self.done(app, batch)
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])

    async def test_withdrawal_during_sender_preparation_prevents_platform_send(self):
        captured = await self.capture()
        entered, release = asyncio.Event(), asyncio.Event()

        calls = 0

        async def pause(_umo):
            nonlocal calls
            calls += 1
            if calls == 3:  # model preflight, model postflight, sender preflight
                entered.set()
                await release.wait()
            return "original"

        self.map_second_native_persona()
        self.bridge.session_persona_override_lookup = pause
        host = await self.setup_host()
        app = host.application
        batch = host.receive(captured).unwrap()
        await asyncio.wait_for(entered.wait(), 3)
        event_key = app.runtime.read_one("SELECT event_key FROM conversation_inputs WHERE batch_id = ?", (batch,))["event_key"]
        app.withdraw(self.context(app, batch), event_key).unwrap()
        release.set()
        await self.done(app, batch)
        self.assertEqual(len(self.native.provider.client.state.calls), 1)
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])
