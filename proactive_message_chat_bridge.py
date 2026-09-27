# -*- coding: utf-8 -*-
"""chat_bridge 域。

由 tools/split_mixin_domain.py 从 proactive_message.py 机械抽取（8 个方法 + 0 个模块级名字 + 0 个类级赋值 / 570 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageMixin）。
"""
from __future__ import annotations

import sys
import uuid
from .conversation_prompt_section import PromptRenderMode, exact_text, prompt_section, render_prompt_sections
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .persona_config import runtime_persona_setting
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)





# ---- 宿主 patch 兼容层（由 tools/inject_host_patch_shim.py 注入）----
# PyTest 里 patch("...proactive_message.<name>") 期望改动能被本模块感知。
# 原 import 会被下面的同名函数覆盖，方法体调用时实时转发到宿主模块。
def _now_ts(*args, **kwargs):
    from . import proactive_message as _host
    return getattr(_host, "_now_ts")(*args, **kwargs)

class ProactiveMessageChatBridgeMixin:
    """chat_bridge 域（从 ProactiveMessageMixin 拆出）。"""


    def _proactive_chat_bridge_user(self, session_id: str) -> tuple[str, dict[str, Any] | None]:
        session = _single_line(session_id, 180)
        if ":FriendMessage:" not in session:
            return "", None
        resolver = getattr(self, "_input_status_user_id_from_umo", None)
        user_id = resolver(session) if callable(resolver) else ""
        if not user_id:
            user_id = _single_line(session.split(":FriendMessage:", 1)[-1], 100)
        canonicalizer = getattr(self, "_canonical_private_user_id", None)
        if callable(canonicalizer):
            user_id = str(canonicalizer(user_id) or "").strip()
        users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else None
        user = users.get(user_id) if isinstance(users, dict) else None
        snapshot_getter = getattr(self, "_req041_relationship_snapshot_view", None)
        if isinstance(user, dict) and callable(snapshot_getter):
            user = snapshot_getter(user, source="proactive_chat_bridge")
        return user_id, user if isinstance(user, dict) else None

    def _proactive_chat_decorating_context(self) -> dict[str, Any]:
        """Read Proactive Chat's send context, preferring the deep runtime attempt."""
        context: dict[str, Any] = {
            "detected": False,
            "deep_bridge": False,
            "attempt_id": "",
            "token": "",
            "full_text": "",
            "tts_sent": False,
            "segment_index": 0,
            "segment_count": 1,
        }
        runtime_context: dict[str, Any] = {}
        runtime_bridge = getattr(self, "_proactive_chat_runtime_bridge", None)
        if runtime_bridge is not None:
            try:
                runtime_context = runtime_bridge.outbound_context()
            except Exception:
                runtime_context = {}
        frame = sys._getframe()
        try:
            for _ in range(48):
                frame = frame.f_back
                if frame is None:
                    break
                module_name = str(frame.f_globals.get("__name__", "") or "")
                if "astrbot_plugin_proactive_chat" not in module_name:
                    continue
                context["detected"] = True
                if frame.f_code.co_name != "_send_proactive_message":
                    continue
                local_values = frame.f_locals
                context["attempt_id"] = f"proactive-chat-{id(frame):x}"
                context["full_text"] = str(local_values.get("text") or "").strip()
                context["tts_sent"] = bool(local_values.get("is_tts_sent", False))
                segments = local_values.get("segments")
                if isinstance(segments, list) and segments:
                    context["segment_count"] = max(1, len(segments))
                    try:
                        context["segment_index"] = max(
                            0,
                            min(
                                int(local_values.get("idx", 0) or 0),
                                context["segment_count"] - 1,
                            ),
                        )
                    except Exception:
                        context["segment_index"] = 0
        finally:
            del frame
        if runtime_context:
            context.update(
                {
                    "detected": True,
                    "deep_bridge": True,
                    "attempt_id": _single_line(runtime_context.get("attempt_id"), 100),
                    "token": _single_line(runtime_context.get("token"), 80),
                    "full_text": str(runtime_context.get("full_text") or context.get("full_text") or "").strip(),
                }
            )
        return context

    def _proactive_chat_bridge_review_cache(self) -> dict[str, dict[str, Any]]:
        cache = getattr(self, "_proactive_chat_bridge_reviews", None)
        if not isinstance(cache, dict):
            cache = {}
            setattr(self, "_proactive_chat_bridge_reviews", cache)
        now = _now_ts()
        stale = [
            key
            for key, item in cache.items()
            if not isinstance(item, dict) or now - _safe_float(item.get("created_at"), 0) > 10 * 60
        ]
        for key in stale:
            cache.pop(key, None)
        if len(cache) > 80:
            ordered = sorted(
                cache,
                key=lambda key: _safe_float(cache.get(key, {}).get("created_at"), 0),
            )
            for key in ordered[: len(cache) - 80]:
                cache.pop(key, None)
        return cache

    def _proactive_chat_bridge_preflight_block_reason(
        self,
        user: dict[str, Any],
        *,
        now: float,
    ) -> str:
        relationship_gate = getattr(self, "_current_relationship_gate_mode", None)
        emotion_gate = getattr(self, "_current_emotion_gate_mode", None)
        try:
            if callable(relationship_gate) and relationship_gate(user, now=now) == "backoff":
                return "expression_contact_boundary"
            if callable(emotion_gate) and emotion_gate(user, now=now) == "hurt":
                return "expression_interaction_hurt"
        except Exception:
            return "expression_boundary_check_unavailable"
        rest_gate = getattr(self, "_proactive_rest_block_until", None)
        if callable(rest_gate):
            try:
                if _safe_float(
                    rest_gate(
                        user,
                        now=now,
                        reason="check_in",
                        source="proactive_chat",
                    ),
                    0,
                ) > now:
                    return "user_explicit_rest"
            except Exception:
                pass
        busy_gate = getattr(self, "_busy_reply_proactive_block_until", None)
        if callable(busy_gate):
            try:
                if _safe_float(
                    busy_gate(
                        user,
                        now=now,
                        reason="check_in",
                        source="proactive_chat",
                    ),
                    0,
                ) > now:
                    return "bot_busy_schedule"
            except Exception:
                pass
        quiet_checker = getattr(self, "_is_quiet_time", None)
        insomnia_checker = getattr(self, "_can_send_insomnia_night_message", None)
        quiet = False
        try:
            quiet = bool(quiet_checker()) if callable(quiet_checker) else False
            insomnia_allowed = bool(insomnia_checker(user)) if callable(insomnia_checker) else False
            if quiet and not insomnia_allowed:
                return "private_companion_quiet_hours"
        except Exception:
            pass
        daily_limit_getter = getattr(self, "_effective_user_daily_limit", None)
        unlimited_checker = getattr(self, "_proactive_daily_limit_is_unlimited", None)
        reset_daily = getattr(self, "_reset_daily_counter_if_needed", None)
        try:
            if callable(reset_daily):
                reset_daily(user)
            if not callable(daily_limit_getter):
                return "expression_allowance_unavailable"
            daily_limit = _safe_int(daily_limit_getter(user), 0, 0)
            unlimited = bool(unlimited_checker(daily_limit)) if callable(unlimited_checker) else False
            if daily_limit <= 0:
                return "expression_proactive_budget_zero"
            if not unlimited and _safe_int(user.get("sent_today"), 0, 0) >= daily_limit:
                return "expression_proactive_budget_exhausted"
        except Exception:
            return "expression_allowance_unavailable"
        expression_builder = getattr(self, "_build_expression_decision_for_user", None)
        try:
            if not callable(expression_builder):
                return "expression_decision_unavailable"
            decision = expression_builder(
                user,
                proactive_candidate={"eligible": True, "dynamic_allowance": daily_limit, "current_ts": now},
                schedule={"quiet_hours": quiet},
                message_intent={"requested_content_tier": "normal"},
                now=now,
            )
            projection = decision.to_dict() if hasattr(decision, "to_dict") else dict(decision or {})
            if _single_line(projection.get("blocker"), 40):
                return f"expression_{_single_line(projection.get('blocker'), 40)}"
            if _safe_int(projection.get("proactive_budget"), 0, 0) <= 0:
                return "expression_proactive_budget_zero"
        except Exception:
            return "expression_decision_unavailable"
        collision_window = max(
            10,
            min(
                600,
                _safe_int(
                    runtime_persona_setting(
                        self, "proactive_chat_bridge_collision_window_seconds", 90
                    ),
                    90,
                    10,
                    600,
                ),
            ),
        )
        last_sent = max(
            _safe_float(user.get("last_sent"), 0),
            _safe_float(user.get("last_proactive_sent_at"), 0),
            _safe_float(user.get("proactive_chat_bridge_last_sent_at"), 0),
        )
        if last_sent > 0 and now - last_sent < collision_window:
            return "recent_proactive_collision_window"
        return ""

    async def _prepare_proactive_chat_bridge(
        self,
        session_id: str,
        *,
        unanswered_count: int = 0,
    ) -> dict[str, Any]:
        if not bool(runtime_persona_setting(self, "enable_proactive_chat_integration", True)):
            return {"enabled": False, "allowed": True, "reason": "integration_disabled"}
        user_id, user = self._proactive_chat_bridge_user(session_id)
        if not user_id or not isinstance(user, dict):
            return {"enabled": False, "allowed": True, "reason": "private_user_not_managed"}
        enabled_checker = getattr(self, "_user_enabled_for_proactive", None)
        if callable(enabled_checker) and not enabled_checker(user_id, user):
            return {"enabled": True, "allowed": False, "reason": "private_user_disabled"}
        now = _now_ts()
        if bool(user.get("proactive_sending")):
            recover = getattr(self, "_recover_stale_proactive_sending", None)
            if callable(recover):
                recover(user, now=now)
        if bool(user.get("proactive_sending")):
            return {"enabled": True, "allowed": False, "reason": "another_proactive_message_is_sending"}
        preflight_block = self._proactive_chat_bridge_preflight_block_reason(user, now=now)
        if preflight_block:
            return {"enabled": True, "allowed": False, "reason": preflight_block}
        runtime_formatter = getattr(self, "_format_proactive_review_runtime_context", None)
        runtime_context = runtime_formatter(user, now=now) if callable(runtime_formatter) else ""
        identity_formatter = getattr(self, "_format_proactive_recipient_identity_guard", None)
        recipient_identity = (
            identity_formatter(user, _single_line(user.get("nickname"), 40))
            if callable(identity_formatter)
            else ""
        )
        voice_formatter = getattr(self, "_format_proactive_voice_prompt", None)
        proactive_voice = voice_formatter() if callable(voice_formatter) else ""
        expression_formatter = getattr(self, "_format_expression_voice_for_prompt", None)
        expression_voice = (
            expression_formatter(
                scope="proactive",
                target_id=user_id,
                context_owner=user,
                stage_owner=user,
            )
            if callable(expression_formatter)
            else ""
        )
        relationship_formatter = getattr(self, "_format_proactive_relationship_fact", None)
        try:
            relationship_context = relationship_formatter(user) if callable(relationship_formatter) else ""
        except Exception:
            relationship_context = ""
        intent_formatter = getattr(self, "_format_intent_relationship_injection", None)
        try:
            intent_context = intent_formatter(user) if callable(intent_formatter) else ""
        except Exception:
            intent_context = ""
        time_formatter = getattr(self, "_format_time_period_injection", None)
        try:
            time_context = time_formatter() if callable(time_formatter) else ""
        except Exception:
            time_context = ""
        state_context = ""
        state_formatter = getattr(self, "_format_state_for_framework_prompt", None)
        daily_state = self.data.get("daily_state") if isinstance(getattr(self, "data", None), dict) else None
        if callable(state_formatter):
            try:
                state_context = state_formatter(
                    daily_state if isinstance(daily_state, dict) else {},
                    reason="check_in",
                    action="message",
                )
            except Exception:
                state_context = ""
        location_formatter = getattr(self, "_format_mobile_user_location_context_for_proactive", None)
        try:
            location_context = location_formatter(user) if callable(location_formatter) else ""
        except Exception:
            location_context = ""
        schedule_context = ""
        include_schedule = runtime_persona_setting(self, "include_schedule_in_messages", True)
        schedule_formatter = getattr(self, "_format_schedule_context_for_prompt", None)
        if include_schedule and callable(schedule_formatter):
            try:
                schedule_context = str(schedule_formatter() or "").strip()
            except Exception:
                schedule_context = ""
        schedule_sanitizer = getattr(self, "_sanitize_schedule_context_for_private_user", None)
        if include_schedule and schedule_context and callable(schedule_sanitizer):
            try:
                schedule_context = str(schedule_sanitizer(schedule_context, user) or "").strip()
            except Exception:
                schedule_context = ""
        def labeled_bridge_section(key: str, title: str, content: str) -> str:
            return render_prompt_sections(
                [
                    prompt_section(
                        key=key,
                        title=title,
                        source="proactive_message",
                        content=content,
                    )
                ],
                mode=PromptRenderMode.LABELED_BLOCK,
            )

        bridge_intro = labeled_bridge_section(
            "proactive_chat.bridge",
            "Private Companion × Proactive Chat 联动",
            (
                "这是一条由 Proactive Chat 定时触发的主动消息，不是在回复用户刚发来的话。\n"
                f"当前连续未回应次数：{max(0, int(unanswered_count or 0))}。不要因此质问、催促或制造负担。"
            ),
        )
        fragment = "\n".join(
            part
            for part in (
                bridge_intro,
                recipient_identity,
                labeled_bridge_section("proactive_chat.relationship", "当前关系", relationship_context) if relationship_context else "",
                labeled_bridge_section("proactive_chat.atmosphere", "当前互动气氛", _single_line(intent_context, 360)) if intent_context else "",
                labeled_bridge_section("proactive_chat.timing", "当前时机", time_context) if time_context else "",
                labeled_bridge_section("proactive_chat.runtime", "当前运行态", runtime_context) if runtime_context else "",
                labeled_bridge_section("proactive_chat.state", "Bot 当前状态底色", state_context) if state_context else "",
                location_context,
                labeled_bridge_section("proactive_chat.schedule", "当前生活片段候选", schedule_context[:700]) if schedule_context and schedule_context != "（暂无）" else "",
                proactive_voice,
                expression_voice,
                "先沿用 Proactive Chat 本轮自己的主动动机，再从以上信息中只取与当前收件人和此刻真正相关的少量线索；不要拼成状态播报。",
                "只吸收与当前收件人、关系和时机有关的内容；不要提到插件、调度器、状态字段或联动过程。",
            )
            if str(part or "").strip()
        )
        token = uuid.uuid4().hex
        lock = getattr(self, "_data_lock", None)
        if lock is not None:
            async with lock:
                current = self._get_user(user_id)
                if current.get("proactive_sending"):
                    return {"enabled": True, "allowed": False, "reason": "another_proactive_message_is_sending"}
                preflight_block = self._proactive_chat_bridge_preflight_block_reason(current, now=now)
                if preflight_block:
                    return {"enabled": True, "allowed": False, "reason": preflight_block}
                current["proactive_sending"] = True
                current["proactive_sending_started_at"] = now
                current["proactive_chat_bridge_token"] = token
                current["proactive_chat_bridge_session"] = _single_line(session_id, 180)
                self._save_data_sync(sections={"users"})
        return {
            "enabled": True,
            "allowed": True,
            "token": token,
            "prompt_fragment": fragment[:5200].strip(),
            "prompt_fragment_section": prompt_section(
                key="proactive_chat.bridge.fragment",
                title="Private Companion × Proactive Chat 联动",
                source="proactive_message",
                content=exact_text(fragment[:5200].strip()),
                metadata={"wire": "legacy_exact"},
            ),
            "user_id": user_id,
        }

    async def _review_proactive_chat_bridge_message(
        self,
        session_id: str,
        text: str,
        *,
        token: str = "",
        attempt_id: str = "",
    ) -> dict[str, Any]:
        if not bool(runtime_persona_setting(self, "enable_proactive_chat_integration", True)):
            return {"ok": True, "text": str(text or "").strip(), "reason": "integration_disabled"}
        user_id, user = self._proactive_chat_bridge_user(session_id)
        if not user_id or not isinstance(user, dict):
            return {"ok": True, "text": str(text or "").strip(), "reason": "bridge_not_managed"}
        enabled_checker = getattr(self, "_user_enabled_for_proactive", None)
        if callable(enabled_checker) and not enabled_checker(user_id, user):
            return {"ok": True, "text": str(text or "").strip(), "reason": "bridge_not_managed"}
        expected = _single_line(user.get("proactive_chat_bridge_token"), 80)
        if expected and token != expected:
            return {"ok": False, "text": "", "reason": "bridge_token_mismatch"}
        cache_key = _single_line(attempt_id, 100)
        if cache_key:
            cached = self._proactive_chat_bridge_review_cache().get(cache_key)
            if isinstance(cached, dict) and cached.get("session_id") == _single_line(session_id, 180):
                result = cached.get("result")
                if isinstance(result, dict):
                    return dict(result)
        cleaned = self._sanitize_proactive_text(str(text or "").strip())
        if not cleaned:
            return {"ok": False, "text": "", "reason": "empty_after_sanitize"}
        local_checker = getattr(self, "_local_proactive_send_decision", None)
        decision: dict[str, Any] = {"decision": "send", "reason": "local_checker_unavailable"}
        if callable(local_checker):
            decision = local_checker(
                user,
                cleaned,
                reason="proactive_chat_bridge",
                action="message",
                motive="由 Proactive Chat 提供更即时的主动触发",
                topic="",
                action_context="没有外部图片或工具结果",
            )
        review_mode = str(
            runtime_persona_setting(self, "proactive_chat_bridge_review_mode", "local")
            or "local"
        ).strip().lower()
        full_reviewer = getattr(self, "_review_proactive_message_send_decision", None)
        if (
            review_mode == "follow_proactive_review"
            and bool(runtime_persona_setting(self, "enable_proactive_message_review", True))
            and callable(full_reviewer)
            and str(runtime_persona_setting(self, "proactive_review_mode", "full") or "full")
            .strip()
            .lower()
            != "local_only"
        ):
            try:
                decision = await full_reviewer(
                    user,
                    cleaned,
                    reason="proactive_chat_bridge",
                    action="message",
                    motive="由 Proactive Chat 提供更即时的主动触发",
                    topic="",
                    action_summary="Proactive Chat 已生成纯文本主动候选；没有外部图片或工具结果",
                )
            except Exception as exc:
                logger.warning(
                    "Proactive Chat 联动终审失败，已回退本地检查: %s",
                    _single_line(exc, 160),
                )

        action = str(decision.get("decision") or "send").strip().lower()
        if action in {"drop", "defer"}:
            result = {
                "ok": False,
                "text": "",
                "reason": _single_line(decision.get("reason"), 160) or action,
                "decision": action,
            }
        else:
            if action == "rewrite" and _single_line(decision.get("text"), 500):
                cleaned = self._sanitize_proactive_text(_single_line(decision.get("text"), 500))
            result = {
                "ok": bool(cleaned),
                "text": cleaned,
                "reason": _single_line(decision.get("reason"), 160)
                or ("private_companion_model_review" if review_mode == "follow_proactive_review" else "private_companion_local_review"),
                "decision": action if action in {"send", "rewrite"} else "send",
            }
        if cache_key:
            self._proactive_chat_bridge_review_cache()[cache_key] = {
                "created_at": _now_ts(),
                "session_id": _single_line(session_id, 180),
                "result": dict(result),
            }
        return result

    async def _record_proactive_chat_bridge_sent(
        self,
        session_id: str,
        text: str,
        *,
        token: str = "",
        attempt_id: str = "",
    ) -> dict[str, Any]:
        if not bool(runtime_persona_setting(self, "enable_proactive_chat_integration", True)):
            return {"recorded": False, "reason": "integration_disabled"}
        user_id, user = self._proactive_chat_bridge_user(session_id)
        if not user_id or not isinstance(user, dict):
            return {"recorded": False, "reason": "private_user_not_managed"}
        enabled_checker = getattr(self, "_user_enabled_for_proactive", None)
        if callable(enabled_checker) and not enabled_checker(user_id, user):
            return {"recorded": False, "reason": "private_user_not_managed"}
        now = _now_ts()
        visible_formatter = getattr(self, "_visible_text_without_tts_reading", None)
        visible = (
            visible_formatter(text, limit=500)
            if callable(visible_formatter)
            else _single_line(text, 500)
        )
        lock = getattr(self, "_data_lock", None)
        if lock is not None:
            async with lock:
                current = self._get_user(user_id)
                expected = _single_line(current.get("proactive_chat_bridge_token"), 80)
                if expected and token != expected:
                    return {"recorded": False, "reason": "bridge_token_mismatch"}
                normalized_attempt = _single_line(attempt_id, 100)
                if normalized_attempt and _single_line(current.get("proactive_chat_bridge_last_attempt_id"), 100) == normalized_attempt:
                    return {"recorded": False, "reason": "duplicate_attempt", "user_id": user_id}
                if expected:
                    current["proactive_sending"] = False
                    current["proactive_sending_started_at"] = 0
                current["proactive_chat_bridge_token"] = ""
                current["proactive_chat_bridge_session"] = ""
                current["last_sent"] = now
                current["last_proactive_sent_at"] = now
                current["last_proactive_message"] = visible
                current["last_companion_message"] = visible
                current["last_companion_message_at"] = now
                current["last_proactive_delivery_umo"] = _single_line(session_id, 180)
                current["last_proactive_delivery_inbound_count"] = _safe_int(current.get("inbound_count"), 0)
                current["last_proactive_reply_context_consumed_for"] = 0
                current["last_proactive_reason"] = "proactive_chat_bridge"
                current["last_proactive_action"] = "message"
                current["last_proactive_motive"] = "Proactive Chat 即时触发"
                current["last_proactive_source"] = "proactive_chat"
                current["proactive_chat_bridge_last_sent_at"] = now
                current["proactive_chat_bridge_last_attempt_id"] = normalized_attempt
                reset_daily = getattr(self, "_reset_daily_counter_if_needed", None)
                if callable(reset_daily):
                    reset_daily(current)
                elif str(current.get("sent_day") or "") != _today_key():
                    current["sent_day"] = _today_key()
                    current["sent_today"] = 0
                current["sent_today"] = _safe_int(current.get("sent_today"), 0) + 1
                current["proactive_sent_count"] = _safe_int(current.get("proactive_sent_count"), 0) + 1
                current["ignored_streak"] = _safe_int(current.get("ignored_streak"), 0) + 1
                current["awaiting_reply_since"] = now
                current["pending_followup_event"] = {}
                current["planned_proactive_quota_exempt"] = False
                action_recorder = getattr(self, "_note_action_sent", None)
                if callable(action_recorder):
                    action_recorder(
                        current,
                        "message",
                        reason="proactive_chat_bridge",
                        text=visible or text,
                        motive="Proactive Chat 即时触发",
                        action_summary="外部主动插件已完成发送",
                        source="proactive_chat",
                    )
                topic_recorder = getattr(self, "_remember_proactive_topic", None)
                if callable(topic_recorder):
                    topic_recorder(current, text=visible or text, topic="Proactive Chat", motive="即时主动触发")
                self._save_data_sync(sections={"users"})
        logger.info(
            "已同步 Proactive Chat 主动发送: user=%s session=%s text=%s",
            user_id,
            _single_line(session_id, 120),
            _single_line(visible, 120),
        )
        return {"recorded": True, "user_id": user_id, "sent_at": now}

    async def _cancel_proactive_chat_bridge(self, session_id: str, *, token: str = "") -> bool:
        user_id, user = self._proactive_chat_bridge_user(session_id)
        if not user_id or not isinstance(user, dict):
            return False
        lock = getattr(self, "_data_lock", None)
        if lock is None:
            return False
        async with lock:
            current = self._get_user(user_id)
            expected = _single_line(current.get("proactive_chat_bridge_token"), 80)
            if not expected or token != expected:
                return False
            current["proactive_sending"] = False
            current["proactive_sending_started_at"] = 0
            current["proactive_chat_bridge_token"] = ""
            current["proactive_chat_bridge_session"] = ""
            self._save_data_sync(sections={"users"})
        return True
