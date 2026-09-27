# -*- coding: utf-8 -*-
"""心情/低频触达域。

由 tools/split_mixin_domain.py 从 proactive_engine.py 机械抽取（4 个方法 + 6 个模块级名字 + 0 个类级赋值 / 305 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveEngineMixin）。
"""
from __future__ import annotations
from .proactive_engine_shared import _engine_host

import hashlib
import random
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from datetime import datetime
from typing import Any



MOOD_CHECKIN_PROBABILITY = 0.48

MEMORY_ECHO_PROBABILITY = 0.24

ABSENCE_MISS_PROBABILITY = 0.56

MEMORY_ECHO_MIN_SILENCE_SECONDS = 8 * 3600

MOOD_CHECKIN_MIN_SILENCE_SECONDS = 8 * 3600

ABSENCE_MISS_MIN_PROACTIVE_SILENCE_SECONDS = 36 * 3600


class ProactiveEngineMoodMixin:
    """心情/低频触达域（从 ProactiveEngineMixin 拆出）。"""


    def _pick_mood_checkin_event(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """Follow up only on a clearly negative user-state residue from yesterday."""
        if self._private_user_role(user) != "owner":
            return None
        check_now = _engine_host._now_ts() if now is None else now
        summary = self.data.get("yesterday_conversation_summary", {})
        if (
            not isinstance(summary, dict)
            or summary.get("date") != _today_key()
            or summary.get("scope") != "owner_private_only"
            or _safe_int(summary.get("raw_excerpt_chars"), 0, 0) <= 0
        ):
            return None
        residues = summary.get("residues")
        items = [item for item in residues if isinstance(item, dict)] if isinstance(residues, list) else []
        negative_tokens = (
            "低落", "焦虑", "难受", "压力", "紧张", "失眠", "疲惫", "不舒服",
            "担心", "烦躁", "委屈", "害怕", "心情不好", "身体不适",
        )
        candidates = [
            item
            for item in items
            if _single_line(item.get("content"), 140)
            and (
                _single_line(item.get("type"), 24) in {"情绪", "身体"}
                or any(token in _single_line(item.get("content"), 140) for token in negative_tokens)
            )
        ]
        if not candidates:
            return None
        source_date = _single_line(summary.get("source_date"), 20)
        residue = candidates[0]
        residue_text = _single_line(residue.get("content"), 140)
        check_key = hashlib.sha1(f"{source_date}|{residue_text}".encode("utf-8")).hexdigest()[:20]
        if check_key in {
            _single_line(user.get("last_mood_checkin_key"), 40),
            _single_line(user.get("mood_checkin_checked_key"), 40),
        }:
            return None
        if _safe_int(user.get("ignored_streak"), 0, 0) > 0:
            return None
        last_user_at = _safe_float(user.get("last_user_message_at"), 0)
        if last_user_at <= 0 or check_now - last_user_at > 3 * 24 * 3600:
            return None
        last_sent = _safe_float(user.get("last_sent"), 0)
        if last_sent > 0 and check_now - last_sent < MOOD_CHECKIN_MIN_SILENCE_SECONDS:
            return None
        user["mood_checkin_checked_key"] = check_key
        if _engine_host.random.random() > MOOD_CHECKIN_PROBABILITY:
            return None
        scheduled = self._move_timestamp_into_reason_window(
            check_now + _engine_host.random.randint(25, 110) * 60,
            "mood_checkin",
            user,
        )
        context = {
            "check_key": check_key,
            "source_date": source_date,
            "residue_type": _single_line(residue.get("type"), 24) or "情绪",
            "residue": residue_text,
        }
        return {
            "window": self._window_from_delay_minutes(max(5, int((scheduled - check_now) / 60)), width_minutes=50),
            "reason": "mood_checkin",
            "action": "message",
            "why": "昨天对方明确留下了一点负面情绪或身体状态，隔天轻声接一下",
            "topic": residue_text,
            "motive": "还惦记昨天那点不舒服，想轻轻问一句今天有没有好一点",
            "_scheduled_ts": scheduled,
            "context_key": "mood_checkin_context",
            "context": context,
            "origin_event_id": f"mood_checkin:{check_key}",
        }

    def _pick_corrected_memory_echo_event(
        self,
        user: dict[str, Any],
        *,
        now: float,
    ) -> dict[str, Any] | None:
        correction_getter = getattr(self, "_recent_memory_correction_for_echo", None)
        correction = correction_getter(user, now=now) if callable(correction_getter) else {}
        if not isinstance(correction, dict) or not correction:
            return None
        correction_text = _single_line(correction.get("text"), 180)
        correction_key = _single_line(correction.get("correction_key"), 40)
        if not correction_text or not correction_key:
            return None
        echo_key = f"correction:{correction_key}"
        if echo_key in {
            _single_line(user.get("last_memory_echo_key"), 40),
            _single_line(user.get("memory_echo_checked_key"), 40),
        }:
            return None
        if _safe_int(user.get("ignored_streak"), 0, 0) > 0:
            return None
        last_user_at = _safe_float(user.get("last_user_message_at"), 0)
        if last_user_at <= 0 or now - last_user_at > 7 * 24 * 3600:
            return None
        last_sent = _safe_float(user.get("last_sent"), 0)
        if last_sent > 0 and now - last_sent < MEMORY_ECHO_MIN_SILENCE_SECONDS:
            return None
        user["memory_echo_checked_key"] = echo_key
        if _engine_host.random.random() > 0.18:
            return None
        scheduled = self._move_timestamp_into_reason_window(
            now + _engine_host.random.randint(45, 180) * 60,
            "memory_echo",
            user,
        )
        context = {
            "echo_key": echo_key,
            "source_date": datetime.fromtimestamp(
                _safe_float(correction.get("at"), now)
            ).date().isoformat(),
            "summary": "用户后来纠正了 Bot 对一件事的记忆或事实归属",
            "residue_type": "修正版记忆",
            "residue": correction_text,
            "correction": correction_text,
            "strength": "中",
        }
        return {
            "window": self._window_from_delay_minutes(
                max(5, int((scheduled - now) / 60)),
                width_minutes=55,
            ),
            "reason": "memory_echo",
            "action": "message",
            "why": "之前记岔过一件事并被用户纠正，现在想以修正版轻轻承接一次",
            "topic": correction_text,
            "motive": "想起之前被纠正的那件事，想让对方知道这次记住的是修正版",
            "_scheduled_ts": scheduled,
            "context_key": "memory_echo_context",
            "context": context,
            "origin_event_id": f"memory_echo:{echo_key}",
        }

    def _pick_memory_echo_event(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """Build a low-frequency echo grounded in yesterday's owner-private summary."""
        if self._private_user_role(user) != "owner":
            return None
        check_now = _engine_host._now_ts() if now is None else now
        corrected_echo = ProactiveEngineMoodMixin._pick_corrected_memory_echo_event(
            self,
            user,
            now=check_now,
        )
        if corrected_echo is not None:
            return corrected_echo
        summary = self.data.get("yesterday_conversation_summary", {})
        if (
            not isinstance(summary, dict)
            or summary.get("date") != _today_key()
            or summary.get("scope") != "owner_private_only"
            or _safe_int(summary.get("raw_excerpt_chars"), 0, 0) <= 0
        ):
            return None
        source_date = _single_line(summary.get("source_date"), 20)
        overview = _single_line(summary.get("summary"), 180)
        residues = summary.get("residues")
        residue_items = [item for item in residues if isinstance(item, dict)] if isinstance(residues, list) else []
        residue_items = [item for item in residue_items if _single_line(item.get("content"), 140)]
        if not source_date or not overview or "暂无可用" in overview or not residue_items:
            return None
        negative_state_tokens = (
            "低落", "焦虑", "难受", "压力", "紧张", "失眠", "疲惫", "不舒服",
            "担心", "烦躁", "委屈", "害怕", "心情不好", "身体不适",
        )
        if any(
            any(token in _single_line(item.get("content"), 140) for token in negative_state_tokens)
            for item in residue_items
        ):
            return None
        signature_source = "|".join(
            [source_date, overview]
            + [_single_line(item.get("content"), 140) for item in residue_items[:4]]
        )
        echo_key = hashlib.sha1(signature_source.encode("utf-8")).hexdigest()[:20]
        if echo_key in {
            _single_line(user.get("last_memory_echo_key"), 40),
            _single_line(user.get("memory_echo_checked_key"), 40),
        }:
            return None
        if _safe_int(user.get("ignored_streak"), 0, 0) > 0:
            return None
        last_user_at = _safe_float(user.get("last_user_message_at"), 0)
        if last_user_at <= 0 or check_now - last_user_at > 7 * 24 * 3600:
            return None
        last_sent = _safe_float(user.get("last_sent"), 0)
        if last_sent > 0 and check_now - last_sent < MEMORY_ECHO_MIN_SILENCE_SECONDS:
            return None
        user["memory_echo_checked_key"] = echo_key
        if _engine_host.random.random() > MEMORY_ECHO_PROBABILITY:
            return None
        strength_rank = {"强": 3, "中": 2, "轻": 1}
        residue = max(
            residue_items,
            key=lambda item: strength_rank.get(_single_line(item.get("strength"), 8), 0),
        )
        residue_text = _single_line(residue.get("content"), 140)
        residue_type = _single_line(residue.get("type"), 24) or "聊天余韵"
        scheduled = self._move_timestamp_into_reason_window(
            check_now + _engine_host.random.randint(35, 150) * 60,
            "memory_echo",
            user,
        )
        context = {
            "echo_key": echo_key,
            "source_date": source_date,
            "summary": overview,
            "residue_type": residue_type,
            "residue": residue_text,
            "strength": _single_line(residue.get("strength"), 8) or "轻",
        }
        return {
            "window": self._window_from_delay_minutes(
                max(5, int((scheduled - check_now) / 60)),
                width_minutes=55,
            ),
            "reason": "memory_echo",
            "action": "message",
            "why": "昨天聊过的一件小事今天又自然浮上来，想轻轻接一下，不要求对方回应",
            "topic": residue_text,
            "motive": f"想起昨天留下的{residue_type}，想顺手提一句",
            "_scheduled_ts": scheduled,
            "context_key": "memory_echo_context",
            "context": context,
            "origin_event_id": f"memory_echo:{echo_key}",
        }

    def _pick_absence_miss_event(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """Express one low-pressure miss when silence is not an ignored bot message."""
        if self._private_user_role(user) != "owner":
            return None
        check_now = _engine_host._now_ts() if now is None else now
        last_user_at = _safe_float(user.get("last_user_message_at"), 0)
        if last_user_at <= 0:
            return None
        absent_days = (check_now - last_user_at) / 86400
        if absent_days < 3 or absent_days > 21:
            return None
        if _safe_int(user.get("ignored_streak"), 0, 0) > 0:
            return None
        # ``last_sent`` also moves for passive replies.  Only a proactive send
        # should make this look like an unanswered bot message.
        last_proactive_sent = _safe_float(user.get("last_proactive_sent_at"), 0)
        if last_proactive_sent > last_user_at or (
            last_proactive_sent > 0 and check_now - last_proactive_sent < ABSENCE_MISS_MIN_PROACTIVE_SILENCE_SECONDS
        ):
            return None
        relation_mode_getter = getattr(self, "_current_relationship_gate_mode", None)
        emotion_mode_getter = getattr(self, "_current_emotion_gate_mode", None)
        relation_mode = relation_mode_getter(user, now=check_now) if callable(relation_mode_getter) else ""
        emotion_mode = emotion_mode_getter(user, now=check_now) if callable(emotion_mode_getter) else ""
        if relation_mode in {"refusing", "backoff", "hurt", "avoidant"} or emotion_mode in {"hurt", "avoidant"}:
            return None
        episode_key = hashlib.sha1(f"{int(last_user_at)}".encode("utf-8")).hexdigest()[:20]
        if episode_key in {
            _single_line(user.get("last_absence_miss_key"), 40),
            _single_line(user.get("absence_miss_checked_key"), 40),
        }:
            return None
        user["absence_miss_checked_key"] = episode_key
        if _engine_host.random.random() > ABSENCE_MISS_PROBABILITY:
            return None
        scheduled = self._move_timestamp_into_reason_window(
            check_now + _engine_host.random.randint(20, 100) * 60,
            "absence_miss",
            user,
        )
        context = {
            "episode_key": episode_key,
            "last_user_message_at": last_user_at,
            "absent_days": round(absent_days, 1),
        }
        return {
            "window": self._window_from_delay_minutes(max(5, int((scheduled - check_now) / 60)), width_minutes=55),
            "reason": "absence_miss",
            "action": "message",
            "why": "双方自然停聊了几天，且没有一条未回复的 Bot 主动消息，想低压力地表达一点想念",
            "topic": "隔了几天没聊留下的一点想念",
            "motive": "有点想念对方，但不想让这句话变成催回复",
            "_scheduled_ts": scheduled,
            "context_key": "absence_miss_context",
            "context": context,
            "origin_event_id": f"absence_miss:{episode_key}",
        }
