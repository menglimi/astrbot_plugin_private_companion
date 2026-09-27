# -*- coding: utf-8 -*-
"""MemoryCompanionAdapterBridgeLifecycleMixin。

由 tools/split_mixin_domain.py 从 memory_companion_adapter.py 机械抽取（13 个方法 + 0 个模块级名字 + 0 个类级赋值 / 372 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 MemoryCompanionAdapterMixin）。
"""
from __future__ import annotations

import sys
import time
from .helpers import _missing_optional_model_dependency, _single_line
from .memory_companion_adapter_shared import logger
from typing import Any



class MemoryCompanionAdapterBridgeLifecycleMixin:
    """MemoryCompanionAdapterBridgeLifecycleMixin（从 MemoryCompanionAdapterMixin 拆出）。"""


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
