from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import json
import unittest
from unittest.mock import patch

from companion_v2.execution.models import (
    Lane, ProviderBinding, SideEffect, SupervisorLimits, TaskContext, TaskDefinition,
    TaskRegistration, TaskResult, TaskSpec,
)
from companion_v2.execution.service import TaskSupervisor
from companion_v2.foundation.clock import DeterministicClock
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import AudienceRef, SubjectKind, SubjectRef
from companion_v2.identity_policy.personas import ConversationRoute
from tests.test_management_access import INSTALLATION, ManagementFixture, NOW, SCOPE


class ExecutionFixture(ManagementFixture):
    def setUp(self):
        super().setUp()
        self.clock = DeterministicClock.at(NOW)
        self.inputs_current = True
        self.foreground = TaskDefinition("fixture.read", "fixture", "configuration.read", Lane.FOREGROUND,
                                         timeout_seconds=10, backoff_seconds=1)
        self.background = replace(self.foreground, kind="fixture.background", lane=Lane.BACKGROUND)
        self.effectful = replace(self.foreground, kind="fixture.effect", side_effect=SideEffect.LOCAL,
                                binding=ProviderBinding("fixture.commit", "fixture", "1", "provider-1"))
        self.registrations = tuple(TaskRegistration(d, self.validate_inputs)
                                   for d in (self.foreground, self.background, self.effectful))
        self.limits = SupervisorLimits(foreground_slots=2, background_slots=1, queued_per_lane=8, retained_tasks=20)
        self.tasks = self.supervisor()
        self.tasks.start(expected_generation=0).unwrap()
        self.context = self.context_for(self.important)

    def validate_inputs(self, spec, now):
        return Outcome.success(True) if self.inputs_current else Outcome.failed("input_expired", "original fixture input expired")

    def supervisor(self, runtime=None, registrations=None, limits=None):
        return TaskSupervisor(runtime or self.runtime, registrations or self.registrations,
                              limits=limits or self.limits, now=self.clock.now)

    def context_for(self, actor, scope=None):
        scope = scope or (SCOPE if actor == self.important else replace(SCOPE, native_scope_id=actor.subject.subject_id,
                    conversation_id="conversation-" + actor.subject.subject_id, session_id="session-" + actor.subject.subject_id))
        if self.personas.route(scope) is None:
            self.personas.register_route(ConversationRoute(scope, user=actor.subject), observed_at=self.clock.now())
        return TaskContext(actor, self.identity.resolve_role(scope, observed_at=self.clock.now()).unwrap(), AudienceRef(scope, (actor.subject,)))

    def spec(self, identifier="task-1", *, kind="fixture.read", context=None, **changes):
        return replace(TaskSpec(identifier, context or self.context, kind, "fixture:original-input", "key-" + identifier,
                                self.clock.now(), self.clock.now() + timedelta(minutes=5)), **changes)

    def submit(self, *args, **kwargs):
        spec = self.spec(*args, **kwargs)
        return self.tasks.submit(spec).unwrap()

    def claim(self, lane=Lane.FOREGROUND):
        claim = self.tasks.claim(lane).unwrap().claim
        self.assertIsNotNone(claim)
        return claim

    def running(self, identifier="task-1", **kwargs):
        self.submit(identifier, **kwargs)
        claim = self.claim(Lane.BACKGROUND if kwargs.get("kind") == "fixture.background" else Lane.FOREGROUND)
        self.tasks.begin(claim).unwrap()
        return claim

    def read_task(self, task_id="task-1"):
        return self.tasks.read(self.context, task_id).unwrap()


class ExecutionTests(ExecutionFixture):
    def test_task_capacity_bounds_match_host_configuration_bounds(self):
        limits = SupervisorLimits(queued_per_lane=10_000, retained_tasks=100_000)
        self.assertEqual((limits.queued_per_lane, limits.retained_tasks), (10_000, 100_000))
        for values in ({"queued_per_lane": 10_001}, {"retained_tasks": 100_001}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                replace(self.limits, **values)

    def test_start_adopts_only_safe_retained_task_capacity_increases(self):
        saved = self.tasks.submit(self.spec()).unwrap()
        expanded = replace(self.limits, retained_tasks=self.limits.retained_tasks + 10)
        next_supervisor = self.supervisor(limits=expanded)

        self.assertEqual(next_supervisor.start(expected_generation=1).unwrap(), 2)
        self.assertEqual(next_supervisor.read(self.context, saved.task_id).unwrap(), saved)
        limits = json.loads(self.runtime.read_one("SELECT limits_json FROM task_supervisor", ())['limits_json'])
        self.assertEqual(limits["retained_tasks"], expanded.retained_tasks)

    def test_start_adopts_lane_and_queue_capacity_increases_without_clearing_work(self):
        running = self.running(kind="fixture.effect")
        self.tasks.begin_effect(running).unwrap()
        self.tasks.record_operation(running, "original-operation").unwrap()
        queued = self.submit("queued", kind="fixture.background")
        expanded = replace(self.limits, foreground_slots=3, background_slots=3, queued_per_lane=16)
        next_supervisor = self.supervisor(limits=expanded)

        self.assertEqual(next_supervisor.start(expected_generation=1).unwrap(), 2)
        self.assertEqual(next_supervisor.read(self.context, queued.task_id).unwrap(), queued)
        self.assertEqual(self.tasks.check_generation().failure.code, "supervisor_fenced")
        self.assertEqual(next_supervisor.recover().unwrap(), (running.task_id,))
        retained = next_supervisor.read(self.context, running.task_id).unwrap()
        self.assertEqual((retained.state, retained.operation_id, retained.effect),
                         ("uncertain", "original-operation", "unknown"))
        self.assertTrue(next_supervisor.observe(self.context, running.task_id).unwrap()["slot_held"])
        self.assertEqual(next_supervisor.claim(Lane.BACKGROUND).unwrap().claim.task_id, queued.task_id)
        attached = self.supervisor(limits=expanded)
        self.assertEqual(attached.attach(expected_generation=2).unwrap(), 2)
        self.assertEqual(self.supervisor().attach(expected_generation=2).failure.code,
                         "supervisor_generation_conflict")

    def test_start_rejects_capacity_decreases_even_when_another_capacity_increases(self):
        for changed in (replace(self.limits, retained_tasks=self.limits.retained_tasks - 1),
                        replace(self.limits, foreground_slots=self.limits.foreground_slots - 1),
                        replace(self.limits, queued_per_lane=self.limits.queued_per_lane - 1,
                                background_slots=self.limits.background_slots + 1)):
            with self.subTest(changed=changed):
                next_supervisor = self.supervisor(limits=changed)
                self.assertEqual(next_supervisor.start(expected_generation=1).failure.code,
                                 "supervisor_limits_changed")
                self.assertEqual(self.tasks.check_generation().unwrap(), 1)

    def test_large_trusted_catalog_preserves_uniqueness_and_execution_limits(self):
        registrations = tuple(TaskRegistration(replace(self.foreground, kind=f"fixture.domain-{index}"),
                                                self.validate_inputs) for index in range(96))
        tasks = TaskSupervisor(self.runtime, registrations, limits=self.limits, now=self.clock.now)
        self.assertEqual(len(tasks.registrations), 96)
        self.assertEqual(tasks.limits, self.limits)
        for invalid in ((), registrations + (registrations[0],)):
            with self.assertRaisesRegex(ValueError, "unique task kinds"):
                TaskSupervisor(self.runtime, invalid)

    def test_submit_deduplicates_across_proposed_task_ids_without_inventing_an_operation(self):
        spec = self.spec()
        saved = self.tasks.submit(spec).unwrap()
        again = self.tasks.submit(replace(spec, task_id="another-proposed-id")).unwrap()
        self.assertEqual(saved, again)
        self.assertIsNone(saved.operation_id)
        self.assertEqual(self.tasks.submit(replace(spec, request_ref="different")).failure.code, "task_idempotency_conflict")
        self.assertEqual(self.runtime.read_one("SELECT COUNT(*) n FROM operations", ())["n"], 0)

    def test_claim_begin_result_and_actual_queue_observations(self):
        self.submit()
        self.clock.advance(timedelta(seconds=2))
        claim = self.claim()
        self.tasks.begin(claim).unwrap()
        self.assertEqual(self.tasks.begin(claim).failure.code, "task_already_started")
        result = self.tasks.finish(claim, TaskResult("succeeded", result_ref="owner:private-result"), execution_ms=12).unwrap()
        self.assertEqual((result.state, result.operation_id), ("succeeded", None))
        projection = self.tasks.observe(self.context, result.task_id).unwrap()
        self.assertEqual((projection["queue_ms"], projection["execution_ms"], projection["slot_held"]), (2000, 12, False))
        self.assertNotIn("owner:private-result", json.dumps(projection))
        self.assertIsNone(projection["provider_latency_ms"])
        self.assertEqual([r["state"] for r in self.runtime.read_all("SELECT state FROM execution_events ORDER BY revision", ())],
                         ["created", "admitted", "running", "succeeded"])

    def test_two_connections_cannot_claim_same_work(self):
        self.submit()
        other = self.supervisor(self.open_runtime())
        other.attach(expected_generation=1).unwrap()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = tuple(pool.map(lambda supervisor: supervisor.claim(Lane.FOREGROUND).unwrap().claim, (self.tasks, other)))
        self.assertEqual(sum(item is not None for item in results), 1)
        self.assertEqual(self.runtime.read_one("SELECT COUNT(*) n FROM execution_attempts", ())["n"], 1)

    def test_foreground_background_scope_and_queue_reservations_are_separate(self):
        self.running("bg1", kind="fixture.background")
        self.submit("bg2", kind="fixture.background")
        self.assertIsNone(self.tasks.claim(Lane.BACKGROUND).unwrap().claim)
        first = self.running()
        self.submit("same-scope")
        other = self.context_for(self.core)
        self.submit("other-scope", context=other)
        second = self.claim()
        self.assertEqual(second.task_id, "other-scope")
        self.assertIsNone(self.tasks.claim(Lane.FOREGROUND).unwrap().claim)
        self.tasks.finish(first, TaskResult("succeeded")).unwrap()
        self.assertEqual(self.claim().task_id, "same-scope")

    def test_queue_cap_counts_inflight_reservations_for_safe_retry(self):
        for number in range(self.limits.queued_per_lane):
            self.submit(f"task-{number}")
        claim = self.claim()
        self.tasks.begin(claim).unwrap()
        self.assertEqual(self.tasks.submit(self.spec("overflow")).failure.code, "task_queue_full")
        self.tasks.finish(claim, TaskResult("failed", retryable=True)).unwrap()
        self.assertEqual(self.tasks.submit(self.spec("still-full")).failure.code, "task_queue_full")
        self.assertTrue(self.tasks.submit(self.spec("background-space", kind="fixture.background")).is_success)

    def test_safe_retry_preserves_task_operation_and_attempt_budget_across_restart(self):
        claim = self.running(kind="fixture.effect")
        self.tasks.begin_effect(claim).unwrap()
        self.tasks.record_operation(claim, "owner-operation").unwrap()
        first = self.tasks.finish(claim, TaskResult("failed", "not_committed", receipt_ref="owner:no-commit",
                                                 operation_id="owner-operation", retryable=True)).unwrap()
        self.assertEqual((first.state, first.attempt_no), ("retry_scheduled", 1))
        self.assertIsNone(self.tasks.claim(Lane.FOREGROUND).unwrap().claim)
        other = self.supervisor(self.open_runtime())
        other.start(expected_generation=1).unwrap()
        self.clock.advance(timedelta(seconds=1))
        next_claim = other.claim(Lane.FOREGROUND).unwrap().claim
        next_task = other.begin(next_claim).unwrap()
        self.assertEqual((next_task.task_id, next_task.operation_id, next_task.attempt_no), ("task-1", "owner-operation", 2))
        other.begin_effect(next_claim).unwrap()
        last = other.finish(next_claim, TaskResult("failed", "not_committed", receipt_ref="owner:no-commit-2", retryable=True)).unwrap()
        self.assertEqual(last.state, "dead_letter")
        self.assertIsNone(other.claim(Lane.FOREGROUND).unwrap().claim)
        attempts = other.runtime.read_all("SELECT record_json FROM execution_attempts ORDER BY attempt_no", ())
        original, retried = (json.loads(row["record_json"]) for row in attempts)
        self.assertEqual(original["owner_result"]["receipt_ref"], "owner:no-commit")
        self.assertEqual(retried["owner_result"]["receipt_ref"], "owner:no-commit-2")
        self.assertEqual((original["operation_id"], retried["operation_id"]), ("owner-operation", "owner-operation"))
        self.assertLess(original["result_recorded_at"], retried["result_recorded_at"])

    def test_cancel_during_backoff_preserves_completed_attempt_evidence(self):
        claim = self.running(kind="fixture.effect")
        self.tasks.begin_effect(claim).unwrap()
        self.clock.advance(timedelta(seconds=1))
        retry = self.tasks.finish(claim, TaskResult("failed", "not_committed", receipt_ref="owner:no-commit",
                                                  operation_id="original-operation", retryable=True), execution_ms=12).unwrap()
        before = self.runtime.read_one("SELECT * FROM execution_attempts WHERE attempt_id = ?", (claim.attempt_id,))
        self.clock.advance(timedelta(seconds=0.5))
        cancelled = self.tasks.cancel(self.context, retry.task_id, expected_revision=retry.revision).unwrap()
        after = self.runtime.read_one("SELECT * FROM execution_attempts WHERE attempt_id = ?", (claim.attempt_id,))
        self.assertEqual(cancelled.state, "cancelled")
        self.assertEqual(dict(before), dict(after))
        self.assertEqual(json.loads(after["record_json"])["stopped_proof"], "worker_returned")
        self.assertIsNone(self.tasks.claim(Lane.FOREGROUND).unwrap().claim)

    def test_owner_validator_exception_is_redacted_and_does_not_accept_work(self):
        broken = True

        def validator(spec, now):
            if broken:
                raise RuntimeError("private validator payload")
            return Outcome.success(True)

        registrations = tuple(TaskRegistration(entry.definition, validator) for entry in self.registrations)
        self.tasks = self.supervisor(registrations=registrations)
        self.tasks.attach(expected_generation=1).unwrap()
        rejected = self.tasks.submit(self.spec())
        self.assertEqual(rejected.failure.code, "task_validation_failed")
        self.assertNotIn("private validator payload", rejected.failure.reason)
        self.assertEqual(self.runtime.read_one("SELECT COUNT(*) n FROM execution_tasks", ())["n"], 0)
        broken = False
        claim = self.running()
        broken = True
        self.assertEqual(self.tasks.validate(claim).failure.code, "task_validation_failed")
        self.assertEqual(self.tasks.finish(claim, TaskResult("succeeded")).failure.code, "task_validation_failed")
        self.assertEqual(self.read_task().state, "running")
        stopped = self.tasks.stop(claim, reason="permission_changed").unwrap()
        self.assertEqual((stopped.state, stopped.result_ref), ("failed", None))
        observed = self.tasks.observe(self.context, stopped.task_id).unwrap()
        self.assertFalse(observed["slot_held"])
        self.assertNotIn("private validator payload", json.dumps(observed))

    def test_restart_recovers_unstarted_claim_but_retains_unknown_running_effect(self):
        running = self.running(kind="fixture.effect")
        self.tasks.begin_effect(running).unwrap()
        self.tasks.record_operation(running, "accepted-owner-operation").unwrap()
        self.submit("unstarted", kind="fixture.background")
        unstarted = self.claim(Lane.BACKGROUND)
        self.submit("queued")
        other = self.supervisor(self.open_runtime())
        other.start(expected_generation=1).unwrap()
        self.assertEqual(set(other.recover().unwrap()), {"task-1", "unstarted"})
        self.assertEqual(self.tasks.finish(running, TaskResult("succeeded", "committed", receipt_ref="late", operation_id="accepted-owner-operation")).failure.code,
                         "supervisor_fenced")
        unknown = other.read(self.context, "task-1").unwrap()
        self.assertEqual((unknown.state, unknown.operation_id, unknown.effect), ("uncertain", "accepted-owner-operation", "unknown"))
        self.assertTrue(other.observe(self.context, "task-1").unwrap()["slot_held"])
        resumed = other.claim(Lane.BACKGROUND).unwrap().claim
        self.assertEqual(resumed.task_id, "unstarted")
        self.assertNotEqual(resumed.attempt_id, unstarted.attempt_id)
        self.assertEqual(other.claim(Lane.FOREGROUND).unwrap().claim.task_id, "queued")
        known = TaskResult("succeeded", "committed", receipt_ref="original:confirmed", operation_id="accepted-owner-operation")
        self.assertEqual(other.reconcile(self.context, "task-1", expected_revision=unknown.revision, result=known).failure.code,
                         "task_worker_not_stopped")
        self.tasks.acknowledge_stopped(running, proof_ref="owner-worker-terminated").unwrap()
        self.assertEqual(other.read(self.context, "task-1").unwrap().state, "uncertain")
        reconciled = other.reconcile(self.context, "task-1", expected_revision=unknown.revision, result=known).unwrap()
        self.assertEqual(reconciled.state, "succeeded")
        self.assertEqual(other.reconcile(self.context, "task-1", expected_revision=unknown.revision, result=known).failure.code,
                         "task_revision_conflict")

    def test_lease_expiry_neither_proves_stop_nor_permits_a_second_submit(self):
        claim = self.running(kind="fixture.effect")
        self.tasks.begin_effect(claim).unwrap()
        self.clock.advance(timedelta(seconds=10))
        self.assertEqual(self.tasks.finish(claim, TaskResult("uncertain", "unknown")).failure.code, "task_attempt_expired")
        self.tasks.recover().unwrap()
        self.assertEqual(self.read_task().state, "uncertain")
        self.assertIsNone(self.tasks.claim(Lane.FOREGROUND).unwrap().claim)
        self.assertEqual(self.tasks.begin_effect(claim).failure.code, "task_attempt_fenced")

    def test_restart_releases_only_superseded_read_only_slots_without_claiming_workers_stopped(self):
        self.submit("stale-1")
        first = self.claim()
        self.tasks.begin(first).unwrap()
        second_scope = replace(SCOPE, native_scope_id="native-b", conversation_id="conversation-b",
                               session_id="session-b")
        self.personas.register_route(ConversationRoute(second_scope, user=self.important.subject), observed_at=NOW)
        self.grant(self.important, second_scope, "configuration.read")
        second_context = self.context_for(self.important, scope=second_scope)
        self.submit("stale-2", context=second_context)
        second = self.claim()
        self.tasks.begin(second).unwrap()
        self.submit("queued")

        other = self.supervisor(self.open_runtime())
        other.start(expected_generation=1).unwrap()
        self.assertEqual(set(other.recover().unwrap()), {"stale-1", "stale-2"})

        for task_id, context in (("stale-1", self.context), ("stale-2", second_context)):
            observed = other.observe(context, task_id).unwrap()
            self.assertEqual(observed["state"], "uncertain")
            self.assertFalse(observed["slot_held"])
            self.assertFalse(observed["worker_stopped"])
            self.assertEqual(observed["capacity_release"], "superseded_read_only_generation")
            self.assertEqual(other.reconcile(context, task_id, expected_revision=observed["revision"],
                result=TaskResult("succeeded", result_ref="late-read-only-result")).failure.code,
                "task_worker_stop_unknown")

        self.assertEqual(other.claim(Lane.FOREGROUND).unwrap().claim.task_id, "queued")

    def test_process_exit_evidence_releases_unknown_effect_capacity_without_replay(self):
        self.submit(kind="fixture.effect")
        claim = self.claim()
        self.tasks.begin(claim).unwrap()
        self.tasks.begin_effect(claim).unwrap()
        self.tasks.record_operation(claim, "owner-accepted-operation").unwrap()
        self.submit("next-effect", kind="fixture.effect")

        class StoppedLifecycle:
            attempt = None

            def capture(self):
                raise AssertionError("recovery does not start workers")

            def stopped_proof(self, attempt):
                self.attempt = attempt
                return "fixture_process_exited"

        lifecycle = StoppedLifecycle()
        other = self.supervisor(self.open_runtime())
        other.bind_worker_lifecycle(lifecycle)
        other.start(expected_generation=1).unwrap()
        other.recover().unwrap()
        recovered = other.recover_stopped_workers().unwrap()
        self.assertEqual(recovered.released_tasks, ("task-1",))
        unknown = other.read(self.context, "task-1").unwrap()
        observed = other.observe(self.context, "task-1").unwrap()
        self.assertEqual((unknown.state, unknown.effect, unknown.operation_id),
                         ("uncertain", "unknown", "owner-accepted-operation"))
        self.assertFalse(observed["slot_held"])
        self.assertTrue(observed["worker_stopped"])
        self.assertEqual(lifecycle.attempt.claim, claim)
        self.assertEqual(other.claim(Lane.FOREGROUND).unwrap().claim.task_id, "next-effect")

    def test_cancel_before_start_is_final_running_cancel_requires_stop_and_commit_survives(self):
        queued = self.submit()
        cancelled = self.tasks.cancel(self.context, "task-1", expected_revision=queued.revision).unwrap()
        self.assertEqual(cancelled.state, "cancelled")
        claim = self.running("committing", kind="fixture.effect")
        self.tasks.begin_effect(claim).unwrap()
        current = self.read_task("committing")
        cancelling = self.tasks.cancel(self.context, "committing", expected_revision=current.revision).unwrap()
        self.assertEqual(cancelling.state, "cancelling")
        self.assertEqual(self.tasks.begin_effect(claim).failure.code, "task_cancellation_requested")
        committed = self.tasks.finish(claim, TaskResult("succeeded", "committed", receipt_ref="receipt:committed", operation_id="actual-owner-operation")).unwrap()
        self.assertEqual(committed.state, "succeeded")
        self.assertEqual(self.tasks.cancel(self.context, "committing", expected_revision=committed.revision).unwrap().state, "succeeded")

    def test_cancel_at_claim_and_known_not_committed_result(self):
        self.submit()
        claim = self.claim()
        current = self.read_task()
        self.tasks.cancel(self.context, "task-1", expected_revision=current.revision).unwrap()
        self.assertEqual(self.tasks.begin(claim).failure.code, "task_attempt_fenced")
        self.assertFalse(self.tasks.observe(self.context, "task-1").unwrap()["slot_held"])
        running = self.running("running", kind="fixture.effect")
        self.tasks.begin_effect(running).unwrap()
        current = self.read_task("running")
        self.tasks.cancel(self.context, "running", expected_revision=current.revision).unwrap()
        stopped = self.tasks.finish(running, TaskResult("failed", "not_committed", receipt_ref="owner:no-commit", retryable=True)).unwrap()
        self.assertEqual(stopped.state, "cancelled")

    def test_unknown_owner_result_is_never_retried_or_changed_by_cancel(self):
        claim = self.running(kind="fixture.effect")
        self.tasks.begin_effect(claim).unwrap()
        unknown = self.tasks.finish(claim, TaskResult("uncertain", "unknown")).unwrap()
        self.assertEqual(self.tasks.cancel(self.context, "task-1", expected_revision=unknown.revision).unwrap(), unknown)
        self.assertIsNone(self.tasks.claim(Lane.FOREGROUND).unwrap().claim)
        self.assertFalse(self.tasks.observe(self.context, "task-1").unwrap()["slot_held"])

    def test_role_switch_back_revocation_and_input_expiry_block_each_boundary(self):
        claim = self.running()
        original = self.context.role_binding
        other_role = SubjectRef(INSTALLATION, SubjectKind.BOT, "another-role")
        self.identity.bind_role(scope=SCOPE, role=other_role, expected_revision=original.revision, observed_at=NOW)
        changed = self.identity.role_binding(SCOPE)
        self.identity.bind_role(scope=SCOPE, role=original.role, expected_revision=changed.revision, observed_at=NOW)
        self.assertEqual(self.tasks.finish(claim, TaskResult("succeeded")).failure.code, "role_binding_changed")
        self.context = self.context_for(self.important)
        self.assertEqual(self.tasks.read(self.context, "task-1").failure.code, "task_unavailable")
        self.tasks.stop(claim, reason="permission_changed").unwrap()
        self.submit("expiring-input")
        self.inputs_current = False
        self.assertIsNone(self.tasks.claim(Lane.FOREGROUND).unwrap().claim)
        self.assertEqual(self.read_task("expiring-input").reason, "input_expired")
        self.inputs_current = True
        effect = self.running("revoke", kind="fixture.effect")
        self.identity.revoke_binding(self.important.binding_id, expected_revision=self.identity.revision, revoked_at=NOW)
        self.assertEqual(self.tasks.begin_effect(effect).failure.code, "binding_revoked")
        self.assertEqual(self.tasks.read(self.context, "revoke").failure.code, "binding_revoked")

    def test_grant_revocation_does_not_inherit_important_or_core_authority(self):
        context = self.context_for(self.standard)
        granted = self.grant(self.standard, context.scope, "configuration.read")
        spec = self.spec(context=context)
        self.tasks.submit(spec).unwrap()
        claim = self.claim()
        self.identity.revoke_grant(granted.grant_id, expected_revision=self.identity.revision, revoked_at=NOW)
        self.assertEqual(self.tasks.begin(claim).failure.code, "authorization_revoked")

    def test_other_actor_bot_account_and_audience_cannot_borrow_task(self):
        self.submit()
        self.assertEqual(self.tasks.read(self.context_for(self.core), "task-1").failure.code, "task_unavailable")
        wrong = replace(self.context, actor=self.core)
        self.assertEqual(self.tasks.submit(self.spec("wrong-actor", context=wrong)).failure.code, "private_task_required")
        other_scope = replace(SCOPE, bot_id="other-bot")
        other_role = replace(self.context.role_binding, scope=other_scope)
        other = replace(self.context, role_binding=other_role, audience=AudienceRef(other_scope, (self.important.subject,)))
        self.assertEqual(self.tasks.submit(self.spec("wrong-bot", context=other)).failure.code, "task_context_mismatch")

    def test_provider_generation_and_undeclared_effects_cannot_bypass_contract(self):
        claim = self.running()
        self.assertEqual(self.tasks.begin_effect(claim).failure.code, "task_effect_not_declared")
        self.assertEqual(self.tasks.finish(claim, TaskResult("succeeded", "committed", receipt_ref="fake", operation_id="fake")).failure.code,
                         "task_effect_not_declared")
        self.tasks.finish(claim, TaskResult("succeeded")).unwrap()
        effect = self.running("effect", kind="fixture.effect")
        self.assertEqual(self.tasks.record_operation(effect, "premature").failure.code, "task_effect_not_started")
        self.tasks.begin_effect(effect).unwrap()
        self.assertEqual(self.tasks.begin_effect(effect).failure.code, "task_effect_already_started")
        self.tasks.record_operation(effect, "original").unwrap()
        self.assertEqual(self.tasks.record_operation(effect, "another").failure.code, "task_operation_conflict")
        changed = replace(self.effectful, binding=replace(self.effectful.binding, generation="provider-2"))
        other = self.supervisor(self.open_runtime(), registrations=(self.registrations[0], self.registrations[1], TaskRegistration(changed, self.validate_inputs)))
        other.attach(expected_generation=1).unwrap()
        self.assertEqual(other.finish(effect, TaskResult("uncertain", "unknown")).failure.code, "task_definition_changed")

    def test_deadline_retention_and_finite_budgets(self):
        self.submit(deadline_at=NOW + timedelta(seconds=1))
        self.clock.advance(timedelta(seconds=1))
        self.assertIsNone(self.tasks.claim(Lane.FOREGROUND).unwrap().claim)
        self.assertEqual(self.read_task().state, "expired")
        for number in range(1, self.limits.retained_tasks):
            saved = self.submit(f"cancel-{number}")
            self.tasks.cancel(self.context, saved.task_id, expected_revision=saved.revision).unwrap()
        self.assertEqual(self.tasks.submit(self.spec("retention-overflow")).failure.code, "task_queue_full")
        for value in (float("inf"), float("nan"), -1, True):
            with self.assertRaises(ValueError):
                replace(self.foreground, timeout_seconds=value)

    def test_claim_failure_rolls_back_attempt_and_capacity(self):
        self.submit()
        with patch.object(self.tasks, "_change", side_effect=RuntimeError("fault before task transition")):
            with self.assertRaises(RuntimeError):
                self.tasks.claim(Lane.FOREGROUND)
        self.assertEqual(self.runtime.read_one("SELECT COUNT(*) n FROM execution_attempts", ())["n"], 0)
        self.assertEqual(self.read_task().state, "created")
        self.assertIsNotNone(self.claim())


class SelfReviewActionAdmissionTests(ExecutionFixture):
    """The daily review's declared action must survive real task admission."""

    def test_private_owner_enqueue_uses_the_registered_self_review_action(self):
        from companion_v2.role_work.journal.self_review_evidence import READ_ACTION
        definition = replace(self.foreground, kind="fixture.self-review", action=READ_ACTION)
        tasks = self.supervisor(registrations=(*self.registrations,
                                               TaskRegistration(definition, self.validate_inputs)))
        tasks.start(expected_generation=1).unwrap()
        now = self.clock.now()
        self.assertIn(READ_ACTION, tasks.policy.registry.definitions)
        self.assertTrue(tasks.check_context_access(self.context, action=READ_ACTION, observed_at=now).is_success)
        saved = tasks.submit(self.spec(kind="fixture.self-review")).unwrap()
        self.assertEqual(saved.spec.kind, "fixture.self-review")
        self.assertEqual(saved.spec.context, self.context)

        # Another person cannot act on this private window's review, and the
        # refusal happens before any task row is written.
        binding = self.identity.resolve_role(SCOPE, observed_at=now).unwrap()
        foreign = TaskContext(self.standard, binding, AudienceRef(SCOPE, (self.standard.subject,)))
        self.assertEqual(tasks.check_context_access(foreign, action=READ_ACTION, observed_at=now).failure.code,
                         "private_task_required")
        self.assertEqual(tasks.submit(self.spec("task-foreign", kind="fixture.self-review",
                                                context=foreign)).failure.code, "private_task_required")
        self.assertEqual(self.runtime.read_one(
            "SELECT COUNT(*) n FROM execution_tasks WHERE task_id = 'task-foreign'", ())["n"], 0)


if __name__ == "__main__":
    unittest.main()
