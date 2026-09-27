# -*- coding: utf-8 -*-
"""MemoryCompanionAdapterProbeNamespaceScopedMixin。

由 tools/split_mixin_domain.py 从 memory_companion_adapter.py 机械抽取（14 个方法 + 0 个模块级名字 + 0 个类级赋值 / 338 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 MemoryCompanionAdapterMixin）。
"""
from __future__ import annotations

import asyncio
import re
from .bot_personal_contract import (
    BOT_PERSONAL_CANONICAL_SCHEMA_VERSION,
    BOT_PERSONAL_CAPABILITY_SCHEMA_VERSION,
    BOT_PERSONAL_MEMORY_DOMAIN,
    BOT_PERSONAL_MEMORY_TYPES,
    BOT_PERSONAL_PAYLOAD_SCHEMA_VERSION,
    CONTRACT_FINGERPRINT,
    CONTRACT_REVISION,
    WINDOW_SLUGS,
)
from .helpers import _single_line
from .identity_namespace import validate_namespace_context
from .memory_companion_adapter_shared import _LEGACY_V2_CONTRACT
from .namespace_capability import negotiate_namespace_capability
from typing import Any



class MemoryCompanionAdapterProbeNamespaceScopedMixin:
    """MemoryCompanionAdapterProbeNamespaceScopedMixin（从 MemoryCompanionAdapterMixin 拆出）。"""


    def _memory_companion_probe_capabilities(self, bridge: Any) -> dict[str, Any]:
        try:
            getter = getattr(bridge, "probe_bot_personal_memory_capabilities", None)
        except Exception:
            return self._memory_companion_degraded_status("capability_probe_exception")
        if not callable(getter):
            return self._memory_companion_degraded_status("capability_probe_missing")
        try:
            result = getter()
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="capability_probe"):
                return dict(self._bridge_last_status)
            return self._memory_companion_degraded_status("capability_probe_exception")
        if not isinstance(result, dict):
            return self._memory_companion_degraded_status("capability_probe_invalid")

        expected_windows = list(WINDOW_SLUGS)
        expected_memory_types = list(BOT_PERSONAL_MEMORY_TYPES)
        observed_windows = result.get("windows")
        observed_memory_types = result.get("memory_types")
        observed_domain = result.get("memory_domain", result.get("domain", ""))
        mismatches: list[str] = []
        if result.get("contract_fingerprint") != CONTRACT_FINGERPRINT:
            mismatches.append("contract_fingerprint")
        if result.get("contract_revision") != CONTRACT_REVISION:
            mismatches.append("contract_revision")
        if result.get("capability_schema_version") != BOT_PERSONAL_CAPABILITY_SCHEMA_VERSION:
            mismatches.append("capability_schema_version")
        if result.get("payload_schema_version") != BOT_PERSONAL_PAYLOAD_SCHEMA_VERSION:
            mismatches.append("payload_schema_version")
        if result.get("canonical_schema_version") != BOT_PERSONAL_CANONICAL_SCHEMA_VERSION:
            mismatches.append("canonical_schema_version")
        if observed_domain != BOT_PERSONAL_MEMORY_DOMAIN:
            mismatches.append("memory_domain")
        if observed_windows != expected_windows:
            mismatches.append("windows")
        if observed_memory_types != expected_memory_types:
            mismatches.append("memory_types")
        if result.get("available") is not True:
            mismatches.append("available")
        if mismatches:
            legacy_v2 = (
                result.get("available") is True
                and all(result.get(key) == value for key, value in _LEGACY_V2_CONTRACT.items())
                and observed_domain == BOT_PERSONAL_MEMORY_DOMAIN
                and observed_windows == expected_windows
                and observed_memory_types == expected_memory_types
            )
            if legacy_v2:
                status = dict(result)
                status.update(
                    {
                        "state": "ready_compatible",
                        "degraded": False,
                        "available": True,
                        "contract_compatibility": "legacy_v2",
                        "negotiated_canonical_schema_version": 2,
                        "negotiated_mismatches": tuple(mismatches),
                    }
                )
                self._bridge_last_status = status
                return status
            compatible_superset = (
                set(mismatches).issubset({"contract_fingerprint", "windows", "memory_types"})
                and result.get("contract_revision") == CONTRACT_REVISION
                and result.get("capability_schema_version") == BOT_PERSONAL_CAPABILITY_SCHEMA_VERSION
                and result.get("payload_schema_version") == BOT_PERSONAL_PAYLOAD_SCHEMA_VERSION
                and observed_domain == BOT_PERSONAL_MEMORY_DOMAIN
                and isinstance(observed_windows, (list, tuple))
                and isinstance(observed_memory_types, (list, tuple))
                and set(expected_windows).issubset(set(observed_windows))
                and set(expected_memory_types).issubset(set(observed_memory_types))
                and (
                    len(set(observed_windows)) > len(expected_windows)
                    or len(set(observed_memory_types)) > len(expected_memory_types)
                )
            )
            if compatible_superset:
                status = dict(result)
                status.update(
                    {
                        "state": "ready_compatible",
                        "degraded": False,
                        "available": True,
                        "contract_compatibility": "superset",
                        "negotiated_canonical_schema_version": BOT_PERSONAL_CANONICAL_SCHEMA_VERSION,
                        "negotiated_mismatches": tuple(mismatches),
                    }
                )
                self._bridge_last_status = status
                return status
            return self._memory_companion_degraded_status(
                "capability_contract_mismatch",
                mismatches=tuple(mismatches),
            )

        status = dict(result)
        status.setdefault("state", "ready")
        status.setdefault("degraded", False)
        status.setdefault("available", True)
        status.setdefault("negotiated_canonical_schema_version", BOT_PERSONAL_CANONICAL_SCHEMA_VERSION)
        self._bridge_last_status = status
        return status

    def _memory_companion_probe_namespace_capabilities(self, bridge: Any) -> dict[str, Any]:
        """Negotiate only the REQ-041 scoped API; legacy bridge state is untouched."""
        try:
            getter = getattr(bridge, "probe_namespace_context_capabilities", None)
        except Exception:
            getter = None
        if not callable(getter):
            return {
                "available": False,
                "state": "degraded",
                "code": "namespace_capability_probe_missing",
                "mismatches": ["namespace_capability_probe_missing"],
            }
        try:
            result = getter()
        except Exception:
            return {
                "available": False,
                "state": "degraded",
                "code": "namespace_capability_probe_exception",
                "mismatches": ["namespace_capability_probe_exception"],
            }
        return negotiate_namespace_capability(result)

    @staticmethod
    def _memory_companion_namespace_payload(namespace: Any) -> tuple[dict[str, Any], str]:
        if isinstance(namespace, dict):
            payload = dict(namespace)
        else:
            try:
                serialized = namespace.to_dict()
            except Exception:
                serialized = None
            payload = dict(serialized) if isinstance(serialized, dict) else {}
        errors = validate_namespace_context(payload)
        return payload, errors[0] if errors else ""

    def _memory_companion_bind_namespace_epoch(
        self,
        bridge: Any,
        *,
        operation_id: str,
        migration_epoch: str,
        policy_version: str,
        expected_previous_epoch: str = "",
    ) -> dict[str, Any]:
        operation = str(operation_id or "").strip()
        epoch = str(migration_epoch or "").strip()
        policy = str(policy_version or "").strip()
        previous = str(expected_previous_epoch or "").strip()
        token_pattern = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
        if (
            not token_pattern.fullmatch(operation) or not token_pattern.fullmatch(epoch)
            or not token_pattern.fullmatch(policy) or (previous and not token_pattern.fullmatch(previous))
        ):
            return {"ok": False, "state": "rejected", "code": "namespace_epoch_binding_invalid"}
        capability = self._memory_companion_emotion_producer_capability(bridge)
        if capability is None:
            return {"ok": False, "state": "forbidden", "code": "producer_capability_unavailable"}
        try:
            binder = getattr(bridge, "bind_namespace_migration_epoch", None)
        except Exception:
            binder = None
        if not callable(binder):
            return {"ok": False, "state": "degraded", "code": "namespace_epoch_bind_missing"}
        try:
            result = binder(
                capability,
                operation_id=operation,
                expected_previous_epoch=previous,
                migration_epoch=epoch,
                policy_version=policy,
            )
        except Exception:
            return {"ok": False, "state": "degraded", "code": "namespace_epoch_bind_exception"}
        if not isinstance(result, dict):
            return {"ok": False, "state": "degraded", "code": "namespace_epoch_bind_invalid"}
        return dict(result)

    def _memory_companion_scoped_invoke(
        self,
        bridge: Any,
        method_name: str,
        namespace: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload, error = self._memory_companion_namespace_payload(namespace)
        if error:
            return {"ok": False, "state": "rejected", "code": error}
        negotiated = self._memory_companion_probe_namespace_capabilities(bridge)
        if negotiated.get("available") is not True:
            return {
                "ok": False,
                "state": "degraded",
                "code": str(negotiated.get("code") or "namespace_capability_unavailable")[:120],
            }
        capability = self._memory_companion_emotion_producer_capability(bridge)
        if capability is None:
            return {"ok": False, "state": "forbidden", "code": "producer_capability_unavailable"}
        try:
            method = getattr(bridge, method_name, None)
        except Exception:
            method = None
        if not callable(method):
            return {"ok": False, "state": "degraded", "code": "namespace_scoped_method_missing"}
        try:
            result = method(capability, payload, **kwargs)
        except Exception:
            return {"ok": False, "state": "degraded", "code": "namespace_scoped_call_exception"}
        if not isinstance(result, dict):
            return {"ok": False, "state": "degraded", "code": "namespace_scoped_result_invalid"}
        return dict(result)

    def _memory_companion_upsert_scoped_record(
        self, bridge: Any, namespace: Any, **kwargs: Any
    ) -> dict[str, Any]:
        return self._memory_companion_scoped_invoke(
            bridge, "upsert_scoped_record", namespace, **kwargs
        )

    def _memory_companion_read_scoped_record(
        self, bridge: Any, namespace: Any, **kwargs: Any
    ) -> dict[str, Any]:
        return self._memory_companion_scoped_invoke(
            bridge, "read_scoped_record", namespace, **kwargs
        )

    def _memory_companion_list_scoped_records(
        self, bridge: Any, namespace: Any, **kwargs: Any
    ) -> dict[str, Any]:
        return self._memory_companion_scoped_invoke(
            bridge, "list_scoped_records", namespace, **kwargs
        )

    def _memory_companion_tombstone_scoped_record(
        self, bridge: Any, namespace: Any, **kwargs: Any
    ) -> dict[str, Any]:
        return self._memory_companion_scoped_invoke(
            bridge, "tombstone_scoped_record", namespace, **kwargs
        )

    def _memory_companion_tombstone_scoped_namespace(
        self, bridge: Any, namespace: Any, **kwargs: Any
    ) -> dict[str, Any]:
        return self._memory_companion_scoped_invoke(
            bridge, "tombstone_scoped_namespace", namespace, **kwargs
        )

    def _memory_companion_tombstone_scoped_identity_scopes(
        self, bridge: Any, namespace: Any, **kwargs: Any
    ) -> dict[str, Any]:
        return self._memory_companion_scoped_invoke(
            bridge, "tombstone_scoped_identity_scopes", namespace, **kwargs
        )

    def _memory_companion_erase_scoped_group_scopes(
        self, bridge: Any, namespace: Any, **kwargs: Any
    ) -> dict[str, Any]:
        return self._memory_companion_scoped_invoke(
            bridge, "erase_scoped_group_scopes", namespace, **kwargs
        )

    def _memory_companion_erase_scoped_persona_scopes(
        self, bridge: Any, namespace: Any, **kwargs: Any
    ) -> dict[str, Any]:
        return self._memory_companion_scoped_invoke(
            bridge, "erase_scoped_persona_scopes", namespace, **kwargs
        )

    async def _memory_companion_read_profile(
        self,
        profile: str,
        *,
        query: str = "",
        limit: int = 10,
        current_date: str = "",
        current_window: str = "",
        authorized: bool = False,
    ) -> dict[str, Any]:
        """Select one named Bot Profile without sending storage filters downstream."""

        safe_profile = _single_line(profile, 80)
        base = {
            "ok": False,
            "read_only": True,
            "state": "degraded",
            "degraded": True,
            "pending": True,
            "profile": safe_profile,
            "items": [],
            "warnings": [],
        }
        bridge = self._memory_companion_bridge()
        if bridge is None:
            return {**base, "state": self._bridge_last_status.get("state", "degraded"), "error_code": "bridge_unavailable"}
        getter = getattr(bridge, "read_bot_profile", None)
        if not callable(getter):
            return {**base, "error_code": "profile_method_missing"}
        try:
            capability = self._memory_companion_emotion_producer_capability(bridge)
            if capability is None:
                return {**base, "error_code": "producer_capability_unavailable"}
            result = getter(
                safe_profile,
                query=_single_line(query, 240),
                limit=max(1, min(100, int(limit or 10))),
                current_date=_single_line(current_date, 20),
                current_window=_single_line(current_window, 40),
                authorized=bool(authorized),
                producer_capability=capability,
            )
            if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                result = await result
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="read_profile"):
                return dict(self._bridge_last_status)
            return {**base, "error_code": "profile_bridge_exception"}
        if not isinstance(result, dict):
            return {**base, "error_code": "invalid_profile_response"}
        safe_item_keys = {
            "record_id", "memory_domain", "memory_type", "subject", "date", "window",
            "occurred_at", "source_kind", "source_refs", "evidence_level", "status",
            "version", "summary", "reference",
        }
        safe_items: list[dict[str, Any]] = []
        for item in result.get("items", []) if isinstance(result.get("items"), list) else []:
            if not isinstance(item, dict):
                continue
            safe_items.append({key: item[key] for key in safe_item_keys if key in item})
        self._bridge_last_status = {
            **getattr(self, "_bridge_last_status", {}),
            "last_profile": safe_profile,
        }
        return {
            "ok": bool(result.get("ok", True)),
            "read_only": True,
            "state": _single_line(result.get("state"), 40) or "ready",
            "degraded": bool(result.get("degraded", False)),
            "pending": bool(result.get("pending", False)),
            "profile": _single_line(result.get("profile") or safe_profile, 80),
            "items": safe_items,
            "warnings": [
                _single_line(item, 160)
                for item in (result.get("warnings") or [])
                if _single_line(item, 160)
            ][:8],
        }
