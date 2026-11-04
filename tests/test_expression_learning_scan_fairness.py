"""One discovery pass must serve every due learning window, not only the first.

A window whose samples are already learned is still "due" once its rescan
interval elapses: the checkpoint only moves when something commits, so a window
with nothing new keeps reporting "due" forever. The scan used to start a worker
for it, the worker skipped the batch at once, and the scan treated that skip as
the one window this pass serves. Any other window that had become due — an
abandoned batch waiting for its next retry epoch, a freshly rotated scope — was
then never looked at, which is how a retry scheduled 210 seconds ahead ran hours
late while the learned window logged a skip every 60 seconds.

The pass serves no more windows than the task layer runs at once — the owner's
own ``TaskDefinition.concurrency``, one for this task — because a queued attempt
keeps the deadline it was submitted with and would be expired before it starts.
Fairness therefore comes from three things, not from a wider worker count: a
window that is already learned or still held by another attempt is a look, not a
service, so it cannot take the one worker; the look budget (``MAX_SCAN_PREPARES``)
bounds the synchronous reads a pass performs; and ``_in_turn`` hands the windows a
pass did not reach to the next one.

The suite also pins the properties the fix needs besides fairness: a window only
spends its next check when it is really served (a look that finds nothing to
prepare must leave the window free for the next pass), a batch another attempt
still holds is not a service either — the scan asks exactly what the claim asks —
and a pass is bounded in both what it starts and what it reads, with the same
bound holding for one binding's discovery result and for the group queue. A held
batch stops being re-prepared and re-logged every minute.
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
from companion_v2.expression.style_service import (KIND, LEASE_PREFIX, OWNER, ExpressionStyleService,
                                                   retry_wait_seconds)
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import OperationRef, ScopeKind, ScopeRef, VersionRef
from companion_v2.storage.models import OperationState
from companion_v2.storage.repositories import OperationRepository
from companion_v2.storage.runtime import StorageRuntime

NOW = datetime(2026, 10, 30, 9, 0, tzinfo=timezone.utc)
LOGGER = "companion_v2.expression.style_service"
SKIPPED = "跳过同一批素材"
SERVICE_PASSES = "companion_v2.expression.private_scope.role_private_contexts"


def context(number):
    """One private window; its key is what ``next_checks`` and the cursor use."""
    scope = ScopeRef("installation-a", "onebot", ScopeKind.PRIVATE, f"native-{number}", "bot-a",
                     "login-a", f"conversation-{number}", f"session-{number}")
    return SimpleNamespace(key=f"private:{number}", scope=scope)


def group_context(number):
    """One group window; the group loop walks these after the private queue."""
    scope = ScopeRef("installation-a", "onebot", ScopeKind.GROUP, f"native-group-{number}", "bot-a",
                     "login-a", f"group-conversation-{number}", f"group-session-{number}")
    return SimpleNamespace(key=f"group:{number}", scope=scope)


async def _noop(*_args):
    return None


class LimitedTaskLayer:
    """A task layer that runs one attempt at a time and expires a queued one.

    The real supervisor admits ``concurrency`` attempts of one kind at once and
    keeps the deadline each task was submitted with. A pass that starts more
    workers than that queues the tail behind the head, and by the time the tail is
    admitted its deadline has passed: the row settles ``expired`` after its receipt
    was already reserved, and the page has no wording for it. This stub keeps the
    same shape — one attempt at a time, a second concurrent submission expired —
    without waiting out a real 180-second deadline.
    """

    def __init__(self, clock, *, concurrency=1):
        self.clock = clock
        self.concurrency = concurrency
        self.identity = None
        self.submitted = []
        self.expired = []
        self.running = 0

    def now(self):
        return self.clock()

    def submit(self, spec):
        self.submitted.append(spec)
        return Outcome.success(spec)

    async def run(self, context, task_id):
        if self.running >= self.concurrency:
            self.expired.append(task_id)
            return Outcome.success(RunResult(SimpleNamespace(state="expired",
                                                             reason="deadline_before_start")))
        self.running += 1
        try:
            await asyncio.sleep(0)
            return Outcome.success(RunResult(SimpleNamespace(state="succeeded", reason="owner_result")))
        finally:
            self.running -= 1


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
        service.identifier = staticmethod(lambda ctx, attempt: "expression-learn:" + attempt)
        service.samples = SimpleNamespace(role_owner=lambda ctx: "owner:" + ctx.key,
                                          messages=None, collect=self.collect)
        service._policy = lambda ctx, at: SimpleNamespace(active_signal_limit=60,
                                                          scan_interval_minutes=60,
                                                          group_scan_interval_minutes=60,
                                                          group_daily_batch_limit=8,
                                                          group_new_messages=1,
                                                          group_min_samples=1)
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
                                          group_scan_interval_minutes=60,
                                          group_daily_batch_limit=8),
                "material_hash": self.material.get(ctx.key, "material-" + ctx.key),
                "samples": SimpleNamespace(samples=()), "catalogue": (), "existing_rules": (),
                "latest_sequence": 1, "family_keys": (), "sample_context_key": ctx.key,
                "sample_document": {}}

    async def model_round(self, ctx, task_id):
        self.learning_rounds.append(task_id)
        return Outcome.success(RunResult(SimpleNamespace(state="succeeded", reason="owner_result")))

    def bind(self, service, windows):
        """Publish the windows the host holds, in the order discovery walks them."""
        service.app.state_maintenance.bindings.update(
            {window.key: (window, "学习这一批", _noop) for window in windows})

    def instructable(self, windows):
        """Give windows that the host does not bind a stored role instruction.

        A discovered candidate is not necessarily one of the windows the host
        currently binds, so its own instruction has to come from the batch it was
        stored with — the path ``_private_instruction`` reads. The pass is real
        from there on: the only thing the test hands in is what one binding's
        discovery returns.
        """
        with self.runtime.transaction() as connection:
            for window in windows:
                connection.execute("""INSERT INTO conversation_batches(batch_id, origin_key,
                    context_json, role_instruction, state, revision, created_at, contributions_json)
                    VALUES (?, ?, '{}', ?, 'ready', 1, '2026-10-30T08:00:00+00:00', '{}')""",
                    ("batch-" + window.key, window.key, "学习这一批"))

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
        request_key = self.request_key(service, window)
        with self.runtime.transaction() as connection:
            connection.execute("""INSERT INTO runtime_metadata(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                (LEASE_PREFIX + request_key,
                 json.dumps({"epoch": 1, "issued_at": "2026-10-30T08:00:00+00:00"})))
        # The fixture must write the lease where the service reads it. A prefix
        # that drifts would leave this test passing while never exercising the
        # epoch path it claims to cover, so the read is asserted here.
        self.assertEqual(service._lease_epoch(request_key), 1,
                         "the abandoned attempt must be visible at the service's own lease key")

    def accepted(self, service, window, *, age=timedelta(0)):
        """An attempt that opened its receipt and never closed it.

        This is what a failed model round leaves behind, and it is what
        ``_claim_attempt`` still treats as the batch's own while its lease lasts:
        nothing was committed, but one model call is theirs until the declared
        attempt (and its margin) is over. A window in this state is due — only a
        commit moves its checkpoint — and yet it must not spend the pass.
        """
        identifier = service._attempt_identifier(window, self.request_key(service, window), 0)
        reference = OperationRef(identifier, OWNER, window.scope, VersionRef(identifier, 1), identifier)
        self.operations.reserve(reference, parameter_hash=digest(["parameters"]),
                                observed_at=self.now - age).unwrap()
        return identifier

    def pass_once(self, service, discovery=()):
        """Run one discovery pass on the real clock gate.

        ``discovery`` is what one binding's discovery returns. Empty is the
        single-candidate shape — the pass falls back to the bound window itself —
        while a tuple of windows is what one binding reports when the whole role
        has due windows.
        """
        service._next_discovery_at = None
        service.cursor = 0
        with patch(SERVICE_PASSES, return_value=discovery):
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
        self.assertEqual(len(served), 1,
                         "the pass serves the one attempt the task layer runs at once")
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

    async def test_a_pass_serves_the_first_willing_window_and_leaves_the_rest_due(self):
        """Held windows are looked at, not served; the first willing one is served.

        This is the original bug in its smallest shape: two learned windows sit in
        front of two windows with work. The pass must walk past the learned ones
        and serve the first willing window, and it may serve only the one attempt
        the task layer runs at once. The window behind it keeps its own due time
        and is served by the next pass.
        """
        service = self.service()
        learned = (context(1), context(2))
        willing = (context(3), context(4))
        due_at = NOW - timedelta(seconds=1)
        for window in (*learned, *willing):
            service.next_checks[window.key] = due_at
        for window in learned:
            self.learned(service, window)
        self.bind(service, (*learned, *willing))

        self.pass_once(service)

        self.assertEqual(set(service.workers), {service._work_key(willing[0])},
                         "the pass walks past the learned windows and serves the first willing one")
        self.assertLessEqual(len(service.workers), service._service_budget(),
                             "a pass serves no more than the task layer runs at once")
        for window in learned:
            self.assertEqual(service.results[service._work_key(window)]["state"], "already_attempted",
                             "a learned window is still reported as learned")
        self.assertEqual(service.next_checks[willing[1].key], due_at,
                         "the window behind the served one keeps its own due time")
        await self.drain(service)

        self.now = NOW + timedelta(seconds=30)
        self.pass_once(service)

        self.assertEqual(set(service.workers), {service._work_key(willing[1])},
                         "the window the first pass did not serve is served next time")
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

    async def test_a_pass_starts_no_more_workers_than_the_task_layer_runs(self):
        """The worker count is the task layer's concurrency, not a number of its own.

        The owner registers ``concurrency=1``, so a pass that started eight workers
        would queue seven behind the first and expire them before they start. The
        pass therefore starts one, and the windows it did not serve keep their own
        due time for the following pass.
        """
        service = self.service()
        windows = tuple(context(number) for number in range(1, 13))
        due_at = NOW - timedelta(seconds=1)
        for window in windows:
            service.next_checks[window.key] = due_at
        self.bind(service, windows)

        self.pass_once(service)

        self.assertEqual(service._service_budget(), 1,
                         "the registered definition runs one attempt at a time")
        self.assertEqual(len(service.workers), service._service_budget(),
                         "a pass starts exactly the attempts the task layer admits")
        self.assertEqual(set(service.workers), {service._work_key(windows[0])},
                         "the pass serves the first window in turn")
        for window in windows[1:]:
            self.assertEqual(service.next_checks[window.key], due_at,
                             "a window the pass did not serve keeps its own due time")
        await self.drain(service)

        self.now = NOW + timedelta(seconds=30)
        self.pass_once(service)

        self.assertEqual(set(service.workers), {service._work_key(windows[1])},
                         "the windows the first pass did not serve keep their turn")
        await self.drain(service)

    async def test_one_binding_with_many_due_candidates_serves_one_and_keeps_the_rest(self):
        """The bound applies to a discovery result, not only to the binding list.

        One binding's discovery returns every due window of its role, so a pass
        that walks the binding list once can still meet twelve windows at once.
        The service budget has to hold inside that candidate walk too: the pass
        serves one, and the candidates it did not serve are only waiting for their
        turn — they are not served, so their own next check is left exactly as it
        was.
        """
        service = self.service()
        binding = context(0)
        windows = tuple(context(number) for number in range(1, 13))
        due_at = NOW - timedelta(seconds=1)
        for window in (binding, *windows):
            service.next_checks[window.key] = due_at
        self.bind(service, (binding,))
        self.instructable(windows)

        self.pass_once(service, discovery=windows)

        self.assertEqual(len(service.workers), 1,
                         "one binding's discovery result may not start more workers than the task layer runs")
        self.assertEqual(set(service.workers), {service._work_key(windows[0])},
                         "the pass walks the candidates in turn and serves the first")
        for window in windows[1:]:
            self.assertEqual(service.next_checks[window.key], due_at,
                             "a window the pass did not serve must not be marked as handled")
            self.assertNotIn(service._work_key(window), service.results,
                             "a window the pass did not serve must not report a result")
        await self.drain(service)

        # Same discovery result, a later pass: the look cursor is where the last
        # pass stopped, so the next candidate is the one served.
        self.now = NOW + timedelta(seconds=30)
        self.pass_once(service, discovery=windows)

        self.assertEqual(set(service.workers), {service._work_key(windows[1])},
                         "the candidates the first pass did not serve are served next time")
        await self.drain(service)

    async def test_eight_in_flight_batches_do_not_take_the_service_of_a_due_retry(self):
        """A batch another attempt still holds is not a service either.

        The scan used to ask only for a ``completed`` receipt, so every window
        whose earlier attempt was still inside its lease looked like work: eight of
        them started eight workers, each worker skipped at once and spent the pass,
        and a batch that had waited out its whole lease and was due for its retry
        was never served. The two places ask one question, so the scan asks it the
        way ``_claim_attempt`` does — ``completed`` or still owned.
        """
        service = self.service()
        in_flight = tuple(context(number) for number in range(1, 9))
        retry = context(9)
        held_by = {}
        for window in in_flight:
            service.next_checks[window.key] = NOW - timedelta(seconds=1)
            held_by[window.key] = self.accepted(service, window)
        service.next_checks[retry.key] = NOW - timedelta(seconds=1)
        # Its wait is over: the receipt is older than the lease its epoch declares,
        # so the batch is the one thing this pass has real work for.
        self.accepted(service, retry, age=timedelta(seconds=retry_wait_seconds(0) + 60))
        self.bind(service, (*in_flight, retry))

        with self.assertLogs(LOGGER, level="INFO") as logs:
            self.now = NOW
            self.pass_once(service)
            served = set(service.workers)
            await self.drain(service)
            # Three more passes inside the lease: the held windows are met again on
            # each one and are still only worth one line apiece.
            for offset in (60, 120, 180):
                self.now = NOW + timedelta(seconds=offset)
                self.pass_once(service)
                await self.drain(service)

        self.assertEqual(served, {service._work_key(retry)},
                         "the due retry is the pass' service; a batch still held is not")
        self.assertEqual(len(served), 1,
                         "eight held windows must not spend more than the one service the pass has")
        self.assertEqual({service.results.get(service._work_key(window), {}).get("state")
                          for window in in_flight}, {"already_attempted"},
                         "the page still has to say those windows are in flight, not idle")
        notes = [line for line in logs.output if SKIPPED in line]
        self.assertEqual(len(notes), len(in_flight),
                         "each held window is noted once, not once per discovery pass")
        for identifier in held_by.values():
            self.assertEqual(sum(identifier in line for line in notes), 1,
                             "one line per held window and receipt, never a repeat")

    async def test_a_pass_bounds_how_many_windows_it_prepares(self):
        """The pass' synchronous cost is bounded, not only its workers.

        ``prepare`` reads the sample window and the reference rules before any
        worker exists, so a pass that prepares two hundred due windows holds the
        event loop for two hundred reads while starting no worker at all. The look
        budget bounds that, and the windows behind it are untouched: the next pass
        starts where this one stopped and serves them.
        """
        from companion_v2.expression.style_service import MAX_SCAN_PREPARES
        service = self.service()
        binding = context(0)
        windows = tuple(context(number) for number in range(1, 201))
        due_at = NOW - timedelta(seconds=1)
        for window in (binding, *windows):
            service.next_checks[window.key] = due_at
        self.bind(service, (binding,))
        self.instructable(windows)
        # Every window this pass can reach is already learned: it prepares work it
        # cannot serve, which is the shape where the prepare count was the whole
        # cost of the pass.
        for window in windows[:MAX_SCAN_PREPARES]:
            self.learned(service, window)
        prepare_calls = []
        prepared = service.prepare

        def counting_prepare(ctx, instruction, at):
            prepare_calls.append(ctx.key)
            return prepared(ctx, instruction, at)

        service.prepare = counting_prepare

        self.pass_once(service, discovery=windows)

        self.assertEqual(len(prepare_calls), MAX_SCAN_PREPARES,
                         "a pass prepares a bounded number of windows, not one per due window")
        self.assertLess(len(prepare_calls), len(windows),
                        "the bound has to be smaller than the store for it to mean anything")
        self.assertFalse(service.workers, "a pass spent on held windows starts no worker")
        for window in windows[MAX_SCAN_PREPARES:]:
            self.assertEqual(service.next_checks[window.key], due_at,
                             "a window the pass did not look at must not be marked as handled")
            self.assertNotIn(service._work_key(window), service.results,
                             "a window the pass did not look at must not report a result")
        await self.drain(service)

        # Same discovery result, a later pass: the look cursor is where the last
        # pass stopped, so the first window it never looked at is the one served.
        self.now = NOW + timedelta(seconds=30)
        self.pass_once(service, discovery=windows)

        self.assertEqual(set(service.workers), {service._work_key(windows[MAX_SCAN_PREPARES])},
                         "the windows the first pass never looked at are served next time")
        await self.drain(service)

    async def test_the_group_walk_gets_the_service_only_when_the_private_queue_cannot(self):
        """The group queue is walked after the private one, under the same budget.

        Group learning runs after the private queue, and one pass may find more due
        groups than it may serve. Held private windows are looks, not services, so
        they must not stop the walk before the group queue — but a private window
        that is really served spends the pass' only service, and the group queue
        waits for the next pass instead of taking a second one.
        """
        service = self.service()
        private = tuple(context(number) for number in range(1, 9))
        groups = tuple(group_context(number) for number in range(1, 9))
        due_at = NOW - timedelta(seconds=1)
        for window in private[:7]:
            service.next_checks[window.key] = due_at
            self.learned(service, window)
        service.next_checks[private[7].key] = due_at
        for window in groups:
            service.next_checks[window.key] = due_at
        self.bind(service, private)
        service._group_candidates = lambda at: groups
        service._group_instruction = lambda ctx, at: "学习这一批"

        self.pass_once(service)

        self.assertEqual(set(service.workers), {service._work_key(private[7])},
                         "the pass walks past the held private windows and serves the first willing one")
        self.assertEqual(len(service.workers), 1,
                         "the group queue does not take a second service in the same pass")
        for window in groups:
            self.assertEqual(service.next_checks[window.key], due_at,
                             "a group window the pass did not serve keeps its own due time")
        await self.drain(service)

        # The private window that was served is held by its own minute and the
        # learned ones by their rescan interval, so this pass reaches the group
        # queue and serves its first window.
        self.now = NOW + timedelta(seconds=30)
        self.pass_once(service)

        self.assertEqual(set(service.workers), {service._work_key(groups[0])},
                         "the next pass reaches the group queue and serves its first window")
        self.assertEqual(len(service.workers), 1,
                         "the group walk serves no more than the task layer runs at once")
        await self.drain(service)

    async def test_a_pass_never_submits_more_attempts_than_the_task_layer_runs(self):
        """A pass may not reserve a receipt for an attempt the queue will expire.

        The owner's task definition runs one attempt at a time, and every attempt
        keeps the deadline it was submitted with. A pass that starts eight workers
        queues seven of them behind the first; by the time they are admitted their
        deadline has passed, so each settles ``expired`` after its receipt was
        already reserved. The page has no wording for ``expired`` and shows the
        window as not learned yet, while its samples stay held for the whole lease.
        """
        service = self.service()
        layer = LimitedTaskLayer(lambda: self.now, concurrency=1)
        service.tasks = layer
        service.runner = SimpleNamespace(run=layer.run)
        windows = tuple(context(number) for number in range(1, 9))
        for window in windows:
            service.next_checks[window.key] = NOW - timedelta(seconds=1)
        self.bind(service, windows)

        self.pass_once(service)
        await self.drain(service)

        self.assertLessEqual(len(layer.submitted), layer.concurrency,
                             "a pass may not submit more attempts than the task layer runs at once")
        self.assertEqual(layer.expired, [],
                         "no window may be reserved and then expired before it starts")
        self.assertNotIn("expired", {result.get("state") for result in service.results.values()},
                         "an expired attempt would read as 'not learned yet' on the page")
        self.assertEqual(len(service.workers), 1,
                         "the pass serves exactly the one attempt the task layer admits")

    def test_the_service_budget_comes_from_the_task_definition(self):
        """The pass serves what the task layer runs, not a number of its own."""
        service = self.service()
        self.assertEqual(service._service_budget(), 1,
                         "the registered definition runs one attempt at a time")
        service.registrations = (SimpleNamespace(definition=SimpleNamespace(concurrency=3)),)
        self.assertEqual(service._service_budget(), 3,
                         "a wider definition lets the pass serve more windows")
        del service.registrations
        self.assertEqual(service._service_budget(), 1,
                         "a service without registrations falls back to one worker")


if __name__ == "__main__":
    unittest.main()
