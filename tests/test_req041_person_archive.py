from __future__ import annotations

import ast
import asyncio
import copy
import hashlib
from copy import deepcopy
from pathlib import Path
import re
import tempfile
import types
from typing import Any
import unittest

from identity_namespace import NamespaceContext
from migration_outbox import MigrationOutbox
from migration_scoped_projection import ScopedProjectionSynchronizer, scoped_persona_ref
from relationship_account_store import RelationshipAccountStore
from unified_person_registry import UnifiedPersonRegistry
from tests.module_source_index import class_body_defs, class_body_defs_for_file


ROOT = Path(__file__).resolve().parents[1]


def _load_methods(*names: str) -> dict[str, Any]:
    # 巨型模块拆分后，方法体可能位于 main.py 或 main_*.py 域 mixin 类里，
    # 故聚合「宿主类 + 各域 mixin 类」的类体，保持原有断言语义。
    selected = [
        copy.deepcopy(node)
        for node in class_body_defs(ROOT, "main", "PrivateCompanionPlugin")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    missing = set(names) - {node.name for node in selected}
    if missing:
        raise KeyError(sorted(missing))
    for node in selected:
        node.decorator_list = []
    module = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "Any": Any,
        "NamespaceContext": NamespaceContext,
        "deepcopy": deepcopy,
        "hashlib": hashlib,
        "scoped_persona_ref": scoped_persona_ref,
        "_single_line": lambda value, limit=240: " ".join(str(value or "").split())[:limit],
    }
    exec(compile(module, str(ROOT / "main.py"), "exec"), namespace)
    return {name: namespace[name] for name in names}


METHODS = _load_methods(
    "_req041_scoped_private_context_for_person",
    "_req041_person_private_aux_key",
    "_req041_erase_person_private_auxiliary_locked",
    "_req041_persist_archive_saga_locked",
    "archive_unified_person",
    "_req041_resume_confirmed_person_archives",
    "_req041_purge_legacy_person_locked",
    "purge_unified_person",
    "_req041_resume_confirmed_person_purges",
)


class _Request:
    payload: Any = {}

    async def get_json(self, silent: bool = True):
        del silent
        return deepcopy(self.payload)


def _load_page_archive():
    # page_api_users_groups.py 拆分后 archive_unified_person 落在
    # page_api_users_groups_part*.py，按宿主文件名聚合类体（module_source_index）。
    path = ROOT / "page_api_users_groups.py"
    method = next(
        copy.deepcopy(node)
        for node in class_body_defs_for_file(
            ROOT, "page_api_users_groups.py", "PrivateCompanionPageApiUsersGroupsMixin"
        )
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "archive_unified_person"
    )
    method.decorator_list = []
    module = ast.Module(body=[method], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "Any": Any,
        "request": _Request(),
        "logger": types.SimpleNamespace(warning=lambda *_args, **_kwargs: None),
    }
    exec(compile(module, str(path), "exec"), namespace)
    return namespace["archive_unified_person"], namespace["request"]


PAGE_ARCHIVE, PAGE_REQUEST = _load_page_archive()


def _load_page_delete():
    # page_api_users_groups.py 拆分后方法落在 page_api_users_groups_part*.py，
    # 按宿主文件名聚合类体（module_source_index），断言语义不变。
    path = ROOT / "page_api_users_groups.py"
    method = next(
        copy.deepcopy(node)
        for node in class_body_defs_for_file(
            ROOT, "page_api_users_groups.py", "PrivateCompanionPageApiUsersGroupsMixin"
        )
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "delete_unified_person"
    )
    method.decorator_list = []
    module = ast.Module(body=[method], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "Any": Any,
        "request": _Request(),
        "logger": types.SimpleNamespace(warning=lambda *_args, **_kwargs: None),
    }
    exec(compile(module, str(path), "exec"), namespace)
    return namespace["delete_unified_person"], namespace["request"]


PAGE_DELETE, DELETE_REQUEST = _load_page_delete()


def _load_page_lifecycle_sanitizer():
    path = ROOT / "page_api_users_groups.py"
    method = next(
        copy.deepcopy(node)
        for node in class_body_defs_for_file(
            ROOT, "page_api_users_groups.py", "PrivateCompanionPageApiUsersGroupsMixin"
        )
        if isinstance(node, ast.FunctionDef) and node.name == "_safe_person_lifecycle_result"
    )
    method.decorator_list = []
    module = ast.Module(body=[method], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "Any": Any,
        "re": re,
        "_safe_int": lambda value, default=0: int(value) if str(value).lstrip("-").isdigit() else default,
    }
    exec(compile(module, str(path), "exec"), namespace)
    return namespace["_safe_person_lifecycle_result"]


PAGE_SAFE_LIFECYCLE = _load_page_lifecycle_sanitizer()


def _identity(subject: str = "10001") -> dict[str, str]:
    return {
        "companion_instance_id": "astrbot_plugin_private_companion",
        "bot_account_id": "onebot:bot-1",
        "adapter_instance_id": "onebot:default",
        "subject_namespace": "onebot:user",
        "platform_subject_id": subject,
    }


class _AsyncLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _Coordinator:
    def __init__(self) -> None:
        self.rollbacks: list[tuple[str, str]] = []

    def rollback_identity(self, identity_id: str, *, reason_code: str):
        self.rollbacks.append((identity_id, reason_code))
        return {"ok": True}


class _Host:
    _req041_scoped_private_context_for_person = METHODS["_req041_scoped_private_context_for_person"]
    _req041_person_private_aux_key = staticmethod(METHODS["_req041_person_private_aux_key"])
    _req041_erase_person_private_auxiliary_locked = METHODS["_req041_erase_person_private_auxiliary_locked"]
    _req041_persist_archive_saga_locked = METHODS["_req041_persist_archive_saga_locked"]
    archive_unified_person = METHODS["archive_unified_person"]
    _req041_resume_confirmed_person_archives = METHODS["_req041_resume_confirmed_person_archives"]
    _req041_purge_legacy_person_locked = METHODS["_req041_purge_legacy_person_locked"]
    purge_unified_person = METHODS["purge_unified_person"]
    _req041_resume_confirmed_person_purges = METHODS["_req041_resume_confirmed_person_purges"]

    def __init__(self, relationship_path: Path) -> None:
        self.data: dict[str, Any] = {}
        self.registry = UnifiedPersonRegistry(self.data)
        created = self.registry.create_or_link(
            _identity(), profile={"display_name": "A"}, operation_id="create-a",
        )
        self.person_id = created["person_id"]
        self._data_lock = _AsyncLock()
        self.enable_multi_persona_mode = False
        self.persisted = 0
        self.persistence_calls: list[dict[str, Any]] = []
        self.req041_migration_coordinator = _Coordinator()
        self.req041_migration_outbox = MigrationOutbox(
            relationship_path.with_name("outbox.sqlite3"), clock=lambda: 100.0,
        )
        self.req041_migration_outbox.begin_epoch("epoch-1", policy_version="req041-v1")
        self.req041_relationship_store = RelationshipAccountStore(
            relationship_path, active_migration_epoch="epoch-1", clock=lambda: 100.0,
        )
        context = NamespaceContext(
            kind="private", persona_id="default", identity_id=self.person_id, group_id="",
            assurance="verified", profile_status="active", policy_version="req041-v1",
            migration_epoch="epoch-1",
        )
        self.req041_relationship_store.create_account(
            context, operation_id="relationship-create", actor="administrator", score=42,
        )
        self.remote_ok = True
        self.req041_scoped_projection_sync = ScopedProjectionSynchronizer(
            read=lambda *_args, **_kwargs: {"ok": True, "code": "not_found"},
            list_records=lambda *_args, **_kwargs: {"ok": True, "records": []},
            upsert=lambda *_args, **_kwargs: {"ok": True, "code": "created"},
            tombstone=lambda *_args, **_kwargs: {"ok": True, "code": "tombstoned"},
            tombstone_identity_scopes=self._remote_archive,
            migration_epoch="epoch-1", policy_version="req041-v1",
        )

    def _remote_archive(self, *_args, **_kwargs):
        if not self.remote_ok:
            return {"ok": False, "state": "degraded", "code": "memory_unavailable"}
        return {
            "ok": True, "state": "ready", "code": "identity_scopes_tombstoned",
            "count": 3, "namespace_count": 2,
        }

    def _active_unified_person_registry(self):
        return self.registry

    @staticmethod
    def _active_persona_scope() -> str:
        return ""

    def _save_data_now_sync(self, **kwargs: Any) -> None:
        self.persisted += 1
        self.persistence_calls.append(deepcopy(kwargs))


class PersonArchiveSagaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.host = _Host(Path(self.tmp.name) / "relationship.sqlite3")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_remote_failure_keeps_identity_and_relationship_active_then_resume_completes(self) -> None:
        auxiliary_key = self.host._req041_person_private_aux_key(self.host.person_id)
        self.host.data["users"] = {
            "10001": {
                "user_id": "10001", "identity_subject_id": "10001",
                "unified_person_id": self.host.person_id,
            }
        }
        self.host.data["place_cognitive_maps"] = {
            auxiliary_key: {"places": {}}, "10001": {"places": {"legacy": {}}},
        }
        self.host.data["reality_touch_outputs"] = {
            auxiliary_key: {"text": "private"}, "10001": {"text": "legacy"},
        }
        preview = asyncio.run(self.host.archive_unified_person(
            self.host.person_id, operation_id="archive-1", dry_run=True,
        ))
        self.assertEqual("person_archive_prepared", preview["code"])
        self.assertEqual(1, self.host.persisted)
        self.host.remote_ok = False
        failed = asyncio.run(self.host.archive_unified_person(
            self.host.person_id, operation_id="archive-1",
            confirmation_token=preview["confirmation_token"], dry_run=False,
        ))
        self.assertEqual("memory_unavailable", failed["code"])
        self.assertEqual("active", self.host.registry.read_projection(self.host.person_id)["profile_status"])
        context = self.host._req041_scoped_private_context_for_person(self.host.person_id)
        self.assertEqual(42, self.host.req041_relationship_store.account(context)["relationship_score"])

        self.host.remote_ok = True
        completed = asyncio.run(self.host.archive_unified_person(
            self.host.person_id, operation_id="archive-1",
            confirmation_token=preview["confirmation_token"], dry_run=False,
        ))
        self.assertEqual("person_archived", completed["code"])
        self.assertEqual("deleted", self.host.registry.read_projection(self.host.person_id)["profile_status"])
        with self.assertRaisesRegex(Exception, "relationship_account_missing"):
            self.host.req041_relationship_store.account(context)
        self.assertEqual([(self.host.person_id, "person_archived")], self.host.req041_migration_coordinator.rollbacks)
        self.assertEqual({}, self.host.data["place_cognitive_maps"])
        self.assertEqual({}, self.host.data["reality_touch_outputs"])
        self.assertEqual(4, completed["auxiliary_removed_record_count"])
        self.assertEqual(6, self.host.persisted)

    def test_wrong_confirmation_never_calls_destructive_stores(self) -> None:
        preview = asyncio.run(self.host.archive_unified_person(
            self.host.person_id, operation_id="archive-2", dry_run=True,
        ))
        result = asyncio.run(self.host.archive_unified_person(
            self.host.person_id, operation_id="archive-2",
            confirmation_token="0" * 64, dry_run=False,
        ))
        self.assertEqual("archive_confirmation_mismatch", result["code"])
        self.assertEqual("active", self.host.registry.read_projection(self.host.person_id)["profile_status"])
        self.assertTrue(preview["confirmation_token"])

    def test_only_confirmed_saga_is_resumed_without_another_page_action(self) -> None:
        preview = self.host.registry.prepare_person_archive(
            self.host.person_id, operation_id="archive-resume",
            actor_id="page_administrator", reason_code="person_archive",
        )
        self.assertEqual([], self.host.registry.confirmed_person_archives())
        confirmed = self.host.registry.confirm_person_archive(
            self.host.person_id, "archive-resume", preview["confirmation_token"],
            actor_id="page_administrator", reason_code="person_archive",
        )
        self.assertTrue(confirmed["ok"])
        resumed = asyncio.run(self.host._req041_resume_confirmed_person_archives())
        self.assertTrue(resumed["ok"])
        self.assertEqual(1, resumed["completed"])
        self.assertEqual("deleted", self.host.registry.read_projection(self.host.person_id)["profile_status"])

    def test_purge_removes_exact_legacy_records_after_retention_and_keeps_other_users(self) -> None:
        preview = asyncio.run(self.host.archive_unified_person(
            self.host.person_id, operation_id="archive-for-purge", dry_run=True,
        ))
        asyncio.run(self.host.archive_unified_person(
            self.host.person_id, operation_id="archive-for-purge",
            confirmation_token=preview["confirmation_token"], dry_run=False,
        ))
        root = self.host.data["unified_person"]
        root["person_tombstones"][self.host.person_id]["created_at"] = "2020-01-01T00:00:00+00:00"
        self.host.data.update({
            "users": {
                "10001": {"user_id": "10001", "unified_person_id": self.host.person_id, "secret": "private"},
                "20002": {"user_id": "20002", "secret": "other"},
            },
            "groups": {
                "group-a": {
                    "members": {"10001": {"user_id": "10001"}, "20002": {"user_id": "20002"}},
                    "recent_messages": [
                        {"sender_id": "10001", "text": "remove-me"},
                        {"sender_id": "20002", "text": "keep-me"},
                    ],
                }
            },
            "worldbook_member_profiles": {
                "10001": {"user_id": "10001", "content": "remove-profile"},
                "20002": {"user_id": "20002", "content": "keep-profile"},
            },
            "legacy_private_cache": {
                "10001": {"user_id": "10001", "content": "remove-legacy"},
                "20002": {"user_id": "20002", "content": "keep-legacy"},
            },
            "_req041_private_memory": {
                "schema": "req041.person_private_memory.v1",
                "records": {
                    self.host.person_id: {"content": {"open_loops": ["private"]}},
                    "person-other": {"content": {"open_loops": ["keep"]}},
                },
            },
        })
        purge_preview = asyncio.run(self.host.purge_unified_person(
            self.host.person_id, operation_id="purge-1", dry_run=True,
        ))
        self.assertEqual("person_purge_prepared", purge_preview["code"])
        purged = asyncio.run(self.host.purge_unified_person(
            self.host.person_id, operation_id="purge-1",
            confirmation_token=purge_preview["confirmation_token"], dry_run=False,
        ))
        self.assertEqual("person_purged", purged["code"])
        self.assertNotIn("10001", self.host.data["users"])
        self.assertIn("20002", self.host.data["users"])
        self.assertNotIn("10001", self.host.data["groups"]["group-a"]["members"])
        self.assertEqual(["keep-me"], [item["text"] for item in self.host.data["groups"]["group-a"]["recent_messages"]])
        self.assertNotIn("10001", self.host.data["worldbook_member_profiles"])
        self.assertNotIn("10001", self.host.data["legacy_private_cache"])
        self.assertIn("20002", self.host.data["legacy_private_cache"])
        private_records = self.host.data["_req041_private_memory"]["records"]
        self.assertNotIn(self.host.person_id, private_records)
        self.assertIn("person-other", private_records)
        self.assertIsNone(self.host.registry.read_projection(self.host.person_id))
        self.assertIn(self.host.person_id, root["person_tombstones"])
        self.assertEqual(
            {"full_scope": "admin_import_export"},
            self.host.persistence_calls[-1],
        )

    def test_confirmed_purge_resumes_without_page_reconfirmation(self) -> None:
        preview = asyncio.run(self.host.archive_unified_person(
            self.host.person_id, operation_id="archive-before-resume-purge", dry_run=True,
        ))
        asyncio.run(self.host.archive_unified_person(
            self.host.person_id, operation_id="archive-before-resume-purge",
            confirmation_token=preview["confirmation_token"], dry_run=False,
        ))
        root = self.host.data["unified_person"]
        root["person_tombstones"][self.host.person_id]["created_at"] = "2020-01-01T00:00:00+00:00"
        purge = self.host.registry.prepare_person_purge(
            self.host.person_id, operation_id="purge-resume", actor_id="page_administrator",
        )
        self.assertEqual([], self.host.registry.confirmed_person_purges())
        self.host.registry.confirm_person_purge(
            self.host.person_id, "purge-resume", purge["confirmation_token"],
            actor_id="page_administrator",
        )
        resumed = asyncio.run(self.host._req041_resume_confirmed_person_purges())
        self.assertTrue(resumed["ok"])
        self.assertEqual(1, resumed["completed"])
        self.assertIsNone(self.host.registry.read_projection(self.host.person_id))


class _PageHost:
    archive_unified_person = PAGE_ARCHIVE
    _safe_person_lifecycle_result = staticmethod(PAGE_SAFE_LIFECYCLE)

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.plugin = self

    async def _archive(self, person_id: str, **kwargs):
        self.calls.append({"person_id": person_id, **kwargs})
        if kwargs.get("dry_run"):
            return {"ok": True, "code": "person_archive_prepared", "confirmation_token": "a" * 64}
        return {"ok": True, "code": "person_archived"}

    @staticmethod
    def _single_line(value: Any, limit: int) -> str:
        return " ".join(str(value or "").split())[:limit]

    @staticmethod
    def _ok(value: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "data": value}

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        return {"ok": False, "error": message}

    def __getattribute__(self, name: str):
        if name == "archive_unified_person" and object.__getattribute__(self, "plugin") is self:
            # The page method resolves the service on plugin; expose the
            # service callback there while keeping the bound page handler.
            caller = object.__getattribute__(self, "_page_handler_active") if hasattr(self, "_page_handler_active") else False
            if caller:
                return object.__getattribute__(self, "_archive")
        return object.__getattribute__(self, name)

    async def call_page(self):
        self._page_handler_active = True
        try:
            return await PAGE_ARCHIVE(self)
        finally:
            self._page_handler_active = False


class PersonArchivePageTests(unittest.TestCase):
    def test_page_requires_confirmation_for_apply_and_forwards_preview(self) -> None:
        host = _PageHost()
        PAGE_REQUEST.payload = {"person_id": "person_a", "operation_id": "archive-1", "dry_run": True}
        preview = asyncio.run(host.call_page())
        self.assertTrue(preview["ok"])
        self.assertTrue(host.calls[0]["dry_run"])
        PAGE_REQUEST.payload = {"person_id": "person_a", "operation_id": "archive-2", "dry_run": False}
        rejected = asyncio.run(host.call_page())
        self.assertFalse(rejected["ok"])
        self.assertEqual(1, len(host.calls))

    def test_delete_page_forwards_only_fixed_admin_purge_contract(self) -> None:
        calls: list[dict[str, Any]] = []

        class Service:
            async def purge_unified_person(self, person_id: str, **kwargs):
                calls.append({"person_id": person_id, **kwargs})
                return {"ok": True, "code": "person_purge_prepared", "confirmation_token": "b" * 64}

        class Page:
            plugin = Service()
            delete_unified_person = PAGE_DELETE
            _safe_person_lifecycle_result = staticmethod(PAGE_SAFE_LIFECYCLE)

            @staticmethod
            def _single_line(value: Any, limit: int) -> str:
                return " ".join(str(value or "").split())[:limit]

            @staticmethod
            def _ok(value: dict[str, Any]) -> dict[str, Any]:
                return {"ok": True, "data": value}

            @staticmethod
            def _error(message: str) -> dict[str, Any]:
                return {"ok": False, "error": message}

        DELETE_REQUEST.payload = {"person_id": "person_a", "operation_id": "purge-1", "dry_run": True}
        result = asyncio.run(Page().delete_unified_person())
        self.assertTrue(result["ok"])
        self.assertEqual("page_administrator", calls[0]["actor_id"])
        self.assertEqual("person_delete", calls[0]["reason_code"])
        DELETE_REQUEST.payload = {"person_id": "person_a", "operation_id": "purge-2", "dry_run": False}
        self.assertFalse(asyncio.run(Page().delete_unified_person())["ok"])
        self.assertEqual(1, len(calls))

    def test_page_lifecycle_result_is_minimal_and_retention_is_actionable(self) -> None:
        raw = {
            "ok": True,
            "state": "prepared",
            "code": "person_archive_prepared",
            "confirmation_token": "c" * 64,
            "active_identity_count": 2,
            "group_overlay_count": 3,
            "person_id": "person_secret",
            "operation_id": "operation_secret",
            "identity_key": "identity_secret",
            "remote_receipt": {"payload": "secret"},
        }
        safe = PAGE_SAFE_LIFECYCLE(raw, "archive")
        serialized = repr(safe)
        self.assertEqual(2, safe["active_identity_count"])
        self.assertEqual(3, safe["group_overlay_count"])
        self.assertEqual(2, safe["impact"]["migration_stream_count"])
        self.assertFalse(safe["impact"]["automatic_restore_available"])
        for secret in ("person_secret", "operation_secret", "identity_secret", "payload"):
            self.assertNotIn(secret, serialized)

        calls: list[dict[str, Any]] = []

        class Service:
            async def purge_unified_person(self, person_id: str, **kwargs):
                calls.append({"person_id": person_id, **kwargs})
                return {
                    "ok": False, "state": "retention", "code": "archive_retention_active",
                    "eligible_at": "2030-01-02T03:04:05+00:00", "person_id": "person_secret",
                }

        class Page:
            plugin = Service()
            delete_unified_person = PAGE_DELETE
            _safe_person_lifecycle_result = staticmethod(PAGE_SAFE_LIFECYCLE)

            @staticmethod
            def _single_line(value: Any, limit: int) -> str:
                return " ".join(str(value or "").split())[:limit]

            @staticmethod
            def _ok(value: dict[str, Any]) -> dict[str, Any]:
                return {"ok": True, "data": value}

            @staticmethod
            def _error(message: str) -> dict[str, Any]:
                return {"ok": False, "error": message}

        DELETE_REQUEST.payload = {"person_id": "person_a", "operation_id": "purge-wait", "dry_run": True}
        response = asyncio.run(Page().delete_unified_person())
        self.assertTrue(response["ok"])
        result = response["data"]["result"]
        self.assertFalse(result["ok"])
        self.assertEqual("archive_retention_active", result["code"])
        self.assertEqual("2030-01-02T03:04:05+00:00", result["eligible_at"])
        self.assertNotIn("person_secret", repr(result))


if __name__ == "__main__":
    unittest.main()
