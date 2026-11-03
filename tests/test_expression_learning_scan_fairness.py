"""One discovery pass must serve every due learning window, not only the first.

A window whose samples are already learned is still "due" once its rescan
interval elapses: the checkpoint only moves when something commits, so a window
with nothing new keeps reporting "due" forever. The scan used to start a worker
for it, the worker skipped the batch at once, and the scan treated that skip as
the one window this pass serves. Any other window that had become due — an
abandoned batch waiting for its next retry epoch, a freshly rotated scope — was
then never looked at, which is how a retry scheduled 210 seconds ahead ran hours
late while the learned window logged a skip every 60 seconds.

The suite also pins the two properties the fix needs besides fairness: a window
only spends its next check when it is really served (a look that finds nothing
to prepare must leave the window free for the next pass), and a pass is bounded
— it serves a window at most once and starts at most ``MAX_SCAN_SERVICES``
workers. A learned batch stops being re-prepared and re-logged every minute.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from companion_v2.execution.models import digest
from companion_v2.execution.runner import RunResult
from companion_v2.expression.style_service import (KIND, OWNER, ExpressionStyleService,
                                                   retry_wait_seconds)
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import OperationRef, ScopeKind, ScopeRef, VersionRef
from companion_v2.storage.models import OperationState
from companion_v2.storage.repositories import OperationRepository
from companion_v2.storage.runtime import StorageRuntime

NOW = datetime(2026, 10, 30, 9, 0, tzinfo=timezone.utc)
LOGGER = "companion_v2.expression.style_service"
SKIPPED = "跳过同一批素材"
LEASE_PREFIX = "expression-lease:"
SERVICE_PASSES = "companion_v2.expression.private_scope.role_private_contexts"


def context(number):
    """One private window; its key is what ``next_checks`` and the cursor use."""
    scope = ScopeRef("installation-a", "onebot", ScopeKind.PRIVATE, f"native-{number}", "bot-a",
                     "login-a", f"conversation-{number}", f"session-{number}")
    return SimpleNamespace(key=f"private:{number}", scope=scope)


async def _noop(*_args):
    return None


class ScanFixture(unittest.TestCase):
    """A real service against a real store; only the model round is scripted."""

    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="expression-scan-")
        self.addCleanup(self.temporary.cleanup)
        self.runtime = StorageRuntime.open(data_root=Path(self.temporary.name),
                                           installation_id="installation-a")
        self.addCleanup(self.runtime.close)
        self.operations = OperationRepository(self.runtime)
        self.now = NOW
        self.learning_rounds = []
        self.material = {}

    def service(self):
        service = ExpressionStyleService(self.runtime)
        self.app = SimpleNamespace(
            state_maintenance=SimpleNamespace(bindings={}),
            notify=lambda: None,
            tasks=SimpleNamespace(now=lambda: self.now),
            gateway=SimpleNamespace(router=SimpleNamespace(profiles={KIND: object()})))
        service.app, service.gateway = self.app, self.app.gateway
        service.tasks = SimpleNamespace(now=lambda: self.now, identity=None,
                                        submit=lambda spec: Outcome.success(spec))
        # Everything below the scheduler is scripted: what this suite drives is
        # which windows a discovery pass reaches, not what a round then does.
        service.guard = _noop
        service.identifier = staticmethod(lambda ctx, token: "expression-learn:" + token)
        service.samples = SimpleNamespace(role_owner=lambda ctx: "owner:" + ctx.key,
                                          messages=None, collect=self.collect)
        service._policy = lambda ctx, at: SimpleNamespace(active_signal_limit=60,
                                                          scan_interval_minutes=60,
                                                          group_scan_interval_minutes=60)
        service._active_signal_count = lambda ctx: 0
        service.prepare = self.prepare
        service.runner = SimpleNamespace(run=self.model_round)
        return service

    def collect(self, *_args, **_options):
        raise AssertionError("prepare is scripted; nothing may collect real samples")

    def prepare(self, ctx, instruction, at):
        """One preparable batch per window, identified by the window itself."""
        return {"context": ctx, "instruction": instruction, "at": at,
                "policy": SimpleNamespace(learning_mode="whole", scan_interval_minutes=60,
                                          group_scan_interval_minutes=60),
                "material_hash": self.material.get(ctx.key, "material-" + ctx.key),
                "samples": SimpleNamespace(samples=()), "catalogue": (), "existing_rules": (),
                "latest_sequence": 0, "family_keys": (), "sample_context_key": ctx.key,
                "sample_document": {}}

    async def model_round(self, ctx, task_id):
        self.learning_rounds.append(task_id)
        return Outcome.success(RunResult(SimpleNamespace(state="succeeded", reason="owner_result")))

    def bind(self, service, windows):
        """Publish the windows the host holds, in the order discovery walks them."""
        service.app.state_maintenance.bindings.update(
            {window.key: (window, "学习这一批", _noop) for window in windows})

    def request_key(self, service, window):
        work_key = service._work_key(window)
        job = self.prepare(window, "学习这一批", self.now)
        return digest([work_key, job["material_hash"], job["policy"].learning_mode])

    def learned(self, service, window, *, epoch=0):
        """Record that these exact samples were already committed for this window.

        This is the state the production log showed: a ``completed`` receipt for
        the current material batch while the window's checkpoint never moves,
        because only a commit may move it.
        """
        identifier = service._attempt_identifier(window, self.request_key(service, window), epoch)
        reference = OperationRef(identifier, OWNER, window.scope, VersionRef(identifier, 1), identifier)
        self.operations.reserve(reference, parameter_hash=digest(["parameters"]),
                                observed_at=self.now).unwrap()
        self.operations.transition(identifier, expected_state=OperationState.ACCEPTED,
                                   next_state=OperationState.COMPLETED, observed_at=self.now).unwrap()
        return identifier

    def abandoned_attempt(self, service, window):
        """An attempt that already opened its retry epoch: the batch is due now."""
        with self.runtime.transaction() as connection:
            connection.execute("""INSERT INTO runtime_metadata(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                (LEASE_PREFIX + self.request_key(service, window),
                 json.dumps({"epoch": 1, "issued_at": "2026-10-30T08:00:00+00:00"})))

    def pass_once(self, service):
        """Run one discovery pass on the real clock gate."""
        service._next_discovery_at = None
        service.cursor = 0
        with patch(SERVICE_PASSES, return_value=()):
            service.advance()

    async def drain(self, service):
        """Let every worker this pass started settle before the next pass."""
        workers = tuple(service.workers.values())
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        for _ in range(200):
            if not service.workers:
                return
            await asyncio.sleep(0.01)


class DiscoveryFairnessTests(ScanFixture, unittest.IsolatedAsyncioTestCase):
    """The pass serves every willing window; the learned one is not a service."""

    async def test_a_due_retry_is_served_after_a_learned_window_skips(self):
        service = self.service()
        learned, retry = context(1), context(2)
        service.next_checks[learned.key] = NOW - timedelta(seconds=1)
        service.next_checks[retry.key] = NOW - timedelta(seconds=1)
        self.learned(service, learned)
        self.bind(service, (learned, retry))

        self.pass_once(service)

        served = set(service.workers)
        self.assertNotIn(service._work_key(learned), served,
                         "a batch that is already learned is no work, not this pass' service")
        self.assertIn(service._work_key(retry), served,
                      "a due retry must be served in the same pass that met a learned window")
        self.assertEqual(service.results[service._work_key(learned)]["state"], "already_attempted",
                         "the page still has to say this batch is learned")
        await self.drain(service)

    async def test_a_window_waiting_for_its_retry_epoch_is_served_once_it_is_due(self):
        service = self.service()
        learned, retry = context(1), context(2)
        self.learned(service, learned)
        self.abandoned_attempt(service, retry)
        wait = retry_wait_seconds(0)
        service.next_checks[learned.key] = NOW - timedelta(seconds=1)
        service.next_checks[retry.key] = NOW + timedelta(seconds=wait)
        self.bind(service, (learned, retry))

        self.pass_once(service)
        self.assertNotIn(service._work_key(retry), service.workers,
                         "the retry still has its declared wait to serve")
        await self.drain(service)

        self.now = NOW + timedelta(seconds=wait + 1)
        for window in (learned, retry):
            service.next_checks[window.key] = self.now - timedelta(seconds=1)
        self.pass_once(service)

        self.assertIn(service._work_key(retry), service.workers,
                      "the retry waited exactly its wait, then lost its turn to a learned window")
        await self.drain(service)

    async def test_several_windows_due_at_once_are_all_served(self):
        service = self.service()
        windows = tuple(context(number) for number in range(1, 4))
        for window in windows:
            service.next_checks[window.key] = NOW - timedelta(seconds=1)
        self.bind(service, windows)

        self.pass_once(service)

        self.assertEqual({service._work_key(window) for window in windows}, set(service.workers),
                         "one discovery pass must serve every window that is due")
        await self.drain(service)

    async def test_a_learned_window_stops_re_preparing_and_stops_logging(self):
        service = self.service()
        window = context(1)
        identifier = self.learned(service, window)
        self.bind(service, (window,))
        prepare_calls = []
        prepared = service.prepare

        def counting_prepare(ctx, instruction, at):
            prepare_calls.append(ctx.key)
            return prepared(ctx, instruction, at)

        service.prepare = counting_prepare

        with self.assertLogs(LOGGER, level="INFO") as logs:
            for offset in (0, 61, 122, 183):
                self.now = NOW + timedelta(seconds=offset)
                self.pass_once(service)
                await self.drain(service)
            self.assertEqual(prepare_calls, [window.key],
                             "a learned window must not be re-prepared every minute")
            # A cleared gate — a restart, a manual rescan — still must not write
            # the same note twice for a batch already known to be learned.
            service.next_checks.clear()
            self.now = NOW + timedelta(seconds=200)
            self.pass_once(service)
            await self.drain(service)

        notes = [line for line in logs.output if SKIPPED in line]
        self.assertEqual(len(notes), 1, "a learned batch is noted once, not once per discovery pass")
        self.assertIn(identifier, notes[0])
        self.assertGreaterEqual(service.next_checks[window.key], NOW + timedelta(minutes=59),
                                "the window goes back to its own rescan interval")

    async def test_a_window_with_nothing_to_prepare_keeps_its_next_check(self):
        service = self.service()
        current, due = context(1), context(2)
        # ``current`` committed just now, so its own rescan interval has not
        # elapsed and the pass finds nothing to prepare for it. That look is not a
        # service: taking its next check would push the window out of the following
        # pass for no reason, which is the other half of the starvation — the scan
        # reserved a window before it knew whether the window had anything to do.
        service._advance_cursor(service._cursor_key(current), NOW)
        self.bind(service, (current, due))

        self.pass_once(service)

        self.assertNotIn(current.key, service.next_checks,
                         "a window with nothing to prepare must not spend its next check")
        self.assertIn(service._work_key(due), service.workers,
                      "the window behind it still gets its turn")
        await self.drain(service)

    async def test_one_pass_starts_a_bounded_number_of_workers(self):
        from companion_v2.expression.style_service import MAX_SCAN_SERVICES
        service = self.service()
        windows = tuple(context(number) for number in range(1, 13))
        for window in windows:
            service.next_checks[window.key] = NOW - timedelta(seconds=1)
        self.bind(service, windows)

        self.pass_once(service)

        self.assertEqual(len(service.workers), MAX_SCAN_SERVICES,
                         "a pass is bounded so it cannot hold the loop or burst providers")
        await self.drain(service)
        # The windows this pass did not reach are still due, and it is only their
        # turn that was missing — nothing was taken from them. The eight it did
        # serve are held by their own minute, so the next pass picks up the rest.
        self.now = NOW + timedelta(seconds=30)
        self.pass_once(service)
        self.assertEqual(len(service.workers), len(windows) - MAX_SCAN_SERVICES,
                         "the windows the first pass did not reach keep their turn")
        await self.drain(service)


if __name__ == "__main__":
    unittest.main()
