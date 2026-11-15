"""Account-link API and real host prompt integration, with no live network."""
import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from tests.multi_host_support import MultiFixture
from tests.test_person_authorization import _Query, _body
from astrbot_host.acquaintance_api import AcquaintanceWebAPI
from astrbot_host.account_link_api import AccountLinkWebAPI
from companion_v2.execution.models import require
from companion_v2.identity_policy.repository import PersistentIdentityDirectory
from companion_v2.memory.codec import source
from companion_v2.relationships.account_links import PrivateAccountLinks
from companion_v2.relationships.account_link_context import linked_context


class AccountLinkHostTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = await self.start_host()
        self.observer = self.controller.session.core_reply_observations
        self.api = AccountLinkWebAPI(AcquaintanceWebAPI(self.controller))
        self.first = await self.prepare("first", "old-first", "OLD_PRIVATE_FIRST")
        self.second = await self.prepare("second", "old-second", "OLD_PRIVATE_SECOND")
        self.a, self.b = self.first["context"], self.second["context"]
        self.links = PrivateAccountLinks(self.controller.runtime)

    async def flush(self):
        while pending := self.observer._pending - self.observer._intervention_tasks:
            await asyncio.gather(*tuple(pending))

    async def prepare(self, target, identifier, text):
        result = await self.controller.prepare_core_reply(self.event(target, identifier, text))
        self.assertTrue(result.is_success, result.failure)
        await self.flush()
        return result.unwrap()

    @staticmethod
    def person(context):
        return {"subject_id": context.actor.subject.subject_id,
            "route": {"platform": context.scope.platform, "bot_id": context.scope.bot_id,
                      "account_id": context.scope.account_id, "role_id": context.role_binding.role.subject_id}}

    async def request(self, method, *, body=None, username="link-admin"):
        person = self.person(self.a)
        query = "&".join(key + "=" + value for key, value in
                        {"subject_id": person["subject_id"], **person["route"]}.items()) if body is None else ""
        with patch("astrbot_host.acquaintance_api.request", NS(username=username, query=_Query(query),
                   body=_body(json.dumps(body or {}).encode()))):
            return await getattr(self.api, method)()

    def command(self, **overrides):
        return {**self.person(self.a), "members": [self.person(self.a), self.person(self.b)],
                "share_context": True, "expected_revision": self.links.read()["revision"],
                "operation_key": "associate", **overrides}

    async def link(self):
        result = await self.request("save", body=self.command())
        self.assertEqual(result["status"], "ok", result)
        return result["data"]

    def shared(self, context=None, **kwargs):
        return linked_context(self.app, context or self.a, at=self.clock.now(), **kwargs)

    async def test_candidates_are_current_concrete_private_routes(self):
        result = await self.request("get")
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(len(result["data"]["candidates"]), 2)
        self.assertTrue(result["data"]["supported"]["identity"])
        self.assertIsNone(result["data"]["group"])

    async def test_weighted_random_policy_api_roundtrip(self):
        from companion_v2.relationships.account_links import member_for
        weights = [{"member": member_for(self.a), "weight": 3}, {"member": member_for(self.b), "weight": 1}]
        result = await self.request("save", body=self.command(
            proactive={"mode": "random", "preferred": None, "weights": weights}))
        self.assertEqual(result["status"], "ok", result)
        loaded = await self.request("get")
        policy = loaded["data"]["group"]["proactive"]
        self.assertEqual(policy["mode"], "random")
        self.assertEqual({row["member"]["subject_id"]: row["weight"] for row in policy["weights"]},
                         {self.a.actor.subject.subject_id: 3, self.b.actor.subject.subject_id: 1})

    async def test_weighted_random_policy_api_rejects_forged_route(self):
        from companion_v2.relationships.account_links import member_for
        forged = {**member_for(self.b), "account_id": "another-bot-account"}
        result = await self.request("save", body=self.command(proactive={"mode": "random", "preferred": None,
            "weights": [{"member": member_for(self.a), "weight": 1}, {"member": forged, "weight": 5}]}))
        self.assertEqual(result["status"], "error", result)
        self.assertIn("account_link_weights_invalid", str(result))
        self.assertEqual(self.links.read()["revision"], 0)

    async def test_unauthenticated_caller_is_rejected_before_payload_validation(self):
        result = await self.request("save", body={}, username="")
        self.assertEqual(result["status"], "error")
        self.assertIn("dashboard_identity_required", str(result))

    async def test_client_cannot_add_unknown_account_or_cross_persona(self):
        command = self.command()
        command["members"][1]["route"]["role_id"] = "role-b"
        result = await self.request("save", body=command)
        self.assertEqual(result["status"], "error")
        self.assertEqual(self.links.read()["revision"], 0)

    async def test_only_new_governed_peer_text_is_included_and_routes_are_unchanged(self):
        # First Dashboard login provisions its own operator binding, independently
        # of account linking. Compare after that existing authentication step.
        self.assertEqual((await self.request("get"))["status"], "ok")
        revision = PersistentIdentityDirectory(self.controller.runtime).revision
        await self.link()
        self.assertEqual(PersistentIdentityDirectory(self.controller.runtime).revision, revision)
        self.assertEqual(self.shared(), "")
        self.clock.advance(timedelta(seconds=1))
        prepared = await self.prepare("second", "new-peer", "NEW_SHARED_SECOND")
        self.assertEqual(prepared["context"].scope, self.b.scope)
        self.assertEqual(prepared["native_input"].route.scope, self.b.scope)
        text = self.shared()
        self.assertIn("NEW_SHARED_SECOND", text)
        self.assertNotIn("OLD_PRIVATE_FIRST", text)
        self.assertNotIn("OLD_PRIVATE_SECOND", text)
        self.clock.advance(timedelta(seconds=1))
        reply = await self.prepare("first", "new-origin", "continue from this platform")
        self.assertEqual(reply["context"].scope, self.a.scope)
        self.assertIn("account_links", reply["context_sections"])

    async def test_ambiguous_persona_mapping_disables_context(self):
        await self.link()
        self.clock.advance(timedelta(seconds=1))
        await self.prepare("second", "new-peer", "PEER")
        self.assertIn("PEER", self.shared())
        self.app.private_account_link_persona = lambda member: None
        self.assertEqual(self.shared(), "")

    async def test_disable_reenable_and_unlink_do_not_backfill_history(self):
        await self.link()
        self.clock.advance(timedelta(seconds=1))
        await self.prepare("second", "new-peer", "PEER_BEFORE_DISABLE")
        self.assertIn("PEER_BEFORE_DISABLE", self.shared())
        self.links.configure(self.a, [self.a, self.b], persona_id="original", expected_revision=1,
            share_context=False, operation_key="off", operator_id="admin", at=self.clock.now())
        self.assertEqual(self.shared(), "")
        self.clock.advance(timedelta(seconds=1))
        self.links.configure(self.a, [self.a, self.b], persona_id="original", expected_revision=2,
            share_context=True, operation_key="on", operator_id="admin", at=self.clock.now())
        self.assertEqual(self.shared(), "")
        result = await self.request("unlink", body={**self.person(self.a), "expected_revision": 3, "operation_key": "unlink"})
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(self.shared(), "")
        self.assertIsNone(self.links.group_for(self.b))

    async def test_unconfirmed_output_and_later_sends_are_not_shared(self):
        await self.link()
        self.clock.advance(timedelta(seconds=1))
        prepared = await self.prepare("second", "new-peer", "shared input")
        await self.observer.output(prepared, "UNCONFIRMED_DRAFT", state="output_observed")
        await self.flush()
        self.assertNotIn("UNCONFIRMED_DRAFT", self.shared())
        cutoff = self.clock.now()
        self.clock.advance(timedelta(seconds=2))
        await self.observer.output(prepared, "CONFIRMED_REPLY", state="send_returned")
        await self.flush()
        self.assertIn("CONFIRMED_REPLY", self.shared())
        self.assertNotIn("CONFIRMED_REPLY", self.shared(before=cutoff))

    async def test_revoked_source_is_not_shared(self):
        await self.link()
        self.clock.advance(timedelta(seconds=1))
        prepared = await self.prepare("second", "revoked-peer", "REVOKED_PEER_TEXT")
        self.assertIn("REVOKED_PEER_TEXT", self.shared())
        row = self.controller.runtime.read_one("SELECT source_json FROM conversation_native_observations WHERE event_id = ? AND kind='input'",
            (prepared["event_id"],))
        reference = source(json.loads(row["source_json"]))
        require(self.app.messages.sources.revoke(reference, expected_governance_revision=1, observed_at=self.clock.now()))
        self.assertNotIn("REVOKED_PEER_TEXT", self.shared())

    async def test_peer_dialogue_revocation_does_not_get_bypassed_by_association(self):
        await self.link()
        self.clock.advance(timedelta(seconds=1))
        await self.prepare("second", "peer-to-disable", "PEER_WITH_PERMISSION")
        self.assertIn("PEER_WITH_PERMISSION", self.shared())
        identity = PersistentIdentityDirectory(self.controller.runtime)
        grants = identity.grants_for_subject(self.b.actor.subject)
        for grant in grants:
            if grant.scope == self.b.scope and grant.capability == "conversation.reply" and grant.state.value == "active":
                identity.revoke_grant(grant.grant_id, revoked_at=self.clock.now(), expected_revision=identity.revision)
        self.assertEqual(self.shared(), "")

    async def operator(self):
        from astrbot_host.management_identity import dashboard_operator
        return dashboard_operator(self.controller.runtime, "sharing-test-admin", at=self.clock.now())

    async def test_shared_profile_is_editable_from_either_account_and_locals_survive(self):
        from companion_v2.relationships.profile import target_for, GROUP
        from companion_v2.configuration.owners import SparseConfigurationOwner
        from companion_v2.relationships.account_link_settings import seed_values
        owner = self.app.configuration._owners["relationship.profile.settings"]
        fields = self.app.configuration.registry.group(GROUP)
        a, b = target_for(self.a), target_for(self.b)
        for target, value in ((a, "LOCAL-A"), (b, "LOCAL-B")):
            owner.write(target, fields, changes={GROUP + ".default_nickname": value}, reset=(),
                expected_revision=owner.read(target, fields).revision, observed_at=self.clock.now())
        command = self.command(sharing={"profile": [GROUP + ".default_nickname"], "reply": [], "contact": []}, seed=self.person(self.b))
        result = await self.request("save", body=command)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(owner.read(a, fields).fields[GROUP + ".default_nickname"].value, "LOCAL-B")
        owner.write(a, fields, changes={GROUP + ".default_nickname": "SHARED-EDIT"}, reset=(),
            expected_revision=owner.read(a, fields).revision, observed_at=self.clock.now())
        self.assertEqual(owner.read(b, fields).fields[GROUP + ".default_nickname"].value, "SHARED-EDIT")
        self.assertEqual(owner.read_local(a, fields).fields[GROUP + ".default_nickname"].value, "LOCAL-A")
        # A retry after a shared edit uses the exact HTTP command receipt, not newly derived seed values.
        replay = await self.request("save", body=command)
        self.assertEqual(replay["status"], "ok", replay)
        self.assertTrue(replay["data"]["replayed"])
        self.links.unlink(self.a, expected_revision=self.links.read()["revision"], operation_key="remove-sharing", operator_id="test", at=self.clock.now())
        self.assertEqual(owner.read(a, fields).fields[GROUP + ".default_nickname"].value, "LOCAL-A")
        self.assertEqual(owner.read(b, fields).fields[GROUP + ".default_nickname"].value, "LOCAL-B")

    async def test_shared_reply_edits_invalidate_other_accounts_open_editor(self):
        from companion_v2.conversation.reply_settings import target_for
        from companion_v2.conversation.reply_settings import GROUP, OWNER
        from companion_v2.foundation.refs import OperationRef, VersionRef
        from astrbot_host.management_identity import dashboard_operator
        operator = dashboard_operator(self.controller.runtime, "sharing-admin", at=self.clock.now())
        key = GROUP + ".style_prompt"
        command = self.command(sharing={"profile": [], "reply": [key], "contact": []}, seed=self.person(self.a))
        result = await self.request("save", body=command)
        self.assertEqual(result["status"], "ok", result)
        a, b = target_for(self.a), target_for(self.b)
        old_a = require(self.app.configuration.read(operator, target=a, group_id=GROUP, observed_at=self.clock.now()))
        old_b = require(self.app.configuration.read(operator, target=b, group_id=GROUP, observed_at=self.clock.now()))
        op = OperationRef("share-reply-1", OWNER, a.scope, VersionRef("share-reply-1", 1), "share-reply-1")
        result = self.app.configuration.patch(operator, target=a, group_id=GROUP, operation=op,
            expected_edit_token=old_a.edit_token, changes={key: "SHARED STYLE"}, observed_at=self.clock.now())
        self.assertTrue(result.is_success, result.failure)
        new_b = require(self.app.configuration.read(operator, target=b, group_id=GROUP, observed_at=self.clock.now()))
        self.assertEqual(new_b.values[key], "SHARED STYLE")
        self.assertNotEqual(old_b.edit_token, new_b.edit_token)
        feed = require(self.app.configuration.changes_since(operator, target=b, group_id=GROUP,
            after=old_b.change_sequence, observed_at=self.clock.now()))
        self.assertTrue(feed.sequences)
        runtime_settings = self.app.configuration._owners[OWNER].read(b, self.app.configuration.registry.group(GROUP))
        self.assertEqual(runtime_settings.fields[key].value, "SHARED STYLE")

        op2 = OperationRef("share-reply-2", OWNER, b.scope, VersionRef("share-reply-2", 1), "share-reply-2")
        stale = self.app.configuration.patch(operator, target=b, group_id=GROUP, operation=op2,
            expected_edit_token=old_b.edit_token, changes={key: "STALE"}, observed_at=self.clock.now())
        self.assertEqual(stale.failure.code, "configuration_revision_conflict")

    async def test_linked_history_keeps_original_owners_and_can_show_old_records(self):
        from companion_v2.relationships.account_link_history import history
        await self.link()
        result = history(self.app, self.a, at=self.clock.now())
        self.assertEqual({r["scope"]["platform"] for r in result["records"]}, {self.a.scope.platform, self.b.scope.platform})
        self.assertTrue(any("OLD_PRIVATE_SECOND" in r["text"] for r in result["records"]))
        self.assertEqual(self.shared(), "")
        self.assertTrue(all(r["subject_id"] in (self.a.actor.subject.subject_id, self.b.actor.subject.subject_id) for r in result["records"]))

    async def test_identity_requires_confirmation_and_does_not_mutate_original_grants(self):
        from companion_v2.identity_policy.object_access import ObjectAccessPolicy
        from companion_v2.relationships.account_link_identity import CONFIRMATION
        command = self.command(identity={"enabled": True}, seed=self.person(self.a))
        bad = await self.request("save", body=command)
        self.assertEqual(bad["status"], "error", bad)
        self.assertIn("confirmation_required", str(bad))
        before = PersistentIdentityDirectory(self.controller.runtime).revision
        command["identity"]["confirmation"] = CONFIRMATION
        result = await self.request("save", body=command)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(PersistentIdentityDirectory(self.controller.runtime).revision, before)
        from astrbot_host.management_identity import dashboard_operator
        operator = dashboard_operator(self.controller.runtime, "shared-permission-admin", at=self.clock.now())
        management = self.api.api._management()
        status = require(management.status(operator, self.b.actor.subject.subject_id,
            observed_at=self.clock.now(), route=self.person(self.b)["route"]))
        edit = {**self.person(self.b), "operation_key": "shared-permission-off", "capability": "conversation.reply",
            "enabled": False, "expected_revision": status["policy_revision"]}
        saved = management.configure(operator, edit, observed_at=self.clock.now())
        self.assertTrue(saved.is_success, saved.failure)
        for context in (self.a, self.b):
            denial = self.app.tasks.check_context_access(context, action="conversation.reply", observed_at=self.clock.now())
            self.assertEqual(denial.failure.code, "account_link_permission_disabled")
        self.links.unlink(self.a, expected_revision=self.links.read()["revision"], operation_key="identity-remove", operator_id="test", at=self.clock.now())
        self.assertTrue(self.app.tasks.check_context_access(self.a, action="conversation.reply", observed_at=self.clock.now()).is_success)
        stale = management.configure(operator, edit, observed_at=self.clock.now())
        self.assertFalse(stale.is_success)
