# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiMigrationPart01Mixin。

由 tools/split_mixin_domain.py 从 page_api_migration.py 机械抽取（20 个方法 + 0 个模块级名字 + 0 个类级赋值 / 497 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiMigrationMixin）。
"""
from __future__ import annotations

from .page_api_migration_shared import (
    EXTENSION_MIGRATION_NOTICE_VERSION,
    PLUGIN_NAME,
    _MIGRATION_UNKNOWN_CONFIG_KEY,
    _MIGRATION_UNKNOWN_MAX_BYTES,
    _MIGRATION_UNKNOWN_MAX_FIELDS,
    _MIGRATION_UNKNOWN_NAMESPACES,
    _MIGRATION_UNKNOWN_SENSITIVE_NAME,
    logger,
)
from .page_api_migration_shared import Any
from .page_api_migration_shared import _MISSING
from .page_api_migration_shared import _config_root_mapping
from .page_api_migration_shared import _flat_get
from .page_api_migration_shared import asyncio
from .page_api_migration_shared import deepcopy
from .page_api_migration_shared import json
from .page_api_migration_shared import re
from .page_api_migration_shared import request
from .page_api_migration_shared import story_legacy_operation_if
from .page_api_migration_shared import time



class PrivateCompanionPageApiMigrationPart01Mixin:
    """PrivateCompanionPageApiMigrationPart01Mixin（从 PrivateCompanionPageApiMigrationMixin 拆出）。"""


    async def get_extension_migration_notice(self) -> dict[str, Any]:
        """Read the persisted dismissal state for the extension migration notice."""
        version = EXTENSION_MIGRATION_NOTICE_VERSION
        try:
            async with self.plugin._data_lock:
                raw = self.plugin.data.get("extension_migration_notice_preferences")
                record = raw.get(version) if isinstance(raw, dict) else None
                dismissed = bool(record.get("dismissed")) if isinstance(record, dict) else False
            return self._ok({"version": version, "dismissed": dismissed})
        except Exception as exc:
            logger.debug("读取拓展迁移提示偏好失败: %s", self._single_line(exc, 160))
            return self._ok({"version": version, "dismissed": False, "persistent": False})

    async def update_extension_migration_notice(self) -> dict[str, Any]:
        """Persist the user's choice so embedded Page containers do not re-show it."""
        payload = await request.get_json(silent=True) or {}
        version = self._single_line(payload.get("version"), 40) or EXTENSION_MIGRATION_NOTICE_VERSION
        if version != EXTENSION_MIGRATION_NOTICE_VERSION:
            return self._error("无效的迁移提示版本")
        dismissed = payload.get("dismissed") is True
        try:
            async with self.plugin._data_lock:
                preferences = self.plugin.data.get("extension_migration_notice_preferences")
                if not isinstance(preferences, dict):
                    preferences = {}
                preferences = {
                    str(key): value
                    for key, value in preferences.items()
                    if isinstance(value, dict)
                }
                preferences[version] = {
                    "dismissed": dismissed,
                    "updated_at": time.time(),
                }
                self.plugin.data["extension_migration_notice_preferences"] = dict(list(preferences.items())[-12:])
                self.plugin._save_data_sync(sections={"extension_migration_notice_preferences"})
            return self._ok({"version": version, "dismissed": dismissed, "persistent": True})
        except Exception as exc:
            logger.warning("保存拓展迁移提示偏好失败: %s", self._single_line(exc, 160))
            return self._exception_error("保存迁移提示偏好失败")

    async def export_migration_config(self) -> dict[str, Any]:
        try:
            package = await self._build_migration_package(self._migration_export_options_from_request())
            return self._ok(package)
        except Exception as exc:
            logger.error(f"导出配置备份失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    async def list_migration_backups(self) -> dict[str, Any]:
        try:
            return self._ok({"items": self._list_migration_backup_items(limit=8)})
        except Exception as exc:
            logger.error(f"读取配置备份列表失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    async def restore_migration_backup(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        try:
            backup_id = self._single_line(payload.get("id") or payload.get("name"), 160)
            path = self._resolve_migration_backup_path(backup_id)
            package = json.loads(path.read_text(encoding="utf-8-sig"))
            package = self._extract_migration_package(package)
            normalized = self._normalize_migration_package(package)
            overview = await self._apply_migration_normalized(normalized, mode="replace", conflict="use_backup")
            if self._is_http_error_response(overview):
                return overview
            data = overview.get("data") if isinstance(overview.get("data"), dict) else {}
            data["message"] = "已从自动备份恢复。"
            data["restored_from"] = path.name
            overview["data"] = data
            return overview
        except Exception as exc:
            logger.error(f"恢复配置备份失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    async def preview_migration_config_import(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        try:
            package = self._extract_migration_package(
                payload,
                allow_checksum_mismatch=self._normalize_bool_value(payload.get("allow_checksum_mismatch")),
            )
            normalized = self._normalize_migration_package(package)
            summary = await self._migration_import_summary(normalized)
            summary["message"] = "已读取备份，确认后才会写入。"
            return self._ok(summary)
        except Exception as exc:
            logger.error(f"预览配置导入失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    async def apply_migration_config_import(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        try:
            package = self._extract_migration_package(
                payload,
                allow_checksum_mismatch=self._normalize_bool_value(payload.get("allow_checksum_mismatch")),
            )
            mode = str(payload.get("mode") or "merge").strip().lower()
            if mode not in {"merge", "replace"}:
                mode = "merge"
            conflict = str(payload.get("conflict") or "use_backup").strip().lower()
            if conflict not in {"use_backup", "keep_current", "fill_empty"}:
                conflict = "use_backup"
            normalized = self._normalize_migration_package(package)
            if normalized.get("legacy_snapshot") and mode == "replace":
                mode = "merge"
            return await self._apply_migration_normalized(normalized, mode=mode, conflict=conflict)
        except Exception as exc:
            logger.error(f"应用配置导入失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    def _migration_export_options_from_request(self) -> set[str]:
        raw = request.args.get("sections") or ""
        if not raw:
            return {"basic", "relations", "food_skills"}
        options = {part.strip().lower() for part in re.split(r"[,，\s]+", raw) if part.strip()}
        allowed = {"basic", "relations", "food_skills", "providers", "sensitive"}
        selected = {item for item in options if item in allowed}
        return selected or {"basic", "relations", "food_skills"}

    @staticmethod
    def _migration_unknown_key_allowed(value: Any) -> bool:
        return bool(
            type(value) is str
            and 0 < len(value) <= 120
            and not value.startswith("__")
            and _MIGRATION_UNKNOWN_SENSITIVE_NAME.search(value) is None
        )

    @classmethod
    def _bounded_migration_unknown_fields(cls, value: Any) -> dict[str, dict[str, Any]]:
        source = value if type(value) is dict else {}
        result: dict[str, dict[str, Any]] = {}
        total_bytes = 2
        total_fields = 0
        for namespace in _MIGRATION_UNKNOWN_NAMESPACES:
            raw_fields = source.get(namespace)
            if type(raw_fields) is not dict:
                continue
            kept: dict[str, Any] = {}
            for key in sorted(key for key in raw_fields if type(key) is str):
                if (
                    total_fields >= _MIGRATION_UNKNOWN_MAX_FIELDS
                    or not cls._migration_unknown_key_allowed(key)
                ):
                    continue
                try:
                    encoded = json.dumps(
                        [namespace, key, raw_fields[key]],
                        ensure_ascii=False,
                        allow_nan=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                except (TypeError, ValueError, UnicodeError):
                    continue
                if total_bytes + len(encoded) > _MIGRATION_UNKNOWN_MAX_BYTES:
                    continue
                kept[key] = deepcopy(raw_fields[key])
                total_bytes += len(encoded)
                total_fields += 1
            if kept:
                result[namespace] = kept
        return result

    def _migration_unknown_config_fields(self) -> dict[str, dict[str, Any]]:
        raw = self._config_get_raw(_MIGRATION_UNKNOWN_CONFIG_KEY, {})
        if type(raw) is str:
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, TypeError, ValueError):
                raw = {}
        return self._bounded_migration_unknown_fields(raw)

    @story_legacy_operation_if(
        "page.migration.story-apply",
        lambda self, normalized, **kwargs: (
            isinstance(normalized, dict)
            and isinstance(normalized.get("data"), dict)
            and "creative_projects" in normalized["data"]
        ),
    )
    async def _apply_migration_normalized(self, normalized: dict[str, Any], *, mode: str, conflict: str) -> dict[str, Any]:
        data_payload = normalized.get("data") if isinstance(normalized.get("data"), dict) else {}
        if data_payload:
            # Validate the normalized section set before changing config or live data.
            validator = getattr(self.plugin, "_validate_save_request", None)
            if not callable(validator):
                raise RuntimeError("migration section validator is unavailable")
            validator(set(data_payload), (), None)
        before = await self._build_migration_package(include_all=True)
        backup_path = self._write_migration_backup(before)

        config_root = _config_root_mapping(getattr(self.plugin, "config", None))
        if not isinstance(config_root, dict):
            raise RuntimeError("migration configuration store is unavailable")
        config_snapshot = deepcopy(config_root)
        async with self.plugin._data_lock:
            data_snapshot = deepcopy(self.plugin.data)
        try:
            return await self._commit_migration_normalized(
                normalized,
                mode=mode,
                conflict=conflict,
                backup_path=backup_path,
            )
        except BaseException as exc:
            rollback_error = await self._rollback_migration_normalized(
                config_snapshot,
                data_snapshot,
                normalized,
            )
            if rollback_error:
                raise RuntimeError(
                    f"配置导入失败，且回滚未完整: {rollback_error}"
                ) from exc
            raise

    async def _commit_migration_normalized(
        self,
        normalized: dict[str, Any],
        *,
        mode: str,
        conflict: str,
        backup_path: str,
    ) -> dict[str, Any]:
        data_payload = normalized.get("data") if isinstance(normalized.get("data"), dict) else {}

        changed_config: dict[str, Any] = {}
        incoming_unknown = self._bounded_migration_unknown_fields(
            normalized.get("unknown_config_fields")
        )
        if incoming_unknown:
            current_unknown = self._migration_unknown_config_fields()
            merged_unknown = deepcopy(current_unknown)
            self._deep_merge_dict(
                merged_unknown,
                incoming_unknown,
                conflict=conflict,
            )
            merged_unknown = self._bounded_migration_unknown_fields(merged_unknown)
            if merged_unknown != current_unknown:
                changed_config[_MIGRATION_UNKNOWN_CONFIG_KEY] = merged_unknown
        for key, value in normalized.get("features", {}).items():
            normalized_value = self._normalize_bool_value(value)
            current_value = self._normalize_bool_value(getattr(self.plugin, key, self._config_get(key)))
            if self._should_apply_migration_value(current_value, normalized_value, conflict):
                changed_config[key] = normalized_value
        for key, value in normalized.get("providers", {}).items():
            normalized_value = self._single_line(value, 160)
            current_value = self._single_line(self._provider_settings().get(key, ""), 160)
            if self._should_apply_migration_value(current_value, normalized_value, conflict):
                changed_config[key] = normalized_value
        for key, value in normalized.get("settings", {}).items():
            if key in {"storage_backend", "storage_sqlite_path"}:
                continue
            if key in {"group_whitelist_ids", "group_blacklist_ids"}:
                normalized_value = self._normalize_id_list(value)
            elif key == "group_access_mode":
                normalized_mode = str(value or "").strip().lower()
                normalized_value = normalized_mode if normalized_mode in {"whitelist", "blacklist"} else "whitelist"
            else:
                normalized_value = self._normalize_setting_value(key, value)
            current_value = self._migration_current_setting_value(key)
            if self._should_apply_migration_value(current_value, normalized_value, conflict):
                changed_config[key] = normalized_value
        for key, value in changed_config.items():
            self._apply_config_value(key, value, changed_config)
        if "enable_body_monitor_integration" in changed_config:
            runtime_task = getattr(
                self.plugin,
                "_body_monitor_integration_toggle_task",
                None,
            )
            if isinstance(runtime_task, asyncio.Task):
                await runtime_task
        if any(key in self._allowed_provider_keys() for key in changed_config) or "provider_config_mode" in changed_config:
            apply_quick = getattr(self.plugin, "_apply_quick_provider_defaults", None)
            if callable(apply_quick):
                apply_quick()
        config_saved = True
        if changed_config:
            config_saved = await self._save_config_if_possible()
            if not config_saved:
                raise RuntimeError("配置导入持久化失败")

        applied_sections: list[dict[str, Any]] = []
        if data_payload:
            async with self.plugin._data_lock:
                for section, imported_value in data_payload.items():
                    current_value = self.plugin.data.get(section)
                    if mode == "replace" or not isinstance(current_value, dict) or not isinstance(imported_value, dict):
                        self.plugin.data[section] = deepcopy(imported_value)
                    else:
                        merged = deepcopy(current_value)
                        self._deep_merge_dict(merged, imported_value, conflict=conflict)
                        self.plugin.data[section] = merged
                    applied_sections.append(
                        {
                            "key": section,
                            "label": self._migration_section_label(section),
                            "count": self._migration_count_items(imported_value),
                        }
                    )
                self.plugin._save_data_sync(sections=set(data_payload))
            self._refresh_migration_runtime_caches(data_payload)

        overview = await self.get_overview()
        if self._is_http_error_response(overview):
            return overview
        data = overview.get("data") if isinstance(overview.get("data"), dict) else {}
        checks = await self._migration_post_import_checks(config_saved=config_saved)
        data["message"] = "配置已导入。"
        data["mode"] = mode
        data["conflict"] = conflict
        data["backup_path"] = backup_path
        data["config_saved"] = config_saved
        data["changed_config_count"] = len(changed_config)
        data["applied_sections"] = applied_sections
        data["post_import_checks"] = checks
        data["migration_backups"] = self._list_migration_backup_items(limit=8)
        overview["data"] = data
        return overview

    async def _rollback_migration_normalized(
        self,
        config_snapshot: dict[str, Any],
        data_snapshot: dict[str, Any],
        normalized: dict[str, Any],
    ) -> str:
        errors: list[str] = []
        changed_keys = {
            str(key)
            for namespace in ("features", "providers", "settings")
            for key in (
                normalized.get(namespace).keys()
                if isinstance(normalized.get(namespace), dict)
                else ()
            )
        }
        # Reapply old values to reverse runtime-only side effects, then replace
        # the config root again so aliases/default projections cannot alter the
        # exact pre-import image.
        for key in sorted(changed_keys, reverse=True):
            old_value = _flat_get(config_snapshot, key, _MISSING)
            if old_value is _MISSING:
                continue
            try:
                self._apply_config_value(key, deepcopy(old_value), {})
            except Exception as rollback_exc:
                errors.append(f"runtime:{key}:{type(rollback_exc).__name__}")
        config_root = _config_root_mapping(getattr(self.plugin, "config", None))
        if isinstance(config_root, dict):
            try:
                config_root.clear()
                config_root.update(deepcopy(config_snapshot))
            except Exception as rollback_exc:
                errors.append(f"config-memory:{type(rollback_exc).__name__}")
        else:
            errors.append("config-memory:unavailable")

        runtime_task = getattr(
            self.plugin,
            "_body_monitor_integration_toggle_task",
            None,
        )
        if isinstance(runtime_task, asyncio.Task):
            try:
                await asyncio.shield(runtime_task)
            except (asyncio.CancelledError, Exception) as rollback_exc:
                errors.append(f"runtime-task:{type(rollback_exc).__name__}")

        try:
            async with self.plugin._data_lock:
                self.plugin.data.clear()
                self.plugin.data.update(deepcopy(data_snapshot))
                writer = getattr(self.plugin, "_write_data_snapshot_sync", None)
                if not callable(writer):
                    raise RuntimeError("data snapshot writer unavailable")
                writer(deepcopy(data_snapshot))
        except Exception as rollback_exc:
            errors.append(f"data:{type(rollback_exc).__name__}")
        try:
            if not await self._save_config_if_possible():
                errors.append("config-persist:false")
        except Exception as rollback_exc:
            errors.append(f"config-persist:{type(rollback_exc).__name__}")
        return ";".join(errors)

    async def _build_migration_package(self, options: set[str] | None = None, *, include_all: bool = False) -> dict[str, Any]:
        selected = {"basic", "relations", "food_skills", "providers", "sensitive"} if include_all else (options or {"basic", "relations", "food_skills"})
        async with self.plugin._data_lock:
            raw_data = deepcopy(self.plugin.data)
        package = {
            "kind": "private_companion_config_backup",
            "plugin": PLUGIN_NAME,
            "schema": 1,
            "version": self._plugin_version(),
            "exported_at": int(time.time()),
            "included_sections": sorted(selected),
            "settings": self._migration_settings_snapshot(selected),
            "features": self._migration_feature_snapshot(selected),
            "data": self._migration_data_snapshot(raw_data, selected),
            "excluded": [
                "Token 消耗统计",
                "图片/视觉摘要缓存",
                "最近消息和输入状态",
                "主动消息审计与冷却队列",
                "临时任务、排障记录和运行时缓存",
                "本机存储后端与 SQLite 路径",
            ],
        }
        if "providers" in selected:
            package["providers"] = self._migration_provider_snapshot()
        if "sensitive" in selected:
            for namespace, fields in self._migration_unknown_config_fields().items():
                target = package.setdefault(namespace, {})
                if not isinstance(target, dict):
                    continue
                for key, value in fields.items():
                    target.setdefault(key, deepcopy(value))
        package["checksum_algorithm"] = "sha256"
        package["checksum"] = self._migration_checksum(package)
        return package

    def _migration_settings_snapshot(self, selected: set[str]) -> dict[str, Any]:
        if "basic" not in selected and "sensitive" not in selected:
            return {}
        runtime = self._runtime_settings()
        allowed = self._allowed_setting_keys()
        settings = {}
        for key, value in runtime.items():
            if key in {"storage_backend", "storage_sqlite_path"}:
                continue
            if key not in allowed:
                continue
            group = self._migration_setting_group(key)
            if group == "sensitive":
                if "sensitive" in selected:
                    settings[key] = deepcopy(value)
            elif group == "providers":
                if "providers" in selected:
                    settings[key] = deepcopy(value)
            elif "basic" in selected:
                settings[key] = deepcopy(value)
        if "basic" in selected:
            settings["group_access_mode"] = str(getattr(self.plugin, "group_access_mode", "whitelist") or "whitelist")
            settings["group_whitelist_ids"] = list(getattr(self.plugin, "group_whitelist_ids", []) or [])
            settings["group_blacklist_ids"] = list(getattr(self.plugin, "group_blacklist_ids", []) or [])
        if "sensitive" in selected:
            qzone_cookie = self._config_get("QZONE_COOKIE") or str(getattr(self.plugin, "qzone_cookie", "") or "")
            if qzone_cookie:
                settings["QZONE_COOKIE"] = qzone_cookie
            search_api_key = (
                self._config_get("WEB_EXPLORATION_API_KEY")
                or str(getattr(self.plugin, "web_exploration_api_key", "") or "")
            )
            if search_api_key:
                settings["WEB_EXPLORATION_API_KEY"] = search_api_key
        return settings

    def _migration_feature_snapshot(self, selected: set[str]) -> dict[str, bool]:
        if "basic" not in selected and "sensitive" not in selected:
            return {}
        features: dict[str, bool] = {}
        for key in sorted(self._allowed_feature_keys()):
            group = self._migration_setting_group(key)
            if group == "sensitive" and "sensitive" not in selected:
                continue
            if group == "providers" and "providers" not in selected:
                continue
            if group not in {"sensitive", "providers"} and "basic" not in selected:
                continue
            if hasattr(self.plugin, key):
                features[key] = self._normalize_bool_value(getattr(self.plugin, key))
                continue
            raw = self._config_get(key)
            if raw != "":
                features[key] = self._normalize_bool_value(raw)
        return features

    def _migration_provider_snapshot(self) -> dict[str, str]:
        allowed = self._allowed_provider_keys()
        return {key: value for key, value in self._provider_settings().items() if key in allowed}

    def _migration_data_snapshot(self, raw_data: dict[str, Any], selected: set[str]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for section in self._migration_data_sections(selected):
            if section not in raw_data:
                continue
            value = deepcopy(raw_data.get(section))
            if section in {"users", "groups"}:
                value = self._strip_runtime_data(value)
            if self._migration_count_items(value) > 0:
                result[section] = value
        return result

    @classmethod
    def _migration_data_sections(cls, selected: set[str] | None = None) -> tuple[str, ...]:
        selected = selected or {"basic", "relations", "food_skills"}
        sections: list[str] = []
        if "relations" in selected:
            sections.extend(
                [
                    "users",
                    "groups",
                    "worldbook_entries",
                    "worldbook_member_profiles",
                    "worldbook_group_profiles",
                ]
            )
        if "food_skills" in selected:
            sections.extend(["skill_growth", "food_menu", "external_proactive_abilities", "important_dates", "can_do"])
        if "sensitive" in selected:
            sections.extend(["reading_archive_integration", "bookshelf_items"])
        return tuple(sections)
