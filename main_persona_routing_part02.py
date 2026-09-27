# -*- coding: utf-8 -*-
"""PrivateCompanionPluginPersonaRoutingPart02Mixin。

由 tools/split_mixin_domain.py 从 main_persona_routing.py 机械抽取（17 个方法 + 0 个模块级名字 + 0 个类级赋值 / 482 行）。
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
from .main_persona_routing_shared import _ACTIVE_PERSONA_ID
from .main_persona_routing_shared import _single_line
from .main_persona_routing_shared import deepcopy
from .main_persona_routing_shared import detach_persona_settings
from .main_persona_routing_shared import hashlib
from .main_persona_routing_shared import json
from .main_persona_routing_shared import os
from .main_persona_routing_shared import time
from .main_persona_routing_shared import uuid



class PrivateCompanionPluginPersonaRoutingPart02Mixin:
    """PrivateCompanionPluginPersonaRoutingPart02Mixin（从 PrivateCompanionPluginPersonaRoutingMixin 拆出）。"""


    def _persona_window_bindings_store_path(self) -> Path:
        configured = str(getattr(self, "_persona_window_bindings_file", "") or "").strip()
        if configured:
            return Path(configured)
        data_dir = str(getattr(self, "data_dir", "") or "").strip()
        if data_dir:
            return Path(data_dir) / "persona_window_bindings.json"
        profiles_dir = Path(str(getattr(self, "_persona_profiles_dir", "persona_profiles")))
        return profiles_dir.parent / "persona_window_bindings.json"

    def _retire_legacy_persona_routing_sync(self) -> dict[str, Any]:
        """Back up legacy plugin-owned routes without using them at runtime."""
        config_bindings = self._cfg_raw(
            getattr(self, "config", {}), "multi_persona_window_bindings", {}
        )
        config_bindings = dict(config_bindings) if isinstance(config_bindings, dict) else {}
        path = self._persona_window_bindings_store_path()
        file_bindings: dict[str, Any] = {}
        file_error = ""
        if path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict) and isinstance(payload.get("bindings"), dict):
                    file_bindings = dict(payload["bindings"])
                elif isinstance(payload, dict):
                    file_bindings = dict(payload)
                else:
                    file_error = "legacy_binding_root_invalid"
            except Exception as exc:
                file_error = _single_line(exc, 160) or "legacy_binding_read_failed"
        merged = {
            _single_line(window, 240): self._sanitize_persona_id(persona_id)
            for window, persona_id in {**config_bindings, **file_bindings}.items()
            if _single_line(window, 240) and self._sanitize_persona_id(persona_id)
        }
        backup_path = path.with_name("persona_window_bindings.retired.json")
        if (merged or file_error) and not backup_path.exists():
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = backup_path.with_name(
                f".{backup_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
            )
            try:
                temporary.write_text(
                    json.dumps(
                        {
                            "version": 1,
                            "retired_at": time.time(),
                            "reason": "astrbot_is_persona_routing_authority",
                            "config_bindings": config_bindings,
                            "file_bindings": file_bindings,
                            "source_file": str(path),
                            "source_file_error": file_error,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                os.replace(temporary, backup_path)
            finally:
                temporary.unlink(missing_ok=True)
        status = {
            "ignored": bool(merged or file_error),
            "count": len(merged),
            "backup_path": str(backup_path) if backup_path.exists() else "",
            "warning_code": "legacy_persona_binding_ignored" if merged or file_error else "",
            "source_file_preserved": path.is_file(),
            "source_file_error": file_error,
        }
        self._legacy_persona_window_bindings = merged
        self._legacy_persona_routing_status = status
        if status["ignored"]:
            logger.warning(
                "已停用插件窗口人格路由，AstrBot 为唯一权威: count=%s backup=%s error=%s",
                len(merged),
                status["backup_path"] or "-",
                file_error or "-",
            )
        return status

    def _persona_id_for_event(self, event: Any) -> tuple[str, str]:
        """Return a cached plugin scope without consulting legacy bindings."""
        umo = str(getattr(event, "unified_msg_origin", "") or "").strip()
        cached = getattr(event, "_private_companion_persona_route_decision", None)
        pid = self._sanitize_persona_id(
            cached.get("plugin_persona_id") if isinstance(cached, dict) else ""
        )
        enabled = set(self._configured_multi_persona_ids())
        if pid not in enabled:
            pid = self._primary_persona_id()
        return pid, umo

    def _retire_deleted_persona_store_sync(self, persona_id: Any) -> dict[str, Any]:
        pid = self._sanitize_persona_id(persona_id)
        if not pid or pid == self._primary_persona_id():
            return {"persona_id": pid, "removed": [], "failed": []}
        removed: list[str] = []
        failed: list[str] = []
        legacy_path, database_path = self._persona_profile_store_paths(pid)
        backups: list[str] = []
        for path in (
            legacy_path,
            database_path,
            database_path.with_name(database_path.name + "-wal"),
            database_path.with_name(database_path.name + "-shm"),
        ):
            if not path.is_file():
                continue
            backup = self._backup_deleted_persona_store_sync(pid, path)
            if backup is None:
                failed.append(str(path))
                continue
            backups.append(str(backup))
            try:
                path.unlink()
                removed.append(str(path))
            except Exception as exc:
                failed.append(str(path))
                logger.warning("已删除人格档案清理失败，保留原文件: persona=%s path=%s error=%s", pid, path, _single_line(exc, 160))
        if not failed:
            registry = getattr(self, "_persona_sqlite_store_registry", None)
            discard = getattr(registry, "discard", None)
            if callable(discard):
                try:
                    discard(database_path)
                except Exception:
                    pass
            profiles = getattr(self, "_persona_data_profiles", None)
            if isinstance(profiles, dict):
                profiles.pop(pid, None)
            errors = getattr(self, "_persona_profile_errors", None)
            if isinstance(errors, dict):
                errors.pop(pid, None)
            self._reset_persona_prompt_caches(pid)
            if self._sanitize_persona_id(getattr(self, "_page_current_persona_id", "")) == pid:
                self._page_current_persona_id = self._primary_persona_id()
        return {"persona_id": pid, "removed": removed, "failed": failed, "backups": backups}

    def _persona_profile_route_status(self, persona_id: Any) -> tuple[bool, str]:
        pid = self._sanitize_persona_id(persona_id)
        primary = self._primary_persona_id()
        if not pid:
            return False, "persona_id_missing"
        if pid not in set(self._configured_multi_persona_ids()):
            return False, "persona_not_enabled"
        if pid == primary:
            return True, "primary"
        errors = getattr(self, "_persona_profile_errors", {})
        if isinstance(errors, dict) and pid in errors:
            return False, "persona_profile_degraded"
        if not self._persona_config_exists(pid):
            return False, "persona_config_missing"
        return True, "configured"

    @staticmethod
    def _persona_routing_warning_is_active(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        status = _single_line(item.get("status"), 16).lower()
        return not status or status == "active"

    @staticmethod
    def _persona_routing_warning_family(code: Any, channel: Any = "") -> str:
        normalized_code = _single_line(code, 100).lower()
        normalized_channel = _single_line(channel, 24).lower()
        if normalized_code == "persona.route.legacy_binding_ignored":
            return "legacy_binding"
        if normalized_channel == "passive" or normalized_code in {
            "persona.route.passive_primary_fallback",
        }:
            return "passive_delivery"
        if normalized_channel == "proactive" or normalized_code.startswith("persona.route.proactive_"):
            return "proactive_delivery"
        if normalized_code == "persona.route.plugin_persona_unspecified":
            return f"{normalized_channel or 'unknown'}_delivery"
        return normalized_code or "unknown"

    async def _activate_persona_for_event_context(self, event: Any) -> tuple[Any, str]:
        if not bool(getattr(self, "enable_multi_persona_mode", False)):
            # Single-persona mode follows AstrBot's effective session persona.
            # An empty plugin-specific ID is valid and should not create a
            # persistent troubleshooting warning.
            if self._primary_persona_id():
                await self._resolve_persona_routing_warnings(
                    channel="passive",
                    window_key=getattr(event, "unified_msg_origin", ""),
                    warning_families={"passive_delivery"},
                )
            return None, ""
        active = _ACTIVE_PERSONA_ID.get()
        if active:
            return None, active
        cached = getattr(event, "_private_companion_persona_route_decision", None)
        if isinstance(cached, dict):
            pid = self._sanitize_persona_id(cached.get("plugin_persona_id"))
            if pid:
                return _ACTIVE_PERSONA_ID.set(pid), pid

        primary = self._primary_persona_id()
        resolved = await self._astrbot_effective_persona_for_event(event)
        astrbot_persona = self._sanitize_persona_id(resolved.get("persona_id"))
        legacy_bindings = getattr(self, "_legacy_persona_window_bindings", {})
        legacy_persona = self._sanitize_persona_id(
            legacy_bindings.get(str(resolved.get("umo") or ""), "")
            if isinstance(legacy_bindings, dict)
            else ""
        )
        if legacy_persona and legacy_persona != astrbot_persona:
            await self._record_persona_routing_warning(
                code="persona.route.legacy_binding_ignored",
                channel="passive",
                disposition="ignored",
                reason_code="astrbot_is_routing_authority",
                window_key=resolved.get("umo"),
                requested_persona_id=legacy_persona,
                resolved_persona_id=astrbot_persona,
                active_persona_id=astrbot_persona,
            )
        ready, route_reason = self._persona_profile_route_status(astrbot_persona)
        if resolved.get("explicit_none"):
            ready, route_reason = False, "astrbot_persona_explicit_none"
        elif not astrbot_persona:
            ready, route_reason = False, "astrbot_persona_unresolved"
        elif not resolved.get("exists"):
            ready, route_reason = False, "astrbot_persona_missing"
        pid = astrbot_persona if ready else primary
        if not pid:
            await self._record_persona_routing_warning(
                code="persona.route.passive_primary_fallback",
                channel="passive",
                disposition="fallback_unavailable",
                reason_code="primary_persona_invalid",
                window_key=resolved.get("umo"),
                requested_persona_id=astrbot_persona,
            )
            return None, ""
        primary_ready, primary_reason = self._persona_profile_route_status(primary)
        if not ready and not primary_ready:
            await self._record_persona_routing_warning(
                code="persona.route.passive_primary_fallback",
                channel="passive",
                disposition="fallback_unavailable",
                reason_code=f"{route_reason}:{primary_reason}",
                window_key=resolved.get("umo"),
                requested_persona_id=astrbot_persona,
                resolved_persona_id=primary,
            )
            return None, ""

        decision = {
            "astrbot_persona_id": astrbot_persona,
            "plugin_persona_id": pid,
            "source": resolved.get("source"),
            "reason_code": route_reason,
            "fallback": not ready,
            "umo": resolved.get("umo"),
        }
        try:
            setattr(event, "_private_companion_persona_route_decision", decision)
            setattr(event, "private_companion_astrbot_persona_id", astrbot_persona)
            setattr(event, "private_companion_persona_id", pid)
            setattr(event, "private_companion_persona_window", "")
            setattr(event, "private_companion_persona_conflict", {})
        except Exception:
            pass
        if not ready:
            await self._record_persona_routing_warning(
                code="persona.route.passive_primary_fallback",
                channel="passive",
                disposition="fallback",
                reason_code=route_reason,
                window_key=resolved.get("umo"),
                requested_persona_id=astrbot_persona,
                resolved_persona_id=primary,
                active_persona_id=pid,
            )
        else:
            await self._resolve_persona_routing_warnings(
                channel="passive",
                window_key=resolved.get("umo"),
                warning_families={"passive_delivery"},
            )
        self._ensure_persona_profile(pid)
        return _ACTIVE_PERSONA_ID.set(pid), pid

    def _activate_persona_for_event(self, event: Any) -> tuple[Any, str]:
        """Legacy synchronous activation uses only a prior async decision."""
        if not bool(getattr(self, "enable_multi_persona_mode", False)):
            return None, ""
        pid, _ = self._persona_id_for_event(event)
        if not pid:
            return None, ""
        self._ensure_persona_profile(pid)
        return _ACTIVE_PERSONA_ID.set(pid), pid

    def _activate_persona_id(self, persona_id: Any, *, allow_inactive: bool = False) -> Any:
        pid = self._sanitize_persona_id(persona_id)
        if not pid or not bool(getattr(self, "enable_multi_persona_mode", False)):
            return None
        profile_errors = getattr(self, "_persona_profile_errors", {})
        if isinstance(profile_errors, dict) and pid in profile_errors and not allow_inactive:
            return None
        if not allow_inactive and pid not in set(self._configured_multi_persona_ids()):
            return None
        if pid != self._primary_persona_id() and not self._persona_config_exists(pid):
            return None
        self._ensure_persona_profile(pid)
        return _ACTIVE_PERSONA_ID.set(pid)

    def _deactivate_persona_for_event(self, token: Any) -> None:
        if token is not None:
            _ACTIVE_PERSONA_ID.reset(token)

    def _reset_persona_prompt_caches(self, *persona_ids: Any) -> None:
        for attr, value in (
            ("_default_persona_prompt_cache", ""),
            ("_default_persona_prompt_cache_at", 0.0),
            ("_default_persona_prompt_cache_umo", ""),
            ("_default_persona_prompt_cache_persona_id", ""),
            ("_default_persona_prompt_cache_by_scope", {}),
        ):
            try:
                setattr(self, attr, deepcopy(value))
            except Exception:
                pass

        ids = {
            pid
            for pid in (self._sanitize_persona_id(value) for value in persona_ids)
            if pid
        }
        cache = getattr(self, "_passive_light_injection_cache", None)
        if not isinstance(cache, dict) or "text" in cache or not ids:
            self._passive_light_injection_cache = {}
            return
        next_cache = dict(cache)
        for pid in ids:
            next_cache.pop(pid, None)
        self._passive_light_injection_cache = next_cache

    def _multi_persona_status(self) -> dict[str, Any]:
        enabled = bool(getattr(self, "enable_multi_persona_mode", False))
        primary = self._primary_persona_id()
        configured_profiles = self._persona_config_profile_ids()
        profile_labels: dict[str, str] = {}
        if primary:
            profile_labels[primary] = _single_line(
                getattr(self, "bot_name", "") or primary,
                80,
            )
        for pid in configured_profiles:
            profile = self._persona_profile_snapshot_if_exists(pid)
            settings = profile.get(PERSONA_SETTINGS_KEY) if isinstance(profile, dict) else {}
            profile_labels[pid] = _single_line(
                settings.get("bot_name") if isinstance(settings, dict) else "",
                80,
            ) or pid
        return {
            "enabled": enabled,
            "primary": primary,
            "enabled_ids": self._configured_multi_persona_ids(),
            "configured_profiles": configured_profiles,
            "profiles": [primary, *configured_profiles] if primary else configured_profiles,
            "profile_labels": profile_labels,
            "window_bindings": {},
            "window_conflicts": {},
            "window_bindings_revision": 0,
            "routing_authority": "astrbot",
            "legacy_routing": deepcopy(getattr(self, "_legacy_persona_routing_status", {})),
            "primary_setup": {
                "required": bool(
                    getattr(self, "_multi_persona_primary_requires_configuration", False)
                    or getattr(self, "_multi_persona_primary_invalid", False)
                    or (getattr(self, "_multi_persona_enable_requested", False) and not primary)
                ),
                "invalid": bool(getattr(self, "_multi_persona_primary_invalid", False)),
                "legacy_candidate": self._sanitize_persona_id(
                    getattr(self, "_legacy_multi_persona_primary_id_candidate", "")
                ),
                "legacy_mismatch": deepcopy(
                    getattr(self, "_multi_persona_primary_id_mismatch", {})
                ),
            },
            "profile_errors": deepcopy(getattr(self, "_persona_profile_errors", {})),
            "settings_migration": deepcopy(getattr(self, "_persona_settings_migration_status", {})),
            "deleted_persona_reconciliation": deepcopy(
                getattr(self, "_persona_deleted_reconciliation_status", {})
            ),
        }

    def _persona_config_state(self, persona_id: Any) -> dict[str, Any]:
        pid = self._sanitize_persona_id(persona_id)
        primary = self._primary_persona_id()
        if not pid:
            pid = primary
        if not pid:
            raise PersonaConfigError("人格 ID 不能为空")
        is_primary = pid == primary
        if not is_primary and not self._persona_config_exists(pid):
            raise PersonaConfigError("该人格尚未创建独立配置")
        profile = self._ensure_persona_profile(pid)
        raw = {} if is_primary else deepcopy(profile.get(PERSONA_SETTINGS_KEY) or {})
        effective = self.effective_persona_settings(pid, include_common=False)
        manifest = self._persona_scope_manifest()
        sources = {
            key: (
                "primary"
                if is_primary
                else "persona"
                if key in raw
                else "primary"
            )
            for key, entry in manifest.items()
            if entry.get("scope") == "persona"
        }
        sensitive = {key for key, entry in manifest.items() if entry.get("sensitive")}
        redacted_effective = {
            key: ("***" if key in sensitive and value not in (None, "", [], {}) else value)
            for key, value in effective.items()
        }
        redacted_raw = {
            key: ("***" if key in sensitive and value not in (None, "", [], {}) else value)
            for key, value in raw.items()
            if key in manifest and manifest[key].get("scope") == "persona"
        }
        return {
            "persona_id": pid,
            "is_primary": is_primary,
            "enabled": pid in set(self._configured_multi_persona_ids()),
            "degraded": pid in set(getattr(self, "_persona_profile_errors", {})),
            "degraded_reason": str(getattr(self, "_persona_profile_errors", {}).get(pid, "")),
            "schema_version": int(profile.get(PERSONA_SETTINGS_VERSION_KEY) or PERSONA_SETTINGS_SCHEMA_VERSION),
            "revision": int(profile.get(PERSONA_SETTINGS_REVISION_KEY) or 0),
            "settings": redacted_effective,
            "raw_settings": redacted_raw,
            "sources": sources,
        }

    @staticmethod
    def _persona_setting_invalidates_runtime_cache(
        key: str,
        manifest: dict[str, dict[str, Any]],
    ) -> bool:
        entry = manifest.get(str(key)) or {}
        lowered = str(key).lower()
        return bool(entry.get("identity")) or any(
            marker in lowered
            for marker in (
                "provider",
                "prompt",
                "reference",
                "worldbook",
                "knowledge_source",
                "voice",
                "tts_",
            )
        )

    def _persona_detach_preview(self, persona_id: Any) -> dict[str, Any]:
        pid = self._sanitize_persona_id(persona_id)
        primary = self._primary_persona_id()
        if not pid or pid == primary:
            return {"ok": False, "code": "persona_detach_target_invalid", "message": "主人格不需要脱离跟随"}
        if not self._persona_config_exists(pid):
            return {"ok": False, "code": "persona_config_missing", "message": "该人格尚未创建独立配置，不能执行脱离"}
        profile = self._ensure_persona_profile(pid)
        revision = int(profile.get(PERSONA_SETTINGS_REVISION_KEY) or 0)
        settings = profile.get(PERSONA_SETTINGS_KEY) or {}
        detached = detach_persona_settings(settings, self._primary_persona_config(), manifest=self._persona_scope_manifest())
        existing = sorted(set(detached) & set(settings))
        missing = sorted(set(detached) - set(settings))
        digest = hashlib.sha256(
            json.dumps({"persona_id": pid, "revision": revision, "settings": detached}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return {
            "ok": True,
            "persona_id": pid,
            "revision": revision,
            "existing_override_keys": existing,
            "existing_override_count": len(existing),
            "follow_primary_keys": missing,
            "follow_primary_count": len(missing),
            "final_settings_count": len(detached),
            # Kept for compatibility with clients built before split statistics.
            "missing_keys": missing,
            "materialized_count": len(detached),
            "preview_hash": digest,
        }

    def _unified_persona_domain(self) -> str:
        """Return a stable, opaque identity domain for the active persona."""
        if not bool(getattr(self, "enable_multi_persona_mode", False)):
            return ""
        persona_id = self._sanitize_persona_id(self._effective_plugin_persona_id())
        if not persona_id:
            return ""
        digest = hashlib.sha256(persona_id.encode("utf-8")).hexdigest()[:16]
        return f"persona:{digest}"
