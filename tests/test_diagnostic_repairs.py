from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import json
import sqlite3
import unittest

from companion_v2.capabilities.configuration import CapabilityConfigurationOwner
from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.configuration.registry import FieldDefinition, FieldRegistry, ValueKind
from companion_v2.configuration.service import ConfigurationService
from companion_v2.diagnostics.models import DiagnosticQuery
from companion_v2.diagnostics.repairs import RepairProposalService
from companion_v2.diagnostics.service import DiagnosticService
from companion_v2.foundation.refs import SubjectKind, SubjectRef
from companion_v2.identity_policy.models import AuthenticatedHostPrincipal, AuthorizationTier
from companion_v2.identity_policy.personas import ConversationRoute, PersonaBinding, PersonaSelector, SelectorKind
from companion_v2.screen.models import SCREEN_EXTENSION
from tests.test_diagnostics import DiagnosticFixture
from tests.test_management_access import INSTALLATION, NOW, SCOPE, ROLE


class DiagnosticRepairTests(DiagnosticFixture):
    def setUp(self):
        super().setUp()
        self.repairs = RepairProposalService(self.diagnostics)

    def proposal(self, *, context=None, query=None, proposal_id="proposal", changes=None, lifetime_seconds=900):
        context = context or self.important_context
        report = self.check(query, context).unwrap()
        return self.repairs.propose(context, proposal_id=proposal_id, report_id=report.report_id,
            changes=changes or {"output.enabled": False}, rationale="先关闭这一项进行观察", lifetime_seconds=lifetime_seconds)

    def finish(self, proposal, *, confirm=True, context=None, **overrides):
        args = {"proposal_id": proposal.proposal_id, "expected_revision": 1, "review_token": proposal.review_token, **overrides}
        method = self.repairs.confirm if confirm else self.repairs.cancel
        return method(context or self.important_context, **args)

    def test_proposal_preserves_exact_review_without_changing_configuration(self):
        before = self.counts()
        proposal = self.proposal().unwrap()
        self.assertEqual(self.counts(), (*before[:3], before[3] + 1))
        review = json.loads(proposal.review_json)
        self.assertEqual(review["delta"]["output.enabled"]["before"]["value"], True)
        self.assertEqual(review["delta"]["output.enabled"]["after"], {"value": False, "explicit": True})
        self.assertEqual(review["rollback"]["reset_to_inherit"], ["output.enabled"])
        self.assertEqual(review["original_role"]["revision"], self.important_context.role_binding.revision)
        self.assertTrue(self.read(self.important).unwrap().values["output.enabled"])
        self.assertEqual(proposal.state, "pending")

    def test_confirmation_is_atomic_replays_original_and_survives_restart(self):
        proposal = self.proposal().unwrap()
        confirmed = self.finish(proposal).unwrap()
        self.assertEqual(confirmed.state, "confirmed")
        self.assertFalse(self.read(self.important).unwrap().values["output.enabled"])
        self.assertEqual(json.loads(confirmed.receipt_json)["effect"], "local_configuration_saved")
        self.write(self.important, self.read(self.important).unwrap(), value=True).unwrap()
        self.runtime.close()
        self.runtime = self.open_runtime()
        self.configuration = ConfigurationService(self.runtime, self.registry)
        self.diagnostics = DiagnosticService(self.configuration, now=self.clock.now)
        self.repairs = RepairProposalService(self.diagnostics)
        self.clock.advance(timedelta(hours=1))
        retry = self.finish(proposal).unwrap()
        self.assertTrue(retry.replayed)
        self.assertEqual(retry.receipt_json, confirmed.receipt_json)
        self.assertTrue(self.read(self.important, now=self.clock.now()).unwrap().values["output.enabled"])
        self.assertEqual(self.counts()[:3], (2, 2, 0))

    def test_pending_proposal_survives_restart_and_cancel_is_terminal_without_write(self):
        proposal = self.proposal().unwrap()
        self.runtime.close()
        self.runtime = self.open_runtime()
        self.configuration = ConfigurationService(self.runtime, self.registry)
        self.repairs = RepairProposalService(DiagnosticService(self.configuration, now=self.clock.now))
        cancelled = self.finish(proposal, confirm=False).unwrap()
        self.assertEqual(cancelled.state, "cancelled")
        self.assertEqual(json.loads(cancelled.receipt_json)["effect"], "proposal_cancelled")
        self.assertTrue(self.finish(proposal, confirm=False).unwrap().replayed)
        self.assertEqual(self.finish(proposal).failure.code, "repair_proposal_closed")
        self.assertEqual(self.counts()[:3], (0, 0, 0))

    def test_pending_proposal_can_confirm_after_restart_with_same_evidence(self):
        proposal = self.proposal().unwrap()
        self.runtime.close()
        self.runtime = self.open_runtime()
        self.configuration = ConfigurationService(self.runtime, self.registry)
        self.repairs = RepairProposalService(DiagnosticService(self.configuration, now=self.clock.now))
        self.assertEqual(self.finish(proposal).unwrap().state, "confirmed")

    def test_stale_config_and_parent_changes_require_new_review(self):
        for parent in (False, True):
            with self.subTest(parent=parent):
                proposal = self.proposal(proposal_id="parent-" + str(parent)).unwrap()
                target = self.global_target if parent else self.window
                actor = self.core if parent else self.important
                snapshot = self.read(actor, target).unwrap()
                self.write(actor, snapshot, value=not parent).unwrap()
                self.assertEqual(self.finish(proposal).failure.code, "configuration_revision_conflict")
                self.assertEqual(self.finish(proposal, confirm=False).unwrap().state, "cancelled")

    def test_expiry_and_review_mismatch_cannot_apply_and_expired_plan_can_be_cancelled(self):
        proposal = self.proposal(lifetime_seconds=10).unwrap()
        self.assertEqual(self.finish(proposal, review_token="wrong").failure.code, "repair_review_conflict")
        self.assertEqual(self.finish(proposal, expected_revision=True).failure.code, "repair_review_conflict")
        self.clock.advance(timedelta(seconds=10))
        self.assertEqual(self.repairs.read(self.important_context, proposal_id=proposal.proposal_id).unwrap().state, "expired")
        self.assertEqual(self.finish(proposal).failure.code, "repair_proposal_expired")
        self.finish(proposal, confirm=False).unwrap()
        self.assertEqual(self.counts()[:3], (0, 0, 0))

    def test_old_proposal_cannot_cross_actor_or_role_a_b_a(self):
        proposal = self.proposal(context=self.core_context).unwrap()
        self.assertEqual(self.finish(proposal).failure.code, "repair_proposal_unavailable")
        role_b = SubjectRef(INSTALLATION, SubjectKind.BOT, "role-b")
        scope = self.core_context.scope
        original = self.core_context.role_binding
        self.identity.bind_role(scope=scope, role=role_b, expected_revision=original.revision, observed_at=NOW)
        self.identity.bind_role(scope=scope, role=ROLE, expected_revision=original.revision + 1, observed_at=NOW)
        self.assertEqual(self.finish(proposal, context=self.core_context).failure.code, "role_binding_changed")
        newer = self.context(self.core)
        self.assertEqual(self.finish(proposal, context=newer).failure.code, "repair_proposal_unavailable")
        self.assertEqual(self.counts()[:3], (0, 0, 0))

    def test_revocation_and_lower_tier_block_confirm_and_historical_receipt(self):
        proposal = self.proposal(context=self.core_context, query=DiagnosticQuery(target=self.window, group_id="security"),
                                 changes={"security.enabled": False}).unwrap()
        self.finish(proposal, context=self.core_context).unwrap()
        self.policy.set_tier(self.core.subject, AuthorizationTier.IMPORTANT, expected_revision=self.policy.revision, observed_at=NOW)
        self.assertEqual(self.finish(proposal, context=self.core_context).failure.code, "core_access_required")
        pending = self.proposal(proposal_id="pending").unwrap()
        self.identity.revoke_binding(self.important.binding_id, expected_revision=self.identity.revision, revoked_at=NOW)
        self.assertEqual(self.finish(pending).failure.code, "binding_revoked")

    def test_rebound_actor_and_changed_consumer_evidence_do_not_apply(self):
        proposal = self.proposal().unwrap()
        self.configuration.start_consumer("output", expected_generation=0)
        self.assertEqual(self.finish(proposal).failure.code, "diagnostic_evidence_changed")
        self.identity.bind(binding_id="replacement", principal=self.important.principal, subject=self.standard.subject,
                           valid_from=NOW, expected_revision=self.identity.revision)
        self.assertEqual(self.finish(proposal).failure.code, "binding_rebound")
        self.assertEqual(self.counts()[:3], (0, 0, 0))

    def test_failed_terminal_save_rolls_back_configuration_and_operation(self):
        proposal = self.proposal().unwrap()
        with self.runtime.transaction() as connection:
            connection.execute("CREATE TRIGGER reject_repair_finish BEFORE UPDATE ON diagnostic_repair_proposals "
                               "BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.finish(proposal)
        self.assertEqual(self.counts()[:3], (0, 0, 0))
        self.assertTrue(self.read(self.important).unwrap().values["output.enabled"])
        self.assertEqual(self.repairs.read(self.important_context, proposal_id="proposal").unwrap().state, "pending")
        with self.runtime.transaction() as connection:
            connection.execute("DROP TRIGGER reject_repair_finish")
        self.finish(proposal).unwrap()

    def test_invalid_probability_or_field_and_static_explanation_cannot_create_plan(self):
        self.registry = FieldRegistry(version="probability.v1", fields=(
            FieldDefinition("chance", "probability", "output", ValueKind.PROBABILITY, 0.5),))
        self.configuration = ConfigurationService(self.runtime, self.registry)
        self.diagnostics = DiagnosticService(self.configuration, now=self.clock.now)
        self.repairs = RepairProposalService(self.diagnostics)
        query = DiagnosticQuery(target=self.window, group_id="probability")
        self.assertEqual(self.proposal(query=query, changes={"chance": 50}).failure.code, "invalid_probability")
        self.assertEqual(self.proposal(query=query, changes={"unknown": 0}).failure.code, "unknown_configuration_field")
        report = self.diagnostics.explain(self.important_context, "configuration").unwrap()
        self.assertEqual(self.repairs.propose(self.important_context, proposal_id="explain", report_id=report.report_id,
                         changes={"chance": 0}, rationale="test").failure.code, "repair_configuration_evidence_required")
        proposal = self.proposal(query=query, changes={"chance": 0}).unwrap()
        self.finish(proposal).unwrap()
        self.assertEqual(self.configuration.read(self.important, target=self.window, group_id="probability", observed_at=NOW).unwrap().values["chance"], 0)

    def test_same_proposal_id_replays_original_but_cannot_replace_plan(self):
        report = self.check().unwrap()
        kwargs = dict(proposal_id="same", report_id=report.report_id, changes={"output.enabled": False}, rationale="test")
        original = self.repairs.propose(self.important_context, **kwargs).unwrap()
        self.clock.advance(timedelta(minutes=2))
        replayed = self.repairs.propose(self.important_context, **kwargs).unwrap()
        self.assertTrue(replayed.replayed)
        self.assertEqual(replayed.review_json, original.review_json)
        self.assertEqual(self.repairs.propose(self.important_context, **{**kwargs, "changes": {"output.enabled": True}}).failure.code,
                         "repair_proposal_conflict")

    def test_capability_repair_reuses_owner_without_shadow_configuration_values(self):
        self.acquire().unwrap()
        owner = CapabilityConfigurationOwner(self.runtime, extension_id=SCREEN_EXTENSION)
        self.registry = FieldRegistry(version="capability.v1", fields=owner.fields)
        self.configuration = ConfigurationService(self.runtime, self.registry, owners=(owner,))
        self.diagnostics = DiagnosticService(self.configuration, now=self.clock.now)
        self.repairs = RepairProposalService(self.diagnostics)
        target = ConfigTarget(INSTALLATION, ConfigLayer.SCOPE, scope=SCOPE)
        proposal = self.proposal(query=DiagnosticQuery(target=target, group_id=owner.group_id), changes={owner.weight_key: 2.5}).unwrap()
        self.assertEqual(set(json.loads(proposal.review_json)["delta"]), {owner.enabled_key, owner.weight_key})
        self.assertFalse(json.loads(proposal.review_json)["rollback"]["exact_prior_state_restorable"])
        self.assertEqual(json.loads(proposal.review_json)["rollback"]["reset_to_inherit"], [])
        self.finish(proposal).unwrap()
        projection = self.management.repository.projection(extension_id=SCREEN_EXTENSION, scope=SCOPE).unwrap()
        self.assertEqual(projection.settings.thought_weight, 2.5)
        self.assertEqual(self.runtime.read_all("SELECT * FROM configuration_values", ()), ())

    def test_saved_basis_does_not_bypass_current_core_access_when_reading_or_cancelling(self):
        # Current window access may remain after downgrade; the saved storage check
        # still needs its own current authority before it can appear in the review.
        target = ConfigTarget(INSTALLATION, ConfigLayer.WINDOW, ROLE, self.core_context.scope)
        proposal = self.proposal(context=self.core_context, query=DiagnosticQuery(target=target, group_id="output", storage=True)).unwrap()
        self.policy.set_tier(self.core.subject, AuthorizationTier.IMPORTANT, expected_revision=self.policy.revision, observed_at=NOW)
        self.assertEqual(self.repairs.read(self.core_context, proposal_id=proposal.proposal_id).failure.code, "core_access_required")
        self.assertEqual(self.finish(proposal, context=self.core_context, confirm=False).failure.code, "core_access_required")

    def test_stale_review_is_marked_and_current_authorized_expiry_blocks_confirmation(self):
        self.policy.set_tier(self.important.subject, AuthorizationTier.STANDARD, expected_revision=self.policy.revision, observed_at=NOW)
        self.grant(self.important, SCOPE, "configuration.read", expires_at=NOW + timedelta(seconds=10))
        self.grant(self.important, SCOPE, "configuration.write", expires_at=NOW + timedelta(seconds=10))
        proposal = self.proposal().unwrap()
        self.configuration.start_consumer("output", expected_generation=0)
        self.assertEqual(self.repairs.read(self.important_context, proposal_id=proposal.proposal_id).unwrap().state, "stale")
        self.clock.advance(timedelta(seconds=10))
        self.assertEqual(self.finish(proposal).failure.code, "authorization_expired")
        self.assertEqual(self.counts()[:3], (0, 0, 0))

    def test_same_user_and_persona_on_another_bot_cannot_confirm_original_plan(self):
        proposal = self.proposal().unwrap()
        principal = AuthenticatedHostPrincipal(INSTALLATION, "host", "onebot", "important", "bot-b", "login-b")
        self.identity.bind(binding_id="bot-b-binding", principal=principal, subject=self.important.subject,
                           valid_from=NOW, expected_revision=self.identity.revision)
        scope = replace(SCOPE, bot_id="bot-b", account_id="login-b")
        self.personas.register_route(ConversationRoute(scope, user=self.important.subject), observed_at=NOW)
        self.personas.set_binding(PersonaBinding("bot-b-persona", PersonaSelector(INSTALLATION, SelectorKind.BOT, bot_id="bot-b"),
                                 ROLE, 0, NOW, "fixture", "admin"), expected_revision=0, observed_at=NOW)
        actor = self.identity.resolve_authenticated(principal, observed_at=NOW).unwrap()
        context = replace(self.important_context, actor=actor, role_binding=self.identity.resolve_role(scope, observed_at=NOW).unwrap(),
                          audience=replace(self.important_context.audience, scope=scope))
        self.assertEqual(self.finish(proposal, context=context).failure.code, "repair_proposal_unavailable")
        self.assertEqual(self.counts()[:3], (0, 0, 0))

    def test_unrelated_identity_and_policy_bumps_do_not_stale_pending_proposal(self):
        proposal = self.proposal(proposal_id="repair-stable-ab12").unwrap()
        # Advance global identity.revision via an unrelated user binding & grant
        unrelated_subject = SubjectRef(INSTALLATION, SubjectKind.HUMAN, "unrelated-user")
        unrelated_principal = AuthenticatedHostPrincipal(INSTALLATION, "host", "onebot", "unrelated", "bot-a", "login-a")
        self.identity.bind(binding_id="unrelated-binding", principal=unrelated_principal, subject=unrelated_subject,
                           valid_from=NOW, expected_revision=self.identity.revision)
        other_scope = replace(SCOPE, native_scope_id="other-peer")
        self.identity.issue_grant(grant_id="unrelated-grant", subject=unrelated_subject, scope=other_scope,
                                  capability="configuration.read", tier=AuthorizationTier.STANDARD,
                                  valid_from=NOW, expected_revision=self.identity.revision)
        # Advance global policy.revision via an unrelated subject tier and object registration
        self.policy.set_tier(unrelated_subject, AuthorizationTier.IMPORTANT,
                             expected_revision=self.policy.revision, observed_at=NOW)
        self.policy.register_object("memory:unrelated-1", owner=unrelated_subject, scope=other_scope, domain="memory")

        current = self.repairs.read(self.important_context, proposal_id=proposal.proposal_id).unwrap()
        self.assertEqual(current.state, "pending")
        confirmed = self.finish(proposal).unwrap()
        self.assertEqual(confirmed.state, "confirmed")
        self.assertFalse(self.read(self.important).unwrap().values["output.enabled"])

    def test_list_pending_and_short_code_resolution(self):
        first = self.proposal(proposal_id="repair-alpha-11aa").unwrap()
        second = self.proposal(proposal_id="repair-beta-22bb").unwrap()
        listed = self.repairs.list_pending(self.important_context).unwrap()
        self.assertEqual([item.proposal_id for item in listed], ["repair-beta-22bb", "repair-alpha-11aa"])
        self.assertEqual(self.repairs.resolve_proposal_id(self.important_context, "11AA").unwrap(), "repair-alpha-11aa")
        self.assertEqual(self.repairs.resolve_proposal_id(self.important_context, "22bb").unwrap(), "repair-beta-22bb")
        self.assertEqual(self.repairs.resolve_proposal_id(self.important_context, "11").failure.code, "repair_proposal_unavailable")
        # Ambiguous 4-char suffix fails safely
        self.proposal(proposal_id="repair-gamma-11aa").unwrap()
        self.assertEqual(self.repairs.resolve_proposal_id(self.important_context, "11aa").failure.code, "repair_proposal_ambiguous")
        self.assertEqual(self.repairs.resolve_proposal_id(self.important_context, "gamma-11aa").unwrap(), "repair-gamma-11aa")
        self.finish(first, confirm=False).unwrap()
        listed_after = self.repairs.list_pending(self.important_context).unwrap()
        self.assertNotIn("repair-alpha-11aa", [item.proposal_id for item in listed_after])
        # Now 11aa uniquely matches the remaining pending proposal
        self.assertEqual(self.repairs.resolve_proposal_id(self.important_context, "11aa").unwrap(), "repair-gamma-11aa")

    def test_expired_pending_row_does_not_collide_with_fresh_same_suffix(self):
        self.proposal(proposal_id="repair-old-ab12", lifetime_seconds=10).unwrap()
        self.clock.advance(timedelta(seconds=20))
        fresh = self.proposal(proposal_id="repair-new-ab12", lifetime_seconds=900).unwrap()
        self.assertEqual(self.repairs.resolve_proposal_id(self.important_context, "ab12").unwrap(), fresh.proposal_id)

    def test_shared_identity_and_grant_revision_changes_invalidate_basis_token(self):
        from unittest.mock import patch

        proposal = self.proposal(proposal_id="repair-shared-55cc").unwrap()
        with patch("companion_v2.relationships.account_link_identity.effective",
                   return_value={"enabled": True, "tier": "core", "permissions": {}}):
            stale = self.repairs.read(self.important_context, proposal_id=proposal.proposal_id).unwrap()
            self.assertEqual(stale.state, "stale")


if __name__ == "__main__":
    unittest.main()
