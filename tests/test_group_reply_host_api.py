"""Dashboard group reply preferences use exact native group and role windows."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
import json
import unittest
from urllib.parse import urlencode

from astrbot.api.web import PluginRequest, bind_request_context
from starlette.requests import Request

from astrbot_host.management_identity import dashboard_operator
from companion_v2.execution.models import TaskContext, digest
from companion_v2.identity_policy.models import AuthorizationTier
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.configuration.owners import SparseConfigurationOwner
from companion_v2.conversation.participation import (ACTION, CONFIG_GROUP as PARTICIPATION_GROUP,
    group_participation_settings)
from companion_v2.expression.configuration import GROUP_SCOPE_GROUP
from companion_v2.foundation.refs import RoleOwnerRef, ScopeKind, ScopeRef
from tests.group_host_support import GroupFixture, group_config
from tests.native_adapter_support import completion
from tests.test_group_participation import command
from tests.staging_bootstrap_support import wait_for_batch


PREFIX = "/astrbot_plugin_private_companion"


class GroupReplyHostAPITests(GroupFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.controller.register_routes()
        self.routes = {(path, methods[0]): handler for path, handler, methods, _ in self.native.registered_web_apis}
        self.sequence = 0

    async def request(self, suffix="", *, target="group-a", role="role-a", username="admin",
                      data=None, raw=None, query="", path=None):
        path = path or PREFIX + "/host/targets/" + target + "/roles/" + digest(role) + "/reply" + suffix
        body = raw if raw is not None else json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else b""
        method = "POST" if body else "GET"

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        native = Request({"type": "http", "method": method, "path": path, "headers": [],
                          "query_string": query.encode("utf-8")}, receive=receive)
        with bind_request_context(PluginRequest(native, username=username)):
            return await self.routes[(path, method)]()

    async def save(self, changes, *, target="group-a", role="role-a", query="", key="save"):
        status = await self.request(target=target, role=role, query=query)
        self.assertEqual(status["status"], "ok", status)
        return await self.request("/configure", target=target, role=role, query=query,
            data={"operation_key": key, "edit_token": status["data"]["edit_token"], "changes": changes})

    async def receive_group(self, *, target="group-a", unique=False, sender="200"):
        self.sequence += 1
        event = self.group_event(target=target, unique=unique, sender=sender,
                                 identifier="reply-settings-" + str(self.sequence))
        result = await self.controller.receive_claimed(event)
        self.assertTrue(result.is_success, result.failure)
        batch = result.unwrap()
        await wait_for_batch(self.controller.session, batch)
        return batch

    def captured(self, batch):
        row = self.controller.session.application.messages.batch(batch)
        return json.loads(row["contributions_json"])["reply_settings"]["settings"]

    async def test_directory_and_changes_affect_only_new_group_batches(self):
        await self.start_host()
        selected = self.controller.settings.target("group-b")
        bridge = self.controller.session.bridge.groups
        account = bridge.accounts[(selected.platform_id, selected.account_id)]
        conversation_id, role, _ = await bridge._role(account, selected.group_id)
        scope = ScopeRef(self.controller.runtime.installation_id, selected.platform_id, ScopeKind.GROUP,
                         selected.group_id, selected.bot_id, selected.account_id, conversation_id, selected.group_id)
        legacy_target = ConfigTarget(scope.installation_id, ConfigLayer.WINDOW, role, scope)
        application = self.controller.session.application
        reply_group = "conversation.presentation"
        reply_fields = application.configuration.registry.group(reply_group)
        legacy_fields = tuple(replace(field, layers=(ConfigLayer.WINDOW,)) for field in reply_fields)
        reply_owner = application.configuration._owners[reply_group]
        legacy_snapshot = SparseConfigurationOwner.read(reply_owner, legacy_target, legacy_fields)
        SparseConfigurationOwner.write(reply_owner, legacy_target, legacy_fields,
            changes={reply_group + ".group_wakeup_direct_words": '["旧窗口词"]'}, reset=(),
            expected_revision=legacy_snapshot.revision, observed_at=self.controller.now())

        directory = (await self.request(path=PREFIX + "/page/targets"))["data"]
        routes = {path for path, _ in self.routes}
        for target in directory["targets"]:
            for role in target["roles"]:
                self.assertIn(role["reply_api"], routes)
                self.assertIn(role["reply_api"] + "/configure", routes)
                self.assertIn(role["reply_api"] + "/preview", routes)
        old = await self.receive_group()
        previous = self.captured(old)
        self.assertTrue(previous["group_air_guard_enabled"])
        changed = await self.save({"group_air_guard_enabled": False,
            "group_wakeup_direct_words": '["小星", "星星"]',
            "group_wakeup_context_words": '["观星"]',
            "group_wakeup_interest_keywords": '["天文"]',
            "group_wakeup_question_enabled": False})
        self.assertEqual(changed["status"], "ok", changed)
        self.assertEqual(changed["data"]["status"]["settings"]["group_wakeup_direct_words"], '["小星", "星星"]')
        wakeup_field = changed["data"]["status"]["fields"]["group_wakeup_direct_words"]
        self.assertEqual(wakeup_field["editable_layers"], ["role", "window"])
        self.assertEqual(wakeup_field["write_layer"], "window")
        new = await self.receive_group()
        current = self.captured(new)
        self.assertEqual(self.captured(old), previous)
        self.assertFalse(current["group_air_guard_enabled"])
        self.assertEqual(current["group_wakeup_direct_words"], '["小星", "星星"]')
        payload, _ = self.native.provider.client.state.calls[-1]
        instruction = payload["messages"][1]["content"]
        self.assertNotIn("读空气参考", instruction)
        self.assertIn("小星", instruction)
        self.assertIn("观星", instruction)
        self.assertIn("天文", instruction)
        self.assertIn("未点名的一般提问或求助不作为额外唤醒线索", instruction)
        other_group = await self.receive_group(target="group-b")
        self.assertEqual(self.captured(other_group)["group_wakeup_direct_words"], '["旧窗口词"]')
        self.assertTrue(self.captured(old)["group_air_guard_enabled"])
        disabled = await self.save({"group_wakeup_enabled": False}, key="disable-wakeup")
        self.assertEqual(disabled["status"], "ok", disabled)
        after_disable = await self.receive_group()
        self.assertFalse(self.captured(after_disable)["group_wakeup_enabled"])
        self.assertTrue(self.captured(new)["group_wakeup_enabled"])
        payload, _ = self.native.provider.client.state.calls[-1]
        self.assertNotIn("群唤醒参考", payload["messages"][1]["content"])

    async def test_wakeup_preferences_use_original_speaker_and_preview_is_offline(self):
        await self.start_host()
        status = await self.request()
        settings = status["data"]["settings"]
        model_calls = len(self.native.provider.client.state.calls)
        preview = await self.request("/preview", data={"settings": settings, "text": "离线正文", "image_count": 1})
        self.assertEqual(preview["status"], "ok", preview)
        self.assertEqual(preview["data"]["mode"], "offline_preview")
        self.assertEqual(len(self.native.provider.client.state.calls), model_calls)
        for data in ({"settings": settings, "text": ""}, {"settings": settings, "text": "示例", "image_count": 9},
                     {"settings": settings, "text": "示例", "target_id": "group-b"}):
            self.assertEqual((await self.request("/preview", data=data))["status"], "error")
        changes = {"group_wakeup_bot_name": "小星", "group_wakeup_primary_member_ids": '["201"]',
                   "group_wakeup_owner_direct_words": '["私有称呼"]',
                   "group_wakeup_generated_keyword_limit": 12,
                   "group_wakeup_topic_interest_max_boost": 20,
                   "group_wakeup_debounce_pending_penalty": 40,
                   "group_wakeup_cold_group_enabled": True}
        self.assertEqual((await self.save(changes, key="wakeup"))["status"], "ok")
        await self.receive_group(sender="200")
        ordinary = self.native.provider.client.state.calls[-1][0]["messages"][1]["content"]
        self.assertIn("小星", ordinary)
        self.assertIn("最多 12 个", ordinary)
        self.assertIn("最多相对增加 20%", ordinary)
        self.assertNotIn("私有称呼", ordinary)
        await self.receive_group(sender="201")
        primary = self.native.provider.client.state.calls[-1][0]["messages"][1]["content"]
        self.assertIn("私有称呼", primary)
        self.assertIn("冷群参考间隔 25 分钟", primary)
        self.assertEqual((await self.save({"group_wakeup_bot_name_enabled": False},
                                          key="disable-name"))["status"], "ok")
        await self.receive_group(sender="200")
        no_name = self.native.provider.client.state.calls[-1][0]["messages"][1]["content"]
        self.assertNotIn("小星", no_name)

    async def test_group_role_and_native_session_settings_are_separate(self):
        await self.start_host()
        self.assertEqual((await self.save({"group_air_guard_max_bot_replies": 5}))["status"], "ok")
        self.assertEqual((await self.request(target="group-b"))["data"]["settings"]["group_air_guard_max_bot_replies"], 3)
        self.assertEqual((await self.request(target="group-c"))["data"]["settings"]["group_air_guard_max_bot_replies"], 3)
        self.assertEqual((await self.request(query="session_id=201_300"))["data"]["settings"]["group_air_guard_max_bot_replies"], 3)
        self.assertEqual((await self.save({"group_air_guard_max_bot_replies": 7},
            query="session_id=201_300", key="member"))["status"], "ok")
        self.assertEqual((await self.request(query="session_id=202_300"))["data"]["settings"]["group_air_guard_max_bot_replies"], 3)
        member_batch = await self.receive_group(unique=True, sender="201")
        self.assertEqual(self.captured(member_batch)["group_air_guard_max_bot_replies"], 7)
        other_member = await self.receive_group(unique=True, sender="202")
        self.assertEqual(self.captured(other_member)["group_air_guard_max_bot_replies"], 3)
        self.window_personas["qq-main:GroupMessage:300"] = "second"
        self.assertEqual((await self.request(role="role-a"))["data"]["code"], "group_reply_role_changed")
        self.assertEqual((await self.request(role="role-b"))["data"]["settings"]["group_air_guard_max_bot_replies"], 3)
        self.assertEqual((await self.save({"group_air_guard_max_bot_replies": 9}, role="role-b"))["status"], "ok")
        self.window_personas["qq-main:GroupMessage:300"] = "original"
        self.assertEqual((await self.request())["data"]["settings"]["group_air_guard_max_bot_replies"], 5)
        self.assertEqual((await self.request(query="session_id=201_300"))["data"]["settings"]["group_air_guard_max_bot_replies"], 7)

    async def test_interjection_preferences_are_saved_to_the_current_group_window(self):
        await self.start_host()
        initial = await self.request("/participation")
        self.assertEqual(initial["status"], "ok", initial)
        self.assertEqual(initial["data"]["scope"], "window")
        self.assertEqual(set(initial["data"]["settings"]), {
            "enable_group_interjection", "group_interject_min_interval_minutes",
            "group_interject_max_daily", "enable_group_interjection_feedback",
            "group_interject_feedback_window_seconds"})
        self.assertEqual(initial["data"]["fields"]["enable_group_interjection"]["write_layer"], "window")
        self.assertEqual(initial["data"]["fields"]["enable_group_interjection"]["editable_layers"], ["window"])

        changed = await self.request("/participation/configure", data={
            "operation_key": "group-a-interjection",
            "edit_token": initial["data"]["edit_token"],
            "changes": {"enable_group_interjection": True,
                        "group_interject_min_interval_minutes": 45,
                        "group_interject_max_daily": 4,
                        "enable_group_interjection_feedback": False,
                        "group_interject_feedback_window_seconds": 900},
        })
        self.assertEqual(changed["status"], "ok", changed)
        self.assertTrue(changed["data"]["status"]["settings"]["enable_group_interjection"])
        self.assertEqual(changed["data"]["status"]["provenance"]["enable_group_interjection"]["layer"], "window")

        for target in ("group-b", "group-c"):
            isolated = await self.request("/participation", target=target)
            self.assertEqual(isolated["status"], "ok", isolated)
            self.assertFalse(isolated["data"]["settings"]["enable_group_interjection"])
            self.assertEqual(isolated["data"]["settings"]["group_interject_min_interval_minutes"], 180)

        other_field = await self.request("/participation/configure", data={
            "operation_key": "not-an-interjection-field",
            "edit_token": changed["data"]["status"]["edit_token"],
            "changes": {"enable_group_repeat_follow": False},
        })
        self.assertEqual(other_field["data"]["code"], "group_participation_configuration_invalid")

        batch_id = await self.receive_group(target="group-a")
        context = TaskContext.restore(json.loads(
            self.controller.session.application.messages.batch(batch_id)["context_json"]))
        effective = group_participation_settings(self.controller.runtime, context)
        self.assertTrue(effective["enable_group_interjection"])
        self.assertEqual(effective["group_interject_min_interval_minutes"], 45)

        selected = self.controller.settings.target("group-a")
        bridge = self.controller.session.bridge.groups
        account = bridge.accounts[(selected.platform_id, selected.account_id)]
        _, role, _ = await bridge._role(account, selected.group_id)
        role_target = ConfigTarget(self.controller.runtime.installation_id, ConfigLayer.ROLE,
                                   role, bot_id=selected.bot_id)
        operator = dashboard_operator(self.controller.runtime, "admin", at=self.controller.now())
        defaults = self.controller.session.application.configuration.read(
            operator, target=role_target, group_id=PARTICIPATION_GROUP, observed_at=self.controller.now())
        self.assertTrue(defaults.is_success, defaults.failure)
        self.assertFalse(defaults.unwrap().values[PARTICIPATION_GROUP + ".enable_group_interjection"])

        stale_reset = await self.request("/participation/configure", data={
            "operation_key": "group-a-interjection-reset",
            "edit_token": changed["data"]["status"]["edit_token"],
            "changes": {}, "reset_to_inherit": ["enable_group_interjection"],
        })
        self.assertEqual(stale_reset["data"]["code"], "configuration_revision_conflict")
        current = await self.request("/participation")
        reset = await self.request("/participation/configure", data={
            "operation_key": "group-a-interjection-reset-refreshed",
            "edit_token": current["data"]["edit_token"],
            "changes": {}, "reset_to_inherit": ["enable_group_interjection"],
        })
        self.assertEqual(reset["status"], "ok", reset)
        self.assertFalse(reset["data"]["status"]["settings"]["enable_group_interjection"])
        self.assertFalse(reset["data"]["status"]["provenance"]["enable_group_interjection"]["explicit"])

    async def test_group_override_can_be_reset_to_role_inheritance(self):
        await self.start_host()
        override = await self.save({"style_prompt": "本群临时采用更短的回应"}, key="group-style-override")
        self.assertEqual(override["status"], "ok", override)
        status = await self.request()
        self.assertEqual(status["data"]["settings"]["style_prompt"], "本群临时采用更短的回应")
        self.assertEqual(status["data"]["provenance"]["style_prompt"]["layer"], "window")
        reset = await self.request("/configure", data={"operation_key": "group-style-reset",
            "edit_token": status["data"]["edit_token"], "changes": {},
            "reset_to_inherit": ["style_prompt"]})
        self.assertEqual(reset["status"], "ok", reset)
        self.assertEqual(reset["data"]["status"]["settings"]["style_prompt"], "")
        self.assertIsNone(reset["data"]["status"]["provenance"]["style_prompt"]["layer"])
        self.assertFalse(reset["data"]["status"]["provenance"]["style_prompt"]["explicit"])

    async def test_expression_learning_settings_use_the_same_group_session_scope(self):
        await self.start_host()
        status = await self.request("/expression")
        self.assertEqual(status["status"], "ok", status)
        self.assertEqual(status["data"]["group_id"], GROUP_SCOPE_GROUP)
        self.assertFalse(status["data"]["settings"]["group_learning_enabled"])
        member = await self.request("/expression", query="session_id=201_300")
        command = {"operation_key": "group-learning-bounds", "edit_token": member["data"]["edit_token"],
                   "changes": {"group_learning_enabled": True}}
        saved = await self.request("/expression/configure", query="session_id=201_300", data=command)
        self.assertEqual(saved["status"], "ok", saved)
        self.assertTrue(saved["data"]["status"]["settings"]["group_learning_enabled"])
        replayed = await self.request("/expression/configure", query="session_id=201_300", data=command)
        self.assertTrue(replayed["data"]["replayed"])
        self.assertFalse((await self.request("/expression"))["data"]["settings"]["group_learning_enabled"])
        self.assertFalse((await self.request("/expression", query="session_id=202_300"))["data"]["settings"]["group_learning_enabled"])

    async def test_short_fragment_waits_then_merges_only_the_same_member(self):
        config = group_config()
        config["runtime"].update(quiet_seconds=1, maximum_wait_seconds=3)
        self.controller = self.make_controller(config)
        self.controller.register_routes()
        self.routes = {(p, m[0]): h for p, h, m, _ in self.native.registered_web_apis}
        await self.start_host()
        app = self.controller.session.application
        first = self.group_event(identifier="fragment", text="瓦")
        batch = (await self.controller.receive_claimed(first)).unwrap()
        first_due = app.flow.row(batch)["due_at"]
        self.assertEqual(datetime.fromisoformat(app.flow.row(batch)["due_at"]), self.clock.now() + timedelta(seconds=15))
        self.clock.advance(timedelta(seconds=1))
        app.notify()
        await asyncio.sleep(0.01)
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assertEqual((await self.controller.receive_claimed(first)).unwrap(), batch)
        followup = self.group_event(identifier="fragment-followup", text="我想问刚才那道题")
        self.assertEqual((await self.controller.receive_claimed(followup)).unwrap(), batch)
        due = app.flow.row(batch)["due_at"]
        self.assertEqual(due, first_due)
        other = (await self.controller.receive_claimed(self.group_event(identifier="other-member", sender="201"))).unwrap()
        self.assertNotEqual(other, batch)
        self.assertEqual((await self.controller.receive_claimed(first)).unwrap(), batch)
        self.assertEqual(app.flow.row(batch)["due_at"], due)
        self.clock.advance(timedelta(seconds=14))
        app.notify()
        await wait_for_batch(self.controller.session, batch)
        await wait_for_batch(self.controller.session, other)
        payload = self.native.provider.client.state.calls[0][0]
        self.assertIn("已合并当前成员 2 条输入", payload["messages"][1]["content"])
        users = " ".join(material["text"] for message in payload["messages"] if message["role"] == "user"
                         for material in json.loads(message["content"])["materials"])
        self.assertIn("瓦", users)
        self.assertIn("我想问刚才那道题", users)
        self.assertEqual(len(self.native.provider.client.state.calls), 2)

    async def test_fragment_due_survives_configuration_change_and_restart(self):
        await self.start_host()
        app = self.controller.session.application
        first = self.group_event(identifier="durable-fragment", text="瓦")
        batch = (await self.controller.receive_claimed(first)).unwrap()
        due = app.flow.row(batch)["due_at"]
        saved = await self.save({"group_wakeup_short_text_wait_ms": 2000}, key="timing")
        self.assertEqual(saved["status"], "ok", saved)
        self.assertEqual(saved["data"]["status"]["timing_apply_mode"], "next_accepted_input")
        self.assertEqual(app.flow.row(batch)["due_at"], due)
        await self.controller.terminate()
        self.controller = self.make_controller(group_config())
        (await self.controller.load_existing()).unwrap()
        (await self.controller.start()).unwrap()
        app = self.controller.session.application
        self.assertEqual(app.flow.row(batch)["due_at"], due)
        self.assertEqual((await self.controller.receive_claimed(first)).unwrap(), batch)
        self.clock.advance(timedelta(seconds=14))
        app.notify()
        await asyncio.sleep(0.01)
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.clock.advance(timedelta(seconds=1))
        app.notify()
        await wait_for_batch(self.controller.session, batch)
        next_batch = (await self.controller.receive_claimed(self.group_event(identifier="new-fragment", text="瓦"))).unwrap()
        self.assertEqual(datetime.fromisoformat(app.flow.row(next_batch)["due_at"]), self.clock.now() + timedelta(seconds=2))
        self.clock.advance(timedelta(seconds=2))
        app.notify()
        await wait_for_batch(self.controller.session, next_batch)

    async def test_fragment_wait_zero_or_disabled_keeps_original_timing(self):
        await self.start_host()
        for index, changes in enumerate(({"group_wakeup_short_text_wait_ms": 0},
                                        {"group_wakeup_short_text_wait_ms": 15000, "group_wakeup_enabled": False})):
            self.assertEqual((await self.save(changes, key="no-wait-" + str(index)))["status"], "ok")
            batch = (await self.controller.receive_claimed(self.group_event(identifier="no-wait-" + str(index), text="瓦"))).unwrap()
            self.assertEqual(datetime.fromisoformat(self.controller.session.application.flow.row(batch)["due_at"]), self.clock.now())
            await wait_for_batch(self.controller.session, batch)

    async def test_participation_diagnostics_limit_modes_and_exact_target_isolation(self):
        await self.start_host()
        self.assertEqual((await self.save({"group_wakeup_log_limit": 10}))["status"], "ok")
        replied = [await self.receive_group() for _ in range(11)]

        async def skip(payload, tools):
            return completion(tools=[command(ACTION, {"mode": "skip", "reason": "private diagnostic reason"})])

        self.native.provider.client.state.behavior = skip
        skipped = await self.receive_group()
        pending = (await self.controller.receive_claimed(self.group_event(identifier="pending-fragment", text="瓦"))).unwrap()
        status = (await self.request())["data"]
        self.assertEqual(status["participation_limit"], 10)
        recent = status["participation_recent"]
        self.assertEqual(len(recent), 10)
        self.assertEqual([(r["batch_id"], r["mode"]) for r in recent[:2]], [(pending, "pending"), (skipped, "skip")])
        self.assertTrue(all(r["mode"] == "reply" for r in recent[2:]))
        self.assertNotIn(replied[0], {r["batch_id"] for r in recent})
        self.assertEqual(self.controller.runtime.read_one("SELECT COUNT(*) AS n FROM conversation_batches", ())["n"], 13)
        self.assertTrue(all(set(r) == {"batch_id", "created_at", "state", "reason", "mode"} for r in recent))
        self.assertNotIn("private diagnostic reason", json.dumps(recent))
        for target, query in (("group-b", ""), ("group-c", ""), ("group-a", "session_id=201_300")):
            self.assertEqual((await self.request(target=target, query=query))["data"]["participation_recent"], [])
        self.window_personas["qq-main:GroupMessage:300"] = "second"
        self.assertEqual((await self.request(role="role-b"))["data"]["participation_recent"], [])
        self.clock.advance(timedelta(seconds=15))
        self.controller.session.application.notify()
        await wait_for_batch(self.controller.session, pending)

    async def test_style_list_without_a_group_session_is_an_empty_page_but_commands_still_fail(self):
        await self.start_host()
        # A configured group that has never produced a V2 turn is an expected
        # empty state, not a page-level read failure. The page must still know
        # why it is empty.
        listing = await self.request("/style", query="state=adopted&limit=50")
        self.assertEqual(listing["status"], "ok", listing)
        self.assertEqual(listing["data"]["items"], [])
        self.assertEqual(listing["data"]["unavailable_reason"], "role_binding_missing")
        self.assertIsNone(listing["data"]["next_cursor"])
        self.assertEqual(listing["data"]["legacy_orphan_count"], 0)
        # Authorization and identity errors are not swallowed by that fallback.
        unauthenticated = await self.request("/style", username=None, query="state=adopted&limit=50")
        self.assertEqual(unauthenticated["status"], "error", unauthenticated)
        self.assertEqual(unauthenticated["data"]["code"], "dashboard_identity_required")
        # Decisions still need a real conversation context and keep failing.
        command = await self.request("/style/command", data={
            "signal_id": "signal-missing", "action": "retire",
            "view_token": "token", "operation_key": "group-style-command"})
        self.assertEqual(command["status"], "error", command)
        self.assertEqual(command["data"]["code"], "expression_group_context_required")

    async def test_style_list_reports_old_synthetic_scope_rules_without_moving_them(self):
        await self.start_host()
        runtime = self.controller.runtime
        target = self.controller.settings.target("group-a")
        legacy = ScopeRef(runtime.installation_id, target.platform_id, ScopeKind.GROUP,
            target.group_id, target.bot_id, target.account_id, target.group_id, target.group_id)
        owner = digest(RoleOwnerRef(runtime.installation_id, target.bot_id, "role-a"))
        now = self.clock.now().isoformat()
        with runtime.transaction() as connection:
            connection.execute("""INSERT INTO expression_style_runs
                (run_id, owner_key, scope_key, sample_json, material_hash, summary, state, created_at, decided_at)
                VALUES (?, ?, ?, '{}', 'synthetic', '旧版表达', 'decided', ?, ?)""",
                ("legacy-expression:test", owner, digest(legacy), now, now))
            connection.execute("""INSERT INTO expression_style_signals
                (signal_id, run_id, owner_key, scope_key, dimension, observation, proposal,
                 confidence, evidence_json, state, created_at, decided_at, ordinal)
                VALUES (?, ?, ?, ?, 'diction', '旧版群规则', '继续使用', 1.0, '[]', 'adopted', ?, ?, 0)""",
                ("legacy-signal:test", "legacy-expression:test", owner, digest(legacy), now, now))
        listing = await self.request("/style", query="state=adopted&limit=50")
        self.assertEqual(listing["status"], "ok", listing)
        self.assertEqual(listing["data"]["items"], [])
        self.assertEqual(listing["data"]["legacy_orphan_count"], 1)
        # The diagnostic is read-only: the old row stays in its original scope.
        row = runtime.read_one("SELECT scope_key FROM expression_style_signals WHERE signal_id = ?",
                               ("legacy-signal:test",))
        self.assertEqual(row["scope_key"], digest(legacy))

    async def test_invalid_json_identity_session_role_and_core_downgrade(self):
        await self.start_host()
        status = await self.request()
        self.assertEqual(status["status"], "ok", status)
        token = status["data"]["edit_token"]
        for username in (None, "", "api_key:synthetic"):
            self.assertEqual((await self.request(username=username))["data"]["code"], "dashboard_identity_required")
        for query in ("session_id=201_301", "session_id=201_300&session_id=202_300", "owner=admin"):
            self.assertNotEqual((await self.request(query=query))["status"], "ok")
        for raw in (b'{"changes":{},"changes":{}}', b'{"operation_key":',
                    b'{"operation_key":"x","edit_token":"' + token.encode() + b'","changes":{"group_air_guard_window_seconds":NaN}}',
                    b'[' * 5000, b'"' + b'x' * 8192 + b'"'):
            result = await self.request("/configure", raw=raw)
            self.assertEqual(result["data"]["code"], "group_reply_request_invalid", result)
        self.assertEqual((await self.request("/preview", raw=b'{"settings":{},"settings":{},"text":"x"}'))["data"]["code"],
                         "group_reply_request_invalid")
        for changes in ({}, [], {"group_air_guard_enabled": 1},
                        {"group_wakeup_direct_words": '["ok", 2]'},
                        {"group_wakeup_direct_words": '["unfinished"'},
                        {"group_wakeup_direct_words": '["repeat", "REPEAT"]'}):
            result = await self.request("/configure", data={"operation_key": "invalid", "edit_token": token,
                "changes": changes})
            self.assertNotEqual(result["status"], "ok", result)
        self.assertEqual((await self.request())["data"]["edit_token"], token)
        self.window_personas["qq-main:GroupMessage:300"] = "second"
        self.assertEqual((await self.request())["data"]["code"], "group_reply_role_changed")
        self.window_personas["qq-main:GroupMessage:300"] = "original"
        operator = dashboard_operator(self.controller.runtime, "admin", at=self.clock.now())
        policy = ObjectAccessPolicy(self.controller.runtime)
        policy.set_tier(operator.subject, AuthorizationTier.IMPORTANT,
                        expected_revision=policy.revision, observed_at=self.clock.now())
        self.assertEqual((await self.request())["data"]["code"], "core_access_required")
        self.assertEqual((await self.request("/configure", data={"operation_key": "denied",
            "edit_token": token, "changes": {"group_air_guard_enabled": False}}))["data"]["code"],
            "core_access_required")
        self.assertEqual((await self.request("/preview", data={"settings": status["data"]["settings"],
                                                               "text": "offline"}))["data"]["code"],
                         "core_access_required")

    async def test_style_samples_without_a_group_session_is_an_empty_page(self):
        await self.start_host()
        # The samples page lists one source per configured group, so a group
        # that has not produced a V2 turn yet is an empty page and not a
        # page-level read failure.
        samples = await self.request("/style/samples", query="state=all&limit=24")
        self.assertEqual(samples["status"], "ok", samples)
        self.assertEqual(samples["data"]["items"], [])
        self.assertEqual(samples["data"]["counts"], {})
        self.assertIsNone(samples["data"]["next_cursor"])
        self.assertEqual(samples["data"]["unavailable_reason"], "role_binding_missing")
        self.assertIs(samples["data"]["sample_review_enabled"], False)
        # The reader aggregates one runtime block per source, so the empty page
        # keeps the same stable shape instead of leaving it undefined.
        runtime = samples["data"]["runtime"]
        self.assertIsInstance(runtime, dict)
        self.assertIsNone(runtime["last_result"])
        self.assertIs(runtime["running"], False)
        self.assertIs(runtime["learning_enabled"], False)
        self.assertIs(runtime["application_enabled"], False)
        self.assertEqual((runtime["minimum_samples"], runtime["eligible_samples"]), (0, 0))
        self.assertIsNone(runtime["new_messages"])
        self.assertIsNone(runtime["required_new_messages"])
        self.assertEqual((runtime["group_batches_today"], runtime["group_daily_batch_limit"]), (0, 0))
        # Identity errors are not an empty group, and decisions still need a
        # real conversation context.
        unauthenticated = await self.request("/style/samples", username=None, query="state=all&limit=24")
        self.assertEqual(unauthenticated["status"], "error", unauthenticated)
        self.assertEqual(unauthenticated["data"]["code"], "dashboard_identity_required")
        command = await self.request("/style/samples/command", data={
            "handle": "input:missing", "action": "approve",
            "view_token": "token", "operation_key": "group-sample-command"})
        self.assertEqual(command["status"], "error", command)
        self.assertEqual(command["data"]["code"], "expression_group_context_required")

    async def test_style_samples_without_an_original_group_conversation_is_an_empty_page(self):
        await self.start_host()
        # The reported symptom is a group whose role is bound but whose window
        # has no V2 turn yet: binding the role without sending a message
        # reproduces that state, so the samples page must answer with an empty
        # page carrying the reason instead of a page-level read failure.
        selected = self.controller.settings.target("group-a")
        session = self.controller.session
        bridge = session.bridge.groups
        account = bridge.accounts[(selected.platform_id, selected.account_id)]
        conversation_id, role, _ = await bridge._role(account, selected.group_id)
        scope = ScopeRef(self.controller.runtime.installation_id, selected.platform_id, ScopeKind.GROUP,
                         selected.group_id, selected.bot_id, selected.account_id, conversation_id, selected.group_id)
        session.application.tasks.identity.bind_role(
            scope=scope, role=role, expected_revision=0, observed_at=self.clock.now())
        samples = await self.request("/style/samples", query="state=all&limit=24")
        command = await self.request("/style/samples/command", data={
            "handle": "input:missing", "action": "approve",
            "view_token": "token", "operation_key": "group-sample-command"})
        self.assertEqual(samples["status"], "ok", samples)
        self.assertEqual(samples["data"]["items"], [])
        self.assertEqual(samples["data"]["unavailable_reason"], "expression_group_context_required")
        self.assertIn("runtime", samples["data"])
        self.assertEqual(command["status"], "error", command)
        self.assertEqual(command["data"]["code"], "expression_group_context_required")


if __name__ == "__main__":
    unittest.main()
