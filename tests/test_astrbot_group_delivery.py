"""Group delivery isolation, dispatch fencing, durable receipts and recovery."""

from dataclasses import asdict, replace
from copy import deepcopy
from datetime import timedelta
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from astrbot_host.account_session_api import AccountSessionWebAPI
from astrbot_host.group_authority import GroupAuthority
from astrbot_host.group_delivery import INTENT_PREFIX
from astrbot_host.multi_api import PREFIX
from astrbot_host.multi_settings import MultiHostSettings
from astrbot_host.settings import HostControlError
from companion_v2.adapters.astrbot.onebot_group import OneBotGroupSender
from companion_v2.delivery.models import DeliveryEnvelope, LocalSender
from companion_v2.execution.models import TaskContext, TaskError, digest
from companion_v2.foundation.refs import AudienceRef
from tests.group_host_support import GroupFixture, Query, group_config
from tests.native_adapter_support import FakeEvent
from tests.staging_bootstrap_support import database_contents, wait_for_batch


class GroupDeliveryTests(GroupFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.controller = self.make_controller(group_config(group_delivery_mode="onebot_group"))

    async def reply(self, event=None, **kwargs):
        event = event or self.group_event(**kwargs)
        result = await self.controller.receive_claimed(event)
        self.assertTrue(result.is_success, result.failure)
        batch = result.unwrap()
        await wait_for_batch(self.controller.session, batch)
        return self.controller.preview(self.controller.target_for_event(event).target_id, batch).unwrap()

    async def test_relationship_group_target_control_changes_only_the_selected_group(self):
        api = AccountSessionWebAPI(self.controller)
        api._discovered = AsyncMock(return_value={"targets": [{"scope_kind": "group", "platform_id": "qq-main",
            "account_id": "100", "group_id": "399"}]})

        def save(candidate):
            self.controller.config = deepcopy(candidate)

        self.controller.save_plugin_config = save

        async def change(enabled, group_id="399", account_id="100", bot_id="bot-a"):
            body = {"platform_id": "qq-main", "account_id": account_id, "bot_id": bot_id,
                    "group_id": group_id, "enabled": enabled}
            request = NS(username="dashboard-admin", query=Query(),
                         body=AsyncMock(return_value=json.dumps(body).encode("utf-8")))
            with patch("astrbot_host.account_session_api.request", request):
                return await api.save_group_target()

        before = deepcopy(self.controller.config["multi_host"]["group_targets"])
        enabled = await change(True)
        self.assertEqual(enabled["status"], "ok", enabled)
        self.assertFalse(enabled["data"]["requires_initialize"])
        self.assertFalse(enabled["data"]["requires_reload"])
        target_id = enabled["data"]["target_id"]
        self.assertIsNotNone(self.controller.session)
        self.assertIn(target_id, {target.target_id for target in self.controller.settings.group_targets})
        self.assertEqual(target_id, "group-" + digest(("qq-main", "100", "399"))[:20])
        rows = self.controller.config["multi_host"]["group_targets"]
        self.assertEqual(len(rows), len(before) + 1)
        self.assertEqual([row["group_id"] for row in rows[:-1]], [row["group_id"] for row in before])
        self.assertEqual(rows[-1]["target_id"], target_id)

        removed = await change(False)
        self.assertEqual(removed["status"], "ok", removed)
        self.assertFalse(removed["data"]["configured"])
        self.assertFalse(removed["data"]["requires_initialize"])
        self.assertFalse(removed["data"]["requires_reload"])
        self.assertIsNotNone(self.controller.session)
        self.assertNotIn(target_id, {target.target_id for target in self.controller.settings.group_targets})
        self.assertEqual(self.controller.config["multi_host"]["group_targets"], before)

        unavailable = await change(True, "999")
        self.assertEqual(unavailable["data"]["code"], "account_group_target_unavailable")
        self.assertEqual(self.controller.config["multi_host"]["group_targets"], before)

        unresolved = await change(True, account_id="", bot_id="")
        self.assertEqual(unresolved["data"]["code"], "account_group_target_route_unresolved")
        self.assertEqual(self.controller.config["multi_host"]["group_targets"], before)

    async def test_group_target_control_can_enable_all_groups_for_one_account(self):
        api = AccountSessionWebAPI(self.controller)
        api._discovered = AsyncMock(return_value={"targets": []})
        self.controller.save_plugin_config = lambda candidate: setattr(self.controller, "config", deepcopy(candidate))
        body = {"platform_id": "qq-main", "account_id": "100", "bot_id": "bot-a",
                "group_id": "*", "enabled": True}
        request = NS(username="dashboard-admin", query=Query(),
                     body=AsyncMock(return_value=json.dumps(body).encode("utf-8")))

        with patch("astrbot_host.account_session_api.request", request):
            result = await api.save_group_target()

        self.assertEqual(result["status"], "ok", result)
        target_id = result["data"]["target_id"]
        self.assertEqual(target_id, "group-" + digest(("qq-main", "100", "*"))[:20])
        saved = self.controller.config["multi_host"]["group_targets"]
        self.assertTrue(any(row["target_id"] == target_id and row["group_id"] == "*" for row in saved))
        api._discovered.assert_not_awaited()

    async def test_wildcard_group_target_admits_an_unlisted_group_after_restart(self):
        config = group_config()
        config["multi_host"]["group_targets"] = [dict(
            target_id="group-all", platform_id="qq-main", account_id="100",
            bot_id="bot-a", group_id="*")]
        self.controller = self.make_controller(config)
        await self.start_host()
        self.window_personas["qq-main:GroupMessage:777"] = "original"

        event = FakeEvent(NS(platform=self.native.platforms["qq-main"]),
                          event_id="wildcard-group-message", sender="200", login="100", text="新群也能收到吗")
        kind = NS(value="GroupMessage")
        event.message_obj.type = event.session.message_type = kind
        event.message_obj.group_id = event.message_obj.session_id = "777"
        event.session.session_id = "777"
        event.unified_msg_origin = "qq-main:GroupMessage:777"
        event.message_obj.raw_message = dict(post_type="message", message_type="group",
            self_id=100, user_id=200, sender={"user_id": 200}, group_id=777,
            message_id="wildcard-group-message")
        event.created_at = self.clock.now().timestamp()

        route = self.controller.target_for_event(event)
        self.assertTrue(route.target_id.startswith("group-"))
        self.assertEqual((route.platform_id, route.account_id, route.bot_id, route.group_id),
                         ("qq-main", "100", "bot-a", "777"))
        result = await self.controller.receive_claimed(event)
        self.assertTrue(result.is_success, result.failure)
        batch = result.unwrap()
        await wait_for_batch(self.controller.session, batch)
        parts = self.controller.session.application.runtime.read_all(
            "SELECT state FROM conversation_parts WHERE batch_id = ?", (batch,))
        self.assertTrue(parts)

    async def test_unexpected_group_target_write_error_keeps_the_backend_reason(self):
        api = AccountSessionWebAPI(self.controller)
        api._discovered = AsyncMock(return_value={"targets": [{"scope_kind": "group", "platform_id": "qq-main",
            "account_id": "100", "group_id": "399"}]})
        self.controller.apply_bindings = AsyncMock(side_effect=RuntimeError("simulated config writer failure"))
        body = {"platform_id": "qq-main", "account_id": "100", "bot_id": "bot-a",
                "group_id": "399", "enabled": True}
        request = NS(username="dashboard-admin", query=Query(),
                     body=AsyncMock(return_value=json.dumps(body).encode("utf-8")))

        with patch("astrbot_host.account_session_api.request", request):
            result = await api.save_group_target()

        self.assertEqual(result["status"], "error", result)
        self.assertEqual(result["data"]["code"], "account_group_target_apply_failed")
        self.assertIn("RuntimeError: simulated config writer failure", result["reason"])

    def test_mode_defaults_and_existing_fingerprints_remain_compatible(self):
        old = MultiHostSettings.parse(group_config())
        explicit = MultiHostSettings.parse(group_config(group_delivery_mode="preview"))
        self.assertEqual(old.fingerprint, explicit.fingerprint)
        self.assertEqual(old.common.delivery_mode, "onebot_private")
        self.assertEqual(old.group_delivery_mode, "onebot_group")
        for mode in ("onebot_private", "all", True, None):
            with self.subTest(mode=mode), self.assertRaises(HostControlError):
                MultiHostSettings.parse(group_config(group_delivery_mode=mode))

    async def test_configured_groups_need_no_delivery_grant_and_private_routes_reject_them(self):
        self.controller = self.make_controller(group_config(delivery_mode="onebot_private", group_delivery_mode="onebot_group"))
        self.controller.register_routes()
        routes = {path: handler for path, handler, _, _ in self.native.registered_web_apis}
        request = NS(username="dashboard-admin", query=Query())
        with patch("astrbot_host.multi_api.request", request):
            for action in ("probe", "initialize", "start"):
                self.assertEqual((await routes[PREFIX + "/" + action]())["status"], "ok")
            # 逐群投递授权已经移除：旧路由不再注册，配置里的群目标直接可发。
            for legacy in ("authorize-group-delivery", "revoke-group-delivery"):
                self.assertNotIn(PREFIX + "/" + legacy, routes)
            before = database_contents(self.controller.runtime)
            request.query = Query(target_id="group-a")
            self.assertEqual((await routes[PREFIX + "/authorize-delivery"]())["data"]["code"], "host_group_authorization_required")
            self.assertEqual((await routes[PREFIX + "/revoke-delivery"]())["data"]["code"], "host_group_authorization_required")
            self.assertEqual(database_contents(self.controller.runtime), before)
            self.assertEqual((await routes[PREFIX + "/status"]())["data"]["group_delivery_mode"], "onebot_group")
            self.assertNotIn("group_delivery_authorization", self.controller.web_api.target_inventory()[0])
            self.assertTrue((await self.reply())["sent"])
            for target in ("group-b", "group-c"):
                # A configured group target is eligible without a separate grant;
                # private records never change its native route.
                self.controller.authority.write("host.multi.delivery:" + target,
                    {"revoked": False, "expires_at": (self.clock.now() + timedelta(minutes=15)).isoformat()})
                self.assertTrue((await self.reply(target=target))["sent"])
            await self.accepted()
            api = self.native.platform.client._api._wsr_api
            self.assertEqual([action for action, _ in api.calls if action.startswith("send_")],
                             ["send_group_msg", "send_group_msg", "send_private_msg"])
            self.assertEqual([action for action, _ in self.native.platforms["qq-second"].client._api._wsr_api.calls
                              if action.startswith("send_")],
                             ["send_group_msg"])
            request.query = Query(target_id="first")
            self.assertEqual((await routes[PREFIX + "/authorize-delivery"]())["status"], "ok")
            await self.accepted(identifier="authorized-private")
            self.assertEqual([action for action, _ in api.calls][-2:], ["send_group_msg", "send_private_msg"])
        await self.controller.terminate()
        self.assertEqual(self.native.registered_web_apis, [])

    async def test_original_account_group_and_member_session_select_exact_transport(self):
        await self.start_host()
        for index, target in enumerate(("group-a", "group-b", "group-c")):
            result = await self.reply(target=target, unique=True, sender=str(700 + index))
            configured = self.controller.settings.target(target)
            api = self.native.platforms[configured.platform_id].client._api._wsr_api
            self.assertEqual(api.calls[-1], ("send_group_msg", {"self_id": configured.account_id,
                "group_id": int(configured.group_id), "message": [{"type": "text", "data": {"text": "我在这里。"}}]}))
            self.assertEqual(result["speaker_id"], str(700 + index))
            self.assertTrue(result["sent"])
            self.assertEqual(result["parts"][0]["platform_ref"],
                "onebot-message:" + digest([configured.platform_id, configured.account_id])[:24] + ":456")

    async def test_delivery_admission_failure_rolls_back_identity_source_and_input(self):
        await self.start_host()
        authority = self.controller.authority
        before = database_contents(authority.runtime)
        with patch.object(authority.group_delivery, "admit", side_effect=TaskError("synthetic_admission_failure", "rollback")):
            result = await self.controller.receive_claimed(self.group_event(sender="999", unique=True))
        self.assertEqual(result.failure.code, "synthetic_admission_failure")
        self.assertEqual(database_contents(authority.runtime), before)
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])

    async def test_revocation_persona_source_and_connection_races_prevent_dispatch(self):
        app = await self.start_host()
        api = self.native.platform.client._api._wsr_api
        original_client = self.native.platform.client
        original_connections = dict(original_client._wsr_api_clients)
        for index, mutation in enumerate(("persona", "prompt", "disconnect", "client", "withdraw", "grant")):
            with self.subTest(mutation=mutation):
                api.calls.clear()
                original = app.delivery.sender
                original_prompt = self.native.persona_records["original"]["prompt"]
                async def guarded(envelope):
                    async def during_role_lookup(_):
                        scope = envelope.context.scope
                        if mutation == "persona":
                            self.native.selections[f"qq-main:GroupMessage:{scope.session_id}"] = "second"
                        elif mutation == "prompt":
                            self.native.persona_records["original"]["prompt"] = "改变后的提示词"
                        elif mutation == "disconnect":
                            original_client._wsr_api_clients.pop("100", None)
                        elif mutation == "client":
                            self.native.platform.client = object()
                        elif mutation == "withdraw":
                            key = app.runtime.read_one("SELECT i.event_key FROM conversation_inputs i "
                                "JOIN conversation_parts p ON p.batch_id = i.batch_id WHERE p.part_id = ?", (envelope.part_id,))[0]
                            app.withdraw(envelope.context, key).unwrap()
                        elif mutation == "grant":
                            identity = self.controller.authority.identities
                            identifier = GroupAuthority.grant_id(scope, envelope.context.actor.subject, "conversation.reply")
                            identity.revoke_grant(identifier, revoked_at=self.clock.now(), expected_revision=identity.revision)
                    self.native.before_resolve = during_role_lookup
                    try:
                        return await original.send(envelope)
                    finally:
                        self.native.before_resolve = None
                app.delivery.sender = LocalSender(original.binding, guarded)
                batch = (await self.controller.receive_claimed(self.group_event(identifier=mutation,
                    sender=str(800 + index), unique=True))).unwrap()
                await wait_for_batch(self.controller.session, batch)
                parts = app.runtime.read_all("SELECT state FROM conversation_parts WHERE batch_id = ?", (batch,))
                self.assertTrue(parts)
                should_send = False
                self.assertEqual(any(part["state"] == "delivered" for part in parts), should_send)
                self.assertEqual(bool(api.calls), should_send)
                app.delivery.sender = original
                self.native.platform.client = original_client
                original_client._wsr_api_clients.clear()
                original_client._wsr_api_clients.update(original_connections)
                self.native.selections = {"qq-main": "original", "qq-second": "original"}
                self.native.persona_records["original"]["prompt"] = original_prompt

    async def test_original_ack_and_unknown_survive_restart_without_replay(self):
        app = await self.start_host()
        api = self.native.platform.client._api._wsr_api
        api.result = {"message_id": 123}
        delivered_event = self.group_event(identifier="delivered")
        delivered = await self.reply(delivered_event)
        self.assertTrue(delivered["parts"][0]["platform_ref"].endswith(":123"))
        self.assertTrue(delivered["parts"][0]["receipt_ref"])
        api.error = TimeoutError("response may have been lost")
        unknown_event = self.group_event(identifier="unknown")
        unknown = await self.reply(unknown_event)
        self.assertEqual(unknown["parts"][0]["state"], "unknown")
        self.assertFalse(unknown["sent"])
        context = TaskContext.restore(json.loads(app.messages.batch(unknown["batch_id"])["context_json"]))
        confirmed = app.messages.history(context).unwrap()["items"]
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(len(api.calls), 2)
        receipts = [dict(row) for row in app.runtime.read_all("SELECT * FROM conversation_delivery_receipts", ())]
        await self.controller.terminate()
        self.controller = self.make_controller(group_config(group_delivery_mode="onebot_group"))
        self.assertTrue((await self.controller.load_existing()).is_success)
        self.assertFalse(self.controller.claims(unknown_event))
        self.assertTrue((await self.controller.start()).is_success)
        for event, previous in ((delivered_event, delivered), (unknown_event, unknown)):
            self.assertEqual((await self.reply(event))["batch_id"], previous["batch_id"])
        self.assertEqual(len(api.calls), 2)
        self.assertEqual([dict(row) for row in self.controller.runtime.read_all("SELECT * FROM conversation_delivery_receipts", ())], receipts)

    async def test_pending_batches_keep_admission_intent_across_restart(self):
        preview_config = group_config()
        preview_config["runtime"].update(quiet_seconds=2, maximum_wait_seconds=5)
        self.controller = self.make_controller(preview_config)
        app = await self.start_host()
        events = [self.group_event(identifier="old-preview", sender="700", unique=True),
                  self.group_event(identifier="legacy-preview", sender="701", unique=True)]
        batches = [(await self.controller.receive_claimed(event)).unwrap() for event in events]
        # Simulate a pre-upgrade batch, which has no frozen host delivery intent.
        with app.runtime.transaction() as connection:
            connection.execute("DELETE FROM runtime_metadata WHERE key = ?", (INTENT_PREFIX + batches[1],))
        await self.controller.terminate()
        send_config = group_config(group_delivery_mode="onebot_group")
        self.controller = self.make_controller(send_config)
        await self.start_host()
        for event, batch in zip(events, batches):
            self.assertEqual((await self.controller.receive_claimed(event)).unwrap(), batch)
        self.clock.advance(timedelta(seconds=3))
        self.controller.session.application.notify()
        for batch in batches:
            await wait_for_batch(self.controller.session, batch)
        admitted = self.controller.preview("group-a", batches[0]).unwrap()
        legacy = self.controller.preview("group-a", batches[1]).unwrap()
        self.assertEqual(admitted["delivery_mode"], "onebot_group")
        self.assertEqual(legacy["delivery_mode"], "onebot_group")
        self.assertEqual(admitted["configured_delivery_mode"], "onebot_group")
        self.assertEqual(legacy["configured_delivery_mode"], "onebot_group")
        self.assertTrue(admitted["sent"])
        self.assertTrue(legacy["sent"])
        self.assertEqual(len(self.native.platform.client._api._wsr_api.calls), 2)
        self.assertTrue((await self.reply(identifier="new-authorized", unique=True))["sent"])

    async def test_pending_admitted_input_recovers_after_restart(self):
        config = group_config(group_delivery_mode="onebot_group")
        config["runtime"].update(quiet_seconds=2, maximum_wait_seconds=5)
        self.controller = self.make_controller(config)
        await self.start_host()
        event = self.group_event()
        batch = (await self.controller.receive_claimed(event)).unwrap()
        await self.controller.terminate()
        self.controller = self.make_controller(config)
        self.assertTrue((await self.controller.load_existing()).is_success)
        self.assertTrue((await self.controller.start()).is_success)
        self.clock.advance(timedelta(seconds=3))
        self.controller.session.application.notify()
        await wait_for_batch(self.controller.session, batch)
        self.assertTrue(self.controller.preview("group-a", batch).unwrap()["sent"])
        self.assertEqual((await self.controller.receive_claimed(event)).unwrap(), batch)
        self.assertEqual(len(self.native.platform.client._api._wsr_api.calls), 1)

    async def test_sender_rejects_wrong_audience_session_missing_fence_and_invalid_ack(self):
        await self.start_host()
        session = self.controller.session
        captured = (await session.bridge.capture(self.group_event(unique=True))).unwrap()
        context = self.controller.authority.groups.select(self.controller.settings.target("group-a"), captured)
        envelope = DeliveryEnvelope("sender-part", "sender-operation", context, 1, "群聊发送", digest("群聊发送"),
                                    session.sender_binding, lambda: True)
        sender = OneBotGroupSender(session.bridge, binding=session.sender_binding, version=self.types.version,
            reverse_api_type=self.types.reverse_api, authorize=lambda: session.authorize(context), now=self.clock.now)
        api = self.native.platform.client._api._wsr_api
        wrong_speaker = replace(context.actor, principal=replace(context.actor.principal, native_account_id="201"))
        narrowed = replace(context, audience=AudienceRef(context.scope, (context.actor.subject,)))
        for bad in (replace(envelope, context=replace(context, actor=wrong_speaker)),
                    replace(envelope, context=narrowed), replace(envelope, before_dispatch=None),
                    replace(envelope, text="tampered"), replace(envelope, sender=replace(envelope.sender, generation="other"))):
            self.assertEqual((await sender.send(bad)).status, "not_sent")
        self.assertEqual(api.calls, [])
        for ack in (True, None, {}, {"message_id": -1}, {"message_id": 0}, {"message_id": True}, {"message_id": "0456"}, {"message_id": "abc"}):
            with self.subTest(ack=ack):
                api.result = ack
                self.assertEqual((await sender.send(envelope)).status, "unknown")
        self.assertEqual(len(api.calls), 8)
        self.assertTrue(all(action == "send_group_msg" for action, _ in api.calls))


if __name__ == "__main__":
    unittest.main()
