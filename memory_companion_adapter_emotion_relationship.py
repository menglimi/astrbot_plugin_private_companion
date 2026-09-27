# -*- coding: utf-8 -*-
"""MemoryCompanionAdapterEmotionRelationshipMixin。

由 tools/split_mixin_domain.py 从 memory_companion_adapter.py 机械抽取（8 个方法 + 0 个模块级名字 + 0 个类级赋值 / 414 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 MemoryCompanionAdapterMixin）。
"""
from __future__ import annotations

import asyncio
import uuid
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from .memory_companion_adapter_shared import logger
from typing import Any



class MemoryCompanionAdapterEmotionRelationshipMixin:
    """MemoryCompanionAdapterEmotionRelationshipMixin（从 MemoryCompanionAdapterMixin 拆出）。"""


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
