# -*- coding: utf-8 -*-
"""PrivateCompanionPluginPersonaRoutingPart01Mixin。

由 tools/split_mixin_domain.py 从 main_persona_routing.py 机械抽取（20 个方法 + 0 个模块级名字 + 0 个类级赋值 / 474 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPluginPersonaRoutingMixin）。
"""
from __future__ import annotations

from .main_persona_routing_shared import logger
from .main_persona_routing_shared import Any
from .main_persona_routing_shared import PERSONA_SETTINGS_KEY
from .main_persona_routing_shared import PERSONA_SETTINGS_REVISION_KEY
from .main_persona_routing_shared import PERSONA_SETTINGS_SCHEMA_VERSION
from .main_persona_routing_shared import PERSONA_SETTINGS_VERSION_KEY
from .main_persona_routing_shared import Path
from .main_persona_routing_shared import PersonaConfigError
from .main_persona_routing_shared import PersonaSettingsTypeError
from .main_persona_routing_shared import PersonaSqliteStoreRegistry
from .main_persona_routing_shared import _ACTIVE_PERSONA_ID
from .main_persona_routing_shared import _PERSONA_PROFILE_FORBIDDEN_FILENAME_CHARS
from .main_persona_routing_shared import _PERSONA_SETTING_MANIFEST
from .main_persona_routing_shared import _WINDOWS_RESERVED_FILENAME_STEMS
from .main_persona_routing_shared import _now_ts
from .main_persona_routing_shared import _single_line
from .main_persona_routing_shared import deepcopy
from .main_persona_routing_shared import migrate_persona_profile
from .main_persona_routing_shared import read_persona_store_snapshot_read_only
from .main_persona_routing_shared import runtime_persona_setting
from .main_persona_routing_shared import scoped_persona_ref
from .main_persona_routing_shared import stat
from .main_persona_routing_shared import story_legacy_operation
from .main_persona_routing_shared import unicodedata
from .main_persona_routing_shared import unquote
from .main_persona_routing_shared import uuid



class PrivateCompanionPluginPersonaRoutingPart01Mixin:
    """PrivateCompanionPluginPersonaRoutingPart01Mixin（从 PrivateCompanionPluginPersonaRoutingMixin 拆出）。"""


    def _persona_scope_manifest(self) -> dict[str, dict[str, Any]]:
        return _PERSONA_SETTING_MANIFEST

    def _persona_settings_for_id(self, persona_id: Any = "") -> dict[str, Any]:
        pid = self._sanitize_persona_id(persona_id or _ACTIVE_PERSONA_ID.get())
        if not pid:
            return {}
        primary = self._primary_persona_id()
        if pid == primary:
            return {}
        profile = self._ensure_persona_profile(pid)
        settings = profile.get(PERSONA_SETTINGS_KEY)
        return dict(settings) if isinstance(settings, dict) else {}

    def persona_setting(self, key: str, default: Any = None, persona_id: Any = "") -> Any:
        """Short explicit accessor for persona-aware runtime code."""
        return self.get_persona_setting(key, persona_id=persona_id, default=default)

    def _persona_profile_stem(self, persona_id: Any) -> str:
        """Return a reversible, cross-platform-safe filename stem."""
        pid = self._sanitize_persona_id(persona_id)
        encoded_parts: list[str] = []
        for character in pid:
            if (
                character in _PERSONA_PROFILE_FORBIDDEN_FILENAME_CHARS
                or unicodedata.category(character).startswith("C")
            ):
                encoded_parts.extend(
                    f"%{byte:02X}" for byte in character.encode("utf-8")
                )
            else:
                encoded_parts.append(character)
        stem = "".join(encoded_parts)
        if stem.partition(".")[0].upper() in _WINDOWS_RESERVED_FILENAME_STEMS and stem:
            stem = f"%{ord(stem[0]):02X}{stem[1:]}"
        return stem

    def _persona_profile_filename(self, persona_id: Any) -> str:
        """Return the legacy JSON filename for one logical persona ID."""
        return f"{self._persona_profile_stem(persona_id)}.json"

    def _persona_profile_db_filename(self, persona_id: Any) -> str:
        """Return the authoritative secondary-persona SQLite filename."""
        return f"{self._persona_profile_stem(persona_id)}.db"

    def _persona_id_from_profile_path(self, path: Path) -> str:
        filename = path.name
        suffix = path.suffix.lower()
        if suffix not in {".json", ".db"}:
            return ""
        try:
            decoded = unquote(filename[: -len(suffix)], encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, ValueError):
            return ""
        return self._sanitize_persona_id(decoded)

    def _persona_profile_path(self, persona_id: str) -> Path:
        return Path(self._persona_profiles_dir) / self._persona_profile_filename(persona_id)

    def _persona_profile_db_path(self, persona_id: str) -> Path:
        return Path(self._persona_profiles_dir) / self._persona_profile_db_filename(persona_id)

    def _persona_profile_store_paths(self, persona_id: Any) -> tuple[Path, Path]:
        pid = self._sanitize_persona_id(persona_id)
        return self._persona_profile_path(pid), self._persona_profile_db_path(pid)

    def _persona_sqlite_registry(self) -> PersonaSqliteStoreRegistry:
        registry = getattr(self, "_persona_sqlite_store_registry", None)
        if not isinstance(registry, PersonaSqliteStoreRegistry):
            registry = PersonaSqliteStoreRegistry()
            self._persona_sqlite_store_registry = registry
        return registry

    def _prepare_legacy_persona_payload(
        self,
        persona_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate and migrate a legacy JSON profile before SQLite commit."""
        settings = payload.get(PERSONA_SETTINGS_KEY)
        if settings is not None and not isinstance(settings, dict):
            raise PersonaSettingsTypeError("persona_settings must be an object")
        return migrate_persona_profile(
            payload,
            manifest=self._persona_scope_manifest(),
            target_version=PERSONA_SETTINGS_SCHEMA_VERSION,
            persona_id=persona_id,
            legacy_bot_name=(
                self._persona_display_name_for_id(persona_id)
                if not isinstance(settings, dict)
                or not str(settings.get("bot_name") or "").strip()
                else ""
            ),
        )

    def _persona_display_name_for_id(self, persona_id: Any) -> str:
        """Return an AstrBot persona label without requiring async I/O."""
        pid = self._sanitize_persona_id(persona_id)
        manager = getattr(getattr(self, "context", None), "persona_manager", None)
        for item in list(getattr(manager, "personas", None) or []):
            if isinstance(item, dict):
                item_id = item.get("persona_id") or item.get("id") or item.get("name")
                label = item.get("name") or item.get("label") or item_id
            else:
                item_id = getattr(item, "persona_id", None) or getattr(item, "id", None) or getattr(item, "name", None)
                label = getattr(item, "name", None) or getattr(item, "label", None) or item_id
            if self._sanitize_persona_id(item_id) == pid:
                return _single_line(label, 80) or pid
        return pid

    @story_legacy_operation("persona.store.reset")
    async def _reset_current_persona_store(
        self,
        persona_id: Any = "",
        *,
        rebuild_today: bool = True,
        operation_id: str = "",
        _force_default_store: bool = False,
    ) -> dict[str, Any]:
        multi_enabled = bool(getattr(self, "enable_multi_persona_mode", False)) and not _force_default_store
        requested = self._sanitize_persona_id(persona_id)
        active = self._sanitize_persona_id(self._active_persona_scope())
        pid = ""
        if multi_enabled:
            configured = self._configured_multi_persona_ids()
            pid = (
                requested
                or active
                or self._sanitize_persona_id(getattr(self, "_page_current_persona_id", ""))
                or self._primary_persona_id()
                or (configured[0] if configured else "")
            )
            if not pid or pid not in set(configured):
                return {"ok": False, "message": "当前人格不在已启用的多人格列表中"}

        token = None
        if multi_enabled and active != pid:
            token = self._activate_persona_id(pid)
            if token is None:
                return {"ok": False, "message": "无法激活要重置的人格"}

        backup_path: Path | None = None
        generation = 1
        try:
            await self._flush_scheduled_data_save()
            scoped_reset: dict[str, Any] = {
                "ok": True, "state": "not_required", "code": "scoped_persona_erase_not_required",
            }
            synchronizer = getattr(self, "req041_scoped_projection_sync", None)
            migration_status = getattr(self, "req041_migration_status", None)
            if synchronizer is None and isinstance(migration_status, dict) and (
                migration_status.get("required") or migration_status.get("scoped_required")
            ):
                return {"ok": False, "message": "人格分域清理暂不可用", "code": "scoped_persona_erase_unavailable"}
            if synchronizer is not None:
                persona_ref = scoped_persona_ref(pid)
                async with self._data_lock:
                    group_sagas = self.data.get("_req041_group_reset_sagas")
                    if isinstance(group_sagas, dict) and group_sagas:
                        return {
                            "ok": False, "message": "存在未完成的群删除事务，请等待恢复完成后再重置人格",
                            "code": "group_reset_in_progress",
                        }
                    marker = self.data.get("_req041_persona_reset_saga")
                    if marker is not None and not isinstance(marker, dict):
                        return {"ok": False, "message": "人格重置恢复记录损坏", "code": "persona_reset_saga_invalid"}
                    clean_operation = _single_line(operation_id, 120)
                    if isinstance(marker, dict):
                        marker_operation = _single_line(marker.get("operation_id"), 120)
                        if (
                            marker.get("state") != "confirmed"
                            or _single_line(marker.get("persona_id"), 80) != persona_ref
                            or (clean_operation and clean_operation != marker_operation)
                            or not marker_operation
                        ):
                            return {"ok": False, "message": "人格重置恢复记录冲突", "code": "persona_reset_saga_conflict"}
                        clean_operation = marker_operation
                    else:
                        clean_operation = clean_operation or "req041-persona-reset-" + uuid.uuid4().hex
                        self.data["_req041_persona_reset_saga"] = {
                            "operation_id": clean_operation,
                            "persona_id": persona_ref,
                            "source_persona_id": pid,
                            "state": "confirmed",
                            "created_at": _now_ts(),
                        }
                        self._req041_persist_archive_saga_locked(
                            sections={"_req041_persona_reset_saga"},
                        )
                scoped_reset = self._req041_erase_scoped_persona_data(
                    pid, operation_id=clean_operation,
                )
                if not scoped_reset.get("ok"):
                    return {
                        "ok": False,
                        "message": "人格分域清理失败，已保留本地资料并将在启动时重试",
                        "code": str(scoped_reset.get("code") or "scoped_persona_erase_failed")[:120],
                        "operation_id": clean_operation,
                    }
            async with self._data_lock:
                previous = deepcopy(self.data)
                backup_snapshot = deepcopy(previous)
                backup_snapshot.pop("_req041_persona_reset_saga", None)
                lifecycle = previous.get("persona_lifecycle")
                if not isinstance(lifecycle, dict):
                    lifecycle = {}
                try:
                    previous_generation = max(
                        1,
                        int(lifecycle.get("generation", 1) or 1),
                    )
                except (TypeError, ValueError):
                    previous_generation = 1
                generation = previous_generation + 1
                backup_path = self._write_persona_reset_backup_sync(pid, backup_snapshot)

                replacement = self._new_store()
                ensure_defaults = getattr(self, "_ensure_store_defaults", None)
                if callable(ensure_defaults):
                    replacement = ensure_defaults(replacement)
                # Reset life data without resetting the persona's independent
                # configuration or its optimistic-concurrency metadata.
                for settings_key in (
                    "persona_settings",
                    "persona_settings_schema_version",
                    "persona_settings_revision",
                ):
                    if settings_key in previous:
                        replacement[settings_key] = deepcopy(previous[settings_key])
                replacement["persona_lifecycle"] = {
                    "generation": generation,
                    "reset_at": _now_ts(),
                    "previous_backup": str(backup_path),
                }
                self.data = replacement
                if bool(runtime_persona_setting(self, "default_enable_configured_targets", False)):
                    sync_targets = getattr(self, "_sync_configured_targets", None)
                    if callable(sync_targets):
                        sync_targets()
                try:
                    if multi_enabled:
                        self._write_persona_data_snapshot_sync(
                            pid,
                            deepcopy(self.data),
                        )
                        clear_dirty = getattr(self, "_clear_scheduled_data_save_dirty", None)
                        if callable(clear_dirty):
                            clear_dirty(persona_id=pid)
                        else:
                            dirty = getattr(self, "_persona_data_save_dirty", None)
                            if isinstance(dirty, set):
                                dirty.discard(pid)
                    else:
                        self._write_data_snapshot_sync(deepcopy(self.data))
                        clear_dirty = getattr(self, "_clear_scheduled_data_save_dirty", None)
                        if callable(clear_dirty):
                            clear_dirty()
                        else:
                            self._data_save_dirty = False
                except Exception:
                    self.data = previous
                    if multi_enabled:
                        self._write_persona_data_snapshot_sync(pid, previous)
                    else:
                        self._write_data_snapshot_sync(previous)
                    raise

            self._reset_persona_prompt_caches(pid)
            bookshelf_tokens = getattr(self, "_bookshelf_access_tokens", None)
            if isinstance(bookshelf_tokens, dict):
                bookshelf_tokens.clear()

            state: dict[str, Any] = {}
            plan: dict[str, Any] = {}
            rebuild_error = ""
            if rebuild_today:
                try:
                    state, plan, _ = await self._rebuild_today_after_reset()
                except Exception as exc:
                    rebuild_error = _single_line(exc, 180)
                    logger.warning(
                        "当前人格资料已重置，但今日数据重建失败: persona=%s error=%s",
                        pid or "single",
                        rebuild_error,
                        exc_info=True,
                    )
            return {
                "ok": True,
                "persona_id": pid,
                "generation": generation,
                "backup_path": str(backup_path or ""),
                "state": state,
                "plan": plan,
                "rebuild_error": rebuild_error,
                "external_memory_preserved": synchronizer is None,
                "non_req041_external_memory_preserved": True,
                "scoped_memory_reset": bool(synchronizer is not None and scoped_reset.get("ok")),
                "scoped_cleanup": scoped_reset,
            }
        finally:
            if token is not None:
                self._deactivate_persona_for_event(token)

    def _persona_profile_ids(self, *, strict: bool = False) -> list[str]:
        ids = self._configured_multi_persona_ids(strict=strict)
        profiles = getattr(self, "_persona_data_profiles", {})
        if strict and not isinstance(profiles, dict):
            raise PersonaConfigError(
                "persona profile cache cannot be enumerated"
            )
        if isinstance(profiles, dict):
            for pid in profiles:
                if strict and not isinstance(pid, str):
                    raise PersonaConfigError(
                        "persona profile cache contains a non-text id"
                    )
                clean = self._sanitize_persona_id(pid)
                if strict and pid.strip() not in {"", clean}:
                    raise PersonaConfigError(
                        "persona profile cache contains a non-canonical id"
                    )
                if clean and clean not in ids:
                    ids.append(clean)
                elif strict and not clean:
                    raise PersonaConfigError(
                        "persona profile enumeration contains an invalid id"
                    )
        try:
            profiles_dir = Path(self._persona_profiles_dir)
            if strict:
                try:
                    root_stat = profiles_dir.lstat()
                except FileNotFoundError:
                    paths = []
                else:
                    if not stat.S_ISDIR(root_stat.st_mode):
                        raise PersonaConfigError(
                            "persona profile root is not a regular directory"
                        )
                    paths = sorted(
                        profiles_dir.iterdir(),
                        key=lambda item: item.name,
                    )
                candidates = [
                    path
                    for path in paths
                    if path.suffix.lower() in {".db", ".json"}
                ]
            else:
                candidates = [
                    path
                    for pattern in ("*.db", "*.json")
                    for path in profiles_dir.glob(pattern)
                ]
            for path in candidates:
                if strict:
                    entry_stat = path.lstat()
                    if not stat.S_ISREG(entry_stat.st_mode):
                        raise PersonaConfigError(
                            "persona profile entry is not a regular file"
                        )
                clean = self._persona_id_from_profile_path(path)
                if strict and not clean:
                    raise PersonaConfigError(
                        "persona profile entry cannot be identified"
                    )
                if strict:
                    expected_name = (
                        self._persona_profile_db_filename(clean)
                        if path.suffix == ".db"
                        else self._persona_profile_filename(clean)
                    )
                    if path.name != expected_name:
                        raise PersonaConfigError(
                            "persona profile entry is not canonical"
                        )
                if clean and clean not in ids:
                    ids.append(clean)
        except Exception:
            if strict:
                raise PersonaConfigError(
                    "persona profile enumeration is unavailable"
                ) from None
        return ids

    def _persona_profile_snapshot_if_exists(self, persona_id: Any) -> dict[str, Any] | None:
        """Read an existing profile without creating one as a side effect."""
        pid = self._sanitize_persona_id(persona_id)
        if not pid:
            return None
        profiles = getattr(self, "_persona_data_profiles", {})
        if isinstance(profiles, dict) and isinstance(profiles.get(pid), dict):
            return profiles[pid]
        if not self._secondary_persona_store_exists(pid):
            return None
        try:
            payload = self._load_secondary_persona_store_sync(pid).data
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def _persona_profile_snapshot_read_only(
        self,
        persona_id: Any,
    ) -> dict[str, Any] | None:
        """Inspect persisted profile state without migration or initialization."""
        pid = self._sanitize_persona_id(persona_id)
        if not pid:
            raise PersonaConfigError(
                "read-only persisted profile requires a persona id"
            )
        legacy_path, database_path = self._persona_profile_store_paths(pid)
        return read_persona_store_snapshot_read_only(
            persona_id=pid,
            legacy_json_path=legacy_path,
            sqlite_path=database_path,
        )

    def _persona_config_exists(self, persona_id: Any) -> bool:
        pid = self._sanitize_persona_id(persona_id)
        if not pid or pid == self._primary_persona_id():
            return bool(pid)
        errors = getattr(self, "_persona_profile_errors", {})
        if isinstance(errors, dict) and pid in errors:
            return False
        profile = self._persona_profile_snapshot_if_exists(pid)
        if not isinstance(profile, dict):
            return False
        settings = profile.get(PERSONA_SETTINGS_KEY)
        if not isinstance(settings, dict) or not _single_line(settings.get("bot_name"), 80):
            return False
        try:
            revision = int(profile.get(PERSONA_SETTINGS_REVISION_KEY) or 0)
        except (TypeError, ValueError):
            revision = 0
        if revision > 0 or any(key != "bot_name" for key in settings):
            return True
        factory = getattr(self, "_new_store", None)
        if not callable(factory):
            return False
        try:
            baseline = factory()
            ensure_defaults = getattr(self, "_ensure_store_defaults", None)
            if callable(ensure_defaults):
                baseline = ensure_defaults(baseline)
            candidate = deepcopy(profile)
            for key in (
                PERSONA_SETTINGS_KEY,
                PERSONA_SETTINGS_VERSION_KEY,
                PERSONA_SETTINGS_REVISION_KEY,
            ):
                candidate.pop(key, None)
                baseline.pop(key, None)
            for payload in (candidate, baseline):
                for key in tuple(payload):
                    if payload.get(key) in (None, "", [], {}) and key not in (
                        baseline if payload is candidate else candidate
                    ):
                        payload.pop(key, None)
            return candidate != baseline
        except Exception:
            return False

    def _persona_config_profile_ids(self) -> list[str]:
        primary = self._primary_persona_id()
        candidates: list[str] = []
        profiles = getattr(self, "_persona_data_profiles", {})
        if isinstance(profiles, dict):
            candidates.extend(map(str, profiles))
        try:
            profiles_dir = Path(self._persona_profiles_dir)
            for pattern in ("*.db", "*.json"):
                candidates.extend(
                    self._persona_id_from_profile_path(path)
                    for path in profiles_dir.glob(pattern)
                )
        except Exception:
            pass
        result: list[str] = []
        for candidate in candidates:
            pid = self._sanitize_persona_id(candidate)
            if (
                pid
                and pid != primary
                and pid not in result
                and self._persona_config_exists(pid)
            ):
                result.append(pid)
        return result

    def _persona_window_bindings(self) -> dict[str, str]:
        # Compatibility surface only. AstrBot is the sole routing authority.
        return {}
