from __future__ import annotations

import ast
import asyncio
import copy
from copy import deepcopy
from pathlib import Path
import tempfile
from typing import Any
import unittest
import uuid

from identity_namespace import NamespaceContext
from migration_scoped_projection import scoped_persona_ref
from astrbot_plugin_private_companion.persona_config import runtime_persona_setting


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
    namespace = {
        "Any": Any,
        "NamespaceContext": NamespaceContext,
        "Path": Path,
        "deepcopy": deepcopy,
        "scoped_persona_ref": scoped_persona_ref,
        "uuid": uuid,
        "_now_ts": lambda: 100.0,
        "_single_line": lambda value, limit=240: " ".join(str(value or "").split())[:limit],
        "runtime_persona_setting": runtime_persona_setting,
    }
    exec(compile(module, str(ROOT / "main.py"), "exec"), namespace)
    return {name: namespace[name] for name in names}


METHODS = _load_methods(
    "_reset_current_persona_store",
    "_req041_erase_scoped_persona_data",
    "_req041_persist_archive_saga_locked",
    "_req041_resume_confirmed_persona_resets",
)


class _Remote:
    policy_version = "req041-v1"
    migration_epoch = "epoch-1"

    def __init__(self) -> None:
        self.ok = True
        self.calls: list[tuple[str, str]] = []

    def erase_persona_scopes(
        self, context: NamespaceContext, *, operation_id: str, reason_code: str
    ) -> dict[str, Any]:
        self.calls.append((context.persona_id, operation_id))
        if not self.ok:
            return {"ok": False, "state": "degraded", "code": "memory_unavailable"}
        return {
            "ok": True, "state": "ready", "code": "persona_scopes_erased",
            "count": 4, "namespace_count": 4, "reason_code": reason_code,
        }


class _Host:
    _reset_current_persona_store = METHODS["_reset_current_persona_store"]
    _req041_erase_scoped_persona_data = METHODS["_req041_erase_scoped_persona_data"]
    _req041_persist_archive_saga_locked = METHODS["_req041_persist_archive_saga_locked"]
    _req041_resume_confirmed_persona_resets = METHODS["_req041_resume_confirmed_persona_resets"]

    def __init__(self, root: str, data: dict[str, Any] | None = None) -> None:
        self.root = root
        self._data_default = deepcopy(data) if isinstance(data, dict) else {
            "users": {"person-a": {"secret": "old-profile"}},
            "groups": {"group-a": {"recent_messages": [{"text": "old-group"}]}},
            "persona_lifecycle": {"generation": 2},
        }
        self._persona_data_profiles: dict[str, dict[str, Any]] = {}
        self._data_lock = asyncio.Lock()
        self.enable_multi_persona_mode = False
        self.default_enable_configured_targets = False
        self._persona_overrides: dict[str, Any] = {}
        self.sync_calls = 0
        self.req041_migration_status = {"required": True}
        self.req041_scoped_projection_sync = _Remote()
        self.persisted = deepcopy(self._data_default)
        self.fail_replacement_once = False
        self._persona_window_claims: dict[str, str] = {}
        self._persona_window_conflicts: dict[str, Any] = {}
        self._bookshelf_access_tokens: dict[str, Any] = {}

    @property
    def data(self) -> dict[str, Any]:
        return self._data_default

    @data.setter
    def data(self, value: dict[str, Any]) -> None:
        self._data_default = value

    @staticmethod
    def _sanitize_persona_id(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _active_persona_scope() -> str:
        return ""

    @staticmethod
    def _configured_multi_persona_ids() -> list[str]:
        return []

    @staticmethod
    async def _flush_scheduled_data_save() -> None:
        return None

    def _save_data_now_sync(self, **_kwargs: Any) -> None:
        self.persisted = deepcopy(self.data)

    def _write_persona_reset_backup_sync(self, persona_id: str, snapshot: dict[str, Any]) -> Path:
        del persona_id
        path = Path(self.root) / f"backup-{uuid.uuid4().hex}.json"
        path.write_text(str(snapshot), encoding="utf-8")
        return path

    @staticmethod
    def _new_store() -> dict[str, Any]:
        return {"users": {}, "groups": {}, "persona_lifecycle": {"generation": 1}}

    @staticmethod
    def _ensure_store_defaults(data: dict[str, Any]) -> dict[str, Any]:
        data.setdefault("users", {})
        data.setdefault("groups", {})
        return data

    def persona_setting(self, key: str, default: Any = None) -> Any:
        return self._persona_overrides.get(key, getattr(self, key, default))

    def _sync_configured_targets(self) -> None:
        self.sync_calls += 1

    def _write_data_snapshot_sync(self, snapshot: dict[str, Any]) -> None:
        if self.fail_replacement_once and "_req041_persona_reset_saga" not in snapshot:
            self.fail_replacement_once = False
            raise OSError("replacement_write_failed")
        self.persisted = deepcopy(snapshot)

    @staticmethod
    def _reset_persona_prompt_caches(*_args: Any) -> None:
        return None

    @staticmethod
    def _persona_window_bindings() -> dict[str, str]:
        return {}

    @staticmethod
    def _clear_persona_window_runtime_cache(_window: str) -> None:
        return None

    @staticmethod
    def _deactivate_persona_for_event(_token: Any) -> None:
        return None


class PersonaResetSagaTests(unittest.TestCase):
    def test_reset_uses_active_persona_target_sync_setting(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            host = _Host(root)
            host._persona_overrides["default_enable_configured_targets"] = True

            result = asyncio.run(host._reset_current_persona_store(rebuild_today=False))

            self.assertTrue(result["ok"])
            self.assertEqual(1, host.sync_calls)
            self.assertFalse(host.default_enable_configured_targets)

    def test_remote_failure_preserves_local_profile_and_restart_resumes_same_operation(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            host = _Host(root)
            host.req041_scoped_projection_sync.ok = False
            failed = asyncio.run(host._reset_current_persona_store(rebuild_today=False))
            self.assertFalse(failed["ok"])
            self.assertEqual("memory_unavailable", failed["code"])
            self.assertEqual("old-profile", host.data["users"]["person-a"]["secret"])
            marker = host.data["_req041_persona_reset_saga"]
            operation_id = marker["operation_id"]

            restarted = _Host(root, host.persisted)
            resumed = asyncio.run(restarted._req041_resume_confirmed_persona_resets())
            self.assertTrue(resumed["ok"])
            self.assertEqual(1, resumed["completed"])
            self.assertEqual({}, restarted.data["users"])
            self.assertNotIn("_req041_persona_reset_saga", restarted.data)
            self.assertEqual(operation_id, restarted.req041_scoped_projection_sync.calls[0][1])

    def test_local_replacement_failure_keeps_confirmed_marker_for_retry(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            host = _Host(root)
            host.fail_replacement_once = True
            with self.assertRaisesRegex(OSError, "replacement_write_failed"):
                asyncio.run(host._reset_current_persona_store(rebuild_today=False))
            self.assertEqual("old-profile", host.data["users"]["person-a"]["secret"])
            self.assertEqual("confirmed", host.data["_req041_persona_reset_saga"]["state"])
            resumed = asyncio.run(host._req041_resume_confirmed_persona_resets())
            self.assertTrue(resumed["ok"])
            self.assertEqual({}, host.data["users"])
            operation_ids = [call[1] for call in host.req041_scoped_projection_sync.calls]
            self.assertEqual([operation_ids[0], operation_ids[0]], operation_ids)


if __name__ == "__main__":
    unittest.main()
