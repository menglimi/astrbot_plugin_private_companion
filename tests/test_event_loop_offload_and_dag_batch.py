import ast
import asyncio
import contextlib
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from companion_v2.execution.models import TaskContext, TaskError, digest, encode
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import (
    AudienceRef,
    ScopeKind,
    ScopeRef,
    SourceRef,
    SubjectKind,
    SubjectRef,
    VersionRef,
)
from companion_v2.identity_policy.models import (
    AuthenticatedHostPrincipal,
    ResolvedIdentity,
    RoleBinding,
)
from companion_v2.model_gateway.cache_diagnostics import checked_signature_record
from companion_v2.model_gateway.core_observations import (
    observations_for_role,
    observations_with_scope_for_role,
    record_observation,
    request_rows,
)
from companion_v2.model_gateway.management import _cache_diagnostic_summary
from companion_v2.model_gateway.models import CacheSignature
from companion_v2.model_gateway.reply_efficiency import reply_efficiency_report
from companion_v2.role_work.state.ownership import RoleStateRecords
from companion_v2.sources import dependencies, governance
from companion_v2.sources.models import SourceRecord
from companion_v2.sources.repositories import SourceRepository
from companion_v2.storage.runtime import StorageRuntime


REPO_ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc)


def _scope(native_scope_id="user-1", kind=ScopeKind.PRIVATE):
    return ScopeRef(
        installation_id="main",
        platform="test",
        kind=kind,
        native_scope_id=native_scope_id,
        bot_id="bot-1",
        account_id="acc-1",
        conversation_id=f"conv-{native_scope_id}",
        session_id=f"sess-{native_scope_id}",
    )


def _context(native_scope_id="user-1", kind=ScopeKind.PRIVATE):
    scope = _scope(native_scope_id=native_scope_id, kind=kind)
    principal = AuthenticatedHostPrincipal("main", "host-1", "test", native_scope_id, "bot-1", "acc-1")
    user_subject = SubjectRef("main", SubjectKind.HUMAN, f"user:{native_scope_id}")
    role_subject = SubjectRef("main", SubjectKind.BOT, "role:1")
    actor = ResolvedIdentity(principal, user_subject, "bind-1", 1, 1)
    binding = RoleBinding(scope, role_subject, 1)
    audience = AudienceRef(scope, (user_subject,))
    return TaskContext(actor, binding, audience)


def _source(source_id, *, owner="test.owner", issued_at=NOW - timedelta(hours=1), expires_at=None, scope=None):
    scope = scope or _scope()
    return SourceRef(source_id, owner, scope, VersionRef(source_id, 1), issued_at, expires_at)


class _FakeQuery(dict):
    def getlist(self, key):
        return [self[key]]


class _DummyLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _sample_cache_signature(**overrides):
    h = "a" * 64
    base = {
        "model_hash": h,
        "system_hash": h,
        "system_parts": (h,),
        "tools_hash": h,
        "tool_parts": (h,),
        "context_sequence_hash": h,
        "context_multiset_hash": h,
        "context_hashes": (h,),
        "context_material_count": 1,
        "context_chars": 10,
        "context_bytes": 10,
        "context_observed": True,
        "estimates_observed": True,
        "context_est_tokens": 5,
        "system_est_tokens": 5,
        "tool_est_tokens": 0,
        "message_estimates_observed": True,
        "message_est_tokens": 10,
        "message_chars": 20,
        "message_bytes": 20,
        "request_estimate_observed": True,
        "request_est_tokens": 20,
        "context_availability_observed": True,
        "context_availability_hash": h,
        "deferred_material_count": 0,
        "unavailable_material_count": 0,
        "request_stats_observed": True,
        "message_count": 1,
        "system_message_count": 1,
        "system_chars": 10,
        "system_bytes": 10,
        "tool_count": 0,
        "tool_bytes": 0,
        "image_count": 0,
        "audio_count": 0,
        "video_count": 0,
    }
    base.update(overrides)
    return CacheSignature(**base)


class ArchitectureAndModelOptimizationTests(unittest.TestCase):
    def test_companion_v2_does_not_import_astrbot_host(self):
        offenders = []
        for path in (REPO_ROOT / "companion_v2").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "astrbot_host" or alias.name.startswith("astrbot_host."):
                            offenders.append((path.relative_to(REPO_ROOT), alias.name))
                elif isinstance(node, ast.ImportFrom) and node.module:
                    if node.module == "astrbot_host" or node.module.startswith("astrbot_host."):
                        offenders.append((path.relative_to(REPO_ROOT), node.module))
        self.assertEqual(offenders, [])

    def test_cache_signature_hex64_regex_parity(self):
        sig = _sample_cache_signature()
        self.assertEqual(sig.model_hash, "a" * 64)
        valid_09af = "0123456789abcdef" * 4
        self.assertEqual(_sample_cache_signature(model_hash=valid_09af).model_hash, valid_09af)
        invalid_values = [
            "A" * 64,
            "a" * 63,
            "a" * 65,
            "g" + "a" * 63,
            "a" * 64 + "\n",
            "",
            None,
            12345,
            b"a" * 64,
        ]
        for field_name in ("model_hash", "system_hash", "tools_hash"):
            for bad in invalid_values:
                with self.subTest(field=field_name, bad=bad):
                    with self.assertRaisesRegex(ValueError, "cache signature must contain bounded hashes"):
                        _sample_cache_signature(**{field_name: bad})
        for opt_field in ("context_sequence_hash", "context_multiset_hash", "context_availability_hash"):
            for bad in invalid_values:
                if not bad:
                    continue
                with self.subTest(field=opt_field, bad=bad):
                    with self.assertRaisesRegex(ValueError, "cache signature must contain bounded hashes"):
                        _sample_cache_signature(**{opt_field: bad})
        with self.assertRaisesRegex(ValueError, "cache signature must contain bounded hashes"):
            _sample_cache_signature(context_hashes=("a" * 64 + "\n",))

    def test_checked_signature_record_lazy_fallback_digest(self):
        h = "b" * 64
        v5 = _sample_cache_signature(model_hash=h, context_sequence_hash=h).to_record()
        with mock.patch("companion_v2.model_gateway.cache_diagnostics.digest", side_effect=AssertionError("eager digest")):
            parsed = checked_signature_record(v5)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.context_sequence_hash, h)

        legacy = dict(v5)
        legacy["version"] = 2
        legacy.pop("context_sequence_hash")
        legacy.pop("context_multiset_hash")
        legacy.pop("context_availability_hash")
        parsed_legacy = checked_signature_record(legacy)
        self.assertIsNotNone(parsed_legacy)
        self.assertEqual(parsed_legacy.context_sequence_hash, digest(tuple(legacy["context_hashes"])))

    def test_observations_non_utc_offsets_and_report_equivalence(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        runtime = StorageRuntime.open(data_root=Path(temp.name), installation_id="main")
        self.addCleanup(runtime.close)
        ctx_private = _context("user-1", ScopeKind.PRIVATE)
        ctx_group = _context("group-1", ScopeKind.GROUP)
        sig_rec = _sample_cache_signature().to_record()
        in_window_utc = NOW - timedelta(hours=2)
        # Same instant written in +08:00 whose raw prefix looks ahead of UTC if compared lexicographically.
        in_window_plus8 = in_window_utc.astimezone(timezone(timedelta(hours=8)))
        # Out-of-window UTC instant (11:40Z < start 11:55Z) rendered in +08:00 (19:40 > 11:55 lexicographically).
        too_early_plus8 = (NOW - timedelta(days=1, minutes=20)).astimezone(timezone(timedelta(hours=8)))
        too_old = NOW - timedelta(days=10)
        too_future = NOW + timedelta(minutes=30)

        observations = [
            (ctx_private, "turn-1", in_window_plus8.isoformat()),
            (ctx_private, "turn-2", (NOW - timedelta(days=1)).isoformat()),
            (ctx_group, "turn-grp", (NOW - timedelta(hours=1)).isoformat()),
            (ctx_private, "turn-early-offset", too_early_plus8.isoformat()),
            (ctx_private, "turn-old", too_old.isoformat()),
            (ctx_private, "turn-future", too_future.isoformat()),
        ]
        for context, turn_id, ts in observations:
            record_observation(runtime, context, {
                "version": 1,
                "revision": 1,
                "turn_id": turn_id,
                "event_id": "evt-" + turn_id,
                "execution_path": "astrbot_core",
                "received_at": ts,
                "observed_at": ts,
                "generation_state": "succeeded",
                "generation_finished_at": ts,
                "request_observation_available": True,
                "request_observation_complete": True,
                "tool_requery_observed": False,
                "requests": [{
                    "position": 0,
                    "state": "succeeded",
                    "started_at": ts,
                    "finished_at": ts,
                    "latency_ms": 120,
                    "provider": "prov-a",
                    "usage": {"input_tokens": 100, "output_tokens": 20, "tokens": 120,
                              "cached_input_tokens": 40, "cache_creation_tokens": 0},
                    "signature": sig_rec,
                    "measurement_basis": "provider_response",
                }],
                "delivery": {
                    "target_key": context.key,
                    "kind": "text",
                    "started_at": ts,
                    "finished_at": ts,
                    "state": "delivered",
                    "parts": [{"sequence": 0, "state": "delivered", "sent_at": ts}],
                },
            })

        start = (NOW - timedelta(days=1, minutes=5)).isoformat()
        end = NOW.isoformat()
        obs_plain = observations_for_role(runtime, ctx_private, start=start, end=end)
        self.assertEqual([item["turn_id"] for item in obs_plain], ["turn-1", "turn-grp", "turn-2"])

        obs_with_scope = observations_with_scope_for_role(runtime, ctx_private, start=None, end=end)
        self.assertEqual(
            [o["turn_id"] for o, _sk, _dt in obs_with_scope],
            ["turn-1", "turn-grp", "turn-early-offset", "turn-2", "turn-old"],
        )

        rows_with_json = request_rows([o for o, _sk, _dt in obs_with_scope], include_signature_json=True)
        rows_without_json = request_rows([o for o, _sk, _dt in obs_with_scope], include_signature_json=False)
        days = {NOW.date().isoformat()} | {row["day"] for row in rows_with_json}
        role_accounts = {day: digest(["role", day]) for day in days}
        summary_a = _cache_diagnostic_summary(runtime, role_accounts, days, ctx_private.key, core_rows=rows_with_json)
        summary_b = _cache_diagnostic_summary(runtime, role_accounts, days, ctx_private.key, core_rows=rows_without_json)
        self.assertEqual(summary_a, summary_b)

        eff_full = reply_efficiency_report(runtime, ctx_private, NOW)
        eff_start = (NOW - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
        core_7d = [o for o, sk, dt in obs_with_scope if sk == "private" and dt is not None and eff_start <= dt <= NOW]
        eff_sliced = reply_efficiency_report(runtime, ctx_private, NOW, core_observations=core_7d)
        self.assertEqual(eff_full, eff_sliced)
        self.assertEqual(eff_full["summary"]["samples"], 3)


class ValidationBatchTransactionAndRegressionTests(unittest.TestCase):
    def _open_repo(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        runtime = StorageRuntime.open(data_root=Path(temp.name), installation_id="main")
        self.addCleanup(runtime.close)
        repo = SourceRepository(runtime)
        ctx = _context()
        return runtime, repo, ctx

    @staticmethod
    def _publish(repo, ctx, reference, *, observed_at=NOW):
        payload = encode({"id": reference.source_id})
        outcome = repo.publish(SourceRecord(reference, ctx.audience, digest(payload), payload), observed_at=observed_at)
        assert outcome.is_success, outcome.failure

    def test_in_transaction_validation_batch_invalidation_and_savepoint_rollback(self):
        runtime, repo, ctx = self._open_repo()
        orig1 = _source("orig-1")
        orig2 = _source("orig-2")
        mid = _source("mid-1")
        root = _source("root-1")
        for ref in (orig1, orig2, mid, root):
            self._publish(repo, ctx, ref)
        dependencies.link(runtime, mid, (orig1, orig2), NOW)
        dependencies.link(runtime, root, (mid, orig2), NOW)

        sql_statements = []
        with runtime.transaction():
            conn = runtime._require_connection()
            conn.set_trace_callback(sql_statements.append)
            try:
                with dependencies.validation_batch(runtime, NOW):
                    dependencies.validate(runtime, root, NOW)
                    sql_first = len(sql_statements)
                    self.assertLessEqual(sql_first, 15)

                    # Repeat validation hits in-batch unguarded memoization with zero new SQL.
                    dependencies.validate(runtime, root, NOW)
                    dependencies.validate(runtime, mid, NOW)
                    self.assertEqual(len(sql_statements), sql_first)

                    # 1) Savepoint rollback must discard rows cached during the aborted savepoint.
                    active_in_sp = _source("sp-active")
                    try:
                        with runtime.transaction():
                            self._publish(repo, ctx, active_in_sp)
                            dependencies.validate(runtime, active_in_sp, NOW)
                            dependencies.link(runtime, mid, (active_in_sp,), NOW)
                            raise RuntimeError("abort savepoint")
                    except RuntimeError:
                        pass
                    with self.assertRaises(TaskError) as missing_after_rollback:
                        dependencies.validate(runtime, active_in_sp, NOW)
                    self.assertEqual(missing_after_rollback.exception.code, "source_dependency_unavailable")
                    dependencies.validate(runtime, root, NOW)

                    # 2) publish() inside transaction immediately invalidates missing/updated key.
                    late_leaf = _source("late-leaf")
                    with self.assertRaises(TaskError):
                        dependencies.validate(runtime, late_leaf, NOW)
                    self._publish(repo, ctx, late_leaf)
                    dependencies.validate(runtime, late_leaf, NOW)

                    # 3) link() inside transaction invalidates existing dependent subgraph.
                    past_window = _source("past-window", issued_at=NOW + timedelta(hours=1))
                    self._publish(repo, ctx, past_window, observed_at=NOW + timedelta(hours=1))
                    dependencies.link(runtime, mid, (past_window,), NOW + timedelta(hours=1))
                    with self.assertRaises(TaskError) as future_err:
                        dependencies.validate(runtime, root, NOW)
                    self.assertEqual(future_err.exception.code, "source_dependency_expired")

                    # 4) revoke() + revoke_dependants() clear cached active nodes.
                    clean_a = _source("clean-a")
                    clean_b = _source("clean-b")
                    self._publish(repo, ctx, clean_a)
                    self._publish(repo, ctx, clean_b)
                    dependencies.link(runtime, clean_b, (clean_a,), NOW)
                    dependencies.validate(runtime, clean_b, NOW)
                    rev = repo.revoke(clean_a, expected_governance_revision=1, observed_at=NOW)
                    self.assertTrue(rev.is_success)
                    with self.assertRaises(TaskError) as rev_err:
                        dependencies.validate(runtime, clean_b, NOW)
                    self.assertEqual(rev_err.exception.code, "source_dependency_unavailable")

                    # 5) mark_publication() inside transaction invalidates cached owner requirement.
                    guard_owner = "dyn.guarded.owner"
                    guarded_ref = _source("guarded-late", owner=guard_owner)
                    with runtime.transaction() as c:
                        # Publish row manually without mark_publication first to simulate absence -> validate succeeds unguarded.
                        c.execute(
                            """INSERT INTO sources(
                                source_id, lineage_id, source_revision, owner, installation_id, platform,
                                scope_kind, native_scope_id, bot_id, account_id, conversation_id, session_id,
                                issued_at, expires_at, recipients_json, content_hash, payload_json, state,
                                governance_revision, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                *dependencies.key(guarded_ref), guard_owner,
                                ctx.scope.installation_id, ctx.scope.platform, ctx.scope.kind.value,
                                ctx.scope.native_scope_id, ctx.scope.bot_id, ctx.scope.account_id,
                                ctx.scope.conversation_id, ctx.scope.session_id,
                                NOW.isoformat(), None, encode({"recipients": [ctx.actor.subject]}),
                                digest("x"), encode({"x": 1}), "active", 1, NOW.isoformat(), NOW.isoformat(),
                            ),
                        )
                    dependencies.validate(runtime, guarded_ref, NOW)
                    guard = governance.bind(runtime, guard_owner, lambda rec, at: None)
                    governance.mark_publication(runtime, guard_owner)
                    guard.close()
                    with self.assertRaises(TaskError) as owner_err:
                        dependencies.validate(runtime, guarded_ref, NOW)
                    self.assertEqual(owner_err.exception.code, "source_owner_unavailable")
            finally:
                conn.set_trace_callback(None)

    def test_guarded_subgraph_rechecks_owner_while_reusing_batch(self):
        runtime, repo, ctx = self._open_repo()
        checks = []
        guard = governance.bind(runtime, "guarded.owner", lambda rec, at: checks.append(rec.reference.source_id) and None)
        guarded = _source("guarded-1", owner="guarded.owner")
        plain = _source("plain-1", owner="plain.owner")
        top = _source("top-1", owner="plain.owner")
        for ref in (guarded, plain, top):
            self._publish(repo, ctx, ref)
        dependencies.link(runtime, top, (guarded, plain), NOW)
        checks.clear()

        with dependencies.validation_batch(runtime, NOW):
            dependencies.validate(runtime, top, NOW)
            dependencies.validate(runtime, top, NOW)
            self.assertEqual(checks, ["guarded-1", "guarded-1"])
            guard.close()
            with self.assertRaises(TaskError) as err:
                dependencies.validate(runtime, top, NOW)
            self.assertEqual(err.exception.code, "source_owner_unavailable")

    def test_link_deep_chain_bounded_sql_and_cycle_detection(self):
        runtime, repo, ctx = self._open_repo()
        chain = [_source(f"node-{i}") for i in range(40)]
        for ref in chain:
            self._publish(repo, ctx, ref)
        for i in range(1, len(chain)):
            dependencies.link(runtime, chain[i], (chain[i - 1],), NOW)
        top = _source("node-top")
        self._publish(repo, ctx, top)

        sql_statements = []
        conn = runtime._require_connection()
        conn.set_trace_callback(sql_statements.append)
        try:
            dependencies.link(runtime, top, (chain[-1], chain[-2]), NOW)
        finally:
            conn.set_trace_callback(None)
        self.assertLessEqual(len(sql_statements), 25)

        with self.assertRaises(TaskError) as err:
            dependencies.link(runtime, chain[0], (top,), NOW)
        self.assertEqual(err.exception.code, "source_dependency_cycle")

    def test_shared_source_checks_owner_and_current_record_before_history_scan(self):
        runtime, repo, ctx = self._open_repo()
        records = RoleStateRecords(runtime)
        non_state = _source("msg-1", owner="conversation.native_input")
        # Non-role-state owners must short-circuit without touching SQLite.
        with mock.patch.object(runtime, "read_all", side_effect=AssertionError("unexpected SQL")):
            self.assertFalse(records.shared_source(ctx, non_state))

        state_src = _source("role-state-cur", owner="role.state")
        with runtime.transaction() as c:
            c.execute(
                """INSERT INTO role_state_records(owner_key, record_id, revision, updated_at, record_json)
                   VALUES (?, 'current', 1, ?, ?)""",
                (records.owner_key(ctx), NOW.isoformat(), encode({"sources": [state_src]})),
            )
        with mock.patch.object(runtime, "read_all", side_effect=AssertionError("unexpected history scan")):
            self.assertTrue(records.shared_source(ctx, state_src))

    def test_state_evidence_control_validated_skips_duplicate_root_check_when_unguarded(self):
        from companion_v2.role_work.state.evidence import StateEvidence

        runtime, repo, ctx = self._open_repo()
        records = RoleStateRecords(runtime)
        state_src = _source("role-state-single-pass", owner="role.state")
        self._publish(repo, ctx, state_src)
        with runtime.transaction() as c:
            c.execute(
                """INSERT INTO role_state_records(owner_key, record_id, revision, updated_at, record_json)
                   VALUES (?, 'current', 1, ?, ?)""",
                (records.owner_key(ctx), NOW.isoformat(), encode({"sources": [state_src]})),
            )
        fake_svc = SimpleNamespace(
            runtime=runtime,
            records=records,
            tasks=SimpleNamespace(now=lambda: NOW),
            access=SimpleNamespace(sources=repo),
        )
        evidence = StateEvidence(fake_svc)
        with dependencies.validation_batch(runtime, NOW):
            evidence.validate(ctx, (state_src,), at=NOW, control=True)
            with mock.patch.object(repo, "validate_reference", side_effect=AssertionError("duplicate root validation")):
                self.assertTrue(evidence.visible(ctx, (state_src,), at=NOW, control_validated=True))


class EventLoopOffloadTests(unittest.IsolatedAsyncioTestCase):
    async def _measure_concurrent_ticks(self, coro):
        done = asyncio.Event()
        ticks = 0

        async def ticker():
            nonlocal ticks
            while not done.is_set():
                ticks += 1
                await asyncio.sleep(0.01)

        tick_task = asyncio.create_task(ticker())
        try:
            result = await coro
        finally:
            done.set()
            await tick_task
        return result, ticks

    async def test_web_apis_offload_to_worker_thread_without_blocking_loop(self):
        from astrbot_host.life_api import LifeWebAPI
        from astrbot_host.native_perception_api import NativePerceptionWebAPI
        from astrbot_host.state_api import StateWebAPI

        loop_thread = threading.get_ident()
        seen_threads = {}

        def slow_sync(label, value):
            def inner(*_args, **_kwargs):
                seen_threads[label] = threading.get_ident()
                time.sleep(0.08)
                return value
            return inner

        from companion_v2.role_work.state.configuration import DEFAULTS as STATE_DEFAULTS, GROUP as STATE_GROUP
        fake_controller = SimpleNamespace(
            _control_lock=_DummyLock(),
            _terminated=False,
            session=SimpleNamespace(application=SimpleNamespace(now=lambda: NOW, proactive=None,
                                   contact_schedule=SimpleNamespace(news_events=SimpleNamespace(
                                       probe=mock.AsyncMock(return_value={"ok": True}), port=None),
                                       weather_events=None, official_alert_events=None),
                                   configuration=SimpleNamespace(read=slow_sync("cfg", Outcome.success(
                                       SimpleNamespace(values={
                                           f"{STATE_GROUP}.{k}": v for k, v in STATE_DEFAULTS.items()
                                       }))))),
                                   profile=SimpleNamespace(target=_context())),
            last_session=None,
            runtime=object(),
            settings=SimpleNamespace(profile_id="p1"),
            target_id="t1",
            now=lambda: NOW,
        )
        fake_req = SimpleNamespace(username="admin", query=_FakeQuery())

        # 1) StateWebAPI.status
        fake_state_mgr = SimpleNamespace(status=slow_sync("state_status", Outcome.success({"ok": True})))
        with (mock.patch("astrbot_host.state_api.request", fake_req),
              mock.patch("astrbot_host.state_api.dashboard_operator", return_value="op"),
              mock.patch("astrbot_host.state_api.StateManagement", return_value=fake_state_mgr),
              mock.patch("astrbot_host.web_api.outcome_response", side_effect=lambda o: o)):
            out, ticks = await self._measure_concurrent_ticks(StateWebAPI(fake_controller).status())
        self.assertTrue(out.is_success)
        self.assertGreaterEqual(ticks, 3)
        self.assertNotEqual(seen_threads["state_status"], loop_thread)

        # 2) StateWebAPI.weather
        with (mock.patch("astrbot_host.state_api.request", fake_req),
              mock.patch("astrbot_host.state_api.dashboard_operator", return_value="op"),
              mock.patch("astrbot_host.state_api.StateManagement") as sm_cls,
              mock.patch("astrbot_host.web_api.outcome_response", side_effect=lambda o: o)):
            sm_cls.return_value.life.authorize = lambda _op: None
            fake_controller.session.application.runtime = SimpleNamespace(read_snapshot=contextlib.nullcontext)
            out, ticks = await self._measure_concurrent_ticks(StateWebAPI(fake_controller).weather())
        self.assertTrue(out.is_success, getattr(out, "failure", None))
        self.assertGreaterEqual(ticks, 3)
        self.assertNotEqual(seen_threads["cfg"], loop_thread)

        # 3) LifeWebAPI.status & command
        fake_life_mgr = SimpleNamespace(
            status=slow_sync("life_status", {"journals": []}),
            authorize=slow_sync("life_auth", None),
            command=mock.AsyncMock(return_value=Outcome.success({"done": True})),
        )
        with (mock.patch("astrbot_host.life_api.request", fake_req),
              mock.patch("astrbot_host.life_api.dashboard_operator", return_value="op"),
              mock.patch("astrbot_host.life_api.RoleLifeManagement", return_value=fake_life_mgr),
              mock.patch("astrbot_host.web_api.outcome_response", side_effect=lambda o: o)):
            out, ticks = await self._measure_concurrent_ticks(LifeWebAPI(fake_controller).status())
            self.assertTrue(out.is_success)
            self.assertGreaterEqual(ticks, 3)
            self.assertNotEqual(seen_threads["life_status"], loop_thread)

            fake_cmd_req = SimpleNamespace(
                username="admin",
                query={},
                body=mock.AsyncMock(return_value=b'{"action":"diary","local_date":"2026-11-19"}'),
            )
            with mock.patch("astrbot_host.life_api.request", fake_cmd_req):
                out_cmd, ticks_cmd = await self._measure_concurrent_ticks(LifeWebAPI(fake_controller).command())
            self.assertTrue(out_cmd.is_success)
            self.assertGreaterEqual(ticks_cmd, 3)
            self.assertNotEqual(seen_threads["life_auth"], loop_thread)

        # 4) NativePerceptionWebAPI: status, news_diagnostic, runtime_snapshot, material_pool
        perc = NativePerceptionWebAPI(fake_controller)
        with (mock.patch("astrbot_host.native_perception_api.request", fake_req),
              mock.patch("astrbot_host.native_perception_api.dashboard_operator", return_value="op"),
              mock.patch.object(perc, "_status", slow_sync("perc_status", {
                  "model": {"settings": {"calendar_enabled": False}},
                  "environment": {"settings": {"planning_weather_enabled": False}},
              })),
              mock.patch("astrbot_host.native_perception_api.ContactManagement",
                         return_value=SimpleNamespace(status=lambda _op: Outcome.success({"settings": {}}))),
              mock.patch.object(perc, "_weather_snapshot", new=mock.AsyncMock(return_value={"status": "disabled"})),
              mock.patch.object(perc, "_screen_snapshot", side_effect=slow_sync("perc_screen", {})),
              mock.patch.object(perc, "_reality_snapshot", return_value={}),
              mock.patch.object(perc, "_reality_camera_snapshot", return_value={}),
              mock.patch.object(perc, "_other_injections", return_value={}),
              mock.patch("astrbot_host.native_perception_api.ProactiveContactManagement",
                         return_value=SimpleNamespace(status=lambda _op: Outcome.success({"settings": {}}))),
              mock.patch("astrbot_host.native_perception_api.read_role_material_pool",
                         side_effect=slow_sync("perc_pool", {"items": [], "pool": {}, "summary": {}})),
              mock.patch("astrbot_host.web_api.outcome_response", side_effect=lambda o: o)):
            out_s, ticks_s = await self._measure_concurrent_ticks(perc.status())
            self.assertTrue(out_s.is_success)
            self.assertGreaterEqual(ticks_s, 3)
            self.assertNotEqual(seen_threads["perc_status"], loop_thread)

            out_n, ticks_n = await self._measure_concurrent_ticks(perc.news_diagnostic())
            self.assertTrue(out_n.is_success)
            self.assertGreaterEqual(ticks_n, 3)

            out_r, ticks_r = await self._measure_concurrent_ticks(perc.runtime_snapshot())
            self.assertTrue(out_r.is_success)
            self.assertGreaterEqual(ticks_r, 3)
            self.assertNotEqual(seen_threads["perc_screen"], loop_thread)

            out_m, ticks_m = await self._measure_concurrent_ticks(perc.material_pool())
            self.assertTrue(out_m.is_success)
            self.assertGreaterEqual(ticks_m, 3)
            self.assertNotEqual(seen_threads["perc_pool"], loop_thread)

    async def test_native_extensions_offload_to_worker_thread_without_blocking_loop(self):
        from astrbot_host.native_extensions.game.native_context import room_background as game_room_background
        from astrbot_host.native_extensions.reality.host_context import RealityHostContext
        from astrbot_host.native_extensions.together.native_context import (
            room_background as together_room_background,
            room_work_context as together_room_work_context,
        )

        loop_thread = threading.get_ident()
        seen_threads = {}
        ctx = _context()

        def slow_prompt(label, text="bg"):
            def inner(*_args, **_kwargs):
                seen_threads[label] = threading.get_ident()
                time.sleep(0.08)
                return SimpleNamespace(instruction=text)
            return inner

        resolved = SimpleNamespace(application=object(), context=ctx, persona_prompt="p")
        game_rt = SimpleNamespace(private_qq_game_context_enabled=True, controller=SimpleNamespace(now=lambda: NOW))
        with mock.patch("astrbot_host.native_extensions.game.native_context.build_core_reply_prompt",
                        side_effect=slow_prompt("game", "game-bg")):
            out, ticks = await self._measure_concurrent_ticks(
                game_room_background(game_rt, "room-1", "user-1", "hi", resolved=resolved)
            )
        self.assertEqual(out, "game-bg")
        self.assertGreaterEqual(ticks, 3)
        self.assertNotEqual(seen_threads["game"], loop_thread)

        def slow_analysis(*_args, **_kwargs):
            seen_threads["together_work"] = threading.get_ident()
            time.sleep(0.08)
            return Outcome.success([])

        together_app = SimpleNamespace(
            messages=SimpleNamespace(memory_context=None),
            screen_repository=SimpleNamespace(latest_analysis=slow_analysis),
        )
        together_rt = SimpleNamespace(
            enable_memory_context=True,
            controller=SimpleNamespace(now=lambda: NOW),
        )
        together_resolved = SimpleNamespace(application=together_app, context=ctx)
        with (mock.patch("astrbot_host.native_extensions.together.native_context.resolve_room_context",
                         new=mock.AsyncMock(return_value=together_resolved)),
              mock.patch("astrbot_host.native_extensions.together.native_context.build_core_reply_prompt",
                         side_effect=slow_prompt("together_bg", "together-bg"))):
            bg, ticks_bg = await self._measure_concurrent_ticks(together_room_background(together_rt, "r", "q"))
            self.assertEqual(bg, "together-bg")
            self.assertGreaterEqual(ticks_bg, 3)
            self.assertNotEqual(seen_threads["together_bg"], loop_thread)

            work, ticks_w = await self._measure_concurrent_ticks(together_room_work_context(together_rt, "r"))
            self.assertEqual(work, {"available": True, "context_available": False})
            self.assertGreaterEqual(ticks_w, 3)
            self.assertNotEqual(seen_threads["together_work"], loop_thread)

        # RealityHostContext.realtime & synthesize
        def slow_history_read(*_args, **_kwargs):
            seen_threads["reality_hist"] = threading.get_ident()
            time.sleep(0.08)
            return [{"role": "user", "text": "hello"}]

        from dataclasses import asdict
        from companion_v2.conversation.reply_settings import VOICE_FIELDS
        from companion_v2.conversation.voice_reply import VoiceReplyPolicy

        voice_defaults = {
            f"voice_{k}": v for k, v in asdict(VoiceReplyPolicy()).items() if k in VOICE_FIELDS
        }

        def slow_reply_settings(*_args, **_kwargs):
            seen_threads["reality_voice_cfg"] = threading.get_ident()
            time.sleep(0.08)
            return {"settings": dict(voice_defaults)}

        reality_app = SimpleNamespace(
            proactive=SimpleNamespace(history=SimpleNamespace(conversation=SimpleNamespace(
                capture=lambda *_a, **_kw: {},
                read=slow_history_read,
            ))),
            reply_settings=SimpleNamespace(capture=slow_reply_settings),
        )
        reality_runtime = SimpleNamespace(
            _user=lambda _uid, create=False: {"ok": True},
            _reality_touch_audio_consented=lambda _u: True,
        )
        reality_controller = SimpleNamespace(now=lambda: NOW, context=object())
        host_ctx = RealityHostContext(reality_controller, None)
        target_tuple = (None, reality_app, ctx, reality_runtime)
        with (mock.patch.object(host_ctx, "target", return_value=target_tuple),
              mock.patch.object(host_ctx, "verified_persona",
                                new=mock.AsyncMock(return_value={"prompt": "persona"})),
              mock.patch.object(host_ctx, "origin", return_value="test:FriendMessage:user-1"),
              mock.patch("astrbot_host.core_reply_context.build_core_reply_prompt",
                         side_effect=slow_prompt("reality_bg", "scene"))):
            rt_res, ticks_rt = await self._measure_concurrent_ticks(host_ctx.realtime("user-1"))
            self.assertTrue(rt_res["available"])
            self.assertGreaterEqual(ticks_rt, 3)
            self.assertNotEqual(seen_threads["reality_bg"], loop_thread)
            self.assertNotEqual(seen_threads["reality_hist"], loop_thread)

            with mock.patch("astrbot_host.voice.NativeVoiceReply") as voice_cls:
                voice_cls.return_value.render = mock.AsyncMock(return_value=None)
                syn_res, ticks_syn = await self._measure_concurrent_ticks(host_ctx.synthesize("hi", user_id="user-1"))
            self.assertFalse(syn_res["success"])
            self.assertGreaterEqual(ticks_syn, 3)
            self.assertNotEqual(seen_threads["reality_voice_cfg"], loop_thread)


if __name__ == "__main__":
    unittest.main()
