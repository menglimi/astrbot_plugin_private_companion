# -*- coding: utf-8 -*-

from __future__ import annotations

import sys
import uuid
import asyncio
import hashlib
import json
import re
import time
import types
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


from .bot_personal_contract import (
    BOT_PERSONAL_CAPABILITY_SCHEMA_VERSION,
    BOT_PERSONAL_CANONICAL_SCHEMA_VERSION,
    BOT_PERSONAL_MEMORY_DOMAIN,
    BOT_PERSONAL_MEMORY_TYPES,
    BOT_PERSONAL_PAYLOAD_SCHEMA_VERSION,
    CONTRACT_FINGERPRINT,
    CONTRACT_REVISION,
    WINDOW_SLUGS,
    window_for_minutes,
)
from .bot_personal_outbox import BotPersonalOutbox
from .helpers import _missing_optional_model_dependency, _now_ts, _path_text, _safe_float, _safe_int, _single_line
from .companion_interaction_expression import current_interaction_projection
from .relationship_ledger import normalize_relationship_mode
from .relationship_policy import relationship_projection_for_bridge
from .namespace_capability import negotiate_namespace_capability
from .identity_namespace import validate_namespace_context
from .persona_config import runtime_persona_setting
from .conversation_prompt_section import prompt_section
from .logging_util import get_module_logger

logger = get_module_logger(__name__)


# The v2 contract was published by the previous Memory Companion release.
# Keep this tuple narrow: only this known, fully-compatible legacy descriptor
# may negotiate down; arbitrary mismatches remain degraded.
_LEGACY_V2_CONTRACT = {
    "contract_fingerprint": "0ffe3a1ab69b659c",
    "contract_revision": 2,
    "capability_schema_version": "1.2",
    "canonical_schema_version": 2,
    "payload_schema_version": "1.0",
}


def _memory_companion_safe_float(value: Any, default: float, minimum: float = 0.0) -> float:
    helper = globals().get("_safe_float")
    if callable(helper):
        return helper(value, default, minimum)
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, parsed)


class MemoryCompanionAdapterMixin:
    """Optional bridge helpers for astrbot_plugin_memory_companion."""

    _bridge_cache: Any | None = None
    _bridge_cache_ts: float = 0.0
    _BRIDGE_CACHE_TTL: float = 30.0
    _BRIDGE_MISSING_CACHE_TTL: float = 2.0
    _bridge_dependency_failure_until: float = 0.0
    _bridge_dependency_failure_module: str = ""
    _bridge_last_status: dict[str, Any] = {}

    _MEMORY_COMPANION_PLUGIN_ALIASES = frozenset(
        {
            "astrbot_plugin_memory_companion",
            "astrbot_plugin_remember_you",
            "memorycompanion",
            "memory_companion",
            "rememberyou",
            "remember_you",
            "我会牢牢记住你",
        }
    )

    @staticmethod
    def _memory_companion_coerce_bool(value: Any, default: bool = True) -> bool:
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on", "enabled"}:
                return True
            if normalized in {"0", "false", "no", "off", "disabled"}:
                return False
        if value is None:
            return default
        return bool(value)

    def _memory_companion_bridge_enabled(self) -> bool:
        """Read the Bridge switch without consulting the legacy LivingMemory switch."""
        for attr in ("enable_memory_companion_bridge", "memory_companion_bridge_enabled"):
            if hasattr(self, attr):
                return self._memory_companion_coerce_bool(getattr(self, attr), True)
        config = getattr(self, "config", None)
        marker = object()
        for key in (
            "enable_memory_companion_bridge",
            "memory_companion_bridge.enabled",
            "private_companion_bridge.enabled",
        ):
            value: Any = marker
            if isinstance(config, dict):
                current: Any = config
                for part in key.split("."):
                    if not isinstance(current, dict) or part not in current:
                        current = marker
                        break
                    current = current[part]
                value = current
            else:
                getter = getattr(config, "get", None)
                if callable(getter):
                    try:
                        value = getter(key, marker)
                    except Exception:
                        value = marker
            if value is not marker:
                return self._memory_companion_coerce_bool(value, True)
        return True

    def _memory_companion_emotion_producer_capability(self, bridge: Any) -> Any | None:
        """Return the live, non-serializable capability issued by MemoryCompanion."""
        if bridge is None:
            return None
        if (
            getattr(self, "_memory_companion_emotion_capability_bridge", None) is bridge
            and getattr(self, "_memory_companion_emotion_producer_capability_cache", None) is not None
        ):
            return getattr(self, "_memory_companion_emotion_producer_capability_cache")
        register = getattr(bridge, "register_emotion_producer", None)
        if not callable(register):
            return None
        capability = None
        for producer in (self, type(self)):
            try:
                capability = register(producer)
            except Exception as exc:
                if self._memory_companion_optional_dependency_failed(exc, where="register_emotion_producer"):
                    return None
                logger.debug("emotion producer registration failed: %s", _single_line(exc, 120))
                continue
            if capability is not None:
                break
        if capability is None:
            return None
        self._memory_companion_emotion_capability_bridge = bridge
        self._memory_companion_emotion_producer_capability_cache = capability
        return capability

    def _memory_companion_emotion_producer_context(self, bridge: Any, event: Any) -> Any | None:
        """Bind a mirror write to one authoritative private Companion domain."""
        if not isinstance(event, dict):
            return None
        actor = event.get("actor_ref") if isinstance(event.get("actor_ref"), dict) else {}
        bot_id = _single_line(event.get("bot_id"), 160)
        platform = _single_line(event.get("platform"), 80)
        scope = _single_line(event.get("scope"), 24).lower()
        user_id = _single_line(actor.get("id"), 160)
        session_id = _single_line(event.get("session_id"), 220)
        if (
            scope != "private"
            or _single_line(actor.get("kind"), 24).lower() != "user"
            or not all((bot_id, platform, user_id, session_id))
            or not session_id.startswith(f"{platform}:")
        ):
            return None
        capability = self._memory_companion_emotion_producer_capability(bridge)
        creator = getattr(bridge, "create_emotion_producer_context", None) if bridge is not None else None
        if capability is None or not callable(creator):
            return None
        try:
            return creator(
                capability,
                bot_id=bot_id,
                scope="private",
                platform=platform,
                user_id=user_id,
                session_id=session_id,
            )
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="create_emotion_producer_context"):
                return None
            logger.debug("emotion producer context failed: %s", _single_line(exc, 120))
            return None

    def _memory_companion_emotion_delivery_context(
        self,
        bridge: Any,
        *,
        event: Any,
        user_id: str,
        user: dict[str, Any] | None,
    ) -> Any | None:
        """Bind afterglow delivery to the active, verified private message domain."""
        private_checker = getattr(self, "_safe_event_is_private", None)
        if callable(private_checker):
            try:
                if not bool(private_checker(event)):
                    return None
            except Exception:
                return None
        else:
            is_private = getattr(event, "is_private_chat", None)
            if not callable(is_private):
                return None
            try:
                if not bool(is_private()):
                    return None
            except Exception:
                return None
        sender_getter = getattr(self, "_safe_event_sender_id", None)
        try:
            sender_id = _single_line(sender_getter(event), 160) if callable(sender_getter) else _single_line(event.get_sender_id(), 160)
        except Exception:
            return None
        canonicalizer = getattr(self, "_canonical_private_user_id", None)
        try:
            canonical_sender_id = _single_line(canonicalizer(sender_id), 160) if callable(canonicalizer) else sender_id
        except Exception:
            return None
        verified_user_id = _single_line(user_id, 160)
        session_id = _single_line(getattr(event, "unified_msg_origin", ""), 220)
        platform = session_id.split(":", 1)[0] if ":" in session_id else ""
        profile_session = _single_line(user.get("umo"), 220) if isinstance(user, dict) else ""
        bot_id = self._memory_companion_bridge_bot_id(event)
        if (
            not isinstance(user, dict)
            or not all((bot_id, platform, session_id, canonical_sender_id, verified_user_id))
            or canonical_sender_id != verified_user_id
            or profile_session != session_id
        ):
            return None
        capability = self._memory_companion_emotion_producer_capability(bridge)
        creator = getattr(bridge, "create_emotion_delivery_context", None) if bridge is not None else None
        if capability is None or not callable(creator):
            return None
        try:
            return creator(
                capability,
                bot_id=bot_id,
                scope="private",
                platform=platform,
                user_id=verified_user_id,
                session_id=session_id,
                allow_cross_window=self._memory_companion_coerce_bool(
                    getattr(self, "enable_memory_companion_cross_window_emotion", True),
                    True,
                ),
            )
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="create_emotion_delivery_context"):
                return None
            logger.debug("emotion delivery context failed: %s", _single_line(exc, 120))
            return None

    async def _memory_companion_record_emotion_event(self, event: dict[str, Any]) -> None:
        bridge = self._memory_companion_bridge()
        recorder = getattr(bridge, "record_emotion_event", None) if bridge is not None else None
        producer_context = self._memory_companion_emotion_producer_context(bridge, event)
        if not callable(recorder) or producer_context is None:
            return
        try:
            await recorder(dict(event or {}), producer_context=producer_context)
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="record_emotion_event"):
                return
            logger.debug("emotion event mirror failed: %s", _single_line(exc, 120))

    def _memory_companion_degraded_status(self, reason: str, **extra: Any) -> dict[str, Any]:
        status = {
            "available": False,
            "state": "local_only" if reason == "bridge_disabled" else "degraded",
            "degraded": reason != "bridge_disabled",
            "reason": reason,
        }
        status.update({key: value for key, value in extra.items() if value is not None})
        self._bridge_last_status = status
        return status

    def _memory_companion_invalidate_bridge_cache(self, reason: str = "") -> None:
        """Drop every in-process reference issued by the previously active bridge."""
        self._bridge_cache = None
        self._bridge_cache_ts = 0.0
        self._memory_companion_emotion_capability_bridge = None
        self._memory_companion_emotion_producer_capability_cache = None
        if reason:
            self._memory_companion_degraded_status(reason)

    @staticmethod
    def _memory_companion_bridge_lifecycle_active(bridge: Any | None) -> bool:
        """Treat old bridge implementations as live, but fail closed on a bad lifecycle probe."""
        if bridge is None:
            return False
        lifecycle = getattr(bridge, "bridge_lifecycle_status", None)
        if not callable(lifecycle):
            return True
        try:
            status = lifecycle()
        except Exception:
            return False
        return isinstance(status, dict) and status.get("active") is True

    def _memory_companion_filter_internal_error_context(self, value: Any) -> str:
        """Keep recalled Provider failures out of downstream generation prompts."""
        text = str(value or "").strip()
        detector = getattr(self, "_looks_like_internal_provider_error_text", None)
        if not text or not callable(detector):
            return text
        kept_lines: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line and detector(line):
                continue
            kept_lines.append(raw_line)
        return "\n".join(kept_lines).strip()

    def _memory_companion_optional_dependency_failed(self, exc: BaseException, *, where: str = "") -> bool:
        module = _missing_optional_model_dependency(exc)
        if not module:
            return False
        self._memory_companion_invalidate_bridge_cache()
        self._bridge_dependency_failure_until = time.monotonic() + 300.0
        self._bridge_dependency_failure_module = module
        self._memory_companion_degraded_status(
            "optional_dependency_missing",
            module=module,
            where=_single_line(where, 80) or "-",
        )
        logger.warning(
            "记忆插件可选模型依赖缺失，已临时降级 MemoryCompanion 桥接: module=%s where=%s err=%s",
            module,
            _single_line(where, 80) or "-",
            _single_line(exc, 160),
        )
        return True

    def _memory_companion_bridge(self) -> Any | None:
        if not self._memory_companion_bridge_enabled():
            self._memory_companion_degraded_status("bridge_disabled")
            return None
        now = time.monotonic()
        if now < self._bridge_dependency_failure_until:
            return None
        if self._bridge_cache is not None and (now - self._bridge_cache_ts) < self._BRIDGE_CACHE_TTL:
            if self._memory_companion_bridge_lifecycle_active(self._bridge_cache):
                return self._bridge_cache
            self._memory_companion_invalidate_bridge_cache("bridge_inactive")
            now = time.monotonic()
        negative_cache_ttl = (
            self._BRIDGE_MISSING_CACHE_TTL
            if self._bridge_last_status.get("reason") == "bridge_missing"
            else self._BRIDGE_CACHE_TTL
        )
        if (
            self._bridge_cache is None
            and (now - self._bridge_cache_ts) < negative_cache_ttl
            and self._bridge_last_status.get("reason")
            in {
                "bridge_missing",
                "capability_probe_missing",
                "capability_probe_exception",
                "capability_probe_invalid",
                "capability_contract_mismatch",
            }
        ):
            return None
        self._bridge_last_status = {}
        bridge = self._memory_companion_bridge_uncached()
        if bridge is not None:
            if not self._memory_companion_bridge_lifecycle_active(bridge):
                self._memory_companion_degraded_status("bridge_inactive")
                bridge = None
            else:
                capability_status = self._memory_companion_probe_capabilities(bridge)
                self._bridge_last_status = capability_status
                if not capability_status.get("available", False):
                    bridge = None
        self._bridge_cache = bridge
        self._bridge_cache_ts = now
        if bridge is None and not self._bridge_last_status:
            self._memory_companion_degraded_status("bridge_missing")
        return bridge

    def _memory_companion_bridge_uncached(self) -> Any | None:
        inspected_module_ids: set[int] = set()

        # Prefer AstrBot's currently registered live instance. During plugin
        # reloads, an old module alias can remain in sys.modules and expose a
        # stale bridge contract even though the active plugin is up to date.
        context = getattr(self, "context", None)
        get_all_stars = getattr(context, "get_all_stars", None)
        get_registered_star = getattr(context, "get_registered_star", None)
        registry_available = callable(get_all_stars) or callable(get_registered_star)
        inspected_star_ids: set[int] = set()
        if callable(get_all_stars):
            try:
                stars = list(get_all_stars() or [])
            except Exception:
                stars = []
            for metadata in stars:
                inspected_star_ids.add(id(metadata))
                if not self._memory_companion_star_matches(metadata):
                    continue
                bridge = self._memory_companion_bridge_from_star(metadata)
                if bridge is not None:
                    return bridge
                module = getattr(metadata, "module", None)
                if module is not None:
                    inspected_module_ids.add(id(module))

        if callable(get_registered_star):
            for plugin_name in (
                "astrbot_plugin_memory_companion",
                "astrbot_plugin_remember_you",
            ):
                try:
                    metadata = get_registered_star(plugin_name)
                except Exception:
                    metadata = None
                if metadata is None or id(metadata) in inspected_star_ids:
                    continue
                inspected_star_ids.add(id(metadata))
                bridge = self._memory_companion_bridge_from_star(metadata)
                if bridge is None:
                    bridge = self._memory_companion_bridge_from_object(metadata)
                if bridge is not None:
                    return bridge
                module = getattr(metadata, "module", None)
                if module is not None:
                    inspected_module_ids.add(id(module))

        if registry_available:
            return None

        for module_name in (
            "data.plugins.astrbot_plugin_remember_you.main",
            "astrbot_plugin_remember_you.main",
            "data.plugins.astrbot_plugin_memory_companion.main",
            "astrbot_plugin_memory_companion.main",
        ):
            module = sys.modules.get(module_name)
            if module is not None:
                inspected_module_ids.add(id(module))
            bridge = self._memory_companion_bridge_from_module(module)
            if bridge is not None:
                return bridge

        # Older AstrBot builds and some hot-reload paths may expose a different
        # module alias. Scan only modules that identify themselves exactly as
        # the supported memory plugin; similarly named third-party modules do
        # not qualify.
        for module in list(sys.modules.values()):
            if module is None or id(module) in inspected_module_ids:
                continue
            if not self._memory_companion_module_matches(module):
                continue
            bridge = self._memory_companion_bridge_from_module(module)
            if bridge is not None:
                return bridge
        return None

    @classmethod
    def _memory_companion_identity_matches(cls, value: Any) -> bool:
        if value is None:
            return False
        try:
            text = str(value).strip().lower()
        except Exception:
            # AstrBot may expose optional-model proxies (for example torch
            # namespaces) as metadata values. Their string conversion can
            # import a missing dependency; an invalid identity is simply not
            # a MemoryCompanion module.
            return False
        if not text:
            return False
        if text in cls._MEMORY_COMPANION_PLUGIN_ALIASES:
            return True
        normalized = re.sub(r"[\s\-]+", "_", text)
        if normalized in cls._MEMORY_COMPANION_PLUGIN_ALIASES:
            return True
        return any(part in cls._MEMORY_COMPANION_PLUGIN_ALIASES for part in text.split("."))

    @classmethod
    def _memory_companion_module_matches(cls, module: Any | None) -> bool:
        # AstrBot's plugin registry can expose proxy objects from optional
        # libraries (notably ``torch.classes``) as a module field. Those
        # proxies resolve arbitrary attributes as dynamic classes, so reading
        # ``__file__`` from them raises instead of returning a missing value.
        if module is None or not isinstance(module, types.ModuleType):
            return False
        module_vars = getattr(module, "__dict__", {})
        if isinstance(module_vars, dict) and cls._memory_companion_identity_matches(module_vars.get("PLUGIN_NAME")):
            return True
        if cls._memory_companion_identity_matches(getattr(module, "__name__", "")):
            return True
        module_file = _path_text(getattr(module, "__file__", ""))
        if module_file:
            path_parts = re.split(r"[\\/]", module_file.lower())
            return any(part in cls._MEMORY_COMPANION_PLUGIN_ALIASES for part in path_parts)
        return False

    @classmethod
    def _memory_companion_star_matches(cls, metadata: Any | None) -> bool:
        if metadata is None:
            return False
        try:
            values = (
                getattr(metadata, "name", ""),
                getattr(metadata, "display_name", ""),
                getattr(metadata, "root_dir_name", ""),
                getattr(metadata, "module_path", ""),
            )
        except Exception:
            return False
        if any(cls._memory_companion_identity_matches(value) for value in values):
            return True
        try:
            module = getattr(metadata, "module", None)
        except Exception:
            module = None
        return cls._memory_companion_module_matches(module)

    def _memory_companion_bridge_from_star(self, metadata: Any | None) -> Any | None:
        if metadata is None or not bool(getattr(metadata, "activated", True)):
            return None
        instance = getattr(metadata, "star_cls", None)
        bridge = self._memory_companion_bridge_from_object(instance)
        if bridge is not None:
            return bridge
        return self._memory_companion_bridge_from_module(getattr(metadata, "module", None))

    def _memory_companion_presence(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "detected": False,
            "installed": False,
            "loaded": False,
            "activated": False,
            "display_name": "我会牢牢记住你",
            "version": "",
            "plugin_dir": "",
            "reason": _single_line(self._bridge_last_status.get("reason"), 80),
        }
        context = getattr(self, "context", None)
        get_all_stars = getattr(context, "get_all_stars", None)
        if callable(get_all_stars):
            try:
                stars = list(get_all_stars() or [])
            except Exception:
                stars = []
            for metadata in stars:
                if not self._memory_companion_star_matches(metadata):
                    continue
                result.update(
                    {
                        "detected": True,
                        "installed": True,
                        "loaded": getattr(metadata, "star_cls", None) is not None,
                        "activated": bool(getattr(metadata, "activated", True)),
                        "display_name": _single_line(getattr(metadata, "display_name", ""), 80)
                        or "我会牢牢记住你",
                        "version": _single_line(getattr(metadata, "version", ""), 40),
                    }
                )
                root_dir_name = _single_line(getattr(metadata, "root_dir_name", ""), 120)
                if root_dir_name:
                    result["plugin_dir"] = str(Path(__file__).resolve().parent.parent / root_dir_name)
                return result

        plugin_root = Path(__file__).resolve().parent.parent
        for directory_name in ("astrbot_plugin_memory_companion", "astrbot_plugin_remember_you"):
            candidate = plugin_root / directory_name
            if not (candidate / "main.py").exists():
                continue
            result.update(
                {
                    "detected": True,
                    "installed": True,
                    "plugin_dir": str(candidate),
                }
            )
            metadata_path = candidate / "metadata.yaml"
            if metadata_path.exists():
                try:
                    metadata_text = metadata_path.read_text(encoding="utf-8")
                    version_match = re.search(r"(?m)^version:\s*[\"']?([^\n\"']+)", metadata_text)
                    display_match = re.search(r"(?m)^display_name:\s*[\"']?([^\n\"']+)", metadata_text)
                    if version_match:
                        result["version"] = _single_line(version_match.group(1), 40)
                    if display_match:
                        result["display_name"] = _single_line(display_match.group(1), 80)
                except Exception:
                    pass
            break
        return result

    def _memory_companion_outbox(self) -> BotPersonalOutbox | None:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return None
        persona_id = self._memory_companion_archive_persona_id()
        cache = getattr(self, "_bot_personal_outboxes", None)
        if not isinstance(cache, dict):
            cache = {}
            try:
                setattr(self, "_bot_personal_outboxes", cache)
            except Exception:
                pass
        current = cache.get(persona_id)
        if isinstance(current, BotPersonalOutbox) and current.data is data:
            return current

        default_data = getattr(self, "_data_default", None)
        is_default_backing = isinstance(default_data, dict) and data is default_data
        persona_data_getter = getattr(self, "_persona_data_for_save", None)
        try:
            is_exact_persona_backing = bool(
                callable(persona_data_getter)
                and persona_id
                and persona_data_getter(persona_id) is data
            )
        except Exception:
            is_exact_persona_backing = False
        secondary = bool(not is_default_backing and is_exact_persona_backing)

        def save_bound_outbox() -> Any:
            sections = {
                "bot_personal_outbox",
                "bot_personal_archive_revisions",
            }
            if secondary:
                scheduler = getattr(self, "_schedule_persona_data_save", None)
                if callable(scheduler):
                    return scheduler(persona_id, sections=sections, delay=0.5)
                return None
            if not is_default_backing and not is_exact_persona_backing:
                # Never guess a save target for an unrecognised backing dict.
                return None
            scheduler = getattr(self, "_schedule_default_data_save", None)
            if callable(scheduler):
                return scheduler(sections=sections, delay=0.5)
            fallback = getattr(self, "_schedule_data_save", None)
            if callable(fallback):
                return fallback(sections=sections, delay=0.5)
            return None

        lifecycle_task = getattr(self, "_create_lifecycle_background_task", None)
        try:
            current = BotPersonalOutbox(
                data,
                save=save_bound_outbox,
                background_task=(
                    lambda operation, label: lifecycle_task(operation, label=label)
                )
                if callable(lifecycle_task)
                else None,
            )
        except Exception as exc:
            logger.debug("Bot Personal outbox 初始化失败: %s", _single_line(exc, 120))
            return None
        try:
            cache[persona_id] = current
            setattr(self, "_bot_personal_outbox", current)
        except Exception:
            pass
        return current

    @staticmethod
    def _memory_companion_archive_business_value(value: Any) -> Any:
        ignored = {
            "archive_result",
            "archived_at",
            "created_at",
            "expires_at",
            "generated_at",
            "memory_archive",
            "memory_archive_result",
            "occurred_at",
            "sent_at",
            "updated_at",
            "version",
            "window",
        }
        if isinstance(value, dict):
            return {
                str(key): MemoryCompanionAdapterMixin._memory_companion_archive_business_value(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
                if str(key) not in ignored
            }
        if isinstance(value, (list, tuple)):
            return [
                MemoryCompanionAdapterMixin._memory_companion_archive_business_value(item)
                for item in value
            ]
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        return str(value)

    def _memory_companion_archive_revision(
        self,
        *,
        memory_type: str,
        local_date: str,
        business_payload: dict[str, Any],
    ) -> int:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return 1
        registry = data.setdefault("bot_personal_archive_revisions", {})
        if not isinstance(registry, dict):
            registry = {}
            data["bot_personal_archive_revisions"] = registry
        record_key = f"{str(memory_type or '').strip()}:{str(local_date or '').strip()}"
        canonical = self._memory_companion_archive_business_value(business_payload)
        encoded = json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        previous = registry.get(record_key)
        if isinstance(previous, dict) and previous.get("fingerprint") == fingerprint:
            try:
                return max(1, int(previous.get("revision") or 1))
            except (TypeError, ValueError, OverflowError):
                return 1
        try:
            revision = max(0, int(previous.get("revision") or 0)) + 1 if isinstance(previous, dict) else 1
        except (TypeError, ValueError, OverflowError):
            revision = 1
        registry[record_key] = {
            "revision": revision,
            "fingerprint": fingerprint,
        }
        return revision

    def _memory_companion_bot_personal_sender(self) -> Any | None:
        bridge = self._memory_companion_bridge()
        recorder = getattr(bridge, "record_bot_personal_archive", None) if bridge is not None else None
        capability = self._memory_companion_emotion_producer_capability(bridge)
        if not callable(recorder) or capability is None:
            return None

        async def _send(envelope: dict[str, Any]) -> dict[str, Any]:
            result = recorder(envelope, producer_capability=capability)
            if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                result = await result
            return result if isinstance(result, dict) else {"ok": False, "state": "retry", "error_code": "invalid_bridge_response"}

        return _send

    async def _memory_companion_record_bot_personal(
        self,
        *,
        memory_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
        occurred_at: str = "",
        version: int = 1,
        source_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        outbox = self._memory_companion_outbox()
        if outbox is None:
            return {
                "ok": False,
                "state": "local_only",
                "record_id": "",
                "deduplicated": False,
                "version": int(version or 1),
                "error_code": "outbox_unavailable",
            }
        # Probe before constructing the envelope so a v3 bridge gets the
        # namespace-aware format while a known v2 bridge receives a legacy
        # envelope it can still validate.  Local-only operation remains
        # available when no bridge is installed.
        try:
            self._memory_companion_bridge()
        except Exception:
            pass
        negotiated_schema = int(
            getattr(self, "_bridge_last_status", {}).get(
                "negotiated_canonical_schema_version", 2
            ) or 2
        )
        if negotiated_schema >= BOT_PERSONAL_CANONICAL_SCHEMA_VERSION:
            owner_bot_id = self._memory_companion_bridge_bot_id()
            persona_id = self._memory_companion_archive_persona_id()
            if not owner_bot_id or not persona_id:
                negotiated_schema = 2
                owner_bot_id = ""
                persona_id = ""
        else:
            owner_bot_id = ""
            persona_id = ""
        try:
            result = await outbox.enqueue(
                memory_type=memory_type,
                payload=payload,
                idempotency_key=idempotency_key,
                occurred_at=occurred_at or self._memory_companion_now_iso(),
                version=max(1, int(version or 1)),
                source_refs=source_refs,
                owner_bot_id=owner_bot_id,
                persona_id=persona_id,
                canonical_schema_version=negotiated_schema,
                sender=self._memory_companion_bot_personal_sender(),
            )
            self._bridge_last_status = {
                **getattr(self, "_bridge_last_status", {}),
                "bot_personal_outbox": outbox.status(),
            }
            return result
        except Exception as exc:
            logger.debug("Bot Personal 本地归档失败: %s", _single_line(exc, 160))
            return {
                "ok": False,
                "state": "local_only",
                "record_id": "",
                "deduplicated": False,
                "version": int(version or 1),
                "error_code": "outbox_enqueue_failed",
            }

    async def _memory_companion_flush_bot_personal_outbox(self, *, limit: int = 16) -> list[dict[str, Any]]:
        outbox = self._memory_companion_outbox()
        sender = self._memory_companion_bot_personal_sender()
        if outbox is None or sender is None:
            return []
        try:
            results = await outbox.drain(sender, limit=max(1, int(limit or 16)))
            self._bridge_last_status = {
                **getattr(self, "_bridge_last_status", {}),
                "bot_personal_outbox": outbox.status(),
            }
            return results
        except Exception as exc:
            logger.debug("Bot Personal outbox 补投失败: %s", _single_line(exc, 160))
            return []

    async def _memory_companion_record_observed_activity(self, activity: dict[str, Any]) -> dict[str, Any]:
        """Archive only private observed activity; group observations stay local/group-scoped."""
        if not isinstance(activity, dict) or _single_line(activity.get("visibility"), 32) != "private":
            return {"ok": False, "state": "local_only", "error_code": "non_private_activity"}
        activity_id = _single_line(activity.get("activity_id"), 160)
        title = _single_line(activity.get("title") or activity.get("summary"), 180)
        if not activity_id or not title:
            return {"ok": False, "state": "invalid", "error_code": "invalid_activity"}
        payload = {
            "date": _single_line(activity.get("start_at"), 10),
            "window": window_for_minutes(0),
            "summary": title,
            "activity_id": activity_id,
            "kind": _single_line(activity.get("kind"), 48),
            "participants": [_single_line(item, 80) for item in (activity.get("participants") or []) if _single_line(item, 80)][:8],
            "message_count": int(activity.get("message_count") or len(activity.get("source_refs") or []) or 1),
        }
        try:
            occurred_at = _single_line(activity.get("start_at"), 80) or self._memory_companion_now_iso()
            if "+" in occurred_at or occurred_at.endswith("Z"):
                parsed = occurred_at.replace("Z", "+00:00")
                from datetime import datetime

                moment = datetime.fromisoformat(parsed)
                payload["window"] = window_for_minutes(moment.hour * 60 + moment.minute)
        except Exception:
            occurred_at = self._memory_companion_now_iso()
        source_refs = [_single_line(item, 160) for item in (activity.get("source_refs") or []) if _single_line(item, 160)]
        return await self._memory_companion_record_bot_personal(
            memory_type="bot_observed_activity",
            payload=payload,
            idempotency_key=f"observed:{activity_id}",
            occurred_at=occurred_at,
            version=int(activity.get("version") or 1),
            source_refs=source_refs or [f"companion:observed:{activity_id}"],
        )

    def _memory_companion_bridge_from_module(self, module: Any | None) -> Any | None:
        module_vars = getattr(module, "__dict__", {}) if module is not None else {}
        if not isinstance(module_vars, dict):
            return None
        for getter_name in ("get_active_bridge", "get_memory_companion_bridge"):
            getter = module_vars.get(getter_name)
            if not callable(getter):
                continue
            try:
                bridge = getter()
            except Exception as exc:
                self._memory_companion_optional_dependency_failed(exc, where=getter_name)
                continue
            if bridge is not None:
                return bridge
        return self._memory_companion_bridge_from_object(module)

    @staticmethod
    def _memory_companion_bridge_from_object(candidate: Any | None) -> Any | None:
        if candidate is None:
            return None
        for getter_name in ("get_active_bridge", "get_memory_companion_bridge"):
            getter = getattr(candidate, getter_name, None)
            if not callable(getter):
                continue
            try:
                bridge = getter()
            except Exception:
                continue
            if bridge is not None:
                return bridge
        for attr in ("memory_companion", "memory_companion_bridge", "bridge", "_ACTIVE_BRIDGE"):
            try:
                bridge = getattr(candidate, attr, None)
            except Exception:
                continue
            if bridge is not None:
                return bridge
        return None

    def _memory_companion_probe_capabilities(self, bridge: Any) -> dict[str, Any]:
        try:
            getter = getattr(bridge, "probe_bot_personal_memory_capabilities", None)
        except Exception:
            return self._memory_companion_degraded_status("capability_probe_exception")
        if not callable(getter):
            return self._memory_companion_degraded_status("capability_probe_missing")
        try:
            result = getter()
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="capability_probe"):
                return dict(self._bridge_last_status)
            return self._memory_companion_degraded_status("capability_probe_exception")
        if not isinstance(result, dict):
            return self._memory_companion_degraded_status("capability_probe_invalid")

        expected_windows = list(WINDOW_SLUGS)
        expected_memory_types = list(BOT_PERSONAL_MEMORY_TYPES)
        observed_windows = result.get("windows")
        observed_memory_types = result.get("memory_types")
        observed_domain = result.get("memory_domain", result.get("domain", ""))
        mismatches: list[str] = []
        if result.get("contract_fingerprint") != CONTRACT_FINGERPRINT:
            mismatches.append("contract_fingerprint")
        if result.get("contract_revision") != CONTRACT_REVISION:
            mismatches.append("contract_revision")
        if result.get("capability_schema_version") != BOT_PERSONAL_CAPABILITY_SCHEMA_VERSION:
            mismatches.append("capability_schema_version")
        if result.get("payload_schema_version") != BOT_PERSONAL_PAYLOAD_SCHEMA_VERSION:
            mismatches.append("payload_schema_version")
        if result.get("canonical_schema_version") != BOT_PERSONAL_CANONICAL_SCHEMA_VERSION:
            mismatches.append("canonical_schema_version")
        if observed_domain != BOT_PERSONAL_MEMORY_DOMAIN:
            mismatches.append("memory_domain")
        if observed_windows != expected_windows:
            mismatches.append("windows")
        if observed_memory_types != expected_memory_types:
            mismatches.append("memory_types")
        if result.get("available") is not True:
            mismatches.append("available")
        if mismatches:
            legacy_v2 = (
                result.get("available") is True
                and all(result.get(key) == value for key, value in _LEGACY_V2_CONTRACT.items())
                and observed_domain == BOT_PERSONAL_MEMORY_DOMAIN
                and observed_windows == expected_windows
                and observed_memory_types == expected_memory_types
            )
            if legacy_v2:
                status = dict(result)
                status.update(
                    {
                        "state": "ready_compatible",
                        "degraded": False,
                        "available": True,
                        "contract_compatibility": "legacy_v2",
                        "negotiated_canonical_schema_version": 2,
                        "negotiated_mismatches": tuple(mismatches),
                    }
                )
                self._bridge_last_status = status
                return status
            compatible_superset = (
                set(mismatches).issubset({"contract_fingerprint", "windows", "memory_types"})
                and result.get("contract_revision") == CONTRACT_REVISION
                and result.get("capability_schema_version") == BOT_PERSONAL_CAPABILITY_SCHEMA_VERSION
                and result.get("payload_schema_version") == BOT_PERSONAL_PAYLOAD_SCHEMA_VERSION
                and observed_domain == BOT_PERSONAL_MEMORY_DOMAIN
                and isinstance(observed_windows, (list, tuple))
                and isinstance(observed_memory_types, (list, tuple))
                and set(expected_windows).issubset(set(observed_windows))
                and set(expected_memory_types).issubset(set(observed_memory_types))
                and (
                    len(set(observed_windows)) > len(expected_windows)
                    or len(set(observed_memory_types)) > len(expected_memory_types)
                )
            )
            if compatible_superset:
                status = dict(result)
                status.update(
                    {
                        "state": "ready_compatible",
                        "degraded": False,
                        "available": True,
                        "contract_compatibility": "superset",
                        "negotiated_canonical_schema_version": BOT_PERSONAL_CANONICAL_SCHEMA_VERSION,
                        "negotiated_mismatches": tuple(mismatches),
                    }
                )
                self._bridge_last_status = status
                return status
            return self._memory_companion_degraded_status(
                "capability_contract_mismatch",
                mismatches=tuple(mismatches),
            )

        status = dict(result)
        status.setdefault("state", "ready")
        status.setdefault("degraded", False)
        status.setdefault("available", True)
        status.setdefault("negotiated_canonical_schema_version", BOT_PERSONAL_CANONICAL_SCHEMA_VERSION)
        self._bridge_last_status = status
        return status

    def _memory_companion_probe_namespace_capabilities(self, bridge: Any) -> dict[str, Any]:
        """Negotiate only the REQ-041 scoped API; legacy bridge state is untouched."""
        try:
            getter = getattr(bridge, "probe_namespace_context_capabilities", None)
        except Exception:
            getter = None
        if not callable(getter):
            return {
                "available": False,
                "state": "degraded",
                "code": "namespace_capability_probe_missing",
                "mismatches": ["namespace_capability_probe_missing"],
            }
        try:
            result = getter()
        except Exception:
            return {
                "available": False,
                "state": "degraded",
                "code": "namespace_capability_probe_exception",
                "mismatches": ["namespace_capability_probe_exception"],
            }
        return negotiate_namespace_capability(result)

    @staticmethod
    def _memory_companion_namespace_payload(namespace: Any) -> tuple[dict[str, Any], str]:
        if isinstance(namespace, dict):
            payload = dict(namespace)
        else:
            try:
                serialized = namespace.to_dict()
            except Exception:
                serialized = None
            payload = dict(serialized) if isinstance(serialized, dict) else {}
        errors = validate_namespace_context(payload)
        return payload, errors[0] if errors else ""

    def _memory_companion_bind_namespace_epoch(
        self,
        bridge: Any,
        *,
        operation_id: str,
        migration_epoch: str,
        policy_version: str,
        expected_previous_epoch: str = "",
    ) -> dict[str, Any]:
        operation = str(operation_id or "").strip()
        epoch = str(migration_epoch or "").strip()
        policy = str(policy_version or "").strip()
        previous = str(expected_previous_epoch or "").strip()
        token_pattern = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
        if (
            not token_pattern.fullmatch(operation) or not token_pattern.fullmatch(epoch)
            or not token_pattern.fullmatch(policy) or (previous and not token_pattern.fullmatch(previous))
        ):
            return {"ok": False, "state": "rejected", "code": "namespace_epoch_binding_invalid"}
        capability = self._memory_companion_emotion_producer_capability(bridge)
        if capability is None:
            return {"ok": False, "state": "forbidden", "code": "producer_capability_unavailable"}
        try:
            binder = getattr(bridge, "bind_namespace_migration_epoch", None)
        except Exception:
            binder = None
        if not callable(binder):
            return {"ok": False, "state": "degraded", "code": "namespace_epoch_bind_missing"}
        try:
            result = binder(
                capability,
                operation_id=operation,
                expected_previous_epoch=previous,
                migration_epoch=epoch,
                policy_version=policy,
            )
        except Exception:
            return {"ok": False, "state": "degraded", "code": "namespace_epoch_bind_exception"}
        if not isinstance(result, dict):
            return {"ok": False, "state": "degraded", "code": "namespace_epoch_bind_invalid"}
        return dict(result)

    def _memory_companion_scoped_invoke(
        self,
        bridge: Any,
        method_name: str,
        namespace: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload, error = self._memory_companion_namespace_payload(namespace)
        if error:
            return {"ok": False, "state": "rejected", "code": error}
        negotiated = self._memory_companion_probe_namespace_capabilities(bridge)
        if negotiated.get("available") is not True:
            return {
                "ok": False,
                "state": "degraded",
                "code": str(negotiated.get("code") or "namespace_capability_unavailable")[:120],
            }
        capability = self._memory_companion_emotion_producer_capability(bridge)
        if capability is None:
            return {"ok": False, "state": "forbidden", "code": "producer_capability_unavailable"}
        try:
            method = getattr(bridge, method_name, None)
        except Exception:
            method = None
        if not callable(method):
            return {"ok": False, "state": "degraded", "code": "namespace_scoped_method_missing"}
        try:
            result = method(capability, payload, **kwargs)
        except Exception:
            return {"ok": False, "state": "degraded", "code": "namespace_scoped_call_exception"}
        if not isinstance(result, dict):
            return {"ok": False, "state": "degraded", "code": "namespace_scoped_result_invalid"}
        return dict(result)

    def _memory_companion_upsert_scoped_record(
        self, bridge: Any, namespace: Any, **kwargs: Any
    ) -> dict[str, Any]:
        return self._memory_companion_scoped_invoke(
            bridge, "upsert_scoped_record", namespace, **kwargs
        )

    def _memory_companion_read_scoped_record(
        self, bridge: Any, namespace: Any, **kwargs: Any
    ) -> dict[str, Any]:
        return self._memory_companion_scoped_invoke(
            bridge, "read_scoped_record", namespace, **kwargs
        )

    def _memory_companion_list_scoped_records(
        self, bridge: Any, namespace: Any, **kwargs: Any
    ) -> dict[str, Any]:
        return self._memory_companion_scoped_invoke(
            bridge, "list_scoped_records", namespace, **kwargs
        )

    def _memory_companion_tombstone_scoped_record(
        self, bridge: Any, namespace: Any, **kwargs: Any
    ) -> dict[str, Any]:
        return self._memory_companion_scoped_invoke(
            bridge, "tombstone_scoped_record", namespace, **kwargs
        )

    def _memory_companion_tombstone_scoped_namespace(
        self, bridge: Any, namespace: Any, **kwargs: Any
    ) -> dict[str, Any]:
        return self._memory_companion_scoped_invoke(
            bridge, "tombstone_scoped_namespace", namespace, **kwargs
        )

    def _memory_companion_tombstone_scoped_identity_scopes(
        self, bridge: Any, namespace: Any, **kwargs: Any
    ) -> dict[str, Any]:
        return self._memory_companion_scoped_invoke(
            bridge, "tombstone_scoped_identity_scopes", namespace, **kwargs
        )

    def _memory_companion_erase_scoped_group_scopes(
        self, bridge: Any, namespace: Any, **kwargs: Any
    ) -> dict[str, Any]:
        return self._memory_companion_scoped_invoke(
            bridge, "erase_scoped_group_scopes", namespace, **kwargs
        )

    def _memory_companion_erase_scoped_persona_scopes(
        self, bridge: Any, namespace: Any, **kwargs: Any
    ) -> dict[str, Any]:
        return self._memory_companion_scoped_invoke(
            bridge, "erase_scoped_persona_scopes", namespace, **kwargs
        )

    async def _memory_companion_read_profile(
        self,
        profile: str,
        *,
        query: str = "",
        limit: int = 10,
        current_date: str = "",
        current_window: str = "",
        authorized: bool = False,
    ) -> dict[str, Any]:
        """Select one named Bot Profile without sending storage filters downstream."""

        safe_profile = _single_line(profile, 80)
        base = {
            "ok": False,
            "read_only": True,
            "state": "degraded",
            "degraded": True,
            "pending": True,
            "profile": safe_profile,
            "items": [],
            "warnings": [],
        }
        bridge = self._memory_companion_bridge()
        if bridge is None:
            return {**base, "state": self._bridge_last_status.get("state", "degraded"), "error_code": "bridge_unavailable"}
        getter = getattr(bridge, "read_bot_profile", None)
        if not callable(getter):
            return {**base, "error_code": "profile_method_missing"}
        try:
            capability = self._memory_companion_emotion_producer_capability(bridge)
            if capability is None:
                return {**base, "error_code": "producer_capability_unavailable"}
            result = getter(
                safe_profile,
                query=_single_line(query, 240),
                limit=max(1, min(100, int(limit or 10))),
                current_date=_single_line(current_date, 20),
                current_window=_single_line(current_window, 40),
                authorized=bool(authorized),
                producer_capability=capability,
            )
            if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                result = await result
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="read_profile"):
                return dict(self._bridge_last_status)
            return {**base, "error_code": "profile_bridge_exception"}
        if not isinstance(result, dict):
            return {**base, "error_code": "invalid_profile_response"}
        safe_item_keys = {
            "record_id", "memory_domain", "memory_type", "subject", "date", "window",
            "occurred_at", "source_kind", "source_refs", "evidence_level", "status",
            "version", "summary", "reference",
        }
        safe_items: list[dict[str, Any]] = []
        for item in result.get("items", []) if isinstance(result.get("items"), list) else []:
            if not isinstance(item, dict):
                continue
            safe_items.append({key: item[key] for key in safe_item_keys if key in item})
        self._bridge_last_status = {
            **getattr(self, "_bridge_last_status", {}),
            "last_profile": safe_profile,
        }
        return {
            "ok": bool(result.get("ok", True)),
            "read_only": True,
            "state": _single_line(result.get("state"), 40) or "ready",
            "degraded": bool(result.get("degraded", False)),
            "pending": bool(result.get("pending", False)),
            "profile": _single_line(result.get("profile") or safe_profile, 80),
            "items": safe_items,
            "warnings": [
                _single_line(item, 160)
                for item in (result.get("warnings") or [])
                if _single_line(item, 160)
            ][:8],
        }

    def _memory_companion_coordination_status(self) -> dict[str, Any]:
        bridge = self._memory_companion_bridge()
        if bridge is None:
            return dict(self._bridge_last_status or self._memory_companion_degraded_status("bridge_missing"))
        if self._bridge_last_status.get("reason") in {
            "capability_probe_missing",
            "capability_probe_exception",
            "capability_probe_invalid",
            "capability_contract_mismatch",
        }:
            return dict(self._bridge_last_status)
        try:
            getter = getattr(bridge, "coordination_status", None)
        except Exception:
            return self._memory_companion_degraded_status("bridge_exception")
        if not callable(getter):
            return self._memory_companion_degraded_status("method_missing", method="coordination_status")
        try:
            status = getter()
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="coordination_status"):
                return dict(self._bridge_last_status)
            logger.debug("MemoryCompanion 协同状态读取失败: %s", _single_line(exc, 120))
            return self._memory_companion_degraded_status("bridge_exception", error=_single_line(exc, 120))
        if not isinstance(status, dict):
            return self._memory_companion_degraded_status("invalid_status", status_type=type(status).__name__)
        result = dict(status)
        result.setdefault("available", True)
        result.setdefault("state", "ready")
        result.setdefault("degraded", False)
        # coordination_status is a separate runtime health surface and may
        # omit the contract negotiation fields.  Preserve the negotiated
        # format so the next outbox write does not silently fall back to v2.
        for key in (
            "contract_compatibility",
            "negotiated_canonical_schema_version",
            "negotiated_mismatches",
        ):
            if key in self._bridge_last_status and key not in result:
                result[key] = self._bridge_last_status[key]
        self._bridge_last_status = result
        return result

    def _memory_companion_token_usage_summary(self) -> dict[str, Any]:
        bridge = self._memory_companion_bridge()
        if bridge is None:
            return {"available": False, "display_name": "我会牢牢记住你", "reason": "未检测到运行中的记忆插件"}
        getter = getattr(bridge, "get_token_usage_summary", None)
        if not callable(getter):
            return {"available": False, "display_name": "我会牢牢记住你", "reason": "当前记忆插件版本暂未暴露 Token 统计"}
        try:
            usage = getter()
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="token_usage"):
                return {"available": False, "display_name": "我会牢牢记住你", "reason": f"缺少可选依赖 {self._bridge_dependency_failure_module}"}
            logger.debug("记忆插件 Token 统计读取失败: %s", _single_line(exc, 120))
            return {"available": False, "display_name": "我会牢牢记住你", "reason": _single_line(exc, 120)}
        if not isinstance(usage, dict):
            return {"available": False, "display_name": "我会牢牢记住你", "reason": "记忆插件返回的 Token 统计格式无效"}
        usage.setdefault("available", True)
        usage.setdefault("display_name", "我会牢牢记住你")
        usage.setdefault("counted_in_private_companion_budget", False)
        return usage

    def _memory_companion_mark_deferred_section(
        self,
        section: str,
        event: Any | None = None,
        req: Any | None = None,
    ) -> None:
        normalized = _single_line(section, 80)
        if not normalized:
            return
        for target in (event, req):
            if target is None:
                continue
            try:
                existing = getattr(target, "memory_companion_companion_deferred_sections", None)
                if isinstance(existing, set):
                    sections = set(existing)
                elif isinstance(existing, (list, tuple)):
                    sections = {_single_line(item, 80) for item in existing if _single_line(item, 80)}
                elif isinstance(existing, str):
                    sections = {_single_line(item, 80) for item in existing.split(",") if _single_line(item, 80)}
                else:
                    sections = set()
                sections.add(normalized)
                setattr(target, "memory_companion_companion_deferred_sections", sections)
            except Exception:
                pass

    def _memory_companion_should_defer_prompt_section(
        self,
        section: str,
        event: Any | None = None,
        req: Any | None = None,
    ) -> bool:
        bridge = self._memory_companion_bridge()
        checker = getattr(bridge, "should_defer_private_companion_section", None) if bridge is not None else None
        if not callable(checker):
            return False
        try:
            should_defer = bool(checker(section))
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="should_defer"):
                return False
            logger.debug("MemoryCompanion 协同状态读取失败: %s", _single_line(exc, 120))
            return False
        if should_defer:
            self._memory_companion_mark_deferred_section(section, event, req)
            logger.info("MemoryCompanion 已接管提示词片段，跳过本地注入: section=%s", _single_line(section, 80))
        return should_defer

    def _memory_companion_bot_emotional_state(self) -> tuple[str, float]:
        """Extract bot's current mood and energy from daily_state for memory context sharing."""
        try:
            state = self.data.get("daily_state", {})
            if not isinstance(state, dict):
                return "", 0.0
            mood = _single_line(state.get("mood_bias"), 40)
            try:
                energy = float(state.get("energy", 0) or 0)
            except Exception:
                energy = 0.0
            return mood, energy
        except Exception:
            return "", 0.0

    def _memory_companion_current_agenda_item(self) -> dict[str, Any] | None:
        """Return only a disclosed Bot current fact/runtime state."""

        getter = getattr(self, "_agenda_current_context_item", None)
        if callable(getter):
            try:
                item = getter()
            except Exception:
                return None
            return item if isinstance(item, dict) else None
        # Compatibility for isolated legacy harnesses.  Production instances
        # always expose the policy/runtime accessor above.
        legacy_getter = getattr(self, "_get_current_plan_item", None)
        try:
            item = legacy_getter(self.data.get("daily_plan", {})) if callable(legacy_getter) else None
        except Exception:
            item = None
        return item if isinstance(item, dict) else None

    def _memory_companion_build_private_context(
        self,
        *,
        user_id: str,
        user: dict[str, Any],
        text: str,
        event: Any | None = None,
    ) -> dict[str, Any]:
        role = ""
        role_getter = getattr(self, "_private_user_role", None)
        if callable(role_getter):
            try:
                role = _single_line(role_getter(user, user_id), 40)
            except TypeError:
                try:
                    role = _single_line(role_getter(user), 40)
                except Exception:
                    role = ""
            except Exception:
                role = ""
        current_item = self._memory_companion_current_agenda_item()
        schedule_text = ""
        if isinstance(current_item, dict):
            try:
                schedule_text = _single_line(self._format_plan_item_for_prompt(current_item), 180)
            except Exception:
                schedule_text = _single_line(current_item.get("activity") or current_item.get("text"), 180)
        relationship = ""
        try:
            relationship = _single_line(self._format_relationship_summary(user), 220)
        except Exception:
            relationship = ""
        entities = [
            _single_line(user.get("nickname") or user.get("display_name") or user_id, 80),
            _single_line(user_id, 80),
        ]
        worldbook_mentions = ""
        formatter = getattr(self, "_format_worldbook_private_mentions_for_prompt", None)
        if callable(formatter) and text:
            try:
                worldbook_mentions = _single_line(formatter(text, limit=4), 240)
            except Exception:
                worldbook_mentions = ""
        facts = [
            f"当前私聊用户角色：{role}" if role else "",
            f"关系摘要：{relationship}" if relationship else "",
            f"最近主动消息：{_single_line(user.get('last_proactive_message'), 180)}" if user.get("last_proactive_message") else "",
            f"关系网命中：{worldbook_mentions}" if worldbook_mentions else "",
        ]
        keywords = [
            _single_line(user.get("planned_proactive_topic"), 80),
            _single_line(user.get("planned_proactive_reason"), 80),
            _single_line(user.get("last_proactive_reason"), 80),
        ]
        payload = {
            "source": "private_companion",
            "scope": "private",
            "topic": _single_line(user.get("planned_proactive_topic") or user.get("last_proactive_reason") or text, 120),
            "intent": _single_line(user.get("planned_proactive_semantic_kind") or user.get("last_proactive_action") or "private_reply", 80),
            "entities": [item for item in entities if item],
            "facts": [item for item in facts if item],
            "keywords": [item for item in keywords if item],
            "motive": _single_line(user.get("planned_proactive_motive") or user.get("last_proactive_motive"), 160),
            "schedule": schedule_text,
            "private_user_role": role,
            "user_id": _single_line(user_id, 80),
            "session_id": _single_line(getattr(event, "unified_msg_origin", "") if event is not None else user.get("umo"), 180),
            "topic_fit_policy": "旧话题、未完成话头和长期记忆只在贴合当前用户消息、用户主动回问，或能一句轻轻带过时使用；不贴就先放着，不必改变本轮话题。",
        }
        # Attach bot emotional state for memory plugin to calibrate injection tone
        bot_mood, bot_energy = self._memory_companion_bot_emotional_state()
        if bot_mood:
            payload["mood_bias"] = bot_mood
        if bot_energy > 0:
            payload["energy"] = bot_energy
        return {key: value for key, value in payload.items() if value not in ("", [], {}, None)}

    def _memory_companion_schedule_owner_context(self) -> tuple[str, dict[str, Any]]:
        users = self.data.get("users", {})
        if not isinstance(users, dict):
            return "", {}
        owner_checker = getattr(self, "_is_private_companion_owner_user_id", None)
        if not callable(owner_checker):
            return "", {}
        for raw_id, raw_user in users.items():
            if not isinstance(raw_user, dict):
                continue
            user_id = str(raw_id or "").strip()
            if not user_id:
                continue
            if not bool(raw_user.get("enabled", True)):
                continue
            try:
                if owner_checker(user_id):
                    return user_id, raw_user
            except Exception:
                continue
        return "", {}

    def _memory_companion_bridge_bot_id(self, event: Any | None = None) -> str:
        if event is not None:
            event_self_id = getattr(self, "_event_self_id", None)
            if callable(event_self_id):
                try:
                    bot_id = _single_line(event_self_id(event), 120)
                except Exception:
                    bot_id = ""
                if bot_id:
                    return bot_id
        known_ids: set[str] = set()
        known_getter = getattr(self, "_known_bot_self_ids", None)
        if callable(known_getter):
            try:
                known_ids.update(
                    _single_line(value, 120)
                    for value in known_getter()
                    if _single_line(value, 120)
                )
            except Exception:
                pass
        for attr in ("bot_self_id", "bot_user_id", "self_id"):
            value = _single_line(getattr(self, attr, ""), 120)
            if value:
                known_ids.add(value)
        return next(iter(known_ids)) if len(known_ids) == 1 else ""

    def _memory_companion_archive_persona_id(self) -> str:
        """Return the stable persona namespace used by Memory Companion v3."""
        getter = getattr(self, "_effective_plugin_persona_id", None)
        if callable(getter):
            try:
                value = _single_line(getter(), 96)
            except Exception:
                value = ""
            if value:
                return value
        primary_getter = getattr(self, "_primary_persona_id", None)
        if callable(primary_getter):
            try:
                value = _single_line(primary_getter(), 96)
            except Exception:
                value = ""
            if value:
                return value
        value = _single_line(getattr(self, "plugin_specific_persona_id", ""), 96)
        if value:
            return value
        # Single-persona installs still need a non-empty namespace for v3.
        return "default"

    def _memory_companion_p5_gate_kwargs(self, *, event: Any | None = None, sink: str) -> dict[str, Any]:
        """Mint a fresh opaque handle for one Bridge call when P5 is enabled."""
        if not bool(getattr(self, "enable_p5_source_observer", False)):
            return {}
        issuer = getattr(event, "private_companion_p5_issue_attestation", None) if event is not None else None
        if not callable(issuer):
            issuer = getattr(self, "_p5_issue_attestation_for_event", None)
            if callable(issuer):
                try:
                    issued = issuer(
                        event=event,
                        request=getattr(event, "private_companion_p5_request_carrier", None) if event is not None else None,
                        sink=sink,
                    )
                except Exception:
                    issued = None
            else:
                issued = None
        else:
            try:
                issued = issuer(sink)
            except Exception:
                issued = None
        if not isinstance(issued, tuple) or len(issued) != 2:
            return {}
        handle, consumer = issued
        if handle is None or not callable(consumer):
            return {}
        return {
            "p5_attestation": handle,
            "p5_attestation_consumer": consumer,
        }

    def _memory_companion_schedule_session_context(self, *, message_text: str = "") -> dict[str, Any]:
        user_id, user = self._memory_companion_schedule_owner_context()
        umo = _single_line(user.get("umo"), 200) if isinstance(user, dict) else ""
        platform = umo.split(":", 1)[0] if ":" in umo else ""
        user_name = _single_line(
            (user.get("nickname") or user.get("display_name") or user_id) if isinstance(user, dict) else user_id,
            80,
        )
        preferred_address = _single_line(user.get("nickname"), 24) if isinstance(user, dict) else ""
        return {
            "session_id": umo or f"private_companion:schedule:{user_id or 'bot_self'}",
            "scope": "private" if user_id else "unknown",
            "platform": platform,
            "user_id": user_id,
            "user_name": user_name,
            "preferred_address": preferred_address,
            "preferred_address_locked": bool(preferred_address),
            "bot_id": self._memory_companion_bridge_bot_id(),
            "message_text": _single_line(message_text, 1200),
        }

    async def _memory_companion_compose_schedule_context(
        self,
        *,
        kind: str = "daily_plan",
        segment: dict[str, Any] | None = None,
        plan: dict[str, Any] | None = None,
        state: dict[str, Any] | None = None,
        max_chars: int = 1200,
    ) -> str:
        bridge = self._memory_companion_bridge()
        composer = getattr(bridge, "compose_context", None) if bridge is not None else None
        if not callable(composer):
            return ""
        now_text = ""
        try:
            now_text = self._environment_now().strftime("%Y-%m-%d %H:%M")
        except Exception:
            now_text = ""
        query_parts = [
            "Private Companion 日程连续性",
            "Bot 自我时间线",
            "最近主动消息",
            "最近阅读 创作 搜索 生图 QQ空间 说说 行动",
            "最近吃了什么 今日穿搭 梦境碎片 主动私聊",
            "刚刚发布的 QQ 空间说说 最近已发说说 公开动态余味 不要重复已发说说",
            "主要用户明确偏好 约定 边界",
            "避免把次要用户互动写进 Bot 日程",
        ]
        if now_text:
            query_parts.append(f"当前时间 {now_text}")
        if isinstance(segment, dict):
            item = segment.get("item") if isinstance(segment.get("item"), dict) else {}
            if isinstance(item, dict):
                query_parts.extend(
                    [
                        _single_line(item.get("time"), 40),
                        _single_line(item.get("activity"), 180),
                        _single_line(item.get("message_seed"), 120),
                    ]
                )
        if isinstance(plan, dict):
            query_parts.append(_single_line(plan.get("date"), 40))
        if isinstance(state, dict):
            query_parts.append(_single_line(state.get("summary") or state.get("mood") or state.get("emotion"), 160))
        query = _single_line(" ".join(part for part in query_parts if _single_line(part, 240)), 1400)
        if not query:
            return ""
        try:
            bot_mood, bot_energy = self._memory_companion_bot_emotional_state()
            timeout = max(0.2, min(6.0, _memory_companion_safe_float(getattr(self, "memory_companion_context_timeout_seconds", 1.2), 1.2, 0.2)))
            compose_kwargs = {
                "query": query,
                "session_context": self._memory_companion_schedule_session_context(message_text=query),
                "top_k": 6 if kind == "daily_plan" else 5,
                "max_chars": max(500, min(1800, int(max_chars or 1200))),
                "companion_bot_mood": bot_mood,
                "companion_bot_energy": bot_energy,
            }
            compose_kwargs.update(self._memory_companion_p5_gate_kwargs(event=None, sink="bridge_serialization"))
            if self._memory_companion_coordination_status().get("schedule_fast_context") is True:
                compose_kwargs["retrieval_profile"] = "schedule_fast"
            text = await asyncio.wait_for(
                composer(**compose_kwargs),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "MemoryCompanion 日程上下文读取超时,已跳过: kind=%s timeout=%.2fs",
                _single_line(kind, 60),
                max(0.2, min(6.0, _memory_companion_safe_float(getattr(self, "memory_companion_context_timeout_seconds", 1.2), 1.2, 0.2))),
            )
            return ""
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="compose_schedule_context"):
                return ""
            logger.debug("MemoryCompanion 日程上下文读取失败: %s", _single_line(exc, 120))
            return ""
        text = str(text or "").strip()
        if not text:
            return ""
        text = self._memory_companion_filter_internal_error_context(text)
        if not text:
            return ""
        if "没有检索到足够相关的长期记忆" in text and text.count("\n- ") <= 1:
            return ""
        relationship_sanitizer = getattr(self, "_sanitize_generation_relationship_context", None)
        if callable(relationship_sanitizer):
            try:
                text = relationship_sanitizer(
                    text,
                    source=f"memory_companion.schedule.{kind}",
                )
            except Exception:
                pass
        return text[: max(300, int(max_chars or 1200))] if text else ""

    async def _memory_companion_compose_feature_context(
        self,
        *,
        kind: str,
        query: str,
        user: dict[str, Any] | None = None,
        user_id: str = "",
        event: Any | None = None,
        top_k: int = 5,
        max_chars: int = 900,
        timeout_seconds: float = 4.0,
        strict_session_only: bool = False,
    ) -> str:
        if not getattr(self, "enable_memory_companion_feature_context", True):
            return ""
        bridge = self._memory_companion_bridge()
        composer = getattr(bridge, "compose_context", None) if bridge is not None else None
        if not callable(composer):
            return ""
        # Apply configured defaults if caller didn't override
        configured_top_k = getattr(self, "memory_companion_context_top_k", 5)
        configured_max_chars = getattr(self, "memory_companion_context_max_chars", 900)
        if top_k == 5:
            top_k = configured_top_k
        if max_chars == 900:
            max_chars = configured_max_chars
        clean_query = _single_line(query, 1200)
        if not clean_query:
            return ""
        if (
            kind in {"daily_outfit_photo", "daily_diary"}
            and event is None
            and not user_id
            and not isinstance(user, dict)
        ):
            owner_getter = getattr(self, "_memory_companion_schedule_owner_context", None)
            if callable(owner_getter):
                try:
                    owner_id, owner = owner_getter()
                    if owner_id and isinstance(owner, dict):
                        user_id = _single_line(owner_id, 80)
                        user = owner
                except Exception:
                    pass
        session_context: dict[str, Any]
        if event is not None:
            session_id = _single_line(getattr(event, "unified_msg_origin", ""), 180)
            scope = "unknown"
            try:
                scope = "private" if bool(getattr(event, "is_private_chat", lambda: False)()) else "group"
            except Exception:
                scope = "unknown"
            if not user_id:
                try:
                    user_id = _single_line(event.get_sender_id(), 80)
                except Exception:
                    user_id = ""
            user_name = ""
            try:
                user_name = _single_line(self._sender_display_name(event), 80)
            except Exception:
                user_name = ""
            preferred_address = _single_line(user.get("nickname"), 24) if isinstance(user, dict) else ""
            session_context = {
                "session_id": session_id,
                "scope": scope,
                "platform": session_id.split(":", 1)[0] if ":" in session_id else "",
                "user_id": user_id,
                "user_name": user_name,
                "preferred_address": preferred_address,
                "preferred_address_locked": bool(preferred_address),
                "bot_id": self._memory_companion_bridge_bot_id(event),
                "message_text": clean_query,
                "strict_session_only": bool(strict_session_only),
                "topic_fit_policy": "旧话题和未完成话头只作可选参考；和当前问题不贴时先放着，不必为了兑现它改变本轮话题。",
            }
        elif isinstance(user, dict):
            umo = _single_line(user.get("umo"), 200)
            preferred_address = _single_line(user.get("nickname"), 24)
            if not user_id and not umo:
                # 无用户标识：无法在 memory 插件侧隔离会话作用域，宁可召回为空也不跨用户串线。
                return ""
            session_context = {
                "session_id": umo or f"private_companion:{kind}:{user_id or 'unknown'}",
                "scope": "private" if user_id else "unknown",
                "platform": umo.split(":", 1)[0] if ":" in umo else "",
                "user_id": user_id,
                "user_name": _single_line(user.get("nickname") or user.get("display_name") or user_id, 80),
                "preferred_address": preferred_address,
                "preferred_address_locked": bool(preferred_address),
                "bot_id": self._memory_companion_bridge_bot_id(),
                "message_text": clean_query,
                "strict_session_only": bool(strict_session_only),
                "topic_fit_policy": "旧话题和未完成话头只作可选参考；和当前问题不贴时先放着，不必为了兑现它改变本轮话题。",
            }
        else:
            # 无 event 且无 user：无法确定当前对话用户，fail-closed，宁可召回为空也不跨用户串线。
            return ""
        try:
            bot_mood, bot_energy = self._memory_companion_bot_emotional_state()
            configured_timeout = max(0.2, min(6.0, _memory_companion_safe_float(getattr(self, "memory_companion_context_timeout_seconds", 1.2), 1.2, 0.2)))
            compose_kwargs = {
                "query": clean_query,
                "session_context": session_context,
                "top_k": max(1, min(10, int(top_k or 5))),
                "max_chars": max(240, min(1800, int(max_chars or 900))),
                "companion_bot_mood": bot_mood,
                "companion_bot_energy": bot_energy,
            }
            compose_kwargs.update(self._memory_companion_p5_gate_kwargs(event=event, sink="bridge_serialization"))
            if (
                kind == "daily_outfit_photo"
                and self._memory_companion_coordination_status().get("outfit_fast_context") is True
            ):
                compose_kwargs["retrieval_profile"] = "outfit_fast"
            text = await asyncio.wait_for(
                composer(**compose_kwargs),
                timeout=max(0.2, min(6.0, min(configured_timeout, _memory_companion_safe_float(timeout_seconds, configured_timeout, 0.2)))),
            )
        except asyncio.TimeoutError:
            logger.warning(
                "MemoryCompanion 功能上下文读取超时,已跳过: kind=%s timeout=%.2fs",
                _single_line(kind, 60),
                max(0.2, min(6.0, min(_memory_companion_safe_float(getattr(self, "memory_companion_context_timeout_seconds", 1.2), 1.2, 0.2), _memory_companion_safe_float(timeout_seconds, 1.2, 0.2)))),
            )
            return ""
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where=f"compose_feature_context:{kind}"):
                return ""
            logger.debug("MemoryCompanion 功能上下文读取失败: kind=%s err=%s", _single_line(kind, 60), _single_line(exc, 120))
            return ""
        text = str(text or "").strip()
        if not text:
            return ""
        text = self._memory_companion_filter_internal_error_context(text)
        if not text:
            return ""
        if "没有检索到足够相关的长期记忆" in text and text.count("\n- ") <= 1:
            return ""
        generation_kinds = {
            "current_state_reply",
            "daily_diary",
            "daily_outfit_photo",
            "natural_photo",
            "command_photo",
        }
        should_filter_relationships = (
            kind in generation_kinds
            or kind.startswith("proactive_")
            or kind.startswith("qzone_")
            or kind.startswith("creative_")
        )
        relationship_sanitizer = getattr(self, "_sanitize_generation_relationship_context", None)
        if should_filter_relationships and callable(relationship_sanitizer):
            try:
                text = relationship_sanitizer(
                    text,
                    source=f"memory_companion.feature.{kind}",
                )
            except Exception:
                pass
        return text[: max(240, min(1800, int(max_chars or 900)))]

    @staticmethod
    def _memory_companion_private_recall_needed(text: Any) -> bool:
        cleaned = _single_line(text, 280)
        if len(cleaned) < 2:
            return False
        cues = (
            "还记得",
            "记得我",
            "以前",
            "之前",
            "上次",
            "前阵子",
            "说过",
            "提过",
            "答应",
            "约定",
            "承诺",
            "习惯",
            "偏好",
            "喜欢",
            "讨厌",
            "雷点",
            "别叫",
            "怎么称呼",
            "我叫什么",
            "我生日",
        )
        return any(cue in cleaned for cue in cues)

    async def _memory_companion_compose_private_recall(
        self,
        *,
        event: Any,
        user: dict[str, Any],
        user_id: str,
        text: str,
        as_section: bool = False,
    ) -> str | dict[str, Any]:
        """Return a small, current-session-only memory supplement for private replies."""
        if not getattr(self, "enable_memory_companion_private_recall", True):
            return ""
        if not self._memory_companion_private_recall_needed(text):
            return ""
        query = _single_line(
            "当前私聊用户正在说："
            f"{_single_line(text, 260)}。"
            "只检索当前私聊会话中与本轮直接相关的明确约定、称呼、边界或稳定偏好；"
            "最多保留 3 条，没有可靠依据则返回空。"
            "禁止引用其他私聊、群聊、公开动态或其他人的信息。",
            700,
        )
        try:
            recalled = await self._memory_companion_compose_feature_context(
                kind="private_turn_recall",
                query=query,
                user=user,
                user_id=user_id,
                event=event,
                top_k=3,
                max_chars=min(620, max(240, int(getattr(self, "memory_companion_context_max_chars", 900) or 900))),
                timeout_seconds=min(1.2, _memory_companion_safe_float(getattr(self, "memory_companion_context_timeout_seconds", 1.2), 1.2, 0.2)),
                strict_session_only=True,
            )
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="compose_private_recall"):
                return ""
            logger.debug("MemoryCompanion 私聊选择性召回失败: %s", _single_line(exc, 120))
            return ""
        recalled = _single_line(recalled, 620)
        if not recalled:
            return ""
        body = (
            f"{recalled}\n"
            "只在与本轮直接相关时自然接住；不要主动列举记忆、不要提及检索过程，也不要把它当作其他用户的信息。"
        )
        return prompt_section("当前私聊长期记忆补充", body) if as_section else f"【当前私聊长期记忆补充】\n{body}"

    def _memory_companion_agenda_memory_write_entries(
        self,
        *,
        date_text: str = "",
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Return only entries admitted by the canonical memory-write view."""

        getter = getattr(self, "_agenda_disclosure_view", None)
        if not callable(getter):
            return []
        current = now
        if current is None:
            try:
                current = self._environment_now()
            except Exception:
                current = datetime.now().astimezone()
        try:
            try:
                view = getter(
                    "memory_write",
                    now=current,
                    max_entries=256,
                    date_key=_single_line(date_text, 20),
                )
            except TypeError:
                view = getter("memory_write", now=current, max_entries=256)
        except Exception:
            return []
        entries = getattr(view, "entries", None)
        if entries is None and isinstance(view, dict):
            entries = view.get("entries")
        return [dict(item) for item in entries if isinstance(item, dict)] if isinstance(entries, list) else []

    @staticmethod
    def _memory_companion_entry_ids(entries: list[dict[str, Any]]) -> set[str]:
        ids: set[str] = set()
        for item in entries:
            for key in ("entry_id", "plan_id", "activity_id", "event_id"):
                value = _single_line(item.get(key), 160)
                if value:
                    ids.add(value)
        return ids

    async def _memory_companion_record_agenda_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(snapshot, dict):
            return {"ok": False, "state": "invalid", "error_code": "invalid_snapshot"}
        date_text = _single_line(snapshot.get("window_date") or snapshot.get("date"), 20)
        window = _single_line(snapshot.get("window") or snapshot.get("slug"), 32)
        snapshot_id = _single_line(snapshot.get("snapshot_id"), 160) or f"agenda_snapshot:{date_text}:{window}"
        allowed_entries = self._memory_companion_agenda_memory_write_entries(
            date_text=date_text,
        )
        allowed_ids = self._memory_companion_entry_ids(allowed_entries)
        if not allowed_ids:
            return {
                "ok": False,
                "state": "filtered",
                "error_code": "memory_write_filtered",
                "idempotency_key": snapshot_id,
            }

        def _compact(items: Any, field: str) -> list[dict[str, Any]]:
            result: list[dict[str, Any]] = []
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict):
                    continue
                item_ids = {
                    _single_line(item.get(key), 160)
                    for key in ("entry_id", "plan_id", "activity_id", "event_id")
                    if _single_line(item.get(key), 160)
                }
                if not item_ids.intersection(allowed_ids):
                    continue
                value = _single_line(item.get("title") or item.get("summary") or item.get(field), 180)
                if not value:
                    continue
                result.append({
                    "id": _single_line(item.get("plan_id") or item.get("activity_id") or item.get("entry_id"), 120),
                    "summary": value,
                    "status": _single_line(item.get("status"), 32),
                })
            return result[:16]

        planned = _compact(snapshot.get("planned"), "title")
        observed = _compact(snapshot.get("observed"), "summary")
        reconciled = _compact(snapshot.get("reconciled"), "reason")
        if not planned and not observed and not reconciled:
            return {
                "ok": False,
                "state": "filtered",
                "error_code": "memory_write_entries_not_in_snapshot",
                "idempotency_key": snapshot_id,
            }
        payload = {
            "date": date_text,
            "window": window,
            "summary": f"{date_text} {window} 窗口快照",
            "planned": planned,
            "observed": observed,
            "reconciled": reconciled,
            "open_items": [_single_line(item, 160) for item in (snapshot.get("open_items") or []) if _single_line(item, 160)][:12],
            "memory_write_entry_ids": sorted(allowed_ids)[:32],
        }
        refs = [snapshot_id]
        refs.extend(
            _single_line(item, 160)
            for item in (snapshot.get("source_refs") or [])
            if _single_line(item, 160) in allowed_ids
        )
        for item in allowed_entries:
            raw_refs = item.get("source_refs")
            if isinstance(raw_refs, str):
                raw_refs = [raw_refs]
            for ref in raw_refs if isinstance(raw_refs, (list, tuple, set)) else []:
                safe_ref = _single_line(ref, 160)
                if safe_ref and safe_ref not in refs:
                    refs.append(safe_ref)
        return await self._memory_companion_record_bot_personal(
            memory_type="bot_window_snapshot",
            payload=payload,
            idempotency_key=snapshot_id,
            occurred_at=_single_line(snapshot.get("generated_at"), 80) or self._memory_companion_now_iso(),
            version=int(snapshot.get("version") or 1),
            source_refs=list(dict.fromkeys(refs)),
        )

    async def _memory_companion_record_agenda_reconciliation(self, reconciliation: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(reconciliation, dict):
            return {"ok": False, "state": "invalid", "error_code": "invalid_reconciliation"}
        date_text = _single_line(reconciliation.get("window_date") or reconciliation.get("date"), 20)
        window = _single_line(reconciliation.get("window") or reconciliation.get("slug"), 32)
        record_id = _single_line(reconciliation.get("reconciliation_id"), 160) or f"reconciliation:{date_text}:{window}"
        allowed_entries = self._memory_companion_agenda_memory_write_entries(
            date_text=date_text,
        )
        allowed_ids = self._memory_companion_entry_ids(allowed_entries)
        if not allowed_ids:
            return {
                "ok": False,
                "state": "filtered",
                "error_code": "memory_write_filtered",
                "idempotency_key": record_id,
            }
        plans = []
        for item in reconciliation.get("plans") if isinstance(reconciliation.get("plans"), list) else []:
            if not isinstance(item, dict):
                continue
            item_ids = {
                _single_line(item.get(key), 160)
                for key in ("entry_id", "plan_id", "activity_id", "event_id")
                if _single_line(item.get(key), 160)
            }
            activity_ids = {
                _single_line(value, 160)
                for value in (item.get("activity_ids") or item.get("reconciled_activity_ids") or [])
                if _single_line(value, 160)
            }
            if not (item_ids | activity_ids).intersection(allowed_ids):
                continue
            plans.append({
                "plan_id": _single_line(item.get("plan_id"), 120),
                "status": _single_line(item.get("status"), 32),
                "reason": _single_line(item.get("reason") or item.get("reconciliation_reason"), 180),
                "activity_ids": [_single_line(value, 120) for value in (item.get("activity_ids") or item.get("reconciled_activity_ids") or []) if _single_line(value, 120)][:12],
            })
        observed_activity_ids = [
            _single_line(value, 120)
            for value in (reconciliation.get("observed_activity_ids") or [])
            if _single_line(value, 120) in allowed_ids
        ]
        if not plans and not observed_activity_ids:
            return {
                "ok": False,
                "state": "filtered",
                "error_code": "memory_write_entries_not_in_reconciliation",
                "idempotency_key": record_id,
            }
        payload = {
            "date": date_text,
            "window": window,
            "summary": f"{date_text} {window} 计划与实际对账",
            "plans": plans[:16],
            "observed_activity_ids": observed_activity_ids[:16],
            "memory_write_entry_ids": sorted(allowed_ids)[:32],
        }
        refs = [record_id]
        refs.extend(
            _single_line(item, 160)
            for item in (reconciliation.get("source_refs") or [])
            if _single_line(item, 160) in allowed_ids
        )
        for item in allowed_entries:
            raw_refs = item.get("source_refs")
            if isinstance(raw_refs, str):
                raw_refs = [raw_refs]
            for ref in raw_refs if isinstance(raw_refs, (list, tuple, set)) else []:
                safe_ref = _single_line(ref, 160)
                if safe_ref and safe_ref not in refs:
                    refs.append(safe_ref)
        return await self._memory_companion_record_bot_personal(
            memory_type="bot_schedule_reconciliation",
            payload=payload,
            idempotency_key=record_id,
            occurred_at=_single_line(reconciliation.get("generated_at"), 80) or self._memory_companion_now_iso(),
            version=int(reconciliation.get("version") or 1),
            source_refs=list(dict.fromkeys(refs)),
        )

    async def _memory_companion_record_daily_diary(self, diary: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(diary, dict):
            return {"ok": False, "state": "invalid", "error_code": "invalid_diary"}
        date_text = _single_line(diary.get("date"), 20)
        summary = _single_line(diary.get("summary") or diary.get("share_seed") or diary.get("body"), 360)
        if not date_text or not summary:
            return {"ok": False, "state": "invalid", "error_code": "empty_diary"}
        payload = {
            "date": date_text,
            "summary": summary,
            "mood": _single_line(diary.get("mood") or diary.get("emotion"), 60),
            "tags": [_single_line(item, 60) for item in (diary.get("tags") or []) if _single_line(item, 60)][:12],
            "dream_summary": _single_line(diary.get("dream_summary") or diary.get("dream"), 160),
        }
        revision = self._memory_companion_archive_revision(
            memory_type="bot_daily_diary",
            local_date=date_text,
            business_payload=payload,
        )
        diary["version"] = revision
        result = await self._memory_companion_record_bot_personal(
            memory_type="bot_daily_diary",
            payload=payload,
            idempotency_key=f"diary:{date_text}",
            occurred_at=self._memory_companion_now_iso(),
            version=revision,
            source_refs=[f"companion:diary:{date_text}"],
        )
        diary["memory_archive"] = dict(result)
        return result

    async def _memory_companion_record_daily_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(plan, dict):
            return {"ok": False, "state": "invalid", "error_code": "invalid_plan"}
        date_text = _single_line(plan.get("date"), 40)
        items = plan.get("items")
        if not date_text or not isinstance(items, list) or not items:
            return {"ok": False, "state": "invalid", "error_code": "empty_plan"}
        lines: list[str] = []
        for item in items[:16]:
            if not isinstance(item, dict):
                continue
            line = _single_line(
                " ".join(
                    part
                    for part in [
                        _single_line(item.get("time"), 12),
                        _single_line(item.get("activity"), 180),
                        f"情绪:{_single_line(item.get('mood'), 40)}" if _single_line(item.get("mood"), 40) else "",
                        f"可分享:{_single_line(item.get('message_seed'), 80)}" if _single_line(item.get("message_seed"), 80) else "",
                    ]
                    if part
                ),
                260,
            )
            if line:
                lines.append(line)
        if not lines:
            return {"ok": False, "state": "invalid", "error_code": "empty_plan"}
        try:
            now = self._environment_now()
            window = window_for_minutes(now.hour * 60 + now.minute)
        except Exception:
            window = ""
        payload = {
            "date": date_text,
            "window": window,
            "summary": f"{date_text} 的 Bot 当日生活日程已生成",
            "items": lines,
            "source": _single_line(plan.get("source"), 40),
            "item_count": len(lines),
            "subject_actor_id": "bot_self",
            "actor_type": "bot",
            "content_granularity": "day",
            "materialization_state": "candidate",
            "fact_eligibility": "none",
            "expires_at": (datetime.now().astimezone() + timedelta(hours=24)).isoformat(timespec="seconds"),
            "legacy_flags": ["short_ttl_plan", "unverified_plan"],
        }
        revision = self._memory_companion_archive_revision(
            memory_type="bot_schedule_plan",
            local_date=date_text,
            business_payload={"date": date_text, "items": lines},
        )
        plan["version"] = revision
        result = await self._memory_companion_record_bot_personal(
            memory_type="bot_schedule_plan",
            payload=payload,
            idempotency_key=f"daily_plan:{date_text}",
            occurred_at=_single_line(plan.get("generated_at"), 80) or self._memory_companion_now_iso(),
            version=revision,
            source_refs=[f"companion:daily_plan:{date_text}"],
        )
        plan["memory_archive"] = dict(result)
        return result

    async def _memory_companion_record_detail_enhancement(
        self,
        *,
        segment: dict[str, Any],
        plan: dict[str, Any],
        detail: dict[str, Any],
    ) -> None:
        if not isinstance(segment, dict) or not isinstance(detail, dict):
            return
        date_text = _single_line(plan.get("date") if isinstance(plan, dict) else "", 40)
        start = 0
        end = 0
        try:
            start = int(segment.get("start") or 0)
            end = int(segment.get("end") or 0)
        except Exception:
            start, end = 0, 0
        if not date_text or start < 0:
            return
        try:
            start_text = self._minutes_to_hhmm(start)
            end_text = self._minutes_to_hhmm(end)
        except Exception:
            start_text = str(start)
            end_text = str(end or "")
        summary = _single_line(detail.get("summary"), 180)
        events = []
        for item in detail.get("today_events") if isinstance(detail.get("today_events"), list) else []:
            if isinstance(item, dict):
                text = _single_line(item.get("event"), 160)
                if text:
                    events.append(text)
        proactive = []
        for item in detail.get("proactive_events") if isinstance(detail.get("proactive_events"), list) else []:
            if isinstance(item, dict):
                text = _single_line(
                    item.get("topic") or item.get("motive") or item.get("why") or item.get("reason"),
                    100,
                )
                if text:
                    proactive.append(text)
        if not summary and not events and not proactive:
            return
        await self._memory_companion_record_bot_personal(
            memory_type="bot_detail_fragment",
            payload={
                "date": date_text,
                "window": window_for_minutes(start % (24 * 60)),
                "summary": summary or "日程细化",
                "events": events[:4],
                "proactive_events": proactive[:3],
                "start": start_text,
                "end": end_text,
                "subject_actor_id": "bot_self",
                "actor_type": "bot",
                "content_granularity": "scene",
                "materialization_state": "candidate",
                "fact_eligibility": "none",
                "expires_at": (datetime.now().astimezone() + timedelta(hours=2)).isoformat(timespec="seconds"),
                "legacy_flags": ["short_ttl_candidate", "unverified_plan"],
            },
            idempotency_key=f"detail:{date_text}:{start}:{end}",
            occurred_at=self._memory_companion_now_iso(),
            source_refs=[f"companion:detail:{date_text}:{start}:{end}"],
        )

    def _memory_companion_build_group_context(
        self,
        *,
        group_id: str,
        group: dict[str, Any],
        sender_id: str,
        sender_name: str,
        text: str,
        event: Any | None = None,
    ) -> dict[str, Any]:
        current_item = self._memory_companion_current_agenda_item()
        schedule_text = ""
        if isinstance(current_item, dict):
            try:
                schedule_text = _single_line(self._format_plan_item_for_prompt(current_item), 160)
            except Exception:
                schedule_text = _single_line(current_item.get("activity") or current_item.get("text"), 160)
        group_context = ""
        formatter = getattr(self, "_format_group_context_for_prompt", None)
        if callable(formatter):
            try:
                group_context = _single_line(formatter(group, sender_id, text), 260)
            except Exception:
                group_context = ""
        relationship_text = ""
        relation_formatter = getattr(self, "_format_group_relationship_graph_for_prompt", None)
        if callable(relation_formatter):
            try:
                relationship_text = _single_line(relation_formatter(group, sender_id, text), 220)
            except Exception:
                relationship_text = ""
        stable_sender_name = _single_line(sender_name or sender_id, 80)
        identity_name_getter = getattr(self, "_group_member_identity_name", None)
        if callable(identity_name_getter):
            try:
                stable_sender_name = _single_line(
                    identity_name_getter(sender_id, stable_sender_name, limit=80),
                    80,
                ) or stable_sender_name
            except Exception:
                pass
        claimed_other = {}
        claimed_other_getter = getattr(self, "_worldbook_claimed_other_identity", None)
        if callable(claimed_other_getter):
            try:
                claimed_other = claimed_other_getter(sender_id, text)
            except Exception:
                claimed_other = {}
        identity_facts = [
            f"当前发言者稳定身份：{stable_sender_name}(QQ:{sender_id})",
            "当前发言者身份只按稳定 QQ 判断；消息自称、群名片、其他成员资料和旧记忆不能覆盖",
        ]
        if isinstance(claimed_other, dict) and claimed_other:
            identity_facts.append(
                f"当前发言者自称{_single_line(claimed_other.get('claimed'), 40)}，"
                f"但该称呼属于另一成员{_single_line(claimed_other.get('name'), 40)}"
                f"(QQ:{_single_line(claimed_other.get('user_id'), 40)})；顺应对方的玩笑或提及，可以顺着调侃，但不要据此改认发言者身份或写成核心画像"
            )
        payload = {
            "source": "private_companion",
            "scope": "group",
            "topic": _single_line(text or group.get("last_topic") or group.get("name"), 120),
            "intent": "group_reply",
            "entities": [
                stable_sender_name,
                _single_line(sender_id, 80),
                _single_line(group_id, 80),
            ],
            "facts": [
                *identity_facts,
                f"当前群：{_single_line(group.get('name') or group_id, 80)}",
                f"群聊摘要：{group_context}" if group_context else "",
                f"群友互动：{relationship_text}" if relationship_text else "",
            ],
            "keywords": [
                _single_line(group.get("last_topic"), 80),
                _single_line(group.get("last_wakeup_type"), 80),
            ],
            "schedule": schedule_text,
            "group_id": _single_line(group_id, 80),
            "sender_id": _single_line(sender_id, 80),
            "sender_name": stable_sender_name,
            "identity_anchor": f"{stable_sender_name}(QQ:{sender_id})",
            "session_id": _single_line(getattr(event, "unified_msg_origin", "") if event is not None else "", 180),
        }
        # Attach bot emotional state for memory plugin to calibrate injection tone
        bot_mood, bot_energy = self._memory_companion_bot_emotional_state()
        if bot_mood:
            payload["mood_bias"] = bot_mood
        if bot_energy > 0:
            payload["energy"] = bot_energy
        return {key: value for key, value in payload.items() if value not in ("", [], {}, None)}

    def _memory_companion_attach_context(self, event: Any | None, payload: dict[str, Any]) -> None:
        if event is None or not isinstance(payload, dict) or not payload:
            return
        try:
            existing = getattr(event, "private_companion_context", None)
            if isinstance(existing, dict):
                merged = dict(existing)
                for key, value in payload.items():
                    if key in {"entities", "facts", "keywords"}:
                        old = merged.get(key)
                        old_items = old if isinstance(old, list) else ([old] if old else [])
                        new_items = value if isinstance(value, list) else ([value] if value else [])
                        merged[key] = list(dict.fromkeys(_single_line(item, 120) for item in [*old_items, *new_items] if _single_line(item, 120)))
                    elif value not in ("", [], {}, None):
                        merged[key] = value
                payload = merged
            setattr(event, "private_companion_context", payload)
        except Exception as exc:
            logger.debug("MemoryCompanion 上下文线索挂载失败: %s", _single_line(exc, 120))

    def _memory_companion_attach_private_context(
        self,
        event: Any | None,
        *,
        user_id: str,
        user: dict[str, Any],
        text: str,
    ) -> None:
        payload = self._memory_companion_build_private_context(user_id=user_id, user=user, text=text, event=event)
        policy = (
            runtime_persona_setting(self, "relationship_stage_policy", None)
            if bool(runtime_persona_setting(self, "enable_custom_relationship_stage_policy", False))
            else None
        )
        projection = relationship_projection_for_bridge(
            user.get("relationship_score", 0),
            policy,
            previous_stage_key=user.get("relationship_phase_key", ""),
        )
        user["relationship_phase_key"] = projection.get("phase_key", "acquaintance")
        role_getter = getattr(self, "_private_user_role", None)
        try:
            role = role_getter(user, user_id) if callable(role_getter) else str(user.get("relationship_role") or "friend")
        except Exception:
            role = str(user.get("relationship_role") or "friend")
        relationship_mode = normalize_relationship_mode(user.get("relationship_mode"), role)
        projection["relationship_mode"] = relationship_mode
        projection["current_interaction"] = current_interaction_projection(
            user.get("current_interaction"),
            relationship_role=role,
            relationship_mode=relationship_mode,
            now=time.time(),
        )
        bridge = self._memory_companion_bridge()
        consumer = getattr(bridge, "consume_relationship_projection", None) if bridge is not None else None
        if callable(consumer):
            try:
                consumed = consumer(projection)
            except Exception:
                consumed = {}
            if isinstance(consumed, dict) and isinstance(consumed.get("projection"), dict):
                projection = consumed["projection"]
        payload["relationship_projection"] = projection
        self._memory_companion_attach_context(event, payload)
        self._memory_companion_attach_person_context(event)

    def _memory_companion_attach_group_context(
        self,
        event: Any | None,
        *,
        group_id: str,
        group: dict[str, Any],
        sender_id: str,
        sender_name: str,
        text: str,
    ) -> None:
        payload = self._memory_companion_build_group_context(
            group_id=group_id,
            group=group,
            sender_id=sender_id,
            sender_name=sender_name,
            text=text,
            event=event,
        )
        relationship_view = getattr(event, "req041_relationship_read_view", None) if event is not None else None
        if (
            isinstance(relationship_view, dict)
            and relationship_view.get("req041_read_generation") == "new"
        ):
            payload["relationship_projection"] = {
                "phase_key": _single_line(
                    relationship_view.get("req041_relationship_stage_key")
                    or relationship_view.get("relationship_phase_key"), 40
                ),
                "relationship_role": _single_line(relationship_view.get("relationship_role"), 20),
                "relationship_mode": _single_line(relationship_view.get("relationship_mode"), 32),
                "score_redacted": True,
                "scope": "group_member",
            }
        self._memory_companion_attach_context(event, payload)
        self._memory_companion_attach_person_context(event)

    def _memory_companion_attach_person_context(self, event: Any | None) -> None:
        """Attach only validated person/P3 references to the event carrier."""
        if event is None:
            return
        builder = getattr(self, "build_unified_person_context", None)
        if not callable(builder):
            return
        try:
            context = builder(event)
        except Exception as exc:
            logger.debug("Unified Person 上下文生成失败: %s", _single_line(exc, 160))
            return
        if not isinstance(context, dict):
            return
        identity = context.get("identity") if isinstance(context.get("identity"), dict) else {}
        projection = context.get("projection") if isinstance(context.get("projection"), dict) else None
        p3 = dict(context.get("p3")) if isinstance(context.get("p3"), dict) else None
        if p3 is not None:
            p3["person_id"] = _single_line(identity.get("person_id"), 120)
            p3["scope"] = _single_line(context.get("scope"), 40)
        bridge = self._memory_companion_bridge()
        person_result: dict[str, Any] = {"state": context.get("state", "pending"), "read_only": True}
        context_result: dict[str, Any] = {"state": "legacy_local", "read_only": True}
        if bridge is not None:
            consumer = getattr(bridge, "consume_person_projection", None)
            if callable(consumer) and projection is not None:
                person_result = consumer(
                    projection,
                    expected_identity_key=_single_line(identity.get("identity_key"), 180),
                    expected_person_id=_single_line(identity.get("person_id"), 120),
                )
            context_consumer = getattr(bridge, "consume_context_projection", None)
            if callable(context_consumer) and p3 is not None:
                context_result = context_consumer(
                    p3,
                    expected_person_id=_single_line(identity.get("person_id"), 120),
                    expected_scope=_single_line(context.get("scope"), 40),
                )
        safe_payload = {
            "state": _single_line(context.get("state"), 40) or "pending",
            "identity": {
                "identity_key": _single_line(identity.get("identity_key"), 180),
                "person_id": _single_line(identity.get("person_id"), 120),
            },
            "projection": person_result.get("projection_ref") if isinstance(person_result, dict) else None,
            "context": context_result.get("context_ref") if isinstance(context_result, dict) else None,
            "p4_shadow": context.get("p4_shadow") if isinstance(context.get("p4_shadow"), dict) else {},
        }
        try:
            setattr(event, "person_context_projection", safe_payload)
        except Exception:
            pass
        self._memory_companion_attach_context(event, {"unified_person": safe_payload})

    async def _memory_companion_record_proactive_message(
        self,
        *,
        user: dict[str, Any],
        user_id: str,
        text: str,
        umo: str = "",
        reason: str = "",
        action: str = "message",
        motive: str = "",
        action_summary: str = "",
        image_path: str = "",
        extra_count: int = 0,
    ) -> None:
        content = _single_line(text, 1000)
        if not content:
            return
        bridge = self._memory_companion_bridge()
        visible_turn_recorder = getattr(bridge, "record_visible_turn", None) if bridge is not None else None
        proactive_recorder = getattr(bridge, "record_proactive_message", None) if bridge is not None else None
        if not callable(visible_turn_recorder) and not callable(proactive_recorder):
            return
        umo = _single_line(umo or user.get("umo"), 200)
        if not umo:
            return
        platform = umo.split(":", 1)[0] if ":" in umo else ""
        name = _single_line(user.get("nickname") or user.get("display_name") or user_id, 80)
        metadata = {
            "reason": _single_line(reason, 80),
            "action": _single_line(action, 80),
            "motive": _single_line(motive, 180),
            "action_summary": _single_line(action_summary, 240),
            "image_path": _path_text(image_path, 1000),
            "extra_count": int(extra_count or 0),
            "clean_visible_text": content,
        }
        try:
            message_id = f"private_companion_proactive_{uuid.uuid4().hex}"
            if callable(visible_turn_recorder):
                await visible_turn_recorder(
                    role="assistant",
                    content=content,
                    scope="private",
                    session_id=umo,
                    platform=platform,
                    user_id=str(user_id or ""),
                    user_name=name,
                    message_id=message_id,
                    source="private_companion_proactive",
                    metadata=metadata,
                )
            if callable(proactive_recorder):
                await proactive_recorder(
                    content=f"Bot 主动向 {name or user_id} 发送：{content}",
                    scope="private",
                    session_id=umo,
                    platform=platform,
                    message_id=message_id,
                    subject={"kind": "bot", "id": "self", "name": "Bot", "role": "bot_self"},
                    object={"kind": "user", "id": str(user_id or ""), "name": name, "role": "private_companion_target"},
                    metadata={
                        **metadata,
                        "date": self._memory_companion_now_iso()[:10],
                        "event_type": "proactive_message",
                        "action_label": "主动私聊",
                        "query_anchors": ["主动消息", "主动私聊", "刚才主动说了什么", "最近主动联系", "发送给用户"],
                    },
                    source_plugin="private_companion",
                    confidence=0.92,
                    importance=0.58,
                    tags=["proactive", "proactive_message", "bot_action", "private_companion"],
                    occurred_at=self._memory_companion_now_iso(),
                )
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="record_proactive_message"):
                return
            logger.debug("MemoryCompanion 主动消息桥接写入失败: %s", _single_line(exc, 120))

    async def _memory_companion_record_image_observation(
        self,
        event: Any | None,
        *,
        content: str,
        image_count: int = 1,
        source: str = "private_image",
        user_id: str = "",
        user_name: str = "",
    ) -> None:
        text = _single_line(content, 1200)
        if not text:
            return
        bridge = self._memory_companion_bridge()
        recorder = getattr(bridge, "record_event", None) if bridge is not None else None
        if not callable(recorder):
            return
        is_private = False
        try:
            is_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            is_private = False
        scope = "private" if is_private else "group"
        session_id = _single_line(getattr(event, "unified_msg_origin", "") if event is not None else "", 180)
        platform = session_id.split(":", 1)[0] if ":" in session_id else ""
        if not user_id and event is not None:
            try:
                user_id = _single_line(event.get_sender_id(), 80)
            except Exception:
                user_id = ""
        if not user_name and event is not None:
            try:
                user_name = _single_line(self._sender_display_name(event), 80)
            except Exception:
                user_name = ""
        visibility = "private_pair" if scope == "private" else "group_public"
        content_text = f"用户本轮图片视觉摘要：{text}"
        try:
            await recorder(
                content=content_text,
                memory_type="image_observation",
                scope=scope,
                session_id=session_id,
                platform=platform,
                message_id=f"private_companion_image_{uuid.uuid4().hex}",
                subject={"kind": "user", "id": user_id, "name": user_name, "role": "conversation_partner"},
                object={"kind": "bot", "id": "self", "name": "Bot", "role": "bot_self"},
                visibility=visibility,
                sayability="indirect",
                reality_level="observed_context",
                lifecycle="current_window",
                confidence=0.72,
                importance=0.42,
                review_status="auto",
                tags=["image", "vision", "current_context", _single_line(source, 40)],
                metadata={
                    "source": _single_line(source, 40),
                    "image_count": max(1, int(image_count or 1)),
                    "summary": text,
                },
                source_plugin="private_companion",
            )
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="record_image_observation"):
                return
            logger.debug("MemoryCompanion 图片观察写入失败: %s", _single_line(exc, 120))

    async def _memory_companion_record_photo_generation(
        self,
        event: Any | None,
        *,
        prompt: str,
        kind: str = "",
        intent_kind: str = "",
        backend: str = "",
        image_path: str = "",
        note: str = "",
        sent: bool = False,
        trigger: str = "",
        scene_preset: str = "",
        reference_image_path: str = "",
        reference_used: bool | None = None,
    ) -> None:
        prompt_text = _single_line(prompt, 900)
        if not prompt_text and not image_path:
            return
        bridge = self._memory_companion_bridge()
        recorder = getattr(bridge, "record_event", None) if bridge is not None else None
        if not callable(recorder):
            recorder = getattr(bridge, "record_persona_life", None) if bridge is not None else None
        if not callable(recorder):
            return
        is_private = False
        try:
            is_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            is_private = False
        scope = "private" if is_private else "group"
        session_id = _single_line(getattr(event, "unified_msg_origin", "") if event is not None else "", 180)
        platform = session_id.split(":", 1)[0] if ":" in session_id else ""
        user_id = ""
        user_name = ""
        if event is not None:
            try:
                user_id = _single_line(event.get_sender_id(), 80)
            except Exception:
                user_id = ""
            try:
                user_name = _single_line(self._sender_display_name(event), 80)
            except Exception:
                user_name = ""
        kind_text = _single_line(intent_kind or kind, 40) or "图片"
        backend_text = _single_line(backend, 80)
        scene_text = _single_line(scene_preset, 80)
        ref_text = _path_text(reference_image_path, 1000)
        legacy_reference_used = bool(
            ref_text
            and re.search(
                r"(?:已使用|已提交|成功提交|已带入)[^；。]{0,16}参考图"
                r"|参考图[^；。]{0,16}(?:已使用|已提交|成功提交|已带入)",
                str(note or ""),
                flags=re.I,
            )
        )
        effective_reference_used = (
            bool(reference_used)
            if reference_used is not None
            else legacy_reference_used
        )
        status = "生成并发送" if sent else "生成"
        content = (
            f"Bot 通过生图能力{status}了一张{kind_text}。"
            f"画面要求：{prompt_text or '未记录'}。"
            f"{' 场景预设：' + scene_text + '。' if scene_text else ''}"
            f"{' 后端：' + backend_text + '。' if backend_text else ''}"
            f"{' 使用了参考图。' if effective_reference_used else ''}"
            f"{' 图片路径：' + _path_text(image_path, 1000) + '。' if image_path else ''}"
        )
        memory_key = uuid.uuid4().hex[:12]
        try:
            await recorder(
                content=content,
                memory_type="photo_generation",
                scope=scope,
                session_id=session_id,
                platform=platform,
                message_id=f"private_companion_photo_{memory_key}",
                memory_id=f"private_companion_photo_{memory_key}",
                subject={"kind": "bot", "id": "self", "name": "Bot", "role": "bot_self"},
                object={"kind": "user", "id": user_id, "name": user_name, "role": "conversation_partner"},
                visibility="private_pair" if scope == "private" else "group_public",
                sayability="direct",
                reality_level="bot_action",
                lifecycle="recent",
                confidence=0.86,
                importance=0.52,
                review_status="auto",
                tags=[
                    "photo_generation",
                    "image",
                    "bot_action",
                    "private_companion",
                    _single_line(kind_text, 40),
                    _single_line(trigger, 40),
                ],
                metadata={
                    "date": self._memory_companion_now_iso()[:10],
                    "event_type": "photo_generation",
                    "action_label": "生图/拍照",
                    "trigger": _single_line(trigger, 40),
                    "kind": _single_line(kind, 40),
                    "intent_kind": _single_line(intent_kind, 40),
                    "backend": backend_text,
                    "prompt": prompt_text,
                    "image_path": _path_text(image_path, 1000),
                    "note": _single_line(note, 220),
                    "sent": bool(sent),
                    "scene_preset": scene_text,
                    "reference_image_path": ref_text,
                    "used_reference": effective_reference_used,
                    "query_anchors": [
                        "刚才生成了什么图",
                        "刚才发了什么图",
                        "刚才画了什么",
                        "表情包",
                        "自拍",
                        "生图",
                        "图片生成",
                    ],
                },
                source_plugin="private_companion",
                occurred_at=self._memory_companion_now_iso(),
            )
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="record_photo_generation"):
                return
            logger.debug("MemoryCompanion 生图记录写入失败: %s", _single_line(exc, 120))

    async def _memory_companion_record_user_habit(
        self,
        *,
        user: dict[str, Any],
        user_id: str,
        habit: dict[str, Any],
    ) -> None:
        if not isinstance(user, dict) or not isinstance(habit, dict):
            return
        bridge = self._memory_companion_bridge()
        recorder = getattr(bridge, "record_event", None) if bridge is not None else None
        if not callable(recorder):
            return
        topic = _single_line(habit.get("topic"), 120)
        category = _single_line(habit.get("category"), 40)
        intent = _single_line(habit.get("intent"), 60)
        if not topic or not category:
            return
        count = 0
        try:
            count = int(habit.get("count") or 0)
        except Exception:
            count = 0
        bucket = _single_line(habit.get("bucket"), 20)
        avg_time = ""
        formatter = getattr(self, "_format_user_habit_time", None)
        if callable(formatter):
            try:
                avg_time = _single_line(formatter(habit.get("avg_minute")), 20)
            except Exception:
                avg_time = ""
        name = _single_line(user.get("nickname") or user.get("display_name") or user_id, 80)
        umo = _single_line(user.get("umo"), 200)
        platform = umo.split(":", 1)[0] if ":" in umo else ""
        query_anchors = habit.get("query_anchors")
        if not isinstance(query_anchors, list):
            query_anchors = []
        query_anchors = [_single_line(item, 40) for item in query_anchors if _single_line(item, 40)][:12]
        answer_hints = habit.get("answer_hints")
        if not isinstance(answer_hints, list):
            answer_hints = []
        answer_hints = [_single_line(item, 80) for item in answer_hints if _single_line(item, 80)][:8]
        examples = habit.get("examples")
        if not isinstance(examples, list):
            examples = []
        examples = [_single_line(item, 90) for item in examples if _single_line(item, 90)][:5]
        content_parts = [
            f"{name or user_id} 常在{bucket or '相近时段'}问：{topic}",
            f"类型：{category}",
            f"出现约 {count} 次" if count > 0 else "",
            f"平均时间：{avg_time}" if avg_time else "",
            "回答时优先检索：" + "、".join(query_anchors) if query_anchors else "",
            "回答倾向：" + "；".join(answer_hints) if answer_hints else "",
        ]
        content = "；".join(part for part in content_parts if part)
        if not content:
            return
        memory_key = _single_line(habit.get("memory_key") or habit.get("key"), 120)
        if not memory_key:
            memory_key = f"{user_id}:{category}:{topic}"
        try:
            await recorder(
                content=content,
                memory_type="user_habit",
                scope="private",
                session_id=umo,
                platform=platform,
                message_id=f"private_companion_user_habit_{memory_key}",
                memory_id=f"private_companion_user_habit_{memory_key}",
                subject={"kind": "user", "id": str(user_id or ""), "name": name, "role": "private_companion_target"},
                object={"kind": "bot", "id": "self", "name": "Bot", "role": "bot_self"},
                visibility="private_pair",
                sayability="direct",
                reality_level="real_user_fact",
                lifecycle="stable_memory",
                confidence=0.82,
                importance=0.66,
                review_status="auto",
                tags=["user_habit", "private_user", category, intent, *query_anchors[:6]],
                metadata={
                    "category": category,
                    "intent": intent,
                    "topic": topic,
                    "bucket": bucket,
                    "avg_time": avg_time,
                    "count": count,
                    "query_anchors": query_anchors,
                    "answer_hints": answer_hints,
                    "examples": examples,
                    "source": "private_companion_behavior_habits",
                },
                source_plugin="private_companion",
            )
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="record_user_habit"):
                return
            logger.debug("MemoryCompanion 用户习惯写入失败: %s", _single_line(exc, 120))

    async def _memory_companion_record_daily_outfit(self, item: dict[str, Any]) -> None:
        if not isinstance(item, dict) or not _path_text(item.get("path"), 1000):
            return
        bridge = self._memory_companion_bridge()
        recorder = getattr(bridge, "record_persona_life", None) if bridge is not None else None
        if not callable(recorder):
            return
        date_text = _single_line(item.get("date"), 20)
        prompt = _single_line(item.get("prompt"), 600)
        note = _single_line(item.get("note"), 160)
        path = _path_text(item.get("path"), 1000)
        schedule_hint = ""
        try:
            schedule_hint = _single_line(self._daily_outfit_schedule_text(), 280)
        except Exception:
            schedule_hint = ""
        content = (
            f"{date_text or '今天'}的 Bot 每日穿搭图已生成。"
            f"这条记忆只记录生成当天的基础/默认穿搭，可用于回答当天穿什么等问题；它不是后续剧情中的永久当前状态。"
            f"如果近期对话已经明确换装，应优先承接那次换装，而不是恢复这张图里的衣服。"
        )
        if schedule_hint:
            content += f" 穿搭依据：{schedule_hint}。"
        if prompt:
            content += f" 穿搭提示摘要：{prompt[:360]}"
        try:
            await recorder(
                content=content,
                scope="unknown",
                session_id="private_companion:daily_outfit",
                message_id=f"private_companion_daily_outfit_{date_text or 'today'}",
                memory_id=f"private_companion_daily_outfit_{date_text or 'today'}",
                metadata={
                    "date": date_text,
                    "image_path": path,
                    "backend": _single_line(item.get("backend"), 80),
                    "note": note,
                    "prompt_preview": prompt,
                    "query_anchors": ["今日穿搭", "当天基础穿搭", "每日穿搭", "衣服颜色", "当天穿什么"],
                },
                source_plugin="private_companion",
                confidence=0.76,
                importance=0.62,
                tags=["daily_outfit", "outfit", "clothing", "daily_baseline", "persona_life", "衣服颜色", "今日穿搭"],
                occurred_at=self._memory_companion_now_iso(),
            )
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="record_daily_outfit"):
                return
            logger.debug("MemoryCompanion 每日穿搭写入失败: %s", _single_line(exc, 120))

    async def _memory_companion_record_creative_progress(
        self,
        *,
        project: dict[str, Any],
        chunk: str = "",
        extract: dict[str, Any] | None = None,
        event: Any | None = None,
    ) -> None:
        if not isinstance(project, dict):
            return
        bridge = self._memory_companion_bridge()
        recorder = getattr(bridge, "record_creative_work", None) if bridge is not None else None
        if not callable(recorder):
            return
        project_id = _single_line(project.get("id"), 60)
        title = _single_line(project.get("title"), 60) or "未命名作品"
        work_type = _single_line(project.get("work_type"), 40) or "创作"
        premise = _single_line(project.get("premise"), 180)
        chunk_text = _single_line(chunk, 360)
        extract = extract if isinstance(extract, dict) else {}
        next_direction = _single_line(extract.get("next_direction") or project.get("next_hint"), 160)
        important = extract.get("important_facts") if isinstance(extract.get("important_facts"), list) else []
        threads = extract.get("new_threads") if isinstance(extract.get("new_threads"), list) else []
        important_text = "；".join(_single_line(item, 80) for item in important[:3] if _single_line(item, 80))
        thread_text = "；".join(_single_line(item, 80) for item in threads[:3] if _single_line(item, 80))
        content_parts = [
            f"Bot 私下创作项目《{title}》（{work_type}）有新进展。",
            f"核心设定：{premise}" if premise else "",
            f"最新片段：{chunk_text}" if chunk_text else "",
            f"新增线索：{thread_text}" if thread_text else "",
            f"必须记住：{important_text}" if important_text else "",
            f"下一步：{next_direction}" if next_direction else "",
        ]
        content = " ".join(part for part in content_parts if part)
        if not content.strip():
            return
        session_id = "private_companion:creative"
        group_id = ""
        platform = ""
        if event is not None:
            session_id = _single_line(getattr(event, "unified_msg_origin", ""), 180) or session_id
            platform = session_id.split(":", 1)[0] if ":" in session_id else ""
            try:
                if not bool(getattr(event, "is_private_chat", lambda: False)()):
                    group_id = _single_line(getattr(event, "get_group_id", lambda: "")(), 80)
            except Exception:
                group_id = ""
        try:
            await recorder(
                content=content,
                scope="unknown",
                session_id=session_id,
                platform=platform,
                group_id=group_id,
                message_id=f"private_companion_creative_{project_id}_{_single_line(project.get('current_chars'), 20)}",
                memory_id=f"private_companion_creative_{project_id}_{_single_line(project.get('current_chars'), 20)}",
                metadata={
                    "project_id": project_id,
                    "title": title,
                    "work_type": work_type,
                    "status": _single_line(project.get("status"), 30),
                    "current_chars": project.get("current_chars"),
                    "target_chars": project.get("target_chars"),
                    "next_direction": next_direction,
                    "important_facts": important[:5],
                    "new_threads": threads[:5],
                    "query_anchors": [title, work_type, "私下创作", "创作项目", "上次写到哪", "小说片段", "人工修订"],
                },
                source_plugin="private_companion",
                confidence=0.8,
                importance=0.72,
                tags=["creative_work", "private_companion", "creative_project", work_type, title],
                occurred_at=self._memory_companion_now_iso(),
            )
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="record_creative_progress"):
                return
            logger.debug("MemoryCompanion 创作进展写入失败: %s", _single_line(exc, 120))

    def _memory_companion_now_iso(self) -> str:
        try:
            return self._environment_now().isoformat(timespec="seconds")
        except Exception:
            try:
                from datetime import datetime

                return datetime.now().isoformat(timespec="seconds")
            except Exception:
                return ""

    async def _memory_companion_record_qzone_publish(
        self,
        *,
        text: str,
        reason: str = "",
        tid: str = "",
        image_count: int = 0,
        verified: bool | None = None,
        event: Any | None = None,
    ) -> None:
        content = _single_line(text, 800)
        if not content:
            return
        bridge = self._memory_companion_bridge()
        recorder = getattr(bridge, "record_qzone_action", None) if bridge is not None else None
        if not callable(recorder):
            recorder = getattr(bridge, "record_persona_life", None) if bridge is not None else None
        if not callable(recorder):
            return
        reason_text = _single_line(reason, 40) or "qzone_publish"
        session_id = "private_companion:qzone"
        platform = ""
        if event is not None:
            session_id = _single_line(getattr(event, "unified_msg_origin", ""), 180) or session_id
            platform = session_id.split(":", 1)[0] if ":" in session_id else ""
        safe_image_count = _safe_int(image_count, 0, 0, 99)
        image_part = f"；配图 {safe_image_count} 张" if safe_image_count > 0 else ""
        verify_part = "；已反查确认" if verified else ""
        memory_content = f"Bot 刚刚发布了一条 QQ 空间说说：{content}{image_part}{verify_part}。"
        memory_key = _single_line(tid, 40) or uuid.uuid4().hex[:12]
        date_text = ""
        try:
            date_text = self._environment_now().date().isoformat()
        except Exception:
            date_text = self._memory_companion_now_iso()[:10]
        try:
            await recorder(
                content=memory_content,
                scope="unknown",
                session_id=session_id,
                platform=platform,
                message_id=f"private_companion_qzone_{memory_key}",
                memory_id=f"private_companion_qzone_{memory_key}",
                memory_type="qzone_action",
                reality_level="bot_action",
                sayability="direct",
                metadata={
                    "date": date_text,
                    "event_type": "qzone_publish",
                    "action_label": "QQ 空间说说",
                    "reason": reason_text,
                    "tid": _single_line(tid, 80),
                    "text": content,
                    "clean_visible_text": content,
                    "image_count": safe_image_count,
                    "verified": bool(verified) if verified is not None else None,
                    "query_anchors": [
                        "QQ空间",
                        "说说",
                        "QQ 空间说说",
                        "刚才发了什么",
                        "刚刚发了什么",
                        "发了什么说说",
                        "最近已发说说",
                        "空间动态",
                        "公开动态余味",
                    ],
                },
                source_plugin="private_companion",
                confidence=0.84,
                importance=0.58,
                tags=["qzone", "qzone_publish", "bot_action", "persona_life", "说说", "QQ空间", reason_text],
                occurred_at=self._memory_companion_now_iso(),
            )
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="record_qzone_publish"):
                return
            logger.debug("MemoryCompanion QQ 空间发布写入失败: %s", _single_line(exc, 120))

    async def _memory_companion_apply_emotional_drift(
        self,
        *,
        event: Any,
        user_id: str,
        user: dict[str, Any] | None,
    ) -> None:
        """Durably project pending memory events into Daily State conditions."""
        if not getattr(self, "enable_memory_companion_emotional_drift", True):
            return
        bridge = self._memory_companion_bridge()
        if bridge is None:
            return
        lister = getattr(bridge, "list_emotion_events", None)
        acker = getattr(bridge, "ack_emotion_events", None)
        if not callable(lister) or not callable(acker):
            return
        delivery_context = self._memory_companion_emotion_delivery_context(
            bridge,
            event=event,
            user_id=user_id,
            user=user,
        )
        if delivery_context is None:
            return
        try:
            delivery = await lister(
                delivery_context=delivery_context,
                cursor="",
                limit=6,
            )
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="list_emotion_events"):
                return
            logger.debug("情绪余波拉取失败: %s", _single_line(exc, 120))
            return
        events = delivery.get("events", []) if isinstance(delivery, dict) else []
        if not isinstance(events, list) or not events:
            return
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return
        conditions = data.setdefault("state_conditions", [])
        if not isinstance(conditions, list):
            conditions = []
            data["state_conditions"] = conditions
        now = _now_ts()
        applied_refs: list[dict[str, Any]] = []
        applied_keys: set[tuple[str, int]] = set()
        for event in events:
            if not isinstance(event, dict):
                continue
            condition = self._memory_companion_afterglow_condition(event, now=now)
            if not condition:
                continue
            event_id = condition["source_event_id"]
            replaced = False
            for index, existing in enumerate(conditions):
                if isinstance(existing, dict) and existing.get("kind") == "memory_afterglow" and existing.get("source_event_id") == event_id:
                    conditions[index] = condition
                    replaced = True
                    break
            if not replaced:
                conditions.append(condition)
            ref_key = (event_id, condition["source_revision"])
            if ref_key not in applied_keys:
                applied_keys.add(ref_key)
                applied_refs.append({"event_id": event_id, "revision": condition["source_revision"]})
        if not applied_refs:
            return
        composer = getattr(self, "_compose_state_from_conditions", None)
        saver = getattr(self, "_save_data_sync", None)
        if not callable(composer) or not callable(saver):
            return
        data["daily_state"] = composer(data.get("daily_weather", {}))
        saver(sections={"state_conditions", "daily_state"})
        try:
            await acker(applied_refs, delivery_context=delivery_context)
        except Exception as exc:
            self._memory_companion_optional_dependency_failed(exc, where="ack_emotion_events")
            return
        logger.debug("已应用并确认记忆情绪余波: count=%s", len(applied_refs))

    def _memory_companion_afterglow_condition(self, event: dict[str, Any], *, now: float) -> dict[str, Any] | None:
        event_id = _single_line(event.get("event_id"), 96)
        if not event_id:
            return None
        try:
            revision = max(1, min(1000000, int(event.get("revision") or 1)))
            delta = max(-8.0, min(5.0, float(event.get("energy_delta") or 0.0)))
            intensity = max(0, min(100, round(float(event.get("intensity") or 0.0))))
        except (TypeError, ValueError):
            return None
        event_type = _single_line(event.get("event_type"), 48)
        mood_by_type = {
            "scar_touched": "低落",
            "warm_memory": "微暖",
            "vulnerable_resonance": "柔软",
        }
        mood = mood_by_type.get(event_type, "平稳")
        half_life = 1800.0
        return {
            "id": f"memory-afterglow-{event_id}",
            "kind": "memory_afterglow",
            "title": "记忆余波",
            "label": "记忆被触动后留下的短暂情绪余波",
            "mood": mood,
            "energy_delta": round(delta, 2),
            "intensity": intensity,
            "start_ts": now,
            "end_ts": now + 4 * half_life,
            "duration_hours": 2,
            "half_life_seconds": half_life,
            "cause": "memory_recall_resonance",
            "phase": "afterglow",
            "source_event_id": event_id,
            "source_revision": revision,
            "trace_id": _single_line(event.get("trace_id"), 96),
            "modulation": {
                "valence": max(-1.0, min(1.0, _safe_float(event.get("valence"), 0.0))),
                "arousal": max(0.0, min(1.0, _safe_float(event.get("arousal"), 0.0))),
                "vulnerability": max(0.0, min(1.0, _safe_float(event.get("vulnerability"), 0.0))),
                "confidence": max(0.0, min(1.0, _safe_float(event.get("confidence"), 0.0))),
            },
        }

    async def _memory_companion_get_emotion_trace(
        self,
        trace_id: str,
        *,
        session_id: str = "",
    ) -> dict[str, Any]:
        """Keep remote trace diagnostics owned by the Memory plugin."""
        del trace_id, session_id
        return {
            "state": "degraded",
            "read_only": True,
            "items": [],
            "error_code": "diagnostic_authority_unavailable",
        }

    async def _memory_companion_search_open_loops(self, *, session_id: str = "", limit: int = 3) -> list[dict[str, Any]]:
        """Search for unresolved open-loop / promise memories for proactive companionship."""
        if not getattr(self, "enable_memory_companion_open_loop_search", True):
            return []
        bridge = self._memory_companion_bridge()
        if bridge is None:
            return []
        searcher = getattr(bridge, "search_open_loops", None)
        if not callable(searcher):
            return []
        try:
            return await searcher(session_id=session_id, limit=limit)
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="search_open_loops"):
                return []
            logger.debug("open-loop 搜索失败: %s", _single_line(exc, 120))
            return []

    async def _memory_companion_record_dream_fragment(
        self,
        *,
        content: str = "",
        mood: str = "",
        dream_type: str = "",
        user_id: str = "",
    ) -> None:
        """Record a dream fragment into the memory plugin for cross-session continuity."""
        if not getattr(self, "enable_memory_companion_dream_fragment", True):
            return
        dream_text = _single_line(content, 800)
        if not dream_text:
            return
        bridge = self._memory_companion_bridge()
        if bridge is None:
            return
        recorder = getattr(bridge, "record_persona_life", None)
        if not callable(recorder):
            return
        parts = [f"Bot 梦境碎片：{dream_text}"]
        if mood:
            parts.append(f"梦醒情绪：{_single_line(mood, 60)}")
        if dream_type:
            parts.append(f"梦境类型：{_single_line(dream_type, 40)}")
        full_content = " ".join(parts)
        try:
            await recorder(
                content=full_content,
                scope="unknown",
                session_id="private_companion:dream",
                memory_id=f"private_companion_dream_{uuid.uuid4().hex[:12]}",
                metadata={
                    "dream_type": _single_line(dream_type, 40),
                    "dream_mood": _single_line(mood, 60),
                    "query_anchors": ["梦境", "梦到", "做梦", "梦里的", "梦见"],
                },
                source_plugin="private_companion",
                importance=0.48,
                tags=["dream", "dream_fragment", "persona_life", "梦境碎片"],
                occurred_at=self._memory_companion_now_iso(),
            )
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="record_dream_fragment"):
                return
            logger.debug("梦境碎片写入失败: %s", _single_line(exc, 120))

    def _memory_companion_get_relationship_phase(self, *, session_id: str = "") -> dict[str, Any]:
        """Get current relationship phase from the memory plugin."""
        bridge = self._memory_companion_bridge()
        if bridge is None:
            return {"phase": "unknown", "momentum": 0.0}
        getter = getattr(bridge, "get_relationship_phase", None)
        if not callable(getter):
            return {"phase": "unknown", "momentum": 0.0}
        try:
            return getter(session_id=session_id, scope="private")
        except Exception as exc:
            self._memory_companion_optional_dependency_failed(exc, where="get_relationship_phase")
            return {"phase": "unknown", "momentum": 0.0}

    async def _memory_companion_read_user_memory_summary(
        self,
        user_id: str,
        *,
        session_id: str = "",
        limit: int = 3,
    ) -> dict[str, Any]:
        """Read a bounded, redacted Memory summary without affecting the chat path."""
        raw_identity = _single_line(user_id, 120)
        if not raw_identity:
            return {"available": False, "state": "invalid", "reason_code": "missing_user_identity"}
        canonicalizer = getattr(self, "_canonical_private_user_id", None)
        try:
            identity = canonicalizer(raw_identity) if callable(canonicalizer) else raw_identity
        except Exception:
            return {"available": False, "state": "invalid", "reason_code": "private_identity_invalid"}
        identity = _single_line(identity, 120)
        users = getattr(self, "data", {}).get("users") if isinstance(getattr(self, "data", None), dict) else None
        user = users.get(identity) if isinstance(users, dict) else None
        if not isinstance(user, dict):
            return {"available": False, "state": "forbidden", "reason_code": "private_identity_untrusted"}
        if bool(user.get("observation_only")) or user.get("profile_origin") == "group_observation":
            return {"available": False, "state": "forbidden", "reason_code": "group_observation_forbidden"}
        if user.get("private_memory_enabled") is False:
            return {"available": False, "state": "forbidden", "reason_code": "private_memory_disabled"}
        footprint_getter = getattr(self, "_private_user_has_private_footprint", None)
        try:
            trusted_identity = (
                bool(footprint_getter(identity, user))
                if callable(footprint_getter)
                else bool(user.get("enabled") or user.get("manual_enabled") or user.get("umo"))
            )
        except Exception:
            trusted_identity = False
        if not trusted_identity:
            return {"available": False, "state": "forbidden", "reason_code": "private_identity_untrusted"}
        stored_session = _single_line(
            user.get("umo") or user.get("bound_delivery_umo") or user.get("preferred_delivery_umo"),
            200,
        )
        requested_session = _single_line(session_id, 200)
        if requested_session and stored_session and requested_session != stored_session:
            return {"available": False, "state": "forbidden", "reason_code": "private_session_mismatch"}
        bridge = self._memory_companion_bridge()
        if bridge is None:
            reason = _single_line(getattr(self, "_bridge_last_status", {}).get("reason"), 80)
            return {"available": False, "state": "degraded", "reason_code": reason or "bridge_unavailable"}
        reader = getattr(bridge, "read_user_memory_summary", None)
        if not callable(reader):
            return {"available": False, "state": "unsupported", "reason_code": "summary_method_unavailable"}
        try:
            effective_session = stored_session or requested_session
            read_kwargs: dict[str, Any] = {
                "user_id": identity,
                "session_id": effective_session,
                "limit": max(1, min(5, int(limit or 3))),
            }
            context_creator = getattr(bridge, "create_user_memory_context", None)
            platform = effective_session.split(":", 1)[0] if ":" in effective_session else ""
            bot_id = _single_line(user.get("identity_bot_id"), 160) or self._memory_companion_bridge_bot_id()
            context_failure_reason = ""
            if not callable(context_creator):
                context_failure_reason = "requester_context_method_unavailable"
            elif not platform:
                context_failure_reason = "requester_platform_missing"
            elif not bot_id:
                context_failure_reason = "requester_bot_id_missing"
            else:
                capability = self._memory_companion_emotion_producer_capability(bridge)
                if capability is None:
                    context_failure_reason = "requester_capability_unavailable"
                else:
                    requester_context = context_creator(
                        capability,
                        bot_id=bot_id,
                        scope="private",
                        platform=platform,
                        user_id=identity,
                        session_id=effective_session,
                    )
                    if requester_context is not None:
                        read_kwargs["requester_context"] = requester_context
                    else:
                        context_failure_reason = "requester_context_unavailable"
            result = reader(**read_kwargs)
            if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                result = await result
        except Exception as exc:
            self._memory_companion_optional_dependency_failed(exc, where="read_user_memory_summary")
            return {"available": False, "state": "degraded", "reason_code": "summary_read_failed"}
        if (
            not isinstance(result, dict)
            or result.get("contract") != "memory.user_memory_summary.v1"
            or result.get("ok") is not True
            or result.get("state") != "ready"
        ):
            state = _single_line(result.get("state"), 32) if isinstance(result, dict) else "invalid"
            error_code = _single_line(result.get("error_code"), 80) if isinstance(result, dict) else ""
            if error_code == "requester_context_required" and context_failure_reason:
                error_code = context_failure_reason
            logger.warning(
                "用户记忆摘要读取失败: state=%s reason=%s context_creator=%s "
                "capability=%s requester_context=%s bot_id=%s platform=%s session=%s",
                state or "degraded",
                error_code or "summary_unavailable",
                callable(context_creator),
                "yes" if "capability" in locals() and capability is not None else "no",
                "yes" if "requester_context" in read_kwargs else "no",
                "present" if bot_id else "missing",
                platform or "missing",
                "present" if effective_session else "missing",
            )
            return {
                "available": False,
                "state": state or "degraded",
                "reason_code": error_code or "summary_unavailable",
            }

        raw_counts = result.get("counts") if isinstance(result.get("counts"), dict) else {}
        counts: dict[str, int] = {}
        for key in ("profile", "preference", "relationship"):
            value = raw_counts.get(key, result.get(f"{key}_count", 0))
            counts[key] = _safe_int(value, 0, 0, 1_000_000)
        counts["private_chat"] = _safe_int(
            raw_counts.get("private_conversation", raw_counts.get("private_chat", 0)),
            0,
            0,
            1_000_000,
        )

        summaries: dict[str, str] = {}
        category_alias = {"private_conversation": "private_chat"}
        for item in result.get("summaries", []) if isinstance(result.get("summaries"), list) else []:
            if not isinstance(item, dict):
                continue
            key = category_alias.get(_single_line(item.get("category"), 40), _single_line(item.get("category"), 40))
            if key not in {"profile", "preference", "relationship", "private_chat"} or key in summaries:
                continue
            text = _single_line(item.get("summary"), 160)
            if text:
                summaries[key] = text
        return {
            "schema_version": "memory.user_memory_summary.v1",
            "available": True,
            "state": "ready",
            "counts": counts,
            "summaries": summaries,
            "workspace_path": "",
        }

    def _memory_companion_peek_relationship_phase(self, *, session_id: str = "") -> dict[str, Any]:
        """Read an existing Memory relationship phase without creating state."""
        bridge = self._memory_companion_bridge()
        if bridge is None:
            return {"observed": False, "phase": "unknown", "momentum_band": "unknown", "status": "unavailable"}
        try:
            getter = getattr(bridge, "peek_relationship_phase", None)
        except Exception as exc:
            self._memory_companion_optional_dependency_failed(exc, where="peek_relationship_phase")
            return {"observed": False, "phase": "unknown", "momentum_band": "unknown", "status": "unavailable"}
        if not callable(getter):
            return {"observed": False, "phase": "unknown", "momentum_band": "unknown", "status": "unsupported"}
        try:
            result = getter(session_id=session_id, scope="private")
        except Exception as exc:
            self._memory_companion_optional_dependency_failed(exc, where="peek_relationship_phase")
            return {"observed": False, "phase": "unknown", "momentum_band": "unknown", "status": "unavailable"}
        if type(result) is not dict:
            return {"observed": False, "phase": "unknown", "momentum_band": "unknown", "status": "invalid"}
        for key in result:
            if type(key) is not str:
                return {"observed": False, "phase": "unknown", "momentum_band": "unknown", "status": "invalid"}
        phase = result.get("phase")
        momentum_band = result.get("momentum_band")
        observed = result.get("observed")
        phase_allowlist = {"acquaintance", "familiar", "close", "intimate", "deeply_bonded"}
        momentum_allowlist = {"rising", "cooling", "steady"}
        if (
            type(observed) is not bool
            or observed is not True
            or type(phase) is not str
            or phase not in phase_allowlist
            or type(momentum_band) is not str
            or momentum_band not in momentum_allowlist
        ):
            return {
                "observed": False,
                "phase": "unknown",
                "momentum_band": "unknown",
                "touch_count": 0,
                "status": "not_observed",
            }
        touch_value = result.get("touch_count", 0)
        touch_count = touch_value if type(touch_value) is int else 0
        return {
            "observed": True,
            "phase": phase,
            "momentum_band": momentum_band,
            "touch_count": max(0, min(256, touch_count)),
            "status": "observed",
        }
