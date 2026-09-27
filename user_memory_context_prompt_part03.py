# -*- coding: utf-8 -*-
"""UserMemoryContextPromptPart03Mixin。

由 tools/split_mixin_domain.py 从 user_memory_context_prompt.py 机械抽取（10 个方法 + 0 个模块级名字 + 0 个类级赋值 / 226 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryContextPromptMixin）。
"""
from __future__ import annotations
from .user_memory_context_prompt_shared import Any
from .user_memory_context_prompt_shared import _now_ts
from .user_memory_context_prompt_shared import _safe_float
from .user_memory_context_prompt_shared import _safe_int
from .user_memory_context_prompt_shared import _single_line
from .user_memory_context_prompt_shared import datetime
from .user_memory_context_prompt_shared import hashlib



class UserMemoryContextPromptPart03Mixin:
    """UserMemoryContextPromptPart03Mixin（从 UserMemoryContextPromptMixin 拆出）。"""


    @staticmethod
    def _relationship_analysis_reply_rate_band(proactive_count: int, reply_count: int) -> str:
        if proactive_count <= 0:
            return "no_sample"
        reply_rate = reply_count / proactive_count
        if reply_rate < 0.15:
            return "low"
        if reply_rate < 0.35:
            return "guarded"
        if reply_rate < 0.5:
            return "steady"
        return "warm"

    def _relationship_analysis_metrics(self, user: dict[str, Any]) -> dict[str, Any]:
        proactive_count = _safe_int(user.get("proactive_sent_count"), 0, 0)
        reply_count = _safe_int(user.get("reply_count"), 0, 0)
        inbound_count = _safe_int(user.get("inbound_count"), 0, 0)
        relationship_score = _safe_int(user.get("relationship_score"), 0)
        ignored_streak = _safe_int(user.get("ignored_streak"), 0, 0)
        if ignored_streak >= 4:
            ignored_band = "high"
        elif ignored_streak >= 2:
            ignored_band = "guarded"
        elif ignored_streak == 1:
            ignored_band = "single"
        else:
            ignored_band = "none"
        if relationship_score >= 16:
            score_band = "close"
        elif relationship_score >= 3 or inbound_count >= 1:
            score_band = "familiar"
        else:
            score_band = "new"
        return {
            "inbound_count": inbound_count,
            "proactive_count": proactive_count,
            "reply_count": reply_count,
            "interaction_count": inbound_count + proactive_count,
            "reply_rate_band": self._relationship_analysis_reply_rate_band(proactive_count, reply_count),
            "relationship_score_band": score_band,
            "ignored_streak_band": ignored_band,
            "last_user_message_at": _safe_float(user.get("last_user_message_at"), 0),
        }

    @staticmethod
    def _relationship_analysis_signal(user: dict[str, Any]) -> str:
        intent = user.get("intent_profile")
        if not isinstance(intent, dict):
            return ""
        if not bool(intent.get("boundary_durable")):
            return ""
        if _safe_float(intent.get("confidence"), 0) < 0.82:
            return ""
        seed = "|".join(
            (
                str(_safe_float(user.get("last_user_message_at"), 0)),
                _single_line(user.get("last_user_message"), 240),
                _single_line(intent.get("source"), 40),
            )
        )
        return f"boundary:{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:16]}"

    def _relationship_analysis_refresh_reason(
        self,
        user: dict[str, Any],
        *,
        now: float,
        force: bool = False,
    ) -> str:
        if force:
            return "forced"
        profile = user.get("persona_relationship")
        if not isinstance(profile, dict) or not profile.get("level"):
            return "initial"
        if now < _safe_float(user.get("relationship_retry_after"), 0):
            return ""
        previous_metrics = profile.get("source_metrics")
        analyzed_at = _safe_float(profile.get("analyzed_at_ts"), 0)
        if not isinstance(previous_metrics, dict) or analyzed_at <= 0:
            return "legacy_profile"

        current_signal = self._relationship_analysis_signal(user)
        if current_signal and current_signal != str(profile.get("source_signal") or ""):
            return "durable_boundary"

        metrics = self._relationship_analysis_metrics(user)
        age = max(0.0, now - analyzed_at)
        min_interval = max(
            10.0,
            _safe_float(getattr(self, "relationship_analysis_min_interval_minutes", 45), 45),
        ) * 60
        if (
            metrics["ignored_streak_band"] in {"guarded", "high"}
            and metrics["ignored_streak_band"] != str(previous_metrics.get("ignored_streak_band") or "")
            and age >= min(min_interval, 15 * 60)
        ):
            return "ignored_streak_changed"
        if (
            metrics["relationship_score_band"] != str(previous_metrics.get("relationship_score_band") or "")
            and age >= min_interval
        ):
            return "relationship_stage_changed"
        if (
            metrics["proactive_count"] >= 3
            and metrics["reply_rate_band"] != str(previous_metrics.get("reply_rate_band") or "")
            and age >= min_interval
        ):
            return "reply_rate_changed"

        interaction_delta = max(
            0,
            _safe_int(metrics.get("interaction_count"), 0)
            - _safe_int(previous_metrics.get("interaction_count"), 0),
        )
        message_batch = max(
            4,
            _safe_int(getattr(self, "relationship_analysis_interaction_batch", 8), 8, 1),
        )
        if interaction_delta >= message_batch and age >= min_interval:
            return "interaction_batch"
        max_stale = max(
            min_interval * 2,
            _safe_float(getattr(self, "relationship_analysis_max_stale_hours", 8), 8) * 3600,
        )
        if interaction_delta > 0 and age >= max_stale:
            return "stale_with_new_interaction"
        return ""

    async def _refresh_persona_relationship(
        self,
        user_id: str,
        user: dict[str, Any],
        *,
        trigger: str = "interaction",
        force: bool = False,
    ) -> bool:
        # REQ040 compatibility no-op: this old LLM relationship analyzer is
        # no longer allowed to update any user state.
        return False

    def _format_relationship_summary(self, user: dict[str, Any]) -> str:
        profile = self._relationship_profile(user)
        return (
            f"{profile['level']}｜回复率 {profile['reply_rate_label']}｜"
            f"偏好 {profile['preference']}"
        )

    def _format_action_affinity_summary(self, user: dict[str, Any]) -> str:
        raw = user.get("action_reply_affinity")
        if not isinstance(raw, dict) or not raw:
            return "暂无样本"
        labels = {
            "screen_peek": "窥屏",
            "photo_text": "发图",
            "poke": "戳一戳",
            "voice": "语音",
        }
        parts = []
        for key in ("screen_peek", "photo_text", "poke", "voice"):
            stats = raw.get(key)
            if not isinstance(stats, dict):
                continue
            sent = _safe_int(stats.get("sent"), 0, 0)
            replied = _safe_int(stats.get("replied"), 0, 0)
            if sent <= 0:
                continue
            parts.append(f"{labels[key]} {replied}/{sent}")
        return "｜".join(parts) if parts else "暂无样本"

    def _format_next_proactive(self, user: dict[str, Any]) -> str:
        if self._simulation_active(user):
            sim = user.get("simulation_mode")
            if isinstance(sim, dict):
                events = sim.get("events")
                if isinstance(events, list) and events:
                    item = events[0]
                    if isinstance(item, dict):
                        sim_window = _single_line(item.get("_simulated_window") or item.get("window"), 20)
                        reason = item.get("reason") or "未记录"
                        action = item.get("action") or "message"
                        motive = _single_line(item.get("motive"), 36)
                        prefix = f"模拟 {sim_window}" if sim_window else "模拟下一条"
                        if motive:
                            return f"{prefix}｜{reason}｜{action}｜{motive}"
                        return f"{prefix}｜{reason}｜{action}"
        next_at = _safe_float(user.get("next_proactive_at"), 0)
        if next_at <= 0:
            return "未安排"
        when = datetime.fromtimestamp(next_at).strftime("%m-%d %H:%M")
        reason = user.get("planned_proactive_reason") or "未记录"
        action = user.get("planned_proactive_action") or "message"
        motive = _single_line(user.get("planned_proactive_motive"), 36)
        timer_event = self._get_active_llm_timer(user)
        source_prefix = "模型预约 " if isinstance(timer_event, dict) and _safe_float(timer_event.get("scheduled_ts"), 0) == next_at else ""
        if motive:
            return f"{source_prefix}{when}｜{reason}｜{action}｜{motive}"
        return f"{source_prefix}{when}｜{reason}｜{action}"

    def _format_simulation_summary(self, user: dict[str, Any]) -> str:
        sim = user.get("simulation_mode")
        if not isinstance(sim, dict) or not sim.get("active"):
            return ""
        events = sim.get("events")
        if not isinstance(events, list):
            events = []
        label = self._simulation_label(user)
        lines = [f"{label}：进行中（剩余 {len(events)} 条）"]
        for item in events[:6]:
            if not isinstance(item, dict):
                continue
            sim_window = _single_line(item.get("_simulated_window") or item.get("window"), 20)
            when = f"模拟 {sim_window}" if sim_window else datetime.fromtimestamp(_safe_float(item.get("_scheduled_ts"), _now_ts())).strftime("%H:%M")
            lines.append(
                f"- {when}｜{item.get('reason', '')}｜{item.get('action', 'message')}｜{_single_line(item.get('topic') or item.get('motive'), 28)}"
            )
        return "\n".join(lines)

    def _format_user_profile(self, user: dict[str, Any]) -> str:
        profile = self._relationship_profile(user)
        return (
            "你的陪伴画像：\n"
            f"关系层级：{profile['level']}\n"
            f"回复率：{profile['reply_rate_label']}\n"
            f"互动次数：{profile['inbound_count']}\n"
            f"主动发送：{profile['proactive_count']}\n"
            f"主动后回复：{profile['reply_count']}\n"
            f"各主动方式承接：{self._format_action_affinity_summary(user)}\n"
            f"打扰偏好：{profile['preference']}\n"
            f"关系分：{profile['score']}\n"
            f"人格判断：{profile.get('note') or '暂无'}\n"
            f"本地陪伴画像：{_single_line(self._format_companion_memory_for_prompt(user), 180)}\n"
            f"表达节奏学习：{_single_line(self._format_expression_profile_for_prompt(user), 180)}\n"
            f"气氛状态：{_single_line(self._format_intent_relationship_injection(user), 180) or '暂无'}\n"
            f"媒介偏好：{_single_line(self._action_preference_hint(user), 180) or '暂无'}"
        )
