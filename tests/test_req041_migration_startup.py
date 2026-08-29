from __future__ import annotations

import ast
import asyncio
import copy
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import tempfile
import types
import unittest
from typing import Any

from migration_coordinator import MigrationCoordinator
from migration_backfill import MigrationBackfill, legacy_pending_reference
from migration_dual_write import MigrationDualWriteProducer
from migration_outbox import MigrationOutbox
from migration_replay import MigrationReplayWorker
from migration_read_router import MigrationRelationshipReadRouter
from migration_source_inspector import inspect_migration_sources
from migration_scoped_projection import ScopedProjectionSynchronizer
from migration_scoped_projection import scoped_group_ref, scoped_persona_ref
from relationship_account_store import RelationshipAccountStore
from identity_namespace import AssurancePolicy, NamespaceContext
from tests.test_req041_scoped_projection import _Remote
from relationship_ledger import normalize_relationship_positive_stage_cap_key
from unified_person_registry import UnifiedPersonRegistry
from scoped_runtime_view import overlay_group_runtime_view, overlay_private_runtime_view


ROOT = Path(__file__).resolve().parents[1]
V608_FIXTURE = ROOT / "tests" / "fixtures" / "req041" / "companion-v6.0.8-sanitized.json"


def _load_methods(*names: str) -> dict[str, Any]:
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PrivateCompanionPlugin")
    selected = [copy.deepcopy(node) for node in owner.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    for node in selected:
        node.decorator_list = []
    module = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "Any": Any,
        "Path": Path,
        "asyncio": asyncio,
        "deepcopy": deepcopy,
        "hashlib": hashlib,
        "json": json,
        "math": math,
        "MigrationBackfill": MigrationBackfill,
        "legacy_pending_reference": legacy_pending_reference,
        "MigrationDualWriteProducer": MigrationDualWriteProducer,
        "MigrationReplayWorker": MigrationReplayWorker,
        "MigrationRelationshipReadRouter": MigrationRelationshipReadRouter,
        "inspect_migration_sources": inspect_migration_sources,
        "ScopedProjectionSynchronizer": ScopedProjectionSynchronizer,
        "RelationshipAccountStore": RelationshipAccountStore,
        "AssurancePolicy": AssurancePolicy,
        "NamespaceContext": NamespaceContext,
        "scoped_group_ref": scoped_group_ref,
        "scoped_persona_ref": scoped_persona_ref,
        "UnifiedPersonRegistry": UnifiedPersonRegistry,
        "overlay_group_runtime_view": overlay_group_runtime_view,
        "overlay_private_runtime_view": overlay_private_runtime_view,
        "normalize_relationship_positive_stage_cap_key": normalize_relationship_positive_stage_cap_key,
        "_single_line": lambda value, limit=240: " ".join(str(value or "").split())[:limit],
        "_now_ts": lambda: 1_786_291_200.0,
        "runtime_persona_setting": lambda host, key, default=None: getattr(host, key, default),
        "logger": types.SimpleNamespace(warning=lambda *_args, **_kwargs: None),
    }
    exec(compile(module, str(ROOT / "main.py"), "exec"), namespace)
    return {name: namespace[name] for name in names}


METHODS = _load_methods(
    "_req041_migration_source_files",
    "_req041_compatibility_snapshot",
    "_req041_registry_for_person",
    "_req041_legacy_relationship_state",
    "_req041_resolve_legacy_pending_for_person",
    "_req041_schedule_replay",
    "_req041_legacy_snapshots_locked",
    "_req041_sync_scoped_now",
    "_req041_rebind_memory_scope_if_available",
    "_req041_run_memory_scope_rebind",
    "_req041_scoped_context_for_user",
    "_req041_persona_global_context",
    "_req041_scoped_private_read_view",
    "_req041_scoped_group_read_view",
    "_req041_replay_finished",
    "_req041_run_replay_batch",
    "_req041_mark_memory_scope_bound",
    "_req041_initialize_fresh_scoped_runtime",
    "_req041_initialize_automatic_migration",
)


class Harness:
    _req041_migration_source_files = METHODS["_req041_migration_source_files"]
    _req041_compatibility_snapshot = METHODS["_req041_compatibility_snapshot"]
    _req041_registry_for_person = METHODS["_req041_registry_for_person"]
    _req041_legacy_relationship_state = METHODS["_req041_legacy_relationship_state"]
    _req041_resolve_legacy_pending_for_person = METHODS["_req041_resolve_legacy_pending_for_person"]
    _req041_schedule_replay = METHODS["_req041_schedule_replay"]
    _req041_legacy_snapshots_locked = METHODS["_req041_legacy_snapshots_locked"]
    _req041_sync_scoped_now = METHODS["_req041_sync_scoped_now"]
    _req041_rebind_memory_scope_if_available = METHODS["_req041_rebind_memory_scope_if_available"]
    _req041_run_memory_scope_rebind = METHODS["_req041_run_memory_scope_rebind"]
    _req041_scoped_context_for_user = METHODS["_req041_scoped_context_for_user"]
    _req041_persona_global_context = METHODS["_req041_persona_global_context"]
    _req041_scoped_private_read_view = METHODS["_req041_scoped_private_read_view"]
    _req041_scoped_group_read_view = METHODS["_req041_scoped_group_read_view"]
    _req041_replay_finished = METHODS["_req041_replay_finished"]
    _req041_run_replay_batch = METHODS["_req041_run_replay_batch"]
    _req041_mark_memory_scope_bound = METHODS["_req041_mark_memory_scope_bound"]
    _req041_initialize_fresh_scoped_runtime = METHODS["_req041_initialize_fresh_scoped_runtime"]
    _req041_initialize_automatic_migration = METHODS["_req041_initialize_automatic_migration"]


class MigrationStartupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _host(self, *, source: bool = True, bind: bool = True) -> Harness:
        host = Harness()
        host.data_dir = str(self.data_dir)
        host.data_file = str(self.data_dir / "companions.json")
        host.storage_backend = "json"
        host.storage_sqlite_effective_path = str(self.data_dir / "companions.db")
        host._persona_profiles_dir = str(self.data_dir / "persona_profiles")
        if source:
            Path(host.data_file).write_bytes(V608_FIXTURE.read_bytes())
        host._data_lock = asyncio.Lock()
        host.data = {"users": {}}
        host._data_default = host.data
        host._persona_data_profiles = {}
        host._active_unified_person_registry = lambda: UnifiedPersonRegistry(host.data)
        host._active_persona_scope = lambda: ""
        host.plugin_identity = {"version": "6.1.1"}
        host.req041_migration_coordinator = MigrationCoordinator(self.data_dir)
        host.req041_migration_outbox = MigrationOutbox(self.data_dir / "req041_migration_outbox.db")
        host.req041_migration_status = {}
        host.enable_auto_user_profile_creation = True
        host.default_enable_configured_targets = False
        host.enable_proactive_only_mode = False
        host.proactive_intensity_preset = "off"
        host.enable_photo_text_action = True
        host.enable_screen_glance_action = False
        host.enable_poke_action = False
        host.enable_voice_action = True
        host.enable_relationship_content_tiers = True
        host.target_user_id = "redacted-by-boolean"
        host.enable_custom_relationship_stage_policy = True
        host.relationship_positive_stage_cap_key = "close"
        host._memory_companion_presence = lambda: {"version": "1.7.2"}
        host._memory_companion_bridge = lambda: object() if bind else None
        host.bind_calls = []

        def binder(_bridge, **kwargs):
            host.bind_calls.append(kwargs)
            return {"ok": True, "state": "ready", "code": "bound"}

        host._memory_companion_bind_namespace_epoch = binder
        host._memory_companion_read_scoped_record = lambda _bridge, _namespace, **_kwargs: {
            "ok": True, "code": "not_found", "record": None
        }
        host._memory_companion_list_scoped_records = lambda _bridge, _namespace, **_kwargs: {
            "ok": True, "code": "listed", "records": []
        }
        host._memory_companion_upsert_scoped_record = lambda _bridge, _namespace, **_kwargs: {
            "ok": True, "code": "created"
        }
        host._memory_companion_tombstone_scoped_record = lambda _bridge, _namespace, **_kwargs: {
            "ok": True, "code": "tombstoned"
        }
        return host

    def _scoped_remote(self, host: Harness) -> _Remote:
        remote = _Remote()
        status = host.req041_migration_coordinator.status()
        host.req041_scoped_projection_sync = ScopedProjectionSynchronizer(
            read=remote.read, list_records=remote.list_records,
            upsert=remote.upsert, tombstone=remote.tombstone,
            migration_epoch=status["migration_epoch"], policy_version=status["policy_version"],
        )
        return remote

    async def test_new_install_without_source_initializes_stable_scoped_runtime(self) -> None:
        host = self._host(source=False)
        await host._req041_initialize_automatic_migration()
        first = host.req041_migration_coordinator.status()
        self.assertEqual("active", host.req041_migration_status["state"])
        self.assertFalse(host.req041_migration_status["required"])
        self.assertTrue(host.req041_migration_status["scoped_required"])
        self.assertEqual("S9", first["phase"])
        self.assertEqual("req041-fresh-v1", first["source_schema_version"])
        self.assertEqual("fresh_scoped_runtime_active", host.req041_migration_status["code"])
        self.assertFalse((self.data_dir / "req041_backups").exists())
        self.assertIsInstance(host.req041_relationship_store, RelationshipAccountStore)
        self.assertIsInstance(host.req041_dual_write_producer, MigrationDualWriteProducer)
        self.assertIsInstance(host.req041_migration_replay, MigrationReplayWorker)
        self.assertIsInstance(host.req041_relationship_read_router, MigrationRelationshipReadRouter)
        self.assertIsInstance(host.req041_scoped_projection_sync, ScopedProjectionSynchronizer)

        Path(host.data_file).write_text('{"users":{}}', encoding="utf-8")
        restarted = self._host(source=True)
        await restarted._req041_initialize_automatic_migration()
        second = restarted.req041_migration_coordinator.status()
        self.assertEqual(first["migration_epoch"], second["migration_epoch"])
        self.assertEqual("S9", second["phase"])
        self.assertFalse((self.data_dir / "req041_backups").exists())

    async def test_new_install_without_memory_is_explicitly_scoped_degraded(self) -> None:
        host = self._host(source=False, bind=False)
        await host._req041_initialize_automatic_migration()
        self.assertEqual("degraded", host.req041_migration_status["state"])
        self.assertEqual("memory_bridge_unavailable", host.req041_migration_status["code"])
        self.assertTrue(host.req041_migration_status["scoped_required"])
        self.assertIsNone(getattr(host, "req041_scoped_projection_sync", None))
        self.assertFalse((host.data.get("_req041_memory_scope_state") or {}).get("ever_bound", False))
        self.assertFalse((self.data_dir / "req041_backups").exists())

    async def test_late_memory_startup_rebinds_scoped_runtime(self) -> None:
        host = self._host(source=False, bind=False)
        host._memory_companion_bridge_enabled = lambda: True
        await host._req041_initialize_automatic_migration()
        self.assertEqual("degraded", host.req041_migration_status["state"])
        self.assertIsNone(getattr(host, "req041_scoped_projection_sync", None))

        host._memory_companion_bridge = lambda: object()
        result = await host._req041_rebind_memory_scope_if_available()

        self.assertTrue(result["ok"])
        self.assertIsInstance(host.req041_scoped_projection_sync, ScopedProjectionSynchronizer)
        self.assertTrue(host.req041_migration_status["memory_bound"])
        self.assertTrue((host.data.get("_req041_memory_scope_state") or {}).get("ever_bound"))

    async def test_memory_bridge_replacement_rebinds_existing_scoped_runtime(self) -> None:
        host = self._host(source=False)
        bridges = {"current": object()}
        host._memory_companion_bridge = lambda: bridges["current"]
        await host._req041_initialize_automatic_migration()
        original_sync = host.req041_scoped_projection_sync
        original_bridge = host._req041_scoped_bridge

        bridges["current"] = object()
        result = await host._req041_rebind_memory_scope_if_available()

        self.assertTrue(result["ok"])
        self.assertIsNot(original_sync, host.req041_scoped_projection_sync)
        self.assertIsNot(original_bridge, host._req041_scoped_bridge)
        self.assertEqual(2, len(host.bind_calls))

    async def test_new_install_first_exact_identity_reaches_new_read_through_outbox(self) -> None:
        host = self._host(source=False)
        await host._req041_initialize_automatic_migration()
        registry = UnifiedPersonRegistry(host.data)
        created = registry.create_or_link(
            {
                "companion_instance_id": "astrbot_plugin_private_companion",
                "bot_account_id": "onebot:bot-1",
                "adapter_instance_id": "onebot:default",
                "subject_namespace": "onebot:user",
                "platform_subject_id": "10001",
            },
            profile={"display_name": "Fresh User"},
            operation_id="fresh-first-exact-event",
        )
        host.data["users"]["10001"] = {
            "user_id": "10001",
            "identity_subject_id": "10001",
            "unified_person_id": created["person_id"],
            "relationship_role": "friend",
            "relationship_mode": "normal",
            "relationship_score": 0,
        }
        emitted = host.req041_dual_write_producer.emit_identity_change(
            registry=registry,
            result=created,
            action="create",
            operation_id="fresh-first-exact-event",
        )
        self.assertEqual("enqueued", emitted["status"])
        await asyncio.wait_for(host._req041_replay_task, timeout=2.0)
        identity_status = host.req041_migration_coordinator.identity_status(created["person_id"])
        self.assertEqual("new", identity_status["read_generation"])
        self.assertEqual("new_read", identity_status["state"])
        self.assertEqual([], host.req041_migration_outbox.pending(
            host.req041_migration_coordinator.status()["migration_epoch"]
        ))

    async def test_existing_install_auto_backs_up_captures_policy_starts_outbox_and_binds_memory(self) -> None:
        host = self._host()
        await host._req041_initialize_automatic_migration()
        status = host.req041_migration_coordinator.status()
        self.assertEqual("S6", status["phase"])
        self.assertTrue(host.req041_migration_coordinator.verify_backup())
        self.assertEqual("active", host.req041_migration_status["state"])
        self.assertTrue(host.req041_migration_status["memory_bound"])
        self.assertTrue(host.data["_req041_memory_scope_state"]["ever_bound"])
        self.assertEqual(1, len(host.bind_calls))
        self.assertEqual(status["migration_epoch"], host.bind_calls[0]["migration_epoch"])
        self.assertEqual(status["policy_version"], host.bind_calls[0]["policy_version"])
        outbox = host.req041_migration_outbox.epoch_status(status["migration_epoch"])
        self.assertEqual("active", outbox["state"])
        compatibility = __import__("json").loads(status["compatibility_json"])
        self.assertNotIn("target_user_id", str(compatibility))
        self.assertTrue(compatibility["owner_policy"]["configured_target"])
        self.assertTrue(status["source_schema_version"].startswith("companion-v1-"))
        manifest = __import__("json").loads(
            (self.data_dir / status["backup_manifest"]).read_text(encoding="utf-8")
        )
        self.assertEqual("req041.backup_manifest.v2", manifest["schema"])
        self.assertEqual(status["source_schema_version"], manifest["source_inventory"]["source_schema_version"])

    async def test_restart_ignores_persona_profiles_created_after_source_manifest(self) -> None:
        host = self._host()
        await host._req041_initialize_automatic_migration()
        first = host.req041_migration_coordinator.status()
        self.assertTrue(first["backup_manifest"])
        profiles = self.data_dir / "persona_profiles"
        profiles.mkdir(parents=True, exist_ok=True)
        (profiles / "new-persona.json").write_bytes(V608_FIXTURE.read_bytes())

        frozen = host._req041_migration_source_files()

        self.assertEqual([Path(host.data_file)], frozen)
        await host._req041_initialize_automatic_migration()
        second = host.req041_migration_coordinator.status()
        self.assertEqual(first["migration_epoch"], second["migration_epoch"])
        self.assertNotEqual("migration_resume_contract_conflict", second["error_code"])

    async def test_sanitized_v608_fixture_upgrades_without_manual_action_and_preserves_scope_isolation(self) -> None:
        host = self._host()
        source = Path(host.data_file)
        source_before = source.read_bytes()
        host.data = json.loads(source_before)
        host._data_default = host.data
        registry = UnifiedPersonRegistry(host.data)
        linked = registry.create_or_link(
            {
                "companion_instance_id": "astrbot_plugin_private_companion",
                "bot_account_id": "onebot:bot-fixture",
                "adapter_instance_id": "onebot:fixture",
                "subject_namespace": "onebot:user",
                "platform_subject_id": "10001",
            },
            profile={"display_name": "Fixture Owner"},
            operation_id="fixture-preexisting-exact-link",
        )
        host.data["users"]["10001"]["unified_person_id"] = linked["person_id"]

        remote = _Remote()
        host._memory_companion_read_scoped_record = lambda _bridge, context, **kwargs: remote.read(context, **kwargs)
        host._memory_companion_list_scoped_records = lambda _bridge, context, **kwargs: remote.list_records(context, **kwargs)
        host._memory_companion_upsert_scoped_record = lambda _bridge, context, **kwargs: remote.upsert(context, **kwargs)
        host._memory_companion_tombstone_scoped_record = lambda _bridge, context, **kwargs: remote.tombstone(context, **kwargs)

        await host._req041_initialize_automatic_migration()

        self.assertEqual("S6", host.req041_migration_status["phase"])
        self.assertEqual(1, host.req041_migration_status["s4"]["migrated"])
        self.assertEqual(1, host.req041_migration_status["s4"]["pending"])
        self.assertEqual(source_before, source.read_bytes())
        status = host.req041_migration_coordinator.status()
        backup = self.data_dir / status["backup_manifest"]
        self.assertEqual(source_before, (backup.parent / "files" / "companions.json").read_bytes())

        records, contexts = host.req041_scoped_projection_sync.build_records(host.data)
        serialized = json.dumps([record.payload for record in records], ensure_ascii=False)
        self.assertIn("fixture-private-sentinel", serialized)
        self.assertIn("fixture-group-a-sentinel", serialized)
        self.assertIn("fixture-group-b-sentinel", serialized)
        self.assertNotIn("relationship_score", serialized)
        self.assertNotIn("relationship_role", serialized)
        private = next(context for context in contexts if context.kind == "private")
        private_view = host.req041_scoped_projection_sync.read_projection(private)
        self.assertIn("fixture-private-sentinel", json.dumps(private_view, ensure_ascii=False))
        group_views = [
            host.req041_scoped_projection_sync.read_projection(context)
            for context in contexts
            if context.kind == "group_shared"
        ]
        self.assertEqual(2, len(group_views))
        self.assertTrue(any("fixture-group-a-sentinel" in json.dumps(view, ensure_ascii=False) for view in group_views))
        self.assertTrue(any("fixture-group-b-sentinel" in json.dumps(view, ensure_ascii=False) for view in group_views))
        self.assertTrue(all(
            not (
                "fixture-group-a-sentinel" in json.dumps(view, ensure_ascii=False)
                and "fixture-group-b-sentinel" in json.dumps(view, ensure_ascii=False)
            )
            for view in group_views
        ))

    async def test_first_exact_event_claims_pending_v608_owner_and_finishes_replay(self) -> None:
        host = self._host()
        host.data = json.loads(Path(host.data_file).read_text(encoding="utf-8"))
        host._data_default = host.data

        await host._req041_initialize_automatic_migration()

        self.assertEqual(2, host.req041_migration_coordinator.pending_summary()["total"])
        registry = UnifiedPersonRegistry(host.data)
        created = registry.create_or_link(
            {
                "companion_instance_id": "astrbot_plugin_private_companion",
                "bot_account_id": "onebot:bot-fixture",
                "adapter_instance_id": "onebot:fixture",
                "subject_namespace": "onebot:user",
                "platform_subject_id": "10001",
            },
            profile={"display_name": "Fixture Owner", "affinity_score": 87, "owner_mode": "owner"},
            operation_id="fixture-first-exact-event",
        )
        user = host.data["users"]["10001"]
        user["unified_person_id"] = created["person_id"]
        emitted = host.req041_dual_write_producer.emit_identity_change(
            registry=registry,
            result=created,
            action="create",
            operation_id="fixture-first-exact-event",
        )
        self.assertEqual("enqueued", emitted["status"])
        task = host._req041_replay_task
        await asyncio.wait_for(task, timeout=2.0)

        status = host.req041_migration_coordinator.identity_status(created["person_id"])
        self.assertEqual("new", status["read_generation"])
        self.assertGreaterEqual(status["stable_cycles"], 2)
        self.assertEqual(1, host.req041_migration_coordinator.pending_summary()["total"])
        context = NamespaceContext(
            kind="private", identity_id=created["person_id"], group_id="",
            assurance="verified", profile_status="active", policy_version="req041-v1",
            migration_epoch=host.req041_migration_coordinator.status()["migration_epoch"],
        )
        account = host.req041_relationship_store.account(context)
        self.assertEqual("owner", account["relationship_role"])
        self.assertEqual("normal", account["relationship_mode"])
        self.assertEqual(87, account["relationship_score"])
        self.assertEqual([], host.req041_migration_outbox.pending(context.migration_epoch))

        replayed = host.req041_migration_replay.run_batch()
        self.assertEqual("ok", replayed["status"])
        self.assertEqual(0, replayed["count"])
        self.assertEqual(1, host.req041_migration_coordinator.pending_summary()["total"])

    async def test_v608_sqlite_store_is_detected_and_backed_up_online_without_source_mutation(self) -> None:
        host = self._host(source=False)
        fixture = json.loads(V608_FIXTURE.read_text(encoding="utf-8"))
        database = self.data_dir / "companions.db"
        connection = sqlite3.connect(database)
        try:
            connection.execute(
                "CREATE TABLE store_sections (section_name TEXT PRIMARY KEY,payload_json TEXT NOT NULL,"
                "updated_at REAL NOT NULL,checksum TEXT DEFAULT '',schema_version INTEGER DEFAULT 1)"
            )
            connection.executemany(
                "INSERT INTO store_sections VALUES(?,?,0,'',1)",
                [(key, json.dumps(value, ensure_ascii=False)) for key, value in fixture.items()],
            )
            connection.commit()
        finally:
            connection.close()
        source_before = database.read_bytes()
        host.storage_backend = "sqlite"
        host.storage_sqlite_effective_path = str(database)
        host.data = fixture
        host._data_default = host.data

        await host._req041_initialize_automatic_migration()

        status = host.req041_migration_coordinator.status()
        self.assertEqual("S6", status["phase"])
        self.assertEqual(source_before, database.read_bytes())
        manifest = json.loads((self.data_dir / status["backup_manifest"]).read_text(encoding="utf-8"))
        self.assertEqual({"json": 0, "sqlite": 1}, manifest["source_inventory"]["formats"])
        copied = self.data_dir / status["backup_manifest"]
        copied = copied.parent / "files" / "companions.db"
        copied_connection = sqlite3.connect(copied)
        try:
            self.assertEqual("ok", copied_connection.execute("PRAGMA quick_check").fetchone()[0])
            copied_users = copied_connection.execute(
                "SELECT payload_json FROM store_sections WHERE section_name='users'"
            ).fetchone()[0]
        finally:
            copied_connection.close()
        self.assertEqual(fixture["users"], json.loads(copied_users))

    async def test_invalid_local_store_keeps_legacy_runtime_and_creates_no_migration_epoch(self) -> None:
        host = self._host()
        source = Path(host.data_file)
        source.write_text('{"version":1,"users":{}}', encoding="utf-8")
        source_before = source.read_bytes()

        await host._req041_initialize_automatic_migration()

        self.assertEqual("degraded", host.req041_migration_status["state"])
        self.assertEqual("migration_source_required_section_missing", host.req041_migration_status["code"])
        self.assertEqual({}, host.req041_migration_coordinator.status())
        self.assertEqual(source_before, source.read_bytes())
        self.assertFalse((self.data_dir / "req041_backups").exists())

    async def test_missing_memory_degrades_only_shadow_and_restart_reuses_epoch(self) -> None:
        host = self._host(bind=False)
        await host._req041_initialize_automatic_migration()
        first = host.req041_migration_coordinator.status()
        self.assertEqual("S6", first["phase"])
        self.assertEqual("degraded", host.req041_migration_status["state"])
        self.assertEqual("memory_bridge_unavailable", host.req041_migration_status["code"])
        await host._req041_initialize_automatic_migration()
        second = host.req041_migration_coordinator.status()
        self.assertEqual(first["migration_epoch"], second["migration_epoch"])
        self.assertEqual("S6", second["phase"])

    async def test_paused_restart_keeps_durable_dual_write_capture_available(self) -> None:
        first_host = self._host()
        await first_host._req041_initialize_automatic_migration()
        first_host.req041_migration_coordinator.pause("test_pause")
        restarted = self._host()
        await restarted._req041_initialize_automatic_migration()
        self.assertEqual("paused", restarted.req041_migration_status["state"])
        self.assertEqual("capturing_while_paused", restarted.req041_migration_status["dual_write"])
        self.assertIsInstance(restarted.req041_dual_write_producer, MigrationDualWriteProducer)

    async def test_startup_backfills_only_explicitly_linked_legacy_user(self) -> None:
        host = self._host()
        registry = UnifiedPersonRegistry(host.data)
        identity = {
            "companion_instance_id": "astrbot_plugin_private_companion",
            "bot_account_id": "onebot:bot-1",
            "adapter_instance_id": "onebot:default",
            "subject_namespace": "onebot:user",
            "platform_subject_id": "10001",
        }
        person = registry.create_or_link(identity, operation_id="startup-fixture")
        host.data["users"] = {
            "10001": {
                "unified_person_id": person["person_id"],
                "relationship_role": "owner",
                "relationship_mode": "normal",
                "relationship_score": 88,
            }
        }
        await host._req041_initialize_automatic_migration()
        account = host.req041_relationship_store.account(
            __import__("identity_namespace").NamespaceContext(
                kind="private",
                identity_id=person["person_id"],
                group_id="",
                assurance="verified",
                profile_status="active",
                policy_version="req041-v1",
                migration_epoch=host.req041_migration_coordinator.status()["migration_epoch"],
            )
        )
        self.assertEqual("S6", host.req041_migration_status["phase"])
        self.assertEqual(1, host.req041_migration_status["s4"]["migrated"])
        self.assertEqual("owner", account["relationship_role"])
        self.assertEqual("normal", account["relationship_mode"])
        self.assertEqual(88, account["relationship_score"])
        self.assertEqual(
            2,
            host.req041_migration_coordinator.identity_status(person["person_id"])["stable_cycles"],
        )
        self.assertEqual(
            "new",
            host.req041_migration_coordinator.identity_status(person["person_id"])["read_generation"],
        )

    async def test_live_dual_write_schedules_and_drains_s5_replay(self) -> None:
        host = self._host()
        registry = UnifiedPersonRegistry(host.data)
        identity = {
            "companion_instance_id": "astrbot_plugin_private_companion",
            "bot_account_id": "onebot:bot-1",
            "adapter_instance_id": "onebot:default",
            "subject_namespace": "onebot:user",
            "platform_subject_id": "10001",
        }
        person = registry.create_or_link(identity, operation_id="live-fixture")
        user = {
            "user_id": "10001", "unified_person_id": person["person_id"],
            "relationship_role": "friend", "relationship_mode": "normal",
            "relationship_score": 10, "relationship_positive_stage_cap_key": "close",
            "relationship_daily_totals": {"day": "2026-08-10", "positive": 0, "negative": 0},
            "relationship_last_effective_at": 1_700_000_000,
        }
        host.data["users"] = {"10001": user}
        await host._req041_initialize_automatic_migration()
        user.update({
            "relationship_score": 12,
            "relationship_daily_totals": {"day": "2026-08-10", "positive": 2, "negative": 0},
        })
        emitted = host.req041_dual_write_producer.emit_relationship(
            registry=registry, user=user, requested_delta=2, reason_code="inbound",
            source_revision=1,
            result={
                "changed": True, "delta": 2,
                "entry": {"event_key": "f" * 24, "score_before": 10, "score_after": 12},
            },
        )
        task = host._req041_replay_task
        self.assertEqual("enqueued", emitted["status"])
        self.assertIsInstance(task, asyncio.Task)
        await asyncio.wait_for(task, timeout=2.0)
        context = __import__("identity_namespace").NamespaceContext(
            kind="private", identity_id=person["person_id"], group_id="",
            assurance="verified", profile_status="active", policy_version="req041-v1",
            migration_epoch=host.req041_migration_coordinator.status()["migration_epoch"],
        )
        self.assertEqual(12, host.req041_relationship_store.account(context)["relationship_score"])
        self.assertEqual([], host.req041_migration_outbox.pending(context.migration_epoch))

    async def test_reconciled_scoped_views_overlay_copies_without_mutating_legacy_or_crossing_groups(self) -> None:
        host = self._host()
        registry = UnifiedPersonRegistry(host.data)
        person = registry.create_or_link(
            {
                "companion_instance_id": "astrbot_plugin_private_companion",
                "bot_account_id": "onebot:bot-1", "adapter_instance_id": "onebot:default",
                "subject_namespace": "onebot:user", "platform_subject_id": "10001",
            },
            operation_id="scoped-view-fixture",
        )
        user = {
            "user_id": "10001", "identity_subject_id": "10001", "unified_person_id": person["person_id"],
            "nickname": "legacy-name", "companion_memory": {"items": [{"text": "private-sentinel"}]},
            "expression_profile": {"learned_rules": [{"id": "p", "style": "private-rule"}]},
            "relationship_score": 8,
        }
        host.data["users"] = {"10001": user}
        host.data["groups"] = {
            "group-a": {
                "group_id": "group-a", "recent_messages": [{"text": "group-a-sentinel"}],
                "members": {"10001": {"name": "group-a-name", "recent_phrases": ["a"]}},
            },
            "group-b": {
                "group_id": "group-b", "recent_messages": [{"text": "group-b-sentinel"}],
                "members": {"10001": {"name": "group-b-name", "recent_phrases": ["b"]}},
            },
        }
        await host._req041_initialize_automatic_migration()
        self._scoped_remote(host)
        synced = host.req041_scoped_projection_sync.sync_snapshot(host.data)
        self.assertTrue(synced["ok"])
        private_event = types.SimpleNamespace()
        relation_copy = dict(user)
        private_view = host._req041_scoped_private_read_view(private_event, relation_copy)
        self.assertEqual("new", private_view["req041_scoped_read_generation"])
        self.assertEqual("private-sentinel", private_view["companion_memory"]["items"][0]["text"])
        self.assertNotIn("req041_scoped_read_generation", user)
        group_event = types.SimpleNamespace()
        group_view = host._req041_scoped_group_read_view(
            group_event, group_id="group-a", group=host.data["groups"]["group-a"],
            sender_id="10001", relationship_user=user,
        )
        self.assertEqual("group-a-sentinel", group_view["recent_messages"][0]["text"])
        self.assertNotIn("group-b-sentinel", str(group_view))
        self.assertNotIn("req041_scoped_read_generation", host.data["groups"]["group-a"])

        host.req041_scoped_projection_sync.mark_dirty()
        private_dirty_event = types.SimpleNamespace()
        private_dirty = host._req041_scoped_private_read_view(private_dirty_event, relation_copy)
        self.assertEqual("new_unavailable", private_dirty["req041_scoped_read_generation"])
        self.assertIs(private_dirty, private_dirty_event.req041_scoped_private_read_view)
        group_dirty_event = types.SimpleNamespace()
        group_dirty = host._req041_scoped_group_read_view(
            group_dirty_event, group_id="group-a", group=host.data["groups"]["group-a"],
            sender_id="10001", relationship_user=user,
        )
        self.assertEqual("new_unavailable", group_dirty["req041_scoped_read_generation"])
        self.assertIs(group_dirty, group_dirty_event.req041_scoped_group_read_view)

    async def test_external_sqlite_path_fails_safe_without_blocking_legacy_runtime(self) -> None:
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(outside, ignore_errors=True))
        database = outside / "companions.db"
        database.write_bytes(b"not sqlite but explicit active store")
        host = self._host(source=False)
        host.storage_backend = "sqlite"
        host.storage_sqlite_effective_path = str(database)
        await host._req041_initialize_automatic_migration()
        self.assertEqual("degraded", host.req041_migration_status["state"])
        self.assertIn("migration_source_path_invalid", host.req041_migration_status["code"])

    def test_initialize_schedules_migration_before_scheduler_and_maintenance(self) -> None:
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        migration = source.index('"req041_automatic_migration"')
        scheduler = source.index("self._task = asyncio.create_task(self._scheduler_loop())")
        maintenance = source.index("self._startup_maintenance_task = asyncio.create_task")
        self.assertLess(migration, scheduler)
        self.assertLess(migration, maintenance)


if __name__ == "__main__":
    unittest.main()
