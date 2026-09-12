from __future__ import annotations

import ast
import asyncio
import copy
import hashlib
import hmac
import json
from pathlib import Path
import types
from typing import Any
import unittest

from identity_namespace import NamespaceContext
from migration_backfill import legacy_pending_reference
from unified_person_registry import UnifiedPersonRegistry


ROOT = Path(__file__).resolve().parents[1]


def _identity(subject: str) -> dict[str, str]:
    return {
        "companion_instance_id": "astrbot_plugin_private_companion",
        "bot_account_id": "onebot:bot-1",
        "adapter_instance_id": "onebot:default",
        "subject_namespace": "onebot:user",
        "platform_subject_id": subject,
    }


class _Request:
    payload: dict[str, Any] = {}

    async def get_json(self, silent: bool = True) -> dict[str, Any]:
        del silent
        return copy.deepcopy(self.payload)


def _load_page_unlink():
    path = ROOT / "page_api_users_groups.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    owner = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "PrivateCompanionPageApiUsersGroupsMixin"
    )
    names = {
        "_identity_domain_summary",
        "_identity_pending_reference",
        "_identity_pending_summary",
        "_identity_admin_summary",
        "_identity_archive_status",
        "_identity_archive_remote_available",
        "delete_user",
        "_identity_link_confirmation",
        "_identity_unlink_confirmation",
        "_safe_identity_unlink_result",
        "link_unified_identity",
        "unlink_unified_identity",
        "update_pending_identity_review",
    }
    methods = [
        copy.deepcopy(node) for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    for method in methods:
        method.decorator_list = []
    module = ast.Module(body=methods, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "Any": Any,
        "hashlib": hashlib,
        "hmac": hmac,
        "json": json,
        "request": _Request(),
        "logger": types.SimpleNamespace(warning=lambda *_args, **_kwargs: None),
        "_safe_int": lambda value, default=0: int(value or default),
        "legacy_pending_reference": legacy_pending_reference,
    }
    exec(compile(module, str(path), "exec"), namespace)
    return {name: namespace[name] for name in names}, namespace["request"]


PAGE_METHODS, PAGE_REQUEST = _load_page_unlink()


class _AsyncLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _PageHost:
    _identity_domain_summary = PAGE_METHODS["_identity_domain_summary"]
    _identity_pending_reference = PAGE_METHODS["_identity_pending_reference"]
    _identity_pending_summary = PAGE_METHODS["_identity_pending_summary"]
    _identity_admin_summary = PAGE_METHODS["_identity_admin_summary"]
    _identity_archive_status = PAGE_METHODS["_identity_archive_status"]
    _identity_archive_remote_available = PAGE_METHODS["_identity_archive_remote_available"]
    delete_user = PAGE_METHODS["delete_user"]
    _identity_link_confirmation = staticmethod(PAGE_METHODS["_identity_link_confirmation"])
    _identity_unlink_confirmation = staticmethod(PAGE_METHODS["_identity_unlink_confirmation"])
    _safe_identity_unlink_result = staticmethod(PAGE_METHODS["_safe_identity_unlink_result"])
    link_unified_identity = PAGE_METHODS["link_unified_identity"]
    unlink_unified_identity = PAGE_METHODS["unlink_unified_identity"]
    update_pending_identity_review = PAGE_METHODS["update_pending_identity_review"]

    def __init__(self) -> None:
        self.data: dict[str, Any] = {"users": {}}
        self.registry = UnifiedPersonRegistry(self.data)
        created = self.registry.create_or_link(
            _identity("10001"), profile={"display_name": "A"}, operation_id="create-a"
        )
        self.person_id = created["person_id"]
        linked = self.registry.link_identity(
            self.person_id, _identity("10002"), operation_id="link-b"
        )
        assert linked["ok"]
        self.data["users"] = {
            "10001": {
                "user_id": "10001", "identity_subject_id": "10001",
                "unified_person_id": self.person_id,
            },
            "10002": {
                "user_id": "10002", "identity_subject_id": "10002",
                "unified_person_id": self.person_id,
            },
        }
        self.plugin = self
        self._data_lock = _AsyncLock()
        self.saved = 0
        self.dual_writes: list[tuple[str, str]] = []

    @staticmethod
    def _active_persona_scope() -> str:
        return ""

    def _page_unified_person_registry(self):
        return self.registry

    @staticmethod
    def _single_line(value: Any, limit: int) -> str:
        return " ".join(str(value or "").split())[:limit]

    @staticmethod
    def _ok(value: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "data": value}

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        return {"ok": False, "error": message}

    def _schedule_data_save(self, *, sections=(), **_kwargs) -> None:
        self.assert_saved_sections = getattr(self, "assert_saved_sections", [])
        self.assert_saved_sections.append(set(sections or ()))
        self.saved += 1

    def _req041_emit_identity_dual_write(self, result, *, action, operation_id, registry):
        assert result.get("identity_key")
        assert registry is self.registry
        self.dual_writes.append((action, operation_id))


class IdentityAdminUiTests(unittest.TestCase):
    def test_missing_archive_bridge_exposes_reason_and_recovery(self) -> None:
        host = _PageHost()
        host._req041_scoped_archive_available = lambda: False
        summary = host._identity_admin_summary(host.data["users"]["10001"])
        lifecycle = summary["lifecycle"]
        self.assertFalse(lifecycle["archive_ready"])
        self.assertFalse(lifecycle["can_archive"])
        self.assertEqual("scoped_archive_bridge_unavailable", lifecycle["archive_code"])
        self.assertIn("记忆桥接", lifecycle["archive_reason"])
        self.assertIn("LivingMemory", lifecycle["archive_recovery"])

    def test_paused_archive_bridge_exposes_migration_recovery(self) -> None:
        host = _PageHost()
        host._req041_scoped_archive_available = lambda: False
        host.req041_scoped_projection_sync = types.SimpleNamespace(archive_identity_scopes=lambda: None)
        host.req041_migration_status = {"state": "paused"}
        status = host._identity_archive_status()
        self.assertEqual("scoped_archive_paused", status["code"])
        self.assertIn("暂停", status["reason"])
        self.assertIn("身份迁移", status["recovery"])

    def test_delete_reports_unavailable_archive_without_modifying_user(self) -> None:
        host = _PageHost()
        host._req041_scoped_archive_available = lambda: False
        host._canonical_private_user_id = lambda value: value
        before = copy.deepcopy(host.data)
        PAGE_REQUEST.payload = {"user_id": "10001"}
        result = asyncio.run(host.delete_user())
        self.assertFalse(result["ok"])
        self.assertIn("当前无法归档", result["error"])
        self.assertIn("scoped archive", result["error"])
        self.assertNotIn("预览并归档", result["error"])
        self.assertEqual(before, host.data)

    def test_domain_summary_counts_only_scopes_and_records_for_current_person(self) -> None:
        host = _PageHost()
        other = "person_" + "f" * 24

        def context(kind: str, identity_id: str = "", group_id: str = "") -> NamespaceContext:
            return NamespaceContext(
                kind=kind, persona_id="default", identity_id=identity_id,
                group_id=group_id, assurance="verified", profile_status="active",
                policy_version="req041-v1", migration_epoch="epoch-1",
            )

        private = context("private", host.person_id)
        member_a = context("group_member", host.person_id, "group-a")
        member_b = context("group_member", host.person_id, "group-b")
        shared_a = context("group_shared", group_id="group-a")
        shared_b = context("group_shared", group_id="group-b")
        other_private = context("private", other)
        contexts = [private, member_a, member_b, shared_a, shared_b, other_private]
        records = [
            types.SimpleNamespace(context=private),
            types.SimpleNamespace(context=private),
            types.SimpleNamespace(context=member_a),
            types.SimpleNamespace(context=shared_a),
            types.SimpleNamespace(context=shared_b),
            types.SimpleNamespace(context=other_private),
        ]

        class Synchronizer:
            @staticmethod
            def build_records(_snapshot, *, source_scope):
                assert source_scope == "default"
                return records, contexts

            @staticmethod
            def is_ready(value):
                return value.cache_scope() in {private.cache_scope(), member_a.cache_scope(), shared_a.cache_scope()}

        host.req041_scoped_projection_sync = Synchronizer()
        summary = host._identity_domain_summary(host.person_id, {"users": {}})
        self.assertEqual(
            {"status": "ready", "scope_count": 1, "record_count": 2, "ready_scope_count": 1},
            summary["private"],
        )
        self.assertEqual(2, summary["group_member"]["scope_count"])
        self.assertEqual(1, summary["group_member"]["record_count"])
        self.assertEqual(2, summary["group_shared"]["scope_count"])
        self.assertEqual(2, summary["group_shared"]["record_count"])
        self.assertNotIn("group-a", json.dumps(summary, ensure_ascii=False))

    def test_safe_summary_and_exact_resolver_never_expose_subject(self) -> None:
        host = _PageHost()
        resolved = host.registry.identity_for_person_subject(host.person_id, "10002")
        self.assertEqual("10002", resolved["platform_subject_id"])
        summary = host.registry.safe_admin_person_summary(host.person_id, "10002")
        self.assertTrue(summary["linked"])
        self.assertTrue(summary["current_identity_linked"])
        self.assertEqual(2, summary["active_identity_count"])
        encoded = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("10001", encoded)
        self.assertNotIn("10002", encoded)
        self.assertNotIn("identity_key", encoded)

    def test_detached_identity_relink_requires_preview_and_never_exposes_identity(self) -> None:
        host = _PageHost()
        detached = host.registry.unlink_identity(
            host.person_id,
            _identity("10002"),
            operation_id="detach-for-relink",
            actor_id="page_administrator",
            dry_run=False,
        )
        self.assertTrue(detached["ok"])
        self.assertIsNotNone(
            host.registry.detached_identity_for_person_subject(host.person_id, "10002")
        )
        summary = host._identity_admin_summary(
            host.data["users"]["10002"], user_id="10002", snapshot={}
        )
        self.assertTrue(summary["current_identity_detached"])
        self.assertTrue(summary["lifecycle"]["can_relink_current"])

        PAGE_REQUEST.payload = {
            "person_id": host.person_id,
            "user_id": "10002",
            "operation_id": "page-relink-b",
            "dry_run": True,
        }
        preview = asyncio.run(host.link_unified_identity())
        self.assertTrue(preview["ok"])
        self.assertEqual("identity_relink_preview", preview["data"]["result"]["code"])
        encoded_preview = json.dumps(preview, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("platform_subject_id", encoded_preview)
        self.assertNotIn("identity_key", encoded_preview)
        self.assertNotIn("checkpoint", encoded_preview)

        PAGE_REQUEST.payload["dry_run"] = False
        PAGE_REQUEST.payload["confirmation_token"] = preview["data"]["result"]["confirmation_token"]
        applied = asyncio.run(host.link_unified_identity())
        self.assertTrue(applied["ok"])
        self.assertEqual("identity_relinked", applied["data"]["result"]["code"])
        self.assertIsNotNone(
            host.registry.identity_for_person_subject(host.person_id, "10002")
        )
        self.assertEqual([("link", "page-relink-b")], host.dual_writes)
        self.assertEqual(1, host.saved)

    def test_detached_identity_relink_rejects_forged_confirmation(self) -> None:
        host = _PageHost()
        host.registry.unlink_identity(
            host.person_id,
            _identity("10002"),
            operation_id="detach-for-forged-relink",
            actor_id="page_administrator",
            dry_run=False,
        )
        PAGE_REQUEST.payload = {
            "person_id": host.person_id,
            "user_id": "10002",
            "operation_id": "page-relink-forged",
            "dry_run": False,
            "confirmation_token": "0" * 64,
        }
        rejected = asyncio.run(host.link_unified_identity())
        self.assertFalse(rejected["ok"])
        self.assertIsNotNone(
            host.registry.detached_identity_for_person_subject(host.person_id, "10002")
        )

    def test_detached_identity_relink_rejects_stale_projection_preview(self) -> None:
        host = _PageHost()
        host.registry.unlink_identity(
            host.person_id,
            _identity("10002"),
            operation_id="detach-for-stale-relink",
            actor_id="page_administrator",
            dry_run=False,
        )
        PAGE_REQUEST.payload = {
            "person_id": host.person_id,
            "user_id": "10002",
            "operation_id": "page-relink-stale",
            "dry_run": True,
        }
        preview = asyncio.run(host.link_unified_identity())
        self.assertTrue(preview["ok"])
        changed = host.registry.link_identity(
            host.person_id,
            _identity("10003"),
            operation_id="concurrent-link",
            actor_id="other_administrator",
        )
        self.assertTrue(changed["ok"])
        PAGE_REQUEST.payload["dry_run"] = False
        PAGE_REQUEST.payload["confirmation_token"] = preview["data"]["result"]["confirmation_token"]
        rejected = asyncio.run(host.link_unified_identity())
        self.assertFalse(rejected["ok"])
        self.assertIsNotNone(
            host.registry.detached_identity_for_person_subject(host.person_id, "10002")
        )

    def test_unlinked_user_maps_to_safe_pending_status_without_exposing_lookup_material(self) -> None:
        host = _PageHost()
        raw_user_id = "legacy-user-998877"
        epoch = "migration-epoch-secret"
        expected = legacy_pending_reference(epoch, "default", raw_user_id)

        class Coordinator:
            seen = ""

            @staticmethod
            def status():
                return {"migration_epoch": epoch}

            @classmethod
            def pending_status(cls, reference):
                cls.seen = reference
                return {
                    "found": True,
                    "source_kind": "legacy_user",
                    "reason_code": "identity_link_missing",
                    "state": "pending",
                    "first_seen_at": 1.0,
                    "updated_at": 2.0,
                }

        host.req041_migration_coordinator = Coordinator()
        summary = host._identity_admin_summary(
            {"user_id": raw_user_id}, user_id=raw_user_id, snapshot={}
        )
        self.assertEqual(expected, Coordinator.seen)
        self.assertEqual("identity_link_missing", summary["pending"]["reason_code"])
        encoded = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        self.assertNotIn(raw_user_id, encoded)
        self.assertNotIn(epoch, encoded)
        self.assertNotIn(expected, encoded)

    def test_pending_review_defer_and_restore_use_only_selected_user(self) -> None:
        host = _PageHost()
        raw_user_id = "legacy-user-review"
        host.data["users"][raw_user_id] = {"user_id": raw_user_id}
        epoch = "migration-epoch-review"
        states: dict[str, str] = {}

        class Coordinator:
            @staticmethod
            def status():
                return {"migration_epoch": epoch}

            @staticmethod
            def pending_status(reference):
                state = states.get(reference, "pending")
                return {
                    "found": True, "source_kind": "legacy_user",
                    "reason_code": "identity_link_missing", "state": state,
                }

            @staticmethod
            def dismiss_pending(reference):
                if states.get(reference, "pending") != "pending":
                    return False
                states[reference] = "dismissed"
                return True

            @staticmethod
            def restore_pending(reference):
                if states.get(reference) != "dismissed":
                    return False
                states[reference] = "pending"
                return True

        host.req041_migration_coordinator = Coordinator()
        reference = legacy_pending_reference(epoch, "default", raw_user_id)
        PAGE_REQUEST.payload = {"user_id": raw_user_id, "action": "dismiss"}
        dismissed = asyncio.run(host.update_pending_identity_review())
        self.assertTrue(dismissed["ok"])
        self.assertEqual("dismissed", dismissed["data"]["result"]["state"])
        self.assertEqual("dismissed", states[reference])
        self.assertNotIn(raw_user_id, json.dumps(dismissed, ensure_ascii=False))
        self.assertNotIn(reference, json.dumps(dismissed, ensure_ascii=False))

        PAGE_REQUEST.payload = {"user_id": raw_user_id, "action": "restore"}
        restored = asyncio.run(host.update_pending_identity_review())
        self.assertTrue(restored["ok"])
        self.assertEqual("pending", restored["data"]["result"]["state"])

        PAGE_REQUEST.payload = {"user_id": "10001", "action": "dismiss"}
        self.assertFalse(asyncio.run(host.update_pending_identity_review())["ok"])

    def test_page_unlink_derives_exact_identity_from_selected_user(self) -> None:
        host = _PageHost()
        PAGE_REQUEST.payload = {
            "person_id": host.person_id,
            "user_id": "10002",
            "operation_id": "page-unlink-b",
            "dry_run": True,
        }
        preview = asyncio.run(host.unlink_unified_identity())
        self.assertTrue(preview["ok"])
        self.assertTrue(preview["data"]["result"]["ok"])
        self.assertEqual("migration_dry_run", preview["data"]["result"]["code"])
        self.assertNotIn("identity_key", json.dumps(preview, ensure_ascii=False))
        self.assertNotIn("checkpoint", json.dumps(preview, ensure_ascii=False))

        PAGE_REQUEST.payload["dry_run"] = False
        PAGE_REQUEST.payload["confirmation_token"] = preview["data"]["result"]["confirmation_token"]
        applied = asyncio.run(host.unlink_unified_identity())
        self.assertTrue(applied["ok"])
        self.assertEqual("identity_unlinked", applied["data"]["result"]["code"])
        self.assertIsNone(
            host.registry.identity_for_person_subject(host.person_id, "10002")
        )
        self.assertEqual(1, host.saved)

    def test_page_unlink_rejects_stale_or_missing_preview_confirmation(self) -> None:
        host = _PageHost()
        PAGE_REQUEST.payload = {
            "person_id": host.person_id,
            "user_id": "10002",
            "operation_id": "page-unlink-stale",
            "dry_run": False,
        }
        self.assertFalse(asyncio.run(host.unlink_unified_identity())["ok"])
        PAGE_REQUEST.payload["confirmation_token"] = "0" * 64
        rejected = asyncio.run(host.unlink_unified_identity())
        self.assertFalse(rejected["ok"])
        self.assertIsNotNone(
            host.registry.identity_for_person_subject(host.person_id, "10002")
        )

    def test_page_unlink_rejects_browser_supplied_raw_identity(self) -> None:
        host = _PageHost()
        PAGE_REQUEST.payload = {
            "person_id": host.person_id,
            "identity": _identity("10002"),
            "operation_id": "raw-browser-identity",
            "dry_run": True,
        }
        rejected = asyncio.run(host.unlink_unified_identity())
        self.assertFalse(rejected["ok"])
        self.assertIsNotNone(
            host.registry.identity_for_person_subject(host.person_id, "10002")
        )

    def test_official_v620_user_workspace_contains_safe_identity_lifecycle(self) -> None:
        english = (ROOT / "pages" / "companion-panel" / "app.js").read_text(encoding="utf-8")
        chinese = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
        self.assertEqual(english, chinese)
        self.assertIn('["identity","身份与隔离"]', english)
        self.assertIn('data-identity-action="archive"', english)
        self.assertIn('data-identity-action="relink"', english)
        self.assertIn('"/user/identity/link"', english)
        self.assertIn('postJson(endpoint, body)', english)
        self.assertIn('confirmation_token: preview.confirmationToken', english)
        self.assertIn("安全待确认", english)
        self.assertIn("等待精确身份事件", english)
        self.assertIn("data-identity-lifecycle-preview", english)
        self.assertIn("当前版本尚无自动恢复归档的全链路", english)
        self.assertIn("群共享记忆不归个人所有", english)
        self.assertIn("archive_retention_active", english)
        self.assertIn('data-pending-identity-action=', english)
        self.assertIn('"/user/identity/pending"', english)
        self.assertIn("暂不处理", english)
        self.assertIn("重新加入审核", english)
        self.assertNotIn('data-identity-action="confirm-pending"', english)
        self.assertIn("不能绕过统一数据链直接删除", (ROOT / "page_api_users_groups.py").read_text(encoding="utf-8"))
        self.assertNotIn("identity.identity_key", english)


if __name__ == "__main__":
    unittest.main()
