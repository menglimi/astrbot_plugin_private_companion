# -*- coding: utf-8 -*-
"""PrivateCompanionPluginPersonaProfilePart01Mixin。

由 tools/split_mixin_domain.py 从 main_persona_profile.py 机械抽取（20 个方法 + 0 个模块级名字 + 0 个类级赋值 / 474 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPluginPersonaProfileMixin）。
"""
from __future__ import annotations

from .main_persona_profile_shared import logger
from .main_persona_profile_shared import Any
from .main_persona_profile_shared import PERSONA_SETTINGS_KEY
from .main_persona_profile_shared import PERSONA_SETTINGS_REVISION_KEY
from .main_persona_profile_shared import PERSONA_SETTINGS_SCHEMA_VERSION
from .main_persona_profile_shared import PERSONA_SETTINGS_VERSION_KEY
from .main_persona_profile_shared import Path
from .main_persona_profile_shared import PersonaConfigError
from .main_persona_profile_shared import PersonaSettingsTypeError
from .main_persona_profile_shared import _ACTIVE_PERSONA_ID
from .main_persona_profile_shared import _set_into_config
from .main_persona_profile_shared import _single_line
from .main_persona_profile_shared import asyncio
from .main_persona_profile_shared import datetime
from .main_persona_profile_shared import deepcopy
from .main_persona_profile_shared import json
from .main_persona_profile_shared import load_persona_sqlite_store
from .main_persona_profile_shared import migrate_persona_profile
from .main_persona_profile_shared import os
from .main_persona_profile_shared import re
from .main_persona_profile_shared import resolve_effective_settings
from .main_persona_profile_shared import resolve_persona_setting
from .main_persona_profile_shared import runtime_persona_setting
from .main_persona_profile_shared import shutil
from .main_persona_profile_shared import story_legacy_sync_operation
from .main_persona_profile_shared import time
from .main_persona_profile_shared import uuid



class PrivateCompanionPluginPersonaProfilePart01Mixin:
    """PrivateCompanionPluginPersonaProfilePart01Mixin（从 PrivateCompanionPluginPersonaProfileMixin 拆出）。"""


    def _primary_persona_config(self) -> Any:
        return getattr(self, "config", {})

    def _primary_persona_id(self) -> str:
        """Return the plugin's only authoritative primary persona ID."""
        return self._sanitize_persona_id(
            object.__getattribute__(self, "plugin_specific_persona_id")
            if hasattr(self, "plugin_specific_persona_id")
            else ""
        )

    def get_persona_setting(self, key: str, persona_id: Any = "", default: Any = None) -> Any:
        """Return one effective setting for the active or requested persona."""
        key = str(key or "").strip()
        if not key:
            return default
        manifest = self._persona_scope_manifest()
        # Runtime attributes use snake_case while AstrBot's provider schema
        # keeps legacy provider IDs in upper case. Treat those spellings as
        # aliases at the resolver boundary so sparse persona profiles written
        # by either version continue to work.
        manifest_key = key
        if manifest_key not in manifest:
            folded = key.casefold()
            manifest_key = next(
                (candidate for candidate in manifest if str(candidate).casefold() == folded),
                key,
            )
        runtime_key = (
            manifest_key.lower()
            if manifest_key.isupper() and manifest_key.endswith("_PROVIDER_ID")
            else key
        )
        active = self._sanitize_persona_id(persona_id or _ACTIVE_PERSONA_ID.get())
        enabled = bool(object.__getattribute__(self, "enable_multi_persona_mode")) if hasattr(self, "enable_multi_persona_mode") else False
        if not enabled or not active:
            try:
                return object.__getattribute__(self, runtime_key)
            except AttributeError:
                return default
        if manifest_key == "plugin_specific_persona_id":
            return active
        primary = self._primary_persona_id()
        if active == primary:
            try:
                return object.__getattribute__(self, runtime_key)
            except AttributeError:
                pass
        settings = self._persona_settings_for_id(active)
        if manifest_key not in settings and key in settings and manifest_key != key:
            # Accept the lowercase runtime spelling in hand-edited/early
            # profile files while keeping the schema's canonical key in the
            # resolver path.
            settings[manifest_key] = deepcopy(settings[key])
        try:
            primary_value = object.__getattribute__(self, runtime_key)
            primary_source: Any = {manifest_key: deepcopy(primary_value)}
        except AttributeError:
            primary_source = self._primary_persona_config()
        value = resolve_persona_setting(
            manifest_key,
            settings,
            primary_source,
            manifest=manifest,
            default=default,
        )
        # The resolver intentionally returns a copy. This keeps mutable list/
        # object settings from being modified through a runtime read.
        return value

    def mobile_persona_identity(self) -> dict[str, Any]:
        """读取陪伴形象。主人格/单人格场景下 bot_name 即通用配置。"""
        name = _single_line(self.persona_setting("bot_name", getattr(self, "bot_name", "")), 12)
        return {"bot_name": name or "小星"}

    async def mobile_set_persona_identity(self, bot_name: Any = "") -> dict[str, Any]:
        """写入 Bot 称呼，复用与网页端一致的配置持久化路径。"""
        name = _single_line(bot_name, 12).strip()
        if not name:
            return {"ok": False, "code": "identity_name_empty", "message": "称呼不能为空"}
        before = _single_line(getattr(self, "bot_name", ""), 80)
        try:
            await self._flush_scheduled_data_save()
            _set_into_config(self.config, "bot_name", name)
            if not bool(await self._save_config_if_possible()):
                _set_into_config(self.config, "bot_name", before)
                return {"ok": False, "code": "identity_config_not_saved", "message": "配置未能持久化"}
            # 运行时读取走实例属性，同步更新让新称呼立刻生效
            self.bot_name = name
        except Exception as exc:
            _set_into_config(self.config, "bot_name", before)
            return {"ok": False, "code": "identity_update_failed", "message": str(exc)[:200]}
        return {"ok": True, "bot_name": name}

    def effective_persona_settings(self, persona_id: Any = "", *, include_common: bool = False) -> dict[str, Any]:
        active = self._sanitize_persona_id(persona_id or _ACTIVE_PERSONA_ID.get())
        if not active:
            active = self._primary_persona_id()
        settings = self._persona_settings_for_id(active)
        primary = self._primary_persona_id()
        if active == primary:
            result: dict[str, Any] = {}
            for key, entry in self._persona_scope_manifest().items():
                if not include_common and entry.get("scope") == "common":
                    continue
                try:
                    result[key] = deepcopy(object.__getattribute__(self, key))
                except AttributeError:
                    result[key] = resolve_persona_setting(
                        key,
                        {},
                        self._primary_persona_config(),
                        manifest=self._persona_scope_manifest(),
                    )
            return result
        return resolve_effective_settings(
            settings,
            self._primary_persona_config(),
            manifest=self._persona_scope_manifest(),
            include_common=include_common,
            include_identity=True,
        )

    @story_legacy_sync_operation("persona.profiles.startup-migrate")
    def _migrate_persona_profiles_sync(self) -> dict[str, Any]:
        """Migrate legacy persona JSON into SQLite and upgrade sparse settings."""
        result = {"ok": True, "migrated": [], "degraded": [], "skipped": []}
        profiles_dir = Path(str(getattr(self, "_persona_profiles_dir", "") or ""))
        if not profiles_dir.exists():
            return result
        primary = self._primary_persona_id()
        errors = getattr(self, "_persona_profile_errors", None)
        if not isinstance(errors, dict):
            errors = {}
            self._persona_profile_errors = errors
        candidates: dict[str, list[Path]] = {}
        for pattern in ("*.json", "*.db"):
            for path in sorted(profiles_dir.glob(pattern)):
                pid = self._persona_id_from_profile_path(path)
                if pid:
                    candidates.setdefault(pid, []).append(path)
        profiles = getattr(self, "_persona_data_profiles", None)
        if not isinstance(profiles, dict):
            profiles = {}
            self._persona_data_profiles = profiles
        for pid, paths in sorted(candidates.items()):
            if not pid or pid == primary:
                result["skipped"].append(pid or paths[0].name)
                continue
            legacy_path = self._persona_profile_path(pid)
            legacy_present = legacy_path.is_file()
            try:
                handle = self._load_secondary_persona_store_sync(pid)
                raw = handle.data
                settings = raw.get(PERSONA_SETTINGS_KEY)
                if settings is not None and not isinstance(settings, dict):
                    raise PersonaSettingsTypeError("persona_settings must be an object")
                # Legacy JSON is transformed before the SQLite write. For an
                # existing DB, keep the upgrade path below so a failed schema
                # migration leaves the already-authoritative DB untouched.
                migrated = raw if legacy_present else migrate_persona_profile(
                    raw,
                    manifest=self._persona_scope_manifest(),
                    target_version=PERSONA_SETTINGS_SCHEMA_VERSION,
                    persona_id=pid,
                    legacy_bot_name=(
                        self._persona_display_name_for_id(pid)
                        if not isinstance(settings, dict)
                        or not str(settings.get("bot_name") or "").strip()
                        else ""
                    ),
                )
                changed = migrated != raw
                if changed:
                    handle.manager.save_snapshot(deepcopy(migrated))
                profiles[pid] = migrated
                errors.pop(pid, None)
                if changed or legacy_present:
                    result["migrated"].append(pid)
            except Exception as exc:
                backup = (
                    self._backup_corrupt_persona_profile_sync(legacy_path, reason=str(exc))
                    if legacy_path.is_file()
                    else None
                )
                errors[pid] = str(exc)
                result["degraded"].append(pid)
                result["ok"] = False
                if backup is not None:
                    result.setdefault("backups", {})[pid] = str(backup)
                logger.warning(
                    "人格配置迁移降级: persona=%s error=%s",
                    pid,
                    _single_line(exc, 180),
                )
        return result

    def _effective_plugin_persona_id(self) -> str:
        active = _ACTIVE_PERSONA_ID.get()
        if bool(getattr(self, "enable_multi_persona_mode", False)) and active:
            return active
        return str(runtime_persona_setting(self, 'plugin_specific_persona_id', "") or "").strip()

    def _active_persona_scope(self) -> str:
        return _ACTIVE_PERSONA_ID.get() if bool(getattr(self, "enable_multi_persona_mode", False)) else ""

    def _configured_multi_persona_ids(self, *, strict: bool = False) -> list[str]:
        raw = self._cfg_raw(getattr(self, "config", {}), "multi_persona_ids", [])
        if isinstance(raw, str):
            raw = re.split(r"[\s,，、]+", raw)
        elif strict and not isinstance(raw, list):
            raise PersonaConfigError(
                "multi_persona_ids has an unverifiable container"
            )
        if not isinstance(raw, (list, tuple, set)):
            raw = []
        result: list[str] = []
        for value in raw:
            if strict and not isinstance(value, str):
                raise PersonaConfigError(
                    "multi_persona_ids contains a non-text persona id"
                )
            pid = self._sanitize_persona_id(value)
            if strict and str(value or "").strip() not in {"", pid}:
                raise PersonaConfigError(
                    "multi_persona_ids contains a non-canonical persona id"
                )
            if pid and pid not in result:
                result.append(pid)
        primary = self._primary_persona_id()
        if strict and not primary:
            raise PersonaConfigError(
                "multi-persona primary id cannot be verified"
            )
        if primary:
            result = [primary, *(pid for pid in result if pid != primary)]
        return result

    @story_legacy_sync_operation("persona.store.load")
    def _load_secondary_persona_store_sync(
        self,
        persona_id: Any,
        *,
        prepare_payload: Any = None,
    ):
        pid = self._sanitize_persona_id(persona_id)
        if not pid or pid == self._primary_persona_id():
            raise PersonaConfigError("secondary persona SQLite requires a non-primary persona")
        legacy_path, database_path = self._persona_profile_store_paths(pid)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        if not callable(prepare_payload):
            prepare_payload = lambda payload: self._prepare_legacy_persona_payload(
                pid, payload
            )
        return load_persona_sqlite_store(
            persona_id=pid,
            legacy_json_path=legacy_path,
            sqlite_path=database_path,
            ensure_defaults=self._ensure_store_defaults,
            new_store=self._new_store,
            registry=self._persona_sqlite_registry(),
            prepare_payload=prepare_payload,
        )

    def _secondary_persona_store_exists(self, persona_id: Any) -> bool:
        pid = self._sanitize_persona_id(persona_id)
        if not pid or pid == self._primary_persona_id():
            return False
        legacy_path, database_path = self._persona_profile_store_paths(pid)
        return legacy_path.is_file() or database_path.is_file()

    def _backup_corrupt_persona_profile_sync(self, path: Path, *, reason: str) -> Path | None:
        if not path.exists():
            return None
        backup = path.with_name(
            f"{path.name}.corrupt-{int(time.time())}-{uuid.uuid4().hex[:8]}.bak"
        )
        try:
            shutil.copy2(path, backup)
            logger.error(
                "已隔离损坏人格 profile: source=%s backup=%s reason=%s",
                path,
                backup,
                _single_line(reason, 160),
            )
            return backup
        except Exception as exc:
            logger.error(
                "人格 profile 备份失败: source=%s error=%s",
                path,
                _single_line(exc, 160),
            )
            return None

    def _ensure_persona_profile(self, persona_id: str) -> dict[str, Any]:
        pid = self._sanitize_persona_id(persona_id) or self._primary_persona_id()
        if not pid:
            return self._data_default
        if pid == self._primary_persona_id():
            return self._data_default
        profile_errors = getattr(self, "_persona_profile_errors", None)
        if not isinstance(profile_errors, dict):
            profile_errors = {}
            self._persona_profile_errors = profile_errors
        profiles = getattr(self, "_persona_data_profiles", None)
        if profiles is None:
            profiles = {}
            self._persona_data_profiles = profiles
        existing = profiles.get(pid)
        if isinstance(existing, dict):
            return existing
        legacy_path, database_path = self._persona_profile_store_paths(pid)
        store_existed = legacy_path.is_file() or database_path.is_file()
        loaded: dict[str, Any] | None = None
        try:
            handle = self._load_secondary_persona_store_sync(pid)
            loaded = handle.data
            profile_errors.pop(pid, None)
        except Exception as exc:
            if legacy_path.is_file():
                self._backup_corrupt_persona_profile_sync(legacy_path, reason=str(exc))
            profile_errors[pid] = str(exc)
            logger.warning("人格资料读取失败 persona=%s error=%s", pid, _single_line(exc, 160))
        if loaded is not None:
            profile = loaded
        else:
            factory = getattr(self, "_new_store", None)
            profile = factory() if callable(factory) else {}
        ensure_defaults = getattr(self, "_ensure_store_defaults", None)
        if callable(ensure_defaults):
            profile = ensure_defaults(profile)
        if PERSONA_SETTINGS_KEY not in profile:
            profile["persona_settings"] = {}
        elif not isinstance(profile.get(PERSONA_SETTINGS_KEY), dict):
            reason = "persona_settings must be an object"
            self._backup_corrupt_persona_profile_sync(legacy_path, reason=reason)
            profile_errors[pid] = reason
            profile[PERSONA_SETTINGS_KEY] = {}
        if store_existed and loaded is not None and not str(profile[PERSONA_SETTINGS_KEY].get("bot_name") or "").strip():
            # Pre-settings secondary profiles need an identity to be editable;
            # ordinary missing keys remain sparse and continue following the
            # primary configuration.
            profile[PERSONA_SETTINGS_KEY]["bot_name"] = self._persona_display_name_for_id(pid)
            profile[PERSONA_SETTINGS_VERSION_KEY] = PERSONA_SETTINGS_SCHEMA_VERSION
            profile.setdefault(PERSONA_SETTINGS_REVISION_KEY, 0)
            try:
                self._save_persona_profile_sync(pid, profile)
            except Exception as exc:
                logger.warning(
                    "旧人格身份配置初始化落盘失败: persona=%s error=%s",
                    pid,
                    _single_line(exc, 160),
                )
        profiles[pid] = profile
        return profile

    def _save_persona_profile_sync(self, persona_id: str, data: dict[str, Any] | None = None) -> None:
        pid = self._sanitize_persona_id(persona_id)
        if not pid:
            return
        if pid == self._primary_persona_id():
            payload = data if isinstance(data, dict) else self._data_default
            self._write_data_snapshot_sync(deepcopy(payload))
            return
        payload = data if isinstance(data, dict) else self._ensure_persona_profile(pid)
        handle = self._load_secondary_persona_store_sync(pid)
        handle.manager.save_snapshot(deepcopy(payload))

    async def _save_persona_profile_async(
        self,
        persona_id: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        # 全量快照写盘可能耗时（JSON 序列化 + SQLite 事务），从事件循环移到线程池。
        await asyncio.to_thread(self._save_persona_profile_sync, persona_id, data)

    def _write_persona_reset_backup_sync(
        self,
        persona_id: str,
        snapshot: dict[str, Any],
    ) -> Path:
        pid = self._sanitize_persona_id(persona_id)
        profile_stem = (
            Path(self._persona_profile_filename(pid)).stem
            if pid
            else "single-profile"
        )
        data_root = Path(
            str(getattr(self, "data_dir", "") or "").strip()
            or Path(self._persona_profiles_dir).parent
        )
        backup_dir = data_root / "persona_backups" / profile_stem
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = backup_dir / f"{timestamp}-{uuid.uuid4().hex[:8]}.json"
        temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        payload = {
            "backup_version": 1,
            "persona_id": pid,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "data": snapshot,
        }
        try:
            temp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temp, path)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except Exception:
                pass
        return path

    def _astrbot_persona_exists(self, persona_id: Any) -> bool:
        pid = self._sanitize_persona_id(persona_id)
        if not pid:
            return False
        manager = getattr(getattr(self, "context", None), "persona_manager", None)
        getter = getattr(manager, "get_persona_v3_by_id", None)
        if callable(getter):
            try:
                return getter(pid) is not None
            except Exception:
                pass
        for item in list(getattr(manager, "personas_v3", None) or []) + list(
            getattr(manager, "personas", None) or []
        ):
            if isinstance(item, dict):
                item_id = item.get("name") or item.get("persona_id") or item.get("id")
            else:
                item_id = (
                    getattr(item, "persona_id", None)
                    or getattr(item, "name", None)
                    or getattr(item, "id", None)
                )
            if self._sanitize_persona_id(item_id) == pid:
                return True
        return False

    def _astrbot_persona_ids_snapshot(self) -> tuple[set[str] | None, str]:
        """Return AstrBot's complete in-memory persona set when verifiable."""
        manager = getattr(getattr(self, "context", None), "persona_manager", None)
        if manager is None:
            return None, "persona_manager_unavailable"
        source = getattr(manager, "personas", None)
        source_name = "personas"
        # PersonaManager creates both attributes eagerly, but only populates
        # them during initialize().  Treat the pre-initialize empty state as
        # unknown instead of interpreting it as an authoritative empty list.
        if (
            isinstance(source, (list, tuple))
            and not source
            and getattr(manager, "selected_default_persona", None) is None
            and getattr(manager, "selected_default_persona_v3", None) is None
        ):
            return None, "persona_manager_not_initialized"
        if source is None and hasattr(manager, "personas_v3"):
            source = getattr(manager, "personas_v3", None)
            source_name = "personas_v3"
        if not isinstance(source, (list, tuple)):
            return None, f"{source_name}_unavailable"
        ids: set[str] = set()
        for item in source:
            if isinstance(item, dict):
                item_id = item.get("persona_id") or item.get("name") or item.get("id")
            else:
                item_id = (
                    getattr(item, "persona_id", None)
                    or getattr(item, "name", None)
                    or getattr(item, "id", None)
                )
            pid = self._sanitize_persona_id(item_id)
            if not pid:
                return None, f"{source_name}_item_invalid"
            ids.add(pid)
        return ids, "ok"

    def _backup_deleted_persona_store_sync(self, persona_id: str, path: Path) -> Path | None:
        """Make a recoverable copy before retiring a deleted persona store."""
        if not path.is_file():
            return None
        root = Path(str(getattr(self, "data_dir", "") or "").strip() or Path(self._persona_profiles_dir).parent)
        backup_dir = root / "persona_backups" / self._persona_profile_stem(persona_id)
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"deleted-{int(time.time())}-{uuid.uuid4().hex[:8]}-{path.name}"
        try:
            shutil.copy2(path, backup)
            return backup
        except Exception as exc:
            logger.warning("已删除人格档案备份失败，保留原文件: persona=%s path=%s error=%s", persona_id, path, _single_line(exc, 160))
            return None
