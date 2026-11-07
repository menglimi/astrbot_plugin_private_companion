"""Nested owner reads share only successful synchronous dependency checks."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import timedelta
from threading import get_ident
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from companion_v2.conversation.content.access import ContentAccess, document
from companion_v2.conversation.content.sources import ContentSources
from companion_v2.execution.models import TaskContext, TaskError, digest, require
from companion_v2.foundation.refs import AudienceRef, SourceRef, VersionRef
from companion_v2.sources.dependencies import key, link, validation_batch
from companion_v2.sources.governance import bind
from companion_v2.sources.models import SourceRecord
from companion_v2.sources.repositories import SourceRepository
from tests.test_management_access import ManagementFixture, NOW, SCOPE


class SourceValidationTraversalTests(ManagementFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.sources = SourceRepository(self.runtime)
        self.audience = AudienceRef(SCOPE, (self.important.subject,))

    def publish(self, name, owner="review.original"):
        reference = SourceRef(name, owner, SCOPE, VersionRef(name, 1), NOW)
        return self.sources.publish(SourceRecord(reference, self.audience, digest(name), name), observed_at=NOW).unwrap()

    def guard(self, owner, check, *, runtime=None):
        guard = bind(runtime or self.runtime, owner, check)
        self.addCleanup(guard.close)
        return guard

    def test_content_version_chain_is_linear_with_a_clock_that_advances_on_every_read(self):
        self.grant(self.important, SCOPE, "text")
        context = TaskContext(self.important, self.identity.resolve_role(SCOPE, observed_at=NOW).unwrap(), self.audience)
        ticks = 0

        def now():
            nonlocal ticks
            ticks += 1
            return NOW + timedelta(microseconds=ticks)

        access = ContentAccess(SimpleNamespace(runtime=self.runtime, messages=SimpleNamespace(
            sources=self.sources, tasks=SimpleNamespace(identity=self.identity, now=now))))
        states = {None: {"state": "active", "content_hash": None, "sources": []}}
        checks = []

        def state(owner, work_id, version_id):
            checks.append(version_id)
            return states[version_id]

        content = ContentSources(access, state=state, events=lambda *args: None,
            admit=lambda *args: None, live=lambda: None, binding={})
        self.addCleanup(content.close)
        previous, depth = None, 24
        for number in range(depth):
            version = str(number)
            provenance = [] if previous is None else [
                {**document(previous.reference), "content_hash": previous.content_hash}]
            states[version] = {"state": "active", "content_hash": digest("version:" + version), "sources": provenance}
            anchor = content.anchor(context, {}, "work", version)
            previous = self.publish("read:" + version)
            link(self.runtime, previous.reference, (anchor,), now())

        checks.clear()
        before = ticks
        with patch.object(self.identity, "authorize", wraps=self.identity.authorize) as authorize:
            self.assertEqual(access.read_source(previous.reference, context), previous)
        self.assertEqual(ticks - before, 1)
        self.assertEqual(len(checks), depth + 1)
        self.assertEqual(len(set(checks)), depth + 1)
        self.assertEqual(authorize.call_count, depth)

    def test_next_call_rechecks_owner_and_withdrawal_even_at_the_same_time(self):
        checked = []
        guard = self.guard("review.guarded", lambda record, at: checked.append(record.reference.source_id))
        original = self.publish("original", "review.guarded")
        derived = self.publish("derived")
        link(self.runtime, derived.reference, (original.reference,), NOW)
        self.sources.validate_reference(derived.reference, observed_at=NOW).unwrap()
        count = len(checked)
        self.sources.validate_reference(derived.reference, observed_at=NOW).unwrap()
        self.assertEqual(len(checked), count + 1)
        guard.close()
        self.assertEqual(self.sources.validate_reference(derived.reference, observed_at=NOW).failure.code,
                         "source_owner_unavailable")
        self.guard("review.guarded", lambda *args: None)
        self.sources.validate_reference(derived.reference, observed_at=NOW).unwrap()
        self.sources.revoke(original.reference, expected_governance_revision=1, observed_at=NOW).unwrap()
        self.assertEqual(self.sources.validate_reference(derived.reference, observed_at=NOW).failure.code, "source_revoked")

    def test_read_snapshot_batch_reuses_graph_but_rechecks_owner(self):
        checked = []
        guard = self.guard("review.batched", lambda record, at: checked.append(record.reference.source_id))
        original = self.publish("batched-original", "review.batched")
        derived = self.publish("batched-derived")
        link(self.runtime, derived.reference, (original.reference,), NOW)
        checked.clear()

        with patch.object(self.runtime, "read_all", wraps=self.runtime.read_all) as read_all:
            with validation_batch(self.runtime, NOW):
                self.sources.validate_reference(derived.reference, observed_at=NOW).unwrap()
                self.sources.validate_reference(derived.reference, observed_at=NOW).unwrap()
                guard.close()
                self.assertEqual(self.sources.validate_reference(derived.reference, observed_at=NOW).failure.code,
                                 "source_owner_unavailable")
        self.assertEqual(checked, ["batched-original", "batched-original"])
        self.assertFalse(any("FROM source_dependencies" in call.args[0] for call in read_all.call_args_list))

    def test_failed_nested_checks_are_not_cached_when_the_owner_catches_them(self):
        failures, checks = [], []

        def reject(record, at):
            checks.append(record.reference.source_id)
            raise TaskError("review_denied", "the owner rejected this original")

        self.guard("review.rejected", reject)
        original = self.publish("rejected", "review.rejected")

        def inspect_twice(record, at):
            for _ in range(2):
                result = self.sources.validate_reference(original.reference, observed_at=at)
                failures.append(result.failure.code if result.failure else None)

        self.guard("review.inspect", inspect_twice)
        derived = self.publish("inspector", "review.inspect")
        self.sources.validate_reference(derived.reference, observed_at=NOW).unwrap()
        self.assertEqual(failures, ["review_denied", "review_denied"])
        self.assertEqual(checks, ["rejected", "rejected"])
        self.guard("review.rejected", lambda *args: None)
        self.sources.validate_reference(original.reference, observed_at=NOW).unwrap()

    def test_declared_and_recursive_owner_cycles_fail_without_poisoning_next_call(self):
        first, second = self.publish("first"), self.publish("second")
        link(self.runtime, first.reference, (second.reference,), NOW)
        with self.assertRaisesRegex(TaskError, "cannot support itself"):
            link(self.runtime, second.reference, (first.reference,), NOW)
        # Simulate a damaged persisted graph; production link() rejects this edge.
        with self.runtime.transaction() as connection:
            connection.execute("INSERT INTO source_dependencies VALUES (?, ?, ?, ?, ?, ?)",
                               (*key(second.reference), *key(first.reference)))
        self.assertEqual(self.sources.validate_reference(first.reference, observed_at=NOW).failure.code,
                         "source_dependency_cycle")
        with validation_batch(self.runtime, NOW):
            self.assertEqual(self.sources.validate_reference(first.reference, observed_at=NOW).failure.code,
                             "source_dependency_cycle")

        def recursive(record, at):
            require(self.sources.validate_reference(record.reference, observed_at=at))

        self.guard("review.recursive", recursive)
        recursive_source = self.publish("recursive", "review.recursive")
        self.assertEqual(self.sources.validate_reference(recursive_source.reference, observed_at=NOW).failure.code,
                         "source_dependency_cycle")
        self.guard("review.recursive", lambda *args: None)
        self.sources.validate_reference(recursive_source.reference, observed_at=NOW).unwrap()

    def test_nested_runtime_and_observation_time_have_independent_checks(self):
        def dated(record, at):
            if at > NOW:
                raise TaskError("review_expired", "the owner's observation has expired")

        self.guard("review.dated", dated)
        original = self.publish("dated", "review.dated")
        other = self.open_runtime()

        def remote(record, at):
            raise TaskError("review_other_runtime", "this runtime's owner rejects the original")

        self.guard("review.dated", remote, runtime=other)
        failures = []

        def inspect(record, at):
            failures.append(self.sources.validate_reference(original.reference, observed_at=at + timedelta(seconds=1)).failure.code)
            failures.append(SourceRepository(other).validate_reference(original.reference, observed_at=at).failure.code)

        self.guard("review.inspect", inspect)
        derived = self.publish("inspector", "review.inspect")
        link(self.runtime, derived.reference, (original.reference,), NOW)
        self.sources.validate_reference(derived.reference, observed_at=NOW).unwrap()
        self.assertEqual(failures, ["review_expired", "review_other_runtime"])

    def test_cached_dependencies_do_not_skip_identity_audience_or_content_checks(self):
        self.grant(self.important, SCOPE, "text")
        self.grant(self.standard, SCOPE, "text")
        original = self.publish("original")
        results = []

        def inspect(record, at):
            read = lambda actor, capability: self.sources.read(original.reference, resolved=actor,
                capability=capability, observed_at=at, authorizer=self.identity)
            self.assertTrue(read(self.important, "text").is_success)
            results.append(read(self.standard, "text"))
            results.append(read(self.important, "ungranted.capability"))
            results.append(self.sources.validate_reference_for_audience(original.reference,
                recipient=self.important.subject, observed_at=at, expected_content_hash=digest("changed")))

        self.guard("review.inspect", inspect)
        derived = self.publish("inspector", "review.inspect")
        link(self.runtime, derived.reference, (original.reference,), NOW)
        self.sources.validate_reference(derived.reference, observed_at=NOW).unwrap()
        self.assertTrue(all(result.failure is not None for result in results))
        self.assertEqual(results[0].failure.code, "source_audience_denied")
        self.assertEqual(results[2].failure.code, "source_content_changed")

    def test_cached_dependency_rechecks_revocation_and_owner_close_in_nested_calls(self):
        guard = self.guard("review.guarded", lambda *args: None)
        original = self.publish("original", "review.guarded")
        failures = []

        def inspect(record, at):
            self.sources.validate_reference(original.reference, observed_at=at).unwrap()
            guard.close()
            failures.append(self.sources.validate_reference(original.reference, observed_at=at).failure.code)
            self.guard("review.guarded", lambda *args: None)
            self.sources.validate_reference(original.reference, observed_at=at).unwrap()
            self.sources.revoke(original.reference, expected_governance_revision=1, observed_at=at).unwrap()
            failures.append(self.sources.validate_reference(original.reference, observed_at=at).failure.code)

        self.guard("review.inspect", inspect)
        # This dependency is read only by the owner so its revocation does not
        # itself invalidate the inspector while testing the nested read result.
        derived = self.publish("inspector", "review.inspect")
        self.sources.validate_reference(derived.reference, observed_at=NOW).unwrap()
        self.assertEqual(failures, ["source_owner_unavailable", "source_revoked"])

    def test_copied_context_in_another_thread_cannot_reuse_parent_success(self):
        parent_thread, checks, results = get_ident(), [], []

        def check(record, at):
            checks.append(get_ident())
            if get_ident() != parent_thread:
                raise TaskError("review_thread_denied", "this synchronous branch must run its own check")

        self.guard("review.thread", check)
        original = self.publish("original", "review.thread")

        def inspect(record, at):
            copied = copy_context()
            with ThreadPoolExecutor(max_workers=1) as executor:
                result = executor.submit(copied.run, self.sources.validate_reference,
                    original.reference, observed_at=at).result(timeout=3)
            results.append(result.failure.code if result.failure else None)

        self.guard("review.inspect", inspect)
        derived = self.publish("inspector", "review.inspect")
        link(self.runtime, derived.reference, (original.reference,), NOW)
        checks.clear()
        self.sources.validate_reference(derived.reference, observed_at=NOW).unwrap()
        self.assertEqual(results, ["review_thread_denied"])
        self.assertEqual(len(checks), 2)

    async def test_task_inheriting_context_after_return_runs_current_owner_checks(self):
        allowed, pending = True, []

        def check(record, at):
            if not allowed:
                raise TaskError("review_task_denied", "the owner changed after the synchronous traversal")

        self.guard("review.task", check)
        original = self.publish("original", "review.task")

        async def later():
            return self.sources.validate_reference(original.reference, observed_at=NOW)

        def inspect(record, at):
            pending.append(asyncio.create_task(later()))

        self.guard("review.inspect", inspect)
        derived = self.publish("inspector", "review.inspect")
        link(self.runtime, derived.reference, (original.reference,), NOW)
        self.sources.validate_reference(derived.reference, observed_at=NOW).unwrap()
        allowed = False
        self.assertEqual((await pending[0]).failure.code, "review_task_denied")


if __name__ == "__main__":
    unittest.main()
