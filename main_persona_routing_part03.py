# -*- coding: utf-8 -*-
"""PrivateCompanionPluginPersonaRoutingPart03Mixin。

由 tools/split_mixin_domain.py 从 main_persona_routing.py 机械抽取（7 个方法 + 0 个模块级名字 + 0 个类级赋值 / 99 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPluginPersonaRoutingMixin）。
"""
from __future__ import annotations
from .main_persona_routing_shared import Any
from .main_persona_routing_shared import P3_CONTRACT_NAME
from .main_persona_routing_shared import P3_CONTRACT_VERSION
from .main_persona_routing_shared import PERSON_CONTRACT_NAME
from .main_persona_routing_shared import PERSON_CONTRACT_VERSION
from .main_persona_routing_shared import PLUGIN_ID
from .main_persona_routing_shared import _single_line
from .main_persona_routing_shared import hashlib
from .main_persona_routing_shared import person_contract_self_check



class PrivateCompanionPluginPersonaRoutingPart03Mixin:
    """PrivateCompanionPluginPersonaRoutingPart03Mixin（从 PrivateCompanionPluginPersonaRoutingMixin 拆出）。"""


    def _unified_persona_scoped_value(self, value: Any, *, limit: int = 120) -> str:
        maximum = max(40, min(160, int(limit or 120)))
        base = _single_line(value, maximum)
        persona_domain = self._unified_persona_domain()
        if not base or not persona_domain:
            return base
        available = maximum - len(persona_domain) - 1
        if len(base) > available:
            base_hash = hashlib.sha256(base.encode("utf-8")).hexdigest()[:12]
            prefix_length = max(1, available - len(base_hash) - 1)
            base = f"{base[:prefix_length]}:{base_hash}"
        return f"{base}:{persona_domain}"

    @staticmethod
    def _unified_wire_group_scope(platform: Any, group_id: Any) -> str:
        """Match Memory's persona-neutral group scope wire contract."""
        platform_name = _single_line(platform, 40).lower()
        group_key = _single_line(group_id, 120)
        if not platform_name or not group_key:
            return ""
        return _single_line(f"group:{platform_name}:{group_key}", 80)

    def unified_person_contract_status(self) -> dict[str, Any]:
        issues = list(person_contract_self_check())
        return {
            "available": not issues,
            "state": "ready" if not issues else "degraded",
            "degraded": bool(issues),
            "contract_name": PERSON_CONTRACT_NAME,
            "contract_version": PERSON_CONTRACT_VERSION,
            "p3_contract_name": P3_CONTRACT_NAME,
            "p3_contract_version": P3_CONTRACT_VERSION,
            "warnings": issues,
            "registry": self._active_unified_person_registry().status(),
        }

    def _unified_person_registry_status(self) -> dict[str, Any]:
        return self._active_unified_person_registry().status()

    def _unified_person_event_identity(
        self,
        event: Any | None = None,
        *,
        subject_id: str = "",
        subject_namespace: str = "",
    ) -> dict[str, str]:
        sender_id = _single_line(subject_id, 160)
        if not sender_id and event is not None:
            sender_getter = getattr(self, "_event_sender_id", None)
            if callable(sender_getter):
                try:
                    sender_id = _single_line(sender_getter(event), 160)
                except Exception:
                    sender_id = ""
            if not sender_id:
                try:
                    sender_id = _single_line(event.get_sender_id(), 160)
                except Exception:
                    sender_id = ""
        platform = ""
        if event is not None:
            try:
                platform = _single_line(event.get_platform_name(), 80)
            except Exception:
                platform = ""
            if not platform:
                platform = _single_line(str(getattr(event, "unified_msg_origin", "") or "").split(":", 1)[0], 80)
        platform = platform or _single_line(getattr(self, "target_platform", ""), 80) or "unknown"
        self_id = ""
        if event is not None:
            self_getter = getattr(self, "_event_self_id", None)
            if callable(self_getter):
                try:
                    self_id = _single_line(self_getter(event), 160)
                except Exception:
                    self_id = ""
        if not self_id:
            ids = sorted(_single_line(item, 160) for item in self._known_bot_self_ids() if _single_line(item, 160))
            if len(ids) == 1:
                self_id = ids[0]
        if not sender_id or not self_id:
            return {}
        namespace = _single_line(subject_namespace, 160).lower()
        if not namespace:
            namespace = f"{platform}:bot" if sender_id == self_id else f"{platform}:user"
        adapter_instance = _single_line(
            getattr(event, "adapter_instance_id", "") if event is not None else "",
            160,
        ) or f"{platform}:{_single_line(getattr(self, 'target_platform', ''), 80) or platform}"
        return {
            "companion_instance_id": self._unified_persona_scoped_value(PLUGIN_ID),
            "bot_account_id": f"{platform}:{self_id}",
            "adapter_instance_id": adapter_instance,
            "subject_namespace": namespace,
            "platform_subject_id": sender_id,
        }

    def resolve_unified_person_identity(self, identity: dict[str, Any]) -> dict[str, Any]:
        return self._active_unified_person_registry().resolve(identity)

    def resolve_unified_person_for_event(self, event: Any | None = None) -> dict[str, Any]:
        identity = self._unified_person_event_identity(event)
        if not identity:
            return {"state": "pending", "identity_key": "", "person_id": "", "errors": ["event_identity_missing"]}
        return self.resolve_unified_person_identity(identity)
