from __future__ import annotations

import ast
import asyncio
import copy
from copy import deepcopy
from pathlib import Path
import types
from typing import Any
import unittest
import uuid

from identity_namespace import NamespaceContext
from migration_scoped_projection import scoped_group_ref, scoped_persona_ref


ROOT = Path(__file__).resolve().parents[1]


def _load_methods(*names: str) -> dict[str, Any]:
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    owner = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "PrivateCompanionPlugin"
    )
    selected = [
        copy.deepcopy(node) for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    for node in selected:
        node.decorator_list = []
    module = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(module)

    def set_config(config: dict[str, Any], key: str, value: Any) -> bool:
        config[key] = deepcopy(value)
        return True

    namespace = {
        "Any": Any,
        "asyncio": asyncio,
        "NamespaceContext": NamespaceContext,
        "deepcopy": deepcopy,
        "scoped_group_ref": scoped_group_ref,
        "scoped_persona_ref": scoped_persona_ref,
        "uuid": uuid,
        "_now_ts": lambda: 100.0,
        "_set_into_config": set_config,
        "_single_line": lambda value, limit=240: " ".join(str(value or "").split())[:limit],
        "logger": types.SimpleNamespace(
            warning=lambda *_args, **_kwargs: None,
            info=lambda *_args, **_kwargs: None,
        ),
    }
    exec(compile(module, str(ROOT / "main.py"), "exec"), namespace)
    return {name: namespace[name] for name in names}


METHODS = _load_methods(
    "_req041_erase_scoped_group_data",
    "_req041_group_reset_sagas_locked",
    "_req041_finalize_group_reset_locked",
    "_req041_persist_archive_saga_locked",
    "_req041_memory_scope_was_bound",
    "_req041_group_remote_cleanup_required",
    "reset_group_scoped_data",
    "_req041_resume_confirmed_group_resets",
)


class _Request:
    payload: Any = {}

    async def get_json(self, silent: bool = True) -> Any:
        del silent
        return deepcopy(self.payload)


def _load_page_delete_group():
    path = ROOT / "page_api_users_groups.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    owner = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "PrivateCompanionPageApiUsersGroupsMixin"
    )
    method = next(
        copy.deepcopy(node) for node in owner.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "delete_group"
    )
    method.decorator_list = []
    module = ast.Module(body=[method], type_ignores=[])
    ast.fix_missing_locations(module)
    request = _Request()
    namespace = {
        "Any": Any,
        "request": request,
        "logger": types.SimpleNamespace(error=lambda *_args, **_kwargs: None),
    }
    exec(compile(module, str(path), "exec"), namespace)
    return namespace["delete_group"], request


PAGE_DELETE_GROUP, PAGE_REQUEST = _load_page_delete_group()


class _Remote:
    policy_version = "req041-v1"
    migration_epoch = "epoch-1"

    def __init__(self) -> None:
        self.ok = True
        self.calls: list[tuple[str, str]] = []

    def erase_group_scopes(
        self, context: NamespaceContext, *, operation_id: str, reason_code: str
    ) -> dict[str, Any]:
        self.calls.append((context.group_id, operation_id))
        if not self.ok:
            return {"ok": False, "state": "degraded", "code": "memory_unavailable"}
        return {
            "ok": True, "state": "ready", "code": "group_scopes_erased",
            "count": 3, "namespace_count": 3, "reason_code": reason_code,
        }


class _Host:
    _req041_erase_scoped_group_data = METHODS["_req041_erase_scoped_group_data"]
    _req041_group_reset_sagas_locked = METHODS["_req041_group_reset_sagas_locked"]
    _req041_finalize_group_reset_locked = METHODS["_req041_finalize_group_reset_locked"]
    _req041_persist_archive_saga_locked = METHODS["_req041_persist_archive_saga_locked"]
    _req041_memory_scope_was_bound = METHODS["_req041_memory_scope_was_bound"]
    _req041_group_remote_cleanup_required = METHODS["_req041_group_remote_cleanup_required"]
    reset_group_scoped_data = METHODS["reset_group_scoped_data"]
    _req041_resume_confirmed_group_resets = METHODS["_req041_resume_confirmed_group_resets"]

    def __init__(self, data: dict[str, Any] | None = None, config: dict[str, Any] | None = None) -> None:
        self.data = deepcopy(data) if isinstance(data, dict) else {
            "groups": {
                "group-a": {"group_id": "group-a", "recent_messages": [{"text": "secret-a"}]},
                "group-b": {"group_id": "group-b", "recent_messages": [{"text": "keep-b"}]},
            }
        }
        self.config = deepcopy(config) if isinstance(config, dict) else {}
        self.group_whitelist_ids = list(self.config.get("group_whitelist_ids") or ["group-a", "group-b"])
        self.group_blacklist_ids = list(self.config.get("group_blacklist_ids") or ["group-a"])
        self.expression_group_learning_source_ids = list(
            self.config.get("expression_group_learning_source_ids") or ["group-a", "group-b"]
        )
        self.expression_group_application_ids = list(
            self.config.get("expression_group_application_ids") or ["group-a"]
        )
        self._data_lock = asyncio.Lock()
        self.enable_multi_persona_mode = False
        self.req041_migration_status = {"required": True}
        self.req041_scoped_projection_sync = _Remote()
        self.persisted = 0
        self.config_save_ok = True
        self.voice_refreshes = 0

    @staticmethod
    def _active_persona_scope() -> str:
        return ""

    @staticmethod
    def _normalize_group_identity_id(value: Any) -> str:
        return str(value or "").strip()

    def _save_data_now_sync(self) -> None:
        self.persisted += 1

    async def _save_config_if_possible(self) -> bool:
        return self.config_save_ok

    def _refresh_expression_voice_profile(self) -> None:
        self.voice_refreshes += 1


class GroupResetSagaTests(unittest.TestCase):
    def test_remote_failure_persists_saga_without_mutating_local_then_restart_resumes(self) -> None:
        host = _Host()
        host.req041_scoped_projection_sync.ok = False
        failed = asyncio.run(host.reset_group_scoped_data("group-a"))
        self.assertEqual("memory_unavailable", failed["code"])
        self.assertIn("group-a", host.data["groups"])
        self.assertIn("group-a", host.group_whitelist_ids)
        sagas = host.data["_req041_group_reset_sagas"]
        self.assertEqual(1, len(sagas))
        operation_id = next(iter(sagas))

        restarted = _Host(host.data, host.config)
        resumed = asyncio.run(restarted._req041_resume_confirmed_group_resets())
        self.assertTrue(resumed["ok"])
        self.assertEqual(1, resumed["completed"])
        self.assertNotIn("group-a", restarted.data["groups"])
        self.assertIn("group-b", restarted.data["groups"])
        self.assertNotIn("group-a", restarted.group_whitelist_ids)
        self.assertNotIn("_req041_group_reset_sagas", restarted.data)
        self.assertEqual(operation_id, restarted.req041_scoped_projection_sync.calls[0][1])

    def test_config_failure_keeps_config_pending_saga_and_same_operation_is_replayed(self) -> None:
        host = _Host()
        host.config_save_ok = False
        result = asyncio.run(host.reset_group_scoped_data("group-a"))
        self.assertEqual("group_reset_config_save_failed", result["code"])
        self.assertNotIn("group-a", host.data["groups"])
        saga = next(iter(host.data["_req041_group_reset_sagas"].values()))
        self.assertEqual("config_pending", saga["state"])
        operation_id = saga["operation_id"]

        host.config_save_ok = True
        resumed = asyncio.run(host._req041_resume_confirmed_group_resets())
        self.assertTrue(resumed["ok"])
        self.assertNotIn("_req041_group_reset_sagas", host.data)
        self.assertEqual([operation_id, operation_id], [call[1] for call in host.req041_scoped_projection_sync.calls])

    def test_unmigrated_install_returns_not_required_without_local_mutation(self) -> None:
        host = _Host()
        host.req041_scoped_projection_sync = None
        host.req041_migration_status = {"required": False}
        result = asyncio.run(host.reset_group_scoped_data("group-a"))
        self.assertTrue(result["ok"])
        self.assertEqual("not_required", result["state"])
        self.assertIn("group-a", host.data["groups"])

    def test_fresh_runtime_without_memory_bridge_allows_local_group_delete(self) -> None:
        host = _Host()
        host.req041_scoped_projection_sync = None
        host.req041_migration_status = {
            "required": False,
            "scoped_required": True,
            "state": "degraded",
            "code": "memory_bridge_unavailable",
        }
        host.req041_migration_coordinator = types.SimpleNamespace(
            status=lambda: {"source_schema_version": "req041-fresh-v1"}
        )

        result = asyncio.run(host.reset_group_scoped_data("group-a"))

        self.assertTrue(result["ok"])
        self.assertEqual("not_required", result["state"])
        self.assertIn("group-a", host.data["groups"])

    def test_existing_runtime_without_memory_bridge_allows_local_group_delete(self) -> None:
        host = _Host()
        host.req041_scoped_projection_sync = None
        host.req041_migration_status = {
            "required": True,
            "state": "degraded",
            "code": "memory_bridge_unavailable",
        }
        host.req041_migration_coordinator = types.SimpleNamespace(
            status=lambda: {"source_schema_version": "companion-v6", "memory_version": "not-detected"}
        )

        result = asyncio.run(host.reset_group_scoped_data("group-a"))

        self.assertTrue(result["ok"])
        self.assertEqual("not_required", result["state"])
        self.assertIn("group-a", host.data["groups"])

    def test_fresh_runtime_that_had_memory_bound_still_fails_closed(self) -> None:
        host = _Host()
        host.req041_scoped_projection_sync = None
        host.req041_migration_status = {
            "required": False,
            "scoped_required": True,
            "state": "degraded",
            "code": "memory_bridge_unavailable",
        }
        host.req041_migration_coordinator = types.SimpleNamespace(
            status=lambda: {"source_schema_version": "req041-fresh-v1"}
        )
        host.data["_req041_memory_scope_state"] = {"ever_bound": True}

        result = asyncio.run(host.reset_group_scoped_data("group-a"))

        self.assertFalse(result["ok"])
        self.assertEqual("scoped_group_erase_unavailable", result["code"])
        self.assertIn("group-a", host.data["groups"])

    def test_group_reset_waits_for_scoped_startup_binding(self) -> None:
        async def run() -> tuple[dict[str, Any], _Host]:
            host = _Host()
            host.req041_scoped_projection_sync = None
            host.req041_migration_status = {"required": True, "state": "initializing"}

            async def finish_startup() -> None:
                await asyncio.sleep(0)
                host.req041_scoped_projection_sync = _Remote()

            task = asyncio.create_task(finish_startup())
            host._startup_background_tasks = {"req041_automatic_migration": task}
            return await host.reset_group_scoped_data("group-a"), host

        result, host = asyncio.run(run())
        self.assertTrue(result["ok"])
        self.assertEqual("completed", result["state"])
        self.assertNotIn("group-a", host.data["groups"])
        self.assertEqual(1, len(host.req041_scoped_projection_sync.calls))


class _PageApi:
    delete_group = PAGE_DELETE_GROUP

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin

    @staticmethod
    def _normalize_page_group_id(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _ok(data: dict[str, Any]) -> dict[str, Any]:
        return {"success": True, "data": data}

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        return {"success": False, "error": message}


class _PagePlugin:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls: list[str] = []

    async def reset_group_scoped_data(self, group_id: str) -> dict[str, Any]:
        self.calls.append(group_id)
        return deepcopy(self.result)


class GroupResetPageTests(unittest.TestCase):
    def test_page_returns_only_after_saga_completed(self) -> None:
        plugin = _PagePlugin({
            "ok": True, "state": "completed", "operation_id": "group-reset-1",
            "removed_group": True, "removed_whitelist": True,
            "removed_blacklist": False, "removed_expression_scope": True,
            "config_saved": True,
            "scoped_cleanup": {"ok": True, "code": "group_scopes_erased", "count": 3},
        })
        PAGE_REQUEST.payload = {"group_id": "group-a"}
        result = asyncio.run(_PageApi(plugin).delete_group())
        self.assertTrue(result["success"])
        self.assertEqual(["group-a"], plugin.calls)
        self.assertEqual("group-reset-1", result["data"]["operation_id"])
        self.assertEqual(3, result["data"]["scoped_cleanup"]["count"])

    def test_page_does_not_claim_success_when_remote_reset_failed(self) -> None:
        plugin = _PagePlugin({"ok": False, "state": "confirmed", "code": "memory_unavailable"})
        PAGE_REQUEST.payload = {"group_id": "group-a"}
        result = asyncio.run(_PageApi(plugin).delete_group())
        self.assertFalse(result["success"])
        self.assertEqual("memory_unavailable", result["error"])
        self.assertEqual(["group-a"], plugin.calls)


if __name__ == "__main__":
    unittest.main()
