# -*- coding: utf-8 -*-
"""人格路由告警与投递校验域。

从 main.py 机械抽取后一度落在 ``main_misc_unassigned``（杂项未分配桶），
现按业务域归位到人格路由族（4 个方法 / 431 行）。

方法体零改动：所有 ``self.xxx`` 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from types import SimpleNamespace
from typing import Any

from .helpers import _safe_float, _safe_int, _single_line
from .logging_util import get_module_logger
from .main_shared import _ACTIVE_PERSONA_ID

logger = get_module_logger(__name__)


class PrivateCompanionPluginPersonaRoutingPart04Mixin:
    """人格路由告警与投递校验（从 PrivateCompanionPlugin 拆出）。"""

    async def _astrbot_effective_persona_for_event(self, event: Any) -> dict[str, Any]:
        """Resolve AstrBot's request persona using AstrBot's own precedence."""
        umo = str(getattr(event, "unified_msg_origin", "") or "").strip()
        result = {
            "persona_id": "",
            "source": "unresolved",
            "exists": False,
            "explicit_none": False,
            "umo": umo,
            "error": "",
        }
        if not umo:
            result["error"] = "umo_missing"
            return result
        context = getattr(self, "context", None)
        conversation_persona = None
        try:
            conversation_manager = getattr(context, "conversation_manager", None)
            if conversation_manager is not None:
                cid = await self._await_if_needed(
                    conversation_manager.get_curr_conversation_id(umo)
                )
                if cid:
                    conversation = await self._await_if_needed(
                        conversation_manager.get_conversation(umo, cid)
                    )
                    conversation_persona = getattr(conversation, "persona_id", None)

            config_getter = getattr(context, "get_config", None)
            try:
                astrbot_config = config_getter(umo=umo) if callable(config_getter) else {}
            except TypeError:
                astrbot_config = config_getter() if callable(config_getter) else {}
            provider_settings = (
                astrbot_config.get("provider_settings", {})
                if isinstance(astrbot_config, dict)
                else {}
            )
            platform_getter = getattr(event, "get_platform_name", None)
            platform_name = (
                str(platform_getter() or "") if callable(platform_getter) else ""
            )
            persona_manager = getattr(context, "persona_manager", None)
            resolver = getattr(persona_manager, "resolve_selected_persona", None)
            if callable(resolver):
                resolved = await self._await_if_needed(
                    resolver(
                        umo=umo,
                        conversation_persona_id=conversation_persona,
                        platform_name=platform_name,
                        provider_settings=provider_settings,
                    )
                )
                selected = resolved[0] if isinstance(resolved, (list, tuple)) and resolved else ""
                persona = resolved[1] if isinstance(resolved, (list, tuple)) and len(resolved) > 1 else None
                forced = resolved[2] if isinstance(resolved, (list, tuple)) and len(resolved) > 2 else ""
                pid = self._sanitize_persona_id(selected)
                result.update(
                    {
                        "persona_id": pid,
                        "source": (
                            "session_rule"
                            if forced
                            else "explicit_none"
                            if conversation_persona == "[%None]"
                            else "conversation"
                            if conversation_persona
                            else "provider_default"
                        ),
                        "exists": persona is not None,
                        "explicit_none": selected == "[%None]" or conversation_persona == "[%None]",
                    }
                )
                return result

            selected = conversation_persona
            source = "conversation"
            if selected is None:
                selected = provider_settings.get("default_personality")
                source = "provider_default"
            result.update(
                {
                    "persona_id": self._sanitize_persona_id(selected),
                    "source": "explicit_none" if selected == "[%None]" else source,
                    "exists": self._astrbot_persona_exists(selected),
                    "explicit_none": selected == "[%None]",
                    "error": "astrbot_effective_persona_resolver_unavailable",
                }
            )
        except Exception as exc:
            result["error"] = _single_line(exc, 160) or "persona_resolution_failed"
        return result

    async def _validate_proactive_persona_delivery(
        self,
        target_umo: Any,
        scheduled_persona_id: Any,
    ) -> dict[str, Any]:
        """Validate the last-mile target without changing AstrBot's routing."""
        umo = _single_line(target_umo, 240)
        multi = bool(getattr(self, "enable_multi_persona_mode", False))
        primary = self._primary_persona_id()
        scheduled = self._sanitize_persona_id(
            scheduled_persona_id
            or _ACTIVE_PERSONA_ID.get()
            or ("" if multi else primary)
        )
        # In single-persona mode an empty plugin_specific_persona_id means
        # “use AstrBot's current/default persona”, not a routing problem.
        # AstrBot remains the authority for the effective conversation persona.
        if not multi and not primary and umo:
            return {
                "ok": True,
                "action": "matched",
                "astrbot_persona_id": "",
                "scheduled_persona_id": "",
                "reason_code": "",
            }
        event = SimpleNamespace(
            unified_msg_origin=umo,
            get_platform_name=lambda: umo.partition(":")[0],
        )
        resolved = await self._astrbot_effective_persona_for_event(event)
        astrbot_persona = self._sanitize_persona_id(resolved.get("persona_id"))
        reason = ""
        if not umo:
            reason = "target_umo_missing"
        elif not astrbot_persona:
            reason = "astrbot_persona_unresolved"
        elif not resolved.get("exists"):
            reason = "astrbot_persona_missing"
        elif not scheduled:
            reason = "scheduled_persona_missing"
        elif astrbot_persona != scheduled:
            reason = "target_persona_mismatch"
        elif multi:
            ready, readiness_reason = self._persona_profile_route_status(scheduled)
            if not ready:
                reason = readiness_reason

        if not reason:
            await self._resolve_persona_routing_warnings(
                channel="proactive",
                window_key=umo,
                warning_families={"proactive_delivery"},
            )
            return {
                "ok": True,
                "action": "matched",
                "astrbot_persona_id": astrbot_persona,
                "scheduled_persona_id": scheduled,
                "reason_code": "",
            }
        if not multi:
            await self._record_persona_routing_warning(
                code="persona.route.proactive_single_mismatch_allowed",
                channel="proactive",
                disposition="sent_with_warning",
                reason_code=reason,
                window_key=umo,
                requested_persona_id=astrbot_persona,
                resolved_persona_id=scheduled,
                active_persona_id=scheduled,
            )
            return {
                "ok": True,
                "action": "sent_with_warning",
                "astrbot_persona_id": astrbot_persona,
                "scheduled_persona_id": scheduled,
                "reason_code": reason,
            }
        await self._record_persona_routing_warning(
            code="persona.route.proactive_multi_mismatch_blocked",
            channel="proactive",
            disposition="blocked",
            reason_code=reason,
            window_key=umo,
            requested_persona_id=astrbot_persona,
            resolved_persona_id=scheduled,
            active_persona_id=scheduled,
        )
        return {
            "ok": False,
            "action": "blocked",
            "astrbot_persona_id": astrbot_persona,
            "scheduled_persona_id": scheduled,
            "reason_code": reason,
        }

    async def _record_persona_routing_warning(
        self,
        *,
        code: str,
        channel: str,
        disposition: str,
        reason_code: str,
        window_key: Any = "",
        requested_persona_id: Any = "",
        resolved_persona_id: Any = "",
        active_persona_id: Any = "",
    ) -> None:
        """Persist one global, content-free persona routing diagnostic."""
        now = time.time()
        window = _single_line(window_key, 180)
        requested = self._sanitize_persona_id(requested_persona_id)
        resolved = self._sanitize_persona_id(resolved_persona_id)
        active = self._sanitize_persona_id(active_persona_id)
        reason = _single_line(reason_code, 80) or "unknown"
        normalized_code = _single_line(code, 100)
        normalized_channel = _single_line(channel, 24)
        warning_family = self._persona_routing_warning_family(
            normalized_code,
            normalized_channel,
        )

        signature = "|".join((normalized_code, reason, normalized_channel, window))
        record_id = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:20]
        should_schedule = False
        lock = getattr(self, "_data_lock", None)

        async def update() -> None:
            nonlocal should_schedule
            store = getattr(self, "_data_default", None)
            if not isinstance(store, dict):
                return
            root = store.get("persona_routing_warnings")
            if not isinstance(root, dict):
                root = {"schema_version": 2, "items": []}
                store["persona_routing_warnings"] = root
            root["schema_version"] = 2
            items = root.get("items")
            if not isinstance(items, list):
                items = []
                root["items"] = items
            item = next(
                (
                    candidate
                    for candidate in items
                    if isinstance(candidate, dict)
                    and (
                        candidate.get("id") == record_id
                        or (
                            _single_line(candidate.get("code"), 100) == normalized_code
                            and _single_line(candidate.get("reason_code"), 80) == reason
                            and _single_line(candidate.get("channel"), 24) == normalized_channel
                            and _single_line(candidate.get("window_key"), 180) == window
                        )
                    )
                ),
                None,
            )
            if item is None:
                item = next(
                    (
                        candidate
                        for candidate in items
                        if isinstance(candidate, dict)
                        and not self._persona_routing_warning_is_active(candidate)
                        and _single_line(candidate.get("channel"), 24) == normalized_channel
                        and _single_line(candidate.get("window_key"), 180) == window
                        and (
                            _single_line(candidate.get("warning_family"), 80)
                            or self._persona_routing_warning_family(
                                candidate.get("code"),
                                candidate.get("channel"),
                            )
                        )
                        == warning_family
                    ),
                    None,
                )
            if item is None:
                item = {
                    "id": record_id,
                    "first_ts": now,
                    "count": 0,
                    "lifetime_count": 0,
                }
                items.append(item)
                should_schedule = True
            previous_last_ts = _safe_float(item.get("last_ts"), 0.0)
            was_active = self._persona_routing_warning_is_active(item) and (
                previous_last_ts > 0 and now - previous_last_ts <= 2 * 60 * 60
            )
            previous_episode_count = _safe_int(item.get("count"), 0, 0)
            previous_lifetime_count = max(
                previous_episode_count,
                _safe_int(item.get("lifetime_count"), 0, 0),
            )
            if not was_active:
                item["first_ts"] = now
                item["count"] = 0
                should_schedule = True
            current_episode_count = previous_episode_count + 1 if was_active else 1
            item.update(
                {
                    "schema_version": 2,
                    "code": normalized_code,
                    "level": "error" if disposition == "blocked" else "warn",
                    "channel": normalized_channel,
                    "warning_family": warning_family,
                    "disposition": _single_line(disposition, 24),
                    "reason_code": reason,
                    "source": "persona_router",
                    "window_key": window,
                    "requested_persona_id": requested,
                    "resolved_persona_id": resolved,
                    "active_persona_id": active,
                    "primary_persona_id": self._primary_persona_id(),
                    "multi_persona": bool(getattr(self, "enable_multi_persona_mode", False)),
                    "status": "active",
                    "resolved_ts": 0,
                    "last_ts": now,
                    "count": current_episode_count,
                    "lifetime_count": previous_lifetime_count + 1,
                }
            )
            items.sort(
                key=lambda candidate: _safe_float(
                    candidate.get("last_ts") if isinstance(candidate, dict) else 0,
                    0.0,
                ),
                reverse=True,
            )
            del items[120:]
            save_marks = getattr(self, "_persona_routing_warning_save_marks", None)
            if not isinstance(save_marks, dict):
                save_marks = {}
                self._persona_routing_warning_save_marks = save_marks
            previous_save = _safe_float(save_marks.get(record_id), 0.0)
            if now - previous_save >= 60:
                save_marks[record_id] = now
                should_schedule = True

        if isinstance(lock, asyncio.Lock):
            async with lock:
                await update()
        else:
            await update()
        if should_schedule:
            scheduler = getattr(self, "_schedule_default_data_save", None)
            if callable(scheduler):
                clear_token = _ACTIVE_PERSONA_ID.set("")
                try:
                    scheduler(sections={"persona_routing_warnings"}, delay=0.2)
                finally:
                    _ACTIVE_PERSONA_ID.reset(clear_token)
        log_marks = getattr(self, "_persona_routing_warning_log_marks", None)
        if not isinstance(log_marks, dict):
            log_marks = {}
            self._persona_routing_warning_log_marks = log_marks
        if now - _safe_float(log_marks.get(record_id), 0.0) >= 300:
            log_marks[record_id] = now
            logger.warning(
                "人格路由告警 code=%s reason=%s umo=%s astrbot=%s plugin=%s action=%s",
                code,
                reason,
                window or "-",
                requested or "-",
                active or "-",
                disposition,
            )

    async def _resolve_persona_routing_warnings(
        self,
        *,
        channel: str,
        window_key: Any,
        warning_families: set[str],
    ) -> int:
        """Resolve active routing warnings after the same route becomes healthy."""
        now = time.time()
        normalized_channel = _single_line(channel, 24)
        window = _single_line(window_key, 180)
        families = {
            _single_line(value, 80)
            for value in warning_families
            if _single_line(value, 80)
        }
        if not normalized_channel or not families:
            return 0
        resolved_count = 0
        lock = getattr(self, "_data_lock", None)

        async def update() -> None:
            nonlocal resolved_count
            store = getattr(self, "_data_default", None)
            if not isinstance(store, dict):
                return
            root = store.get("persona_routing_warnings")
            if not isinstance(root, dict):
                return
            items = root.get("items")
            if not isinstance(items, list):
                return
            for item in items:
                if not self._persona_routing_warning_is_active(item):
                    continue
                item_channel = _single_line(item.get("channel"), 24)
                item_window = _single_line(item.get("window_key"), 180)
                family = _single_line(item.get("warning_family"), 80) or self._persona_routing_warning_family(
                    item.get("code"), item_channel
                )
                if item_channel != normalized_channel or item_window != window or family not in families:
                    continue
                item.update(
                    {
                        "schema_version": 2,
                        "warning_family": family,
                        "status": "resolved",
                        "resolved_ts": now,
                    }
                )
                resolved_count += 1
            if resolved_count:
                root["schema_version"] = 2

        if isinstance(lock, asyncio.Lock):
            async with lock:
                await update()
        else:
            await update()
        if resolved_count:
            scheduler = getattr(self, "_schedule_default_data_save", None)
            if callable(scheduler):
                clear_token = _ACTIVE_PERSONA_ID.set("")
                try:
                    scheduler(sections={"persona_routing_warnings"}, delay=0.2)
                finally:
                    _ACTIVE_PERSONA_ID.reset(clear_token)
        return resolved_count
