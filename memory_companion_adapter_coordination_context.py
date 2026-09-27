# -*- coding: utf-8 -*-
"""MemoryCompanionAdapterCoordinationContextMixin。

由 tools/split_mixin_domain.py 从 memory_companion_adapter.py 机械抽取（12 个方法 + 0 个模块级名字 + 0 个类级赋值 / 339 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 MemoryCompanionAdapterMixin）。
"""
from __future__ import annotations

from .helpers import _single_address, _single_line
from .memory_companion_adapter_shared import logger
from typing import Any



class MemoryCompanionAdapterCoordinationContextMixin:
    """MemoryCompanionAdapterCoordinationContextMixin（从 MemoryCompanionAdapterMixin 拆出）。"""


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
        preferred_address = _single_address(user.get("nickname"), 24) if isinstance(user, dict) else ""
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
