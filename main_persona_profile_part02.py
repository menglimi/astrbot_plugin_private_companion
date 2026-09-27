# -*- coding: utf-8 -*-
"""PrivateCompanionPluginPersonaProfilePart02Mixin。

由 tools/split_mixin_domain.py 从 main_persona_profile.py 机械抽取（13 个方法 + 0 个模块级名字 + 0 个类级赋值 / 449 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPluginPersonaProfileMixin）。
"""
from __future__ import annotations

from .main_persona_profile_shared import logger
from .main_persona_profile_shared import Any
from .main_persona_profile_shared import PERSONA_SETTINGS_KEY
from .main_persona_profile_shared import PERSONA_SETTINGS_REVISION_KEY
from .main_persona_profile_shared import PERSONA_SETTINGS_SCHEMA_VERSION
from .main_persona_profile_shared import PERSONA_SETTINGS_VERSION_KEY
from .main_persona_profile_shared import PersonaConfigError
from .main_persona_profile_shared import _set_into_config
from .main_persona_profile_shared import _single_line
from .main_persona_profile_shared import asyncio
from .main_persona_profile_shared import copy_from_primary_config
from .main_persona_profile_shared import create_persona_settings
from .main_persona_profile_shared import deepcopy
from .main_persona_profile_shared import detach_persona_settings
from .main_persona_profile_shared import dispatch_runtime_config_effects
from .main_persona_profile_shared import normalize_persona_settings
from .main_persona_profile_shared import normalize_setting_value
from .main_persona_profile_shared import re
from .main_persona_profile_shared import story_legacy_operation
from .main_persona_profile_shared import story_legacy_sync_operation



class PrivateCompanionPluginPersonaProfilePart02Mixin:
    """PrivateCompanionPluginPersonaProfilePart02Mixin（从 PrivateCompanionPluginPersonaProfileMixin 拆出）。"""


    async def _reconcile_deleted_personas_async(self) -> dict[str, Any]:
        """Reconcile plugin persona state with AstrBot's official persona list."""
        official_ids, reason = self._astrbot_persona_ids_snapshot()
        result: dict[str, Any] = {"ok": official_ids is not None, "state": "verified" if official_ids is not None else "unverifiable", "reason": reason, "removed": [], "backups": {}}
        if official_ids is None:
            logger.info("跳过已删除人格对账: AstrBot 人格列表不可验证 reason=%s", reason)
            return result
        primary = self._primary_persona_id()
        configured = self._configured_multi_persona_ids()
        try:
            candidates = set(self._persona_profile_ids())
        except Exception:
            candidates = set(configured)
        stale = sorted(pid for pid in candidates if pid and pid != primary and pid not in official_ids)
        if not stale:
            return result
        next_ids = [pid for pid in configured if pid == primary or pid in official_ids]
        config_changed = next_ids != configured
        before_config = deepcopy(self._cfg_raw(self.config, "multi_persona_ids", []))
        if config_changed:
            _set_into_config(self.config, "multi_persona_ids", next_ids)
            try:
                if not bool(await self._save_config_if_possible()):
                    raise RuntimeError("AstrBot 配置未能持久化")
            except Exception as exc:
                _set_into_config(self.config, "multi_persona_ids", before_config)
                result.update({"ok": False, "state": "degraded", "reason": "config_save_failed", "error": _single_line(exc, 180)})
                logger.warning("已删除人格对账未完成，配置保存失败: %s", _single_line(exc, 180))
                return result
        self.multi_persona_ids = next_ids
        for pid in stale:
            retired = self._retire_deleted_persona_store_sync(pid)
            if retired["removed"] and not retired["failed"]:
                result["removed"].append(pid)
            if retired["failed"]:
                result.setdefault("failed", {})[pid] = retired["failed"]
            if retired.get("backups"):
                result["backups"][pid] = retired["backups"]
        result["config_changed"] = config_changed
        if result["removed"] or config_changed:
            logger.info("已同步 AstrBot 删除的人格: removed=%s config_changed=%s", ",".join(result["removed"]) or "-", config_changed)
        return result

    async def _conversation_persona_id_for_event(self, event: Any) -> str:
        """Compatibility wrapper returning AstrBot's final effective persona."""
        resolved = await self._astrbot_effective_persona_for_event(event)
        return self._sanitize_persona_id(resolved.get("persona_id"))

    def _clear_persona_runtime_cache(self, profile: dict[str, Any]) -> None:
        if not isinstance(profile, dict):
            return
        for key in tuple(profile.keys()):
            lowered = str(key).lower()
            if "cache" in lowered or lowered in {"conversation_history", "recent_context", "pending_context"}:
                profile.pop(key, None)

    @story_legacy_sync_operation("persona.profile.migrate")
    def _migrate_persona_profile(self, source_persona_id: Any, target_persona_id: Any, keys: list[Any]) -> dict[str, Any]:
        source = self._sanitize_persona_id(source_persona_id)
        target = self._sanitize_persona_id(target_persona_id)
        if not source or not target or source == target:
            return {"ok": False, "message": "源人格和目标人格必须不同"}
        if not bool(getattr(self, "enable_multi_persona_mode", False)):
            return {
                "ok": False,
                "code": "persona_migration_multi_persona_disabled",
                "message": "请先开启多人格模式后再迁移人格资料",
            }
        enabled_ids = set(self._configured_multi_persona_ids())
        if source not in enabled_ids:
            return {
                "ok": False,
                "code": "persona_migration_source_not_enabled",
                "message": "来源人格未在已保存的人格拓扑中启用",
            }
        if target not in enabled_ids:
            return {
                "ok": False,
                "code": "persona_migration_target_not_enabled",
                "message": "目标人格未在已保存的人格拓扑中启用",
            }
        primary = self._primary_persona_id()

        def eligible(persona_id: str) -> bool:
            # The primary intentionally has no separate persona JSON; its
            # authoritative data is the single-persona store.
            return persona_id == primary or self._persona_config_exists(persona_id)

        if not eligible(source):
            return {
                "ok": False,
                "code": "persona_migration_source_config_missing",
                "message": "来源人格尚未建立有效的人格配置文件",
            }
        if not eligible(target):
            return {
                "ok": False,
                "code": "persona_migration_target_config_missing",
                "message": "目标人格尚未建立有效的人格配置文件",
            }
        source_data = self._ensure_persona_profile(source)
        target_data = self._ensure_persona_profile(target)
        source_before = deepcopy(source_data)
        target_before = deepcopy(target_data)
        source_next = deepcopy(source_data)
        target_next = deepcopy(target_data)
        selected = [str(key).strip() for key in keys if str(key).strip()]
        if not selected:
            selected = ["daily_plan", "daily_state", "bot_diaries", "users", "groups", "memo_notes", "token_usage"]
        migration_keys = list(selected)
        if "bot_diaries" in migration_keys:
            for companion_key in (
                "diary_generated_day",
                "daily_diary_deleted_days",
                "daily_diary_delete_revision",
            ):
                if companion_key not in migration_keys:
                    migration_keys.append(companion_key)
        for key in migration_keys:
            source_settings = source_next.get("persona_settings") if isinstance(source_next.get("persona_settings"), dict) else {}
            target_settings = target_next.setdefault("persona_settings", {})
            if key in source_settings:
                target_settings[key] = deepcopy(source_settings[key])
            elif key in source_next:
                target_next[key] = deepcopy(source_next[key])
        if "bot_diaries" in migration_keys:
            diaries = source_next.get("bot_diaries")
            diary_days: list[str] = []
            if isinstance(diaries, list):
                diary_days = [
                    _single_line(item.get("date"), 16)
                    for item in diaries
                    if isinstance(item, dict)
                ]
            elif isinstance(diaries, dict):
                diary_days = [
                    _single_line(
                        (item.get("date") if isinstance(item, dict) else "") or stored_date,
                        16,
                    )
                    for stored_date, item in diaries.items()
                ]
            valid_days = [day for day in diary_days if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day)]
            source_marker = _single_line(source_next.get("diary_generated_day"), 16)
            target_next["diary_generated_day"] = (
                source_marker
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", source_marker)
                else max(valid_days, default="")
            )
            source_deleted_days = source_next.get("daily_diary_deleted_days")
            target_next["daily_diary_deleted_days"] = deepcopy(
                source_deleted_days if isinstance(source_deleted_days, list) else []
            )
            try:
                target_next["daily_diary_delete_revision"] = max(
                    0,
                    int(source_next.get("daily_diary_delete_revision") or 0),
                )
            except (TypeError, ValueError, OverflowError):
                target_next["daily_diary_delete_revision"] = 0
        self._clear_persona_runtime_cache(source_next)
        self._clear_persona_runtime_cache(target_next)
        try:
            self._save_persona_profile_sync(source, source_next)
            self._save_persona_profile_sync(target, target_next)
        except Exception as exc:
            for persona_id, previous in ((source, source_before), (target, target_before)):
                try:
                    self._save_persona_profile_sync(persona_id, previous)
                except Exception:
                    pass
            return {
                "ok": False,
                "message": f"人格资料迁移落盘失败: {_single_line(exc, 120)}",
            }
        source_data.clear()
        source_data.update(source_next)
        target_data.clear()
        target_data.update(target_next)
        self._reset_persona_prompt_caches(source, target)
        return {"ok": True, "source_persona_id": source, "target_persona_id": target, "keys": migration_keys, "cache_cleared": True}

    @story_legacy_operation("persona.profile.migrate-transaction")
    async def _migrate_persona_profile_async(
        self,
        source_persona_id: Any,
        target_persona_id: Any,
        keys: list[Any],
    ) -> dict[str, Any]:
        await self._flush_scheduled_data_save()
        async with self._data_lock:
            return await asyncio.to_thread(
                self._migrate_persona_profile,
                source_persona_id,
                target_persona_id,
                keys,
            )

    def _switch_persona_for_window(
        self,
        persona_id: Any,
        *,
        window_key: str = "",
        source_persona_id: str = "",
        migrate_keys: list[Any] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "status_code": 410,
            "code": "plugin_persona_routing_removed",
            "message": "窗口人格由 AstrBot 管理，插件不再提供窗口绑定",
            "routing_authority": "astrbot",
        }

    async def _switch_persona_for_window_async(
        self,
        *args,
        persist: bool = False,
        **kwargs,
    ) -> dict[str, Any]:
        return self._switch_persona_for_window(
            args[0] if args else kwargs.get("persona_id", "")
        )

    @story_legacy_sync_operation("persona.mode.transition")
    def _prepare_multi_persona_transition(self, enabled: bool) -> None:
        """Keep the canonical single store authoritative across mode changes."""
        current = bool(getattr(self, "enable_multi_persona_mode", False))
        if current == bool(enabled):
            return
        if enabled:
            primary = self._primary_persona_id()
            if not primary:
                raise PersonaConfigError("开启多人格前必须先补充插件指定人格 ID")
            if not self._astrbot_persona_exists(primary):
                raise PersonaConfigError("插件指定人格在 AstrBot 中不存在，请先重新选择")
            ids = self._configured_multi_persona_ids()
            if primary not in ids:
                ids.insert(0, primary)
            self.multi_persona_ids = ids
        else:
            self._write_data_snapshot_sync(deepcopy(self._data_default))

    async def _create_persona_config_async(
        self,
        persona_id: Any,
        *,
        bot_name: Any,
        mode: Any,
        source_persona_id: Any = "",
        recovery: bool = False,
    ) -> dict[str, Any]:
        pid = self._sanitize_persona_id(persona_id)
        name = _single_line(bot_name, 80)
        primary = self._primary_persona_id()
        if not pid or pid == primary:
            return {"ok": False, "code": "persona_config_target_invalid", "message": "请选择非主人格作为新配置目标"}
        if pid not in set(self._configured_multi_persona_ids()):
            return {"ok": False, "code": "persona_config_target_not_enabled", "message": "请先保存人格拓扑，再为该人格创建配置"}
        if not name:
            return {"ok": False, "code": "persona_bot_name_required", "message": "Bot 名字不能为空"}
        create_mode = str(mode or "follow_primary").strip().lower()
        source_id = self._sanitize_persona_id(source_persona_id)
        if create_mode == "copy":
            if not source_id or source_id == pid:
                return {"ok": False, "code": "persona_config_invalid", "message": "请选择不同的来源人格"}
            if source_id != primary and not self._persona_config_exists(source_id):
                return {"ok": False, "code": "persona_config_invalid", "message": "复制来源尚未创建独立人格配置"}
        await self._flush_scheduled_data_save()
        async with self._data_lock:
            existed = self._secondary_persona_store_exists(pid) or pid in getattr(
                self, "_persona_data_profiles", {}
            )
            profile = deepcopy(self._ensure_persona_profile(pid))
            current_settings = profile.get(PERSONA_SETTINGS_KEY)
            profile_degraded = pid in set(getattr(self, "_persona_profile_errors", {}))
            if existed and isinstance(current_settings, dict) and current_settings and not (recovery and profile_degraded):
                return {"ok": False, "code": "persona_config_exists", "message": "该人格配置已存在，请直接编辑或恢复使用"}
            try:
                if create_mode == "copy":
                    if source_id == primary:
                        settings = copy_from_primary_config(
                            self._primary_persona_config(),
                            bot_name=name,
                            manifest=self._persona_scope_manifest(),
                        )
                    else:
                        source_profile = self._ensure_persona_profile(source_id)
                        settings = create_persona_settings(
                            "copy",
                            bot_name=name,
                            source_settings=source_profile.get(PERSONA_SETTINGS_KEY) or {},
                            manifest=self._persona_scope_manifest(),
                        )
                else:
                    settings = create_persona_settings(
                        create_mode,
                        bot_name=name,
                        primary_config=self._primary_persona_config(),
                        manifest=self._persona_scope_manifest(),
                        normalizer=normalize_setting_value,
                    )
            except PersonaConfigError as exc:
                return {"ok": False, "code": "persona_config_invalid", "message": str(exc)}
            before_config = deepcopy(self._cfg_raw(self.config, "multi_persona_ids", []))
            before_profile = deepcopy(profile)
            next_profile = deepcopy(profile)
            database_path = self._persona_profile_db_path(pid)
            next_profile[PERSONA_SETTINGS_KEY] = settings
            next_profile[PERSONA_SETTINGS_VERSION_KEY] = PERSONA_SETTINGS_SCHEMA_VERSION
            next_profile[PERSONA_SETTINGS_REVISION_KEY] = max(1, int(profile.get(PERSONA_SETTINGS_REVISION_KEY) or 0) + 1)
            ids = self._configured_multi_persona_ids()
            if pid not in ids:
                ids.append(pid)
            try:
                await self._save_persona_profile_async(pid, next_profile)
                _set_into_config(self.config, "multi_persona_ids", ids)
                config_saved = bool(await self._save_config_if_possible())
                if not config_saved:
                    raise RuntimeError("AstrBot 配置未能持久化")
            except Exception as exc:
                _set_into_config(self.config, "multi_persona_ids", before_config)
                try:
                    if existed:
                        await self._save_persona_profile_async(pid, before_profile)
                    else:
                        registry = getattr(self, "_persona_sqlite_store_registry", None)
                        discard = getattr(registry, "discard", None)
                        if callable(discard):
                            discard(database_path)
                        database_path.unlink(missing_ok=True)
                        database_path.with_name(database_path.name + "-wal").unlink(missing_ok=True)
                        database_path.with_name(database_path.name + "-shm").unlink(missing_ok=True)
                except Exception:
                    pass
                return {"ok": False, "code": "persona_config_persistence_failed", "message": f"人格配置保存失败，已回滚: {_single_line(exc, 120)}"}
            live = self._ensure_persona_profile(pid)
            live.clear()
            live.update(next_profile)
            self.multi_persona_ids = ids
            self._reset_persona_prompt_caches(pid)
            return {"ok": True, "created": True, **self._persona_config_state(pid)}

    async def _update_persona_settings_async(
        self,
        persona_id: Any,
        *,
        changes: Any,
        follow_primary_keys: Any,
        expected_revision: Any,
    ) -> dict[str, Any]:
        pid = self._sanitize_persona_id(persona_id)
        primary = self._primary_persona_id()
        if not pid or pid == primary:
            return {"ok": False, "code": "persona_settings_target_invalid", "message": "主人格请使用现有通用配置保存接口"}
        if not self._persona_config_exists(pid):
            return {"ok": False, "code": "persona_config_missing", "message": "该人格尚未创建独立配置"}
        if not isinstance(changes, dict) or not isinstance(follow_primary_keys, list):
            return {"ok": False, "code": "persona_settings_payload_invalid", "message": "人格配置更新格式无效"}
        manifest = self._persona_scope_manifest()
        overlap = set(changes) & {str(key) for key in follow_primary_keys}
        if overlap:
            return {"ok": False, "code": "persona_settings_overlap", "message": "同一配置项不能同时覆盖和恢复跟随"}
        await self._flush_scheduled_data_save()
        changed_keys = sorted(set(changes) | set(map(str, follow_primary_keys)))
        async with self._data_lock:
            profile = self._ensure_persona_profile(pid)
            revision = int(profile.get(PERSONA_SETTINGS_REVISION_KEY) or 0)
            try:
                expected = int(expected_revision)
            except (TypeError, ValueError):
                expected = revision
            if expected != revision:
                return {"ok": False, "status_code": 409, "code": "persona_settings_revision_conflict", "message": "人格配置已被其他页面修改", "revision": revision}
            next_profile = deepcopy(profile)
            raw = deepcopy(next_profile.get(PERSONA_SETTINGS_KEY) or {})
            for key, value in changes.items():
                entry = manifest.get(str(key))
                if not entry or entry.get("scope") != "persona":
                    return {"ok": False, "code": "persona_setting_not_allowed", "message": f"配置项不允许按人格覆盖: {key}"}
                raw[str(key)] = normalize_setting_value(str(key), value, entry)
            for raw_key in follow_primary_keys:
                key = str(raw_key)
                entry = manifest.get(key)
                if not entry or entry.get("scope") != "persona":
                    return {"ok": False, "code": "persona_setting_not_allowed", "message": f"配置项不允许按人格跟随: {key}"}
                if entry.get("identity"):
                    return {"ok": False, "code": "persona_identity_cannot_follow", "message": f"身份配置不能跟随主人格: {key}"}
                raw.pop(key, None)
            if not str(raw.get("bot_name") or "").strip():
                return {"ok": False, "code": "persona_bot_name_required", "message": "Bot 名字不能为空"}
            next_profile[PERSONA_SETTINGS_KEY] = normalize_persona_settings(raw, manifest=manifest, preserve_unknown=True)
            next_profile[PERSONA_SETTINGS_VERSION_KEY] = PERSONA_SETTINGS_SCHEMA_VERSION
            next_profile[PERSONA_SETTINGS_REVISION_KEY] = revision + 1
            if any(
                self._persona_setting_invalidates_runtime_cache(key, manifest)
                for key in changed_keys
            ):
                self._clear_persona_runtime_cache(next_profile)
            try:
                await self._save_persona_profile_async(pid, next_profile)
            except Exception as exc:
                return {"ok": False, "code": "persona_settings_persistence_failed", "message": f"人格配置保存失败: {_single_line(exc, 120)}"}
            profile.clear()
            profile.update(next_profile)
            result = {
                "ok": True,
                "changed": changed_keys,
                **self._persona_config_state(pid),
            }
        self._apply_persona_setting_hot_effects(pid, changed_keys)
        return result

    def _apply_persona_setting_hot_effects(
        self,
        persona_id: str,
        changed_keys: list[str],
    ) -> None:
        """Compatibility adapter to the sole runtime side-effect dispatcher."""
        dispatch_runtime_config_effects(
            self,
            {str(key): None for key in changed_keys},
            scope="persona",
            persona_id=persona_id,
            source="persona",
        )

    async def _detach_persona_settings_async(self, persona_id: Any, *, expected_revision: Any, preview_hash: Any) -> dict[str, Any]:
        preview = self._persona_detach_preview(persona_id)
        if not preview.get("ok"):
            return preview
        if str(preview_hash or "") != preview["preview_hash"]:
            return {"ok": False, "status_code": 409, "code": "persona_detach_preview_stale", "message": "脱离预览已过期，请重新预览"}
        return await self._update_persona_settings_async(
            preview["persona_id"],
            changes=detach_persona_settings(
                self._ensure_persona_profile(preview["persona_id"]).get(PERSONA_SETTINGS_KEY) or {},
                self._primary_persona_config(),
                manifest=self._persona_scope_manifest(),
            ),
            follow_primary_keys=[],
            expected_revision=expected_revision,
        )

    async def _mutate_persona_window_binding_async(
        self,
        *,
        action: str,
        window_key: Any,
        persona_id: Any = "",
        previous_window_key: Any = "",
        expected_revision: Any = None,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "status_code": 410,
            "code": "plugin_persona_routing_removed",
            "message": "窗口人格由 AstrBot 管理，旧插件绑定只读保留",
            "routing_authority": "astrbot",
        }
