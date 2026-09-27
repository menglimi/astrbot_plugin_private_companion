# -*- coding: utf-8 -*-
"""lifecycle。

由 tools/split_main_domain.py 从 main.py 机械抽取（19 个方法 / 591 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import asyncio
import functools
import time
from .external_bridge_resolver import invalidate_external_bridge_cache
from .helpers import _safe_int, _set_into_config, _single_line
from .lab_fixture_adapter import register_companion_lab_fixture_adapter
from .main_shared import _private_companion_runtime

# 出版槽位（拆分自 main.py:511，初始值语义保持一致）
_private_companion_plugin: Any | None = _private_companion_runtime.active_plugin
from .persona_config import runtime_persona_setting
from .photo_reference_catalog import CATALOG_VERSION
from .plugin_identity import is_module_path_for_package
from .plugin_lifecycle import cancel_registered_host_tasks, close_early_resources, task_manager
from .storage.path_generation import activate_persistence_owner
from .story_authority import StoryAuthorityError, story_legacy_sync_operation
from .story_handoff import resume_story_handoff
from astrbot.api.event import filter
from astrbot.core.star.star_handler import star_handlers_registry
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)

class PrivateCompanionPluginLifecycleMixin:
    """lifecycle（从 PrivateCompanionPlugin 拆出）。"""

    @filter.on_plugin_loaded()
    async def _on_external_plugin_loaded(self, metadata: Any, *args: Any, **kwargs: Any) -> None:
        # 任意插件装载后主动失效桥接缓存：运行中安装/重载可选扩展能立即被
        # 发现，未安装扩展的负向结果因此可以缓存到失效为止，不做周期重查。
        # metadata 是 AstrBot 传入的外部对象，这里不读取其内容。
        invalidate_external_bridge_cache(self)

    @filter.on_plugin_unloaded()
    async def _on_external_plugin_unloaded(self, metadata: Any, *args: Any, **kwargs: Any) -> None:
        # 卸载后立即清掉旧实例引用，避免正向缓存继续指向已卸载插件的
        # extension_api。同样只失效，不读取 metadata 内容。
        invalidate_external_bridge_cache(self)

    def _initialize_lab_fixture_adapter(self) -> None:
        try:
            self._lab_fixture_adapter = register_companion_lab_fixture_adapter()
        except Exception as exc:
            self._lab_fixture_adapter = None
            logger.warning(
                "LAB fixture 门控注册失败，已保持生产路径关闭: %s",
                type(exc).__name__,
            )

    def _lab_fixture_relationship_view(self, event: Any, user: Any) -> Any:
        adapter = getattr(self, "_lab_fixture_adapter", None)
        overlay = getattr(adapter, "overlay_relationship_view", None)
        if not callable(overlay):
            return user
        try:
            return overlay(event, user)
        except Exception as exc:
            logger.warning(
                "LAB fixture 关系投影失败，已放行原始生产视图: %s",
                type(exc).__name__,
            )
            return user

    def plugin_identity_status(self) -> dict[str, Any]:
        return dict(self.plugin_identity)

    def _extension_task_count(self) -> int:
        """Count live companion-owned tasks for the control-plane snapshot."""
        tasks: set[asyncio.Task] = set()
        for candidate in (
            getattr(self, "_task", None),
            getattr(self, "_startup_maintenance_task", None),
            getattr(self, "_req041_replay_task", None),
            getattr(self, "_req041_scoped_sync_task", None),
            getattr(self, "_termination_save_task", None),
        ):
            if isinstance(candidate, asyncio.Task) and not candidate.done():
                tasks.add(candidate)
        for registry_name in (
            "_startup_background_tasks",
            "_lifecycle_background_tasks",
            "_passive_input_status_tasks",
            "_group_image_understanding_tasks",
            "_troubleshooting_proactive_wakeup_tasks",
        ):
            registry = getattr(self, registry_name, {})
            if isinstance(registry, dict):
                values = registry.keys() if registry_name == "_lifecycle_background_tasks" else registry.values()
                for value in values:
                    task = value.get("task") if isinstance(value, dict) else value
                    if isinstance(task, asyncio.Task) and not task.done():
                        tasks.add(task)
        return len(tasks)

    def runtime_compatibility_status(self) -> dict[str, Any]:
        return self.runtime_capabilities.to_dict()

    def bot_personal_capability_status(self) -> dict[str, Any]:
        return dict(self.bot_personal_capabilities)

    def _repair_private_companion_handler_bindings(self) -> None:
        """热更新后强制把残留 handler 重新绑定到当前插件实例。"""
        try:
            module_path = str(getattr(type(self), "__module__", "") or "")
            package_prefix = module_path.rsplit(".", 1)[0] if "." in module_path else module_path
            if not package_prefix:
                return
            repaired = 0
            for handler in list(star_handlers_registry):
                handler_module_path = str(getattr(handler, "handler_module_path", "") or "")
                if not is_module_path_for_package(handler_module_path, package_prefix):
                    continue
                handler_name = str(getattr(handler, "handler_name", "") or "")
                if not handler_name:
                    continue
                current_func = getattr(type(self), handler_name, None)
                if not callable(current_func):
                    continue
                handler.handler = functools.partial(current_func, self)
                repaired += 1
            if repaired:
                logger.info("已修复热更新残留回调绑定: handlers=%s", repaired)
        except Exception as exc:
            logger.warning("修复热更新残留回调绑定失败: %s", _single_line(exc, 160))

    async def initialize(self):
        global _private_companion_plugin
        # Lifecycle tests execute this method in an isolated namespace that
        # only contains the class methods. Keep duplicate-instance fencing
        # active in the full module while allowing that reduced namespace to
        # exercise publication behavior.
        is_primary_instance = globals().get("_is_primary_plugin_instance")
        if not callable(is_primary_instance):
            is_primary_instance = lambda _instance: True
        with _private_companion_runtime.lock:
            active = _private_companion_runtime.active_plugin
            if (
                active is not None
                and active is not self
                and is_primary_instance(active)
                and not is_primary_instance(self)
            ):
                self._private_companion_duplicate_instance = True
                logger.warning(
                    "检测到同名 worktree 插件实例，已跳过其事件处理: module=%s",
                    type(self).__module__,
                )
                return
        await self._initialize_before_publication()
        try:
            await resume_story_handoff(self)
        except asyncio.CancelledError:
            raise
        except StoryAuthorityError as exc:
            # A durable marker is irreversible, but Content may legitimately
            # load later. Keep the Companion core available while Story stays
            # fenced and a later startup/API call replays the same marker.
            logger.warning(
                "Story handoff replay pending: code=%s",
                exc.code,
            )
        except Exception:
            logger.warning(
                "Story handoff replay pending: "
                "code=story_handoff_replay_failed"
            )
        # Keep the prior ready instance visible until every startup step succeeds.
        activate = getattr(self.extension_api, "_activate_story_migration_api", None)
        if not callable(activate) or not activate():
            return
        # No await may split activation, supersession, and global publication.
        with _private_companion_runtime.lock:
            store_manager = getattr(self, "store_manager", None)
            activate_persistence = getattr(
                store_manager, "activate_persistence_generation", None
            )
            if store_manager is not None and not callable(activate_persistence):
                raise RuntimeError("persistence generation activation is unavailable")
            if callable(activate_persistence):
                activate_persistence()
            else:
                owner_token = str(
                    getattr(self, "_persistence_owner_token", "") or ""
                ).strip()
                data_file = getattr(self, "data_file", None)
                if not owner_token or data_file is None or not str(data_file).strip():
                    raise RuntimeError(
                        "direct persistence generation activation is unavailable"
                    )
                activate_persistence_owner(owner_token, [data_file])
            previous = _private_companion_runtime.active_plugin
            if previous is not None and previous is not self:
                previous_api = getattr(previous, "extension_api", None)
                supersede = getattr(previous_api, "_supersede_story_migration_api", None)
                if callable(supersede):
                    supersede()
            _private_companion_runtime.active_plugin = self
        _private_companion_plugin = self

    async def _initialize_before_publication(self):
        self._repair_private_companion_handler_bindings()
        if getattr(self, "_legacy_enabled_config_disabled", False):
            logger.warning(
                "检测到旧版配置 enabled=false；该字段已废弃并被忽略。"
                "如需停用插件，请在 AstrBot 官方插件管理页关闭本插件。"
            )
        self._log_registered_command_handlers()
        self._install_send_message_to_user_tool_sanitizer()
        boundary_ability_registrar = getattr(self, "_register_relationship_boundary_proactive_ability", None)
        if callable(boundary_ability_registrar) and bool(runtime_persona_setting(self, 'enable_relationship_boundary_feedback', True)):
            boundary_ability_registrar()
        self._schedule_default_persona_prompt_refresh()
        await self._body_monitor_integration.set_enabled(self.enable_body_monitor_integration)
        needs_startup_save = False
        agenda_before = bool(getattr(self, "_agenda_migration_dirty", False))
        self._agenda_prepare_store()
        agenda_migration_changed = bool(
            getattr(self, "_agenda_migration_dirty", False) and not agenda_before
        )
        if agenda_migration_changed:
            needs_startup_save = True
        async with self._data_lock:
            changed = False
            raw_users = self.data.get("users") if isinstance(self.data, dict) else None
            if isinstance(raw_users, dict):
                cleaned_habit_users = 0
                for habit_user in raw_users.values():
                    if isinstance(habit_user, dict) and self._sanitize_user_behavior_habit_patterns(habit_user):
                        cleaned_habit_users += 1
                if cleaned_habit_users:
                    changed = True
                    logger.info(
                        "已清理旧版低质量用户习惯记录: users=%s",
                        cleaned_habit_users,
                    )
            if runtime_persona_setting(self, 'default_enable_configured_targets', True):
                self._sync_configured_targets()
                changed = True
            recovered_troubleshooting = self._recover_stale_troubleshooting_proactive_plans()
            if recovered_troubleshooting:
                logger.info("已恢复未完成的排障临时主动任务: %s", recovered_troubleshooting)
            if self._prime_enabled_user_schedules():
                changed = True
            if recovered_troubleshooting:
                changed = True
            if changed:
                needs_startup_save = True
        if needs_startup_save:
            # Initialization may combine an agenda schema migration with legacy
            # user repair and recovery across several durable roots. Keep this
            # one-time boundary explicit and distinguish migration from upkeep.
            if agenda_migration_changed:
                self._schedule_data_save(
                    full_scope="startup_migration",
                    delay=0.5,
                )
            else:
                self._schedule_data_save(
                    full_scope="startup_maintenance",
                    delay=0.5,
                )
        self._create_startup_background_task(
            "req041_automatic_migration",
            self._req041_initialize_automatic_migration,
        )
        self._create_startup_background_task(
            "req041_memory_scope_rebind",
            self._req041_run_memory_scope_rebind,
        )
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._scheduler_loop())
            logger.info("主动消息循环已启动")
        if self._startup_maintenance_task is None or self._startup_maintenance_task.done():
            self._startup_maintenance_task = asyncio.create_task(self._run_startup_background_maintenance())
        self._create_startup_background_task(
            "reset_stale_qq_presence",
            self._reset_stale_qq_presence_if_needed,
        )
        self._create_startup_background_task("prepare_today", self._startup_prepare_today)
        # Keep one orchestrator alive in multi-persona mode so each persona's
        # own enable/time/provider settings are evaluated inside its ContextVar.
        if runtime_persona_setting(self, 'enable_daily_review', True) or bool(getattr(self, "enable_multi_persona_mode", False)):
            self._create_startup_background_task("daily_review", self._daily_review_loop)
        if self.enable_balance_awareness:
            self._create_startup_background_task(
                "refresh_balance_awareness",
                self._maybe_refresh_balance_awareness,
            )
        self._create_startup_background_task(
            "refresh_passive_injection_cache",
            self._refresh_passive_injection_cache,
        )
        await self._proactive_chat_runtime_bridge.start()
        standalone_webui = getattr(self, "standalone_webui", None)
        if standalone_webui is not None:
            try:
                await standalone_webui.start()
            except Exception as exc:
                logger.warning(
                    "独立陪伴 WebUI 启动失败: %s",
                    _single_line(exc, 160),
                    exc_info=True,
                )

    def _create_startup_background_task(self, label: str, operation: Any) -> asyncio.Task:
        return task_manager(self).create_startup(label, operation)

    @asynccontextmanager
    async def _temporarily_release_data_lock(self):
        """Release the data lock for an external await, then reacquire it safely."""
        lock = getattr(self, "_data_lock", None)
        if lock is None or not lock.locked():
            yield
            return
        lock.release()
        reacquire_cancelled = False
        try:
            yield
        finally:
            while True:
                try:
                    await lock.acquire()
                    break
                except asyncio.CancelledError:
                    reacquire_cancelled = True
            if reacquire_cancelled:
                raise asyncio.CancelledError

    def _create_lifecycle_background_task(
        self,
        operation: Any,
        *,
        label: str,
    ) -> asyncio.Task | None:
        return task_manager(self).create_lifecycle(operation, label=label)

    async def _cancel_lifecycle_background_tasks(self, timeout: float = 3.0) -> None:
        await task_manager(self).cancel_lifecycle(timeout)

    @story_legacy_sync_operation("startup.story-maintenance")
    def _run_startup_data_maintenance_locked(self) -> bool:
        changed = False

        def run_step(label: str, func: Any) -> None:
            nonlocal changed
            started = time.perf_counter()
            try:
                if callable(func) and func():
                    changed = True
            except Exception as exc:
                logger.warning("启动后台维护步骤失败: %s error=%s", label, _single_line(exc, 160))
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if elapsed_ms > 1200:
                logger.warning("启动后台维护步骤耗时较高: step=%s elapsed=%sms", label, elapsed_ms)

        run_step("legacy_prompt_trace_cleanup", self._cleanup_legacy_proactive_prompt_traces)
        run_step("framework_meta_leak_cleanup", self._cleanup_framework_meta_leak_records)
        run_step("creative_fallback_cleanup", self._cleanup_legacy_creative_fallback_chunks)
        run_step("runtime_social_fact_sanitize", self._sanitize_runtime_social_facts_inplace)
        run_step("false_sleep_interaction_cleanup", self._cleanup_false_sleep_interaction_updates)
        run_step("private_user_alias_merge", self._merge_private_user_alias_records)
        run_step("reaction_expression_orphan_user_cleanup", self._cleanup_orphan_reaction_expression_users)
        run_step("group_slang_cleanup", self._cleanup_all_group_slang_terms)
        run_step("recall_image_cache_cleanup", lambda: self._cleanup_recall_message_image_cache(force=True))

        def cleanup_groups() -> bool:
            groups = self.data.get("groups") if isinstance(self.data.get("groups"), dict) else {}
            if not isinstance(groups, dict):
                return False
            group_changed = False
            cleaner = getattr(self, "_cleanup_group_members", None)
            edge_cleaner = getattr(self, "_cleanup_group_relationship_edges", None)
            for raw_group in groups.values():
                if not isinstance(raw_group, dict):
                    continue
                if callable(cleaner) and cleaner(raw_group):
                    group_changed = True
                if callable(edge_cleaner) and edge_cleaner(raw_group):
                    group_changed = True
            return group_changed

        run_step("group_record_cleanup", cleanup_groups)

        if runtime_persona_setting(self, 'worldbook_auto_import', True):
            run_step("worldbook_auto_import", self._import_worldbook_entries_from_sources)
        return changed

    async def _run_startup_background_maintenance(self) -> None:
        await asyncio.sleep(0)
        started = time.perf_counter()
        try:
            reconcile = getattr(self, "_reconcile_deleted_personas_async", None)
            if callable(reconcile):
                try:
                    self._persona_deleted_reconciliation_status = await reconcile()
                except Exception as exc:
                    self._persona_deleted_reconciliation_status = {"ok": False, "state": "degraded", "reason": "reconciliation_failed", "error": _single_line(exc, 180)}
                    logger.warning("启动已删除人格对账失败，保留现有插件数据: %s", _single_line(exc, 180))
            if bool(getattr(self, "_startup_photo_reference_catalog_migration_pending", False)):
                config_started = time.perf_counter()
                catalog_saved = await self._save_config_if_possible()
                if catalog_saved and _set_into_config(self.config, "photo_reference_catalog_version", CATALOG_VERSION):
                    self.photo_reference_catalog_version = CATALOG_VERSION
                    marker_saved = await self._save_config_if_possible()
                    if marker_saved:
                        self._startup_photo_reference_catalog_migration_pending = False
                        self.photo_reference_catalog_read_only = False
                        logger.info(
                            "参考图目录迁移完成: version=%s references=%s",
                            CATALOG_VERSION,
                            len(runtime_persona_setting(self, 'photo_reference_catalog', ()) or ()),
                        )
                    else:
                        logger.error("参考图目录已保存，但迁移版本号保存失败；下次启动会安全重试")
                elif not catalog_saved:
                    logger.error("参考图目录迁移保存失败，当前进程继续使用只读内存投影")
                else:
                    logger.error("参考图目录已保存，但迁移版本号无法写入；当前进程继续使用只读内存投影")
                elapsed_ms = int((time.perf_counter() - config_started) * 1000)
                if elapsed_ms > 1200:
                    logger.warning("启动后台配置保存耗时较高: elapsed=%sms", elapsed_ms)
            elif _safe_int(getattr(self, "_startup_config_migration_changes", 0), 0, 0) > 0:
                config_started = time.perf_counter()
                await self._save_config_if_possible()
                elapsed_ms = int((time.perf_counter() - config_started) * 1000)
                if elapsed_ms > 1200:
                    logger.warning("启动后台配置保存耗时较高: elapsed=%sms", elapsed_ms)
            try:
                await asyncio.wait_for(self._apply_sqlite_wal_optimizations(), timeout=20)
            except asyncio.TimeoutError:
                logger.warning("SQLite WAL 后台优化超时,已跳过本轮启动优化")
            await self._image_companion_maintenance()
            if self._nai_image_selected():
                await self._nai_image_maintenance()
            async with self._data_lock:
                if self._run_startup_data_maintenance_locked():
                    # This one-time pass can scrub legacy records across several
                    # roots, so it intentionally uses the startup maintenance scope.
                    self._save_data_sync(full_scope="startup_maintenance")
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if elapsed_ms > 1200:
                logger.info("启动后台维护完成: elapsed=%sms", elapsed_ms)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("启动后台维护失败: %s", _single_line(exc, 160), exc_info=True)

    async def terminate(self):
        global _private_companion_plugin
        await close_early_resources(self)
        await self._cancel_lifecycle_background_tasks()
        invalidate_bridge = getattr(self, "_memory_companion_invalidate_bridge_cache", None)
        if callable(invalidate_bridge):
            invalidate_bridge()
        scoped_sync = getattr(self, "req041_scoped_projection_sync", None)
        if scoped_sync is not None:
            mark_dirty = getattr(scoped_sync, "mark_dirty", None)
            if callable(mark_dirty):
                mark_dirty()
        self.req041_scoped_projection_sync = None
        self._req041_scoped_bridge = None

        runtime_bridge = getattr(self, "_proactive_chat_runtime_bridge", None)
        if runtime_bridge is not None:
            try:
                await runtime_bridge.stop()
            except Exception as exc:
                logger.warning(
                    "终止 Proactive Chat 深度联动失败: %s",
                    _single_line(exc, 160),
                )

        await cancel_registered_host_tasks(self)
        try:
            await asyncio.wait_for(self._flush_scheduled_data_save(), timeout=3.0)
        except asyncio.TimeoutError:
            logger.warning(
                "Scheduled persistence did not drain before "
                "shutdown; final persistence will continue in the background"
            )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.debug(
                "Waiting for scheduled persistence during "
                "shutdown failed: %s",
                _single_line(exc, 160),
            )
        self._termination_flush_already_attempted = True
        close_image_download_session = getattr(self, "_close_external_image_download_session", None)
        if callable(close_image_download_session):
            try:
                await asyncio.wait_for(close_image_download_session(), timeout=3.0)
            except asyncio.TimeoutError:
                logger.warning("终止时关闭在线图片下载会话超时")
            except Exception as exc:
                logger.debug("终止时关闭在线图片下载会话失败: %s", _single_line(exc, 160))
        final_save_task = asyncio.create_task(self._save_data_on_terminate())
        self._termination_save_task = final_save_task
        self._termination_save_status = {
            "state": "running",
            "started_at": asyncio.get_running_loop().time(),
            "task_id": id(final_save_task),
        }

        def observe_final_save(task: asyncio.Task) -> None:
            if task.cancelled():
                self._termination_save_status = {
                    **getattr(self, "_termination_save_status", {}),
                    "state": "cancelled",
                    "completed_at": asyncio.get_running_loop().time(),
                }
                if getattr(self, "_termination_save_task", None) is task:
                    self._termination_save_task = None
                return
            try:
                error = task.exception()
            except (asyncio.CancelledError, asyncio.InvalidStateError):
                return
            if error is not None:
                self._termination_save_status = {
                    **getattr(self, "_termination_save_status", {}),
                    "state": "failed",
                    "completed_at": asyncio.get_running_loop().time(),
                    "error": _single_line(error, 160),
                }
                logger.warning(
                    "Final shutdown persistence failed: %s",
                    _single_line(error, 160),
                )
            else:
                persistence = dict(
                    getattr(self, "_last_persistence_write_status", {}) or {}
                )
                self._termination_save_status = {
                    **getattr(self, "_termination_save_status", {}),
                    "state": (
                        "superseded"
                        if persistence.get("accepted") is False
                        else "completed"
                    ),
                    "completed_at": asyncio.get_running_loop().time(),
                    "persistence": persistence,
                }
            if getattr(self, "_termination_save_task", None) is task:
                self._termination_save_task = None

        final_save_task.add_done_callback(observe_final_save)
        try:
            await asyncio.wait_for(asyncio.shield(final_save_task), timeout=3.0)
        except asyncio.TimeoutError:
            self._termination_save_status = {
                **getattr(self, "_termination_save_status", {}),
                "state": "timed_out_background",
                "timed_out_at": asyncio.get_running_loop().time(),
            }
            logger.warning(
                "Final shutdown persistence timed out; the "
                "shielded task will continue in the background"
            )
        with _private_companion_runtime.lock:
            if _private_companion_runtime.active_plugin is self:
                _private_companion_runtime.active_plugin = None
        if _private_companion_plugin is self:
            _private_companion_plugin = None

    async def _save_data_on_terminate(self) -> None:
        if not bool(getattr(self, "_termination_flush_already_attempted", False)):
            await self._flush_scheduled_data_save()

        manager = getattr(self, "store_manager", None)
        manager_backend = str(getattr(manager, "backend_name", "") or "").lower()
        sqlite_incremental = bool(
            manager_backend == "sqlite"
            and callable(getattr(manager, "save_sections", None))
        )
        if sqlite_incremental:
            await self._flush_default_data_save_on_terminate()
        else:
            task = getattr(self, "_data_save_task", None)
            if (
                isinstance(task, asyncio.Task)
                and not task.done()
                and task is not asyncio.current_task()
            ):
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.debug(
                        "Waiting for the default JSON writer "
                        "during shutdown failed: %s",
                        _single_line(exc, 160),
                    )
            async with self._data_lock:
                snapshot = deepcopy(getattr(self, "_data_default", self.data))
            await asyncio.to_thread(self._write_data_snapshot_sync, snapshot)

        primary = self._primary_persona_id()
        if bool(getattr(self, "enable_multi_persona_mode", False)):
            profiles = getattr(self, "_persona_data_profiles", {}) or {}
            persona_ids = (
                [
                    str(persona_id)
                    for persona_id, profile in profiles.items()
                    if isinstance(profile, dict) and str(persona_id) != primary
                ]
                if isinstance(profiles, dict)
                else []
            )
            for persona_id in persona_ids:
                task = getattr(self, "_persona_data_save_tasks", {}).get(persona_id)
                if isinstance(task, asyncio.Task) and not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        pass
                    except Exception as exc:
                        logger.debug(
                            "Waiting for a persona writer during "
                            "shutdown failed: persona=%s error=%s",
                            persona_id,
                            _single_line(exc, 160),
                        )
                async with self._data_lock:
                    profile = getattr(self, "_persona_data_profiles", {}).get(persona_id)
                    if not isinstance(profile, dict):
                        continue
                    snapshot = deepcopy(profile)
                await asyncio.to_thread(
                    self._write_persona_data_snapshot_sync,
                    persona_id,
                    snapshot,
                )
