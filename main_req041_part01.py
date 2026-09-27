# -*- coding: utf-8 -*-
"""PrivateCompanionPluginReq041Part01Mixin。

由 tools/split_mixin_domain.py 从 main_req041.py 机械抽取（17 个方法 + 0 个模块级名字 + 0 个类级赋值 / 582 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPluginReq041Mixin）。
"""
from __future__ import annotations

from .main_req041_shared import logger
from .main_req041_shared import Any
from .main_req041_shared import NamespaceContext
from .main_req041_shared import Path
from .main_req041_shared import ScopedProjectionSynchronizer
from .main_req041_shared import UnifiedPersonRegistry
from .main_req041_shared import _single_line
from .main_req041_shared import asyncio
from .main_req041_shared import deepcopy
from .main_req041_shared import hashlib
from .main_req041_shared import json
from .main_req041_shared import legacy_pending_reference
from .main_req041_shared import math
from .main_req041_shared import normalize_relationship_positive_stage_cap_key
from .main_req041_shared import runtime_persona_setting
from .main_req041_shared import scoped_group_ref
from .main_req041_shared import scoped_persona_ref
from .main_req041_shared import uuid



class PrivateCompanionPluginReq041Part01Mixin:
    """PrivateCompanionPluginReq041Part01Mixin（从 PrivateCompanionPluginReq041Mixin 拆出）。"""


    def _req041_update_unified_profile_facts(
        self,
        user: dict[str, Any],
        changes: dict[str, Any],
        *,
        operation_id: str = "",
        actor_id: str = "companion",
        schedule_save: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(user, dict) or not isinstance(changes, dict) or not changes:
            return {"ok": False, "state": "skipped", "code": "profile_fact_update_skipped"}
        person_id = _single_line(user.get("unified_person_id"), 80)
        if not person_id:
            return {"ok": False, "state": "skipped", "code": "profile_identity_pending"}
        result = self._active_unified_person_registry().update_identity_profile_facts(
            person_id,
            changes,
            operation_id=(
                _single_line(operation_id, 120)
                or f"req041-profile-{uuid.uuid4().hex}"
            ),
            actor_id=actor_id,
        )
        if result.get("ok") and result.get("changed") and schedule_save:
            self._schedule_data_save(sections={"unified_person"})
        return result

    def _req041_emit_identity_dual_write(
        self,
        result: dict[str, Any],
        *,
        action: str,
        operation_id: str,
        registry: UnifiedPersonRegistry | None = None,
    ) -> dict[str, Any]:
        producer = getattr(self, "req041_dual_write_producer", None)
        if producer is None:
            return {"status": "skipped", "code": "dual_write_not_active"}
        active_registry = registry if isinstance(registry, UnifiedPersonRegistry) else self._active_unified_person_registry()
        try:
            return producer.emit_identity_change(
                registry=active_registry,
                result=result,
                action=action,
                operation_id=operation_id,
            )
        except Exception as exc:
            producer.fail_closed("identity_dual_write_failed")
            migration_status = getattr(self, "req041_migration_status", None)
            if isinstance(migration_status, dict):
                migration_status.update({
                    "state": "paused",
                    "code": "identity_dual_write_failed",
                    "dual_write": "failed",
                })
            logger.warning(
                "REQ-041 身份双写失败，已暂停新读切换并保留 legacy 写入: %s",
                _single_line(exc, 160),
            )
            return {"status": "failed", "code": "identity_dual_write_failed"}

    def _req041_emit_relationship_snapshot(
        self,
        user: dict[str, Any],
        *,
        reason_code: str,
    ) -> dict[str, Any]:
        producer = getattr(self, "req041_dual_write_producer", None)
        if producer is None:
            return {"status": "skipped", "code": "dual_write_not_active"}
        try:
            try:
                source_revision = max(0, int(user.get("req041_relationship_source_revision") or 0)) + 1
            except (TypeError, ValueError, OverflowError):
                source_revision = 1
            scope = self._unified_persona_domain()
            emitted = producer.emit_relationship_snapshot(
                registry=self._active_unified_person_registry(),
                user=user,
                reason_code=reason_code,
                source_scope=scope or "default",
                source_revision=source_revision,
            )
            if int(emitted.get("source_revision") or 0) > 0:
                user["req041_relationship_source_revision"] = int(emitted["source_revision"])
            return emitted
        except Exception as exc:
            producer.fail_closed("relationship_snapshot_dual_write_failed")
            migration_status = getattr(self, "req041_migration_status", None)
            if isinstance(migration_status, dict):
                migration_status.update({
                    "state": "paused",
                    "code": "relationship_snapshot_dual_write_failed",
                    "dual_write": "failed",
                })
            logger.warning(
                "REQ-041 关系快照双写失败，已暂停新读切换并保留 legacy 写入: %s",
                _single_line(exc, 160),
            )
            return {"status": "failed", "code": "relationship_snapshot_dual_write_failed"}

    def _req041_migration_source_files(self) -> list[Path]:
        # Once a migration has a verified backup, its manifest is the authority
        # for the legacy source set. Persona profiles created later are new
        # runtime stores and must not change the resume contract on restart.
        coordinator = getattr(self, "req041_migration_coordinator", None)
        status_getter = getattr(coordinator, "status", None)
        if callable(status_getter):
            try:
                status = status_getter()
            except Exception:
                status = {}
            manifest_name = _single_line(
                status.get("backup_manifest") if isinstance(status, dict) else "",
                300,
            )
            if manifest_name:
                data_root = Path(str(getattr(self, "data_dir", "") or "")).resolve()
                try:
                    manifest_path = (data_root / manifest_name).resolve(strict=True)
                    manifest_path.relative_to(data_root)
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    entries = manifest.get("files") if isinstance(manifest, dict) else None
                    frozen: list[Path] = []
                    if isinstance(entries, list):
                        for entry in entries:
                            name = entry.get("name") if isinstance(entry, dict) else ""
                            if not isinstance(name, str) or not name.strip():
                                frozen = []
                                break
                            candidate = (data_root / name).resolve(strict=False)
                            candidate.relative_to(data_root)
                            if candidate.is_symlink():
                                frozen = []
                                break
                            frozen.append(candidate)
                    if frozen:
                        return frozen
                except (OSError, ValueError, json.JSONDecodeError):
                    # Fall through to live discovery. The coordinator will then
                    # retain its existing fail-closed validation and error code.
                    pass
        candidates: list[Path] = []
        if str(getattr(self, "storage_backend", "json") or "json").lower() == "sqlite":
            candidates.append(Path(str(getattr(self, "storage_sqlite_effective_path", "") or "")))
        else:
            candidates.append(Path(str(getattr(self, "data_file", "") or "")))
        profiles = Path(str(getattr(self, "_persona_profiles_dir", "") or ""))
        if profiles.is_dir():
            candidates.extend(sorted(profiles.glob("*.db")))
            candidates.extend(sorted(profiles.glob("*.json")))
        result: list[Path] = []
        for candidate in candidates:
            try:
                if candidate and candidate.is_file() and not candidate.is_symlink():
                    result.append(candidate)
            except OSError:
                continue
        return result

    def _req041_compatibility_snapshot(self) -> dict[str, Any]:
        return {
            "auto_profile_creation": bool(runtime_persona_setting(self, 'enable_auto_user_profile_creation', False)),
            "private_access_policy": {
                "passive_private_default": "legacy_effective",
                "configured_targets_default": bool(runtime_persona_setting(self, 'default_enable_configured_targets', False)),
            },
            "proactive_policy": {
                "proactive_only": bool(runtime_persona_setting(self, 'enable_proactive_only_mode', False)),
                "intensity": _single_line(runtime_persona_setting(self, 'proactive_intensity_preset', "off"), 40) or "off",
            },
            "tool_policy": {
                "photo": bool(runtime_persona_setting(self, 'enable_photo_text_action', False)),
                "screen": bool(runtime_persona_setting(self, 'enable_screen_glance_action', False)),
                "poke": bool(runtime_persona_setting(self, 'enable_poke_action', False)),
                "voice": bool(runtime_persona_setting(self, 'enable_voice_action', False)),
            },
            "content_policy": {
                "relationship_tiers": bool(runtime_persona_setting(self, 'enable_relationship_content_tiers', False)),
            },
            "owner_policy": {
                "configured_target": _single_line(getattr(self, "target_user_id", ""), 80) != "",
                "normal_cap_exempt": True,
                "exclusive_mode_frozen": True,
            },
            "relationship_policy": {
                "enabled": bool(runtime_persona_setting(self, 'enable_custom_relationship_stage_policy', False)),
                "positive_cap": _single_line(
                    runtime_persona_setting(self, 'relationship_positive_stage_cap_key', "deeply_bonded"), 40
                ) or "deeply_bonded",
                "group_ordinary_delta": 0,
            },
        }

    def _req041_registry_for_person(self, person_id: str) -> UnifiedPersonRegistry | None:
        """Locate exactly one persona-scoped registry for a stable person id."""
        stores: list[dict[str, Any]] = []
        default_data = getattr(self, "_data_default", None)
        if not isinstance(default_data, dict):
            default_data = self.data if isinstance(getattr(self, "data", None), dict) else None
        if isinstance(default_data, dict):
            stores.append(default_data)
        profiles = getattr(self, "_persona_data_profiles", {})
        if isinstance(profiles, dict):
            for profile_data in profiles.values():
                if isinstance(profile_data, dict) and all(profile_data is not item for item in stores):
                    stores.append(profile_data)
        matches = [
            UnifiedPersonRegistry(store)
            for store in stores
            if UnifiedPersonRegistry(store).read_projection(person_id) is not None
        ]
        return matches[0] if len(matches) == 1 else None

    def _req041_legacy_relationship_state(self, person_id: str) -> dict[str, Any] | None:
        """Read exactly one live legacy authority row for S5 reconciliation."""
        stores: list[dict[str, Any]] = []
        default_data = getattr(self, "_data_default", None)
        if not isinstance(default_data, dict):
            default_data = self.data if isinstance(getattr(self, "data", None), dict) else None
        if isinstance(default_data, dict):
            stores.append(default_data)
        profiles = getattr(self, "_persona_data_profiles", {})
        if isinstance(profiles, dict):
            for profile_data in profiles.values():
                if isinstance(profile_data, dict) and all(profile_data is not item for item in stores):
                    stores.append(profile_data)
        matches: list[dict[str, Any]] = []
        for store in stores:
            registry = UnifiedPersonRegistry(store)
            users = store.get("users") if isinstance(store.get("users"), dict) else {}
            for legacy_key, user in users.items():
                if not isinstance(user, dict) or user.get("unified_person_id") != person_id:
                    continue
                subject = _single_line(
                    user.get("identity_subject_id") or user.get("user_id") or legacy_key, 160
                )
                if not subject or not registry.matches_person_subject(person_id, subject):
                    continue
                try:
                    score = int(user.get("relationship_score", 0))
                    totals = user.get("relationship_daily_totals")
                    totals = totals if isinstance(totals, dict) else {}
                    positive = int(totals.get("positive", 0))
                    negative = int(totals.get("negative", 0))
                    effective = float(user.get("relationship_last_effective_at") or 0.0)
                except (TypeError, ValueError, OverflowError):
                    return None
                if (
                    any(isinstance(value, bool) for value in (user.get("relationship_score"), totals.get("positive"), totals.get("negative")))
                    or not -1200 <= score <= 1200 or not 0 <= positive <= 120
                    or not -180 <= negative <= 0 or not math.isfinite(effective) or effective < 0
                ):
                    return None
                role = "owner" if str(user.get("relationship_role") or "").strip().lower() == "owner" else "friend"
                mode = (
                    "owner_exclusive"
                    if role == "owner" and str(user.get("relationship_mode") or "").strip().lower() == "owner_exclusive"
                    else "normal"
                )
                matches.append({
                    "relationship_role": role,
                    "relationship_mode": mode,
                    "relationship_score": score,
                    "positive_stage_cap_key": normalize_relationship_positive_stage_cap_key(
                        user.get("relationship_positive_stage_cap_key")
                    ),
                    "daily_totals": {
                        "day": _single_line(totals.get("day"), 16),
                        "positive": positive,
                        "negative": negative,
                    },
                    "last_effective_at": effective,
                })
        return matches[0] if len(matches) == 1 else None

    def _req041_resolve_legacy_pending_for_person(self, person_id: str) -> int:
        """Resolve one S4 opaque pending row only after S5 proves exact parity."""
        coordinator = getattr(self, "req041_migration_coordinator", None)
        status = coordinator.status() if coordinator is not None else {}
        epoch = _single_line(status.get("migration_epoch"), 128) if isinstance(status, dict) else ""
        if not epoch:
            return 0
        scoped_stores: list[tuple[str, dict[str, Any]]] = []
        default_data = getattr(self, "_data_default", None)
        if not isinstance(default_data, dict):
            default_data = self.data if isinstance(getattr(self, "data", None), dict) else None
        if isinstance(default_data, dict):
            scoped_stores.append(("default", default_data))
        profiles = getattr(self, "_persona_data_profiles", {})
        if isinstance(profiles, dict):
            for persona_id, profile_data in profiles.items():
                if not isinstance(profile_data, dict):
                    continue
                scope_hash = hashlib.sha256(str(persona_id).encode("utf-8")).hexdigest()[:24]
                scoped_stores.append((f"persona:{scope_hash}", profile_data))
        matches: list[tuple[str, str]] = []
        for source_scope, store in scoped_stores:
            registry = UnifiedPersonRegistry(store)
            users = store.get("users") if isinstance(store.get("users"), dict) else {}
            for legacy_key, user in users.items():
                if not isinstance(user, dict) or user.get("unified_person_id") != person_id:
                    continue
                subject = _single_line(
                    user.get("identity_subject_id") or user.get("user_id") or legacy_key, 160
                )
                if subject and registry.matches_person_subject(person_id, subject):
                    matches.append((source_scope, str(legacy_key)))
        if len(matches) != 1:
            return 0
        source_scope, legacy_key = matches[0]
        reference = legacy_pending_reference(epoch, source_scope, legacy_key)
        return int(bool(coordinator.resolve_pending(reference)))

    def _req041_schedule_replay(self) -> None:
        worker = getattr(self, "req041_migration_replay", None)
        if worker is None:
            return
        self._req041_replay_requested = True
        task = getattr(self, "_req041_replay_task", None)
        if task is not None and not task.done():
            return
        try:
            self._req041_replay_task = asyncio.get_running_loop().create_task(
                self._req041_run_replay_batch(), name="req041-shadow-replay"
            )
            self._req041_replay_task.add_done_callback(self._req041_replay_finished)
        except RuntimeError:
            raise RuntimeError("migration_replay_loop_unavailable")

    def _req041_legacy_snapshots_locked(self) -> list[tuple[str, dict[str, Any]]]:
        snapshots: list[tuple[str, dict[str, Any]]] = []
        default_data = getattr(self, "_data_default", None)
        if not isinstance(default_data, dict):
            default_data = self.data if isinstance(getattr(self, "data", None), dict) else {}
        snapshots.append(("default", deepcopy(default_data)))
        profiles = getattr(self, "_persona_data_profiles", {})
        if isinstance(profiles, dict):
            for persona_id, profile_data in profiles.items():
                if not isinstance(profile_data, dict):
                    continue
                scope_hash = hashlib.sha256(str(persona_id).encode("utf-8")).hexdigest()[:24]
                snapshots.append((f"persona:{scope_hash}", deepcopy(profile_data)))
        return snapshots

    async def _req041_sync_scoped_now(self) -> dict[str, Any]:
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        if synchronizer is None:
            return {"ok": False, "code": "scoped_projection_not_initialized", "scopes": []}
        synchronizer.mark_dirty()
        async with self._data_lock:
            snapshots = self._req041_legacy_snapshots_locked()
        results: list[dict[str, Any]] = []
        for source_scope, snapshot in snapshots:
            results.append(await asyncio.to_thread(
                synchronizer.sync_snapshot, snapshot, source_scope=source_scope
            ))
        ok = all(item.get("ok") is True for item in results)
        summary = {
            "ok": ok,
            "code": "scoped_projection_synced" if ok else "scoped_projection_degraded",
            "scopes": results,
            "records": sum(int(item.get("records") or 0) for item in results),
            "errors": sum(int(item.get("errors") or 0) for item in results),
        }
        self.req041_scoped_projection_status = summary
        return summary

    async def _req041_rebind_memory_scope_if_available(self) -> dict[str, Any]:
        """Bind the scoped memory API after a late MemoryCompanion startup."""
        enabled = getattr(self, "_memory_companion_bridge_enabled", None)
        if callable(enabled) and not enabled():
            return {"ok": False, "code": "memory_bridge_disabled"}
        coordinator = getattr(self, "req041_migration_coordinator", None)
        binder = getattr(self, "_memory_companion_bind_namespace_epoch", None)
        bridge_getter = getattr(self, "_memory_companion_bridge", None)
        if coordinator is None or not callable(binder) or not callable(bridge_getter):
            return {"ok": False, "code": "memory_scope_runtime_unavailable"}
        lock = getattr(self, "_req041_memory_bind_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._req041_memory_bind_lock = lock
        async with lock:
            status_reader = getattr(coordinator, "status", None)
            try:
                control = status_reader() if callable(status_reader) else {}
            except Exception:
                control = {}
            if not isinstance(control, dict):
                control = {}
            epoch = _single_line(control.get("migration_epoch"), 128)
            policy = _single_line(control.get("policy_version"), 64)
            if not epoch or not policy:
                return {"ok": False, "code": "migration_epoch_unavailable"}
            try:
                bridge = bridge_getter()
            except Exception as exc:
                return {"ok": False, "code": _single_line(exc, 120) or "memory_bridge_lookup_failed"}
            if bridge is None:
                return {"ok": False, "code": "memory_bridge_unavailable"}
            synchronizer = getattr(self, "req041_scoped_projection_sync", None)
            bound_bridge = getattr(self, "_req041_scoped_bridge", None)
            # A synchronizer without an explicit bridge marker may have been
            # created by an older hot-loaded plugin instance. Do not reuse
            # its closures against a newly discovered bridge.
            if synchronizer is not None and bound_bridge is bridge:
                scoped_result = await self._req041_sync_scoped_now()
                if scoped_result.get("ok"):
                    self._req041_mark_memory_scope_bound()
                    runtime = getattr(self, "req041_migration_status", None)
                    if isinstance(runtime, dict):
                        runtime.update({"memory_bound": True, "scoped": scoped_result})
                return scoped_result
            if synchronizer is not None:
                mark_dirty = getattr(synchronizer, "mark_dirty", None)
                if callable(mark_dirty):
                    mark_dirty()
            try:
                remote = binder(
                    bridge,
                    operation_id=f"req041-bind-{epoch}",
                    migration_epoch=epoch,
                    policy_version=policy,
                )
            except Exception as exc:
                return {"ok": False, "code": _single_line(exc, 120) or "namespace_epoch_bind_exception"}
            if not isinstance(remote, dict) or not remote.get("ok"):
                return dict(remote) if isinstance(remote, dict) else {
                    "ok": False, "code": "namespace_epoch_bind_invalid"
                }
            self._req041_mark_memory_scope_bound()
            self.req041_scoped_projection_sync = ScopedProjectionSynchronizer(
                read=lambda namespace, **kwargs: self._memory_companion_read_scoped_record(
                    bridge, namespace, **kwargs
                ),
                list_records=lambda namespace, **kwargs: self._memory_companion_list_scoped_records(
                    bridge, namespace, **kwargs
                ),
                upsert=lambda namespace, **kwargs: self._memory_companion_upsert_scoped_record(
                    bridge, namespace, **kwargs
                ),
                tombstone=lambda namespace, **kwargs: self._memory_companion_tombstone_scoped_record(
                    bridge, namespace, **kwargs
                ),
                tombstone_identity_scopes=lambda namespace, **kwargs: self._memory_companion_tombstone_scoped_identity_scopes(
                    bridge, namespace, **kwargs
                ),
                erase_group_scopes=lambda namespace, **kwargs: self._memory_companion_erase_scoped_group_scopes(
                    bridge, namespace, **kwargs
                ),
                erase_persona_scopes=lambda namespace, **kwargs: self._memory_companion_erase_scoped_persona_scopes(
                    bridge, namespace, **kwargs
                ),
                migration_epoch=epoch,
                policy_version=policy,
                observability=getattr(self, "req041_observability", None),
            )
            self._req041_scoped_bridge = bridge
            scoped_result = await self._req041_sync_scoped_now()
            runtime = getattr(self, "req041_migration_status", None)
            if isinstance(runtime, dict):
                runtime.update({"memory_bound": True, "scoped": scoped_result})
            return scoped_result

    async def _req041_run_memory_scope_rebind(self) -> None:
        """Retry late memory binding until the scoped runtime becomes usable."""
        stop_event = getattr(self, "_stop_event", None)
        startup_tasks = getattr(self, "_startup_background_tasks", {})
        migration_task = startup_tasks.get("req041_automatic_migration") if isinstance(startup_tasks, dict) else None
        if isinstance(migration_task, asyncio.Task) and migration_task is not asyncio.current_task():
            try:
                await asyncio.shield(migration_task)
            except Exception:
                pass
        while True:
            if isinstance(stop_event, asyncio.Event) and stop_event.is_set():
                return
            enabled = getattr(self, "_memory_companion_bridge_enabled", None)
            if callable(enabled) and not enabled():
                return
            status = getattr(self, "req041_migration_status", None)
            if not isinstance(status, dict):
                await asyncio.sleep(2.0)
                continue
            try:
                result = await self._req041_rebind_memory_scope_if_available()
            except Exception as exc:
                logger.warning(
                    "[PrivateCompanion] 记忆作用域补绑定暂未完成，将重试: %s",
                    _single_line(exc, 160),
                    exc_info=True,
                )
                await asyncio.sleep(2.0)
                continue
            if result.get("ok"):
                status = getattr(self, "req041_migration_status", None)
                if isinstance(status, dict):
                    status.update({"memory_bound": True, "scoped": result})
                    paused = status.get("state") == "paused"
                    replay_ready = (
                        not status.get("required")
                        or (status.get("s5") or {}).get("status") == "ok"
                    )
                    if paused or not replay_ready:
                        return
                    if status.get("required"):
                        status.update({"state": "active", "code": "migration_shadow_active"})
                    else:
                        status.update({"state": "active", "code": "fresh_scoped_runtime_active"})
                    return
                elif status is None:
                    return
            await asyncio.sleep(2.0)

    async def _req041_run_scoped_sync(self) -> None:
        while bool(getattr(self, "_req041_scoped_sync_requested", False)):
            self._req041_scoped_sync_requested = False
            result = await self._req041_sync_scoped_now()
            if result.get("ok") is not True:
                status = getattr(self, "req041_migration_status", None)
                if isinstance(status, dict):
                    status.update({"state": "degraded", "code": "scoped_projection_degraded", "scoped": result})
                return

    def _req041_scoped_sync_finished(self, task: Any) -> None:
        self._req041_scoped_sync_task = None
        if not task.cancelled():
            try:
                error = task.exception()
            except Exception:
                error = None
            if error is not None:
                self.req041_scoped_projection_status = {
                    "ok": False, "code": _single_line(error, 120) or "scoped_projection_exception"
                }
        if bool(getattr(self, "_req041_scoped_sync_requested", False)):
            self._req041_schedule_scoped_sync()

    def _req041_schedule_scoped_sync(self) -> None:
        if getattr(self, "req041_scoped_projection_sync", None) is None:
            return
        self.req041_scoped_projection_sync.mark_dirty()
        stop_event = getattr(self, "_stop_event", None)
        if stop_event is not None and callable(getattr(stop_event, "is_set", None)) and stop_event.is_set():
            return
        self._req041_scoped_sync_requested = True
        task = getattr(self, "_req041_scoped_sync_task", None)
        if isinstance(task, asyncio.Task) and not task.done():
            return
        try:
            task = asyncio.get_running_loop().create_task(
                self._req041_run_scoped_sync(), name="req041-scoped-projection-sync"
            )
        except RuntimeError:
            return
        self._req041_scoped_sync_task = task
        task.add_done_callback(self._req041_scoped_sync_finished)

    def _req041_scoped_context_for_user(
        self,
        user: dict[str, Any],
        *,
        kind: str = "private",
        group_id: str = "",
        purpose: str = "memory_read",
    ) -> NamespaceContext | None:
        if not isinstance(user, dict):
            return None
        person_id = str(user.get("unified_person_id") or "").strip()
        subject = str(user.get("identity_subject_id") or user.get("user_id") or "").strip()
        if not person_id or not subject:
            return None
        registry = self._active_unified_person_registry()
        if not registry.matches_person_subject(person_id, subject):
            return None
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        if synchronizer is None:
            return None
        active_persona = self._active_persona_scope()
        persona_id = scoped_persona_ref(active_persona)
        safe_group = scoped_group_ref(persona_id, group_id) if kind == "group_member" else ""
        resolution = registry.formal_namespace_for_person(
            person_id, kind=kind, group_id=safe_group,
            policy_version=synchronizer.policy_version,
            migration_epoch=synchronizer.migration_epoch,
            purpose=purpose,
        )
        raw = resolution.get("context") if isinstance(resolution, dict) else None
        if not resolution.get("ok") or not isinstance(raw, dict):
            return None
        context = NamespaceContext(
            kind=kind, persona_id=persona_id, identity_id=person_id, group_id=safe_group,
            assurance=str(raw.get("assurance") or "verified"),
            profile_status=str(raw.get("profile_status") or "active"),
            policy_version=synchronizer.policy_version,
            migration_epoch=synchronizer.migration_epoch,
        )
        return context if not context.errors() else None
