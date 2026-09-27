# -*- coding: utf-8 -*-
"""PrivateCompanionPluginReq041Part04Mixin。

由 tools/split_mixin_domain.py 从 main_req041.py 机械抽取（2 个方法 + 0 个模块级名字 + 0 个类级赋值 / 445 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPluginReq041Mixin）。
"""
from __future__ import annotations

from .main_req041_shared import logger
from .main_req041_shared import Any
from .main_req041_shared import MigrationBackfill
from .main_req041_shared import MigrationDualWriteProducer
from .main_req041_shared import MigrationRelationshipReadRouter
from .main_req041_shared import MigrationReplayWorker
from .main_req041_shared import Path
from .main_req041_shared import RelationshipAccountStore
from .main_req041_shared import ScopedProjectionSynchronizer
from .main_req041_shared import _now_ts
from .main_req041_shared import _single_line
from .main_req041_shared import asyncio
from .main_req041_shared import inspect_migration_sources



class PrivateCompanionPluginReq041Part04Mixin:
    """PrivateCompanionPluginReq041Part04Mixin（从 PrivateCompanionPluginReq041Mixin 拆出）。"""


    async def _req041_initialize_automatic_migration(self) -> None:
        try:
            metrics_type = Req041Observability
        except NameError:  # Standalone migration harnesses load selected methods only.
            from req041_observability import Req041Observability as metrics_type
        if not isinstance(getattr(self, "req041_observability", None), metrics_type):
            self.req041_observability = metrics_type()
        if not str(getattr(self, "_req041_runtime_boot_ref", "") or ""):
            self._req041_runtime_boot_ref = f"boot-{id(self)}"
        coordinator = getattr(self, "req041_migration_coordinator", None)
        outbox = getattr(self, "req041_migration_outbox", None)
        if coordinator is None or outbox is None:
            self.req041_migration_status = {
                "required": False, "state": "degraded", "code": "migration_runtime_unavailable"
            }
            return
        sources = self._req041_migration_source_files()
        presence_getter = getattr(self, "_memory_companion_presence", None)
        try:
            presence = presence_getter() if callable(presence_getter) else {}
        except Exception:
            presence = {}
        memory_version = _single_line((presence or {}).get("version"), 32) or "not-detected"
        companion_version = _single_line((getattr(self, "plugin_identity", {}) or {}).get("version"), 32) or "unknown"
        try:
            current_status = coordinator.status()
            is_fresh_runtime = current_status.get("source_schema_version") == "req041-fresh-v1"
            if not sources and not current_status:
                current_status = await asyncio.to_thread(
                    coordinator.initialize_fresh_runtime,
                    policy_version="req041-v1",
                    target_schema_version="req041-v1",
                    companion_version=companion_version,
                    memory_version=memory_version,
                )
                is_fresh_runtime = True
            if is_fresh_runtime:
                await self._req041_initialize_fresh_scoped_runtime(current_status)
                return
            async with self._data_lock:
                source_inventory = await asyncio.to_thread(
                    inspect_migration_sources,
                    self.data_dir,
                    sources,
                )
                status = await asyncio.to_thread(
                    coordinator.start_or_resume,
                    source_files=sources,
                    policy_version="req041-v1",
                    source_schema_version=source_inventory["source_schema_version"],
                    target_schema_version="req041-v1",
                    companion_version=companion_version,
                    memory_version=memory_version,
                    source_inventory=source_inventory,
                )
            if status.get("phase") == "S1" and status.get("state") != "paused":
                await asyncio.to_thread(coordinator.capture_compatibility, self._req041_compatibility_snapshot())
                status = coordinator.status()
            epoch = str(status.get("migration_epoch") or "")
            policy = str(status.get("policy_version") or "")
            await asyncio.to_thread(outbox.begin_epoch, epoch, policy_version=policy)
            self.req041_dual_write_producer = MigrationDualWriteProducer(
                outbox=outbox,
                coordinator=coordinator,
                migration_epoch=epoch,
                policy_version=policy,
                on_enqueued=self._req041_schedule_replay,
            )
            if status.get("state") == "paused":
                self.req041_migration_status = {
                    "required": True, "state": "paused", "code": status.get("error_code") or "migration_paused",
                    "phase": status.get("phase", "S0"), "dual_write": "capturing_while_paused",
                }
                return
            if status.get("phase") == "S2":
                status = await asyncio.to_thread(coordinator.transition, "S3", checkpoint="durable_outbox_active")

            backfill_result: dict[str, Any] = {"ok": True, "code": "s4_not_required"}
            if status.get("phase") in {"S3", "S4"}:
                try:
                    async with self._data_lock:
                        legacy_snapshots = self._req041_legacy_snapshots_locked()
                    backfiller = await asyncio.to_thread(
                        MigrationBackfill,
                        coordinator=coordinator,
                        relationship_path=Path(self.data_dir) / "req041_relationship.db",
                        migration_epoch=epoch,
                        policy_version=policy,
                        outbox=outbox,
                    )
                    backfill_counts: dict[str, Any] = {
                        "phase": status.get("phase", "S3"), "migrated": 0, "idempotent": 0,
                        "pending": 0, "conflicts": 0, "formal_identities": 0, "legacy_users": 0,
                        "identity_baselines": 0,
                        "source_scopes": len(legacy_snapshots),
                    }
                    for source_scope, legacy_snapshot in legacy_snapshots:
                        scoped_counts = await asyncio.to_thread(
                            backfiller.run,
                            legacy_snapshot,
                            source_scope=source_scope,
                        )
                        backfill_counts["phase"] = scoped_counts["phase"]
                        for count_key in (
                            "migrated", "idempotent", "pending", "conflicts",
                            "formal_identities", "legacy_users",
                            "identity_baselines",
                        ):
                            backfill_counts[count_key] += int(scoped_counts[count_key])
                    self.req041_migration_backfill = backfiller
                    self.req041_relationship_store = backfiller.relationships
                    backfill_result = {"ok": True, "code": "s4_shadow_backfilled", **backfill_counts}
                    status = coordinator.status()
                except Exception as backfill_exc:
                    backfill_result = {
                        "ok": False,
                        "code": _single_line(backfill_exc, 120) or "s4_backfill_failed",
                    }
                    logger.warning(
                        "REQ-041 S4 Shadow 回填失败，继续使用 legacy 路径: %s",
                        _single_line(backfill_exc, 160),
                    )

            relationship_store = getattr(self, "req041_relationship_store", None)
            if relationship_store is None and status.get("phase") in {"S4", "S5", "S6", "S7", "S8", "S9"}:
                backfiller = await asyncio.to_thread(
                    MigrationBackfill,
                    coordinator=coordinator,
                    relationship_path=Path(self.data_dir) / "req041_relationship.db",
                    migration_epoch=epoch,
                    policy_version=policy,
                    outbox=outbox,
                )
                self.req041_migration_backfill = backfiller
                self.req041_relationship_store = backfiller.relationships
                relationship_store = backfiller.relationships
                relationship_store.set_observability(self.req041_observability)

            replay_result: dict[str, Any] = {"status": "skipped", "code": "s5_not_ready"}
            if relationship_store is not None and backfill_result.get("ok"):
                if status.get("phase") == "S4":
                    status = await asyncio.to_thread(
                        coordinator.transition, "S5", checkpoint="ordered_shadow_replay_active"
                    )
                if status.get("phase") in {"S5", "S6", "S7", "S8", "S9"}:
                    active_registry = self._active_unified_person_registry()
                    replay_worker = MigrationReplayWorker(
                        outbox=outbox,
                        coordinator=coordinator,
                        relationship_store=relationship_store,
                        registry=active_registry,
                        registry_resolver=self._req041_registry_for_person,
                        legacy_relationship_resolver=self._req041_legacy_relationship_state,
                        legacy_pending_resolver=self._req041_resolve_legacy_pending_for_person,
                        enable_gap_recovery=True,
                        migration_epoch=epoch,
                        policy_version=policy,
                        observability=self.req041_observability,
                    )
                    self.req041_migration_replay = replay_worker
                    await asyncio.to_thread(
                        outbox.set_epoch_state, epoch, "replaying", checkpoint="s5_replay_batch"
                    )
                    replay_result = await asyncio.to_thread(replay_worker.run_batch)
                    if replay_result.get("status") == "ok" and status.get("phase") == "S5":
                        status = await asyncio.to_thread(
                            coordinator.transition, "S6", checkpoint="per_identity_relationship_cutover_enabled"
                        )
                        replay_result = await asyncio.to_thread(replay_worker.run_batch)
                    if replay_result.get("status") == "ok":
                        await asyncio.to_thread(
                            outbox.set_epoch_state, epoch, "active", checkpoint="s5_reconciled"
                        )
                    else:
                        status = coordinator.status()
                    if replay_result.get("status") == "ok":
                        self.req041_relationship_read_router = MigrationRelationshipReadRouter(
                            coordinator=coordinator,
                            relationship_store=relationship_store,
                            registry_resolver=self._req041_registry_for_person,
                            migration_epoch=epoch,
                            policy_version=policy,
                            observability=self.req041_observability,
                        )
                        await asyncio.to_thread(
                            coordinator.prune_read_chains, older_than=_now_ts() - 3600
                        )

            remote = {"ok": False, "state": "degraded", "code": "memory_bridge_unavailable"}
            bridge_getter = getattr(self, "_memory_companion_bridge", None)
            bridge = bridge_getter() if callable(bridge_getter) else None
            binder = getattr(self, "_memory_companion_bind_namespace_epoch", None)
            if bridge is not None and callable(binder):
                remote = binder(
                    bridge,
                    operation_id=f"req041-bind-{epoch}",
                    migration_epoch=epoch,
                    policy_version=policy,
                )
            scoped_result: dict[str, Any] = {
                "ok": False, "code": "namespace_scoped_api_not_bound", "scopes": []
            }
            archive_resume: dict[str, Any] = {
                "ok": True, "code": "person_archive_resume_not_required",
                "pending": 0, "completed": 0, "error_codes": [],
            }
            purge_resume: dict[str, Any] = {
                "ok": True, "code": "person_purge_resume_not_required",
                "pending": 0, "completed": 0, "error_codes": [],
            }
            group_reset_resume: dict[str, Any] = {
                "ok": True, "code": "group_reset_resume_not_required",
                "pending": 0, "completed": 0, "error_codes": [],
            }
            persona_reset_resume: dict[str, Any] = {
                "ok": True, "code": "persona_reset_resume_not_required",
                "pending": 0, "completed": 0, "error_codes": [],
            }
            if remote.get("ok") and bridge is not None:
                self._req041_mark_memory_scope_bound()
                self._req041_scoped_bridge = bridge
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
                    observability=self.req041_observability,
                )
                resumer = getattr(self, "_req041_resume_confirmed_person_archives", None)
                if callable(resumer):
                    archive_resume = await resumer()
                purge_resumer = getattr(self, "_req041_resume_confirmed_person_purges", None)
                if archive_resume.get("ok") and callable(purge_resumer):
                    purge_resume = await purge_resumer()
                group_resumer = getattr(self, "_req041_resume_confirmed_group_resets", None)
                if archive_resume.get("ok") and purge_resume.get("ok") and callable(group_resumer):
                    group_reset_resume = await group_resumer()
                persona_resumer = getattr(self, "_req041_resume_confirmed_persona_resets", None)
                if (
                    archive_resume.get("ok") and purge_resume.get("ok")
                    and group_reset_resume.get("ok") and callable(persona_resumer)
                ):
                    persona_reset_resume = await persona_resumer()
                if archive_resume.get("ok") and purge_resume.get("ok") and group_reset_resume.get("ok") and persona_reset_resume.get("ok"):
                    scoped_result = await self._req041_sync_scoped_now()
                else:
                    scoped_result = {
                        "ok": False, "code": "lifecycle_resume_degraded", "scopes": [],
                    }
            self.req041_migration_status = {
                "required": True,
                "state": "active" if remote.get("ok") and archive_resume.get("ok") and purge_resume.get("ok") and group_reset_resume.get("ok") and persona_reset_resume.get("ok") and scoped_result.get("ok") and backfill_result.get("ok") and replay_result.get("status") == "ok" else (
                    "paused" if status.get("state") == "paused" else "degraded"
                ),
                "code": (
                    "migration_shadow_active"
                    if remote.get("ok") and archive_resume.get("ok") and purge_resume.get("ok") and group_reset_resume.get("ok") and persona_reset_resume.get("ok") and scoped_result.get("ok") and backfill_result.get("ok") and replay_result.get("status") == "ok"
                    else str(
                        backfill_result.get("code") if not backfill_result.get("ok")
                        else replay_result.get("error_code") if replay_result.get("status") == "paused"
                        else archive_resume.get("code") if not archive_resume.get("ok")
                        else purge_resume.get("code") if not purge_resume.get("ok")
                        else group_reset_resume.get("code") if not group_reset_resume.get("ok")
                        else persona_reset_resume.get("code") if not persona_reset_resume.get("ok")
                        else scoped_result.get("code") if remote.get("ok") and not scoped_result.get("ok")
                        else remote.get("code") or "migration_degraded"
                    )[:120]
                ),
                "phase": status.get("phase", "S5"),
                "memory_bound": bool(remote.get("ok")),
                "checkpoint": status.get("checkpoint", ""),
                "s4": backfill_result,
                "s5": replay_result,
                "dual_write": "capturing",
                "scoped": scoped_result,
                "archive_resume": archive_resume,
                "purge_resume": purge_resume,
                "group_reset_resume": group_reset_resume,
                "persona_reset_resume": persona_reset_resume,
            }
            try:
                stability_fn = advance_migration_stability
            except NameError:
                from migration_stability import advance_migration_stability as stability_fn
            stability = await asyncio.to_thread(
                stability_fn,
                coordinator=coordinator, outbox=outbox, migration_epoch=epoch,
                replay_ok=replay_result.get("status") == "ok",
                scoped_ok=bool(scoped_result.get("ok")), memory_bound=bool(remote.get("ok")),
                observability=self.req041_observability,
                boot_ref=self._req041_runtime_boot_ref,
            )
            status = coordinator.status()
            self.req041_migration_status.update({
                "phase": status.get("phase", self.req041_migration_status.get("phase")),
                "checkpoint": status.get("checkpoint", self.req041_migration_status.get("checkpoint")),
                "stability": stability,
            })
        except Exception as exc:
            status = coordinator.status()
            self.req041_migration_status = {
                "required": bool(status),
                "state": "paused" if status.get("state") == "paused" else "degraded",
                "code": _single_line(exc, 120) or "migration_startup_failed",
                "phase": status.get("phase", "S0") if status else "S0",
            }
            logger.warning(
                "REQ-041 自动迁移启动失败，继续使用官方 legacy 路径: %s",
                _single_line(exc, 160),
            )

    async def _req041_initialize_fresh_scoped_runtime(
        self,
        status: dict[str, Any],
    ) -> None:
        """Bring a source-free install directly into the normal scoped runtime."""
        coordinator = self.req041_migration_coordinator
        outbox = self.req041_migration_outbox
        epoch = _single_line(status.get("migration_epoch"), 128)
        policy = _single_line(status.get("policy_version"), 64)
        if not epoch or not policy:
            raise RuntimeError("fresh_runtime_contract_invalid")
        await asyncio.to_thread(outbox.begin_epoch, epoch, policy_version=policy)
        relationship_store = RelationshipAccountStore(
            Path(self.data_dir) / "req041_relationship.db",
            active_migration_epoch=epoch,
            observability=self.req041_observability,
        )
        self.req041_relationship_store = relationship_store
        self.req041_dual_write_producer = MigrationDualWriteProducer(
            outbox=outbox,
            coordinator=coordinator,
            migration_epoch=epoch,
            policy_version=policy,
            on_enqueued=self._req041_schedule_replay,
        )
        replay_worker = MigrationReplayWorker(
            outbox=outbox,
            coordinator=coordinator,
            relationship_store=relationship_store,
            registry=self._active_unified_person_registry(),
            registry_resolver=self._req041_registry_for_person,
            legacy_relationship_resolver=self._req041_legacy_relationship_state,
            legacy_pending_resolver=self._req041_resolve_legacy_pending_for_person,
            enable_gap_recovery=True,
            migration_epoch=epoch,
            policy_version=policy,
            observability=self.req041_observability,
        )
        self.req041_migration_replay = replay_worker
        await asyncio.to_thread(outbox.set_epoch_state, epoch, "active", checkpoint="fresh_runtime_active")
        replay_result = await asyncio.to_thread(replay_worker.run_batch)
        self.req041_relationship_read_router = MigrationRelationshipReadRouter(
            coordinator=coordinator,
            relationship_store=relationship_store,
            registry_resolver=self._req041_registry_for_person,
            migration_epoch=epoch,
            policy_version=policy,
            observability=self.req041_observability,
        )

        remote = {"ok": False, "state": "degraded", "code": "memory_bridge_unavailable"}
        bridge_getter = getattr(self, "_memory_companion_bridge", None)
        bridge = bridge_getter() if callable(bridge_getter) else None
        binder = getattr(self, "_memory_companion_bind_namespace_epoch", None)
        if bridge is not None and callable(binder):
            remote = binder(
                bridge,
                operation_id=f"req041-bind-{epoch}",
                migration_epoch=epoch,
                policy_version=policy,
            )
        scoped_result: dict[str, Any] = {
            "ok": False, "code": "namespace_scoped_api_not_bound", "scopes": []
        }
        if remote.get("ok") and bridge is not None:
            self._req041_mark_memory_scope_bound()
            self._req041_scoped_bridge = bridge
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
                observability=self.req041_observability,
            )
            scoped_result = await self._req041_sync_scoped_now()
        ready = bool(
            remote.get("ok")
            and scoped_result.get("ok")
            and replay_result.get("status") == "ok"
        )
        self.req041_migration_status = {
            "required": False,
            "scoped_required": True,
            "state": "active" if ready else "degraded",
            "code": "fresh_scoped_runtime_active" if ready else str(
                replay_result.get("error_code")
                if replay_result.get("status") != "ok"
                else scoped_result.get("code") if remote.get("ok")
                else remote.get("code") or "fresh_scoped_runtime_degraded"
            )[:120],
            "phase": "S9",
            "memory_bound": bool(remote.get("ok")),
            "checkpoint": status.get("checkpoint", "fresh_runtime_initialized"),
            "dual_write": "capturing",
            "s5": replay_result,
            "scoped": scoped_result,
        }
