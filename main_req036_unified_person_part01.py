# -*- coding: utf-8 -*-
"""PrivateCompanionPluginReq036UnifiedPersonPart01Mixin。

由 tools/split_mixin_domain.py 从 main_req036_unified_person.py 机械抽取（10 个方法 + 0 个模块级名字 + 0 个类级赋值 / 441 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPluginReq036UnifiedPersonMixin）。
"""
from __future__ import annotations
from .main_req036_unified_person_shared import Any
from .main_req036_unified_person_shared import NamespaceContext
from .main_req036_unified_person_shared import UnifiedPersonRegistry
from .main_req036_unified_person_shared import _now_ts
from .main_req036_unified_person_shared import _safe_int
from .main_req036_unified_person_shared import _single_line
from .main_req036_unified_person_shared import hashlib
from .main_req036_unified_person_shared import json
from .main_req036_unified_person_shared import req036_build_person_ref
from .main_req036_unified_person_shared import req036_build_profile_dto
from .main_req036_unified_person_shared import req036_capability_summary
from .main_req036_unified_person_shared import req036_private_companion_gate
from .main_req036_unified_person_shared import req036_proactive_private_gate
from .main_req036_unified_person_shared import req036_update_capabilities
from .main_req036_unified_person_shared import req036_validate_profile_dto
from .main_req036_unified_person_shared import runtime_persona_setting



class PrivateCompanionPluginReq036UnifiedPersonPart01Mixin:
    """PrivateCompanionPluginReq036UnifiedPersonPart01Mixin（从 PrivateCompanionPluginReq036UnifiedPersonMixin 拆出）。"""


    def _active_unified_person_registry(self) -> UnifiedPersonRegistry:
        """Bind identity operations to the store selected by the current persona context."""
        store = self.data
        registry = getattr(self, "unified_person_registry", None)
        if isinstance(registry, UnifiedPersonRegistry) and registry.is_bound_to(store):
            return registry
        return UnifiedPersonRegistry(store)

    def _req036_source_event_anchor(self, event: Any) -> str:
        """Build an event-local, content-free anchor when an adapter omits message IDs."""
        cached = _single_line(
            getattr(event, "_private_companion_req036_source_event_anchor", ""),
            80,
        )
        if cached:
            return cached

        raw_reader = getattr(self, "_event_raw_payload", None)
        try:
            raw = raw_reader(event) if callable(raw_reader) else {}
        except Exception:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        message_obj = getattr(event, "message_obj", None)
        metadata: dict[str, str] = {
            "origin": _single_line(getattr(event, "unified_msg_origin", ""), 200),
            "event_type": _single_line(type(event).__qualname__, 120),
            "runtime_event_ref": f"{id(event):x}",
        }
        for key in (
            "post_type",
            "message_type",
            "notice_type",
            "sub_type",
            "time",
            "timestamp",
            "user_id",
            "group_id",
            "self_id",
        ):
            value = _single_line(raw.get(key), 120)
            if value:
                metadata[f"raw_{key}"] = value
        for attr in ("time", "timestamp"):
            value = _single_line(getattr(message_obj, attr, ""), 120)
            if value:
                metadata[f"message_{attr}"] = value
            event_value = _single_line(getattr(event, attr, ""), 120)
            if event_value:
                metadata[f"event_{attr}"] = event_value

        inbound_ts = _single_line(
            getattr(event, "_private_companion_inbound_ts", ""),
            80,
        )
        if not inbound_ts:
            inbound_reader = getattr(self, "_event_inbound_activity_ts", None)
            try:
                inbound_ts = _single_line(
                    inbound_reader(event) if callable(inbound_reader) else _now_ts(),
                    80,
                )
            except Exception:
                inbound_ts = _single_line(_now_ts(), 80)
        metadata["observed_at"] = inbound_ts

        anchor = hashlib.sha256(
            json.dumps(
                metadata,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        try:
            setattr(event, "_private_companion_req036_source_event_anchor", anchor)
        except Exception:
            pass
        return anchor

    def create_unified_person(
        self,
        identity: dict[str, Any],
        *,
        profile: dict[str, Any] | None = None,
        operation_id: str = "",
    ) -> dict[str, Any]:
        registry = self._active_unified_person_registry()
        result = registry.create_or_link(
            identity,
            profile=profile,
            operation_id=operation_id,
            actor_id="companion",
        )
        self._req041_emit_identity_dual_write(
            result,
            action="create",
            operation_id=operation_id,
            registry=registry,
        )
        return result

    def get_unified_person_projection(self, person_id: str) -> dict[str, Any] | None:
        return self._active_unified_person_registry().read_projection(person_id)

    def _req036_private_gate_for_user(self, user: Any) -> dict[str, Any]:
        return req036_private_companion_gate(user)

    def _req036_migrate_configured_target_capability(self, user_id: Any, user: Any) -> bool:
        """Repair migration-only false gates for configured targets and legacy owners."""
        if not isinstance(user, dict):
            return False
        if bool(user.get("manual_disabled")):
            return False
        canonicalizer = getattr(self, "_canonical_private_user_id", None)
        identity_normalizer = getattr(self, "_normalize_private_identity_id", None)

        def normalized_identity(value: Any) -> str:
            candidate = _single_line(value, 160)
            if callable(identity_normalizer):
                try:
                    candidate = _single_line(identity_normalizer(candidate), 160) or candidate
                except Exception:
                    return ""
            if callable(canonicalizer):
                try:
                    candidate = _single_line(canonicalizer(candidate), 160)
                except Exception:
                    return ""
            return candidate

        configured_targets = getattr(self, "_configured_target_ids", None)
        target_ids: set[str] = set()
        try:
            if callable(configured_targets):
                target_ids = {
                    target_id
                    for target_id in (normalized_identity(target) for target in configured_targets())
                    if target_id
                }
        except Exception:
            return False

        stamped_subject = normalized_identity(user.get("identity_subject_id"))
        if stamped_subject:
            # Once a record has a stamped platform subject, its storage key
            # and historical transport aliases are no longer authorization
            # candidates. This prevents a target-named shadow row from
            # inheriting the target's active capability for another person.
            identity_candidates = {stamped_subject}
        else:
            identity_candidates = {
                candidate
                for candidate in (
                    normalized_identity(user_id),
                    normalized_identity(user.get("user_id")),
                )
                if candidate
            }
        # ``alias_user_ids`` are transport/history hints, never authorization
        # credentials.  Using them here allowed a renamed or migrated identity
        # to reopen active permission for another stable user.
        configured_match = bool(target_ids.intersection(identity_candidates))

        # A bare numeric/openid target must not grant the same identifier on a
        # different platform. Adapter instance changes inside the configured
        # platform remain compatible and are handled by the scoped profile.
        if configured_match:
            platform_normalizer = getattr(self, "_normalize_platform_kind", None)
            configured_platform_raw = _single_line(getattr(self, "target_platform", ""), 80).lower()
            observed_platform = _single_line(user.get("identity_platform_kind"), 40).lower()
            configured_platform = ""
            if callable(platform_normalizer) and configured_platform_raw:
                try:
                    configured_platform = _single_line(platform_normalizer(configured_platform_raw), 40).lower()
                except Exception:
                    configured_platform = ""
            if (
                configured_platform
                and configured_platform != "generic"
                and observed_platform
                and observed_platform != "generic"
                and configured_platform != observed_platform
            ):
                configured_match = False
            elif configured_platform == "generic" and configured_platform_raw:
                observed_adapter = _single_line(user.get("identity_adapter_instance_id"), 120).lower()
                if observed_adapter and configured_platform_raw not in {observed_adapter, observed_adapter.split(":", 1)[0]}:
                    configured_match = False

        owner_match = False
        if not configured_match:
            return False

        capabilities = user.get("unified_profile_capabilities")
        if isinstance(capabilities, dict) and capabilities.get("private_companion_enabled") is True:
            return False
        grant_source = (
            _single_line(capabilities.get("grant_source"), 80).lower()
            if isinstance(capabilities, dict)
            else ""
        )
        explicit_sources = {
            "admin",
            "administrator",
            "manual",
            "page_administrator",
            "page_administrator_update",
        }
        if grant_source in explicit_sources or "administrator" in grant_source:
            return False

        # Audit provenance is authoritative even when an older writer failed
        # to keep grant_source synchronized.
        audit = user.get("unified_profile_capability_audit")
        if isinstance(audit, list):
            for entry in reversed(audit[-64:]):
                if not isinstance(entry, dict):
                    continue
                changed = entry.get("changed")
                private_change = changed.get("private_companion_enabled") if isinstance(changed, dict) else None
                if not isinstance(private_change, dict) or "to" not in private_change:
                    continue
                if private_change.get("to") is True:
                    break
                actor = _single_line(entry.get("actor_id"), 80).lower()
                reason = _single_line(entry.get("reason_code"), 80).lower()
                compatibility_change = any(
                    token in f"{actor} {reason}"
                    for token in ("migration", "compatibility", "reconciliation", "startup")
                )
                if private_change.get("to") is False and not compatibility_change:
                    return False
                break

        repairable_sources = {
            "",
            "default_closed",
            "group_observation",
            "legacy_effective_migration",
            "legacy_configured_target_migration",
            "owner_default_enabled",
        }
        if (
            isinstance(capabilities, dict)
            and grant_source not in repairable_sources
            and not bool(user.get("manual_enabled"))
        ):
            return False

        proactive_enabled = bool(
            owner_match
            or (isinstance(capabilities, dict) and capabilities.get("proactive_private_enabled") is True)
            or user.get("proactive_private_enabled") is True
            or _safe_int(user.get("proactive_daily_limit"), 0, 0) > 0
        )
        source = (
            "owner_capability_reconciliation"
            if owner_match and not configured_match
            else "configured_target_capability_reconciliation"
            if isinstance(capabilities, dict)
            else "legacy_configured_target_migration"
        )
        result = req036_update_capabilities(
            user,
            {
                "private_companion_enabled": True,
                "proactive_private_enabled": proactive_enabled,
            },
            actor_authorized=True,
            grant_source=source,
            actor_id="compatibility_migration",
            target_identity=normalized_identity(user.get("identity_subject_id")) or normalized_identity(user_id),
            reason_code=source,
        )
        return bool(result.get("ok"))

    def _req036_capability_summary_for_user(self, user: Any) -> dict[str, Any]:
        bridge = self._memory_companion_bridge()
        portrait_backend_available = callable(getattr(bridge, "read_unified_profile_portrait", None))
        return req036_capability_summary(
            user,
            global_portrait_mode=runtime_persona_setting(self, 'portrait_global_mode', "disabled"),
            portrait_backend_available=portrait_backend_available,
        )

    def _req036_proactive_private_allowed(self, user: Any) -> bool:
        return bool(req036_proactive_private_gate(user).get("allowed"))

    def _req036_update_capabilities(
        self,
        user: dict[str, Any],
        changes: dict[str, Any],
        *,
        actor_id: str = "page_administrator",
        target_identity: str = "",
        reason_code: str = "administrator_update",
    ) -> dict[str, Any]:
        requested_mode = _single_line(changes.get("portrait_mode"), 40).lower() if isinstance(changes, dict) else ""
        if requested_mode and requested_mode not in {"disabled", "off", "follow_global"}:
            bridge = self._memory_companion_bridge()
            if not callable(getattr(bridge, "read_unified_profile_portrait", None)):
                return {
                    "ok": False,
                    "code": "memory_companion_required",
                    "message": "需要安装并启用 MemoryCompanion",
                    "capabilities": self._req036_capability_summary_for_user(user),
                }
        return req036_update_capabilities(
            user,
            changes,
            actor_authorized=True,
            grant_source="administrator",
            actor_id=actor_id,
            target_identity=target_identity,
            reason_code=reason_code,
        )

    def _req036_attach_unified_profile_context(
        self,
        event: Any,
        *,
        user: dict[str, Any] | None = None,
        group_id: str = "",
        source: str = "observation",
    ) -> dict[str, Any]:
        """Attach the smallest exact-person context for Memory's read-only use."""
        identity = self._unified_person_event_identity(event)
        if not identity:
            return {"state": "identity_pending", "code": "identity_pending"}
        resolution = self.resolve_unified_person_identity(identity)
        if resolution.get("state") != "resolved":
            profile = user if isinstance(user, dict) else {}
            created = self.create_unified_person_for_event(
                event,
                operation_id=f"req036.{source}:{str(resolution.get('identity_key') or '')[-24:]}",
                profile={
                    "display_name": (
                        _single_line(profile.get("nickname"), 80) if not group_id else ""
                    ),
                    "preferred_address": (
                        _single_line(profile.get("nickname"), 40) if not group_id else ""
                    ),
                    "style": _single_line(profile.get("style"), 40) if not group_id else "",
                    "profile_origin": _single_line(profile.get("profile_origin"), 60),
                    "auto_profile_created": bool(profile.get("auto_profile_created", False)),
                    "affinity_score": _safe_int(profile.get("relationship_score"), 0, -1200, 1200),
                    "owner_mode": "owner" if _single_line(profile.get("relationship_role"), 40) == "owner" else "not_owner",
                    "relation_policy_id": _single_line(profile.get("relationship_mode"), 40) or "default_friend",
                },
            )
            if created.get("state") != "resolved":
                return {"state": "identity_pending", "code": str(created.get("code") or "identity_pending")}
            resolution = self.resolve_unified_person_identity(identity)
        projection = resolution.get("projection") if isinstance(resolution.get("projection"), dict) else None
        if not isinstance(projection, dict):
            return {"state": "identity_pending", "code": "projection_missing"}
        person_id = _single_line(projection.get("person_id"), 80)
        if isinstance(user, dict) and person_id:
            user["unified_person_id"] = person_id
            user["unified_profile_projection_revision"] = int(projection.get("projection_revision") or 1)
            if not group_id:
                private_facts = {
                    "style": _single_line(user.get("style"), 40),
                    "profile_origin": _single_line(user.get("profile_origin"), 60),
                    "auto_profile_created": bool(user.get("auto_profile_created", False)),
                }
                private_name = _single_line(user.get("nickname"), 80)
                if private_name:
                    private_facts["display_name"] = private_name
                    private_facts["preferred_address"] = private_name[:40]
                fact_signature = hashlib.sha256(
                    json.dumps(private_facts, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest()[:32]
                self._req041_update_unified_profile_facts(
                    user,
                    private_facts,
                    operation_id=f"req041-private-profile-observation-{person_id[-16:]}-{fact_signature}",
                    actor_id="private_observation",
                    schedule_save=True,
                )
        group_scope = ""
        if group_id and person_id:
            platform = _single_line(identity.get("subject_namespace"), 80).split(":", 1)[0]
            group_scope = self._unified_wire_group_scope(platform, group_id)
            if group_scope:
                self._active_unified_person_registry().upsert_group_overlay(
                    person_id,
                    group_scope,
                    {
                        "alias": _single_line((user or {}).get("nickname"), 80),
                        "source": "group_observation",
                        "public": True,
                    },
                    operation_id=f"req036.overlay:{person_id[-12:]}:{_single_line(group_id, 40)}",
                    actor_id="companion",
                )
        if person_id:
            event_id = _single_line(self._event_message_id(event), 120)
            event_anchor = event_id or self._req036_source_event_anchor(event)
            source_fingerprint = hashlib.sha256(
                f"req036:{source}:{group_scope or 'private'}:{event_anchor}".encode("utf-8", errors="ignore")
            ).hexdigest()
            self._active_unified_person_registry().record_identity_source_event(
                person_id,
                _single_line(projection.get("resolved_identity_key"), 160),
                group_scope or "private",
                source_fingerprint,
                operation_id=f"req036.source:{source}:{source_fingerprint[:24]}",
            )
        portrait_namespace_getter = getattr(self, "_req041_scoped_context_for_user", None)
        if callable(portrait_namespace_getter) and isinstance(user, dict):
            try:
                portrait_namespace = portrait_namespace_getter(
                    user,
                    kind="group_member" if group_id else "private",
                    group_id=group_id,
                    purpose="profile_read",
                )
            except Exception:
                portrait_namespace = None
            if isinstance(portrait_namespace, NamespaceContext) and not portrait_namespace.errors():
                try:
                    setattr(
                        event,
                        "private_companion_namespace_context",
                        portrait_namespace.to_dict(),
                    )
                except Exception:
                    pass
        dto = req036_build_profile_dto(
            person_ref=req036_build_person_ref(projection),
            identity_summary={"display_name": _single_line((user or {}).get("nickname"), 80)},
            expression_summary={
                "relationship_score": _safe_int((user or {}).get("relationship_score"), 0, -1200, 1200),
                "relationship_role": _single_line((user or {}).get("relationship_role"), 40) or "friend",
            },
            capability_summary=self._req036_capability_summary_for_user(user),
            context_overlays={"group_scope": group_scope} if group_scope else {},
            bridge_status={"state": "ready", "source": "companion"},
        )
        errors = req036_validate_profile_dto(dto)
        if errors:
            return {"state": "degraded", "code": "bridge_contract_mismatch", "errors": errors}
        try:
            setattr(event, "private_companion_unified_profile_context", dto)
        except Exception:
            return {"state": "degraded", "code": "bridge_unavailable"}
        return {"state": "profile_exact", "code": "profile_exact", "dto": dto, "person_id": person_id}
