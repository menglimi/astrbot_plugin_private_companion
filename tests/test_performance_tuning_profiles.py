"""Unit and integration tests for 5-tier performance tuning, cgroup detection, Anti-OOM clamps, hot refresh, and Web API."""

from __future__ import annotations

import asyncio
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from tests import astrbot_data_root  # noqa: F401 - Isolate data root before AstrBot SDK imports.
from astrbot.api.web import PluginRequest, bind_request_context
from starlette.requests import Request

from astrbot_host import core_reply_voice
from astrbot_host.multi_api import MultiHostWebAPI
from astrbot_host.native_extensions.image_native.store import ImageStore
from astrbot_host.settings import HostSettings
from astrbot_host.tuning_api import (
    PREFIX as TUNING_PREFIX,
    TuningWebAPI,
    apply_host_performance_tuning,
    release_host_performance_tuning,
)
from astrbot_host.web_api import StagingWebAPI
from companion_v2.execution.models import Lane, SupervisorLimits, TaskDefinition, TaskRegistration
from companion_v2.execution.service import TaskSupervisor
from companion_v2.memory.search import SearchIndex
from companion_v2.storage import sqlite_tuning
from companion_v2.storage.runtime import StorageRuntime
from companion_v2.storage.semantic import SemanticStore
from companion_v2.storage.telemetry_archive import TelemetryArchiveStore
from companion_v2.storage.tuning_config import (
    CONCRETE_PROFILE_IDS,
    PROFILE_IDS,
    TUNING_FIELDS,
    active_performance_plan,
    active_telemetry_ttl_days,
    active_tts_max_concurrency,
    active_write_batch_window_ms,
    clear_active_performance_plan,
    compact_sparse_overrides,
    detect_hardware_profile,
    normalize_tuning_config,
    resolve_performance_plan,
    set_active_performance_plan,
)

GIB = 1024 ** 3
MIB = 1024 ** 2


class PerformanceTuningConfigTests(unittest.TestCase):
    def setUp(self):
        clear_active_performance_plan()
        self.addCleanup(clear_active_performance_plan)

    def test_all_five_profiles_resolve_cleanly(self):
        self.assertEqual(PROFILE_IDS, ("auto", "eco", "balanced", "turbo", "compat"))
        self.assertEqual(len(TUNING_FIELDS), 11)

        eco = resolve_performance_plan({"profile": "eco"}, total_memory_bytes=4 * GIB, cpu_count=4, is_32bit=False)
        self.assertEqual(eco.resolved_profile, "eco")
        self.assertEqual(eco.values["cache_size_mb"], 8)
        self.assertEqual(eco.values["mmap_size_mb"], 0)
        self.assertEqual(eco.values["reader_pool_size"], 2)
        self.assertEqual(eco.values["background_slots"], 2)
        self.assertEqual(eco.values["tts_max_concurrency"], 1)
        self.assertEqual(eco.values["telemetry_ttl_days"], 7)

        balanced = resolve_performance_plan({"profile": "balanced"}, total_memory_bytes=8 * GIB, cpu_count=8, is_32bit=False)
        self.assertEqual(balanced.values["cache_size_mb"], 32)
        self.assertEqual(balanced.values["mmap_size_mb"], 128)
        self.assertEqual(balanced.values["reader_pool_size"], 4)
        self.assertEqual(balanced.values["synchronous"], "NORMAL")

        turbo = resolve_performance_plan({"profile": "turbo"}, total_memory_bytes=32 * GIB, cpu_count=16, is_32bit=False)
        self.assertEqual(turbo.values["cache_size_mb"], 128)
        self.assertEqual(turbo.values["mmap_size_mb"], 1024)
        self.assertEqual(turbo.values["reader_pool_size"], 8)
        self.assertEqual(turbo.values["write_batch_window_ms"], 30)
        self.assertEqual(turbo.values["background_slots"], 5)
        self.assertEqual(turbo.values["tts_max_concurrency"], 4)
        self.assertEqual(turbo.values["telemetry_ttl_days"], 30)

        compat = resolve_performance_plan({"profile": "compat"}, total_memory_bytes=16 * GIB, cpu_count=8, is_32bit=False)
        self.assertEqual(compat.values["mmap_size_mb"], 0)
        self.assertEqual(compat.values["sidecar_mmap_size_mb"], 0)
        self.assertEqual(compat.values["synchronous"], "FULL")
        self.assertEqual(compat.values["reader_pool_size"], 2)
        self.assertEqual(compat.values["write_batch_window_ms"], 100)
        self.assertEqual(compat.sqlite_profile.effective_synchronous(), "FULL")

    def test_cgroup_v2_and_v1_memory_limit_detection(self):
        with TemporaryDirectory(prefix="cgroup-test-") as tmp:
            root = Path(tmp)
            v2 = root / "memory.max"
            v1 = root / "memory.limit_in_bytes"

            v2.write_text(str(2 * GIB), encoding="ascii")
            self.assertEqual(sqlite_tuning.detect_cgroup_memory_limit_bytes(v2_path=v2, v1_path=v1), 2 * GIB)

            # "max" in cgroup v2 falls back to cgroup v1.
            v2.write_text("max\n", encoding="ascii")
            v1.write_text(str(3 * GIB), encoding="ascii")
            self.assertEqual(sqlite_tuning.detect_cgroup_memory_limit_bytes(v2_path=v2, v1_path=v1), 3 * GIB)

            # Unlimited sentinel in cgroup v1 is ignored.
            v1.write_text("9223372036854771712\n", encoding="ascii")
            self.assertIsNone(sqlite_tuning.detect_cgroup_memory_limit_bytes(v2_path=v2, v1_path=v1))

        # Container with 1.5 GiB cgroup limit on a 64 GiB host recommends eco.
        hw = detect_hardware_profile(
            host_memory_bytes=64 * GIB,
            cgroup_limit_bytes=int(1.5 * GIB),
            cpu_count=8,
            is_32bit=False,
        )
        self.assertTrue(hw.cgroup_limited)
        self.assertEqual(hw.effective_memory_bytes, int(1.5 * GIB))
        self.assertEqual(hw.recommended_profile, "eco")
        self.assertIn("容器内存上限", hw.recommendation_reason)

    def test_anti_oom_clamps_and_32bit_mmap_guard(self):
        # 1 GiB host trying to use turbo (128 MB cache, 512 MB mmap) must be safely clamped.
        plan = resolve_performance_plan(
            {"profile": "turbo", "overrides": {"cache_size_mb": 512, "mmap_size_mb": 2048}},
            total_memory_bytes=1 * GIB,
            cpu_count=2,
            is_32bit=False,
        )
        self.assertLessEqual(plan.values["cache_size_mb"], 102)
        self.assertLessEqual(plan.values["mmap_size_mb"], 256)
        clamped_keys = {c.field for c in plan.clamped_fields}
        self.assertIn("cache_size_mb", clamped_keys)
        self.assertIn("mmap_size_mb", clamped_keys)

        # 32-bit Python forces mmap and sidecar_mmap to 0.
        plan32 = resolve_performance_plan(
            {"profile": "balanced"},
            total_memory_bytes=8 * GIB,
            cpu_count=4,
            is_32bit=True,
        )
        self.assertEqual(plan32.values["mmap_size_mb"], 0)
        self.assertEqual(plan32.values["sidecar_mmap_size_mb"], 0)
        self.assertIn("mmap_size_mb", {c.field for c in plan32.clamped_fields})

    def test_sparse_override_normalization_and_compaction(self):
        profile, overrides = normalize_tuning_config(
            {"profile": "balanced", "overrides": {"cache_size_mb": 64, "mmap_size_mb": None}},
            strict=True,
        )
        self.assertEqual(profile, "balanced")
        self.assertEqual(overrides, {"cache_size_mb": 64})

        # Overriding back to baseline gets stripped by compact_sparse_overrides.
        hw = detect_hardware_profile(total_memory_bytes=8 * GIB, cpu_count=4, is_32bit=False)
        compacted = compact_sparse_overrides("balanced", {"cache_size_mb": 32, "reader_pool_size": 6}, hardware=hw)
        self.assertEqual(compacted, {"reader_pool_size": 6})

        with self.assertRaises(ValueError):
            normalize_tuning_config({"profile": "unknown"}, strict=True)
        with self.assertRaises(ValueError):
            normalize_tuning_config({"profile": "balanced", "overrides": {"cache_size_mb": 99999}}, strict=True)
        with self.assertRaises(ValueError):
            normalize_tuning_config({"profile": "balanced", "overrides": {"synchronous": "OFF"}}, strict=True)

    def test_settings_parse_honors_performance_background_slots_including_schema_defaults(self):
        # Real AstrBot populates runtime.background_slots=3 from _conf_schema.json.
        schema_populated = {
            "staging": {"profile_id": "main"},
            "model": {"provider_id": "p1"},
            "runtime": {"background_slots": 3},
            "performance": {"profile": "turbo", "overrides": {}},
        }
        self.assertEqual(HostSettings.parse(schema_populated).limits.background_slots, 5)

        eco_cfg = deepcopy(schema_populated)
        eco_cfg["performance"] = {"profile": "eco", "overrides": {}}
        self.assertEqual(HostSettings.parse(eco_cfg).limits.background_slots, 2)

        # Untouched default auto profile preserves explicit runtime.background_slots.
        auto_custom = {
            "staging": {"profile_id": "main"},
            "model": {"provider_id": "p1"},
            "runtime": {"background_slots": 7},
            "performance": {"profile": "auto", "overrides": {}},
        }
        self.assertEqual(HostSettings.parse(auto_custom).limits.background_slots, 7)

        # Explicit performance override under auto takes effect.
        auto_override = deepcopy(auto_custom)
        auto_override["performance"]["overrides"] = {"background_slots": 4}
        self.assertEqual(HostSettings.parse(auto_override).limits.background_slots, 4)

    def test_payload_exposes_all_five_baselines_including_exact_auto_tier(self):
        plan = resolve_performance_plan(
            {"profile": "compat"},
            total_memory_bytes=8 * GIB,
            cpu_count=8,
            is_32bit=False,
        )
        payload = plan.to_payload()
        self.assertEqual(set(payload["baselines"].keys()), set(PROFILE_IDS))
        # On an 8 GiB host, auto resolves to the high-tier SQLite baseline (64 MB cache / 256 MB mmap),
        # distinct from balanced (32 MB / 128 MB).
        self.assertEqual(payload["baselines"]["auto"]["cache_size_mb"], 64)
        self.assertEqual(payload["baselines"]["auto"]["mmap_size_mb"], 256)
        self.assertEqual(payload["baselines"]["balanced"]["cache_size_mb"], 32)
        self.assertEqual(payload["baselines"]["balanced"]["mmap_size_mb"], 128)
        self.assertEqual(payload["baselines"]["compat"]["synchronous"], "FULL")

    def test_auto_profile_publishes_active_plan_for_low_high_and_32bit_hosts(self):
        # 1.5 GiB low-RAM host (auto -> eco): runtime helpers must match UI values (1 / 7 / 80), not fallback defaults.
        low_ctrl = NS(config={"performance": {"profile": "auto", "overrides": {}}}, runtime=None, session=None, last_session=None)
        low_plan, _ = apply_host_performance_tuning(low_ctrl, total_memory_bytes=int(1.5 * GIB), cpu_count=2, is_32bit=False)
        self.assertEqual(low_plan.resolved_profile, "eco")
        self.assertIs(active_performance_plan(), low_plan)
        self.assertEqual(active_tts_max_concurrency(), 1)
        self.assertEqual(active_telemetry_ttl_days(), 7)
        self.assertEqual(active_write_batch_window_ms(), 80)
        self.assertEqual(core_reply_voice._max_concurrent_tts(None), 1)
        self.assertEqual(sqlite_tuning.active_sqlite_tuning_profile().mmap_size, 0)

        # 32 GiB high-RAM host (auto -> turbo): runtime helpers must match UI values (4 / 30 / 30).
        high_ctrl = NS(config={"performance": {"profile": "auto"}}, runtime=None, session=None, last_session=None)
        high_plan, _ = apply_host_performance_tuning(high_ctrl, total_memory_bytes=32 * GIB, cpu_count=16, is_32bit=False)
        self.assertEqual(high_plan.resolved_profile, "turbo")
        self.assertIs(active_performance_plan(), high_plan)
        self.assertEqual(active_tts_max_concurrency(), 4)
        self.assertEqual(active_telemetry_ttl_days(), 30)
        self.assertEqual(active_write_batch_window_ms(), 30)

        # 32-bit host (auto): clamped sidecar mmap=0 must take effect on sidecars.
        bit32_ctrl = NS(config={"performance": {"profile": "auto"}}, runtime=None, session=None, last_session=None)
        apply_host_performance_tuning(bit32_ctrl, total_memory_bytes=8 * GIB, cpu_count=4, is_32bit=True)
        with TemporaryDirectory(prefix="auto-32bit-sidecar-") as tmp:
            idx = SearchIndex(Path(tmp))
            with closing(idx._connect()) as conn:
                self.assertEqual(conn.execute("PRAGMA mmap_size").fetchone()[0], 0)

        # Releasing the owning controller clears the active plan cleanly; non-owning controllers do not interfere.
        release_host_performance_tuning(low_ctrl)
        self.assertIsNotNone(active_performance_plan())
        release_host_performance_tuning(bit32_ctrl)
        self.assertIsNone(active_performance_plan())

    def test_conf_schema_declares_performance_overrides_as_dict_and_loads_in_astrbot_config(self):
        from astrbot.core.config.astrbot_config import AstrBotConfig

        schema_path = Path(__file__).resolve().parent.parent / "_conf_schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        perf_items = schema["performance"]["items"]
        self.assertEqual(perf_items["overrides"]["type"], "dict")
        self.assertEqual(perf_items["overrides"]["default"], {})
        self.assertTrue(perf_items["overrides"]["invisible"])

        # Every "object" node in _conf_schema.json must carry an "items" dict for AstrBotConfig._parse_schema.
        missing_items: list[str] = []

        def walk(node: dict, prefix: str = "") -> None:
            for key, value in node.items():
                if not isinstance(value, dict):
                    continue
                dotted = f"{prefix}.{key}" if prefix else key
                if value.get("type") == "object":
                    if not isinstance(value.get("items"), dict):
                        missing_items.append(dotted)
                    else:
                        walk(value["items"], dotted)

        walk(schema)
        self.assertEqual(missing_items, [])

        with TemporaryDirectory(prefix="astrbot-schema-") as tmp:
            cfg_path = str(Path(tmp) / "plugin_config.json")
            cfg = AstrBotConfig(config_path=cfg_path, schema=schema)
            self.assertEqual(cfg["performance"]["profile"], "auto")
            self.assertEqual(cfg["performance"]["overrides"], {})
            cfg["performance"]["overrides"] = {"cache_size_mb": 64, "synchronous": "FULL"}
            cfg.save_config()

            reloaded = AstrBotConfig(config_path=cfg_path, schema=schema)
            self.assertEqual(
                reloaded["performance"]["overrides"],
                {"cache_size_mb": 64, "synchronous": "FULL"},
            )


class HotRefreshRuntimeAndSidecarsTests(unittest.TestCase):
    def setUp(self):
        clear_active_performance_plan()
        self.addCleanup(clear_active_performance_plan)

    def test_runtime_hot_refresh_updates_writer_retires_readers_and_batcher_window(self):
        with TemporaryDirectory(prefix="tuning-hot-") as tmp, StorageRuntime.open(
            data_root=Path(tmp), installation_id="hot-inst"
        ) as runtime:

            # Warm an active reader inside one thread while hot-refreshing from another.
            entered = threading.Event()
            release = threading.Event()
            reader_errors: list[BaseException] = []

            def hold_reader():
                try:
                    with runtime.read_snapshot() as conn:
                        entered.set()
                        release.wait(timeout=5.0)
                        conn.execute("SELECT 1").fetchone()
                except BaseException as exc:
                    reader_errors.append(exc)

            thread = threading.Thread(target=hold_reader)
            thread.start()
            self.assertTrue(entered.wait(timeout=5.0))

            compat_plan = resolve_performance_plan(
                {"profile": "compat", "overrides": {"write_batch_window_ms": 0}},
                total_memory_bytes=8 * GIB,
                cpu_count=4,
                is_32bit=False,
            )
            info = runtime.apply_tuning_profile(
                compat_plan.sqlite_profile,
                write_batch_window_ms=0,
            )
            self.assertEqual(info["synchronous"], "FULL")
            self.assertEqual(info["reader_pool_size"], 2)
            self.assertEqual(runtime.batched_write_stats()["window_ms"], 0)

            # Let the old active reader finish cleanly and retire.
            release.set()
            thread.join(timeout=5.0)
            self.assertEqual(reader_errors, [])

            # Writer connection reflects new PRAGMAs immediately.
            with runtime.transaction() as writer_conn:
                self.assertEqual(writer_conn.execute("PRAGMA cache_size").fetchone()[0], -16384)
                self.assertEqual(writer_conn.execute("PRAGMA mmap_size").fetchone()[0], 0)
                self.assertEqual(writer_conn.execute("PRAGMA synchronous").fetchone()[0], 2)  # FULL=2

            # Newly created reader reflects new PRAGMAs immediately.
            with runtime.read_snapshot() as reader_conn:
                self.assertEqual(reader_conn.execute("PRAGMA cache_size").fetchone()[0], -16384)
                self.assertEqual(reader_conn.execute("PRAGMA mmap_size").fetchone()[0], 0)
                self.assertEqual(reader_conn.execute("PRAGMA synchronous").fetchone()[0], 2)

            # Enqueue batched write at 0 ms window and verify prompt flush.
            committed = []
            runtime.enqueue_batched_write(lambda conn: committed.append(conn.execute("SELECT 1").fetchone()[0]))
            runtime.flush_batched_writes(timeout=3.0)
            self.assertEqual(committed, [1])

    def test_sidecars_and_voice_concurrency_follow_active_plan(self):
        compat = resolve_performance_plan(
            {"profile": "compat", "overrides": {"tts_max_concurrency": 5}},
            total_memory_bytes=8 * GIB,
            cpu_count=4,
            is_32bit=False,
        )
        set_active_performance_plan(compat)
        self.assertEqual(core_reply_voice._max_concurrent_tts(None), 5)
        self.assertEqual(core_reply_voice._max_concurrent_tts(NS(max_concurrent_tts=2)), 2)

        with TemporaryDirectory(prefix="sidecar-hot-") as tmp:
            root = Path(tmp)
            search_index = SearchIndex(root)
            with closing(search_index._connect()) as conn:
                self.assertEqual(conn.execute("PRAGMA synchronous").fetchone()[0], 2)
                self.assertEqual(conn.execute("PRAGMA mmap_size").fetchone()[0], 0)
                self.assertEqual(conn.execute("PRAGMA cache_size").fetchone()[0], -8192)

            sem = SemanticStore(root)
            with sem.db() as conn:
                self.assertEqual(conn.execute("PRAGMA synchronous").fetchone()[0], 2)

            arch = TelemetryArchiveStore(root)
            arch.ensure_initialized()
            with arch.db(read_only=True) as conn:
                self.assertEqual(conn.execute("PRAGMA synchronous").fetchone()[0], 2)

            img_store = ImageStore(root / "img", None, identity_key="id-1")
            with img_store.read_db() as conn:
                self.assertEqual(conn.execute("PRAGMA synchronous").fetchone()[0], 2)

    def test_supervisor_update_background_slots_is_atomic_and_restart_safe(self):
        with TemporaryDirectory(prefix="sup-slots-") as tmp, StorageRuntime.open(
            data_root=Path(tmp), installation_id="sup-inst"
        ) as runtime:
            now = lambda: datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)
            reg = TaskRegistration(
                TaskDefinition("test.bg", "test", "test.bg", Lane.BACKGROUND, timeout_seconds=30, max_attempts=1, concurrency=1),
                lambda payload: payload,
            )
            sup = TaskSupervisor(runtime, (reg,), limits=SupervisorLimits(2, 5, 16, 100), now=now)
            self.assertEqual(sup.start(expected_generation=0).unwrap(), 1)

            # Hot-decrease background_slots from 5 -> 2 and verify generation check remains valid.
            updated = sup.update_background_slots(2)
            self.assertEqual(updated.background_slots, 2)
            self.assertEqual(sup.check_generation().unwrap(), 1)

            # A restarted supervisor with another background_slots decrease (2 -> 1) starts cleanly.
            next_sup = TaskSupervisor(runtime, (reg,), limits=SupervisorLimits(2, 1, 16, 100), now=now)
            self.assertEqual(next_sup.start(expected_generation=1).unwrap(), 2)


class TuningWebAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        clear_active_performance_plan()
        self.addCleanup(clear_active_performance_plan)
        self.tmp = TemporaryDirectory(prefix="tuning-api-")
        self.addCleanup(self.tmp.cleanup)
        self.runtime = StorageRuntime.open(data_root=Path(self.tmp.name), installation_id="tuning-api-inst")
        self.addCleanup(self.runtime.close)
        self.config = {"performance": {"enabled": False}}

        def save_config(candidate):
            self.config.clear()
            self.config.update(deepcopy(candidate))

        self.controller = NS(
            config=self.config,
            runtime=self.runtime,
            session=None,
            last_session=None,
            _control_lock=asyncio.Lock(),
            _terminated=False,
            _performance_mode_enabled=False,
            save_plugin_config=save_config,
        )
        self.api = TuningWebAPI(self.controller)

    async def _get(self, *, username="admin", query=""):
        req = Request({
            "type": "http", "method": "GET", "path": TUNING_PREFIX,
            "headers": [], "query_string": query.encode(),
        })
        with bind_request_context(PluginRequest(req, username=username)):
            return await self.api.status()

    async def _post(self, body: dict, *, username="admin", query=""):
        raw = json.dumps(body).encode("utf-8")
        req = NS(
            username=username,
            query=dict(item.split("=", 1) for item in query.split("&") if "=" in item) if query else {},
            body=AsyncMock(return_value=raw),
        )
        with patch("astrbot_host.tuning_api.request", req):
            return await self.api.update()

    async def test_auth_and_validation_enforced(self):
        denied = await self._get(username=None)
        self.assertEqual(denied["status"], "error")
        self.assertEqual(denied["data"]["code"], "dashboard_identity_required")

        bad_q = await self._get(query="foo=1")
        self.assertEqual(bad_q["data"]["code"], "tuning_request_invalid")

        bad_prof = await self._post({"profile": "hyperdrive"})
        self.assertEqual(bad_prof["data"]["code"], "tuning_request_invalid")

        bad_override = await self._post({"profile": "balanced", "overrides": {"cache_size_mb": 1}})
        self.assertEqual(bad_override["data"]["code"], "tuning_request_invalid")

    async def test_save_reset_and_lite_page_roundtrip(self):
        initial = await self._get()
        self.assertEqual(initial["status"], "ok")
        self.assertEqual(initial["data"]["profile"], "auto")
        self.assertEqual(len(initial["data"]["profiles"]), 5)
        self.assertEqual(len(initial["data"]["fields"]), 11)

        # Save compat with sparse override + redundant baseline value stripped automatically.
        saved = await self._post({
            "action": "save",
            "profile": "compat",
            "overrides": {"reader_pool_size": 3, "mmap_size_mb": 0},
        })
        self.assertEqual(saved["status"], "ok", saved)
        self.assertEqual(saved["data"]["profile"], "compat")
        self.assertEqual(saved["data"]["overrides"], {"reader_pool_size": 3})
        self.assertEqual(saved["data"]["override_count"], 1)
        self.assertTrue(saved["data"]["applied"]["sqlite_applied"])
        self.assertEqual(self.runtime.reader_pool_size, 3)
        self.assertEqual(self.runtime.tuning_profile.effective_synchronous(), "FULL")
        self.assertEqual(self.config["performance"]["profile"], "compat")
        self.assertEqual(self.config["performance"]["overrides"], {"reader_pool_size": 3})

        # Reset overrides keeps profile=compat and clears overrides.
        reset = await self._post({"action": "reset", "profile": "compat"})
        self.assertEqual(reset["status"], "ok")
        self.assertEqual(reset["data"]["overrides"], {})
        self.assertEqual(self.runtime.reader_pool_size, 2)

        # Toggle lite_page preserves profile & overrides and flags reload_required.
        lite = await self._post({"action": "lite_page", "lite_page_enabled": True})
        self.assertEqual(lite["status"], "ok")
        self.assertTrue(self.config["performance"]["enabled"])
        self.assertEqual(self.config["performance"]["profile"], "compat")
        self.assertTrue(lite["data"]["lite_page"]["saved_enabled"])
        self.assertTrue(lite["data"]["lite_page"]["reload_required"])

    async def test_routes_registered_in_both_multi_and_staging_web_apis(self):
        class FakeCtx:
            def __init__(self):
                self.registered_web_apis = []

            def register_web_api(self, route, handler, methods, description):
                self.registered_web_apis.append((route, handler, methods, description))

        multi_ctx = FakeCtx()
        multi_ctrl = NS(
            config={"performance": {"enabled": False}},
            context=multi_ctx,
            data_root=Path(self.tmp.name),
        )
        multi_api = MultiHostWebAPI(multi_ctrl)
        multi_api.target_inventory = lambda refresh_routes=False: []
        multi_api.register()
        self.assertIn(TUNING_PREFIX, [row[0] for row in multi_ctx.registered_web_apis])

        # Lite mode must not register TuningWebAPI.
        lite_ctx = FakeCtx()
        lite_ctrl = NS(
            config={"performance": {"enabled": True}},
            context=lite_ctx,
            data_root=Path(self.tmp.name),
        )
        MultiHostWebAPI(lite_ctrl).register()
        self.assertNotIn(TUNING_PREFIX, [row[0] for row in lite_ctx.registered_web_apis])

        # StagingWebAPI also registers TuningWebAPI.
        staging_ctx = FakeCtx()
        staging_ctrl = NS(
            config={},
            context=staging_ctx,
            data_root=Path(self.tmp.name),
        )
        with patch("astrbot_host.optional_persona_designer.register_optional_designer"):
            StagingWebAPI(staging_ctrl).register()
        self.assertIn(TUNING_PREFIX, [row[0] for row in staging_ctx.registered_web_apis])


if __name__ == "__main__":
    unittest.main()
