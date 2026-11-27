"""Regression tests for observed_model_catalog perf, expression tolerance, and mobile persistence."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from astrbot_host.native_extensions.reality import mobile_gateway
from astrbot_host.native_extensions.reality.mobile_gateway import (
    MOBILE_STATE_FILENAME,
    MobileGatewayMixin,
    _MobileRequestState,
    _mobile_request_state,
)
from companion_v2.expression import learning, style_service
from companion_v2.execution.models import TaskContext, TaskError, encode
from companion_v2.foundation.refs import AudienceRef, ScopeKind, ScopeRef, SubjectKind, SubjectRef
from companion_v2.identity_policy.models import AuthenticatedHostPrincipal, ResolvedIdentity, RoleBinding
from companion_v2.model_gateway.core_observations import (
    query_observed_model_catalog,
    record_observation,
    role_key,
    sync_observed_model_catalog,
)
from companion_v2.storage.core_reply_telemetry_schema import (
    OBSERVED_MODEL_CATALOG_TABLE,
    ensure_core_reply_telemetry_schema,
)
from companion_v2.storage.runtime import StorageRuntime
from companion_v2.storage.telemetry_archive import archive_cold_telemetry, load_core_daily_rollups


NOW = datetime(2026, 11, 27, 10, 0, tzinfo=timezone.utc)


def _sample_context(role_id: str = "persona-a", conv_id: str = "conv-1") -> TaskContext:
    inst = "inst-catalog-test"
    scope = ScopeRef(inst, "onebot", ScopeKind.PRIVATE, "200", "bot-a", "login-a", conv_id, "sess-1")
    principal = AuthenticatedHostPrincipal(inst, "host-1", "onebot", "200", "bot-a", "login-a")
    subject = SubjectRef(inst, SubjectKind.HUMAN, "user-200")
    actor = ResolvedIdentity(principal, subject, "bind-1", 1, 1)
    audience = AudienceRef(scope, (subject,))
    role = SubjectRef(inst, SubjectKind.BOT, role_id)
    binding = RoleBinding(scope, role, 1)
    return TaskContext(actor, binding, audience)


def _observation(
    turn_id: str,
    *,
    revision: int = 1,
    received_at: datetime = NOW,
    requests: list[dict],
    extra_materials: int = 0,
) -> dict:
    iso = received_at.isoformat()
    materials = [
        {"owner": "conversation", "purpose": f"mat_{idx}", "state": "ready", "elapsed_ms": 1, "chars": 200}
        for idx in range(extra_materials)
    ]
    return {
        "version": 1,
        "revision": revision,
        "turn_id": turn_id,
        "event_id": "evt-" + turn_id,
        "execution_path": "astrbot_core",
        "received_at": iso,
        "observed_at": iso,
        "generation_state": "completed",
        "requests": requests,
        "tools": [],
        "materials": materials,
    }


def _req(provider: str, *, tokens: int, inp: int = 0, cached: int = 0, cache_create: int = 0, out: int = 0, pos: int = 1) -> dict:
    return {
        "position": pos,
        "state": "returned",
        "started_at": NOW.isoformat(),
        "finished_at": NOW.isoformat(),
        "latency_ms": 50,
        "provider": provider,
        "measurement_basis": "provider_response",
        "usage": {
            "input_tokens": inp,
            "cached_input_tokens": cached,
            "cache_creation_tokens": cache_create,
            "output_tokens": out,
            "tokens": tokens,
            "availability": "reported",
            "cache_availability": "reported",
            "cost_availability": "unavailable",
        },
    }


class ObservedModelCatalogPerfTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="companion-catalog-test-")
        self.addCleanup(self._tmp.cleanup)
        self.runtime = StorageRuntime.open(data_root=Path(self._tmp.name), installation_id="inst-catalog-test")
        self.addCleanup(self.runtime.close)
        self.ctx_a = _sample_context("persona-a", "conv-a")
        self.ctx_b = _sample_context("persona-b", "conv-b")

    def test_incremental_insert_revision_update_and_stale_revision_never_double_count(self):
        # Insert turn-1 with two calls to prov-1
        record_observation(self.runtime, self.ctx_a, _observation(
            "turn-1", revision=1, requests=[
                _req("prov-1", tokens=100, inp=70, cached=20, cache_create=5, out=30, pos=1),
                _req("prov-1", tokens=50, inp=30, cached=10, cache_create=0, out=20, pos=2),
            ],
        ))
        # Revision 2 updates turn-1 (replaces prov-1 counters and adds prov-2)
        record_observation(self.runtime, self.ctx_a, _observation(
            "turn-1", revision=2, requests=[
                _req("prov-1", tokens=200, inp=140, cached=30, cache_create=10, out=60, pos=1),
                _req("prov-2", tokens=80, inp=50, cached=0, cache_create=0, out=30, pos=2),
            ],
        ))
        # Stale revision 1 MUST be ignored without corrupting catalog counters
        record_observation(self.runtime, self.ctx_a, _observation(
            "turn-1", revision=1, requests=[_req("prov-1", tokens=999999, inp=999999, out=0, pos=1)],
        ))
        # Second role writes prov-1 separately
        record_observation(self.runtime, self.ctx_b, _observation(
            "turn-2", revision=1, requests=[_req("prov-1", tokens=40, inp=30, out=10, pos=1)],
        ))

        by_a = {row["provider_id"]: row for row in query_observed_model_catalog(self.runtime, role_key_value=role_key(self.ctx_a))}
        self.assertEqual(by_a["prov-1"], {
            "provider_id": "prov-1", "calls": 1, "requests": 1,
            "total_tokens": 200, "input_tokens": 140, "cached_input_tokens": 30,
            "cache_creation_tokens": 10, "output_tokens": 60,
        })
        self.assertEqual(by_a["prov-2"]["total_tokens"], 80)

        by_all = {row["provider_id"]: row for row in query_observed_model_catalog(self.runtime)}
        self.assertEqual(by_all["prov-1"]["calls"], 2)
        self.assertEqual(by_all["prov-1"]["requests"], 2)
        self.assertEqual(by_all["prov-1"]["total_tokens"], 240)

    def test_cold_start_backfill_and_out_of_band_raw_sql_sync_exact_totals(self):
        ensure_core_reply_telemetry_schema(self.runtime)
        rk = role_key(self.ctx_a)
        ctx_json = encode(self.ctx_a)
        # Simulate pre-upgrade legacy rows written directly into astrbot_core_reply_observations
        with self.runtime.transaction() as conn:
            for idx in range(12):
                rec = _observation(f"legacy-{idx}", revision=1, requests=[_req("legacy-prov", tokens=100, inp=60, out=40)])
                conn.execute(
                    """INSERT INTO astrbot_core_reply_observations
                       (turn_id, revision, role_key, context_key, scope_kind, context_json, received_at, observed_at, record_json, context_hash)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '')""",
                    (rec["turn_id"], 1, rk, self.ctx_a.key, "private", ctx_json, rec["received_at"], rec["observed_at"], encode(rec)),
                )

        # Chunked cold-start backfill with tiny batch_size
        self.assertTrue(sync_observed_model_catalog(self.runtime, batch_size=16))
        rows = {row["provider_id"]: row for row in query_observed_model_catalog(self.runtime, role_key_value=rk)}
        self.assertEqual(rows["legacy-prov"]["calls"], 12)
        self.assertEqual(rows["legacy-prov"]["requests"], 12)
        self.assertEqual(rows["legacy-prov"]["total_tokens"], 1200)

        # Out-of-band raw SQL INSERT + UPDATE are caught automatically by watermark check
        with self.runtime.transaction() as conn:
            rec_new = _observation("raw-new", revision=1, requests=[_req("legacy-prov", tokens=300, inp=200, out=100)])
            conn.execute(
                """INSERT INTO astrbot_core_reply_observations
                   (turn_id, revision, role_key, context_key, scope_kind, context_json, received_at, observed_at, record_json, context_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '')""",
                (rec_new["turn_id"], 1, rk, self.ctx_a.key, "private", ctx_json, rec_new["received_at"], rec_new["observed_at"], encode(rec_new)),
            )
            rec_upd = _observation("legacy-0", revision=2, requests=[_req("legacy-prov", tokens=500, inp=300, out=200)])
            conn.execute(
                "UPDATE astrbot_core_reply_observations SET revision = 2, record_json = ? WHERE turn_id = 'legacy-0'",
                (encode(rec_upd),),
            )

        updated = {row["provider_id"]: row for row in query_observed_model_catalog(self.runtime, role_key_value=rk)}
        self.assertEqual(updated["legacy-prov"]["requests"], 13)
        self.assertEqual(updated["legacy-prov"]["total_tokens"], 1900)  # 1200 + 300 + (500 - 100)

    def test_out_of_band_delete_plus_insert_rebuilds_catalog_without_orphan_inflation(self):
        rk = role_key(self.ctx_a)
        ctx_json = encode(self.ctx_a)
        for idx in range(3):
            record_observation(self.runtime, self.ctx_a, _observation(
                f"t-{idx}", revision=1, requests=[_req("prov-mix", tokens=100, inp=60, out=40)],
            ))
        initial = {row["provider_id"]: row for row in query_observed_model_catalog(self.runtime, role_key_value=rk)}
        self.assertEqual((initial["prov-mix"]["calls"], initial["prov-mix"]["requests"], initial["prov-mix"]["total_tokens"]), (3, 3, 300))

        # Out-of-band raw SQL DELETE 1 old row + INSERT 2 new rows -> live_count(4) >= prev_count(3),
        # live_max_rowid(5) > prev_max(3), live_rev_sum(4) >= prev_rev(3). Must detect orphan & rebuild cleanly.
        with self.runtime.transaction() as conn:
            conn.execute("DELETE FROM astrbot_core_reply_observations WHERE turn_id = 't-0'")
            for idx in (3, 4):
                rec = _observation(f"t-{idx}", revision=1, requests=[_req("prov-mix", tokens=100, inp=60, out=40)])
                conn.execute(
                    """INSERT INTO astrbot_core_reply_observations
                       (turn_id, revision, role_key, context_key, scope_kind, context_json, received_at, observed_at, record_json, context_hash)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '')""",
                    (rec["turn_id"], 1, rk, self.ctx_a.key, "private", ctx_json, rec["received_at"], rec["observed_at"], encode(rec)),
                )

        after = {row["provider_id"]: row for row in query_observed_model_catalog(self.runtime, role_key_value=rk)}
        self.assertEqual((after["prov-mix"]["calls"], after["prov-mix"]["requests"], after["prov-mix"]["total_tokens"]), (4, 4, 400))
        orphan = self.runtime.read_one(
            """SELECT COUNT(*) AS n FROM observed_core_reply_turn_usage u
               WHERE NOT EXISTS (SELECT 1 FROM astrbot_core_reply_observations o WHERE o.turn_id = u.turn_id)""",
            (),
        )
        self.assertEqual(orphan["n"], 0)

    def test_archive_uses_in_transaction_revision_when_concurrent_upgrade_occurs(self):
        rk = role_key(self.ctx_a)
        old_dt = NOW - timedelta(days=45)
        record_observation(self.runtime, self.ctx_a, _observation(
            "cold-race", revision=1, received_at=old_dt,
            requests=[_req("prov-race", tokens=100, inp=60, out=40)],
        ))
        record_observation(self.runtime, self.ctx_a, _observation(
            "hot-stay", revision=1, received_at=NOW,
            requests=[_req("prov-race", tokens=250, inp=150, out=100)],
        ))

        # Simulate concurrent revision bump happening between cold_rows read and archive write transaction.
        orig_archive_core = __import__("companion_v2.storage.telemetry_archive", fromlist=["TelemetryArchiveStore"]).TelemetryArchiveStore.archive_core_rows

        def bump_before_tx(store_self, rows, *, archived_at):
            res = orig_archive_core(store_self, rows, archived_at=archived_at)
            record_observation(self.runtime, self.ctx_a, _observation(
                "cold-race", revision=3, received_at=old_dt,
                requests=[_req("prov-race", tokens=100, inp=60, out=40)],
            ))
            return res

        with patch("companion_v2.storage.telemetry_archive.TelemetryArchiveStore.archive_core_rows", autospec=True, side_effect=bump_before_tx):
            stats = archive_cold_telemetry(self.runtime, ttl_days=30, now=NOW)
        self.assertEqual(stats["core_rows_archived"], 1)

        wm = json.loads(self.runtime.read_one(
            "SELECT value FROM runtime_metadata WHERE key = 'observed_model_catalog.watermark.v1'", ()
        )["value"])
        # Remaining live row is hot-stay (revision=1), so watermark revision_sum MUST be 1 (not underflowed/drifted).
        self.assertEqual((wm["row_count"], wm["revision_sum"]), (1, 1))
        live = {row["provider_id"]: row for row in query_observed_model_catalog(self.runtime, role_key_value=rk)}
        self.assertEqual(live["prov-race"]["total_tokens"], 250)

    def test_archive_cold_telemetry_subtracts_catalog_for_exact_rollup_parity(self):
        rk = role_key(self.ctx_a)
        old_dt = NOW - timedelta(days=45)
        for idx in range(3):
            record_observation(self.runtime, self.ctx_a, _observation(
                f"cold-{idx}", revision=1, received_at=old_dt,
                requests=[_req("prov-archive", tokens=1000, inp=600, out=400)],
            ))
        for idx in range(2):
            record_observation(self.runtime, self.ctx_a, _observation(
                f"hot-{idx}", revision=1, received_at=NOW,
                requests=[_req("prov-archive", tokens=500, inp=300, out=200)],
            ))

        stats = archive_cold_telemetry(self.runtime, ttl_days=30, now=NOW)
        self.assertEqual(stats["core_rows_archived"], 3)

        live = {row["provider_id"]: row for row in query_observed_model_catalog(self.runtime, role_key_value=rk)}
        self.assertEqual(live["prov-archive"]["calls"], 2)
        self.assertEqual(live["prov-archive"]["requests"], 2)
        self.assertEqual(live["prov-archive"]["total_tokens"], 1000)

        rollup_tokens = sum(
            int((item["payload"].get("provider_totals") or {}).get("prov-archive", {}).get("total_tokens") or 0)
            for item in load_core_daily_rollups(self.runtime, role_key_value=rk)
        )
        # Live catalog + archived rollups MUST equal exact 4000 lifetime tokens (zero double-counting).
        self.assertEqual(live["prov-archive"]["total_tokens"] + rollup_tokens, 4000)

    def test_steady_state_catalog_query_avoids_json_each_scan_and_runs_fast(self):
        rk = role_key(self.ctx_a)
        for idx in range(200):
            record_observation(self.runtime, self.ctx_a, _observation(
                f"perf-{idx}", revision=1,
                requests=[_req(f"prov-{idx % 4}", tokens=100, inp=60, out=40)],
                extra_materials=40,
            ))

        # Warm and verify query plan uses observed_model_catalog PK lookup (zero json_each / obs scan)
        plan = self.runtime.read_all(
            f"EXPLAIN QUERY PLAN SELECT provider_id, calls FROM {OBSERVED_MODEL_CATALOG_TABLE} WHERE role_key = ?",
            (rk,),
        )
        plan_text = " ".join(str(row["detail"]) for row in plan)
        self.assertIn(OBSERVED_MODEL_CATALOG_TABLE, plan_text)
        self.assertNotIn("json_each", plan_text.lower())

        t0 = perf_counter()
        rows = query_observed_model_catalog(self.runtime, role_key_value=rk)
        elapsed_ms = (perf_counter() - t0) * 1000.0
        self.assertEqual(len(rows), 4)
        self.assertEqual(sum(r["total_tokens"] for r in rows), 20_000)
        # Well under 50ms even on loaded CI disks (steady-state is typically < 2ms).
        self.assertLess(elapsed_ms, 50.0, f"expected sub-50ms steady-state query, got {elapsed_ms:.2f}ms")


class ExpressionLearningToleranceAndDiagnosticsTests(unittest.TestCase):
    def _signal(self, **overrides) -> dict:
        base = {
            "dimension": "punctuation",
            "observation": "常以短句停顿回应",
            "proposal": "自然收束短句",
            "confidence": 0.75,
            "expression": {
                "kind": "grammar",
                "situation": "日常简短回应时",
                "pattern": "先应一声，再补一句简短说明",
                "avoid": "需要展开解释长问题时",
            },
            "applicability": {
                "channels": ["private"],
                "relationship_stages": ["any"],
                "emotion_gates": ["any"],
                "intent": "any",
                "keywords": [],
            },
            "evidence_handles": ["sample:a"],
        }
        base.update(overrides)
        return base

    def test_per_signal_tolerance_preserves_valid_signals_and_records_exact_rejection_paths(self):
        payload = {
            "summary": "混合批次观察",
            "signals": [
                self._signal(observation="有效规则一"),
                self._signal(observation="编造证据", evidence_handles=["sample:fabricated"]),
                self._signal(dimension="diction", observation="有效规则二", proposal="偶尔用语气助词软化",
                             expression={"kind": "style", "situation": "轻松接话时", "pattern": "那倒也是", "avoid": "严肃讨论时"}),
                self._signal(observation="非法意图", applicability={
                    "channels": ["private"], "relationship_stages": ["any"],
                    "emotion_gates": ["any"], "intent": "not_a_valid_intent", "keywords": [],
                }),
            ],
        }
        parsed = style_service.parse_output(
            json.dumps(payload, ensure_ascii=False),
            allowed_evidence={"sample:a"},
            default_channels=("private",),
            limit=6,
        )
        self.assertEqual(len(parsed.signals), 2)
        self.assertEqual([s.observation for s in parsed.signals], ["有效规则一", "有效规则二"])
        self.assertEqual(
            [(r.index, r.reason, r.field_path) for r in parsed.rejected_signals],
            [
                (1, "invalid_evidence", "signals[1].evidence_handles"),
                (3, "invalid_applicability", "signals[3].applicability.intent"),
            ],
        )

    def test_all_invalid_signals_raise_task_error_with_exact_parse_error_and_field_path(self):
        payload = {
            "summary": "全部无效",
            "signals": [
                self._signal(evidence_handles=["sample:missing"]),
                self._signal(confidence=1.5),
            ],
        }
        with self.assertRaises(TaskError) as raised:
            style_service.parse_output(
                json.dumps(payload, ensure_ascii=False),
                allowed_evidence={"sample:a"},
                default_channels=("private",),
                limit=6,
            )
        self.assertEqual(raised.exception.code, "expression_output_invalid")
        self.assertEqual(raised.exception.details["parse_error"], "invalid_evidence")
        self.assertEqual(raised.exception.details["field_path"], "signals[0].evidence_handles")
        self.assertEqual(raised.exception.details["rejected_signals"], 2)

    def test_strict_learning_parse_and_limit_cap_remain_enforced(self):
        mixed = {"summary": "混合", "signals": [self._signal(), self._signal(evidence_handles=["sample:nope"])]}
        with self.assertRaises(learning.ExpressionOutputParseError) as raised:
            learning.parse_output(mixed, allowed_evidence={"sample:a"})
        self.assertEqual(str(raised.exception), "invalid_evidence")
        self.assertEqual(raised.exception.field_path, "signals[1].evidence_handles")

        over_limit = {"summary": "超限", "signals": [self._signal(), self._signal(), self._signal()]}
        with self.assertRaises(TaskError) as limit_err:
            style_service.parse_output(over_limit, allowed_evidence={"sample:a"}, limit=2)
        self.assertEqual(limit_err.exception.code, "expression_output_invalid")
        self.assertEqual(limit_err.exception.details["parse_error"], "invalid_shape")
        self.assertEqual(limit_err.exception.details["field_path"], "signals")

    def test_note_refusal_logs_parse_diagnostic_instead_of_ceiling_mismatch(self):
        svc = style_service.ExpressionStyleService.__new__(style_service.ExpressionStyleService)
        obs = {
            "stage": "parse",
            "diagnosis": {
                "parse_error": "invalid_expression_form",
                "field_path": "signals[0].expression",
                "rejected_signals": 1,
            },
        }
        with self.assertLogs("companion_v2.expression.style_service", level="WARNING") as captured:
            svc._note_refusal(obs, "expression_output_invalid")
        out = "\n".join(captured.output)
        self.assertIn("表达学习输出校验失败", out)
        self.assertIn("parse_error=invalid_expression_form", out)
        self.assertIn("field_path=signals[0].expression", out)
        self.assertNotIn("上限配置与模型能力不匹配", out)


class _PersistentMobileHarness(MobileGatewayMixin):
    def __init__(self, data_dir: Path, *, pairing_token: str = "test-pairing-token-123456789012",
                 location_ttl: int = 900, context_key: str = "ctx-200"):
        self.data_dir = data_dir
        self._context_key = context_key
        self._cfg = {
            "mobile.enabled": True,
            "mobile.pairing_token": pairing_token,
            "mobile.allowed_user_id": "200",
            "mobile.location_ttl_seconds": location_ttl,
            "mobile.amap_reverse_geocode_enabled": False,
        }
        self._host_api = NS(
            reality_mobile_context=lambda uid: {
                "schema": "companion.reality-mobile-context.v1",
                "user_id": uid,
                "subject_ref": "subject:user:200",
                "context_key": self._context_key,
                "snapshot_authorized": True,
            }
        )
        self._mobile_gateway_init()

    def _cfg_bool(self, key, default=False):
        return bool(self._cfg.get(key, default))

    def _cfg_str(self, key, default=""):
        return str(self._cfg.get(key, default))

    def _cfg_int(self, key, default=0, minimum=None, maximum=None):
        value = int(self._cfg.get(key, default))
        if minimum is not None:
            value = max(minimum, value)
        if maximum is not None:
            value = min(maximum, value)
        return value

    def v2_host_api(self):
        return self._host_api

    def pair(self, token: str = "secret-bearer-token-200", *, ttl: int = 3600):
        now = time.time()
        self._mobile_store_session(token, {
            "user_id": "200",
            "device_name": "Pixel-Test",
            "created_at": now,
            "last_seen_at": now,
            "expires_at": now + ttl,
        })
        return token

    async def call(self, token: str, handler, payload: dict | None = None):
        state = _MobileRequestState(
            headers={"authorization": f"Bearer {token}"},
            host="127.0.0.1:6322",
            remote_addr="127.0.0.1",
            payload=dict(payload or {}),
        )
        ctx = _mobile_request_state.set(state)
        try:
            return await handler()
        finally:
            _mobile_request_state.reset(ctx)


class MobileGatewayRestartPersistenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="companion-mobile-persist-")
        self.addCleanup(self._tmp.cleanup)
        self.data_dir = Path(self._tmp.name)

    async def test_sessions_and_ttl_valid_locations_survive_restart_without_raw_tokens_on_disk(self):
        raw_token = "super-secret-mobile-session-token-999"
        gw1 = _PersistentMobileHarness(self.data_dir)
        gw1._schedule_v2_location_notification = Mock()
        gw1.pair(raw_token)
        body, status = await gw1.call(raw_token, gw1.mobile_location, {
            "latitude": 30.25, "longitude": 120.15,
            "place": {"matched": True, "name": "家", "kind": "home"},
        })
        self.assertEqual((status, body["ok"]), (200, True))
        await gw1._stop_mobile_server()

        state_file = self.data_dir / MOBILE_STATE_FILENAME
        self.assertTrue(state_file.is_file())
        raw_text = state_file.read_text(encoding="utf-8")
        # Raw bearer token MUST NEVER be written to disk.
        self.assertNotIn(raw_token, raw_text)
        self.assertIn(gw1._mobile_token_key(raw_token), raw_text)

        # Simulate process restart with a fresh gateway instance
        gw2 = _PersistentMobileHarness(self.data_dir)
        restored_sessions, restored_locations = gw2._mobile_load_persisted_state()
        self.assertEqual((restored_sessions, restored_locations), (1, 1))

        # Subsequent status upload succeeds with 200 (no post-restart 401!)
        dev_body, dev_status = await gw2.call(raw_token, gw2.mobile_device_status, {
            "app_state": "foreground", "battery_percent": 82,
        })
        self.assertEqual((dev_status, dev_body["ok"]), (200, True))

        # Location heartbeat succeeds with 200 (no post-restart 409!) and keeps valid RV-1 stamp
        hb_body, hb_status = await gw2.call(raw_token, gw2.mobile_location_heartbeat)
        self.assertEqual((hb_status, hb_body["ok"]), (200, True))
        self.assertEqual(hb_body["data"]["place"]["name"], "家")
        self.assertIn("_rv1", gw2._mobile_locations["200"])

    async def test_pairing_token_rotation_invalidates_persisted_state(self):
        raw_token = "token-before-rotation"
        gw1 = _PersistentMobileHarness(self.data_dir, pairing_token="old-pairing-token-123456789012")
        gw1._schedule_v2_location_notification = Mock()
        gw1.pair(raw_token)
        await gw1.call(raw_token, gw1.mobile_location, {"latitude": 30.25, "longitude": 120.15})

        gw2 = _PersistentMobileHarness(self.data_dir, pairing_token="new-pairing-token-987654321098")
        self.assertEqual(gw2._mobile_load_persisted_state(), (0, 0))
        # Stale state file from mismatched pairing token MUST be unlinked from disk.
        self.assertFalse((self.data_dir / MOBILE_STATE_FILENAME).exists())
        _body, status = await gw2.call(raw_token, gw2.mobile_device_status, {"app_state": "foreground"})
        self.assertEqual(status, 401)

    async def test_settings_pairing_token_rotation_before_stop_revokes_old_sessions_and_clears_stale_file(self):
        # Reproduce settings_methods.py:351-365 order: pairing_token is updated in config BEFORE
        # _stop_mobile_server() -> _start_mobile_server(). Old sessions & locations MUST NOT be
        # re-signed with the new pairing_token hash.
        raw_token = "token-before-settings-rotation"
        gw = _PersistentMobileHarness(self.data_dir, pairing_token="old-pairing-token-123456789012")
        gw._schedule_v2_location_notification = Mock()
        gw.pair(raw_token)
        body, status = await gw.call(raw_token, gw.mobile_location, {
            "latitude": 30.25, "longitude": 120.15,
            "place": {"matched": True, "name": "家", "kind": "home"},
        })
        self.assertEqual((status, body["ok"]), (200, True))
        state_file = self.data_dir / MOBILE_STATE_FILENAME
        self.assertTrue(state_file.is_file())

        # Mutate pairing_token in-place before stopping (exact settings_methods.py sequence).
        gw._cfg["mobile.pairing_token"] = "rotated-pairing-token-999999999999"
        # Even before stop, live _mobile_authorize rejects session bound to old pairing_token.
        _body, pre_stop_status = await gw.call(raw_token, gw.mobile_device_status, {"app_state": "foreground"})
        self.assertEqual(pre_stop_status, 401)

        # Re-pair simulation on another instance to ensure _stop_mobile_server() itself never re-signs old memory.
        gw_rot = _PersistentMobileHarness(self.data_dir, pairing_token="old-pairing-token-123456789012")
        gw_rot._schedule_v2_location_notification = Mock()
        gw_rot.pair(raw_token)
        await gw_rot.call(raw_token, gw_rot.mobile_location, {"latitude": 30.25, "longitude": 120.15})
        gw_rot._cfg["mobile.pairing_token"] = "rotated-pairing-token-999999999999"
        await gw_rot._stop_mobile_server()
        self.assertFalse(state_file.exists())

        # Restart with rotated pairing_token restores 0 sessions & 0 locations; old token stays 401.
        gw_restarted = _PersistentMobileHarness(self.data_dir, pairing_token="rotated-pairing-token-999999999999")
        self.assertEqual(gw_restarted._mobile_load_persisted_state(), (0, 0))
        _body, post_status = await gw_restarted.call(raw_token, gw_restarted.mobile_device_status, {"app_state": "foreground"})
        self.assertEqual(post_status, 401)

        # Clearing pairing_token to empty also unlinks any persisted state file.
        gw_clear = _PersistentMobileHarness(self.data_dir, pairing_token="temp-pairing-token-123456789012")
        gw_clear.pair("token-to-clear")
        self.assertTrue(state_file.is_file())
        gw_clear._cfg["mobile.pairing_token"] = ""
        gw_clear._mobile_save_persisted_state()
        self.assertFalse(state_file.exists())

    async def test_revoke_location_and_close_session_scrub_persisted_state_immediately(self):
        raw_token = "token-to-close"
        gw1 = _PersistentMobileHarness(self.data_dir)
        gw1._schedule_v2_location_notification = Mock()
        gw1.pair(raw_token)
        await gw1.call(raw_token, gw1.mobile_location, {"latitude": 30.25, "longitude": 120.15})

        # Revoke location -> scrubbed on disk so restart does not resurrect it
        rev_body, rev_status = await gw1.call(raw_token, gw1.mobile_revoke_location)
        self.assertEqual((rev_status, rev_body["ok"]), (200, True))
        gw_mid = _PersistentMobileHarness(self.data_dir)
        self.assertEqual(gw_mid._mobile_load_persisted_state(), (1, 0))

        # Close session -> scrubbed on disk so restart rejects old token
        close_body, close_status = await gw1.call(raw_token, gw1.mobile_close_session)
        self.assertEqual((close_status, close_body["ok"]), (200, True))
        gw_after = _PersistentMobileHarness(self.data_dir)
        self.assertEqual(gw_after._mobile_load_persisted_state(), (0, 0))
        _body, status = await gw_after.call(raw_token, gw_after.mobile_device_status, {"app_state": "foreground"})
        self.assertEqual(status, 401)

    async def test_missing_location_heartbeat_returns_structured_409_and_logs_info_not_warning(self):
        raw_token = "token-hb-miss"
        gw = _PersistentMobileHarness(self.data_dir)
        gw.pair(raw_token)
        request = NS(
            can_read_body=False,
            content_length=0,
            transport=None,
            remote="127.0.0.1",
            headers={"Authorization": f"Bearer {raw_token}"},
            json=AsyncMock(return_value={}),
        )
        with patch.object(mobile_gateway, "logger") as mock_logger:
            response = await gw._mobile_aiohttp_dispatch(request, gw.mobile_location_heartbeat)
            self.assertEqual(response.status, 409)
            body = json.loads(response.text)
            self.assertEqual(body["code"], "mobile_location_reacquire_required")
            self.assertTrue(body["reacquire_location"])
            mock_logger.info.assert_called_once()
            mock_logger.warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
