"""从被拒记录追加一项具体私聊目标：只动那一行路由，其余配置原样。"""

import asyncio
import json
import unittest
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from astrbot_host.acquaintance_api import AcquaintanceWebAPI, STRANGER_TARGET_PATH
from astrbot_host.multi_settings import HostControlError, MultiHostSettings
from astrbot_host.stranger_notice import CONTACT_KIND
from companion_v2.execution.models import TaskError
from tests.group_host_support import group_config
from tests.multi_host_support import MultiFixture, multi_config


class _Query(dict):
    def getlist(self, key):
        return [self[key]] if key in self else []


class _Body:
    def __init__(self, payload=None, raw=None):
        self.payload, self.raw = payload, raw

    async def __call__(self):
        if self.raw is not None:
            return self.raw
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


class _BrokenSettings:
    """Stands in for a configuration the host refuses to parse."""

    @property
    def all_targets(self):
        raise HostControlError("consistency_error", "the configuration is not readable")


def _native_context():
    """The host's native seam: connected clients for the configured accounts."""
    clients = {"100": object(), "101": object()}
    return NS(get_platform_inst=lambda platform_id: NS(
        meta=lambda: NS(id=platform_id, name="aiocqhttp"),
        get_client=lambda: NS(_wsr_api_clients=clients)))


def _ledger(platform_id, login, peer, *, attempts=1):
    return json.dumps({"version": 1, "platform_id": platform_id, "login": login, "peer": peer,
                       "first_seen_at": "2026-11-03T10:00:00+00:00",
                       "last_seen_at": "2026-11-03T10:05:00+00:00",
                       "attempts": attempts, "last_outcome": "stranger_notice_sent"})


class StrangerTargetApiTests(unittest.IsolatedAsyncioTestCase):
    """API seam with a recording apply, so the exact configuration delta is visible."""

    def config_for(self):
        return group_config()

    def setUp(self):
        self.config = self.config_for()
        self.applied, self.expected = [], []
        self.behavior = []
        self.result = {"saved": True, "requires_reload": False, "requires_initialize": False,
                       "started": True, "startup_error": None}
        self.rows = []
        self.controller = NS(
            config=self.config, settings=MultiHostSettings.parse(self.config),
            runtime=NS(read_all=lambda sql, params=(): self.rows),
            context=_native_context(), _terminated=False, _control_lock=asyncio.Lock(), now=lambda: None,
            apply_bindings=AsyncMock(side_effect=self.apply))
        self.api = AcquaintanceWebAPI(self.controller)
        self.operator = patch.object(AcquaintanceWebAPI, "_operator",
                                     AsyncMock(return_value=("admin", None)))
        self.operator.start()

    def tearDown(self):
        self.operator.stop()

    def adopt(self, config):
        self.controller.config.clear()
        self.controller.config.update(deepcopy(config))
        self.controller.settings = MultiHostSettings.parse(self.controller.config)

    async def apply(self, candidate, *, expected_config=None):
        self.assertIsNot(expected_config, self.controller.config,
                         "the reference check needs its own snapshot")
        self.assertEqual(expected_config, self.controller.config)
        self.expected.append(deepcopy(expected_config))
        self.applied.append(deepcopy(candidate))
        behavior = self.behavior.pop(0) if self.behavior else None
        if behavior == "stale_saved":
            # Another administrator saved this very target while we were in flight.
            self.adopt(candidate)
            raise TaskError("legacy_plan_stale", "插件配置在等待保存期间已变化，请重新试算。")
        if behavior == "stale_unrelated":
            self.controller.config["concurrent_edit"] = True
            raise TaskError("legacy_plan_stale", "插件配置在等待保存期间已变化，请重新试算。")
        self.adopt(candidate)
        return dict(self.result)

    async def send(self, payload=None, *, raw=None, query=None):
        request = NS(username="admin", query=_Query(query or ()), body=_Body(payload, raw))
        with patch("astrbot_host.acquaintance_api.request", request):
            return await self.api.add_stranger_target()

    async def read(self):
        request = NS(username="admin", query=_Query(), body=_Body({}))
        with patch("astrbot_host.acquaintance_api.request", request):
            return await self.api.strangers()

    def payload(self, platform_id="qq-main", login="100", peer="999"):
        return {"platform_id": platform_id, "login": login, "peer": peer}

    def broken_api(self):
        controller = NS(**vars(self.controller))
        controller.settings = _BrokenSettings()
        return AcquaintanceWebAPI(controller)

    async def test_an_unreadable_configuration_leaves_the_ledger_visible(self):
        self.rows = [{"document_json": _ledger("qq-main", "100", "999")}]
        with patch("astrbot_host.acquaintance_api.request",
                   NS(username="admin", query=_Query(), body=_Body(None))):
            result = await self.broken_api().strangers()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["items"][0]["target_state"], "unavailable")

    async def test_an_unreadable_configuration_refuses_the_command(self):
        with patch("astrbot_host.acquaintance_api.request",
                   NS(username="admin", query=_Query(), body=_Body(self.payload()))):
            result = await self.broken_api().add_stranger_target()
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["data"]["code"], "stranger_target_unavailable")
        self.assertEqual(self.applied, [])

    async def test_the_route_is_registered_beside_the_read_only_list(self):
        routes = {(path, tuple(methods)) for path, _, methods, _ in self.api.routes()}
        self.assertIn((STRANGER_TARGET_PATH, ("POST",)), routes)

    async def test_one_exact_target_is_the_only_configuration_change(self):
        before = deepcopy(self.config)

        result = await self.send(self.payload())

        self.assertEqual(result["status"], "ok", result)
        data = result["data"]
        self.assertEqual(data["state"], "added")
        self.assertTrue(data["saved"])
        self.assertTrue(data["started"])
        self.assertTrue(data["target_id"].startswith("private-"))
        self.assertEqual(len(self.applied), 1)
        self.assertEqual(len(self.expected), 1)

        after = self.config
        self.assertEqual({key: value for key, value in after.items() if key != "multi_host"},
                         {key: value for key, value in before.items() if key != "multi_host"})
        self.assertEqual({key: value for key, value in after["multi_host"].items() if key != "targets"},
                         {key: value for key, value in before["multi_host"].items() if key != "targets"})
        old_targets = before["multi_host"]["targets"]
        new_targets = after["multi_host"]["targets"]
        self.assertEqual(new_targets[:len(old_targets)], old_targets, "existing targets keep their content and order")
        self.assertEqual(len(new_targets), len(old_targets) + 1)
        self.assertEqual(new_targets[-1], {
            "target_id": data["target_id"], "platform_id": "qq-main", "account_id": "100",
            "bot_id": "bot-a", "participant_id": "999"})
        self.assertFalse([item for item in new_targets if item["participant_id"] == "*"],
                         "the quick add never invents a wildcard template")
        parsed = MultiHostSettings.parse(after)
        effective = parsed.private_target("qq-main", "100", "999")
        self.assertEqual(effective.target_id, data["target_id"])
        self.assertEqual(effective.bot_id, "bot-a")

    async def test_each_receiver_and_platform_keeps_its_own_route(self):
        before = deepcopy(self.config)

        other_receiver = await self.send(self.payload("qq-second", "101", "999"))
        same_sender_other_platform = await self.send(self.payload("qq-second", "101", "250"))
        second_sender_first_receiver = await self.send(self.payload("qq-main", "100", "998"))

        for result in (other_receiver, same_sender_other_platform, second_sender_first_receiver):
            self.assertEqual(result["status"], "ok", result)
            self.assertEqual(result["data"]["state"], "added")
        targets = self.config["multi_host"]["targets"]
        self.assertEqual(targets[:len(before["multi_host"]["targets"])], before["multi_host"]["targets"])
        added = {(item["platform_id"], item["account_id"], item["participant_id"], item["bot_id"])
                 for item in targets[len(before["multi_host"]["targets"]):]}
        self.assertEqual(added, {("qq-second", "101", "999", "bot-b"),
                                 ("qq-second", "101", "250", "bot-b"),
                                 ("qq-main", "100", "998", "bot-a")})
        self.assertEqual(len(self.applied), 3)

    async def test_existing_exact_target_is_a_stable_no_op(self):
        first = await self.send(self.payload())
        saved = deepcopy(self.config)

        again = await self.send(self.payload())

        self.assertEqual(again["status"], "ok", again)
        self.assertEqual(again["data"]["state"], "added")
        self.assertEqual(again["data"]["target_id"], first["data"]["target_id"])
        self.assertFalse(again["data"]["saved"])
        self.assertEqual(self.config, saved)
        self.assertEqual(len(self.applied), 1, "a duplicate never runs the apply lifecycle")

    async def test_wildcard_target_for_the_same_receiver_reports_covered(self):
        covered = self.config_for()
        covered["multi_host"]["targets"] = [
            {"target_id": "first", "platform_id": "qq-main", "account_id": "100",
             "bot_id": "bot-a", "participant_id": "*"},
            dict(covered["multi_host"]["targets"][1])]
        self.controller.config.clear()
        self.controller.config.update(covered)
        self.controller.settings = MultiHostSettings.parse(covered)
        saved = deepcopy(covered)

        result = await self.send(self.payload())

        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["data"]["state"], "covered")
        self.assertFalse(result["data"]["saved"])
        self.assertEqual(self.config, saved)
        self.assertEqual(self.applied, [])

    async def test_unconfigured_receiver_is_unavailable_and_writes_nothing(self):
        saved = deepcopy(self.config)

        for payload in (self.payload(login="777"), self.payload(platform_id="qq-third")):
            result = await self.send(payload)
            self.assertEqual(result["status"], "ok", result)
            self.assertEqual(result["data"]["state"], "unavailable")
            self.assertFalse(result["data"]["saved"])
        self.assertEqual(self.config, saved)
        self.assertEqual(self.applied, [])

    async def test_stranger_list_projects_the_live_configuration(self):
        self.rows = [{"document_json": _ledger("qq-main", "100", "200", attempts=3)},
                     {"document_json": _ledger("qq-second", "101", "999")},
                     {"document_json": _ledger("qq-main", "777", "555")},
                     {"document_json": "{not json"}]
        await self.send(self.payload("qq-second", "101", "999"))

        result = await self.read()

        self.assertEqual(result["status"], "ok", result)
        items = {item["peer"]: item for item in result["data"]["items"]}
        self.assertEqual(set(items), {"200", "999", "555"})
        self.assertEqual(items["200"]["target_state"], "added")
        self.assertEqual(items["200"]["attempts"], 3, "ledger history is projected, not rewritten")
        self.assertEqual(items["200"]["first_seen_at"], "2026-11-03T10:00:00+00:00")
        self.assertEqual(items["999"]["target_state"], "added")
        self.assertEqual(items["555"]["target_state"], "unavailable")

    async def test_removing_the_target_restores_addable(self):
        before = deepcopy(self.config)
        await self.send(self.payload())
        self.rows = [{"document_json": _ledger("qq-main", "100", "999")}]
        self.assertEqual((await self.read())["data"]["items"][0]["target_state"], "added")

        self.controller.config.clear()
        self.controller.config.update(before)
        self.controller.settings = MultiHostSettings.parse(before)

        self.assertEqual((await self.read())["data"]["items"][0]["target_state"], "addable")

    async def test_a_concurrent_save_of_the_same_target_is_reported_as_added(self):
        self.behavior = ["stale_saved"]

        result = await self.send(self.payload())

        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["data"]["state"], "added")
        self.assertTrue(result["data"]["conflict"])
        self.assertFalse(result["data"]["saved"])
        self.assertEqual(len(self.applied), 1)
        self.assertEqual([item["participant_id"] for item in self.config["multi_host"]["targets"]],
                         ["200", "200", "999"])

    async def test_an_unrelated_concurrent_save_asks_for_a_retry_and_keeps_it(self):
        self.behavior = ["stale_unrelated"]

        result = await self.send(self.payload())

        self.assertEqual(result["status"], "error", result)
        self.assertEqual(result["data"]["code"], "legacy_plan_stale")
        self.assertTrue(self.config["concurrent_edit"], "the other administrator's change stays saved")
        self.assertEqual(len(self.applied), 1)

    async def test_saved_but_not_started_reports_the_init_and_startup_gap(self):
        self.result = {"saved": True, "requires_reload": False, "requires_initialize": True,
                       "started": False, "startup_error": "host_provider_required"}

        result = await self.send(self.payload())

        self.assertEqual(result["status"], "ok", result)
        data = result["data"]
        self.assertEqual(data["state"], "added")
        self.assertTrue(data["saved"])
        self.assertTrue(data["requires_initialize"])
        self.assertFalse(data["started"])
        self.assertEqual(data["startup_error"], "host_provider_required")

    async def test_invalid_commands_never_touch_the_configuration(self):
        saved = deepcopy(self.config)
        cases = [
            (self.payload() | {"extra": "x"}, None, None),
            ({"platform_id": "qq-main", "login": "100"}, None, None),
            (("not", "a", "dict"), None, None),
            (self.payload(peer="0"), None, None),
            (self.payload(peer="999:1"), None, None),
            (None, b"{not json", None),
            (self.payload(), None, {"platform_id": "qq-main"}),
            (self.payload(), b"x" * 9000, None),
            (self.payload(peer="*"), None, None),
        ]
        for payload, raw, query in cases:
            result = await self.send(payload, raw=raw, query=query)
            self.assertEqual(result["status"], "error", (payload, raw, query, result))
            self.assertEqual(result["data"]["code"], "stranger_target_request_invalid",
                             (payload, raw, query, result))
        self.assertEqual(self.config, saved)
        self.assertFalse([item for item in self.config["multi_host"]["targets"]
                          if item.get("participant_id") == "*"],
                         "a forged wildcard sender never becomes a template")
        self.assertEqual(self.applied, [])


class StrangerTargetHostTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    """真实 runtime：追加后配置读回、被拒列表读回、默认值与既有授权不动。"""

    def setUp(self):
        MultiFixture.setUp(self)
        self.controller = self.make_controller(multi_config(delivery_mode="onebot_private"))

        def save(document):
            self.controller.config.clear()
            self.controller.config.update(deepcopy(document))

        self.controller.save_plugin_config = save

    def event(self, *, sender="999", identifier="stranger-1", login=None, platform_id="qq-main"):
        from tests.native_adapter_support import FakeEvent

        login = login or self.controller.settings.target("first").account_id
        event = FakeEvent(NS(platform=self.native.platforms[platform_id]), event_id=identifier,
                          sender=sender, login=login, text="在吗")
        event.created_at = self.clock.now().timestamp()
        event.message_obj.raw_message = {
            "post_type": "message", "message_type": "private", "sub_type": "friend",
            "self_id": int(login), "user_id": int(sender), "message_id": identifier,
            "sender": {"user_id": int(sender)}, "message": [{"type": "text", "data": {"text": "在吗"}}]}
        event.stopped = False
        event.stop_event = lambda: setattr(event, "stopped", True)
        return event

    async def refuse(self, **kwargs):
        from astrbot_host.stranger_notice import refuse_stranger

        return await refuse_stranger(self.controller, self.event(**kwargs))

    def set_defaults(self, app, **changes):
        from astrbot_host.management_identity import dashboard_operator

        from companion_v2.configuration.models import ConfigLayer, ConfigTarget
        from companion_v2.foundation.refs import OperationRef, VersionRef
        from companion_v2.relationships.private_access import GROUP

        target = ConfigTarget(app.runtime.installation_id, ConfigLayer.INSTALLATION)
        operator = dashboard_operator(app.runtime, "tester", at=self.clock.now())
        snapshot = app.configuration.read(operator, target=target, group_id=GROUP,
                                          observed_at=self.clock.now()).unwrap()
        identifier = "private-access-defaults-" + ",".join(sorted(changes))
        owner = app.configuration.registry.group(GROUP)[0].owner_id
        return app.configuration.patch(
            operator, target=target, group_id=GROUP, expected_edit_token=snapshot.edit_token,
            changes={GROUP + "." + key: value for key, value in changes.items()},
            operation=OperationRef(identifier, owner, target.authorization_scope,
                                   VersionRef(identifier, 1), identifier),
            observed_at=self.clock.now()).unwrap()

    def defaults(self, app):
        from astrbot_host.management_identity import dashboard_operator

        from companion_v2.configuration.models import ConfigLayer, ConfigTarget
        from companion_v2.relationships.private_access import GROUP

        target = ConfigTarget(app.runtime.installation_id, ConfigLayer.INSTALLATION)
        operator = dashboard_operator(app.runtime, "tester", at=self.clock.now())
        return dict(app.configuration.read(operator, target=target, group_id=GROUP,
                                           observed_at=self.clock.now()).unwrap().values)

    async def post(self, payload):
        request = NS(username="admin", query=_Query(), body=_Body(payload))
        with patch("astrbot_host.acquaintance_api.request", request):
            return await AcquaintanceWebAPI(self.controller).add_stranger_target()

    async def read(self):
        request = NS(username="admin", query=_Query(), body=_Body({}))
        with patch("astrbot_host.acquaintance_api.request", request):
            return await AcquaintanceWebAPI(self.controller).strangers()

    async def test_add_records_one_target_and_keeps_defaults_and_existing_grants(self):
        from companion_v2.identity_policy.models import AuthorizationTier

        app = await self.start_host()
        self.set_defaults(app, dialogue_default=False, proactive_default=False)
        identity = self.controller.authority.identities
        context = self.controller.authority.context("first", "role-a")
        subject = next(binding.subject for binding in identity.snapshot().bindings
                       if binding.principal.native_account_id == "200")
        identity.revoke_grant(self.controller.authority.grant_id(context.scope, "conversation.reply"),
                              revoked_at=self.clock.now(), expected_revision=identity.revision)
        identity.issue_grant(grant_id="expired-decision", subject=subject, scope=context.scope,
                             capability="memory.read", tier=AuthorizationTier.STANDARD,
                             valid_from=self.clock.now() - timedelta(days=2),
                             expires_at=self.clock.now() - timedelta(days=1),
                             expected_revision=identity.revision)
        self.assertIsNotNone(await self.refuse(sender="999"))
        defaults_before = self.defaults(app)
        before = deepcopy(self.controller.config)

        listed = await self.read()
        self.assertEqual(listed["status"], "ok", listed)
        self.assertEqual(listed["data"]["items"][0]["target_state"], "addable")

        result = await self.post({"platform_id": "qq-main", "login": "100", "peer": "999"})

        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["data"]["state"], "added")
        self.assertTrue(result["data"]["saved"])
        after = self.controller.config
        self.assertEqual(after["multi_host"]["targets"][:-1], before["multi_host"]["targets"])
        self.assertEqual(after["multi_host"]["targets"][-1]["participant_id"], "999")
        self.assertEqual(after["multi_host"]["targets"][-1]["bot_id"], "bot-a")
        self.assertEqual({key: value for key, value in after["multi_host"].items() if key != "targets"},
                         {key: value for key, value in before["multi_host"].items() if key != "targets"})
        restarted = self.controller.session.application
        self.assertEqual(self.defaults(restarted), defaults_before,
                         "default dialogue/proactive decisions stay exactly as configured")

        listed = await self.read()
        self.assertEqual(listed["data"]["items"][0]["target_state"], "added")
        self.assertEqual(listed["data"]["items"][0]["attempts"], 1)
        self.assertEqual(self.controller.settings.private_target("qq-main", "100", "999").bot_id, "bot-a")
    def authorization_query(self):
        context = self.controller.authority.context("first", "role-a")
        return {"subject_id": context.actor.subject.subject_id,
                "route": {"platform": context.scope.platform, "bot_id": context.scope.bot_id,
                          "account_id": context.scope.account_id,
                          "role_id": context.role_binding.role.subject_id}}

    async def authorization(self):
        query = self.authorization_query()
        request = NS(username="admin", query=_Query({**{"subject_id": query["subject_id"]},
                                                    **query["route"]}), body=_Body({}))
        with patch("astrbot_host.acquaintance_api.request", request):
            result = await AcquaintanceWebAPI(self.controller).authorization()
        self.assertEqual(result["status"], "ok", result)
        return result["data"]

    def seed_personal_decisions(self):
        """One revoked and one expired decision beside the grants init already made."""
        from companion_v2.identity_policy.models import AuthorizationTier

        identity = self.controller.authority.identities
        context = self.controller.authority.context("first", "role-a")
        subject = next(binding.subject for binding in identity.snapshot().bindings
                       if binding.principal.native_account_id == "200")
        identity.revoke_grant(self.controller.authority.grant_id(context.scope, "conversation.reply"),
                              revoked_at=self.clock.now(), expected_revision=identity.revision)
        identity.issue_grant(grant_id="expired-decision", subject=subject, scope=context.scope,
                             capability="role.state.intervene", tier=AuthorizationTier.STANDARD,
                             valid_from=self.clock.now() - timedelta(days=2),
                             expires_at=self.clock.now() - timedelta(days=1),
                             expected_revision=identity.revision)

    def assert_all_decision_kinds(self, status):
        states = {item["state"] for item in status["items"]}
        self.assertTrue({"active", "revoked", "expired"} <= states,
                        "the status sample must carry every decision kind: " + repr(states))

    async def test_personal_decisions_stay_visible_through_the_public_authorization_api(self):
        app = await self.start_host()
        self.set_defaults(app, dialogue_default=True, proactive_default=False)
        self.seed_personal_decisions()
        defaults_before = self.defaults(app)
        before = await self.authorization()
        self.assert_all_decision_kinds(before)
        await self.refuse(sender="999")

        result = await self.post({"platform_id": "qq-main", "login": "100", "peer": "999"})

        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["data"]["state"], "added")
        restarted = self.controller.session.application
        self.assertEqual(self.defaults(restarted), defaults_before,
                         "dialogue on / proactive off stay exactly as configured")
        after = await self.authorization()
        decided = {item["capability"]: (item["state"], item["grant_id"])
                   for item in before["items"] if item["state"] != "off"}
        after_rows = {item["capability"]: (item["state"], item["grant_id"]) for item in after["items"]}
        for capability, decision in decided.items():
            self.assertEqual(after_rows.get(capability), decision,
                             "an existing personal decision changed through the public status")
        self.assertGreaterEqual(after["counts"]["revoked"], before["counts"]["revoked"])
        self.assertGreaterEqual(after["counts"]["expired"], before["counts"]["expired"])

    async def test_proactive_on_dialogue_off_survives_with_every_decision_kind(self):
        app = await self.start_host()
        self.set_defaults(app, dialogue_default=False, proactive_default=True)
        self.seed_personal_decisions()
        defaults_before = self.defaults(app)
        self.assertFalse(defaults_before["private_access.dialogue_default"])
        self.assertTrue(defaults_before["private_access.proactive_default"])
        before = await self.authorization()
        self.assert_all_decision_kinds(before)
        await self.refuse(sender="999")

        result = await self.post({"platform_id": "qq-main", "login": "100", "peer": "999"})

        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["data"]["state"], "added")
        restarted = self.controller.session.application
        self.assertEqual(self.defaults(restarted), defaults_before,
                         "dialogue off / proactive on stay exactly as configured")
        after = await self.authorization()
        before_rows = {item["capability"]: (item["state"], item["grant_id"])
                       for item in before["items"] if item["state"] != "off"}
        after_rows = {item["capability"]: (item["state"], item["grant_id"]) for item in after["items"]}
        for capability, decision in before_rows.items():
            self.assertEqual(after_rows.get(capability), decision,
                             "an existing personal decision changed through the public status")

    async def test_saved_target_survives_a_model_failure_before_initialization(self):
        """The real chain: save succeeds, startup stops on model, reads go dark."""
        await self.start_host()
        await self.refuse(sender="999")
        before = deepcopy(self.controller.config)
        provider = self.native.provider
        self.native.provider = None  # the configured model service goes away
        try:
            result = await self.post({"platform_id": "qq-main", "login": "100", "peer": "999"})
        finally:
            self.native.provider = provider

        self.assertEqual(result["status"], "ok", result)
        self.assertTrue(result["data"]["saved"], result)
        self.assertEqual(result["data"]["state"], "added")
        self.assertTrue(result["data"]["requires_initialize"])
        self.assertFalse(result["data"]["started"])
        self.assertEqual(result["data"]["startup_error"], "host_provider_unavailable")
        after = self.controller.config
        self.assertEqual(after["multi_host"]["targets"][:-1], before["multi_host"]["targets"])
        self.assertEqual(after["multi_host"]["targets"][-1]["participant_id"], "999")
        self.assertEqual(after["multi_host"]["targets"][-1]["bot_id"], "bot-a")
        self.assertEqual({key: value for key, value in after["multi_host"].items() if key != "targets"},
                         {key: value for key, value in before["multi_host"].items() if key != "targets"})

        listed = await self.read()
        self.assertEqual(listed["status"], "error", listed)
        self.assertEqual(listed["data"]["code"], "staging_not_initialized")

        self.assertTrue((await self.controller.initialize_profile()).is_success)
        self.assertTrue((await self.controller.start()).is_success)
        listed = await self.read()
        self.assertEqual(listed["status"], "ok", listed)
        row = listed["data"]["items"][0]
        self.assertEqual(row["target_state"], "added")
        self.assertEqual(row["attempts"], 1, "the refusal history is still there after recovery")
        self.assertEqual(row["peer"], "999")

    async def test_a_disconnected_receiver_is_unavailable_and_recovers(self):
        await self.start_host()
        await self.refuse(sender="999")
        before = deepcopy(self.controller.config)
        client = self.native.platforms["qq-main"].client
        saved = client._wsr_api_clients.pop("100")
        try:
            listed = await self.read()
            self.assertEqual(listed["data"]["items"][0]["target_state"], "unavailable",
                             "an offline Bot account cannot receive a new private target")
            refused = await self.post({"platform_id": "qq-main", "login": "100", "peer": "999"})
            self.assertEqual(refused["status"], "ok", refused)
            self.assertEqual(refused["data"]["state"], "unavailable")
            self.assertFalse(refused["data"]["saved"])
            self.assertEqual(self.controller.config, before, "an unavailable receiver writes nothing")
        finally:
            client._wsr_api_clients["100"] = saved

        listed = await self.read()
        self.assertEqual(listed["data"]["items"][0]["target_state"], "addable")
        added = await self.post({"platform_id": "qq-main", "login": "100", "peer": "999"})
        self.assertEqual(added["data"]["state"], "added", added)
        self.assertTrue(added["data"]["saved"])

    async def test_an_offline_account_does_not_block_another_receivers_row(self):
        await self.start_host()
        await self.refuse(sender="999")
        await self.refuse(sender="998", login="101", platform_id="qq-second", identifier="stranger-2")
        before = deepcopy(self.controller.config)
        client = self.native.platforms["qq-second"].client
        saved = client._wsr_api_clients.pop("101")
        try:
            items = (await self.read())["data"]["items"]
            states = {(item["platform_id"], item["login"], item["peer"]): item["target_state"]
                      for item in items}
            self.assertEqual(states[("qq-second", "101", "998")], "unavailable")
            self.assertEqual(states[("qq-main", "100", "999")], "addable",
                             "one offline account must not disable another receiver")
            self.assertEqual(self.controller.config, before)
        finally:
            client._wsr_api_clients["101"] = saved

    async def test_repeated_request_is_a_no_op_after_the_target_is_saved(self):
        app = await self.start_host()
        await self.refuse(sender="999")
        first = await self.post({"platform_id": "qq-main", "login": "100", "peer": "999"})
        self.assertEqual(first["status"], "ok", first)
        saved = deepcopy(self.controller.config)

        again = await self.post({"platform_id": "qq-main", "login": "100", "peer": "999"})

        self.assertEqual(again["status"], "ok", again)
        self.assertEqual(again["data"]["state"], "added")
        self.assertFalse(again["data"]["saved"])
        self.assertEqual(self.controller.config, saved)
