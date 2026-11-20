"""Shared companion stores through authenticated native private turns."""
from tests import astrbot_data_root  # noqa: F401

import asyncio
import json
import unittest

from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA
from companion_v2.execution.models import require
from companion_v2.memory.models import Proposal
from tests.linked_reply_support import LinkedReplyFixture


class LinkedOwnerTests(LinkedReplyFixture, unittest.IsolatedAsyncioTestCase):
    second_channel = "qq-second"  # the channel instance of the joining endpoint

    async def turn(self, index, text, identifier):
        event = self.event_for(index, text, identifier)
        await asyncio.wait_for(await self.dispatch(event), 20)
        await self.controller.session.linked_conversations.gate.drain()
        prepared = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["prepared"]
        await prepared["native_source_future"]
        return prepared

    def learn(self, prepared, operation):
        context = prepared["context"]
        evidence = self.app.memory.writer.access.capture(context, prepared["native_source_future"].result())
        return require(self.app.memory.writer.submit(context, Proposal("add", predicate="drink",
            object="tea", representation="The owner drinks tea", evidence=(evidence,)), operation_key=operation))

    async def test_existing_fact_and_new_sources_are_shared_without_moving_records(self):
        first = await self.turn(0, "I drink tea", "before-link")
        saved = self.learn(first, "learn-before-link")
        self.assertEqual(saved["state"], "persisted")
        separate = await self.turn(1, "A previously separate fact", "separate-before-link")
        separate_fact = self.learn(separate, "learn-separate-before-link")
        self.assertEqual((await self.link())["status"], "ok")
        second = await self.turn(1, "Remember my drink", "after-link")
        context = second["context"]
        from companion_v2.memory.models import AtomRef
        original = require(self.app.memory.writer.read(context, AtomRef(saved["atom_id"], 1)))
        self.assertEqual((original["atom_id"], original["revision"]), (saved["atom_id"], 1))
        excluded = self.app.memory.writer.read(context, AtomRef(separate_fact["atom_id"], 1))
        self.assertEqual(excluded.failure.code, "memory_unavailable")
        self.app.memory.query.begin(context, "shared-facts")
        result = require(self.app.memory.query.query(context, "shared-facts", query_intent="", kind="facts"))
        self.assertIn("The owner drinks tea", str(result))
        source = first["native_source_future"].result()
        self.assertEqual(self.app.memory.writer.access.capture(context, source).source.scope.platform, "qq-main")
        back = await self.turn(0, "What did I ask?", "back")
        other_source = second["native_source_future"].result()
        self.assertEqual(self.app.memory.writer.access.capture(back["context"], other_source).source.scope.platform,
                         self.second_channel)

    async def test_link_does_not_inherit_core_tier_or_bypass_endpoint_action_revocation(self):
        from companion_v2.identity_policy.models import AuthorizationTier
        from companion_v2.identity_policy.object_access import ObjectAccessPolicy
        policy = ObjectAccessPolicy(self.app.runtime)
        first = (await self.turn(0, "Hello", "permission-before"))["context"]
        policy.set_tier(first.actor.subject, AuthorizationTier.CORE,
                        expected_revision=policy.revision, observed_at=self.clock.now())
        self.assertEqual((await self.link())["status"], "ok")
        second = (await self.turn(1, "Hello again", "permission-after"))["context"]
        admin = policy.scope_access(second.actor, scope=second.scope, action="access.policy.set",
                                    observed_at=self.clock.now())
        self.assertFalse(admin.is_success)
        self.assertEqual(admin.failure.code, "core_access_required")
        directory = self.controller.authority.identities
        for grant in directory.grants_for_subject(second.actor.subject):
            if grant.scope == second.scope and grant.capability == "memory.read":
                directory.revoke_grant(grant.grant_id, revoked_at=self.clock.now(),
                                       expected_revision=directory.revision)
        with self.assertRaises(Exception) as denied:
            self.app.memory.query.begin(second, "revoked-read")
        self.assertEqual(denied.exception.code, "authorization_revoked")
        self.app.memory.query.begin(first, "original-still-readable")

    async def attempt(self, index, text, identifier):
        """A turn that may be refused: its prepared context, or None."""
        event = self.event_for(index, text, identifier)
        await asyncio.wait_for(await self.dispatch(event), 20)
        await self.controller.session.linked_conversations.gate.drain()
        return (event.get_extra(CORE_REPLY_CONTEXT_EXTRA) or {}).get("prepared")

    def revoke(self, context, capability):
        directory = self.controller.authority.identities
        for grant in directory.grants_for_subject(context.actor.subject):
            if grant.scope == context.scope and grant.capability == capability:
                directory.revoke_grant(grant.grant_id, revoked_at=self.clock.now(),
                                       expected_revision=directory.revision)

    async def test_a_joined_endpoint_gains_no_tier_from_the_shared_subject(self):
        from astrbot_host.command_authority import is_core_subject
        from companion_v2.capabilities.privacy import is_core
        from companion_v2.identity_policy.interventions import INTERVENTION_CAPABILITIES, authorized_capabilities
        from companion_v2.identity_policy.models import AuthorizationTier
        from companion_v2.identity_policy.object_access import ObjectAccessPolicy
        policy = ObjectAccessPolicy(self.app.runtime)
        first = (await self.turn(0, "Hello", "tier-before"))["context"]
        policy.set_tier(first.actor.subject, AuthorizationTier.CORE,
                        expected_revision=policy.revision, observed_at=self.clock.now())
        self.assertEqual((await self.link())["status"], "ok")
        first = (await self.turn(0, "Hello", "tier-owner"))["context"]
        second = (await self.turn(1, "Hello again", "tier-joined"))["context"]
        self.assertEqual(second.actor.subject, first.actor.subject)
        at = self.clock.now()
        self.assertEqual(authorized_capabilities(self.app.tasks, first, at=at), frozenset(INTERVENTION_CAPABILITIES))
        self.assertEqual(authorized_capabilities(self.app.tasks, second, at=at), frozenset())
        self.assertEqual((is_core(self.app.runtime, first.actor), is_core(self.app.runtime, second.actor)), (True, False))
        self.assertEqual((is_core_subject(self.app.runtime, first.actor),
                          is_core_subject(self.app.runtime, second.actor)), (True, False))
        session = self.controller.session
        self.assertEqual((session._actor_is_core(first), session._actor_is_core(second)), (True, False))

    async def test_a_joined_endpoint_gains_no_device_authority_from_the_shared_subject(self):
        from datetime import timedelta
        from types import SimpleNamespace
        from companion_v2.identity_policy.models import AuthorizationTier
        from companion_v2.identity_policy.object_access import ObjectAccessPolicy
        from companion_v2.product import PRODUCT_VERSION
        from companion_v2.reality import provider as reality
        from tests.native_internal_support import internal_registry
        from tests.test_mihome_control_profiles import AC_DID, api_for
        policy = ObjectAccessPolicy(self.app.runtime)
        first = (await self.turn(0, "Hello", "device-before"))["context"]
        policy.set_tier(first.actor.subject, AuthorizationTier.CORE,
                        expected_revision=policy.revision, observed_at=self.clock.now())
        self.assertEqual((await self.link())["status"], "ok")
        first = (await self.turn(0, "Hello", "device-owner"))["context"]
        second = (await self.turn(1, "Hello again", "device-joined"))["context"]
        self.assertEqual(second.actor.subject, first.actor.subject)
        api = api_for(admin_only=True)
        api.plugin.controller = SimpleNamespace(runtime=self.app.runtime)
        api.plugin.mihome_web_api = api
        powered = []

        async def control_power(did, is_on, alias):
            powered.append(did)
            return True
        api.plugin.client.control_power = control_power
        registry = internal_registry(reality.NativeRealityProvider(
            api.plugin, self.root / "reality", version=PRODUCT_VERSION), now=self.clock.now)
        self.addAsyncCleanup(registry.aclose)
        declaration = next(item for item in registry.declarations()
                           if item.descriptor.tool.action == "reality.device.control")

        async def power(context, identifier):
            from companion_v2.execution.models import digest
            arguments = {"alias": "客厅空调", "is_on": True}
            return await registry.invoke(declaration.host_tool_id, SimpleNamespace(
                invocation_id=identifier, parent_task_id="task-" + identifier, tool_call_id="call-" + identifier,
                context=context, descriptor=declaration.descriptor, arguments_json=json.dumps(arguments),
                arguments_hash=digest(arguments), deadline_at=self.clock.now() + timedelta(minutes=1),
                before_dispatch=lambda: None, task_run=None))

        refused = await power(second, "joined-power")
        self.assertEqual((refused.state, refused.effect), ("failed", "not_committed"))
        self.assertEqual(powered, [])
        allowed = await power(first, "owner-power")
        self.assertEqual((allowed.state, allowed.effect), ("succeeded", "committed"))
        self.assertEqual(powered, [AC_DID])

    async def test_a_joined_endpoint_is_fenced_to_the_scopes_of_its_link(self):
        from companion_v2.conversation.reply_settings import GROUP
        from companion_v2.identity_policy.object_access import ObjectAccessPolicy
        await self.db.create_conversation(self.origins[0], "qq-main", cid="conversation-c", persona_id="original",
                                          content=[])
        await self.manager.switch_conversation(self.origins[0], "conversation-c")
        unrelated = (await self.turn(0, "Another conversation of the owner", "fence-unrelated"))["context"]
        await self.manager.switch_conversation(self.origins[0], "conversation-a")
        self.assertEqual((await self.link())["status"], "ok")
        first = (await self.turn(0, "The linked conversation", "fence-owner"))["context"]
        second = (await self.turn(1, "The joined endpoint", "fence-joined"))["context"]
        self.assertNotEqual(unrelated.scope.conversation_id, first.scope.conversation_id)
        policy, at = ObjectAccessPolicy(self.app.runtime), self.clock.now()
        self.assertTrue(policy.scope_access(first.actor, scope=unrelated.scope, action="memory.read",
                                            observed_at=at, allow_private_owner=True).is_success)
        for scope in (first.scope, second.scope):
            self.assertTrue(policy.scope_access(second.actor, scope=scope, action="memory.read",
                                                observed_at=at, allow_private_owner=True).is_success)
        refused = policy.scope_access(second.actor, scope=unrelated.scope, action="memory.read",
                                      observed_at=at, allow_private_owner=True)
        self.assertEqual(refused.failure and refused.failure.code, "scope_not_granted")
        settings = self.app.configuration.read(second.actor, target=self.window(unrelated), group_id=GROUP,
                                               observed_at=at)
        self.assertEqual(settings.failure and settings.failure.code, "scope_not_granted")
        # The grant check itself holds the same line, for readers that ask it directly.
        directory = self.controller.authority.identities
        direct = directory.authorize(second.actor, scope=unrelated.scope, capability="memory.read", observed_at=at)
        self.assertEqual(direct.failure and direct.failure.code, "scope_not_granted")
        self.revoke(second, "memory.read")
        for scope in (first.scope, second.scope):
            own = directory.authorize(second.actor, scope=scope, capability="memory.read", observed_at=at)
            self.assertEqual(own.failure and own.failure.code, "authorization_revoked")
        self.assertTrue(directory.authorize(first.actor, scope=second.scope, capability="memory.read",
                                            observed_at=at).is_success)

    async def test_an_account_link_identity_of_the_joined_endpoint_lends_the_anchor_nothing_on_its_records(self):
        from dataclasses import replace
        from types import SimpleNamespace as NS
        from companion_v2.identity_policy.management import PAGE_MANAGED_CAPABILITIES
        from companion_v2.identity_policy.object_access import ObjectAccessPolicy
        from companion_v2.relationships.account_links import PrivateAccountLinks
        self.assertEqual((await self.link())["status"], "ok")
        first = (await self.turn(0, "The linked conversation", "shared-identity-owner"))["context"]
        second = (await self.turn(1, "The joined endpoint", "shared-identity-joined"))["context"]
        # A CORE account-link identity saved for the joined endpoint and a third account only.
        third = NS(scope=replace(second.scope, platform="qq-third", bot_id="102"), actor=second.actor,
                   role_binding=second.role_binding, audience=NS(recipients=(second.actor.subject,)))
        runtime = self.app.runtime
        previous = getattr(runtime, "private_account_link_persona", None)
        runtime.private_account_link_persona = lambda member: "original"
        self.addCleanup(setattr, runtime, "private_account_link_persona", previous)
        PrivateAccountLinks(runtime).configure(second, [second, third], persona_id="original", expected_revision=0,
            share_context=False, operation_key="identity-for-joined", operator_id="owner", at=self.clock.now(),
            identity={"enabled": True, "tier": "core",
                      "permissions": {capability: True for capability in PAGE_MANAGED_CAPABILITIES}})
        policy, at = ObjectAccessPolicy(runtime), self.clock.now()
        on_joined_record = policy.feature_access(first.actor, scope=second.scope, action="access.policy.set",
                                                 observed_at=at)
        self.assertEqual(on_joined_record.failure and on_joined_record.failure.code, "core_access_required")
        self.revoke(first, "memory.read")
        own_grant = policy.feature_access(first.actor, scope=second.scope, action="memory.read", observed_at=at)
        self.assertEqual(own_grant.failure and own_grant.failure.code, "authorization_revoked")

    async def associate(self, first, second):
        """Save an upstream account-link group of both endpoints through its Dashboard API."""
        from unittest.mock import patch
        from types import SimpleNamespace as NS
        from astrbot_host.acquaintance_api import AcquaintanceWebAPI
        from astrbot_host.account_link_api import AccountLinkWebAPI
        from companion_v2.relationships.account_links import PrivateAccountLinks
        from tests.test_person_authorization import _Query, _body

        def person(context):
            return {"subject_id": context.actor.subject.subject_id,
                    "route": {"platform": context.scope.platform, "bot_id": context.scope.bot_id,
                              "account_id": context.scope.account_id, "role_id": context.role_binding.role.subject_id}}
        body = {**person(first), "members": [person(first), person(second)], "share_context": False,
                "expected_revision": PrivateAccountLinks(self.controller.runtime).read()["revision"],
                "operation_key": "associate"}
        with patch("astrbot_host.acquaintance_api.request", NS(username="link-admin", query=_Query(""),
                   body=_body(json.dumps(body).encode()))):
            return await AccountLinkWebAPI(AcquaintanceWebAPI(self.controller)).save()

    async def test_an_endpoint_of_a_linked_conversation_cannot_join_an_account_link(self):
        first = (await self.turn(0, "Hello", "exclusive-owner"))["context"]
        second = (await self.turn(1, "Hello", "exclusive-other"))["context"]
        self.assertEqual((await self.link())["status"], "ok")
        # The joined endpoint is checked first: it, not the anchor, trips the refusal.
        refused = await self.associate(second, first)
        self.assertEqual((refused["status"], refused["data"].get("code")),
                         ("error", "account_link_linked_conversation_conflict"))

    async def test_an_endpoint_of_an_account_link_cannot_join_a_linked_conversation(self):
        first = (await self.turn(0, "Hello", "exclusive-owner"))["context"]
        second = (await self.turn(1, "Hello", "exclusive-other"))["context"]
        associated = await self.associate(first, second)
        self.assertEqual(associated["status"], "ok", associated)
        refused = await self.link()
        self.assertEqual((refused["status"], refused["data"].get("code")),
                         ("error", "conversation_link_account_link_conflict"))
        self.assertIsNone(self.controller.session.linked_conversations.get("owner-dialogue"))

    async def test_an_account_link_saved_while_a_link_edit_waits_leaves_exactly_one_of_them_saved(self):
        first = (await self.turn(0, "Hello", "race-owner"))["context"]
        second = (await self.turn(1, "Hello", "race-other"))["context"]
        manager = self.controller.session.linked_conversations
        original, saved = manager.settings, []

        async def settings(*args):
            # The association lands while the link edit waits on its own checks.
            if not saved:
                saved.append(await self.associate(first, second))
            return await original(*args)
        manager.settings = settings
        self.addCleanup(setattr, manager, "settings", original)
        linked = await self.link()
        self.assertEqual(saved[0]["status"], "ok", saved[0])
        self.assertEqual((linked["status"], linked["data"].get("code")),
                         ("error", "conversation_link_account_link_conflict"))
        self.assertIsNone(manager.get("owner-dialogue"))

    async def test_a_linked_scope_of_another_installation_has_no_account_on_this_bridge(self):
        from dataclasses import replace
        self.assertEqual((await self.link())["status"], "ok")
        second = (await self.turn(1, "The joined endpoint", "installation-joined"))["context"]
        bridge = self.controller.session.bridge
        self.assertEqual(bridge.account(second.scope).platform_id, second.scope.platform)
        with self.assertRaises(Exception) as refused:
            bridge.account(replace(second.scope, installation_id="another-installation"))
        self.assertEqual(getattr(refused.exception, "code", None), "host_account_changed")

    async def test_a_refusal_made_before_linking_still_binds_the_endpoint(self):
        second = (await self.turn(1, "An independent endpoint", "refusal-before"))["context"]
        self.revoke(second, "memory.read")
        self.assertFalse(await self.attempt(1, "Refused while independent", "refusal-independent"))
        self.assertEqual((await self.link())["status"], "ok")
        self.assertFalse(await self.attempt(1, "Refused after linking", "refusal-linked"))
        self.assertTrue(await self.attempt(0, "The other endpoint is unaffected", "refusal-other"))

    def allowed(self, context, capability):
        directory = self.controller.authority.identities
        actor = require(directory.resolve_authenticated(context.actor.principal, observed_at=self.clock.now()))
        return directory.authorize(actor, scope=context.scope, capability=capability,
                                   observed_at=self.clock.now()).is_success

    def decisions(self, context):
        """Every capability this endpoint has a grant row for, with today's answer."""
        directory = self.controller.authority.identities
        return {grant.capability: self.allowed(context, grant.capability)
                for grant in directory.grants_for_subject(context.actor.subject) if grant.scope == context.scope}

    def save(self, owner, fields, context, **changes):
        target = self.window(context)
        owner.write(target, fields, changes=changes, reset=(),
            expected_revision=owner.read(target, fields).revision, observed_at=self.clock.now())

    def growth(self, context, **values):
        from companion_v2.role_work.growth.configuration import GrowthConfigurationOwner, configuration_fields
        self.save(GrowthConfigurationOwner(self.app.runtime), configuration_fields(), context,
                  **{"role.growth." + name: value for name, value in values.items()})

    GROWTH = ("role.growth.read", "role.growth.propose")

    async def test_linking_to_an_oppositely_configured_anchor_flips_no_growth_permission(self):
        first = (await self.turn(0, "The anchor reads growth", "growth-flip-a"))["context"]
        second = (await self.turn(1, "The other endpoint proposes", "growth-flip-b"))["context"]
        self.growth(first, read_enabled=True, proposals_enabled=False)
        self.growth(second, read_enabled=False, proposals_enabled=True)
        before = [{action: self.allowed(context, action) for action in self.GROWTH} for context in (first, second)]
        self.assertEqual(before, [dict(zip(self.GROWTH, (True, False))), dict(zip(self.GROWTH, (False, True)))])
        self.assertEqual((await self.link())["status"], "ok")
        for attempt in range(2):  # the answer holds on the joining turn and on every later one
            first = (await self.turn(0, "After linking", "growth-flip-a-linked-%d" % attempt))["context"]
            second = (await self.turn(1, "After linking", "growth-flip-b-linked-%d" % attempt))["context"]
            self.assertEqual([{action: self.allowed(context, action) for action in self.GROWTH}
                              for context in (first, second)], before)

    async def test_a_revoked_state_permission_stays_revoked_under_an_anchor_that_enables_state(self):
        from companion_v2.role_work.state.configuration import StateConfigurationOwner, configuration_fields
        first = (await self.turn(0, "The anchor keeps state", "state-revoked-a"))["context"]
        second = (await self.turn(1, "The other endpoint too", "state-revoked-b"))["context"]
        owner, fields = StateConfigurationOwner(self.app.runtime), configuration_fields()
        for index, context in enumerate((first, second)):
            role = owner.role_target(self.window(context))
            owner.write(role, fields, changes={"role.state.enabled": True}, reset=(),
                expected_revision=owner.read(role, fields).revision, observed_at=self.clock.now())
            await self.turn(index, "State is kept now", "state-enabled-%d" % index)
        self.assertTrue(self.allowed(second, "role.state.read"))
        self.revoke(second, "role.state.read")
        self.assertEqual((await self.link())["status"], "ok")
        for attempt in range(2):
            second = (await self.turn(1, "After linking", "state-revoked-b-linked-%d" % attempt))["context"]
            self.assertFalse(self.allowed(second, "role.state.read"))
            self.assertTrue(self.allowed(second, "role.state.maintain"))
        self.assertTrue(self.allowed((await self.turn(0, "The anchor", "state-revoked-a-linked"))["context"],
                                     "role.state.read"))

    async def test_every_decision_of_an_endpoint_survives_linking_including_an_explicit_grant(self):
        from companion_v2.identity_policy.models import AuthorizationTier
        first = (await self.turn(0, "The anchor", "decisions-a"))["context"]
        second = (await self.turn(1, "An independent endpoint", "decisions-b"))["context"]
        directory = self.controller.authority.identities
        directory.issue_grant(grant_id="operator-grant-rest-wake", subject=second.actor.subject, scope=second.scope,
            capability="role.rest.wake", tier=AuthorizationTier.STANDARD, valid_from=self.clock.now(),
            expected_revision=directory.revision)
        self.revoke(second, "proactive.contact")
        owners, before = self.decisions(first), self.decisions(second)
        self.assertTrue(before["role.rest.wake"])
        self.assertEqual((await self.link())["status"], "ok")
        first = (await self.turn(0, "After linking", "decisions-a-linked"))["context"]
        second = (await self.turn(1, "After linking", "decisions-b-linked"))["context"]
        self.assertEqual(self.decisions(first), owners)
        self.assertEqual(self.decisions(second), {**self.decisions(second), **before})
        self.assertEqual({capability for capability, allowed in self.decisions(second).items() if allowed},
                         {capability for capability, allowed in before.items() if allowed})
        self.assertFalse(self.allowed(first, "role.rest.wake"))

    async def test_a_withdrawn_image_consent_is_not_issued_again_by_linking(self):
        from astrbot_host.image_access import IMAGE_EXTENSION
        from companion_v2.capabilities.models import CapabilityAuthorizationState
        from companion_v2.capabilities.repositories import CapabilityRepository
        repository = CapabilityRepository(self.app.runtime)

        def consent(context):
            return repository.authorize(extension_id=IMAGE_EXTENSION, capability_id="image.generate",
                                        subject=context.actor.subject, scope=context.scope).is_success
        second = (await self.turn(1, "An independent endpoint", "image-before"))["context"]
        self.assertTrue(consent(second))
        current = require(repository.consent(extension_id=IMAGE_EXTENSION, capability_id="image.generate",
                                             subject=second.actor.subject, scope=second.scope))
        require(repository.set_authorization(extension_id=IMAGE_EXTENSION, capability_id="image.generate",
            subject=second.actor.subject, scope=second.scope, state=CapabilityAuthorizationState.REVOKED,
            expected_revision=current.revision, observed_at=self.clock.now()))
        self.assertEqual((await self.link())["status"], "ok")
        first = (await self.turn(0, "The anchor", "image-anchor"))["context"]
        second = (await self.turn(1, "After linking", "image-after"))["context"]
        self.assertEqual((consent(first), consent(second)), (True, False))

    async def test_a_removed_endpoint_has_no_authority_and_a_rejoin_brings_back_nothing_it_lacks_on_its_own(self):
        read, directory = "role.growth.read", self.controller.authority.identities
        self.assertEqual((await self.link())["status"], "ok")
        first = (await self.turn(0, "The anchor", "rejoin-a"))["context"]
        linked = (await self.turn(1, "A member", "rejoin-b"))["context"]
        self.growth(linked, read_enabled=True)
        self.assertTrue(self.allowed(linked, read))
        # Removed: its grant rows remain, and decide nothing.
        self.assertEqual((await self.link(revision=1, members=["first"]))["status"], "ok")
        self.assertTrue(any(grant.scope == linked.scope and grant.capability == read
                            for grant in directory.grants_for_subject(linked.actor.subject)))
        refused = directory.authorize(linked.actor, scope=linked.scope, capability=read, observed_at=self.clock.now())
        self.assertEqual(refused.failure and refused.failure.code, "scope_not_granted")
        # Admitted on its own: a conversation of its own, decided by its own settings and grants.
        self.assertEqual((await self.link(revision=2, members=["first"],
                                          independent_target_ids=["second"]))["status"], "ok")
        own = (await self.turn(1, "On my own", "rejoin-own"))["context"]
        self.assertNotEqual(own.scope, linked.scope)
        self.assertNotEqual(own.actor.principal.host_id, linked.actor.principal.host_id)
        self.assertFalse(self.allowed(own, read))
        self.assertTrue(self.allowed(own, "memory.read"))
        # Linked again: what it had under the earlier membership and lacks on its own stays refused.
        self.assertEqual((await self.link(revision=3))["status"], "ok")
        again = (await self.turn(1, "A member again", "rejoin-again"))["context"]
        self.assertEqual(again.scope, linked.scope)
        self.assertEqual((self.allowed(again, read), self.allowed(again, "memory.read")), (False, True))
        self.assertTrue(self.allowed((await self.turn(0, "The anchor", "rejoin-a-again"))["context"], "memory.read"))

    async def rejoined_after_a_refusal_made_on_its_own(self, refuse, held=lambda context: None):
        """Member, removed, admitted on its own, refuses something there, linked again."""
        self.assertEqual((await self.link())["status"], "ok")
        linked = (await self.turn(1, "A member", "refused-own-member"))["context"]
        held(linked)
        self.assertEqual((await self.link(revision=1, members=["first"]))["status"], "ok")
        self.assertEqual((await self.link(revision=2, members=["first"],
                                          independent_target_ids=["second"]))["status"], "ok")
        own = (await self.turn(1, "On my own", "refused-own-independent"))["context"]
        self.assertNotEqual(own.scope, linked.scope)
        refuse(own)
        self.assertEqual((await self.link(revision=3))["status"], "ok")
        again = (await self.turn(1, "A member again", "refused-own-again"))["context"]
        self.assertEqual(again.scope, linked.scope)
        return again

    async def test_a_consent_withdrawn_on_its_own_is_not_restored_by_rejoining(self):
        from astrbot_host.image_access import IMAGE_EXTENSION
        from companion_v2.capabilities.models import CapabilityAuthorizationState
        from companion_v2.capabilities.repositories import CapabilityRepository
        repository = CapabilityRepository(self.app.runtime)

        def consent(context):
            return repository.authorize(extension_id=IMAGE_EXTENSION, capability_id="image.generate",
                                        subject=context.actor.subject, scope=context.scope).is_success

        def withdraw(context):
            self.assertTrue(consent(context))
            current = require(repository.consent(extension_id=IMAGE_EXTENSION, capability_id="image.generate",
                                                 subject=context.actor.subject, scope=context.scope))
            require(repository.set_authorization(extension_id=IMAGE_EXTENSION, capability_id="image.generate",
                subject=context.actor.subject, scope=context.scope, state=CapabilityAuthorizationState.REVOKED,
                expected_revision=current.revision, observed_at=self.clock.now()))
        again = await self.rejoined_after_a_refusal_made_on_its_own(withdraw)
        self.assertFalse(consent(again))
        self.assertFalse(consent((await self.turn(1, "And on the next turn", "refused-own-later"))["context"]))
        self.assertTrue(consent((await self.turn(0, "The anchor", "refused-own-anchor"))["context"]))

    async def test_a_grant_revoked_on_its_own_is_not_restored_by_rejoining(self):
        from companion_v2.identity_policy.models import AuthorizationTier
        directory = self.controller.authority.identities

        def consent(context):
            directory.issue_grant(grant_id="consent-" + context.key, subject=context.actor.subject,
                scope=context.scope, capability="proactive.contact", tier=AuthorizationTier.STANDARD,
                valid_from=self.clock.now(), expected_revision=directory.revision)
            self.assertTrue(self.allowed(context, "proactive.contact"))

        def refuse(context):
            consent(context)
            self.revoke(context, "proactive.contact")
        again = await self.rejoined_after_a_refusal_made_on_its_own(refuse, held=consent)
        self.assertEqual((self.allowed(again, "proactive.contact"), self.allowed(again, "conversation.reply")),
                         (False, True))
        later = (await self.turn(1, "And on the next turn", "refused-own-later"))["context"]
        self.assertFalse(self.allowed(later, "proactive.contact"))

    async def test_a_later_shared_setting_change_moves_only_the_grants_of_the_endpoint_that_made_it(self):
        self.assertEqual((await self.link())["status"], "ok")
        first = (await self.turn(0, "Growth preferences", "later-setting-a"))["context"]
        second = (await self.turn(1, "Growth preferences again", "later-setting-b"))["context"]
        read = "role.growth.read"
        self.assertEqual((self.allowed(first, read), self.allowed(second, read)), (False, False))
        for value, expected in ((True, (False, True)), (False, (False, False))):
            self.growth(second, read_enabled=value)
            for attempt in range(2):  # at once, and after each endpoint has been activated again
                self.assertEqual((self.allowed(first, read), self.allowed(second, read)), expected)
                first = (await self.turn(0, "Later", "later-setting-a-%s-%d" % (value, attempt)))["context"]
                second = (await self.turn(1, "Later", "later-setting-b-%s-%d" % (value, attempt)))["context"]
        self.growth(first, read_enabled=True)
        self.growth(second, read_enabled=True)
        self.growth(first, read_enabled=False)
        for attempt in range(2):
            self.assertEqual((self.allowed(first, read), self.allowed(second, read)), (False, True))
            first = (await self.turn(0, "Later still", "later-setting-a-off-%d" % attempt))["context"]
            second = (await self.turn(1, "Later still", "later-setting-b-off-%d" % attempt))["context"]

    async def test_reading_the_state_page_of_a_peer_changes_no_grant_after_a_shared_setting_was_saved(self):
        from astrbot_host.management_identity import dashboard_operator
        from companion_v2.role_work.state.management import StateManagement
        self.assertEqual((await self.link())["status"], "ok")
        first = (await self.turn(0, "State preferences", "state-read-a"))["context"]
        second = (await self.turn(1, "State preferences again", "state-read-b"))["context"]
        operator = dashboard_operator(self.app.runtime, "synthetic-admin", at=self.clock.now())
        watched = ("role.state.read", "role.state.maintain", "platform.typing")

        def held():
            return [{action: self.allowed(context, action) for action in watched} for context in (first, second)]
        nothing = dict.fromkeys(watched, False)
        self.assertEqual(held(), [nothing, nothing])
        page = StateManagement(self.app, first)
        before = require(page.status(operator))
        require(page.command(operator, dict(action="configure", operation_key="shared-state-on",
            edit_token=before["edit_token"], role_edit_token=before["role_edit_token"],
            changes={"enabled": True, "typing_enabled": True})))
        saved = [dict.fromkeys(watched, True), nothing]
        self.assertEqual(held(), saved)
        # The peer's page shows the shared setting and is only a read.
        shown = require(StateManagement(self.app, second).status(operator))
        self.assertEqual((shown["settings"]["enabled"], shown["settings"]["typing_enabled"]), (True, True))
        self.assertEqual(held(), saved)
        for index in (0, 1):
            await self.turn(index, "Later", "state-read-later-%d" % index)
        self.assertEqual(held(), saved)
        # Nor does a read while the peer is removed leave it anything to come back to.
        self.assertEqual((await self.link(revision=1, members=["first"]))["status"], "ok")
        StateManagement(self.app, second).status(operator)
        self.assertEqual((await self.link(revision=2))["status"], "ok")
        second = (await self.turn(1, "A member again", "state-read-rejoined"))["context"]
        self.assertEqual(held(), saved)

    async def test_a_joined_endpoint_keeps_owner_features_on_its_own_window_only(self):
        from companion_v2.identity_policy.object_access import ObjectAccessPolicy
        policy = ObjectAccessPolicy(self.app.runtime)
        actions = ("relationship.memory.read", "calendar.read", "calendar.write", "memo.read", "memo.list",
                   "role.self_review.read", "role.expression.read")
        from dataclasses import replace
        def owned(context, scope):
            return {action for action in actions if policy.feature_access(context.actor, scope=scope, action=action,
                observed_at=self.clock.now(), allow_private_owner=True).is_success}
        independent = (await self.turn(1, "Before linking", "owner-feature-before"))["context"]
        before = owned(independent, independent.scope)
        self.assertEqual(before, set(actions))
        self.assertEqual((await self.link())["status"], "ok")
        first = (await self.turn(0, "The owner", "owner-feature-owner"))["context"]
        second = (await self.turn(1, "After linking", "owner-feature-joined"))["context"]
        for context in (first, second):
            for scope in (first.scope, second.scope):
                self.assertEqual(owned(context, scope), before)
        outside = replace(second.scope, conversation_id="astrbot-conversation:another")
        self.assertEqual(owned(second, outside), set())

    async def test_pre_send_recheck_refuses_a_joined_endpoint_that_lost_dialogue_permission(self):
        self.assertEqual((await self.link(source_target_id="second", conversation_id="conversation-b"))["status"], "ok")
        self.pause_model = True
        event = self.event_for(0, "Still allowed?", "presend-joined")
        task = await self.dispatch(event)
        await asyncio.wait_for(self.model_done.wait(), 20)
        prepared = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["prepared"]
        self.assertEqual(prepared["context"].actor.principal.host_id, "astrbot-native-linked-private")
        session, target = self.controller.session, self.controller.target_for_event(event)
        self.assertTrue((await session.authorize_core_reply(event, target, prepared)).is_success)
        self.revoke(prepared["context"], "conversation.reply")
        refused = await session.authorize_core_reply(event, target, prepared)
        self.assertEqual(refused.failure and refused.failure.code, "private_access_dialogue_denied")
        self.allow_model.set()
        await asyncio.wait_for(task, 20)
        await session.linked_conversations.gate.drain()

    async def test_a_malformed_link_record_closes_its_member_and_leaves_an_unlinked_chat_working(self):
        from astrbot_host.linked_conversations import KEY
        self.assertEqual((await self.link(members=["first"]))["status"], "ok")
        self.assertTrue(await self.attempt(0, "The linked member", "malformed-before-a"))
        self.assertTrue(await self.attempt(1, "A chat in no link", "malformed-before-b"))
        with self.app.runtime.transaction() as connection:
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ?", ("{not json", KEY))
        self.assertTrue(await self.attempt(1, "Still a chat in no link", "malformed-unlinked"))
        self.assertFalse(await self.attempt(0, "The member stays closed", "malformed-member"))

    async def test_companion_body_and_maintenance_owner_already_agree(self):
        from astrbot_host.management_identity import dashboard_operator
        from companion_v2.role_work.state.ownership import role_schedule_key
        from tests.test_role_state import state_data
        from companion_v2.storage.repositories import OperationRepository
        operations = OperationRepository(self.app.runtime)
        first = await self.turn(0, "I feel rested", "body-before")
        operator = dashboard_operator(self.app.runtime, "synthetic-admin", at=self.clock.now())
        saved = self.app.role_state.write(first["context"], data=state_data(energy=54), expected_revision=0,
            operation_key="body-before-link", sources=(first["native_source_future"].result(),),
            operator=operator, reason="human_edit")
        self.assertEqual((await self.link())["status"], "ok")
        second = await self.turn(1, "How rested?", "body-after")
        body = self.app.role_state.read(second["context"], operator=operator)
        self.assertEqual(body["values"]["energy"], 54)
        self.assertEqual(require(operations.get(saved["operation_id"])).operation.scope.platform,
                         "qq-main")
        self.assertEqual(role_schedule_key(first["context"]), role_schedule_key(second["context"]))
        saved = self.app.role_state.write(second["context"], data=state_data(energy=61), expected_revision=1,
            operation_key="body-from-second", sources=(second["native_source_future"].result(),),
            operator=operator, reason="human_edit")
        updated = self.app.role_state.read(first["context"], operator=operator)
        self.assertEqual(updated["values"]["energy"], 61)
        self.assertEqual(require(operations.get(saved["operation_id"])).operation.scope.platform,
                         self.second_channel)

    def grant(self, context, action):
        from companion_v2.identity_policy.models import AuthorizationTier
        directory = self.controller.authority.identities
        directory.issue_grant(grant_id="synthetic:" + context.key + ":" + action,
            subject=context.actor.subject, scope=context.scope, capability=action,
            tier=AuthorizationTier.STANDARD, valid_from=self.clock.now(), expected_revision=directory.revision)

    async def test_relationship_memory_already_agrees_and_retains_source_scope(self):
        from companion_v2.foundation.refs import RoleOwnerRef
        from companion_v2.relationships import RelationshipMemory, RelationshipMemoryKind
        first = await self.turn(0, "We enjoy walking together", "relationship-before")
        context = first["context"]
        self.grant(context, "relationship.memory.propose")
        service = self.app.relationship_memories
        original = require(service.propose(RelationshipMemory("shared-relationship",
            RoleOwnerRef(context.scope.installation_id, context.scope.bot_id, context.role_binding.role.subject_id),
            context.actor.subject, context.scope, RelationshipMemoryKind.CONTEXT,
            "We enjoy walking together", (first["native_source_future"].result(),), self.clock.now(), self.clock.now()),
            actor=context.actor, observed_at=self.clock.now()))
        self.assertEqual((await self.link())["status"], "ok")
        second = await self.turn(1, "Our walks", "relationship-after")
        shared = require(service.read("shared-relationship", actor=second["context"].actor,
            scope=second["context"].scope, observed_at=self.clock.now()))
        self.assertEqual(shared, original)
        self.assertEqual(shared.sources[0].scope.platform, "qq-main")

    @staticmethod
    def sections(prepared):
        return {item["purpose"]: item["state"] for item in prepared["context_materials"]}

    async def test_reply_background_of_a_joined_endpoint_has_the_owner_sections_and_the_shared_relationship(self):
        from companion_v2.foundation.refs import RoleOwnerRef
        from companion_v2.relationships import RelationshipMemory, RelationshipMemoryKind, RelationshipMemoryState
        first = await self.turn(0, "We enjoy walking together", "background-before")
        context, service = first["context"], self.app.relationship_memories
        for action in ("propose", "review", "adopt"):
            self.grant(context, "relationship.memory." + action)
        memory = require(service.propose(RelationshipMemory("adopted-relationship",
            RoleOwnerRef(context.scope.installation_id, context.scope.bot_id, context.role_binding.role.subject_id),
            context.actor.subject, context.scope, RelationshipMemoryKind.CONTEXT,
            "We enjoy walking together", (first["native_source_future"].result(),), self.clock.now(), self.clock.now()),
            actor=context.actor, observed_at=self.clock.now()))
        for state in (RelationshipMemoryState.REVIEWED, RelationshipMemoryState.ADOPTED):
            memory = require(service.transition(memory.memory_id, actor=context.actor, scope=context.scope,
                expected_revision=memory.revision, operation_id="adopted-relationship:" + state.value, state=state,
                observed_at=self.clock.now()))
        self.assertEqual((await self.link())["status"], "ok")
        owner = await self.turn(0, "Our walks", "background-owner")
        joined = await self.turn(1, "Our walks again", "background-joined")
        for purpose in ("relationship", "open_items"):
            self.assertNotEqual(self.sections(owner)[purpose], "unavailable")
            self.assertEqual(self.sections(joined)[purpose], self.sections(owner)[purpose])
        self.assertEqual(self.sections(joined)["relationship"], "ready")
        reader = self.app.messages.relationship_context
        snapshot = reader.capture(joined["context"], self.clock.now())
        self.assertEqual([item["memory_id"] for item in snapshot["references"]], ["adopted-relationship"])
        self.assertIn("We enjoy walking together",
                      str(reader.contributions(snapshot, joined["context"], self.clock.now())))

    async def test_window_settings_share_values_but_keep_physical_authorization_scope(self):
        from companion_v2.conversation.reply_settings import ReplyConfigurationOwner, configuration_fields, target_for
        first = await self.turn(0, "Preferences", "settings-before")
        owner = ReplyConfigurationOwner(self.app.runtime)
        common = self.controller.settings.common
        fields = configuration_fields(common.segmentation, common.streaming, common.policy.part_gap_seconds)
        first_target = target_for(first["context"])
        self.assertEqual((await self.link())["status"], "ok")
        second = await self.turn(1, "Same preferences", "settings-after")
        second_target = target_for(second["context"])
        field = "conversation.presentation.private_debounce_enabled"
        self.assertFalse(owner.read(second_target, fields).fields[field].value)
        self.assertEqual(second_target.authorization_scope.platform, self.second_channel)
        owner.write(second_target, fields, changes={field: True}, reset=(),
            expected_revision=owner.read(second_target, fields).revision, observed_at=self.clock.now())
        self.assertTrue(owner.read(first_target, fields).fields[field].value)

    async def test_scope_based_settings_read_the_linked_window_owner(self):
        from companion_v2.conversation.reply_settings import ReplyConfigurationOwner, configuration_fields, target_for
        from companion_v2.conversation.media.understanding import intake_settings
        self.assertEqual((await self.link())["status"], "ok")
        first = (await self.turn(0, "Shared intake preferences", "intake-settings-a"))["context"]
        second = (await self.turn(1, "Read intake preferences", "intake-settings-b"))["context"]
        common = self.controller.settings.common
        fields = configuration_fields(common.segmentation, common.streaming, common.policy.part_gap_seconds)
        owner, target = ReplyConfigurationOwner(self.app.runtime), target_for(first)
        owner.write(target, fields, changes={"conversation.presentation.file_understanding_max_chars": 12000},
            reset=(), expected_revision=owner.read(target, fields).revision, observed_at=self.clock.now())
        self.assertEqual(intake_settings(self.app.runtime, second.scope, second.role_binding.role)
                         ["file_understanding_max_chars"], 12000)

    @staticmethod
    def window(context):
        """A window target as every feature builds it: the physical scope and nothing else."""
        from companion_v2.configuration.models import ConfigLayer, ConfigTarget
        return ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW, context.role_binding.role, context.scope)

    async def test_plain_window_targets_address_the_conversation_settings(self):
        from companion_v2.conversation.reply_settings import GROUP, ReplyConfigurationOwner, configuration_fields
        self.assertEqual((await self.link())["status"], "ok")
        first = (await self.turn(0, "Plain target", "plain-target-a"))["context"]
        second = (await self.turn(1, "Plain target again", "plain-target-b"))["context"]
        common = self.controller.settings.common
        fields = configuration_fields(common.segmentation, common.streaming, common.policy.part_gap_seconds)
        owner, field = ReplyConfigurationOwner(self.app.runtime), "conversation.presentation.private_debounce_enabled"
        target = self.window(second)
        owner.write(target, fields, changes={field: True}, reset=(),
            expected_revision=owner.read(target, fields).revision, observed_at=self.clock.now())
        self.assertTrue(owner.read(self.window(first), fields).fields[field].value)
        snapshot = require(self.app.configuration.read(first.actor, target=self.window(first), group_id=GROUP,
                                                       observed_at=self.clock.now()))
        self.assertTrue(snapshot.fields[field].value)
        self.assertEqual(snapshot.target.authorization_scope, first.scope)

    async def test_settings_read_through_the_configuration_side_store_are_shared(self):
        from companion_v2.role_work.life.configuration import LifeConfigurationOwner, configuration_fields
        from companion_v2.role_work.life.rest_gate import read_settings
        self.assertEqual((await self.link())["status"], "ok")
        first = (await self.turn(0, "Rest preferences", "side-store-a"))["context"]
        second = (await self.turn(1, "Rest preferences again", "side-store-b"))["context"]
        self.assertIsNot(self.app.runtime.configuration_store, self.app.runtime)
        owner, target, fields = LifeConfigurationOwner(self.app.runtime), self.window(first), configuration_fields()
        owner.write(target, fields, changes={"role.life.rest_reply_probability": 41}, reset=(),
            expected_revision=owner.read(target, fields).revision, observed_at=self.clock.now())
        self.assertEqual(read_settings(self.app.runtime, second).rest_reply_probability, 41)

    async def test_a_settings_write_changes_grants_only_on_the_endpoint_that_wrote_it(self):
        from companion_v2.role_work.growth.configuration import GrowthConfigurationOwner, configuration_fields
        self.assertEqual((await self.link())["status"], "ok")
        first = (await self.turn(0, "Growth preferences", "growth-grants-a"))["context"]
        second = (await self.turn(1, "Growth preferences again", "growth-grants-b"))["context"]
        owner, fields, field = GrowthConfigurationOwner(self.app.runtime), configuration_fields(), "role.growth.read_enabled"
        directory = self.controller.authority.identities
        for context in (first, second):
            target = self.window(context)
            owner.write(target, fields, changes={field: True}, reset=(),
                expected_revision=owner.read(target, fields).revision, observed_at=self.clock.now())
        def allowed(context):
            return directory.authorize(require(directory.resolve_authenticated(context.actor.principal,
                observed_at=self.clock.now())), scope=context.scope, capability="role.growth.read",
                observed_at=self.clock.now()).is_success
        self.assertEqual((allowed(first), allowed(second)), (True, True))
        target = self.window(second)
        owner.write(target, fields, changes={field: False}, reset=(),
            expected_revision=owner.read(target, fields).revision, observed_at=self.clock.now())
        self.assertFalse(owner.read(self.window(first), fields).fields[field].value)
        self.assertEqual((allowed(first), allowed(second)), (True, False))

    async def contact_editor(self, identifier):
        """Both linked endpoints, an operator, and the contact section as the settings editor shows it."""
        from astrbot_host.management_identity import dashboard_operator
        from companion_v2.configuration.management import ProactiveContactManagement
        from companion_v2.relationships.private_management import PrivateMemberSettings
        self.assertEqual((await self.link())["status"], "ok")
        first = (await self.turn(0, "Contact preferences", identifier + "-a"))["context"]
        second = (await self.turn(1, "Contact preferences again", identifier + "-b"))["context"]
        operator = dashboard_operator(self.app.runtime, "synthetic-admin", at=self.clock.now())
        shared = ProactiveContactManagement(self.app, first)
        require(shared.configure(operator, {"operation_key": "global-contact-defaults",
            "edit_token": require(shared.status(operator))["edit_token"],
            "changes": {"enabled": True, "interval_seconds": 600}}))

        def contact(context):
            status = require(PrivateMemberSettings(self.app, context).status(operator))
            self.assertEqual(status["section_errors"], {})
            return status["sections"]["contact"]

        def save(writer, values, command=None):
            """Save through the editor; the same command again is a repeat of that save."""
            command = command or {"operation_key": "contact-override-%d" % values["interval_seconds"], "sections": {
                "contact": {"edit_token": contact(writer)["edit_token"], "changes": values}}}
            return command, require(PrivateMemberSettings(self.app, writer).configure(operator, command))
        return first, second, contact, save

    async def test_a_contact_override_saved_in_the_settings_editor_reads_the_same_on_both_endpoints(self):
        from datetime import timedelta
        first, second, contact, save = await self.contact_editor("contact-override")
        pause = (self.clock.now() + timedelta(hours=1)).isoformat()
        for writer, values in ((second, {"enabled": False, "interval_seconds": 1200, "pause_until": pause}),
                               (first, {"enabled": False, "interval_seconds": 1800, "pause_until": ""})):
            _, saved = save(writer, values)
            for context in (first, second):
                settings = contact(context)["settings"]
                self.assertEqual({name: settings[name] for name in values}, values)
            operation = require(self.app.configuration.operations.get(saved["results"]["contact"]["operation_id"]))
            self.assertEqual(operation.operation.scope, writer.scope)

    async def test_a_repeated_settings_save_from_a_joined_endpoint_finds_its_receipt(self):
        first, second, _, save = await self.contact_editor("contact-replay")
        for writer, values in ((second, {"interval_seconds": 1200}), (first, {"interval_seconds": 1800})):
            command, saved = save(writer, values)
            _, repeated = save(writer, values, command)
            self.assertTrue(repeated["results"]["contact"]["replayed"])
            self.assertEqual(repeated["results"]["contact"]["operation_id"],
                             saved["results"]["contact"]["operation_id"])

    async def test_model_window_settings_are_shared_in_dashboard_and_runtime_reads(self):
        from astrbot_host.management_identity import dashboard_operator
        from astrbot_host.model_route_material import route_snapshot
        from companion_v2.model_gateway.management import ModelManagement
        self.assertEqual((await self.link())["status"], "ok")
        first = (await self.turn(0, "Model preferences", "model-settings-a"))["context"]
        second = (await self.turn(1, "Shared model preferences", "model-settings-b"))["context"]
        operator = dashboard_operator(self.app.runtime, "synthetic-admin", at=self.clock.now())
        manager = ModelManagement(self.app, second)
        status = require(manager.status(operator, layer="window"))
        task = status["tasks"][0]
        saved = require(manager.configure(operator, {"kind": task["kind"], "layer": "window",
            "operation_key": "shared-model-prompt", "edit_token": task["edit_token"],
            "changes": {"prompt": "Shared model task instruction"}, "reset": []}))
        operation = require(self.app.configuration.operations.get(saved["operation_id"]))
        self.assertEqual(operation.operation.scope, second.scope)
        other = require(ModelManagement(self.app, first).status(operator, layer="window"))
        original_task = next(item for item in other["tasks"] if item["kind"] == task["kind"])
        self.assertEqual(original_task["fields"]["prompt"]["value"], "Shared model task instruction")
        self.assertEqual(route_snapshot(self.app, second), route_snapshot(self.app, first))

    async def test_timeline_and_journal_archive_share_records_with_original_sources(self):
        from companion_v2.role_work.growth import GrowthKind, RoleGrowthRecord
        from companion_v2.role_work.models import RoleActivity, RoleActivityState
        from companion_v2.role_work.journal.models import JournalContext, JournalDraft, JournalKey, JournalKind
        first = await self.turn(0, "We walked and wrote a diary", "timeline-before")
        context, at = first["context"], self.clock.now()
        life = self.app.life_context
        owner = life.owner(context)
        activity = life.life.add_activity(RoleActivity("shared-walk", context.role_binding.role.subject_id,
            context.scope, at, RoleActivityState.ACTIVE, description="A walk together"))
        growth = self.app.role_growth_context.reader.ledger.add(RoleGrowthRecord("shared-goal", owner,
            context.scope, GrowthKind.GOAL, "Walk regularly", "Walk together", "Proposed",
            (first["native_source_future"].result(),), at, at, proposed_by=context.actor.subject))
        key = JournalKey(owner, JournalKind.DIARY, at.date().isoformat())
        life.journal.save(JournalDraft(JournalContext(key, context.scope, "UTC"), body="We walked together"),
            expected_revision=0, operation_id="shared-diary")
        self.assertEqual((await self.link())["status"], "ok")
        second = await self.turn(1, "Our timeline", "timeline-after")
        reader = second["context"]
        for action in ("role.life.read", "role.growth.read"):
            self.grant(reader, action)
        result = self.app.role_timeline.query(reader, kinds=["activity", "growth", "diary"])
        identifiers = {item["reference"]["identifier"] for item in result["items"]}
        self.assertEqual(identifiers, {"shared-walk", "shared-goal", "shared-diary"})
        archive = life.journal.get_for_audience(key, scope=reader.scope, recipient=reader.actor.subject, observed_at=at)
        self.assertEqual(archive.draft.body, "We walked together")
        self.assertEqual(archive.draft.context.scope.platform, "qq-main")
        self.assertEqual((activity.scope.platform, growth.sources[0].scope.platform), ("qq-main", "qq-main"))

    async def test_the_most_recent_growth_reaches_the_reply_context_when_the_conversation_has_more_than_one_scan(self):
        from datetime import timedelta
        from companion_v2.role_work.growth import GrowthKind, RoleGrowthRecord
        from companion_v2.role_work.growth.context import SCAN_LIMIT
        self.assertEqual((await self.link())["status"], "ok")
        prepared = [await self.turn(index, "Our growth", "growth-scan-%d" % index) for index in (0, 1)]
        reader = self.app.role_growth_context.reader
        # Twenty confirmed records on each endpoint, written alternately: the later, the higher its id.
        for number in range(SCAN_LIMIT + 8):
            turn = prepared[number % 2]
            context, at = turn["context"], self.clock.now() + timedelta(seconds=number)
            identifier, owner = "growth-%02d" % number, reader.owner(turn["context"])
            reader.ledger.add(RoleGrowthRecord(identifier, owner, context.scope, GrowthKind.GOAL,
                "Goal %02d" % number, "", "", (turn["native_source_future"].result(),), at, at,
                proposed_by=context.actor.subject))
            reader.ledger.confirm(identifier, owner=owner, scope=context.scope, expected_revision=1,
                operation_id="confirm-" + identifier, actor=context.actor.subject, observed_at=at)
        self.clock.advance(timedelta(minutes=5))
        for turn in prepared:
            self.grant(turn["context"], "role.growth.read")
            found = reader.candidates(turn["context"], self.clock.now())
            newest = [item.reference["growth_id"] for item in found.items]
            self.assertEqual(newest, ["growth-%02d" % number
                                      for number in range(SCAN_LIMIT + 7, 7, -1)])
            self.assertTrue(found.truncated)

    async def test_diary_with_its_original_input_and_life_candidates_are_read_through_the_other_endpoint(self):
        from companion_v2.role_work.models import RoleActivity, RoleActivityState
        from companion_v2.role_work.journal.models import JournalContext, JournalDraft, JournalKey, JournalKind
        first = await self.turn(0, "We walked and I wrote it down", "life-before")
        context, at, life = first["context"], self.clock.now(), self.app.life_context
        source = first["native_source_future"].result()
        life.life.add_activity(RoleActivity("evidenced-walk", context.role_binding.role.subject_id,
            context.scope, at, RoleActivityState.ACTIVE, description="A walk together"))
        key = JournalKey(life.owner(context), JournalKind.DIARY, at.date().isoformat())
        life.journal.save(JournalDraft(JournalContext(key, context.scope, "UTC"), body="We walked together",
            sources=(source,)), expected_revision=0, operation_id="evidenced-diary")
        self.assertEqual((await self.link())["status"], "ok")
        reader = (await self.turn(1, "What did we do?", "life-after"))["context"]
        self.grant(reader, "role.life.read")
        entry = life.journal.get_for_audience(key, scope=reader.scope, recipient=reader.actor.subject, observed_at=at)
        self.assertEqual((entry.draft.body, entry.draft.sources[0].scope.platform), ("We walked together", "qq-main"))
        candidates = life.candidates(reader, self.clock.now())
        found = {(item.reference["kind"], item.reference.get("activity_id") or item.reference.get("local_date"))
                 for options in candidates.variants for item in options}
        self.assertEqual(found, {("activity", "evidenced-walk"), ("journal", key.local_date)})
        self.assertEqual(candidates.skipped, 0)

    async def test_linked_usage_excludes_other_participants_of_the_same_account(self):
        from dataclasses import replace
        from companion_v2.foundation.refs import AudienceRef
        from companion_v2.model_gateway.core_observations import observations_for_role, record_observation
        first = await self.turn(0, "Count this turn", "usage-own")
        await asyncio.gather(*self.controller.session.core_reply_observations._pending)
        own = first["context"]
        scope = replace(own.scope, native_scope_id="999", session_id="999")
        stranger = replace(own, role_binding=replace(own.role_binding, scope=scope),
                           audience=AudienceRef(scope, own.audience.recipients))
        mine = observations_for_role(self.app.runtime, own)[0]
        record_observation(self.app.runtime, stranger, {**{key: mine[key] for key in mine
            if key not in ("context_key", "context_json")}, "turn_id": "core-reply:another-participant"})
        self.assertEqual({row["turn_id"] for row in observations_for_role(self.app.runtime, own)},
                         {first["turn_id"], "core-reply:another-participant"})
        self.assertEqual((await self.link())["status"], "ok")
        second = await self.turn(1, "Count this one too", "usage-linked")
        await asyncio.gather(*self.controller.session.core_reply_observations._pending)
        for context in (second["context"], (await self.turn(0, "And again", "usage-owner"))["context"]):
            self.assertNotIn("core-reply:another-participant",
                             {row["turn_id"] for row in observations_for_role(self.app.runtime, context)})
        self.assertIn(first["turn_id"],
                      {row["turn_id"] for row in observations_for_role(self.app.runtime, second["context"])})

    async def test_usage_unifies_existing_and_linked_turns_once_with_source_tags(self):
        from companion_v2.model_gateway.core_observations import observations_for_role
        first = await self.turn(0, "Count this old turn", "usage-before")
        self.assertEqual((await self.link())["status"], "ok")
        second = await self.turn(1, "Count this new turn", "usage-after")
        await asyncio.gather(*self.controller.session.core_reply_observations._pending)
        first_rows = observations_for_role(self.app.runtime, first["context"])
        second_rows = observations_for_role(self.app.runtime, second["context"])
        self.assertEqual({r["turn_id"] for r in first_rows}, {first["turn_id"], second["turn_id"]})
        self.assertEqual(first_rows, second_rows)
        self.assertEqual({r["source_channel"] for r in second_rows}, {"qq-main", self.second_channel})
        duplicate = self.event_for(1, "Count this new turn", "usage-after")
        await (await self.dispatch(duplicate))
        await self.controller.session.linked_conversations.gate.drain()
        self.assertEqual(len(observations_for_role(self.app.runtime, second["context"])), 2)

    async def test_input_logs_are_one_view_with_physical_source_tags(self):
        from astrbot.core.log import LogManager
        sink = LogManager.get_plugin_logger("astrbot_plugin_private_companion")
        self.assertEqual((await self.link())["status"], "ok")
        with self.assertLogs(sink, level="INFO") as captured:
            await self.turn(0, "Log A", "log-a")
            await self.turn(1, "Log B", "log-b")
            duplicate = self.event_for(1, "Log B", "log-b")
            await (await self.dispatch(duplicate))
        rows = [record.getMessage() for record in captured.records if "companion_native_input" in record.getMessage()]
        self.assertEqual(len(rows), 2)
        self.assertTrue(any("source_channel=qq-main" in row for row in rows))
        self.assertTrue(any("source_channel=" + self.second_channel in row for row in rows))

    async def test_owners_survive_restart_and_original_endpoint_detachment(self):
        from datetime import timedelta
        from companion_v2.model_gateway.core_observations import observations_for_role
        from companion_v2.memory.models import AtomRef
        from companion_v2.role_work.state.ownership import role_schedule_key
        from companion_v2.role_work.journal.models import JournalContext, JournalDraft, JournalKey, JournalKind
        first = await self.turn(0, "I drink tea", "restart-before")
        saved = self.learn(first, "persistent-fact")
        life, at = self.app.life_context, self.clock.now()
        key = JournalKey(life.owner(first["context"]), JournalKind.DIARY, at.date().isoformat())
        life.journal.save(JournalDraft(JournalContext(key, first["context"].scope, "UTC"), body="Persistent diary"),
                          expected_revision=0, operation_id="persistent-diary")
        self.clock.advance(timedelta(seconds=2))
        self.assertEqual((await self.link())["status"], "ok")
        second = await self.turn(1, "Before restart", "restart-linked")
        await self.controller.terminate()
        self.native.platforms.pop("qq-main")
        self.controller = self.make_controller()
        self.plugin.staging = self.controller
        result = await self.controller.start()
        self.assertTrue(result.is_success, result.failure)
        self.app = self.controller.session.application
        restored = await self.turn(1, "After restart", "restart-after")
        self.assertEqual(require(self.app.memory.writer.read(restored["context"], AtomRef(saved["atom_id"], 1)))["object"], "tea")
        from companion_v2.conversation.linked_ownership import owner_scope
        self.assertEqual(owner_scope(self.app.runtime, restored["context"].scope), first["context"].scope)
        self.assertEqual(role_schedule_key(first["context"]), role_schedule_key(restored["context"]))
        self.assertEqual((await self.link(revision=1, members=["second"]))["status"], "ok")
        detached = await self.turn(1, "Only this endpoint remains", "after-detach")
        self.assertEqual(require(self.app.memory.writer.read(detached["context"], AtomRef(saved["atom_id"], 1)))["object"], "tea")
        archive = self.app.life_context.journal.get_for_audience(key, scope=detached["context"].scope,
            recipient=detached["context"].actor.subject, observed_at=self.clock.now())
        self.assertEqual(archive.draft.body, "Persistent diary")
        self.assertEqual(archive.draft.context.scope.platform, "qq-main")
        self.assertEqual({row["source_channel"] for row in observations_for_role(self.app.runtime, detached["context"])},
                         {"qq-main", self.second_channel})

    def life_routines(self, *contexts):
        life = self.app.life_runtime
        return {id(routine): routine for routine in (life.for_context(context) for context in contexts)}

    async def test_linked_endpoints_have_one_life_routine_and_daily_schedule_also_after_restart(self):
        self.assertEqual((await self.link())["status"], "ok")
        first = (await self.turn(0, "Good morning", "life-owner-a"))["context"]
        second = (await self.turn(1, "Good morning again", "life-owner-b"))["context"]
        await asyncio.gather(*tuple(self.controller.session.core_reply_maintenance._pending))
        routines = self.life_routines(first, second)
        self.assertEqual(len(routines), 1)
        self.assertEqual({routine.schedule_id for routine in routines.values()},
                         {"role.life.daily:" + first.key})
        await self.controller.terminate()
        self.controller = self.make_controller()
        self.plugin.staging = self.controller
        result = await self.controller.start()
        self.assertTrue(result.is_success, result.failure)
        self.app = self.controller.session.application
        # Upstream binds accounts in the background after a start.
        await self.controller.session.account_recovery.refresh(wait=True)
        contexts = [self.controller.authority.context(target, first.role_binding.role.subject_id)
                    for target in ("first", "second")]
        routines = self.life_routines(*contexts)
        self.assertEqual(len(routines), 1)
        self.assertIsNotNone(next(iter(routines.values())).instruction)

    async def own_life_routines(self, identifier):
        """Each endpoint enables its routines itself, before any link exists."""
        from companion_v2.role_work.life.configuration import LifeConfigurationOwner, configuration_fields
        for index, name in enumerate(("first", "second")):
            context = (await self.turn(index, "Before linking", "%s-own-%d" % (identifier, index)))["context"]
            self.save(LifeConfigurationOwner(self.app.runtime), configuration_fields(), context,
                      **{"role.life.enabled": True})
            # The window's next activation issues the grants; no host tick runs in between.
            self.controller.authority.select(self.controller.settings.target(name), context.scope,
                                             context.role_binding.role)

    async def test_the_life_routine_runs_through_the_healthy_peer_when_its_first_binder_disconnects(self):
        await self.own_life_routines("life-failover")
        self.assertEqual((await self.link())["status"], "ok")
        first = (await self.turn(0, "Good morning", "life-failover-a"))["context"]
        second = (await self.turn(1, "Good morning again", "life-failover-b"))["context"]
        self.assertEqual((self.allowed(first, "role.life.run"), self.allowed(second, "role.life.run")), (True, True))
        await asyncio.gather(*tuple(self.controller.session.core_reply_maintenance._pending))
        life, bridge = self.app.life_runtime, self.controller.session.bridge
        routine, = self.life_routines(first, second).values()
        routine.refresh()
        await routine.preflight()
        self.assertEqual(routine.context.scope, first.scope)
        self.native.platforms["qq-main"].client._wsr_api_clients.clear()
        with self.assertRaises(Exception) as unavailable:
            await bridge.verify_role(first.scope, first.role_binding, routine.instruction)
        # Upstream's connection layer names a disconnected client offline, not changed.
        self.assertEqual(unavailable.exception.code, "host_account_offline")
        life.bind(second, routine.instruction, guard=bridge.verify_role)
        ran = require(await routine.diary(local_date=self.clock.now().date().isoformat()))
        # The member that ran it is the one the work is attributed to.
        self.assertEqual(routine.context.scope, second.scope)
        task = self.app.runtime.read_one("SELECT origin_key FROM execution_tasks WHERE task_id = ?",
                                         (ran.writing_task_id,))
        self.assertEqual(task["origin_key"], second.key)
        # Still one routine and one daily schedule for the conversation.
        self.assertEqual(set(self.life_routines(first, second)), {id(routine)})
        self.assertEqual([key for key in self.app.journal_schedule._schedules if key.startswith("role.life.daily:")],
                         ["role.life.daily:" + first.key])
        # The first binder takes its routine back once its account returns.
        self.native.platforms["qq-main"].client._wsr_api_clients["100"] = object()
        await routine.preflight()
        self.assertEqual(routine.context.scope, first.scope)

    async def test_the_life_routine_leaves_a_removed_first_binder_for_the_remaining_member_without_a_restart(self):
        await self.own_life_routines("life-removed")
        self.assertEqual((await self.link())["status"], "ok")
        first = (await self.turn(0, "Good morning", "life-removed-a"))["context"]
        second = (await self.turn(1, "Good morning again", "life-removed-b"))["context"]
        await asyncio.gather(*tuple(self.controller.session.core_reply_maintenance._pending))
        routine, = self.life_routines(first, second).values()
        routine.advance()
        self.assertEqual(routine.context.scope, first.scope)
        self.assertEqual((await self.link(revision=1, members=["second"]))["status"], "ok")
        # The host loop's next pass, with no restart and no new bind.
        routine.advance()
        self.assertEqual(routine.context.scope, second.scope)
        ran = require(await routine.diary(local_date=self.clock.now().date().isoformat()))
        task = self.app.runtime.read_one("SELECT origin_key FROM execution_tasks WHERE task_id = ?",
                                         (ran.writing_task_id,))
        self.assertEqual(task["origin_key"], second.key)

    async def test_context_records_keep_their_stored_form(self):
        from dataclasses import asdict
        import json
        from companion_v2.execution.models import encode
        first = (await self.turn(0, "Independent", "unlinked-encoding"))["context"]
        self.assertEqual((await self.link())["status"], "ok")
        second = (await self.turn(1, "Linked", "linked-encoding"))["context"]
        for context in (first, second):
            # Task records store ``asdict(spec)``; other owners store ``encode(context)``.
            self.assertEqual(set(asdict(context)), {"actor", "role_binding", "audience"})
            self.assertEqual(set(json.loads(encode(context))), {"actor", "role_binding", "audience"})

    async def test_schema_one_link_without_subject_uses_persisted_anchor_identity(self):
        from astrbot_host.linked_conversations import KEY
        first = (await self.turn(0, "An existing conversation", "old-link-before"))["context"]
        self.assertEqual((await self.link())["status"], "ok")
        records = self.controller.authority.read(KEY)
        for record in records.values():
            record["anchor"].pop("subject")
        self.controller.authority.write(KEY, records)
        await self.controller.terminate()
        self.controller = self.make_controller()
        self.plugin.staging = self.controller
        result = await self.controller.start()
        self.assertTrue(result.is_success, result.failure)
        self.app = self.controller.session.application
        second = (await self.turn(1, "Read the old link", "old-link-after"))["context"]
        self.assertEqual(second.actor.subject, first.actor.subject)
        from companion_v2.conversation.linked_ownership import owner_scope
        self.assertEqual(owner_scope(self.app.runtime, second.scope), first.scope)

    async def test_person_memory_shares_names_with_the_source_channel_of_each_change(self):
        self.assertEqual((await self.link())["status"], "ok")
        first = (await self.turn(0, "Remember my name", "person-a"))["context"]
        second = (await self.turn(1, "The same person", "person-b"))["context"]
        cards = self.app.messages.acquaintance
        cards.record_platform_names(first, nickname="First channel name")
        view = cards.recall(second)
        self.assertEqual(view["display_name"], "First channel name")
        self.assertEqual(view["display_name_source_channel"], "qq-main")
        cards.record_platform_names(second, nickname="Second channel name")
        view = cards.recall(first)
        self.assertEqual(view["display_name"], "Second channel name")
        self.assertEqual(view["recent_renames"][0]["source_channel"], self.second_channel)


class LinkedDistinctPrincipalTests(LinkedOwnerTests):
    origins = ("qq-main:FriendMessage:200", "qq-second:FriendMessage:201")

    def make_controller(self, config=None):
        from tests.multi_host_support import multi_config
        config = config or multi_config()
        config["multi_host"]["targets"][1]["participant_id"] = "201"
        return super().make_controller(config)

    async def test_anchor_endpoint_cannot_use_private_ownership_to_bypass_its_action_grant(self):
        from companion_v2.identity_policy.object_access import ObjectAccessPolicy
        self.assertEqual((await self.link())["status"], "ok")
        first = (await self.turn(0, "First source", "anchor-permission-a"))["context"]
        second = (await self.turn(1, "Second source", "anchor-permission-b"))["context"]
        directory = self.controller.authority.identities
        for grant in directory.grants_for_subject(first.actor.subject):
            if grant.scope == first.scope and grant.capability == "memory.read":
                directory.revoke_grant(grant.grant_id, revoked_at=self.clock.now(),
                                       expected_revision=directory.revision)
        policy = ObjectAccessPolicy(self.app.runtime)
        for scope in (first.scope, second.scope):
            result = policy.scope_access(first.actor, scope=scope, action="memory.read",
                allow_private_owner=True, observed_at=self.clock.now())
            self.assertFalse(result.is_success)
            self.assertEqual(result.failure.code, "authorization_revoked")

    async def test_joining_admin_endpoint_does_not_promote_the_conversation_owner(self):
        from companion_v2.identity_policy.object_access import ObjectAccessPolicy
        policy = ObjectAccessPolicy(self.app.runtime)
        first = (await self.turn(0, "A standard conversation", "standard-owner"))["context"]
        self.controller.authority.core_target_id = "second"
        independent = (await self.turn(1, "An administrator endpoint", "admin-before-link"))["context"]
        self.assertTrue(policy.scope_access(independent.actor, scope=independent.scope,
            action="access.policy.set", observed_at=self.clock.now()).is_success)
        self.assertEqual((await self.link())["status"], "ok")
        joined = (await self.turn(1, "Join the standard conversation", "admin-after-link"))["context"]
        for context in (first, joined):
            result = policy.scope_access(context.actor, scope=context.scope,
                action="access.policy.set", observed_at=self.clock.now())
            self.assertFalse(result.is_success)
            self.assertEqual(result.failure.code, "core_access_required")


class LinkedSameBotIdentityTests(LinkedReplyFixture, unittest.IsolatedAsyncioTestCase):
    origins = ("qq-main:FriendMessage:200", "qq-second:FriendMessage:201")
    turn = LinkedOwnerTests.turn

    def make_controller(self, config=None):
        from tests.multi_host_support import multi_config
        config = config or multi_config()
        config["multi_host"]["targets"][1].update(participant_id="201", bot_id="bot-a")
        config["multi_host"]["personas"] = [row for row in config["multi_host"]["personas"] if row["bot_id"] == "bot-a"]
        return super().make_controller(config)

    async def test_private_link_does_not_rebind_identity_used_by_unlinked_group_routes(self):
        second = await self.turn(1, "An independent native identity", "identity-before")
        original = second["context"].actor
        self.assertEqual((await self.link())["status"], "ok")
        linked = await self.turn(1, "A linked private conversation", "identity-after")
        current = require(self.controller.authority.identities.resolve_authenticated(original.principal,
            observed_at=self.clock.now()))
        self.assertEqual((current.subject, current.binding_id, current.binding_revision),
                         (original.subject, original.binding_id, original.binding_revision))
        self.assertEqual(linked["context"].actor.subject.subject_id, "onebot-user:200")
