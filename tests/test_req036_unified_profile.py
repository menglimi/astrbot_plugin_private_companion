from __future__ import annotations

import ast
import asyncio
import copy
import hashlib
import re
import sys
import time
import types
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "req036_companion"
if PACKAGE not in sys.modules:
    module = types.ModuleType(PACKAGE)
    module.__path__ = [str(ROOT)]
    sys.modules[PACKAGE] = module

from req036_companion.unified_person_registry import UnifiedPersonRegistry
from req036_companion.unified_profile_contract import (
    CONTRACT_FINGERPRINT,
    build_person_ref,
    build_portrait_request,
    build_profile_dto,
    validate_person_ref,
    validate_portrait_request,
    validate_profile_dto,
)
from req036_companion.unified_profile_service import (
    DEFAULT_CLOSED_REPAIR_OPERATION_ID,
    DEFAULT_UNAUTHORIZED_PRIVATE_REPLY,
    MIGRATION_KEY,
    capability_summary,
    default_capabilities,
    default_closed_repair_preview,
    ensure_legacy_profile_capabilities,
    ensure_new_profile_capabilities,
    migrate_legacy_capabilities,
    private_companion_gate,
    proactive_private_gate,
    repair_default_closed_capabilities,
    rollback_legacy_capabilities,
    update_capabilities,
)


def _identity(subject_id: str) -> dict[str, str]:
    return {
        "companion_instance_id": "astrbot_plugin_private_companion",
        "bot_account_id": "onebot:bot-1",
        "adapter_instance_id": "onebot:default",
        "subject_namespace": "onebot:user",
        "platform_subject_id": subject_id,
    }


def _person_projection(seed: str = "a") -> dict[str, Any]:
    return {
        "person_id": "person_" + seed * 24,
        "resolved_identity_key": "chat-origin-v1:" + seed * 64,
        "projection_revision": 1,
        "identity_assurance": "observed",
        "profile_status": "active",
    }


def _load_method(name: str) -> Any:
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PrivateCompanionPlugin")
    method = next(node for node in owner.body if isinstance(node, ast.AsyncFunctionDef) and node.name == name)
    method = copy.deepcopy(method)
    method.decorator_list = []
    module = ast.Module(body=[method], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace: dict[str, Any] = {
        "Any": Any,
        "AstrMessageEvent": Any,
        "ProviderRequest": Any,
        "DEFAULT_UNAUTHORIZED_PRIVATE_REPLY": DEFAULT_UNAUTHORIZED_PRIVATE_REPLY,
        "logger": types.SimpleNamespace(
            debug=lambda *_args, **_kwargs: None,
            info=lambda *_args, **_kwargs: None,
            warning=lambda *_args, **_kwargs: None,
        ),
        "time": time,
        "_now_ts": lambda: 100.0,
        "_safe_float": lambda value, default=0.0: float(value or default),
        "_single_line": lambda value, limit=240: str(value or "").strip()[:limit],
        "runtime_persona_setting": lambda host, key, default=None: getattr(host, key, default),
    }
    exec(compile(module, str(ROOT / "main.py"), "exec"), namespace)
    return namespace[name]


REQ036_LLM_GATE = _load_method("guard_req036_private_capability_before_llm")
REQ036_EARLY_GATE = _load_method("guard_req036_private_capability_early")
REQ036_GROUP_GATE = _load_method("guard_req036_group_portrait_queries")
REQ036_COMMAND_GATE = _load_method("companion_command")
REQ036_REJECT = _load_method("_req036_reject_unauthorized_private_event")


def _load_async_function(path: Path, name: str) -> Any:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == name)
    function = copy.deepcopy(function)
    function.decorator_list = []
    module = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace: dict[str, Any] = {
        "Any": Any,
        "logger": types.SimpleNamespace(
            debug=lambda *_args, **_kwargs: None,
            info=lambda *_args, **_kwargs: None,
            warning=lambda *_args, **_kwargs: None,
        ),
        "_now_ts": lambda: 100.0,
        "_single_line": lambda value, limit=240: " ".join(str(value or "").split())[:limit],
    }
    exec(compile(module, str(path), "exec"), namespace)
    return namespace[name]


REQ036_PRIVATE_HANDLER = _load_async_function(ROOT / "message_pipeline.py", "handle_private_message")


def _load_event_dispatch_method(name: str) -> Any:
    source = (ROOT / "event_dispatch.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "EventDispatchMixin")
    method = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == name)
    method = copy.deepcopy(method)
    module = ast.Module(body=[method], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace: dict[str, Any] = {
        "Any": Any,
        "AstrMessageEvent": Any,
        "Mapping": Mapping,
        "logger": types.SimpleNamespace(
            debug=lambda *_args, **_kwargs: None,
            info=lambda *_args, **_kwargs: None,
            warning=lambda *_args, **_kwargs: None,
        ),
        "re": re,
        "time": time,
        "_now_ts": lambda: 100.0,
        "_safe_float": lambda value, default=0.0: float(value or default),
        "_single_line": lambda value, limit=240: str(value or "").strip()[:limit],
    }
    exec(compile(module, str(ROOT / "event_dispatch.py"), "exec"), namespace)
    return namespace[name]


EVENT_RAW_PAYLOAD = _load_event_dispatch_method("_event_raw_payload")
EVENT_REQ036_VISIBLE_TEXT = _load_event_dispatch_method("_event_req036_visible_text")
EVENT_REQ036_SCOPE = _load_event_dispatch_method("_event_req036_scope")
EVENT_REQ036_HAS_MEDIA = _load_event_dispatch_method("_event_req036_has_media")
EVENT_REQ036_COMPONENT_SHAPE = _load_event_dispatch_method("_event_req036_component_shape")
EVENT_REMEMBER_REQ036_DENIAL_ECHO = _load_event_dispatch_method("_remember_req036_denial_echo")
EVENT_FORGET_REQ036_DENIAL_ECHO = _load_event_dispatch_method("_forget_req036_denial_echo")
EVENT_IS_RECENT_REQ036_DENIAL_ECHO = _load_event_dispatch_method("_event_is_recent_req036_denial_echo")
EVENT_IS_INBOUND_CHAT_MESSAGE = _load_event_dispatch_method("_event_is_inbound_chat_message")


def _method_priority(name: str) -> int:
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PrivateCompanionPlugin")
    method = next(
        node
        for node in owner.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name
    )
    for decorator in method.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        for keyword in decorator.keywords:
            if keyword.arg != "priority":
                continue
            if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, int):
                return keyword.value.value
            if (
                isinstance(keyword.value, ast.UnaryOp)
                and isinstance(keyword.value.op, ast.USub)
                and isinstance(keyword.value.operand, ast.Constant)
                and isinstance(keyword.value.operand.value, int)
            ):
                return -keyword.value.operand.value
    raise AssertionError(f"{name} has no literal priority")


def _load_sync_plugin_method(name: str) -> Any:
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PrivateCompanionPlugin")
    method = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == name)
    method = copy.deepcopy(method)
    module = ast.Module(body=[method], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace: dict[str, Any] = {
        "Any": Any,
        "UnifiedPersonRegistry": UnifiedPersonRegistry,
        "_single_line": lambda value, limit=120: str(value or "").strip()[:limit],
        "_safe_int": lambda value, default=0, minimum=0: max(minimum, int(value or default)),
        "req036_update_capabilities": update_capabilities,
    }
    exec(compile(module, str(ROOT / "main.py"), "exec"), namespace)
    return namespace[name]


REQ036_CONFIGURED_TARGET_MIGRATION = _load_sync_plugin_method("_req036_migrate_configured_target_capability")
REQ036_ACTIVE_REGISTRY = _load_sync_plugin_method("_active_unified_person_registry")


def _load_proactive_target_sync() -> Any:
    source = (ROOT / "proactive.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ProactiveMixin")
    method = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == "_sync_configured_targets")
    method = copy.deepcopy(method)
    module = ast.Module(body=[method], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace: dict[str, Any] = {
        "req036_capability_summary": capability_summary,
        "req036_update_capabilities": update_capabilities,
        "_safe_int": lambda value, default=0, minimum=0: max(minimum, int(value or default)),
        "_safe_float": lambda value, default=0.0: float(value or default),
        "_proactive_setting_value": lambda host, key, default=None: getattr(host, key, default),
        "_now_ts": lambda: 100.0,
    }
    exec(compile(module, str(ROOT / "proactive.py"), "exec"), namespace)
    return namespace["_sync_configured_targets"]


REQ036_CONFIGURED_TARGET_SYNC = _load_proactive_target_sync()


def _load_user_list_method() -> Any:
    source = (ROOT / "page_api_users_groups.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    owner = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "PrivateCompanionPageApiUsersGroupsMixin"
    )
    method = next(node for node in owner.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "list_users")
    module = ast.Module(body=[copy.deepcopy(method)], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace: dict[str, Any] = {
        "Any": Any,
        "logger": types.SimpleNamespace(
            warning=lambda *_args, **_kwargs: None,
            error=lambda *_args, **_kwargs: None,
        ),
        "time": time,
    }
    exec(compile(module, str(ROOT / "page_api_users_groups.py"), "exec"), namespace)
    return namespace["list_users"]


REQ036_USER_LIST = _load_user_list_method()


class _PrivateEvent:
    def __init__(
        self,
        message_str: str = "",
        *,
        raw_message: dict[str, Any] | None = None,
        message_id: str = "",
    ) -> None:
        self.stopped = False
        self.message_str = message_str
        raw = dict(raw_message or {})
        if message_id and "message_id" not in raw:
            raw["message_id"] = message_id
        self.message_obj = types.SimpleNamespace(raw_message=raw, message_id=message_id)

    @staticmethod
    def is_private_chat() -> bool:
        return True

    @staticmethod
    def get_sender_id() -> str:
        return "u-1"

    def stop_event(self) -> None:
        self.stopped = True


class _EventIngressHost:
    _event_raw_payload = EVENT_RAW_PAYLOAD
    _event_req036_visible_text = EVENT_REQ036_VISIBLE_TEXT
    _event_req036_scope = EVENT_REQ036_SCOPE
    _event_req036_has_media = EVENT_REQ036_HAS_MEDIA
    _event_req036_component_shape = EVENT_REQ036_COMPONENT_SHAPE
    _remember_req036_denial_echo = EVENT_REMEMBER_REQ036_DENIAL_ECHO
    _forget_req036_denial_echo = EVENT_FORGET_REQ036_DENIAL_ECHO
    _event_is_recent_req036_denial_echo = EVENT_IS_RECENT_REQ036_DENIAL_ECHO
    _event_is_inbound_chat_message = EVENT_IS_INBOUND_CHAT_MESSAGE

    @staticmethod
    def _event_scope_key(_event: Any) -> str:
        return "private:u-1"

    @staticmethod
    def _confirm_req036_denial_echo(entry: Any) -> None:
        if isinstance(entry, dict):
            entry["state"] = "sent"


class _GateHost(_EventIngressHost):
    def __init__(self) -> None:
        self.data = {"users": {"u-1": {"unified_profile_capabilities": default_capabilities()}}}
        self.replies: list[str] = []
        self.memory_calls = 0
        self.tool_calls = 0
        self.relationship_calls = 0

    @staticmethod
    def _canonical_private_user_id(value: str) -> str:
        return value

    @staticmethod
    def _req036_private_gate_for_user(user: Any) -> dict[str, Any]:
        return private_companion_gate(user, "固定拒绝文本")

    async def _req036_reject_unauthorized_private_event(self, event: Any, gate: dict[str, Any]) -> None:
        self.replies.append(str(gate["reply"]))
        event.private_companion_req036_denied = True
        event.stop_event()


class _CommandEvent(_PrivateEvent):
    message_str = "陪伴 状态"


class _CommandGateHost(_EventIngressHost):
    def __init__(self) -> None:
        self._data_lock = asyncio.Lock()
        self.user = {"unified_profile_capabilities": default_capabilities()}
        self.replies: list[str] = []
        self.qzone_calls = 0
        self.attached_sources: list[str] = []

    @staticmethod
    def _sender_display_name(_event: Any) -> str:
        return "测试用户"

    def _ensure_auto_private_user_profile(self, _event: Any, **_kwargs: Any) -> tuple[dict[str, Any], bool]:
        return self.user, False

    def _req036_attach_unified_profile_context(self, _event: Any, **kwargs: Any) -> dict[str, Any]:
        self.attached_sources.append(str(kwargs.get("source") or ""))
        return {"state": "profile_exact"}

    @staticmethod
    def _schedule_data_save(*_args, **_kwargs) -> None:
        return None

    @staticmethod
    def _req036_private_gate_for_user(user: Any) -> dict[str, Any]:
        return private_companion_gate(user, "固定拒绝文本")

    async def _req036_reject_unauthorized_private_event(self, event: Any, gate: dict[str, Any]) -> None:
        self.replies.append(str(gate["reply"]))
        event.private_companion_req036_denied = True
        event.stop_event()

    def _qzone_note_event_bot(self, _event: Any) -> None:
        self.qzone_calls += 1


class _EarlyGateHost(_EventIngressHost):
    def __init__(self) -> None:
        self._data_lock = asyncio.Lock()
        self.data: dict[str, Any] = {"users": {}}
        self.replies: list[str] = []
        self.profile_checks = 0

    @staticmethod
    def _event_self_id(_event: Any) -> str:
        return "bot-1"

    @staticmethod
    def _sender_display_name(_event: Any) -> str:
        return "陌生用户"

    def _ensure_auto_private_user_profile(self, _event: Any, **_kwargs: Any) -> tuple[None, bool]:
        self.profile_checks += 1
        return None, False

    @staticmethod
    def _req036_migrate_configured_target_capability(_user_id: str, _user: Any) -> bool:
        return False

    @staticmethod
    def _req036_private_gate_for_user(user: Any) -> dict[str, Any]:
        return private_companion_gate(user, "固定拒绝文本")

    async def _req036_reject_unauthorized_private_event(self, event: Any, gate: dict[str, Any]) -> None:
        self.replies.append(str(gate["reply"]))
        event.private_companion_req036_denied = True
        event.stop_event()


class _PrivatePipelineGateHost(_CommandGateHost):
    def __init__(self) -> None:
        super().__init__()
        self.data: dict[str, Any] = {"users": {}}

    @staticmethod
    def _event_self_id(_event: Any) -> str:
        return "bot-1"


class _RejectRaisesHost:
    @staticmethod
    async def _reply(_event: Any, _text: str) -> None:
        raise RuntimeError("transport unavailable")


class _RejectRecorderHost(_EventIngressHost):
    def __init__(self, *, fail_first: bool = False) -> None:
        self.replies: list[str] = []
        self.attempts = 0
        self.fail_first = fail_first
        self._req036_recent_denial_message_ids: dict[str, float] = {}

    @staticmethod
    def _event_message_id(event: Any) -> str:
        return str(getattr(getattr(event, "message_obj", None), "message_id", "") or "")

    @staticmethod
    def _platform_kind_for_event(_event: Any) -> str:
        return "onebot"

    async def _reply(self, _event: Any, text: str) -> None:
        self.attempts += 1
        if self.fail_first and self.attempts == 1:
            raise RuntimeError("transport unavailable")
        self.replies.append(text)


class _RejectSilentCancelHost(_RejectRecorderHost):
    async def _reply(self, _event: Any, text: str) -> bool:
        self.attempts += 1
        if self.attempts == 1:
            return False
        self.replies.append(text)
        return True


class _GroupEvent:
    def __init__(self, text: str, *, directed: bool = False) -> None:
        self.message_str = text
        self.stopped = False
        self.is_at_or_wake_command = directed

    @staticmethod
    def get_sender_id() -> str:
        return "u-1"

    def stop_event(self) -> None:
        self.stopped = True


class _GroupGateHost:
    replies: list[str]

    def __init__(self) -> None:
        self.replies = []
        self.enable_group_third_party_portrait_guard = True

    @staticmethod
    def _extract_group_id_from_event(event: Any) -> str:
        return "group-disabled"

    @staticmethod
    def _group_observation_event_text(event: Any) -> str:
        return str(event.message_str)

    @staticmethod
    def _req036_group_portrait_query_kind(text: Any) -> str:
        return "third_party"

    @staticmethod
    def _req036_group_portrait_query_is_directed(event: Any) -> bool:
        return bool(getattr(event, "is_at_or_wake_command", False))

    async def _reply(self, event: Any, text: str) -> None:
        self.replies.append(text)


class _GroupGateReplyRaisesHost(_GroupGateHost):
    @staticmethod
    async def _reply(_event: Any, _text: str) -> None:
        raise RuntimeError("transport unavailable")


class _PersonaRegistryHost:
    def __init__(self) -> None:
        self.primary_store: dict[str, Any] = {}
        self.secondary_store: dict[str, Any] = {}
        self.data = self.primary_store
        self.unified_person_registry = UnifiedPersonRegistry(self.primary_store)


class _ConfiguredTargetHost:
    def __init__(self) -> None:
        self.default_nickname = "你"
        self.user: dict[str, Any] = {"proactive_daily_limit": 0}
        self.delivery_bound = 0

    @staticmethod
    def _configured_target_ids() -> list[str]:
        return ["legacy-target"]

    def _get_user(self, user_id: str) -> dict[str, Any]:
        return self.user

    @staticmethod
    def _clear_pending_proactive_plan(user: dict[str, Any]) -> None:
        user["cleared"] = True

    def _ensure_private_user_umo(self, user_id: str, user: dict[str, Any]) -> None:
        self.delivery_bound += 1

    @staticmethod
    def _user_enabled_for_proactive(user_id: str, user: dict[str, Any]) -> bool:
        return False


class _LegacyTargetMigrationHost:
    target_platform = "aiocqhttp"

    @staticmethod
    def _canonical_private_user_id(value: str) -> str:
        return str(value or "").strip()

    @staticmethod
    def _normalize_platform_kind(value: Any) -> str:
        return {"aiocqhttp": "onebot", "onebot": "onebot", "telegram": "telegram"}.get(
            str(value or "").strip().lower(), "generic"
        )

    @staticmethod
    def _configured_target_ids() -> list[str]:
        return ["legacy-target"]

    @staticmethod
    def _schedule_next_proactive(user: dict[str, Any], *, now: float) -> None:
        raise AssertionError("disabled proactive capability must not schedule")


class _UserListHost:
    def __init__(self) -> None:
        self.plugin = types.SimpleNamespace(
            _data_lock=asyncio.Lock(),
            data={"users": {"source-a": {}, "source-b": {}}},
        )

    @staticmethod
    def _query_int(_name: str, default: int, _minimum: int, _maximum: int) -> int:
        return default

    @staticmethod
    def _single_line(value: Any, limit: int = 80) -> str:
        return str(value or "").strip()[:limit]

    @staticmethod
    def _user_summary(user_id: str, _user: dict[str, Any]) -> dict[str, Any]:
        return {
            "user_id": user_id,
            "unified_person_id": "person_" + "d" * 24,
            "last_seen_ts": 1,
        }

    @staticmethod
    def _ok(payload: dict[str, Any]) -> dict[str, Any]:
        return payload


class Req036CompanionTests(unittest.TestCase):
    def test_default_capabilities_keep_private_open_and_proactive_independent(self) -> None:
        state = default_capabilities()
        self.assertTrue(state["private_companion_enabled"])
        self.assertFalse(state["proactive_private_enabled"])
        self.assertEqual("disabled", state["portrait_mode"])
        state["proactive_private_enabled"] = True
        self.assertTrue(proactive_private_gate({"unified_profile_capabilities": state})["allowed"])

    def test_portrait_capability_fails_closed_without_memory_backend(self) -> None:
        state = default_capabilities()
        state["portrait_mode"] = "learn_and_use"
        state["portrait_mode_override"] = "explicit"
        summary = capability_summary(
            {"unified_profile_capabilities": state},
            portrait_backend_available=False,
        )
        self.assertEqual("disabled", summary["portrait_mode"])
        self.assertFalse(summary["portrait_learning_enabled"])
        self.assertFalse(summary["portrait_usage_enabled"])

    def test_administrator_update_records_only_capability_audit(self) -> None:
        user: dict[str, Any] = {"unified_profile_capabilities": default_capabilities()}
        result = update_capabilities(
            user,
            {"private_companion_enabled": True, "proactive_private_enabled": True, "portrait_mode": "use_existing"},
            actor_authorized=True,
            actor_id="page_administrator",
            target_identity="u-1",
            reason_code="test",
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["capabilities"]["effective_proactive_private_enabled"])
        self.assertEqual("use_existing", result["capabilities"]["portrait_mode"])
        self.assertEqual("page_administrator", user["unified_profile_capability_audit"][-1]["actor_id"])
        self.assertNotIn("content", repr(user["unified_profile_capability_audit"]))

    def test_req039_group_path_uses_transient_projection_without_private_user_write(self) -> None:
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        plugin = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PrivateCompanionPlugin")
        capture = next(node for node in plugin.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "_capture_group_observation_event")
        rendered = ast.unparse(capture)
        self.assertNotIn("_get_user", rendered)
        self.assertNotIn("group_inbound", rendered)
        projection = next(node for node in plugin.body if isinstance(node, ast.FunctionDef) and node.name == "_req039_group_observation_projection")
        projection_rendered = ast.unparse(projection)
        self.assertNotIn("_get_user", projection_rendered)
        self.assertIn("'projection_kind': 'group_observation'", projection_rendered)
        expression = next(node for node in plugin.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "inject_unified_relationship_expression")
        expression_rendered = ast.unparse(expression)
        self.assertIn("group_id = '' if is_private", expression_rendered)
        self.assertIn("_req039_group_observation_projection", expression_rendered)
        self.assertIn("'private_chat': is_private", expression_rendered)

    def test_capability_migration_is_dry_idempotent_and_reversible(self) -> None:
        data: dict[str, Any] = {
            "users": {
                "legacy-on": {"enabled": True, "proactive_daily_limit": 2},
                "legacy-off": {"enabled": False, "proactive_daily_limit": 8},
            }
        }
        before = copy.deepcopy(data)
        preview = migrate_legacy_capabilities(data, operation_id="req036-test", dry_run=True)
        self.assertEqual("migration_dry_run", preview["code"])
        self.assertEqual(before, data)
        applied = migrate_legacy_capabilities(data, operation_id="req036-test", dry_run=False)
        self.assertEqual("migration_applied", applied["code"])
        self.assertTrue(data["users"]["legacy-on"]["unified_profile_capabilities"]["private_companion_enabled"])
        self.assertFalse(data["users"]["legacy-off"]["unified_profile_capabilities"]["proactive_private_enabled"])
        self.assertEqual("migration_idempotent_replay", migrate_legacy_capabilities(data, operation_id="req036-test", dry_run=False)["code"])
        self.assertEqual("migration_rolled_back", rollback_legacy_capabilities(data, operation_id="req036-test")["code"])
        self.assertEqual(before, {"users": data["users"]})

    def test_existing_profile_materializes_legacy_permission_without_default_template_evidence(self) -> None:
        legacy = {
            "enabled": True,
            "proactive_daily_limit": 2,
            "relationship_role": "friend",
        }
        state = ensure_legacy_profile_capabilities(legacy)
        self.assertTrue(state["private_companion_enabled"])
        self.assertTrue(state["proactive_private_enabled"])
        self.assertEqual("legacy_effective_migration", state["grant_source"])

        unknown = {"relationship_role": "friend"}
        unknown_state = ensure_legacy_profile_capabilities(unknown)
        self.assertTrue(unknown_state["private_companion_enabled"])
        self.assertFalse(unknown_state["proactive_private_enabled"])

        late_import = {
            "enabled": False,
            "manual_enabled": True,
            "proactive_daily_limit": 2,
            "unified_profile_capabilities": default_capabilities(),
        }
        imported_state = ensure_legacy_profile_capabilities(late_import)
        self.assertTrue(imported_state["private_companion_enabled"])
        self.assertTrue(imported_state["proactive_private_enabled"])
        self.assertEqual("legacy_default_closed_repair", imported_state["grant_source"])

    def test_existing_owner_legacy_permission_respects_explicit_disable_audit(self) -> None:
        owner = {
            "enabled": False,
            "relationship_role": "owner",
            "unified_profile_capability_audit": [{
                "actor_id": "page_administrator",
                "reason_code": "page_administrator_update",
                "changed": {
                    "private_companion_enabled": {"from": True, "to": False},
                    "proactive_private_enabled": {"from": True, "to": False},
                },
            }],
        }
        state = ensure_legacy_profile_capabilities(owner)
        self.assertTrue(state["private_companion_enabled"])
        self.assertFalse(state["proactive_private_enabled"])

        enabled_state = default_capabilities(grant_source="administrator")
        enabled_state["private_companion_enabled"] = True
        enabled_state["proactive_private_enabled"] = True
        manually_disabled = {
            "manual_disabled": True,
            "unified_profile_capabilities": enabled_state,
        }
        disabled_state = ensure_legacy_profile_capabilities(manually_disabled)
        self.assertTrue(disabled_state["private_companion_enabled"])
        self.assertTrue(disabled_state["proactive_private_enabled"])

    def test_default_closed_compatibility_repair_restores_legacy_evidence_once(self) -> None:
        state = default_capabilities()
        state["portrait_mode"] = "use_existing"
        state["portrait_mode_override"] = "explicit"
        state.pop("grant_source")
        data: dict[str, Any] = {
            "users": {
                "legacy-enabled": {
                    "enabled": True,
                    "manual_enabled": True,
                    "private_companion_enabled": False,
                    "proactive_private_enabled": False,
                    "proactive_daily_limit": 2,
                    "unified_profile_capabilities": state,
                }
            }
        }
        before = copy.deepcopy(data["users"])
        preview = default_closed_repair_preview(data, operation_id=DEFAULT_CLOSED_REPAIR_OPERATION_ID)
        self.assertEqual("default_closed_repair_dry_run", preview["code"])
        self.assertEqual(1, preview["count"])
        self.assertEqual(before, data["users"])

        applied = repair_default_closed_capabilities(
            data,
            operation_id=DEFAULT_CLOSED_REPAIR_OPERATION_ID,
            dry_run=False,
        )
        self.assertEqual("default_closed_repair_applied", applied["code"])
        self.assertEqual(1, applied["count"])
        repaired = data["users"]["legacy-enabled"]["unified_profile_capabilities"]
        self.assertTrue(repaired["private_companion_enabled"])
        self.assertTrue(repaired["proactive_private_enabled"])
        self.assertEqual("legacy_default_closed_repair", repaired["grant_source"])
        self.assertEqual("use_existing", repaired["portrait_mode"])
        self.assertEqual("explicit", repaired["portrait_mode_override"])
        self.assertEqual(
            "migration_idempotent_replay",
            repair_default_closed_capabilities(
                data,
                operation_id=DEFAULT_CLOSED_REPAIR_OPERATION_ID,
                dry_run=False,
            )["code"],
        )

        self.assertEqual(
            "migration_rolled_back",
            rollback_legacy_capabilities(data, operation_id=DEFAULT_CLOSED_REPAIR_OPERATION_ID)["code"],
        )
        self.assertEqual(before, data["users"])

    def test_default_closed_repair_does_not_open_new_or_explicitly_disabled_profiles(self) -> None:
        explicit_audit = [{
            "actor_id": "page_administrator",
            "reason_code": "page_administrator_update",
            "changed": {"private_companion_enabled": {"from": True, "to": False}},
        }]
        data: dict[str, Any] = {
            "users": {
                "manual-disabled": {
                    "enabled": True,
                    "manual_disabled": True,
                    "proactive_daily_limit": 3,
                    "unified_profile_capabilities": default_capabilities(),
                },
                "administrator-disabled": {
                    "enabled": True,
                    "proactive_daily_limit": 3,
                    "unified_profile_capability_audit": explicit_audit,
                    "unified_profile_capabilities": default_capabilities(),
                },
                "automatic-new-profile": {
                    "enabled": True,
                    "auto_profile_created": True,
                    "profile_origin": "private_auto",
                    "proactive_daily_limit": 3,
                    "unified_profile_capabilities": default_capabilities(),
                },
                "no-private-evidence": {
                    "enabled": False,
                    "proactive_daily_limit": 3,
                    "unified_profile_capabilities": default_capabilities(),
                },
            }
        }
        before = copy.deepcopy(data["users"])
        result = repair_default_closed_capabilities(
            data,
            operation_id="req036-default-closed-test-no-open",
            dry_run=False,
        )
        self.assertEqual("default_closed_repair_applied", result["code"])
        self.assertEqual(0, result["count"])
        self.assertEqual(before, data["users"])

    def test_default_closed_repair_uses_exact_legacy_snapshot_when_aliases_were_overwritten(self) -> None:
        data: dict[str, Any] = {
            "users": {
                "legacy-shadow": {
                    "enabled": False,
                    "private_companion_enabled": False,
                    "proactive_private_enabled": False,
                    "proactive_daily_limit": 2,
                    "unified_profile_capabilities": default_capabilities(),
                }
            },
            MIGRATION_KEY: {
                "operations": {
                    "req036-capability-v1": {
                        "count": 1,
                        "snapshots": {
                            "legacy-shadow": {
                                "unified_profile_capabilities": None,
                                "private_companion_enabled": None,
                                "proactive_private_enabled": None,
                                "enabled": True,
                            }
                        },
                    }
                }
            },
        }
        result = repair_default_closed_capabilities(
            data,
            operation_id="req036-default-closed-snapshot-test",
            dry_run=False,
        )
        self.assertEqual(1, result["count"])
        state = data["users"]["legacy-shadow"]["unified_profile_capabilities"]
        self.assertTrue(state["private_companion_enabled"])
        self.assertTrue(state["proactive_private_enabled"])

    def test_portrait_only_update_preserves_permission_provenance(self) -> None:
        user = {
            "unified_profile_capabilities": default_capabilities(
                grant_source="legacy_default_closed_repair"
            )
        }
        before_updated_at = user["unified_profile_capabilities"]["updated_at"]
        result = update_capabilities(
            user,
            {"portrait_mode": "use_existing"},
            actor_authorized=True,
            grant_source="administrator",
        )
        self.assertTrue(result["ok"])
        state = user["unified_profile_capabilities"]
        self.assertEqual("legacy_default_closed_repair", state["grant_source"])
        self.assertEqual("use_existing", state["portrait_mode"])
        self.assertEqual(before_updated_at, state["updated_at"])

    def test_capability_migration_fails_closed_for_manual_disable_and_infinite_limits(self) -> None:
        data: dict[str, Any] = {
            "users": {
                "positive-infinity": {"enabled": True, "proactive_daily_limit": float("inf")},
                "negative-infinity": {"enabled": True, "proactive_daily_limit": float("-inf")},
                "manual-disabled": {
                    "enabled": True,
                    "manual_disabled": True,
                    "proactive_daily_limit": 3,
                },
            }
        }

        preview = migrate_legacy_capabilities(data, operation_id="non-finite-limits", dry_run=True)
        self.assertEqual("migration_dry_run", preview["code"])
        by_user = {item["user_id"]: item for item in preview["planned"]}
        self.assertFalse(by_user["positive-infinity"]["proactive_private_enabled"])
        self.assertFalse(by_user["negative-infinity"]["proactive_private_enabled"])
        self.assertTrue(by_user["manual-disabled"]["private_companion_enabled"])
        self.assertFalse(by_user["manual-disabled"]["proactive_private_enabled"])

        applied = migrate_legacy_capabilities(data, operation_id="non-finite-limits", dry_run=False)
        self.assertEqual("migration_applied", applied["code"])
        for user_id in ("positive-infinity", "negative-infinity"):
            capabilities = data["users"][user_id]["unified_profile_capabilities"]
            self.assertTrue(capabilities["private_companion_enabled"])
            self.assertFalse(capabilities["proactive_private_enabled"])
        disabled = data["users"]["manual-disabled"]["unified_profile_capabilities"]
        self.assertTrue(disabled["private_companion_enabled"])
        self.assertFalse(disabled["proactive_private_enabled"])

    def test_capability_migration_repairs_damaged_containers_and_schema(self) -> None:
        damaged_roots = (
            "broken",
            {"operations": []},
            {"operations": None},
        )
        for index, damaged_root in enumerate(damaged_roots):
            with self.subTest(damaged_root=damaged_root):
                operation_id = f"repair-{index}"
                data: dict[str, Any] = {
                    "users": {
                        "legacy": {
                            "enabled": True,
                            "unified_profile_capabilities": {"schema_version": "broken"},
                        }
                    },
                    MIGRATION_KEY: copy.deepcopy(damaged_root),
                }
                applied = migrate_legacy_capabilities(data, operation_id=operation_id, dry_run=False)
                self.assertEqual("migration_applied", applied["code"])
                self.assertEqual(1, applied["count"])
                migration = data[MIGRATION_KEY]
                self.assertIsInstance(migration, dict)
                self.assertIsInstance(migration["operations"], dict)
                migration["operations"][operation_id]["count"] = "broken"
                replay = migrate_legacy_capabilities(data, operation_id=operation_id, dry_run=False)
                self.assertEqual("migration_idempotent_replay", replay["code"])
                self.assertEqual(1, replay["count"])
                self.assertEqual(1, migration["operations"][operation_id]["count"])

    def test_capability_migration_recovers_corrupt_operation_when_users_remain(self) -> None:
        corrupt_operations = (
            {"count": "broken", "snapshots": []},
            {"count": 1, "snapshots": {"legacy": "broken"}},
            {"count": 1, "snapshots": {"": {}}},
            {"count": "broken", "snapshots": {"legacy": {}}},
        )
        for corrupt_operation in corrupt_operations:
            with self.subTest(operation=corrupt_operation):
                data: dict[str, Any] = {
                    "users": {"legacy": {"enabled": True, "proactive_daily_limit": 2}},
                    MIGRATION_KEY: {
                        "operations": {"corrupt-operation": copy.deepcopy(corrupt_operation)},
                    },
                }
                result = migrate_legacy_capabilities(
                    data,
                    operation_id="corrupt-operation",
                    dry_run=False,
                )

                self.assertTrue(result["ok"])
                self.assertEqual("migration_applied", result["code"])
                self.assertTrue(result["recovered_corrupt_operation"])
                self.assertTrue(
                    data["users"]["legacy"]["unified_profile_capabilities"]["private_companion_enabled"]
                )
                repaired = data[MIGRATION_KEY]["operations"]["corrupt-operation"]
                self.assertEqual(1, repaired["count"])
                self.assertIsInstance(repaired["snapshots"], dict)
                replay = migrate_legacy_capabilities(
                    data,
                    operation_id="corrupt-operation",
                    dry_run=False,
                )
                self.assertEqual("migration_idempotent_replay", replay["code"])

    def test_capability_migration_rejects_corrupt_operation_without_pending_users(self) -> None:
        data: dict[str, Any] = {
            "users": {
                "already-migrated": {
                    "unified_profile_capabilities": default_capabilities(),
                }
            },
            MIGRATION_KEY: {
                "operations": {
                    "corrupt-operation": {"count": "broken", "snapshots": []},
                },
            },
        }
        before = copy.deepcopy(data)

        result = migrate_legacy_capabilities(
            data,
            operation_id="corrupt-operation",
            dry_run=False,
        )

        self.assertFalse(result["ok"])
        self.assertEqual("migration_corrupt", result["code"])
        self.assertEqual(before, data)

    def test_capability_migration_preserves_long_keys_and_explicit_none_on_rollback(self) -> None:
        user_id = "platform-user-" + "长" * 160
        data: dict[str, Any] = {
            "users": {
                user_id: {
                    "enabled": True,
                    "private_companion_enabled": None,
                    "proactive_daily_limit": 1,
                }
            }
        }
        before = copy.deepcopy(data["users"][user_id])
        self.assertEqual(
            "migration_applied",
            migrate_legacy_capabilities(data, operation_id="long-key", dry_run=False)["code"],
        )
        self.assertEqual(
            "migration_rolled_back",
            rollback_legacy_capabilities(data, operation_id="long-key")["code"],
        )
        self.assertEqual(before, data["users"][user_id])

    def test_corrupt_migration_snapshot_fails_without_partial_rollback(self) -> None:
        data = {
            "users": {
                "first": {"enabled": False},
                "second": {"enabled": False},
            },
            MIGRATION_KEY: {
                "operations": {
                    "broken": {
                        "snapshots": {
                            "first": {
                                "unified_profile_capabilities": None,
                                "private_companion_enabled": None,
                                "proactive_private_enabled": None,
                                "enabled": True,
                            },
                            "second": "broken",
                        }
                    }
                }
            },
        }
        before = copy.deepcopy(data["users"])
        result = rollback_legacy_capabilities(data, operation_id="broken")
        self.assertEqual("migration_corrupt", result["code"])
        self.assertEqual(before, data["users"])

        data[MIGRATION_KEY]["operations"]["broken"]["snapshots"]["second"] = {
            "unified_profile_capabilities": None,
            "private_companion_enabled": None,
            "proactive_private_enabled": None,
            "enabled": True,
            "_present_fields": [{}],
        }
        result = rollback_legacy_capabilities(data, operation_id="broken")
        self.assertEqual("migration_corrupt", result["code"])
        self.assertEqual(before, data["users"])

    def test_exact_identity_link_unlink_and_merge_require_review(self) -> None:
        store: dict[str, Any] = {}
        registry = UnifiedPersonRegistry(store)
        first = registry.create_or_link(_identity("10001"), operation_id="create-1")
        self.assertTrue(first["ok"])
        same = registry.create_or_link(_identity("10001"), operation_id="create-1-repeat")
        self.assertEqual(first["person_id"], same["person_id"])
        self.assertFalse(same["changed"])
        linked = registry.link_identity(first["person_id"], _identity("telegram-10001"), operation_id="link-1")
        self.assertTrue(linked["changed"])
        self.assertTrue(
            registry.record_identity_source_event(
                first["person_id"],
                linked["identity_key"],
                "group:onebot:group-a",
                hashlib.sha256(b"source-event").hexdigest(),
                operation_id="event-1",
            )["ok"]
        )
        dry_run = registry.unlink_identity(first["person_id"], _identity("telegram-10001"), operation_id="unlink-1", dry_run=True)
        self.assertEqual("migration_dry_run", dry_run["code"])
        self.assertEqual(1, dry_run["replayable_event_count"])
        applied = registry.unlink_identity(first["person_id"], _identity("telegram-10001"), operation_id="unlink-1", dry_run=False)
        self.assertEqual("identity_unlinked", applied["code"])
        self.assertTrue(applied["changed"])
        self.assertTrue(registry.create_or_link(_identity("10002"), operation_id="create-2")["ok"])
        second = registry.resolve(_identity("10002"))
        merge = registry.preview_person_merge(first["person_id"], second["person_id"], operation_id="merge-preview")
        self.assertEqual("merge_manual_review_required", merge["code"])
        self.assertEqual(0, merge["write_count"])

    def test_registry_sanitizes_nested_profile_values_and_non_finite_numbers(self) -> None:
        store: dict[str, Any] = {}
        registry = UnifiedPersonRegistry(store)
        created = registry.create_or_link(
            _identity("safe-profile"),
            profile={
                "display_name": "雪\n见",
                "aliases": ["小雪", float("nan"), {"level1": {"level2": {"raw": "hidden"}}}],
                "raw_content": "must not persist",
            },
            operation_id="safe-profile-create",
        )
        self.assertTrue(created["ok"])
        stored = store["unified_person"]["profiles"][created["person_id"]]
        self.assertEqual("雪 见", stored["display_name"])
        self.assertEqual(["小雪"], stored["aliases"])
        self.assertNotIn("must not persist", repr(store["unified_person"]))

        overlay_result = registry.upsert_group_overlay(
            created["person_id"],
            "group:onebot:10001",
            {
                "safe-label": "公开别名",
                "raw-content": "hidden-a",
                "chat.text": "hidden-b",
                "message/text": "hidden-c",
            },
            operation_id="safe-overlay",
        )
        self.assertTrue(overlay_result["ok"])
        overlay = registry.read_group_overlay(created["person_id"], "group:onebot:10001")
        self.assertEqual({"safe_label": "公开别名"}, overlay["overlay"])

    def test_active_registry_follows_current_persona_store(self) -> None:
        host = _PersonaRegistryHost()
        self.assertIs(host.unified_person_registry, REQ036_ACTIVE_REGISTRY(host))
        host.data = host.secondary_store
        secondary_registry = REQ036_ACTIVE_REGISTRY(host)
        self.assertTrue(secondary_registry.is_bound_to(host.secondary_store))
        created = secondary_registry.create_or_link(_identity("persona-secondary"), operation_id="persona-create")
        self.assertTrue(created["ok"])
        self.assertNotIn("unified_person", host.primary_store)
        self.assertIn("unified_person", host.secondary_store)

    def test_private_llm_gate_is_a_compatibility_noop(self) -> None:
        host = _GateHost()
        event = _PrivateEvent()
        req = types.SimpleNamespace(
            system_prompt="secret",
            prompt="message",
            contexts=["memory"],
            extra_user_content_parts=["extra"],
            func_tool=object(),
            tools=["tool"],
            images=["image"],
            image_urls=["url"],
        )
        asyncio.run(REQ036_LLM_GATE(host, event, req))
        self.assertFalse(event.stopped)
        self.assertEqual([], host.replies)
        self.assertEqual("secret", req.system_prompt)
        self.assertEqual(["memory"], req.contexts)
        self.assertIsNotNone(req.func_tool)
        self.assertEqual(0, host.memory_calls)
        self.assertEqual(0, host.tool_calls)
        self.assertEqual(0, host.relationship_calls)

    def test_legacy_denied_marker_no_longer_blocks_llm_request(self) -> None:
        host = _GateHost()
        event = _PrivateEvent()
        event.private_companion_req036_denied = True
        req = types.SimpleNamespace(
            system_prompt="secret",
            prompt="message",
            contexts=["memory"],
            extra_user_content_parts=["extra"],
            func_tool=object(),
            tools=["tool"],
            images=["image"],
            image_urls=["url"],
        )
        asyncio.run(REQ036_LLM_GATE(host, event, req))
        self.assertFalse(event.stopped)
        self.assertEqual([], host.replies)
        self.assertEqual("secret", req.system_prompt)
        self.assertEqual("message", req.prompt)
        self.assertEqual(["memory"], req.contexts)
        self.assertIsNotNone(req.func_tool)

    def test_private_guards_run_before_enrichment_hooks(self) -> None:
        enrichment_priority = _method_priority("enforce_p4_live_confinement_before_enrichment")
        for method_name in (
            "guard_req036_private_capability_early",
            "guard_req036_private_capability_before_llm",
        ):
            with self.subTest(method_name=method_name):
                priority = _method_priority(method_name)
                self.assertGreater(priority, 0)
                self.assertGreater(priority, enrichment_priority)

    def test_non_chat_events_are_left_for_dedicated_handlers(self) -> None:
        payloads = (
            {"post_type": "notice", "notice_type": "notify", "sub_type": "input_status"},
            {"post_type": "notice", "notice_type": "notify", "sub_type": "poke"},
            {"post_type": "request", "request_type": "friend"},
            {"post_type": "message_sent", "message_type": "private"},
            {"post_type": "meta_event", "meta_event_type": "heartbeat"},
        )
        for raw_message in payloads:
            with self.subTest(raw_message=raw_message):
                host = _EarlyGateHost()
                event = _PrivateEvent("", raw_message=raw_message)
                asyncio.run(REQ036_EARLY_GATE(host, event))
                self.assertFalse(event.stopped)
                self.assertFalse(getattr(event, "private_companion_req036_denied", False))
                self.assertEqual([], host.replies)
                self.assertEqual(0, host.profile_checks)
                self.assertEqual({}, host.data["users"])

    def test_real_text_and_empty_image_messages_create_profiles_without_denial(self) -> None:
        events = (
            _PrivateEvent("你好", raw_message={"post_type": "message", "message_type": "private"}),
            _PrivateEvent(
                "",
                raw_message={
                    "post_type": "message",
                    "message_type": "private",
                    "message": [{"type": "image", "data": {"file": "a.jpg"}}],
                },
            ),
        )
        for event in events:
            with self.subTest(raw_message=event.message_obj.raw_message):
                host = _EarlyGateHost()
                asyncio.run(REQ036_EARLY_GATE(host, event))
                self.assertFalse(event.stopped)
                self.assertFalse(getattr(event, "private_companion_req036_denied", False))
                self.assertEqual([], host.replies)
                self.assertEqual(1, host.profile_checks)

    def test_event_classification_keeps_real_empty_media_and_old_adapter_messages(self) -> None:
        ingress = _EventIngressHost()
        cases = (
            ({"post_type": "message", "message_type": "private"}, True),
            ({"post_type": "message", "message_type": "private", "message": [{"type": "image"}]}, True),
            ({"post_type": "notice", "notice_type": "notify", "sub_type": "input_status"}, False),
            ({"post_type": "notice", "notice_type": "notify", "sub_type": "poke"}, False),
            ({"post_type": "request", "request_type": "friend"}, False),
            ({"post_type": "message_sent", "message_type": "private"}, False),
            ({"post_type": "meta_event", "meta_event_type": "heartbeat"}, False),
            ({"post_type": "third_party_message", "message_type": "private"}, True),
            ({"message_type": "private"}, True),
            ({"notice_type": "notify"}, False),
            ({}, True),
        )
        for raw_message, expected in cases:
            with self.subTest(raw_message=raw_message):
                self.assertEqual(expected, ingress._event_is_inbound_chat_message(_PrivateEvent(raw_message=raw_message)))

    def test_event_classification_skips_unstringifiable_identity_values(self) -> None:
        class _ExplodingIdentity:
            def __str__(self):
                raise ModuleNotFoundError("No module named 'torch'", name="torch")

        ingress = _EventIngressHost()
        event = _PrivateEvent(
            raw_message={
                "post_type": "message",
                "message_type": "private",
                "user_id": _ExplodingIdentity(),
            }
        )
        assert ingress._event_is_inbound_chat_message(event) is True

    def test_outbound_message_markers_are_not_inbound_even_when_sender_is_recipient(self) -> None:
        ingress = _EventIngressHost()
        payloads = (
            {"post_type": "message", "message_type": "private", "is_self": True},
            {"post_type": "message", "message_type": "private", "is_outbound": "true"},
            {"post_type": "message", "message_type": "private", "direction": "outgoing"},
            {"post_type": "message", "message_type": "private", "status": "sent"},
            {"post_type": "outbound", "message_type": "private"},
            {"post_type": "send", "message_type": "private"},
            {"post_type": "message", "message_type": "private", "user_id": "bot-1", "self_id": "bot-1"},
        )
        for raw_message in payloads:
            with self.subTest(raw_message=raw_message):
                event = _PrivateEvent("", raw_message=raw_message)
                self.assertFalse(ingress._event_is_inbound_chat_message(event))

    def test_outbound_markers_on_event_or_message_object_are_not_inbound(self) -> None:
        ingress = _EventIngressHost()
        event = _PrivateEvent("", raw_message={"post_type": "message", "message_type": "private"})
        event.is_outbound = True
        self.assertFalse(ingress._event_is_inbound_chat_message(event))

        event = _PrivateEvent("", raw_message={"post_type": "message", "message_type": "private"})
        event.message_obj.from_self = True
        self.assertFalse(ingress._event_is_inbound_chat_message(event))

        mapping_event = types.SimpleNamespace(
            message_obj={
                "post_type": "message",
                "message_type": "private",
                "is_outbound": True,
            }
        )
        self.assertFalse(ingress._event_is_inbound_chat_message(mapping_event))

    def test_outbound_events_skip_early_and_llm_private_gates(self) -> None:
        raw_message = {
            "post_type": "message",
            "message_type": "private",
            "is_outbound": True,
            "user_id": "recipient-1",
        }
        early_host = _EarlyGateHost()
        early_event = _PrivateEvent("", raw_message=raw_message)
        asyncio.run(REQ036_EARLY_GATE(early_host, early_event))
        self.assertFalse(early_event.stopped)
        self.assertEqual([], early_host.replies)
        self.assertEqual(0, early_host.profile_checks)

        llm_host = _GateHost()
        llm_event = _PrivateEvent("", raw_message=raw_message)
        req = types.SimpleNamespace(system_prompt="keep", prompt="keep", contexts=["keep"])
        asyncio.run(REQ036_LLM_GATE(llm_host, llm_event, req))
        self.assertFalse(llm_event.stopped)
        self.assertEqual([], llm_host.replies)
        self.assertEqual("keep", req.system_prompt)

    def test_sender_self_echo_skips_llm_gate_without_post_type_message_sent(self) -> None:
        host = _GateHost()
        event = _PrivateEvent(
            "",
            raw_message={
                "post_type": "message",
                "message_type": "private",
                "user_id": "bot-1",
                "self_id": "bot-1",
            },
        )
        req = types.SimpleNamespace(system_prompt="keep", prompt="keep", contexts=["keep"])
        asyncio.run(REQ036_LLM_GATE(host, event, req))
        self.assertFalse(event.stopped)
        self.assertEqual([], host.replies)
        self.assertEqual("keep", req.system_prompt)

    def test_unmarked_denial_reply_echo_is_consumed_once_by_scope_and_text(self) -> None:
        host = _RejectRecorderHost()
        inbound = _PrivateEvent("你好", raw_message={"post_type": "message", "message_type": "private"})
        asyncio.run(REQ036_REJECT(host, inbound, {"code": "disabled", "reply": "固定拒绝文本"}))
        self.assertEqual(["固定拒绝文本"], host.replies)

        echo = _PrivateEvent(
            "固定拒绝文本",
            raw_message={"post_type": "message", "message_type": "private"},
            message_id="echo-1",
        )
        self.assertFalse(host._event_is_inbound_chat_message(echo))
        self.assertFalse(host._event_is_inbound_chat_message(echo))

        duplicate_echo = _PrivateEvent(
            "固定拒绝文本",
            raw_message={"post_type": "message", "message_type": "private"},
            message_id="echo-1",
        )
        self.assertFalse(host._event_is_inbound_chat_message(duplicate_echo))

        user_repeat = _PrivateEvent(
            "固定拒绝文本",
            raw_message={"post_type": "message", "message_type": "private"},
            message_id="user-2",
        )
        self.assertTrue(host._event_is_inbound_chat_message(user_repeat))

    def test_unmarked_empty_echo_does_not_hide_real_media_message(self) -> None:
        host = _RejectRecorderHost()
        inbound = _PrivateEvent("你好", raw_message={"post_type": "message", "message_type": "private"})
        asyncio.run(REQ036_REJECT(host, inbound, {"code": "disabled", "reply": "固定拒绝文本"}))

        empty_echo = _PrivateEvent(
            "",
            raw_message={"post_type": "message", "message_type": "private", "message": []},
        )
        self.assertFalse(host._event_is_inbound_chat_message(empty_echo))

        asyncio.run(REQ036_REJECT(host, _PrivateEvent("再试一次"), {"code": "disabled", "reply": "固定拒绝文本"}))
        image_message = _PrivateEvent(
            "",
            raw_message={
                "post_type": "message",
                "message_type": "private",
                "message": [{"type": "image", "data": {"file": "a.jpg"}}],
            },
        )
        self.assertTrue(host._event_is_inbound_chat_message(image_message))

    def test_denial_echo_guard_keeps_non_text_components_and_mixed_media(self) -> None:
        host = _RejectRecorderHost()
        asyncio.run(
            REQ036_REJECT(
                host,
                _PrivateEvent("你好", raw_message={"post_type": "message", "message_type": "private"}),
                {"code": "disabled", "reply": "固定拒绝文本"},
            )
        )
        for kind in ("marketface", "mface", "json", "xml", "share", "location", "music"):
            with self.subTest(kind=kind):
                event = _PrivateEvent(
                    "",
                    raw_message={
                        "post_type": "message",
                        "message_type": "private",
                        "message": [{"type": kind, "data": {"id": "asset-1"}}],
                    },
                )
                self.assertTrue(host._event_is_inbound_chat_message(event))

        mixed = _PrivateEvent(
            "固定拒绝文本",
            raw_message={
                "post_type": "message",
                "message_type": "private",
                "message": [
                    {"type": "text", "data": {"text": "固定拒绝文本"}},
                    {"type": "image", "data": {"file": "a.jpg"}},
                ],
            },
        )
        self.assertTrue(host._event_is_inbound_chat_message(mixed))

    def test_denial_echo_scope_isolated_by_persona_adapter_and_bot(self) -> None:
        host = _RejectRecorderHost()
        host._private_event_identity_context = lambda event, _subject: {
            "platform": "onebot",
            "adapter": getattr(event, "adapter_instance_id", ""),
            "bot_id": getattr(event, "bot_id", ""),
        }

        sent = _PrivateEvent("你好", raw_message={"post_type": "message", "message_type": "private"})
        sent.private_companion_persona_id = "persona-a"
        sent.adapter_instance_id = "adapter-a"
        sent.bot_id = "bot-a"
        asyncio.run(REQ036_REJECT(host, sent, {"code": "disabled", "reply": "固定拒绝文本"}))

        other_persona = _PrivateEvent("固定拒绝文本", raw_message={"post_type": "message", "message_type": "private"})
        other_persona.private_companion_persona_id = "persona-b"
        other_persona.adapter_instance_id = "adapter-a"
        other_persona.bot_id = "bot-a"
        self.assertTrue(host._event_is_inbound_chat_message(other_persona))

        other_adapter = _PrivateEvent("固定拒绝文本", raw_message={"post_type": "message", "message_type": "private"})
        other_adapter.private_companion_persona_id = "persona-a"
        other_adapter.adapter_instance_id = "adapter-b"
        other_adapter.bot_id = "bot-a"
        self.assertTrue(host._event_is_inbound_chat_message(other_adapter))

        same_scope = _PrivateEvent("固定拒绝文本", raw_message={"post_type": "message", "message_type": "private"})
        same_scope.private_companion_persona_id = "persona-a"
        same_scope.adapter_instance_id = "adapter-a"
        same_scope.bot_id = "bot-a"
        self.assertFalse(host._event_is_inbound_chat_message(same_scope))

    def test_non_chat_events_do_not_enter_private_pipeline_or_llm_gate(self) -> None:
        raw_message = {"post_type": "notice", "notice_type": "notify", "sub_type": "input_status"}
        pipeline_host = _PrivatePipelineGateHost()
        pipeline_event = _PrivateEvent(raw_message=raw_message)
        asyncio.run(REQ036_PRIVATE_HANDLER(pipeline_host, pipeline_event))
        self.assertFalse(pipeline_event.stopped)
        self.assertEqual([], pipeline_host.replies)
        self.assertEqual([], pipeline_host.attached_sources)
        llm_host = _GateHost()
        llm_event = _PrivateEvent(raw_message=raw_message)
        req = types.SimpleNamespace(system_prompt="keep", prompt="keep", contexts=["keep"])
        asyncio.run(REQ036_LLM_GATE(llm_host, llm_event, req))
        self.assertFalse(llm_event.stopped)
        self.assertEqual([], llm_host.replies)
        self.assertEqual("keep", req.system_prompt)

    def test_real_private_messages_are_rejected_once_and_duplicate_ids_are_idempotent(self) -> None:
        host = _RejectRecorderHost()
        first = _PrivateEvent("你好", raw_message={"post_type": "message", "message_type": "private"}, message_id="m-1")
        second = _PrivateEvent("你好", raw_message={"post_type": "message", "message_type": "private"}, message_id="m-1")
        third = _PrivateEvent("你好", raw_message={"post_type": "message", "message_type": "private"}, message_id="m-2")
        asyncio.run(REQ036_REJECT(host, first, {"code": "disabled", "reply": "固定拒绝文本"}))
        asyncio.run(REQ036_REJECT(host, first, {"code": "disabled", "reply": "固定拒绝文本"}))
        asyncio.run(REQ036_REJECT(host, second, {"code": "disabled", "reply": "固定拒绝文本"}))
        asyncio.run(REQ036_REJECT(host, third, {"code": "disabled", "reply": "固定拒绝文本"}))
        self.assertTrue(first.stopped and second.stopped and third.stopped)
        self.assertEqual(2, len(host.replies))
        self.assertEqual(2, host.attempts)

    def test_rejection_without_message_id_is_not_time_rate_limited(self) -> None:
        host = _RejectRecorderHost()
        for _ in range(2):
            event = _PrivateEvent("你好", raw_message={"post_type": "message", "message_type": "private"})
            asyncio.run(REQ036_REJECT(host, event, {"code": "disabled", "reply": "固定拒绝文本"}))
            self.assertTrue(event.stopped)
        self.assertEqual(2, len(host.replies))

    def test_failed_rejection_can_retry_same_message_id(self) -> None:
        host = _RejectRecorderHost(fail_first=True)
        first = _PrivateEvent("你好", raw_message={"post_type": "message", "message_type": "private"}, message_id="retry-1")
        with self.assertRaisesRegex(RuntimeError, "transport unavailable"):
            asyncio.run(REQ036_REJECT(host, first, {"code": "disabled", "reply": "固定拒绝文本"}))
        second = _PrivateEvent("你好", raw_message={"post_type": "message", "message_type": "private"}, message_id="retry-1")
        asyncio.run(REQ036_REJECT(host, second, {"code": "disabled", "reply": "固定拒绝文本"}))
        self.assertEqual(1, len(host.replies))
        self.assertEqual(2, host.attempts)

    def test_silently_cancelled_rejection_releases_echo_and_message_id_reservations(self) -> None:
        host = _RejectSilentCancelHost()
        first = _PrivateEvent(
            "你好",
            raw_message={"post_type": "message", "message_type": "private"},
            message_id="cancel-1",
        )
        asyncio.run(REQ036_REJECT(host, first, {"code": "disabled", "reply": "固定拒绝文本"}))
        self.assertEqual([], host.replies)
        self.assertEqual([], getattr(host, "_req036_denial_echo_cache", []))
        self.assertEqual({}, host._req036_recent_denial_message_ids)

        second = _PrivateEvent(
            "你好",
            raw_message={"post_type": "message", "message_type": "private"},
            message_id="cancel-1",
        )
        asyncio.run(REQ036_REJECT(host, second, {"code": "disabled", "reply": "固定拒绝文本"}))
        self.assertEqual(["固定拒绝文本"], host.replies)
        self.assertEqual(2, host.attempts)

    def test_rejection_stops_event_even_when_reply_transport_fails(self) -> None:
        event = _PrivateEvent()
        with self.assertRaisesRegex(RuntimeError, "transport unavailable"):
            asyncio.run(
                REQ036_REJECT(
                    _RejectRaisesHost(),
                    event,
                    {"code": "private_companion_disabled", "reply": "固定拒绝文本"},
                )
            )
        self.assertTrue(event.stopped)
        self.assertTrue(event.private_companion_req036_denied)

    def test_private_message_attaches_unified_identity_without_permission(self) -> None:
        source = (ROOT / "message_pipeline.py").read_text(encoding="utf-8")
        self.assertIn('source="private_auto"', source)
        self.assertNotIn("_req036_reject_unauthorized_private_event", source)

    def test_private_command_has_no_permission_rejection_path(self) -> None:
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        start = source.index("    async def companion_command(")
        end = source.index("    async def group_companion_command(", start)
        command = source[start:end]
        self.assertNotIn("_req036_reject_unauthorized_private_event", command)
        self.assertIn('source="private_command"', command)

    def test_contract_rejects_raw_conversation_fields(self) -> None:
        # PR #104 and the paired Memory PR #6 publish this exact v1 wire
        # fingerprint. Validation may become stricter without silently
        # changing the cross-plugin protocol identity.
        self.assertEqual("72067a45012a0588", CONTRACT_FINGERPRINT)
        person = _person_projection()
        dto = build_profile_dto(person_ref=build_person_ref(person), identity_summary={"display_name": "雪"})
        self.assertEqual([], validate_profile_dto(dto))
        dto["identity_summary"]["chat_text"] = "should not cross the bridge"
        self.assertIn("identity_summary_contains_forbidden_data", validate_profile_dto(dto))

    def test_contract_rejects_extra_fields_non_finite_values_controls_and_depth(self) -> None:
        base = build_profile_dto(
            person_ref=build_person_ref(_person_projection()),
            identity_summary={"display_name": "雪"},
            expression_summary={"relationship_score": 10, "relationship_role": "friend"},
        )
        mutations = {
            "extra_dto_field": lambda dto: dto.update({"raw_text": "hidden"}),
            "extra_person_ref_field": lambda dto: dto["person_ref"].update({"raw_content": "hidden"}),
            "message_text": lambda dto: dto["identity_summary"].update({"message_text": "hidden"}),
            "non_finite": lambda dto: dto["expression_summary"].update({"relationship_score": float("nan")}),
            "control_character": lambda dto: dto["identity_summary"].update({"display_name": "雪\x1b\nhidden"}),
            "deep_structure": lambda dto: dto["identity_summary"].update(
                {"display_name": {"level1": {"level2": {"level3": "hidden"}}}}
            ),
            "wrong_summary_type": lambda dto: dto["expression_summary"].update({"relationship_score": True}),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                candidate = copy.deepcopy(base)
                mutate(candidate)
                self.assertTrue(validate_profile_dto(candidate))

    def test_contract_rejects_boolean_projection_revision(self) -> None:
        person_ref = build_person_ref(_person_projection())
        person_ref["projection_revision"] = True
        self.assertIn("projection_revision_invalid", validate_person_ref(person_ref))

    def test_portrait_request_validation_is_total_and_same_subject_scoped(self) -> None:
        person = _person_projection()
        person_id = person["person_id"]
        valid = build_portrait_request(
            person_ref=person,
            requester_person_id=person_id,
            target_person_id=person_id,
            scope="group:onebot:10001",
            purpose="summarize_to_subject",
        )
        self.assertEqual([], validate_portrait_request(valid))

        malformed = copy.deepcopy(valid)
        malformed["person_ref"] = "broken"
        self.assertIn("person_ref_invalid", validate_portrait_request(malformed))

        wrong_requester = copy.deepcopy(valid)
        wrong_requester["requester_person_id"] = "person_" + "b" * 24
        self.assertIn("requester_target_mismatch", validate_portrait_request(wrong_requester))

        missing_requester = copy.deepcopy(valid)
        missing_requester["requester_person_id"] = ""
        self.assertIn("requester_person_id_invalid", validate_portrait_request(missing_requester))

        extra_field = copy.deepcopy(valid)
        extra_field["message_text"] = "hidden"
        self.assertIn("portrait_request_fields_invalid", validate_portrait_request(extra_field))

    def test_private_entry_has_no_capability_rejection(self) -> None:
        source = (ROOT / "message_pipeline.py").read_text(encoding="utf-8")
        start = source.index("async def handle_private_message(")
        end = source.index("async def handle_group_message(", start)
        handler = source[start:end]
        self.assertNotIn("private_gate", handler)
        self.assertNotIn("_req036_reject_unauthorized_private_event", handler)
        self.assertLess(handler.index("self._req036_attach_unified_profile_context("), handler.index("self._qzone_note_event_bot(event)"))

    def test_group_portrait_query_classifier_distinguishes_self_from_third_party(self) -> None:
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PrivateCompanionPlugin")
        method = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == "_req036_group_portrait_query_kind")
        method = copy.deepcopy(method)
        method.decorator_list = []
        module = ast.Module(body=[method], type_ignores=[])
        ast.fix_missing_locations(module)
        namespace = {"Any": Any, "re": __import__("re"), "_single_line": lambda value, _limit=240: str(value or "")[:_limit]}
        exec(compile(module, str(ROOT / "main.py"), "exec"), namespace)
        classify = namespace["_req036_group_portrait_query_kind"]
        self.assertEqual("self", classify("@bot 我喜欢什么"))
        self.assertEqual("self", classify("@bot 我爱吃什么"))
        self.assertEqual("self", classify("@bot 我有什么爱好"))
        self.assertEqual("self", classify("@bot 我想知道自己有什么爱好"))
        self.assertEqual("self", classify("@bot 你觉得我喜欢什么"))
        self.assertEqual("self", classify("@bot 你知道我有什么爱好"))
        self.assertEqual("third_party", classify("@bot 小王喜欢什么"))
        self.assertEqual("third_party", classify("@bot 小王有什么爱好"))
        self.assertEqual("third_party", classify("@bot 我姐姐喜欢什么"))
        self.assertEqual("third_party", classify("@bot 我想知道小王有什么爱好"))
        self.assertEqual("bot_self", classify("@bot 你喜欢什么"))
        self.assertEqual("bot_self", classify("@bot 我想知道你有什么爱好"))
        self.assertEqual("third_party", classify("@bot 你姐姐喜欢什么"))
        self.assertEqual("", classify("@用户甲 自己喜欢什么就玩什么喵"))
        self.assertEqual("", classify("喜欢什么就玩什么呀"))
        self.assertEqual("", classify("@bot 喜欢什么发型的女孩子"))
        self.assertEqual("", classify("@bot 什么爱好"))
        self.assertEqual("", classify("@bot 你觉得喜欢什么"))
        self.assertEqual("", classify("@bot 我想知道喜欢什么"))
        self.assertEqual("", classify("小王的爱好是跑步"))

    def test_group_third_party_guard_applies_when_observation_is_disabled(self) -> None:
        host = _GroupGateHost()
        event = _GroupEvent("@bot 小王有什么爱好", directed=True)
        asyncio.run(REQ036_GROUP_GATE(host, event))
        self.assertTrue(event.stopped)
        self.assertEqual(["这个我不方便替别人整理啦。"], host.replies)

    def test_group_third_party_guard_can_be_disabled(self) -> None:
        host = _GroupGateHost()
        host.enable_group_third_party_portrait_guard = False
        event = _GroupEvent("@bot 小王有什么爱好", directed=True)
        asyncio.run(REQ036_GROUP_GATE(host, event))
        self.assertFalse(event.stopped)
        self.assertEqual([], host.replies)

    def test_req036_intercept_log_does_not_include_group_or_message_text(self) -> None:
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("group_hash=", source)
        self.assertIn("text_hash=", source)
        self.assertNotIn("群聊第三方画像查询已拦截: group=%s text=%s", source)

    def test_ordinary_group_preference_chatter_is_not_intercepted(self) -> None:
        host = _GroupGateHost()
        event = _GroupEvent("小王有什么爱好")
        asyncio.run(REQ036_GROUP_GATE(host, event))
        self.assertFalse(event.stopped)
        self.assertEqual([], host.replies)

    def test_bot_self_preference_query_stays_on_normal_group_reply_path(self) -> None:
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PrivateCompanionPlugin")
        method = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == "_req036_group_portrait_query_kind")
        method = copy.deepcopy(method)
        method.decorator_list = []
        module = ast.Module(body=[method], type_ignores=[])
        ast.fix_missing_locations(module)
        namespace = {"Any": Any, "re": __import__("re"), "_single_line": lambda value, _limit=240: str(value or "")[:_limit]}
        exec(compile(module, str(ROOT / "main.py"), "exec"), namespace)

        host = _GroupGateHost()
        host._req036_group_portrait_query_kind = namespace["_req036_group_portrait_query_kind"]
        event = _GroupEvent("@bot 你喜欢什么", directed=True)
        asyncio.run(REQ036_GROUP_GATE(host, event))
        self.assertFalse(event.stopped)
        self.assertEqual([], host.replies)

    def test_group_privacy_guard_stops_even_when_reply_transport_fails(self) -> None:
        host = _GroupGateReplyRaisesHost()
        event = _GroupEvent("@bot 小王有什么爱好", directed=True)
        with self.assertRaisesRegex(RuntimeError, "transport unavailable"):
            asyncio.run(REQ036_GROUP_GATE(host, event))
        self.assertTrue(event.stopped)

    def test_configured_target_migrates_once_then_respects_capability_freeze(self) -> None:
        host = _ConfiguredTargetHost()
        REQ036_CONFIGURED_TARGET_SYNC(host)
        self.assertTrue(host.user["unified_profile_capabilities"]["private_companion_enabled"])
        self.assertFalse(host.user["unified_profile_capabilities"]["proactive_private_enabled"])
        self.assertEqual("default_closed", host.user["unified_profile_capabilities"]["grant_source"])
        self.assertEqual(1, host.delivery_bound)
        update_capabilities(
            host.user,
            {"private_companion_enabled": False},
            actor_authorized=True,
            actor_id="page_administrator",
            target_identity="legacy-target",
        )
        REQ036_CONFIGURED_TARGET_SYNC(host)
        self.assertTrue(host.user["unified_profile_capabilities"]["private_companion_enabled"])
        self.assertEqual(1, host.delivery_bound)

    def test_configured_target_compatibility_migration_never_reopens_explicit_disable(self) -> None:
        host = _LegacyTargetMigrationHost()
        user: dict[str, Any] = {"proactive_daily_limit": 1}
        self.assertTrue(REQ036_CONFIGURED_TARGET_MIGRATION(host, "legacy-target", user))
        state = user["unified_profile_capabilities"]
        self.assertTrue(state["private_companion_enabled"])
        self.assertTrue(state["proactive_private_enabled"])
        self.assertEqual("legacy_configured_target_migration", state["grant_source"])
        update_capabilities(
            user,
            {"private_companion_enabled": False},
            actor_authorized=True,
            actor_id="page_administrator",
            target_identity="legacy-target",
        )
        self.assertFalse(REQ036_CONFIGURED_TARGET_MIGRATION(host, "legacy-target", user))
        self.assertTrue(user["unified_profile_capabilities"]["private_companion_enabled"])

    def test_configured_target_reconciles_migrated_false_scoped_profile(self) -> None:
        host = _LegacyTargetMigrationHost()
        user = {
            "user_id": "onebot:legacy-target:scope",
            "identity_subject_id": "legacy-target",
            "identity_platform_kind": "onebot",
            "enabled": False,
            "unified_profile_capabilities": {
                **default_capabilities(grant_source="legacy_effective_migration"),
                "private_companion_enabled": False,
                "proactive_private_enabled": False,
            },
            "proactive_daily_limit": 1,
        }
        self.assertTrue(REQ036_CONFIGURED_TARGET_MIGRATION(host, "onebot:legacy-target:scope", user))
        self.assertTrue(user["unified_profile_capabilities"]["private_companion_enabled"])
        self.assertTrue(user["unified_profile_capabilities"]["proactive_private_enabled"])
        self.assertEqual("configured_target_capability_reconciliation", user["unified_profile_capabilities"]["grant_source"])
        self.assertTrue(user["enabled"])

    def test_scoped_reconciliation_keeps_platforms_and_manual_disables_isolated(self) -> None:
        host = _LegacyTargetMigrationHost()
        other_platform = {
            "user_id": "telegram:legacy-target:scope",
            "identity_subject_id": "legacy-target",
            "identity_platform_kind": "telegram",
            "unified_profile_capabilities": default_capabilities(grant_source="legacy_effective_migration"),
        }
        self.assertFalse(REQ036_CONFIGURED_TARGET_MIGRATION(host, "telegram:legacy-target:scope", other_platform))
        self.assertTrue(other_platform["unified_profile_capabilities"]["private_companion_enabled"])

        manually_disabled = {
            "user_id": "legacy-target",
            "identity_subject_id": "legacy-target",
            "identity_platform_kind": "onebot",
            "manual_disabled": True,
            "unified_profile_capabilities": default_capabilities(grant_source="legacy_effective_migration"),
        }
        self.assertFalse(REQ036_CONFIGURED_TARGET_MIGRATION(host, "legacy-target", manually_disabled))
        self.assertTrue(manually_disabled["unified_profile_capabilities"]["private_companion_enabled"])

    def test_owner_role_does_not_implicitly_grant_proactive_permission(self) -> None:
        class OwnerHost(_LegacyTargetMigrationHost):
            @staticmethod
            def _configured_target_ids() -> list[str]:
                return []

            @staticmethod
            def _req036_owner_companion_status(_user: Any) -> tuple[bool, bool]:
                return True, True

        host = OwnerHost()
        user = {
            "user_id": "owner-shadow",
            "relationship_role": "owner",
            "unified_profile_capabilities": default_capabilities(grant_source="legacy_effective_migration"),
        }
        self.assertFalse(REQ036_CONFIGURED_TARGET_MIGRATION(host, "owner-shadow", user))
        self.assertTrue(user["unified_profile_capabilities"]["private_companion_enabled"])
        self.assertFalse(user["unified_profile_capabilities"]["proactive_private_enabled"])
        self.assertEqual("legacy_effective_migration", user["unified_profile_capabilities"]["grant_source"])

    def test_user_list_keeps_permission_sources_visible(self) -> None:
        result = asyncio.run(REQ036_USER_LIST(_UserListHost()))
        self.assertEqual(2, result["total"])
        self.assertEqual({"source-a", "source-b"}, {item["user_id"] for item in result["items"]})


if __name__ == "__main__":
    unittest.main()
