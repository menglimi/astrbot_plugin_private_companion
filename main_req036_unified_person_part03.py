# -*- coding: utf-8 -*-
"""PrivateCompanionPluginReq036UnifiedPersonPart03Mixin。

由 tools/split_mixin_domain.py 从 main_req036_unified_person.py 机械抽取（7 个方法 + 0 个模块级名字 + 0 个类级赋值 / 448 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPluginReq036UnifiedPersonMixin）。
"""
from __future__ import annotations

from .main_req036_unified_person_shared import filter
from .main_req036_unified_person_shared import logger
from .main_req036_unified_person_shared import Any
from .main_req036_unified_person_shared import AstrMessageEvent
from .main_req036_unified_person_shared import P3_CONTRACT_NAME
from .main_req036_unified_person_shared import P3_CONTRACT_VERSION
from .main_req036_unified_person_shared import PERSON_CONTRACT_NAME
from .main_req036_unified_person_shared import PERSON_CONTRACT_VERSION
from .main_req036_unified_person_shared import PLACEMENT_DYNAMIC_SYSTEM
from .main_req036_unified_person_shared import PLUGIN_ID
from .main_req036_unified_person_shared import ProviderRequest
from .main_req036_unified_person_shared import SAFE_CONFINEMENT_REPLY
from .main_req036_unified_person_shared import _multi_persona_event_context
from .main_req036_unified_person_shared import _single_line
from .main_req036_unified_person_shared import build_context
from .main_req036_unified_person_shared import build_p4_shadow
from .main_req036_unified_person_shared import compose_reply_temperature
from .main_req036_unified_person_shared import decide_live_request
from .main_req036_unified_person_shared import project_context
from .main_req036_unified_person_shared import re
from .main_req036_unified_person_shared import reply_temperature_prompt_section



class PrivateCompanionPluginReq036UnifiedPersonPart03Mixin:
    """PrivateCompanionPluginReq036UnifiedPersonPart03Mixin（从 PrivateCompanionPluginReq036UnifiedPersonMixin 拆出）。"""


    def build_unified_person_context(self, event: Any | None = None) -> dict[str, Any]:
        identity = self._unified_person_event_identity(event)
        resolution = self.resolve_unified_person_identity(identity) if identity else {
            "state": "pending", "identity_key": "", "person_id": "", "errors": ["event_identity_missing"],
        }
        state = str(resolution.get("state") or "pending")
        projection = resolution.get("projection") if isinstance(resolution.get("projection"), dict) else None
        scope = "unknown"
        if event is not None:
            try:
                scope = "private" if bool(event.is_private_chat()) else "group"
            except Exception:
                scope = "unknown"
        platform = str(identity.get("subject_namespace") or "").split(":", 1)[0] if identity else ""
        group_id = ""
        if scope == "group":
            group_getter = getattr(self, "_extract_group_id_from_event", None)
            if callable(group_getter):
                try:
                    group_id = _single_line(group_getter(event), 160)
                except Exception:
                    group_id = ""
        group_scope = self._unified_wire_group_scope(platform, group_id)
        group_overlay = None
        if state == "resolved" and group_scope and resolution.get("person_id"):
            group_overlay = self._active_unified_person_registry().read_group_overlay(
                str(resolution.get("person_id") or ""), group_scope
            )
        person_payload = {
            key: projection.get(key)
            for key in (
                "person_id", "identity_assurance", "profile_status", "relation_policy_id",
                "relation_label", "owner_mode", "affinity_band", "projection_revision",
                "group_overlay_ref",
            )
            if projection is not None and projection.get(key) not in (None, "", [], {})
        }
        p3 = build_context(
            persona={"companion_instance_id": self._unified_persona_scoped_value(PLUGIN_ID)},
            runtime={"platform": platform, "scope": scope, "adapter_instance_id": identity.get("adapter_instance_id", "")},
            person=person_payload,
            scene={
                "scope": scope,
                "group_scope": group_scope,
                "group_id_present": bool(group_id),
                "group_overlay_revision": group_overlay.get("revision") if isinstance(group_overlay, dict) else 0,
            },
            bridge_available=True,
        )
        if state != "resolved":
            p3["state"] = state if state in {"pending", "invalid", "degraded", "legacy_local"} else "degraded"
            p3["warnings"] = list(p3.get("warnings") or []) + list(resolution.get("errors") or [f"person_{state}"])[:8]
            person_slot = p3.get("slots", {}).get("person")
            if isinstance(person_slot, dict):
                person_slot["state"] = p3["state"]
        p3 = project_context(p3)
        p4 = build_p4_shadow(
            source_kind="companion",
            target_kind="memory_bridge",
            authority="companion",
            reason_code="projection_ready" if state == "resolved" else f"person_{state}",
            safe_reference=str(resolution.get("person_id") or ""),
            operation_id=f"person.context:{str(resolution.get('identity_key') or 'pending')[-24:]}",
            status="shadow" if state == "resolved" else "degraded",
        )
        return {
            "contract_name": PERSON_CONTRACT_NAME,
            "contract_version": PERSON_CONTRACT_VERSION,
            "p3_contract_name": P3_CONTRACT_NAME,
            "p3_contract_version": P3_CONTRACT_VERSION,
            "state": state,
            "identity": {"identity_key": str(resolution.get("identity_key") or ""), "person_id": str(resolution.get("person_id") or "")},
            "projection": projection,
            "p3": p3,
            "p4_shadow": p4,
            "scope": scope,
            "group_scope": group_scope,
        }

    async def archive_unified_person(
        self,
        person_id: str,
        *,
        operation_id: str,
        confirmation_token: str = "",
        dry_run: bool = True,
        actor_id: str = "page_administrator",
        reason_code: str = "person_archive",
    ) -> dict[str, Any]:
        """Run the request-bound, resumable person archive saga."""
        clean_person = _single_line(person_id, 80)
        clean_operation = _single_line(operation_id, 120)
        if not clean_person or not clean_operation or type(dry_run) is not bool:
            return {"ok": False, "state": "invalid", "code": "invalid_request"}
        async with self._data_lock:
            registry = self._active_unified_person_registry()
            prepared = registry.prepare_person_archive(
                clean_person, operation_id=clean_operation,
                actor_id=actor_id, reason_code=reason_code,
            )
            if not prepared.get("ok"):
                return prepared
            self._req041_persist_archive_saga_locked(
                sections={"unified_person"},
            )
            if prepared.get("code") == "person_archived":
                subjects = registry.archived_identity_subjects(clean_person)
                removed = self._req041_erase_person_private_auxiliary_locked(clean_person, subjects)
                if sum(removed.values()) > 0:
                    self._req041_persist_archive_saga_locked(
                        sections={
                            name
                            for name, count in removed.items()
                            if int(count or 0) > 0
                        },
                    )
                return prepared
            if dry_run:
                return prepared
            if not confirmation_token or confirmation_token != prepared.get("confirmation_token"):
                return {
                    "ok": False, "state": "prepared", "code": "archive_confirmation_mismatch",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            archive_available = getattr(self, "_req041_scoped_archive_available", None)
            if callable(archive_available) and not archive_available():
                return {
                    "ok": False, "state": "prepared", "code": "scoped_identity_archive_unavailable",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            confirmed = registry.confirm_person_archive(
                clean_person, clean_operation, confirmation_token,
                actor_id=actor_id, reason_code=reason_code,
            )
            if not confirmed.get("ok"):
                return confirmed
            self._req041_persist_archive_saga_locked(
                sections={"unified_person"},
            )
            context = self._req041_scoped_private_context_for_person(clean_person)
            synchronizer = getattr(self, "req041_scoped_projection_sync", None)
            relationship_store = getattr(self, "req041_relationship_store", None)
            outbox = getattr(self, "req041_migration_outbox", None)
            if context is None or synchronizer is None:
                return {
                    "ok": False, "state": "prepared", "code": "scoped_identity_archive_unavailable",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            if relationship_store is None or not callable(getattr(relationship_store, "tombstone_account", None)):
                synchronizer.mark_dirty()
                return {
                    "ok": False, "state": "prepared", "code": "relationship_archive_unavailable",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            if outbox is None or not callable(getattr(outbox, "retire_streams", None)):
                synchronizer.mark_dirty()
                return {
                    "ok": False, "state": "prepared", "code": "archive_outbox_unavailable",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            try:
                stream_receipt = outbox.retire_streams(
                    [f"identity:{clean_person}", f"relationship:{clean_person}"],
                    synchronizer.migration_epoch,
                    operation_id=f"req041-streams-{clean_operation}", reason_code=reason_code,
                )
            except Exception as exc:
                synchronizer.mark_dirty()
                return {
                    "ok": False, "state": "prepared",
                    "code": _single_line(exc, 120) or "archive_stream_retirement_failed",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            scoped_receipt = synchronizer.archive_identity_scopes(
                context, operation_id=f"req041-scoped-{clean_operation}", reason_code=reason_code,
            )
            if not scoped_receipt.get("ok"):
                return {
                    "ok": False, "state": "prepared",
                    "code": str(scoped_receipt.get("code") or "scoped_identity_archive_failed")[:120],
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            try:
                relationship_receipt = relationship_store.tombstone_account(
                    context, operation_id=f"req041-relationship-{clean_operation}",
                    reason_code=reason_code, actor="administrator",
                )
            except Exception as exc:
                return {
                    "ok": False, "state": "prepared",
                    "code": _single_line(exc, 120) or "relationship_archive_failed",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            legacy_subjects = [
                _single_line(item.get("identity_subject_id") or item.get("user_id"), 160)
                for item in (self.data.get("users") or {}).values()
                if isinstance(item, dict)
                and _single_line(item.get("unified_person_id"), 80) == clean_person
            ] if isinstance(self.data.get("users"), dict) else []
            auxiliary_counts = self._req041_erase_person_private_auxiliary_locked(
                clean_person, legacy_subjects,
            )
            result = registry.finalize_person_archive(
                clean_person, clean_operation, confirmation_token,
                scoped_receipt, relationship_receipt, stream_receipt,
                actor_id=actor_id, reason_code=reason_code,
            )
            if result.get("changed"):
                result["auxiliary_removed_record_count"] = sum(auxiliary_counts.values())
                coordinator = getattr(self, "req041_migration_coordinator", None)
                rollback = getattr(coordinator, "rollback_identity", None)
                if callable(rollback):
                    rollback(clean_person, reason_code="person_archived")
                self._req041_persist_archive_saga_locked(
                    sections={
                        "unified_person",
                        *(
                            name
                            for name, count in auxiliary_counts.items()
                            if int(count or 0) > 0
                        ),
                    },
                )
            return result

    async def purge_unified_person(
        self,
        person_id: str,
        *,
        operation_id: str,
        confirmation_token: str = "",
        dry_run: bool = True,
        actor_id: str = "page_administrator",
        reason_code: str = "person_delete",
    ) -> dict[str, Any]:
        clean_person = _single_line(person_id, 80)
        clean_operation = _single_line(operation_id, 120)
        if not clean_person or not clean_operation or type(dry_run) is not bool:
            return {"ok": False, "state": "invalid", "code": "invalid_request"}
        async with self._data_lock:
            registry = self._active_unified_person_registry()
            prepared = registry.prepare_person_purge(
                clean_person, operation_id=clean_operation,
                actor_id=actor_id, reason_code=reason_code,
            )
            if not prepared.get("ok"):
                return prepared
            self._req041_persist_archive_saga_locked(
                sections={"unified_person"},
            )
            if prepared.get("code") == "person_purged":
                return prepared
            if dry_run:
                return prepared
            if not confirmation_token or confirmation_token != prepared.get("confirmation_token"):
                return {
                    "ok": False, "state": "prepared", "code": "purge_confirmation_mismatch",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            confirmed = registry.confirm_person_purge(
                clean_person, clean_operation, confirmation_token,
                actor_id=actor_id, reason_code=reason_code,
            )
            if not confirmed.get("ok"):
                return confirmed
            self._req041_persist_archive_saga_locked(
                sections={"unified_person"},
            )
            subjects = registry.archived_identity_subjects(clean_person)
            if int(prepared.get("detached_identity_count") or 0) > 0 and not subjects:
                return {
                    "ok": False, "state": "confirmed", "code": "purge_identity_subjects_invalid",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            outbox = getattr(self, "req041_migration_outbox", None)
            synchronizer = getattr(self, "req041_scoped_projection_sync", None)
            if outbox is None or synchronizer is None or not callable(getattr(outbox, "purge_retired_streams", None)):
                return {
                    "ok": False, "state": "confirmed", "code": "purge_outbox_unavailable",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            try:
                outbox_receipt = outbox.purge_retired_streams(
                    [f"identity:{clean_person}", f"relationship:{clean_person}"],
                    synchronizer.migration_epoch,
                    operation_id=f"req041-purge-streams-{clean_operation}", reason_code=reason_code,
                )
            except Exception as exc:
                return {
                    "ok": False, "state": "confirmed",
                    "code": _single_line(exc, 120) or "purge_outbox_failed",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            auxiliary_counts = self._req041_erase_person_private_auxiliary_locked(clean_person, subjects)
            legacy_changed_sections: set[str] = set()
            legacy_counts = self._req041_purge_legacy_person_locked(
                clean_person,
                subjects,
                changed_sections=legacy_changed_sections,
            )
            result = registry.finalize_person_purge(
                clean_person, clean_operation, confirmation_token, outbox_receipt,
                actor_id=actor_id, reason_code=reason_code,
            )
            if result.get("changed"):
                result["legacy_removed_record_count"] = int(legacy_counts.get("records") or 0)
                result["auxiliary_removed_record_count"] = sum(auxiliary_counts.values())
                if legacy_changed_sections:
                    # Legacy identity records may live under unregistered roots.
                    # This administrator-confirmed purge is therefore an explicit
                    # full-store repair boundary rather than an implicit fallback.
                    self._req041_persist_archive_saga_locked(
                        full_scope="admin_import_export",
                    )
                else:
                    self._req041_persist_archive_saga_locked(
                        sections={"unified_person"}
                        | {
                            name
                            for name, count in auxiliary_counts.items()
                            if int(count or 0) > 0
                        },
                    )
            return result

    def _text_looks_like_relation_lookup_question(self, text: str) -> bool:
        cleaned = _single_line(text, 180)
        if not cleaned:
            return False
        compact = re.sub(r"\s+", "", cleaned)
        has_query_word = any(
            token in compact
            for token in (
                "认识吗",
                "认得吗",
                "知道吗",
                "是谁",
                "哪位",
                "什么人",
                "这个人",
                "这人",
                "那个人",
                "那人",
                "qq号",
                "QQ号",
                "QQ",
                "qq",
            )
        )
        if re.search(r"\d{5,12}", compact):
            return has_query_word
        if has_query_word:
            try:
                return bool(self._select_worldbook_member_profiles_for_private_text(compact, limit=1))
            except Exception:
                return True
        return False

    async def _private_reply_only_relation_lookup_text(self, event: AstrMessageEvent) -> str:
        try:
            message_id, raw_message = await self._reply_raw_message_for_event(event)
        except Exception as exc:
            logger.info("私聊引用关系网问题预读取失败: %s", _single_line(exc, 120))
            return ""
        if raw_message is None:
            return ""
        try:
            info = self._extract_reply_rich_card_info(raw_message)
        except Exception as exc:
            logger.info("私聊引用关系网问题解析失败: message_id=%s error=%s", message_id or "-", _single_line(exc, 120))
            return ""
        texts = [_single_line(item, 120) for item in info.get("texts", []) if _single_line(item, 120)]
        if not texts:
            return ""
        quoted_text = _single_line("；".join(texts[:3]), 180)
        if not self._text_looks_like_relation_lookup_question(quoted_text):
            return ""
        logger.info(
            "私聊纯引用关系网问题已补触发文本: message_id=%s text=%s",
            message_id or "-",
            _single_line(quoted_text, 120),
        )
        return quoted_text

    @filter.on_llm_request(priority=220000)
    @_multi_persona_event_context
    async def guard_req036_private_capability_before_llm(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Compatibility hook retained after removing passive private-chat gating."""
        return

    @filter.on_llm_request(priority=-30000)
    @_multi_persona_event_context
    async def enforce_p4_live_confinement_before_enrichment(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Block only an already-resolved private person with an invalid or active P4 state."""
        if self is None or req is None or not bool(getattr(self, "enabled", False)):
            return
        state = self._p4_live_state_for_event(event)
        decision = decide_live_request(state)
        if decision.get("decision") == "skip":
            return
        if decision.get("decision") == "block":
            # This runs before P5/Memory and the enrichment collectors. Leave
            # no request route to original prompt, tool, bridge, or context data.
            for attribute, value in (
                ("system_prompt", SAFE_CONFINEMENT_REPLY),
                ("prompt", ""),
                ("contexts", []),
                ("extra_user_content_parts", []),
                ("func_tool", None),
                ("tools", []),
                ("images", []),
                ("image_urls", []),
            ):
                try:
                    setattr(req, attribute, value)
                except Exception:
                    pass
            try:
                setattr(event, "private_companion_p4_blocked", True)
                setattr(event, "private_companion_p4_block_code", decision.get("code", "p4_state_invalid"))
            except Exception:
                pass
            await self._reply(event, SAFE_CONFINEMENT_REPLY)
            event.stop_event()
            return
        temperature = compose_reply_temperature(
            decision.get("warmth_projection", {}).get("tier"),
            **self._bounded_p4_reply_temperature_signals(event),
        )
        try:
            setattr(req, "_private_companion_reply_temperature", temperature)
        except Exception:
            pass
        if hasattr(req, "system_prompt"):
            section = reply_temperature_prompt_section(temperature)
            self._materialize_conversation_system_block(
                req,
                section=section,
                marker="[Reply boundary]",
                priority=5,
                placement=PLACEMENT_DYNAMIC_SYSTEM,
            )
