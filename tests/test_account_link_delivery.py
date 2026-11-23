"""Real existing contact/task/outbox integration with synthetic wire receipts."""
import asyncio
from dataclasses import replace
from datetime import timedelta
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from companion_v2.delivery.service import DeliveryRequest
from companion_v2.execution.models import TaskError, require
from companion_v2.proactive.configuration import target_for
from companion_v2.relationships.account_link_delivery import PrivateAccountDelivery, PREFIX, CONTACT_PREFIX, weighted_order
from companion_v2.relationships.account_links import PrivateAccountLinks, member_for, member_key
from tests.proactive_support import ContactFixture
from tests import test_proactive_contacts as contacts_support
from tests.test_role_tool_loop import answered


class AccountLinkDeliveryTests(ContactFixture, unittest.IsolatedAsyncioTestCase):
    async def ready(self, *, mode="specified", available=None):
        app = await self.enabled()
        self.a, self.b = self.contact_context(), self.other_window_context()
        self.settings(app, {"enabled": True, "quiet_enabled": False}, target=target_for(self.b)).unwrap()
        self.settings(app, {"quiet_enabled": False}).unwrap()
        app.private_account_link_persona = lambda member: "persona"
        self.runtime.private_account_link_persona = app.private_account_link_persona
        PrivateAccountLinks(self.runtime).configure(self.a, (self.a,self.b), persona_id="persona", expected_revision=0,
            share_context=False, operation_key="associate", operator_id="admin", at=self.clock.now(),
            proactive={"mode": mode, "preferred": member_for(self.b) if mode == "specified" else None})
        self.resolve = lambda member: self.a if member == member_for(self.a) else self.b
        router = PrivateAccountDelivery(app, self.resolve, available=available,
            rng=NS(shuffle=lambda members: members.reverse()))
        app.outbound.private_account_router = router
        return app

    async def test_linked_diagnostic_bypasses_zero_quota_without_changing_configuration(self):
        app = await self.ready()
        for context in (self.a, self.b):
            self.settings(app, {"max_contacts_24h": 0}, target=target_for(context)).unwrap()
        from companion_v2.relationships.account_link_settings import seed_values
        links = PrivateAccountLinks(self.runtime)
        links.configure(self.a, (self.a, self.b), persona_id="persona", expected_revision=links.read()["revision"],
            share_context=False, operation_key="share-zero-quota", operator_id="admin", at=self.clock.now(),
            sharing={"profile": [], "reply": [], "contact": ["proactive.contact.max_contacts_24h"]},
            seed=member_for(self.a), initial_values=seed_values(app, self.a))
        before = links.read()
        task = (await self.linked_diagnostic(app)).unwrap().task
        await self.delivery_state(app, task.task_id, "delivered")
        self.assertEqual([e.context.key for e in self.sent], [self.b.key])
        self.assertEqual(links.read(), before)
        for context in (self.a, self.b):
            preferences = require(app.proactive.preferences.read(context, observed_at=self.clock.now()))
            self.assertEqual(preferences.values["max_contacts_24h"], 0)
            with self.assertRaises(TaskError) as caught:
                app.proactive.budget.contacts(context, preferences, self.clock.now())
            self.assertEqual(caught.exception.code, "contact_delivery_budget")

    async def test_forwarded_diagnostic_does_not_consume_destination_production_quota(self):
        app = await self.ready()
        self.settings(app, {"max_contacts_24h": 1}, target=target_for(self.b)).unwrap()
        task = (await self.linked_diagnostic(app)).unwrap().task
        await self.delivery_state(app, task.task_id, "delivered")
        preferences = require(app.proactive.preferences.read(self.b, observed_at=self.clock.now()))
        app.proactive.budget.contacts(self.b, preferences, self.clock.now())

    async def test_production_route_still_enforces_candidate_quota_and_reports_it(self):
        app = await self.ready()
        self.settings(app, {"max_contacts_24h": 0}, target=target_for(self.b)).unwrap()
        task = (await self.contact(app)).unwrap().task
        await self.delivery_state(app, task.task_id, "delivered")
        self.assertEqual([e.context.key for e in self.sent], [self.a.key])
        observation = require(app.proactive.observe(self.a, task.task_id))
        self.assertTrue(any(r["member"] == member_for(self.b) and r["code"] == "contact_delivery_budget"
                            for r in observation["route_rejections"]))

    async def test_diagnostic_quota_exemption_does_not_bypass_quiet_hours(self):
        app = await self.ready()
        self.settings(app, {"quiet_enabled": True, "quiet_start": "00:00", "quiet_end": "00:00"},
                      target=target_for(self.b)).unwrap()
        task = (await self.linked_diagnostic(app)).unwrap().task
        await self.delivery_state(app, task.task_id, "delivered")
        self.assertEqual([e.context.key for e in self.sent], [self.a.key])
        observation = require(app.proactive.observe(self.a, task.task_id))
        self.assertTrue(any(r["code"] == "proactive_quiet_hours" for r in observation["route_rejections"]))

    async def test_offline_diagnostic_retains_per_account_rejections_after_router_restart(self):
        app = await self.ready(available=lambda context: False)
        task = (await self.linked_diagnostic(app)).unwrap().task
        await self.until(lambda: bool(app.proactive._delivery_rows(task.task_id)))
        row = app.proactive._delivery_rows(task.task_id)[0]
        router = app.outbound.private_account_router
        await self.until(lambda: bool(router.rejection_report(row["delivery_id"])))
        restored = PrivateAccountDelivery(app, self.resolve)
        rejections = restored.rejection_report(row["delivery_id"])
        self.assertEqual(len(rejections), 2)
        self.assertEqual({r["code"] for r in rejections}, {"account_link_transport_unavailable"})
        self.assertIsNone(restored._read(PREFIX + row["delivery_id"]), "readiness must not create a wire journal")
        self.assertEqual(self.sent, [])
        self.assertIsNone(app.outbound._row(row["delivery_id"])["operation_id"])

    async def test_administrator_diagnostic_is_pinned_despite_linked_preferred_recipient(self):
        app = await self.ready()
        task = (await self.contact(app, administrator_test=True)).unwrap().task
        await self.delivery_state(app, task.task_id, "delivered")
        self.assertEqual([envelope.context.key for envelope in self.sent], [self.a.key])
        row = app.proactive._delivery_rows(task.task_id)[0]
        self.assertIsNone(app.outbound.private_account_router._read(PREFIX + row["delivery_id"]))

    async def linked_diagnostic(self, app):
        opportunity = self.opportunity()
        return await app.proactive.contact(self.contact_context(), opportunity, (self.intent(opportunity),),
            role_instruction="这是原角色人格，保持自然。", selection_seed="linked-diagnostic",
            administrator_test=True, diagnostic_allow_linked_accounts=True)

    async def test_opt_in_diagnostic_uses_linked_preferred_account_and_reports_actual_destination(self):
        app = await self.ready()
        task = (await self.linked_diagnostic(app)).unwrap().task
        await self.delivery_state(app, task.task_id, "delivered")
        self.assertEqual([envelope.context.key for envelope in self.sent], [self.b.key])
        from astrbot_host.setup_api import SetupWebAPI
        destinations = SetupWebAPI._diagnostic_delivery_targets(NS(application=app), self.a, task.task_id)
        self.assertEqual(destinations, [{"platform_id": self.b.scope.platform,
            "account_id": self.b.scope.account_id, "participant_id": self.b.scope.native_scope_id,
            "delivery_state": "delivered"}])

    async def test_opt_in_diagnostic_preserves_weighted_selection(self):
        app = await self.weighted_ready(0, 5)
        task = (await self.linked_diagnostic(app)).unwrap().task
        await self.delivery_state(app, task.task_id, "delivered")
        self.assertEqual([envelope.context.key for envelope in self.sent], [self.b.key])

    async def test_opt_in_diagnostic_preserves_safe_fallback(self):
        app = await self.ready()
        async def send(envelope):
            return self.receipt(envelope, "not_sent" if len(self.sent) == 1 else "delivered")
        self.send_behavior = send
        task = (await self.linked_diagnostic(app)).unwrap().task
        await self.delivery_state(app, task.task_id, "delivered")
        self.assertEqual([envelope.context.key for envelope in self.sent], [self.b.key, self.a.key])

    async def test_opt_in_diagnostic_never_falls_back_after_unknown_delivery(self):
        app = await self.ready()
        async def send(envelope):
            return self.receipt(envelope, "unknown")
        self.send_behavior = send
        task = (await self.linked_diagnostic(app)).unwrap().task
        await self.delivery_state(app, task.task_id, "unknown")
        self.assertEqual([envelope.context.key for envelope in self.sent], [self.b.key])

    async def test_forwarding_consent_is_strict_and_only_valid_for_administrator_tests(self):
        app = await self.ready()
        opportunity = self.opportunity()
        for administrator_test, flag in ((False, True), (True, "true"), (True, 1)):
            with self.assertRaises(ValueError):
                await app.proactive.contact(self.a, opportunity, (self.intent(opportunity),),
                    role_instruction="测试人格", selection_seed="invalid-forwarding",
                    administrator_test=administrator_test, diagnostic_allow_linked_accounts=flag)
        self.assertEqual(self.sent, [])

    async def test_confirmed_not_sent_falls_back_in_same_outbox_once(self):
        app = await self.ready()
        async def send(envelope):
            return self.receipt(envelope, "not_sent" if len(self.sent) == 1 else "delivered")
        self.send_behavior = send
        task = (await self.contact(app)).unwrap().task
        await self.delivery_state(app, task.task_id, "delivered")
        self.assertEqual([e.context.key for e in self.sent], [self.b.key, self.a.key])
        rows = app.proactive._delivery_rows(task.task_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["origin_key"], self.a.key)
        journal = app.outbound.private_account_router._read(PREFIX + rows[0]["delivery_id"])
        self.assertEqual([a["state"] for a in journal["attempts"]], ["not_sent", "delivered"])
        # Repeating the original opportunity cannot create a second successful delivery.
        await self.contact(app)
        self.assertEqual(len(self.sent), 2)

    async def test_unknown_receipt_never_tries_another_account(self):
        app = await self.ready()
        async def send(envelope):
            return self.receipt(envelope, "unknown")
        self.send_behavior = send
        task = (await self.contact(app)).unwrap().task
        await self.delivery_state(app, task.task_id, "unknown")
        self.assertEqual([e.context.key for e in self.sent], [self.b.key])
        await self.contact(app)
        self.assertEqual(len(self.sent), 1)

    async def test_exception_is_unknown_not_a_fallback_signal(self):
        app = await self.ready()
        async def send(envelope):
            raise TimeoutError("ambiguous wire timeout")
        self.send_behavior = send
        task = (await self.contact(app)).unwrap().task
        await self.delivery_state(app, task.task_id, "unknown")
        self.assertEqual(len(self.sent), 1)

    async def test_offline_preferred_account_is_skipped(self):
        app = await self.ready(available=lambda context: context.key == self.a.key)
        task = (await self.contact(app)).unwrap().task
        await self.delivery_state(app, task.task_id, "delivered")
        self.assertEqual([e.context.key for e in self.sent], [self.a.key])

    async def test_local_quiet_hours_are_not_bypassed_by_fallback(self):
        app = await self.ready()
        self.settings(app, {"quiet_enabled": True, "quiet_start": "00:00", "quiet_end": "00:00"}, target=target_for(self.b)).unwrap()
        task = (await self.contact(app)).unwrap().task
        await self.delivery_state(app, task.task_id, "delivered")
        self.assertEqual([e.context.key for e in self.sent], [self.a.key])

    async def weighted_ready(self, a_weight, b_weight, **kwargs):
        app = await self.ready(mode="random", **kwargs)
        links = PrivateAccountLinks(self.runtime)
        links.configure(self.a, [self.a, self.b], persona_id="persona", expected_revision=links.read()["revision"],
            share_context=False, operation_key="weights", operator_id="admin", at=self.clock.now(),
            proactive={"mode": "random", "preferred": None, "weights": [
                {"member": member_for(self.a), "weight": a_weight},
                {"member": member_for(self.b), "weight": b_weight}]})
        app.outbound.private_account_router.rng = NS(randrange=lambda total: 0)
        return app

    async def test_zero_weight_never_selected_or_used_as_fallback(self):
        app = await self.weighted_ready(0, 5)
        async def send(envelope):
            return self.receipt(envelope, "not_sent")
        self.send_behavior = send
        task = (await self.contact(app)).unwrap().task
        await self.delivery_state(app, task.task_id, "failed")
        self.assertEqual([e.context.key for e in self.sent], [self.b.key])

    async def test_high_weight_cannot_override_quiet_veto(self):
        app = await self.weighted_ready(1, 1000)
        self.settings(app, {"quiet_enabled": True, "quiet_start": "00:00", "quiet_end": "00:00"}, target=target_for(self.b)).unwrap()
        task = (await self.contact(app)).unwrap().task
        await self.delivery_state(app, task.task_id, "delivered")
        self.assertEqual([e.context.key for e in self.sent], [self.a.key])

    async def test_pinned_account_is_retained_even_after_its_weight_becomes_zero(self):
        app = await self.weighted_ready(0, 5)
        router = app.outbound.private_account_router
        with self.runtime.transaction() as connection:
            router._put(connection, CONTACT_PREFIX + "pinned-weight-test", {"member": member_for(self.a)})
        result = router._candidates(self.a, PrivateAccountLinks(self.runtime).group_for(self.a),
            "care", NS(logical_key="pinned-weight-test"))
        self.assertEqual([context.key for context in result], [self.a.key])

    async def test_random_picks_one_eligible_route_and_never_fanouts(self):
        app = await self.ready(mode="random")
        task = (await self.contact(app)).unwrap().task
        await self.delivery_state(app, task.task_id, "delivered")
        self.assertEqual(len(self.sent), 1)

    async def test_all_unavailable_uses_original_supervisor_backoff_before_effect(self):
        app = await self.ready(available=lambda context: False)
        task = (await self.contact(app)).unwrap().task
        await self.until(lambda: bool(app.proactive._delivery_rows(task.task_id)))
        row = app.proactive._delivery_rows(task.task_id)[0]
        await self.until(lambda: require(app.tasks.read(self.a, row["task_id"])).state == "retry_scheduled")
        self.assertEqual(self.sent, [])
        current = app.outbound._row(row["delivery_id"])
        self.assertEqual(current["state"], "pending")
        self.assertIsNone(current["operation_id"])

    async def test_shared_contact_enabled_does_not_override_local_disabled(self):
        app = await self.ready()
        self.settings(app, {"enabled": False}, target=target_for(self.b)).unwrap()
        from companion_v2.relationships.account_link_settings import seed_values
        links = PrivateAccountLinks(self.runtime)
        links.configure(self.a, (self.a,self.b), persona_id="persona", expected_revision=links.read()["revision"],
            share_context=False, operation_key="share-contact", operator_id="admin", at=self.clock.now(),
            sharing={"profile": [], "reply": [], "contact": ["proactive.contact.enabled"]},
            seed=member_for(self.a), initial_values=seed_values(app,self.a))
        preferences = require(app.proactive.preferences.read(self.b, observed_at=self.clock.now()))
        self.assertTrue(preferences.values["enabled"])
        self.assertEqual(preferences.reason("chat"), "proactive_disabled")
        task = (await self.contact(app)).unwrap().task
        await self.delivery_state(app, task.task_id, "delivered")
        self.assertEqual([e.context.key for e in self.sent], [self.a.key])

    async def test_routed_arrivals_count_towards_destination_local_quota(self):
        app = await self.ready()
        self.settings(app, {"max_contacts_24h": 1}, target=target_for(self.b)).unwrap()
        task = (await self.contact(app)).unwrap().task
        await self.delivery_state(app, task.task_id, "delivered")
        self.assertEqual(self.sent[0].context.key, self.b.key)
        preferences = require(app.proactive.preferences.read(self.b, observed_at=self.clock.now()))
        with self.assertRaises(TaskError) as error:
            app.proactive.budget.contacts(self.b, preferences, self.clock.now())
        self.assertEqual(error.exception.code, "contact_delivery_budget")

    async def test_delivered_first_part_pins_remaining_parts_to_same_account(self):
        app = await self.ready()
        contacts_support.ProactiveContactTests.presentation_settings(self, app, {"enabled": True, "llm_controlled": True,
            "part_gap_mode": "fixed", "part_gap_ms": 0}).unwrap()
        async def model(envelope):
            return answered(json.dumps({"decision": "send", "text": "first part.\n<<PRIVATE_COMPANION_SPLIT>>\nsecond part."}))
        async def send(envelope):
            return self.receipt(envelope, "delivered" if len(self.sent) == 1 else "not_sent")
        self.model_behavior, self.send_behavior = model, send
        task = (await self.contact(app)).unwrap().task
        await self.until(lambda: len(self.sent) >= 2)
        await self.until(lambda: any(row["state"] == "failed" for row in app.proactive._delivery_rows(task.task_id)))
        self.assertEqual([e.context.key for e in self.sent], [self.b.key, self.b.key])

    async def test_aggregate_history_includes_confirmed_outbox_with_original_owner(self):
        from companion_v2.relationships.account_link_history import history
        app = await self.ready()
        task = (await self.contact(app)).unwrap().task
        await self.delivery_state(app, task.task_id, "delivered")
        result = history(app, self.b, at=self.clock.now())
        rows = [row for row in result["records"] if row["record_type"] == "background"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["subject_id"], self.a.actor.subject.subject_id)
        self.assertEqual(rows[0]["scope"], self.a.scope.document())
        self.assertEqual(rows[0]["delivery_scope"], self.b.scope.document())
        self.assertTrue(rows[0]["text"])

    async def test_late_target_receipt_reconciles_unknown_without_cross_resend(self):
        from companion_v2.relationships.account_link_delivery import PrivateAccountDelivery
        app = await self.ready()
        async def send(envelope):
            return self.receipt(envelope, "unknown")
        self.send_behavior = send
        task = (await self.contact(app)).unwrap().task
        await self.delivery_state(app, task.task_id, "unknown")
        envelope = self.sent[0]
        # A new router instance reads the durable wire journal after restart.
        app.outbound.private_account_router = PrivateAccountDelivery(app, self.resolve)
        receipt = replace(self.receipt(envelope, "delivered"), receipt_ref="late-confirmation")
        app.outbound.record_receipt(receipt)
        row = app.proactive._delivery_rows(task.task_id)[0]
        self.assertEqual(row["state"], "delivered")
        self.assertEqual(row["origin_key"], self.a.key)
        self.assertEqual(app.outbound.private_account_router.actual_context(row["delivery_id"]).key, self.b.key)
        self.assertEqual(app.outbound.private_account_router._read(PREFIX + row["delivery_id"])["state"], "delivered")
        await self.contact(app)
        self.assertEqual(len(self.sent), 1)


class WeightedOrderTests(unittest.TestCase):
    def test_exact_three_to_one_first_choice_and_no_duplicate_fallbacks(self):
        from tests.test_private_account_links import context
        a, b = context("a"), context("b")
        weights = {member_key(member_for(a)): 3, member_key(member_for(b)): 1}
        first = []
        for ticket in range(4):
            draws = iter([ticket, 0])
            order = weighted_order([a, b], weights, NS(randrange=lambda total: next(draws)))
            first.append(order[0])
            self.assertEqual(len(order), 2)
            self.assertIsNot(order[0], order[1])
        self.assertEqual(first.count(a), 3)
        self.assertEqual(first.count(b), 1)

    def test_zero_and_unavailable_members_do_not_dilute_probability(self):
        from tests.test_private_account_links import context
        a, b, c = context("a"), context("b"), context("c")
        weights = {member_key(member_for(a)): 0, member_key(member_for(b)): 1,
                   member_key(member_for(c)): 1000}
        totals = []
        order = weighted_order([a, b], weights, NS(randrange=lambda total: totals.append(total) or 0))
        self.assertEqual(order, [b])
        self.assertEqual(totals, [1])
