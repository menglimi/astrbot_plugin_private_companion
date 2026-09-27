# -*- coding: utf-8 -*-
"""PrivateCompanionPluginReq041Part02Mixin。

由 tools/split_mixin_domain.py 从 main_req041.py 机械抽取（21 个方法 + 0 个模块级名字 + 0 个类级赋值 / 569 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPluginReq041Mixin）。
"""
from __future__ import annotations
from .main_req041_shared import Any
from .main_req041_shared import AssurancePolicy
from .main_req041_shared import Collection
from .main_req041_shared import NamespaceContext
from .main_req041_shared import _now_ts
from .main_req041_shared import _set_into_config
from .main_req041_shared import _single_line
from .main_req041_shared import deepcopy
from .main_req041_shared import hashlib
from .main_req041_shared import overlay_private_runtime_view
from .main_req041_shared import scoped_group_ref
from .main_req041_shared import scoped_persona_ref
from .main_req041_shared import uuid



class PrivateCompanionPluginReq041Part02Mixin:
    """PrivateCompanionPluginReq041Part02Mixin（从 PrivateCompanionPluginReq041Mixin 拆出）。"""


    def _req041_scoped_private_context_for_person(
        self,
        person_id: str,
        *,
        purpose: str = "memory_write",
    ) -> NamespaceContext | None:
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        if synchronizer is None:
            return None
        clean_person = _single_line(person_id, 80)
        if not clean_person:
            return None
        registry = self._active_unified_person_registry()
        resolution = registry.formal_namespace_for_person(
            clean_person, kind="private",
            policy_version=synchronizer.policy_version,
            migration_epoch=synchronizer.migration_epoch,
            purpose=purpose,
        )
        raw = resolution.get("context") if isinstance(resolution, dict) else None
        if not resolution.get("ok") or not isinstance(raw, dict):
            return None
        context = NamespaceContext(
            kind="private", persona_id=scoped_persona_ref(self._active_persona_scope()),
            identity_id=clean_person, group_id="",
            assurance=str(raw.get("assurance") or "verified"), profile_status="active",
            policy_version=synchronizer.policy_version,
            migration_epoch=synchronizer.migration_epoch,
        )
        return context if not context.errors() else None

    @staticmethod
    def _req041_person_private_aux_key(person_id: str) -> str:
        """Return a stable opaque key for persona-local person-private helpers."""
        clean_person = str(person_id or "").strip()
        if not clean_person:
            return ""
        digest = hashlib.sha256(f"req041-person-private-aux:{clean_person}".encode("utf-8")).hexdigest()
        return f"person:{digest}"

    def _req041_reality_private_binding(
        self,
        user_id: Any,
        *,
        purpose: str = "memory_read",
    ) -> dict[str, Any]:
        """Resolve one mobile/reality operation to a reconciled private person scope."""
        normalized = _single_line(user_id, 120)
        users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else None
        user = users.get(normalized) if normalized and isinstance(users, dict) else None
        if not isinstance(user, dict):
            return {"ok": False, "code": "private_user_not_managed"}
        context = self._req041_scoped_context_for_user(user, kind="private", purpose=purpose)
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        if context is None or synchronizer is None:
            return {"ok": False, "code": "formal_private_identity_required"}
        projection = synchronizer.read_projection(context)
        if not isinstance(projection, dict) or projection.get("ok") is not True:
            return {
                "ok": False,
                "code": str(projection.get("code") or "scoped_projection_not_reconciled")[:120]
                if isinstance(projection, dict) else "scoped_projection_not_reconciled",
            }
        person_id = _single_line(getattr(context, "identity_id", ""), 80)
        store_key = self._req041_person_private_aux_key(person_id)
        if not person_id or not store_key:
            return {"ok": False, "code": "formal_private_identity_required"}
        snapshot = self._req041_relationship_snapshot_view(
            user, source=f"reality_{_single_line(purpose, 40) or 'memory'}",
        )
        return {
            "ok": True,
            "code": "formal_private_identity_bound",
            "context": context,
            "person_id": person_id,
            "store_key": store_key,
            "subject_ref": store_key,
            "user": snapshot if isinstance(snapshot, dict) else user,
        }

    def _req041_erase_person_private_auxiliary_locked(
        self,
        person_id: str,
        subjects: list[str] | tuple[str, ...] = (),
    ) -> dict[str, int]:
        """Erase canonical and exact legacy auxiliary nodes for one person."""
        canonical = self._req041_person_private_aux_key(person_id)
        keys = {canonical} if canonical else set()
        keys.update(_single_line(item, 160) for item in subjects if _single_line(item, 160))
        counts = {"place_cognitive_maps": 0, "reality_touch_outputs": 0}
        for root_name in tuple(counts):
            root = self.data.get(root_name) if isinstance(self.data, dict) else None
            if not isinstance(root, dict):
                continue
            for key in keys:
                if key in root:
                    root.pop(key, None)
                    counts[root_name] += 1
        return counts

    def _req041_scoped_group_context(
        self,
        group_id: str,
        *,
        purpose: str = "rule_write",
    ) -> NamespaceContext | None:
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        raw_group = _single_line(group_id, 160)
        if synchronizer is None or not raw_group:
            return None
        persona_id = scoped_persona_ref(self._active_persona_scope())
        context = NamespaceContext(
            kind="group_shared", persona_id=persona_id, identity_id="",
            group_id=scoped_group_ref(persona_id, raw_group), assurance="verified",
            profile_status="active", policy_version=synchronizer.policy_version,
            migration_epoch=synchronizer.migration_epoch,
        )
        policy = AssurancePolicy()
        decision = policy.authorize(context, purpose)
        return context if decision.allowed and not context.errors() else None

    def _req041_persona_global_context(
        self, *, purpose: str = "rule_read"
    ) -> NamespaceContext | None:
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        if synchronizer is None:
            return None
        context = NamespaceContext(
            kind="persona_global", persona_id=scoped_persona_ref(self._active_persona_scope()),
            identity_id="", group_id="", assurance="verified", profile_status="active",
            policy_version=synchronizer.policy_version,
            migration_epoch=synchronizer.migration_epoch,
        )
        decision = AssurancePolicy().authorize(context, purpose)
        return context if decision.allowed and not context.errors() else None

    def _req041_erase_scoped_group_data(
        self,
        group_id: str,
        *,
        operation_id: str = "",
        persona_id: str = "",
    ) -> dict[str, Any]:
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        if synchronizer is None:
            if self._req041_group_remote_cleanup_required():
                return {"ok": False, "state": "degraded", "code": "scoped_group_erase_unavailable"}
            return {"ok": True, "state": "not_required", "code": "scoped_group_erase_not_required", "count": 0}
        raw_group = _single_line(group_id, 160)
        safe_persona = _single_line(persona_id, 80) or scoped_persona_ref(self._active_persona_scope())
        group_ref = scoped_group_ref(safe_persona, raw_group)
        context = NamespaceContext(
            kind="group_shared", persona_id=safe_persona, identity_id="", group_id=group_ref,
            assurance="verified", profile_status="active",
            policy_version=synchronizer.policy_version,
            migration_epoch=synchronizer.migration_epoch,
        )
        if not raw_group or context.errors():
            return {"ok": False, "state": "rejected", "code": "scoped_group_erase_context_invalid"}
        clean_operation = _single_line(operation_id, 120) or "req041-group-reset-" + uuid.uuid4().hex
        return synchronizer.erase_group_scopes(
            context, operation_id=clean_operation, reason_code="group_reset",
        )

    def _req041_memory_scope_was_bound(self) -> bool:
        """Return whether this persona has ever had a remote scoped bridge bound."""
        data = getattr(self, "data", None)
        state = data.get("_req041_memory_scope_state") if isinstance(data, dict) else None
        return isinstance(state, dict) and bool(state.get("ever_bound"))

    def _req041_mark_memory_scope_bound(self) -> None:
        """Persist remote-binding history so a later outage remains fail-closed."""
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return
        state = data.get("_req041_memory_scope_state")
        if not isinstance(state, dict):
            state = {}
            data["_req041_memory_scope_state"] = state
        if state.get("ever_bound"):
            return
        state["ever_bound"] = True
        state["bound_at"] = _now_ts()
        scheduler = getattr(self, "_schedule_data_save", None)
        if callable(scheduler):
            try:
                scheduler(delay=0.35)
            except Exception:
                pass

    def _req041_group_remote_cleanup_required(self) -> bool:
        """Decide whether deleting a group must wait for remote scope erasure.

        A fresh runtime with no remote bridge has never published scoped records,
        so local group cleanup is safe. Once a bridge was bound, an outage must
        continue to fail closed because the remote side may retain group data.
        Legacy migrations also remain fail-closed until their remote cleanup is
        available.
        """
        status = getattr(self, "req041_migration_status", None)
        if not isinstance(status, dict):
            return False
        if status.get("required"):
            if (
                not status.get("memory_bound")
                and not self._req041_memory_scope_was_bound()
            ):
                coordinator = getattr(self, "req041_migration_coordinator", None)
                status_getter = getattr(coordinator, "status", None)
                if callable(status_getter):
                    try:
                        control = status_getter()
                    except Exception:
                        control = {}
                    if isinstance(control, dict) and control.get("memory_version") == "not-detected":
                        return False
            return True
        if not status.get("scoped_required"):
            return False
        if status.get("memory_bound") or self._req041_memory_scope_was_bound():
            return True
        coordinator = getattr(self, "req041_migration_coordinator", None)
        status_getter = getattr(coordinator, "status", None)
        if callable(status_getter):
            try:
                control = status_getter()
            except Exception:
                control = {}
            if isinstance(control, dict) and control.get("source_schema_version") == "req041-fresh-v1":
                return False
        return True

    def _req041_erase_scoped_persona_data(
        self,
        persona_id: str,
        *,
        operation_id: str,
    ) -> dict[str, Any]:
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        if synchronizer is None:
            status = getattr(self, "req041_migration_status", None)
            if isinstance(status, dict) and (status.get("required") or status.get("scoped_required")):
                return {"ok": False, "state": "degraded", "code": "scoped_persona_erase_unavailable"}
            return {"ok": True, "state": "not_required", "code": "scoped_persona_erase_not_required"}
        persona_ref = scoped_persona_ref(persona_id)
        context = NamespaceContext(
            kind="persona_global", persona_id=persona_ref, identity_id="", group_id="",
            assurance="verified", profile_status="active",
            policy_version=synchronizer.policy_version,
            migration_epoch=synchronizer.migration_epoch,
        )
        clean_operation = _single_line(operation_id, 120)
        if not clean_operation or context.errors():
            return {"ok": False, "state": "rejected", "code": "scoped_persona_erase_context_invalid"}
        return synchronizer.erase_persona_scopes(
            context, operation_id=clean_operation, reason_code="persona_reset",
        )

    def _req041_group_reset_sagas_locked(self) -> dict[str, dict[str, Any]]:
        sagas = self.data.get("_req041_group_reset_sagas")
        if not isinstance(sagas, dict):
            sagas = {}
            self.data["_req041_group_reset_sagas"] = sagas
        return sagas

    def _req041_finalize_group_reset_locked(self, group_id: str) -> dict[str, Any]:
        normalize = getattr(self, "_normalize_group_identity_id", None)

        def normalized(value: Any) -> str:
            if callable(normalize):
                return _single_line(normalize(value), 160)
            return _single_line(value, 160)

        clean_group = normalized(group_id)
        groups = self.data.get("groups")
        if not isinstance(groups, dict):
            groups = {}
            self.data["groups"] = groups
        matching_keys = [key for key in groups if normalized(key) == clean_group]
        for key in matching_keys:
            groups.pop(key, None)

        changed: dict[str, list[str]] = {}
        for key in (
            "group_whitelist_ids", "group_blacklist_ids",
            "expression_group_learning_source_ids", "expression_group_application_ids",
        ):
            old_values = list(getattr(self, key, []) or [])
            new_values = [
                str(item).strip() for item in old_values
                if str(item).strip() and normalized(item) != clean_group
            ]
            setattr(self, key, new_values)
            _set_into_config(self.config, key, new_values)
            if new_values != old_values:
                changed[key] = new_values
        refresher = getattr(self, "_refresh_expression_voice_profile", None)
        if callable(refresher):
            refresher()
        return {
            "removed_group": bool(matching_keys),
            "removed_whitelist": "group_whitelist_ids" in changed,
            "removed_blacklist": "group_blacklist_ids" in changed,
            "removed_expression_scope": bool(
                {"expression_group_learning_source_ids", "expression_group_application_ids"} & changed.keys()
            ),
        }

    async def _req041_resume_confirmed_group_resets(self) -> dict[str, Any]:
        async with self._data_lock:
            pending = [
                deepcopy(saga) for saga in self._req041_group_reset_sagas_locked().values()
                if isinstance(saga, dict) and saga.get("state") in {"confirmed", "config_pending"}
            ][:32]
        completed = 0
        errors: list[str] = []
        for saga in pending:
            result = await self.reset_group_scoped_data(
                str(saga.get("group_id") or ""),
                operation_id=str(saga.get("operation_id") or ""),
            )
            if result.get("ok") and result.get("state") == "completed":
                completed += 1
            else:
                errors.append(str(result.get("code") or "group_reset_resume_failed")[:120])
        return {
            "ok": not errors,
            "code": "group_reset_resume_complete" if not errors else "group_reset_resume_degraded",
            "pending": len(pending), "completed": completed,
            "error_codes": sorted(set(errors))[:16],
        }

    async def _req041_resume_confirmed_persona_resets(self) -> dict[str, Any]:
        pending: list[dict[str, str]] = []
        async with self._data_lock:
            default_data = getattr(self, "_data_default", None)
            default_marker = (
                default_data.get("_req041_persona_reset_saga")
                if isinstance(default_data, dict) else None
            )
            if isinstance(default_marker, dict) and default_marker.get("state") == "confirmed":
                pending.append({
                    "persona_id": "",
                    "operation_id": str(default_marker.get("operation_id") or ""),
                    "force_default": "1",
                })
            profiles = getattr(self, "_persona_data_profiles", None)
            if isinstance(profiles, dict):
                for raw_persona, profile in profiles.items():
                    marker = profile.get("_req041_persona_reset_saga") if isinstance(profile, dict) else None
                    if isinstance(marker, dict) and marker.get("state") == "confirmed":
                        pending.append({
                            "persona_id": str(raw_persona or ""),
                            "operation_id": str(marker.get("operation_id") or ""),
                            "force_default": "0",
                        })
        completed = 0
        errors: list[str] = []
        for saga in pending[:32]:
            result = await self._reset_current_persona_store(
                saga["persona_id"], rebuild_today=False,
                operation_id=saga["operation_id"],
                _force_default_store=saga["force_default"] == "1",
            )
            if result.get("ok"):
                completed += 1
            else:
                errors.append(str(result.get("code") or "persona_reset_resume_failed")[:120])
        return {
            "ok": not errors,
            "code": "persona_reset_resume_complete" if not errors else "persona_reset_resume_degraded",
            "pending": min(len(pending), 32), "completed": completed,
            "error_codes": sorted(set(errors))[:16],
        }

    def _req041_persist_archive_saga_locked(
        self,
        *,
        sections: Collection[str] | None = None,
        deleted_sections: Collection[str] = (),
        full_scope: str | None = None,
    ) -> None:
        """Durably persist a destructive saga before any cross-store write."""
        normalized_scope = str(full_scope or "").strip() or None
        normalized_deleted = {
            str(section).strip()
            for section in deleted_sections
            if str(section).strip()
        }
        normalized_sections = {
            str(section).strip()
            for section in (sections or ())
            if str(section).strip()
        }
        if normalized_scope is None and sections is None:
            raise ValueError(
                "archive saga sections must be explicit unless full_scope is provided"
            )
        if normalized_scope is not None:
            if normalized_scope != "admin_import_export":
                raise ValueError(
                    "archive saga full_scope must be admin_import_export"
                )
            if sections is not None or normalized_deleted:
                raise ValueError(
                    "archive saga full_scope cannot be combined with sections"
                )
        else:
            # The live mapping is authoritative when an internal caller names a
            # section in both sets. Present values are upserts; absent values are
            # tombstones. Never pass an overlapping request to the writer.
            for section in normalized_sections & normalized_deleted:
                if section in self.data:
                    normalized_deleted.discard(section)
                else:
                    normalized_sections.discard(section)
        if not normalized_sections and not normalized_deleted:
            if normalized_scope is None:
                return
        validator = getattr(self, "_validate_save_request", None)
        if callable(validator):
            validator(
                None if normalized_scope is not None else normalized_sections,
                normalized_deleted,
                normalized_scope,
            )
        active_persona = str(self._active_persona_scope() or "")
        if bool(getattr(self, "enable_multi_persona_mode", False)) and active_persona:
            # Destructive sagas must be durable before touching another store;
            # the persona profile backend exposes only an immediate snapshot API.
            self._write_persona_data_snapshot_sync(active_persona, deepcopy(self.data))
            return
        if normalized_scope is not None:
            self._save_data_now_sync(full_scope="admin_import_export")
        else:
            self._save_data_now_sync(
                sections=normalized_sections,
                deleted_sections=normalized_deleted,
            )

    def _req041_scoped_archive_available(self) -> bool:
        """Return whether a confirmed identity archive can reach Memory safely."""
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        if synchronizer is None or not callable(
            getattr(synchronizer, "archive_identity_scopes", None)
        ):
            return False
        status = getattr(self, "req041_migration_status", None)
        if not isinstance(status, dict):
            return False
        if str(status.get("state") or "").strip().lower() in {"degraded", "paused", "stopped"}:
            return False
        return True

    async def _req041_resume_confirmed_person_archives(self) -> dict[str, Any]:
        registry = self._active_unified_person_registry()
        pending = registry.confirmed_person_archives(limit=32)
        completed = 0
        errors: list[str] = []
        for saga in pending:
            result = await self.archive_unified_person(
                saga["person_id"], operation_id=saga["operation_id"],
                confirmation_token=saga["confirmation_token"], dry_run=False,
                actor_id=saga["actor_id"], reason_code=saga["reason_code"],
            )
            if result.get("ok") and result.get("code") == "person_archived":
                completed += 1
            else:
                errors.append(str(result.get("code") or "person_archive_resume_failed")[:120])
        return {
            "ok": not errors,
            "code": "person_archive_resume_complete" if not errors else "person_archive_resume_degraded",
            "pending": len(pending), "completed": completed,
            "error_codes": sorted(set(errors))[:16],
        }

    def _req041_purge_legacy_person_locked(
        self,
        person_id: str,
        subjects: list[str],
        *,
        changed_sections: set[str] | None = None,
    ) -> dict[str, int]:
        """Remove only exact identity-owned legacy nodes; never fuzzy-search text."""
        clean_person = _single_line(person_id, 80)
        subject_set = {_single_line(item, 160) for item in subjects if _single_line(item, 160)}
        counts = {"mapping_entries": 0, "list_entries": 0, "records": 0}
        identity_fields = {
            "user_id", "identity_subject_id", "platform_subject_id", "sender_id", "member_id",
            "linked_qq_user_id", "target_user_id", "qq_user_id",
        }

        def owned(value: Any) -> bool:
            if not isinstance(value, dict):
                return False
            if str(value.get("unified_person_id") or "").strip() == clean_person:
                return True
            return any(
                str(value.get(field) or "").strip() in subject_set
                for field in identity_fields
                if value.get(field) not in (None, "")
            )

        def scrub(value: Any, *, depth: int = 0) -> Any:
            if depth > 10:
                return value
            if isinstance(value, dict):
                for key in list(value):
                    item = value[key]
                    if str(key) == clean_person or str(key) in subject_set or owned(item):
                        value.pop(key, None)
                        counts["mapping_entries"] += 1
                        counts["records"] += 1
                        continue
                    value[key] = scrub(item, depth=depth + 1)
                return value
            if isinstance(value, list):
                kept: list[Any] = []
                for item in value:
                    if owned(item):
                        counts["list_entries"] += 1
                        counts["records"] += 1
                    else:
                        kept.append(scrub(item, depth=depth + 1))
                value[:] = kept
            return value

        for key in list(self.data):
            if key == "unified_person":
                continue
            before = deepcopy(self.data[key])
            scrubbed = scrub(self.data[key])
            self.data[key] = scrubbed
            if changed_sections is not None and before != scrubbed:
                changed_sections.add(str(key))
        return counts

    async def _req041_resume_confirmed_person_purges(self) -> dict[str, Any]:
        registry = self._active_unified_person_registry()
        pending = registry.confirmed_person_purges(limit=32)
        completed = 0
        errors: list[str] = []
        for saga in pending:
            result = await self.purge_unified_person(
                saga["person_id"], operation_id=saga["operation_id"],
                confirmation_token=saga["confirmation_token"], dry_run=False,
                actor_id=saga["actor_id"], reason_code=saga["reason_code"],
            )
            if result.get("ok") and result.get("code") == "person_purged":
                completed += 1
            else:
                errors.append(str(result.get("code") or "person_purge_resume_failed")[:120])
        return {
            "ok": not errors,
            "code": "person_purge_resume_complete" if not errors else "person_purge_resume_degraded",
            "pending": len(pending), "completed": completed,
            "error_codes": sorted(set(errors))[:16],
        }

    def _req041_scoped_private_read_view(self, event: Any, user: dict[str, Any]) -> dict[str, Any]:
        existing = getattr(event, "req041_scoped_private_read_view", None)
        if isinstance(existing, dict):
            return existing
        view = dict(user) if isinstance(user, dict) else user
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        context = self._req041_scoped_context_for_user(user, kind="private")
        if synchronizer is None or context is None:
            return view
        projection = synchronizer.read_projection(context)
        if not isinstance(projection, dict) or projection.get("ok") is not True:
            if isinstance(view, dict):
                view["req041_scoped_read_generation"] = "new_unavailable"
                try:
                    setattr(event, "req041_scoped_private_read_view", view)
                except Exception:
                    pass
            return view
        persona_context = self._req041_persona_global_context(purpose="rule_read")
        persona_projection = (
            synchronizer.read_projection(persona_context) if persona_context is not None else None
        )
        view = overlay_private_runtime_view(view, projection, persona_projection)
        if not isinstance(view, dict) or view.get("req041_scoped_read_generation") != "new":
            return view
        try:
            setattr(event, "req041_scoped_private_read_view", view)
        except Exception:
            pass
        return view
