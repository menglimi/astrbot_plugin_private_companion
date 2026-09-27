# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiMigrationPart02Mixin。

由 tools/split_mixin_domain.py 从 page_api_migration.py 机械抽取（26 个方法 + 0 个模块级名字 + 0 个类级赋值 / 459 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiMigrationMixin）。
"""
from __future__ import annotations

from .page_api_migration_shared import PLUGIN_NAME
from .page_api_migration_shared import Any
from .page_api_migration_shared import MigrationBackupService
from .page_api_migration_shared import Path
from .page_api_migration_shared import deepcopy
from .page_api_migration_shared import hashlib
from .page_api_migration_shared import json
from .page_api_migration_shared import re
from .page_api_migration_shared import secrets
from .page_api_migration_shared import time



class PrivateCompanionPageApiMigrationPart02Mixin:
    """PrivateCompanionPageApiMigrationPart02Mixin（从 PrivateCompanionPageApiMigrationMixin 拆出）。"""


    @staticmethod
    def _migration_setting_group(key: str) -> str:
        text = str(key)
        if text in {"storage_backend", "storage_sqlite_path"}:
            return "environment"
        if text == "provider_config_mode":
            return "providers"
        if text == "QZONE_COOKIE":
            return "sensitive"
        if text == "WEB_EXPLORATION_API_KEY":
            return "sensitive"
        if text.startswith("reading_archive_") or text.startswith("enable_reading_archive_"):
            return "sensitive"
        if text in {"READING_ARCHIVE_VISION_PROVIDER_ID"}:
            return "sensitive"
        if text.endswith("_PROVIDER_ID") or text in {"LLM_PROVIDER_ID", "tts_conversion_provider_id"}:
            return "providers"
        return "basic"

    @staticmethod
    def _migration_section_label(key: str) -> str:
        return {
            "users": "私聊对象资料",
            "groups": "群聊观测资料",
            "worldbook_entries": "关系网原始条目",
            "worldbook_member_profiles": "关系网成员资料",
            "worldbook_group_profiles": "关系网群资料",
            "skill_growth": "技能熟练度",
            "food_menu": "吃什么候选菜单",
            "external_proactive_abilities": "外部主动能力",
            "important_dates": "重要日期",
            "can_do": "自定义能力",
            "reading_archive_integration": "资料归档状态",
            "bookshelf_items": "资料柜阅读记录",
        }.get(key, key)

    def _extract_migration_package(self, payload: Any, *, allow_checksum_mismatch: bool = False) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("导入内容必须是 JSON 对象")
        package = self._unwrap_migration_package_payload(payload)
        if not isinstance(package, dict):
            raise ValueError("没有读取到可导入的配置备份")
        if package.get("kind") != "private_companion_config_backup" or package.get("plugin") != PLUGIN_NAME:
            legacy = self._legacy_snapshot_to_migration_package(package)
            if legacy is None:
                raise ValueError("这不是 Private Companion 的配置备份")
            return legacy
        checksum = str(package.get("checksum") or "").strip()
        if checksum and not self._migration_checksum_matches(package, checksum):
            if not allow_checksum_mismatch:
                raise ValueError("备份校验失败：文件可能被截断或手动修改过。如确认只是手动脱敏/删改敏感字段，请勾选“校验失败仍继续预览/导入”。")
            package = deepcopy(package)
            package["_checksum_mismatch_allowed"] = True
        return package

    def _unwrap_migration_package_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = payload
        for _ in range(4):
            if not isinstance(current, dict):
                return {}
            if current.get("kind") == "private_companion_config_backup" or (
                isinstance(current.get("overview"), dict)
                and ("users" in current or "groups" in current)
            ):
                return current
            for key in ("package", "data", "payload", "result"):
                nested = current.get(key)
                if isinstance(nested, dict):
                    current = nested
                    break
            else:
                return current
        return current if isinstance(current, dict) else {}

    def _legacy_snapshot_to_migration_package(self, package: dict[str, Any]) -> dict[str, Any] | None:
        overview = package.get("overview") if isinstance(package.get("overview"), dict) else {}
        has_snapshot_shape = bool(overview) and ("users" in package or "groups" in package)
        if not has_snapshot_shape:
            return None

        settings: dict[str, Any] = {}
        raw_settings = overview.get("settings") if isinstance(overview.get("settings"), dict) else {}
        for key, value in raw_settings.items():
            if key in {"storage_backend", "storage_sqlite_path"}:
                continue
            if key in self._allowed_setting_keys():
                settings[key] = deepcopy(value)
        group_overview = overview.get("group") if isinstance(overview.get("group"), dict) else {}
        if group_overview:
            settings["group_access_mode"] = str(group_overview.get("access_mode") or "whitelist")
            settings["group_whitelist_ids"] = self._normalize_id_list(group_overview.get("whitelist"))
            settings["group_blacklist_ids"] = self._normalize_id_list(group_overview.get("blacklist"))

        features: dict[str, bool] = {}
        raw_features = overview.get("features") if isinstance(overview.get("features"), dict) else {}
        for key, value in raw_features.items():
            if key in self._allowed_feature_keys():
                features[key] = self._normalize_bool_value(value)

        providers: dict[str, str] = {}
        raw_providers = overview.get("providers") if isinstance(overview.get("providers"), dict) else {}
        for key, value in raw_providers.items():
            if key in self._allowed_provider_keys():
                providers[key] = self._single_line(value, 160)

        converted = {
            "kind": "private_companion_config_backup",
            "plugin": PLUGIN_NAME,
            "schema": 1,
            "version": str(package.get("version") or overview.get("plugin", {}).get("version") or self._plugin_version()),
            "exported_at": int(time.time()),
            "included_sections": ["basic", "relations"],
            "settings": settings,
            "features": features,
            "providers": providers,
            "data": {},
            "legacy_snapshot": True,
            "excluded": [
                "由旧版页面快照转换；仅导入快照中可识别的配置、名单、开关和模型指向",
                "旧版页面快照中的私聊/群聊列表是展示摘要，不会写回数据文件",
                "最近消息、缓存、Token、运行日志和临时队列不会导入",
            ],
        }
        converted["checksum_algorithm"] = "sha256"
        converted["checksum"] = self._migration_checksum(converted)
        return converted

    def _normalize_migration_package(self, package: dict[str, Any]) -> dict[str, Any]:
        settings: dict[str, Any] = {}
        features: dict[str, bool] = {}
        providers: dict[str, str] = {}
        ignored: list[str] = []
        unknown_config_fields: dict[str, dict[str, Any]] = {}

        def preserve_unknown(namespace: str, key: Any, value: Any) -> None:
            if self._migration_unknown_key_allowed(key):
                unknown_config_fields.setdefault(namespace, {})[str(key)] = deepcopy(value)
            else:
                ignored.append(str(key))

        raw_settings = package.get("settings") if isinstance(package.get("settings"), dict) else {}
        for key, value in raw_settings.items():
            if key in {"storage_backend", "storage_sqlite_path"}:
                ignored.append(str(key))
                continue
            if key == "group_access_mode":
                mode = str(value or "").strip().lower()
                if mode in {"whitelist", "blacklist"}:
                    settings[key] = mode
                continue
            if key in {"group_whitelist_ids", "group_blacklist_ids"}:
                settings[key] = self._normalize_id_list(value)
                continue
            if key in self._allowed_setting_keys():
                settings[key] = self._normalize_setting_value(key, value)
            else:
                preserve_unknown("settings", key, value)

        raw_features = package.get("features") if isinstance(package.get("features"), dict) else {}
        for key, value in raw_features.items():
            if key in self._allowed_feature_keys():
                features[key] = self._normalize_bool_value(value)
            else:
                preserve_unknown("features", key, value)

        raw_providers = package.get("providers") if isinstance(package.get("providers"), dict) else {}
        for key, value in raw_providers.items():
            if key in self._allowed_provider_keys():
                providers[key] = self._single_line(value, 160)
            else:
                preserve_unknown("providers", key, value)

        raw_data = package.get("data") if isinstance(package.get("data"), dict) else {}
        data: dict[str, Any] = {}
        for section in self._migration_data_sections({"relations", "food_skills", "sensitive"}):
            if section not in raw_data:
                continue
            value = deepcopy(raw_data.get(section))
            if section in {"users", "groups"}:
                value = self._strip_runtime_data(value)
            data[section] = value
        for section in raw_data:
            if section not in data:
                ignored.append(str(section))

        bounded_unknown = self._bounded_migration_unknown_fields(
            unknown_config_fields
        )
        preserved_unknown = sorted(
            f"{namespace}.{key}"
            for namespace, fields in bounded_unknown.items()
            for key in fields
        )
        return {
            "version": package.get("version"),
            "exported_at": package.get("exported_at"),
            "included_sections": [str(item) for item in package.get("included_sections", []) if str(item).strip()],
            "checksum": str(package.get("checksum") or ""),
            "checksum_ok": bool(package.get("checksum")) and self._migration_checksum_matches(package, str(package.get("checksum") or "")),
            "checksum_bypassed": bool(package.get("_checksum_mismatch_allowed")),
            "legacy_snapshot": bool(package.get("legacy_snapshot")),
            "settings": settings,
            "features": features,
            "providers": providers,
            "data": data,
            "unknown_config_fields": bounded_unknown,
            "preserved_unknown": preserved_unknown,
            "ignored": sorted(set(ignored))[:80],
        }

    async def _migration_import_summary(self, normalized: dict[str, Any]) -> dict[str, Any]:
        async with self.plugin._data_lock:
            current_data = deepcopy(self.plugin.data)
        config_diff = self._migration_config_diff(normalized)
        config_count = len(normalized.get("settings", {})) + len(normalized.get("features", {})) + len(normalized.get("providers", {}))
        compatibility = self._migration_compatibility(normalized.get("version"))
        sections: list[dict[str, Any]] = []
        for key, value in (normalized.get("data") or {}).items():
            current_value = current_data.get(key)
            diff = self._migration_diff_counts(current_value, value)
            sections.append(
                {
                    "key": key,
                    "label": self._migration_section_label(key),
                    "count": self._migration_count_items(value),
                    "current_count": self._migration_count_items(current_value),
                    **diff,
                }
            )
        return {
            "version": normalized.get("version") or "",
            "current_version": self._plugin_version(),
            "compatibility": compatibility,
            "exported_at": normalized.get("exported_at") or 0,
            "included_sections": normalized.get("included_sections", []),
            "checksum": normalized.get("checksum") or "",
            "checksum_ok": bool(normalized.get("checksum_ok")),
            "checksum_bypassed": bool(normalized.get("checksum_bypassed")),
            "legacy_snapshot": bool(normalized.get("legacy_snapshot")),
            "config_count": config_count,
            "config_diff": config_diff,
            "settings_count": len(normalized.get("settings", {})),
            "features_count": len(normalized.get("features", {})),
            "providers_count": len(normalized.get("providers", {})),
            "preserved_unknown": normalized.get("preserved_unknown", []),
            "preserved_unknown_count": len(
                normalized.get("preserved_unknown", [])
            ),
            "sections": sections,
            "ignored": normalized.get("ignored", []),
            "excluded": [
                "不会导入 Token 统计、缓存、最近消息、审计日志和临时队列。",
                "导入前会自动保存一份当前可迁移配置备份。",
            ],
        }

    def _write_migration_backup(self, package: dict[str, Any]) -> str:
        data_dir = Path(getattr(self.plugin, "data_dir", "") or Path(getattr(self.plugin, "data_file", ".")).parent)
        backup_dir = data_dir / "config_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        path = backup_dir / f"private_companion_before_import_{stamp}_{secrets.token_hex(3)}.json"
        if not package.get("checksum"):
            package["checksum_algorithm"] = "sha256"
            package["checksum"] = self._migration_checksum(package)
        path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)

    def _migration_backup_dir(self) -> Path:
        data_dir = Path(getattr(self.plugin, "data_dir", "") or Path(getattr(self.plugin, "data_file", ".")).parent)
        return data_dir / "config_backups"

    def _migration_backup_service(self) -> MigrationBackupService:
        return MigrationBackupService(
            self._migration_backup_dir(),
            checksum_matches=self._migration_checksum_matches,
            error_text=self._single_line,
        )

    def _resolve_migration_backup_path(self, backup_id: str) -> Path:
        return self._migration_backup_service().resolve(backup_id)

    def _list_migration_backup_items(self, *, limit: int = 8) -> list[dict[str, Any]]:
        return self._migration_backup_service().list_items(limit=limit)

    def _migration_checksum(self, package: dict[str, Any]) -> str:
        payload = deepcopy(package)
        payload.pop("checksum", None)
        payload.pop("checksum_algorithm", None)
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _migration_checksum_digest(payload: dict[str, Any], *, sort_keys: bool, compact: bool) -> str:
        separators = (",", ":") if compact else None
        text = json.dumps(payload, ensure_ascii=False, sort_keys=sort_keys, separators=separators)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _migration_checksum_candidates(self, package: dict[str, Any]) -> set[str]:
        candidates: set[str] = set()
        for remove_algorithm in (True, False):
            payload = deepcopy(package)
            payload.pop("checksum", None)
            if remove_algorithm:
                payload.pop("checksum_algorithm", None)
            for sort_keys in (True, False):
                for compact in (True, False):
                    try:
                        candidates.add(
                            self._migration_checksum_digest(
                                payload,
                                sort_keys=sort_keys,
                                compact=compact,
                            )
                        )
                    except Exception:
                        continue
        return candidates

    def _migration_checksum_matches(self, package: dict[str, Any], expected: str) -> bool:
        checksum = str(expected or "").strip().lower()
        if not checksum:
            return False
        return checksum in self._migration_checksum_candidates(package)

    def _parse_migration_version(self, value: Any) -> tuple[int, int, int] | None:
        match = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", str(value or ""))
        if not match:
            return None
        return tuple(int(part or 0) for part in match.groups())

    def _migration_compatibility(self, backup_version: Any) -> dict[str, Any]:
        current_text = self._plugin_version()
        backup_text = str(backup_version or "")
        current = self._parse_migration_version(current_text)
        backup = self._parse_migration_version(backup_text)
        level = "ok"
        message = "版本接近，可以按预览结果导入。"
        if not backup or not current:
            level = "unknown"
            message = "无法判断版本跨度，建议合并导入，不建议覆盖。"
        elif backup[0] != current[0] or abs((current[1] if len(current) > 1 else 0) - (backup[1] if len(backup) > 1 else 0)) >= 2:
            level = "warn"
            message = "版本跨度较大，建议合并导入，不建议覆盖。"
        elif backup > current:
            level = "warn"
            message = "备份来自更新版本，建议合并导入，不建议覆盖。"
        return {
            "level": level,
            "backup_version": backup_text or "未知",
            "current_version": current_text,
            "message": message,
        }

    async def _migration_post_import_checks(self, *, config_saved: bool) -> list[dict[str, str]]:
        async with self.plugin._data_lock:
            data = deepcopy(self.plugin.data)
        checks: list[dict[str, str]] = []

        def add(level: str, title: str, detail: str) -> None:
            checks.append({"level": level, "title": title, "detail": detail})

        add("ok" if config_saved else "warn", "配置保存", "配置已写入并保存。" if config_saved else "运行态已写入，但配置持久化可能失败。")
        mode = str(getattr(self.plugin, "group_access_mode", "whitelist") or "whitelist")
        whitelist = list(getattr(self.plugin, "group_whitelist_ids", []) or [])
        blacklist = list(getattr(self.plugin, "group_blacklist_ids", []) or [])
        if mode == "whitelist" and not whitelist:
            add("warn", "群聊名单", "当前是白名单模式，但白名单为空；群聊能力可能不会生效。")
        else:
            add("ok", "群聊名单", f"当前为{'白名单' if mode == 'whitelist' else '黑名单'}模式，白名单 {len(whitelist)} 个，黑名单 {len(blacklist)} 个。")

        available_ids = {str(item.get("id") or "") for item in self._available_provider_items()}
        try:
            available_ids.update(
                str(item.get("id") or "")
                for item in await self._available_embedding_provider_items()
                if str(item.get("id") or "")
            )
        except Exception:
            pass
        configured = {key: value for key, value in self._provider_settings().items() if str(value or "").strip()}
        missing = [value for value in configured.values() if available_ids and value not in available_ids]
        if configured and not available_ids:
            add("warn", "模型配置", f"已配置 {len(configured)} 个模型指向，但当前无法读取 AstrBot Provider 列表，暂不能校验是否存在。")
        elif missing:
            add("warn", "模型配置", f"有 {len(missing)} 个 Provider ID 当前未在 AstrBot 中找到。")
        else:
            add("ok", "模型配置", f"已配置 {len(configured)} 个模型指向，当前未发现缺失 Provider。")

        members = data.get("worldbook_member_profiles") if isinstance(data.get("worldbook_member_profiles"), dict) else {}
        groups = data.get("worldbook_group_profiles") if isinstance(data.get("worldbook_group_profiles"), dict) else {}
        if not members and not groups:
            add("warn", "关系网", "当前没有关系网成员或群资料；如果刚导入关系网备份，可能需要检查导入内容。")
        else:
            add("ok", "关系网", f"成员资料 {len(members)} 个，群资料 {len(groups)} 个。")

        food = data.get("food_menu") if isinstance(data.get("food_menu"), dict) else {}
        food_items = food.get("items") if isinstance(food.get("items"), dict) else food
        if isinstance(food_items, dict):
            add("ok" if food_items else "warn", "吃什么候选", f"候选菜单可读取，当前 {len(food_items)} 项。")
        else:
            add("warn", "吃什么候选", "候选菜单结构不是预期格式，请到功能页检查。")
        return checks

    @staticmethod
    def _migration_count_items(value: Any) -> int:
        if isinstance(value, dict):
            return len(value)
        if isinstance(value, list):
            return len(value)
        return 1 if value not in (None, "", [], {}) else 0

    def _migration_current_setting_value(self, key: str) -> Any:
        if key == "group_access_mode":
            return str(getattr(self.plugin, "group_access_mode", "whitelist") or "whitelist")
        if key == "group_whitelist_ids":
            return list(getattr(self.plugin, "group_whitelist_ids", []) or [])
        if key == "group_blacklist_ids":
            return list(getattr(self.plugin, "group_blacklist_ids", []) or [])
        if hasattr(self.plugin, key):
            return deepcopy(getattr(self.plugin, key))
        return self._config_get(key)

    @staticmethod
    def _migration_value_empty(value: Any) -> bool:
        return value is None or value == "" or value == [] or value == {}

    def _should_apply_migration_value(self, current_value: Any, incoming_value: Any, conflict: str) -> bool:
        if current_value == incoming_value:
            return False
        if conflict == "keep_current":
            return False
        if conflict == "fill_empty":
            return self._migration_value_empty(current_value)
        return True

    def _migration_diff_counts(self, current: Any, incoming: Any) -> dict[str, int]:
        if isinstance(incoming, dict):
            current_dict = current if isinstance(current, dict) else {}
            added = 0
            overwritten = 0
            unchanged = 0
            for key, value in incoming.items():
                if key not in current_dict:
                    added += 1
                elif current_dict.get(key) == value:
                    unchanged += 1
                else:
                    overwritten += 1
            return {"added": added, "overwritten": overwritten, "unchanged": unchanged}
        if isinstance(incoming, list):
            if not isinstance(current, list) or not current:
                return {"added": len(incoming), "overwritten": 0, "unchanged": 0}
            if current == incoming:
                return {"added": 0, "overwritten": 0, "unchanged": len(incoming)}
            return {"added": 0, "overwritten": len(incoming), "unchanged": 0}
        return {
            "added": 1 if self._migration_value_empty(current) and not self._migration_value_empty(incoming) else 0,
            "overwritten": 1 if not self._migration_value_empty(current) and current != incoming else 0,
            "unchanged": 1 if current == incoming else 0,
        }

    def _migration_config_diff(self, normalized: dict[str, Any]) -> dict[str, int]:
        counts = {"added": 0, "overwritten": 0, "unchanged": 0}
        for key, value in (normalized.get("settings") or {}).items():
            diff = self._migration_diff_counts(self._migration_current_setting_value(key), value)
            for item in counts:
                counts[item] += diff[item]
        provider_snapshot = self._provider_settings()
        for key, value in (normalized.get("providers") or {}).items():
            diff = self._migration_diff_counts(provider_snapshot.get(key, ""), value)
            for item in counts:
                counts[item] += diff[item]
        for key, value in (normalized.get("features") or {}).items():
            diff = self._migration_diff_counts(self._normalize_bool_value(getattr(self.plugin, key, self._config_get(key))), self._normalize_bool_value(value))
            for item in counts:
                counts[item] += diff[item]
        return counts

    def _refresh_migration_runtime_caches(self, data_payload: dict[str, Any]) -> None:
        if "external_proactive_abilities" in data_payload and hasattr(self.plugin, "_external_proactive_abilities"):
            store = self.plugin.data.get("external_proactive_abilities")
            if isinstance(store, dict):
                self.plugin._external_proactive_abilities = deepcopy(store)
