# -*- coding: utf-8 -*-
"""End-to-end route policies for proactive private messages."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any


ROUTE_VERSION = 2


def _text(value: Any, limit: int = 160) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _fingerprint(*values: Any) -> str:
    normalized = "|".join(_text(value, 500).lower() for value in values if _text(value, 500))
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:20] if normalized else ""


def _context_text(candidate: dict[str, Any]) -> str:
    context = candidate.get("context")
    try:
        rendered = json.dumps(context, ensure_ascii=False, sort_keys=True) if context is not None else ""
    except (TypeError, ValueError):
        rendered = str(context or "")
    return " ".join(
        part
        for part in (
            _text(candidate.get("topic"), 240),
            _text(candidate.get("motive"), 400),
            _text(candidate.get("origin_event_id"), 120),
            _text(candidate.get("trigger_message_id"), 120),
            _text(candidate.get("context_key"), 120),
            _text(rendered, 1000),
        )
        if part
    )


@dataclass(frozen=True)
class RoutePreflight:
    allowed: bool = True
    reason: str = ""
    action: str = "send"
    defer_minutes: tuple[float, float] = (0.0, 0.0)


class ProactiveRoute:
    key = "relational"
    label = "关系关怀"
    source_names: frozenset[str] = frozenset()
    reason_names: frozenset[str] = frozenset()
    semantic_names: frozenset[str] = frozenset()
    active_window_seconds = 55 * 60.0
    grace_window_seconds = 80 * 60.0
    interval_multiplier = 1.0
    score_bias = 0.0
    unanswered_score_penalty = 0.08
    response_expectation = "optional"
    cancel_if_new_inbound = True
    recent_chat_policy = "defer"
    duplicate_policy = "semantic"
    review_profile = "low_pressure"
    disable_segmenting = False
    allow_automatic_followup = True
    retry_profile = "normal"

    def matches(self, *, source: str, reason: str, semantic_kind: str) -> bool:
        return (
            source in self.source_names
            or reason in self.reason_names
            or semantic_kind in self.semantic_names
        )

    def prepare_candidate(
        self,
        candidate: dict[str, Any],
        *,
        source: str,
        now: float,
        date_key: str,
    ) -> dict[str, Any]:
        prepared = dict(candidate)
        prepared["source"] = source or _text(prepared.get("source"), 40) or "unknown"
        prepared["kind"] = self.key
        prepared["kind_label"] = self.label
        prepared["route_version"] = ROUTE_VERSION
        prepared["route_dedupe_key"] = self.dedupe_key(prepared, date_key=date_key)
        prepared["response_expectation"] = self.response_expectation
        prepared["route_review_profile"] = self.review_profile
        prepared["route_retry_profile"] = self.retry_profile
        prepared["route_cancel_if_new_inbound"] = self.cancel_if_new_inbound
        prepared["route_recent_chat_policy"] = self.recent_chat_policy
        prepared["route_allow_automatic_followup"] = self.allow_automatic_followup
        prepared["route_disable_segmenting"] = self.disable_segmenting
        prepared.setdefault("route_created_at", now)
        return prepared

    def dedupe_key(self, candidate: dict[str, Any], *, date_key: str) -> str:
        return f"{self.key}:{_fingerprint(candidate.get('reason'), candidate.get('topic'), candidate.get('motive'))}"

    def preflight(self, user: dict[str, Any], plan: dict[str, Any], *, now: float) -> RoutePreflight:
        return RoutePreflight()

    def render_directive(self, *, quota_tier: int) -> str:
        return (
            "表达一个具体、完整且低压力的意思；不要解释主动机制，也不要用空泛问候凑数。"
        )

    def review_directive(self) -> str:
        return "确认正文与本路线目的相符；软质量问题优先改写，只有明确事实、安全或边界风险才丢弃。"

    def delivery_options(self, plan: dict[str, Any]) -> dict[str, Any]:
        return {
            "disable_segmenting": bool(self.disable_segmenting),
            "quote_anchor": self.key == "continuation",
            "cancel_if_new_inbound": bool(self.cancel_if_new_inbound),
            "recent_chat_policy": self.recent_chat_policy,
            "duplicate_policy": self.duplicate_policy,
            "retry_profile": self.retry_profile,
        }

    def settlement(self, plan: dict[str, Any]) -> dict[str, Any]:
        return {
            "await_reply": self.response_expectation == "expected",
            "allow_automatic_followup": bool(self.allow_automatic_followup),
            "clear_context_keys": (),
            "quota_bucket": self.key,
        }


class TransactionalRoute(ProactiveRoute):
    key = "transactional"
    label = "明确事务"
    source_names = frozenset({"timer", "memo_note"})
    reason_names = frozenset({"timer", "reminder", "memo_note_reminder"})
    active_window_seconds = 90 * 60.0
    grace_window_seconds = 6 * 3600.0
    interval_multiplier = 0.2
    score_bias = 0.16
    unanswered_score_penalty = 0.0
    response_expectation = "none"
    cancel_if_new_inbound = False
    recent_chat_policy = "bypass"
    duplicate_policy = "event_anchor"
    review_profile = "fact_preserving"
    disable_segmenting = True
    allow_automatic_followup = False
    retry_profile = "until_expiry"

    def dedupe_key(self, candidate: dict[str, Any], *, date_key: str) -> str:
        anchor = (
            _text(candidate.get("origin_event_id"), 120)
            or _text(candidate.get("trigger_message_id"), 120)
            or _text(candidate.get("id"), 120)
        )
        scheduled_bucket = int(float(candidate.get("scheduled_ts") or candidate.get("_scheduled_ts") or 0) // 60)
        return f"{self.key}:{anchor or _fingerprint(candidate.get('topic'), candidate.get('motive'))}:{scheduled_bucket}"

    def render_directive(self, *, quota_tier: int) -> str:
        return "事项、对象、时间和原始条件必须原样保真；只润色语气，一次说清，不扩写任务，不追问是否完成。"

    def review_directive(self) -> str:
        return "逐项核对提醒事实；不得为了更自然而改写事项、时间、条件或对象。"


class SafetyEventRoute(ProactiveRoute):
    key = "safety_event"
    label = "安全与环境事件"
    source_names = frozenset({"weather_alert", "body_monitor", "environment_change"})
    reason_names = frozenset({"weather_alert", "environment_change", "health_alert"})
    active_window_seconds = 30 * 60.0
    grace_window_seconds = 45 * 60.0
    interval_multiplier = 0.12
    score_bias = 0.2
    unanswered_score_penalty = 0.0
    response_expectation = "none"
    cancel_if_new_inbound = False
    recent_chat_policy = "bypass"
    duplicate_policy = "event_revision"
    review_profile = "safety_grounded"
    # Safety facts must remain intact, but the configured text splitter can
    # still deliver them as ordered bubbles without changing their content.
    disable_segmenting = False
    allow_automatic_followup = False
    retry_profile = "short_lived"

    def preflight(self, user: dict[str, Any], plan: dict[str, Any], *, now: float) -> RoutePreflight:
        try:
            expire_at = float(plan.get("expire_at") or 0)
        except (TypeError, ValueError):
            expire_at = 0.0
        if expire_at > 0 and now > expire_at:
            return RoutePreflight(
                allowed=False,
                reason="安全事件已经越过有效期",
                action="cancel",
            )
        return RoutePreflight()

    def dedupe_key(self, candidate: dict[str, Any], *, date_key: str) -> str:
        context = _context_text(candidate)
        severity = _text(candidate.get("severity") or candidate.get("level"), 40)
        return f"{self.key}:{_text(candidate.get('origin_event_id'), 120) or _fingerprint(candidate.get('reason'), severity, context)}"

    def render_directive(self, *, quota_tier: int) -> str:
        return "先说清当前有效的事件等级、影响和必要建议，再表达关心；事实与推测分开，不夸大风险，不作医疗诊断。"

    def review_directive(self) -> str:
        return "只允许基于候选证据中的当前事件事实；过期、地域不匹配、无来源升级或夸张描述必须丢弃。"


class ContinuationRoute(ProactiveRoute):
    key = "continuation"
    label = "对话延续"
    source_names = frozenset({"pending_followup", "followup", "atrelay", "open_loop"})
    reason_names = frozenset({"activity_followup", "open_loop_followup"})
    semantic_names = frozenset({"continuation"})
    active_window_seconds = 75 * 60.0
    grace_window_seconds = 90 * 60.0
    interval_multiplier = 0.65
    score_bias = 0.08
    unanswered_score_penalty = 0.04
    response_expectation = "expected"
    cancel_if_new_inbound = True
    recent_chat_policy = "anchor_check"
    duplicate_policy = "conversation_anchor"
    review_profile = "anchor_bound"
    disable_segmenting = True
    allow_automatic_followup = True
    retry_profile = "while_anchor_live"

    @staticmethod
    def _is_activity_followup(plan: dict[str, Any]) -> bool:
        return _text(plan.get("reason"), 40).lower() == "activity_followup"

    def delivery_options(self, plan: dict[str, Any]) -> dict[str, Any]:
        options = super().delivery_options(plan)
        if self._is_activity_followup(plan):
            # A return check has a live user-message anchor, but it is never an
            # invitation to keep prompting after the one scheduled message.
            options["allow_automatic_followup"] = False
        return options

    def settlement(self, plan: dict[str, Any]) -> dict[str, Any]:
        settlement = super().settlement(plan)
        if self._is_activity_followup(plan) or _text(plan.get("reason"), 40) == "open_loop_followup":
            settlement["await_reply"] = False
            settlement["allow_automatic_followup"] = False
        return settlement

    def dedupe_key(self, candidate: dict[str, Any], *, date_key: str) -> str:
        anchor = (
            _text(candidate.get("trigger_message_id"), 120)
            or _text(candidate.get("origin_event_id"), 120)
            or _text(candidate.get("context_key"), 120)
        )
        return f"{self.key}:{anchor or _fingerprint(candidate.get('topic'), candidate.get('motive'))}"

    def preflight(self, user: dict[str, Any], plan: dict[str, Any], *, now: float) -> RoutePreflight:
        try:
            trigger_inbound_count = int(plan.get("trigger_inbound_count"))
        except (TypeError, ValueError):
            trigger_inbound_count = -1
        try:
            private_inbound_count = int(plan.get("private_inbound_count") or 0)
        except (TypeError, ValueError):
            private_inbound_count = 0
        if (
            _text(plan.get("trigger_message_id"), 120)
            and trigger_inbound_count >= 0
            and private_inbound_count > trigger_inbound_count
        ):
            return RoutePreflight(
                allowed=False,
                reason="续聊锚点之后已有新的用户消息，原锚点已失效",
                action="cancel",
            )
        has_anchor = bool(
            _text(plan.get("trigger_message_id"), 120)
            or _text(plan.get("origin_event_id"), 120)
            or plan.get("chain")
            or _text(plan.get("followup_kind"), 40)
            or _text(plan.get("semantic_anchor_type"), 40)
        )
        if not has_anchor:
            return RoutePreflight(
                allowed=True,
                reason="续聊锚点偏弱，交由生成路线收敛为不索取回应的轻续句",
                action="rewrite_context",
            )
        return RoutePreflight()

    def render_directive(self, *, quota_tier: int) -> str:
        return "只延续绑定的原话题或事件；不要伪造对方说过的话，不复读上一条，也不要把续聊变成新的查岗。"

    def review_directive(self) -> str:
        return "检查正文能否从锚点自然接上；若锚点已被回应、撤回或失效，应取消而不是改成无关新话题。"


class RitualRoute(ProactiveRoute):
    key = "ritual"
    label = "日常仪式"
    source_names = frozenset(
        {"daily_greeting", "meal_care", "birthday_celebration", "birthday_curiosity", "special_day_ritual"}
    )
    reason_names = frozenset(
        {
            "morning_greeting",
            "noon_greeting",
            "evening_greeting",
            "meal_care",
            "meal_care_followup",
            "birthday_celebration",
            "birthday_eve_hint",
            "birthday_makeup",
            "birthday_curiosity",
            "important_date_share",
            "special_day_greeting",
        }
    )
    active_window_seconds = 70 * 60.0
    grace_window_seconds = 35 * 60.0
    interval_multiplier = 0.82
    score_bias = 0.04
    unanswered_score_penalty = 0.03
    response_expectation = "optional"
    cancel_if_new_inbound = True
    recent_chat_policy = "satisfy_or_defer"
    duplicate_policy = "daily_slot"
    review_profile = "time_slot"
    # Respect the user's global segmentation preference for greetings and
    # other daily rituals instead of forcing every message into one bubble.
    disable_segmenting = False
    allow_automatic_followup = True

    def dedupe_key(self, candidate: dict[str, Any], *, date_key: str) -> str:
        context = candidate.get("context") if isinstance(candidate.get("context"), dict) else {}
        slot = _text(context.get("meal_key") or candidate.get("reason"), 60)
        return f"{self.key}:{date_key}:{slot}"

    def render_directive(self, *, quota_tier: int) -> str:
        return (
            "严格贴合今天、当前时段和最近对话，像熟悉的人顺手出现；"
            "近期已经聊过时必须承接现有语境，不得声称还没问候过，也不得重新举行早安、午安或晚安仪式；"
            "一句仪式表达只完成一个目的，不扩写成查岗或连续盘问。"
        )

    def review_directive(self) -> str:
        return (
            "检查仪式是否仍符合当前时段和会话进度；若用户已经在该时段自然出现，"
            "正文不得以‘还没说早安/午安/晚安’另起话题，无法自然承接当前对话时应丢弃。"
        )

    def settlement(self, plan: dict[str, Any]) -> dict[str, Any]:
        reason = _text(plan.get("reason"), 40)
        return {
            "await_reply": reason in {"meal_care", "meal_care_followup", "birthday_curiosity"},
            "allow_automatic_followup": reason in {"meal_care", "morning_greeting"},
            "clear_context_keys": (),
            "quota_bucket": self.key,
        }


class ContentShareRoute(ProactiveRoute):
    key = "content_share"
    label = "内容分享"
    source_names = frozenset(
        {"group_share", "news_share", "bili_video_share", "web_exploration_share", "creative_writing"}
    )
    reason_names = frozenset(
        {
            "group_share",
            "news_share",
            "bili_video_share",
            "web_exploration_share",
            "creative_share",
            "reading_archive_recommendation_request",
            "game_invite",
        }
    )
    active_window_seconds = 45 * 60.0
    grace_window_seconds = 60 * 60.0
    interval_multiplier = 0.9
    score_bias = 0.03
    unanswered_score_penalty = 0.015
    response_expectation = "none"
    cancel_if_new_inbound = False
    recent_chat_policy = "short_defer"
    duplicate_policy = "content_fingerprint"
    review_profile = "evidence_bound"
    disable_segmenting = False
    allow_automatic_followup = False

    _CONTEXT_KEYS = {
        "group_share": "group_share_context",
        "bili_video_share": "bilibili_video_context",
        "news_share": "news_context",
        "web_exploration_share": "web_exploration_context",
        "creative_share": "creative_share_context",
        "reading_archive_recommendation_request": "reading_archive_recommendation_context",
        "game_invite": "game_invite_context",
    }

    def dedupe_key(self, candidate: dict[str, Any], *, date_key: str) -> str:
        context = _context_text(candidate)
        url = re.search(r"https?://[^\s<>]+", context)
        content_id = _text(candidate.get("content_id") or candidate.get("origin_event_id"), 160)
        return f"{self.key}:{content_id or (url.group(0) if url else _fingerprint(context))}"

    def render_directive(self, *, quota_tier: int) -> str:
        return "先交付具体内容和真正想分享的点；标题、链接、作者、群聊事实或创作内容必须来自证据，不得用空泛占位代替。"

    def review_directive(self) -> str:
        return "核对正文中的可验证细节是否都能在内容上下文找到；无证据事实应删除，核心内容缺失时应取消。"

    def preflight(self, user: dict[str, Any], plan: dict[str, Any], *, now: float) -> RoutePreflight:
        reason = _text(plan.get("reason"), 40)
        context_key = self._CONTEXT_KEYS.get(reason)
        context = user.get(context_key) if context_key else None
        if context_key and (not isinstance(context, dict) or not context):
            return RoutePreflight(
                allowed=False,
                reason="内容分享缺少可核验的核心上下文",
                action="cancel",
            )
        return RoutePreflight()

    def settlement(self, plan: dict[str, Any]) -> dict[str, Any]:
        reason = _text(plan.get("reason"), 40)
        context_key = self._CONTEXT_KEYS.get(reason)
        return {
            "await_reply": False,
            "allow_automatic_followup": False,
            "clear_context_keys": (context_key,) if context_key else (),
            "quota_bucket": self.key,
        }


class SelfLifeRoute(ProactiveRoute):
    key = "self_life"
    label = "生活自述"
    source_names = frozenset({"state", "story", "event", "creative"})
    reason_names = frozenset(
        {"state_share", "activity_share", "background_schedule", "diary_share", "dream_share", "personal_goal_progress"}
    )
    active_window_seconds = 55 * 60.0
    grace_window_seconds = 80 * 60.0
    interval_multiplier = 0.86
    score_bias = 0.02
    unanswered_score_penalty = 0.015
    response_expectation = "none"
    cancel_if_new_inbound = False
    recent_chat_policy = "short_defer"
    duplicate_policy = "life_event"
    review_profile = "self_continuity"
    disable_segmenting = False
    allow_automatic_followup = False

    def dedupe_key(self, candidate: dict[str, Any], *, date_key: str) -> str:
        anchor = _text(candidate.get("origin_event_id"), 120) or _text(candidate.get("context_key"), 120)
        return f"{self.key}:{anchor or _fingerprint(candidate.get('reason'), candidate.get('topic'), candidate.get('motive'))}"

    def render_directive(self, *, quota_tier: int) -> str:
        return "以 Bot 自己持续发生的生活为主体，说一个有时间连续性的具体片段；不凭空制造重大经历，也不要每次机械转问用户。"

    def review_directive(self) -> str:
        return "检查生活片段是否与当前状态、日程或已有事件一致；缺乏重大事实依据时只可保留轻量日常表达。"


class RelationalRoute(ProactiveRoute):
    key = "relational"
    label = "关系关怀"
    source_names = frozenset({"habit", "balance", "random"})
    reason_names = frozenset(
        {
            "quiet_care",
            "check_in",
            "low_balance",
            "memory_echo",
            "mood_checkin",
            "absence_miss",
            "anonymous_area_dwell",
            "anonymous_area_familiarity",
        }
    )
    response_expectation = "expected"

    _ONE_SHOT_CONTEXT_KEYS = {
        "memory_echo": "memory_echo_context",
        "mood_checkin": "mood_checkin_context",
        "absence_miss": "absence_miss_context",
    }

    def prepare_candidate(
        self,
        candidate: dict[str, Any],
        *,
        source: str,
        now: float,
        date_key: str,
    ) -> dict[str, Any]:
        prepared = super().prepare_candidate(
            candidate,
            source=source,
            now=now,
            date_key=date_key,
        )
        if _text(prepared.get("reason"), 40) in self._ONE_SHOT_CONTEXT_KEYS:
            prepared["response_expectation"] = "none"
            prepared["route_allow_automatic_followup"] = False
        return prepared

    def settlement(self, plan: dict[str, Any]) -> dict[str, Any]:
        reason = _text(plan.get("reason"), 40)
        context_key = self._ONE_SHOT_CONTEXT_KEYS.get(reason)
        if context_key:
            return {
                "await_reply": False,
                "allow_automatic_followup": False,
                "clear_context_keys": (context_key,),
                "quota_bucket": self.key,
            }
        return super().settlement(plan)

    def render_directive(self, *, quota_tier: int) -> str:
        frequency_note = (
            "这是高配额关系，可以自然出现，不要因对方没有逐条回复而突然疏远。"
            if quota_tier >= 4
            else "保持克制，不制造必须回应的压力。"
        )
        return f"表达一个具体关心或关系念头，不诊断、不说教、不查岗；{frequency_note}"


class ProactiveRouteRegistry:
    def __init__(self) -> None:
        self._routes: tuple[ProactiveRoute, ...] = (
            TransactionalRoute(),
            SafetyEventRoute(),
            ContinuationRoute(),
            RitualRoute(),
            ContentShareRoute(),
            SelfLifeRoute(),
            RelationalRoute(),
        )
        self._by_key = {route.key: route for route in self._routes}

    def route_for(
        self,
        *,
        source: Any = "",
        reason: Any = "",
        semantic_kind: Any = "",
        kind: Any = "",
    ) -> ProactiveRoute:
        normalized_kind = _text(kind, 40).lower()
        if normalized_kind in self._by_key:
            return self._by_key[normalized_kind]
        normalized_source = _text(source, 40).lower()
        normalized_reason = _text(reason, 40).lower()
        normalized_semantic = _text(semantic_kind, 40).lower()
        # Activity follow-ups are conversation continuations even though AstrBot
        # executes them through the generic timer backend.
        if normalized_reason == "activity_followup":
            return self._by_key["continuation"]
        for route in self._routes[:-1]:
            if route.matches(
                source=normalized_source,
                reason=normalized_reason,
                semantic_kind=normalized_semantic,
            ):
                return route
        return self._by_key["relational"]

    def all_routes(self) -> tuple[ProactiveRoute, ...]:
        return self._routes


PROACTIVE_ROUTE_REGISTRY = ProactiveRouteRegistry()


__all__ = [
    "PROACTIVE_ROUTE_REGISTRY",
    "ROUTE_VERSION",
    "ProactiveRoute",
    "ProactiveRouteRegistry",
    "RoutePreflight",
]
