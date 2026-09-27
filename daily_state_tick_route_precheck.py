# -*- coding: utf-8 -*-
"""DailyStateTickRoutePrecheckMixin。

由 tools/split_mixin_domain.py 从 daily_state_tick.py 机械抽取（5 个方法 + 0 个模块级名字 + 0 个类级赋值 / 98 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateTickMixin）。
"""
from __future__ import annotations

from .helpers import (
    _record_unanswered_proactive,
    _safe_int,
    _single_line,
    _unanswered_proactive_count,
    normalize_legacy_tag_text,
)
from typing import Any



class DailyStateTickRoutePrecheckMixin:
    """DailyStateTickRoutePrecheckMixin（从 DailyStateTickMixin 拆出）。"""


    def _save_proactive_tick_state(self, sections: set[str]) -> None:
        """Persist tick state while tolerating legacy test/extension adapters."""
        saver = getattr(self, "_save_data_sync", None)
        if not callable(saver):
            return
        try:
            saver(sections=sections)
        except TypeError as exc:
            # Older integrations exposed _save_data_sync() without the
            # incremental sections argument. Keep the active loop usable for
            # those adapters, but do not hide unrelated TypeErrors.
            if "unexpected keyword argument" not in str(exc) or "sections" not in str(exc):
                raise
            saver()

    @staticmethod
    def _proactive_similarity_guard_enabled(
        user: dict[str, Any],
        *,
        is_troubleshooting: bool,
        action: str,
        timeliness: str,
        duplicate_policy: str,
        enabled_policies: frozenset[str] | None = None,
    ) -> bool:
        """Burst follow-ups intentionally use a second angle, not duplicate text."""
        active_policies = (
            {"semantic", "content_fingerprint", "life_event"}
            if enabled_policies is None
            else enabled_policies
        )
        return bool(
            not is_troubleshooting
            and (action or "message") == "message"
            and not bool(user.get("planned_proactive_burst"))
            and timeliness == "routine"
            and duplicate_policy in active_policies
        )

    def _route_recent_chat_guard_reason(
        self,
        user: dict[str, Any],
        *,
        now: float,
        planned_reason: str,
        due_timer_active: bool,
        is_troubleshooting: bool,
    ) -> str:
        options = self._planned_proactive_route_delivery_options(user)
        policy = _single_line(options.get("recent_chat_policy"), 40) or "defer"
        if policy in {"bypass", "anchor_check"}:
            return ""
        note = self._recent_chat_proactive_guard_reason(
            user,
            now=now,
            planned_reason=planned_reason,
            planned_source=normalize_legacy_tag_text(user.get("planned_proactive_source")),
            due_timer_active=due_timer_active,
            is_troubleshooting=is_troubleshooting,
        )
        if note and policy == "short_defer":
            return note.replace("普通主动延后", "分享路线短暂避让")
        return note

    def _defer_route_for_recent_chat(self, user: dict[str, Any], *, now: float, note: str) -> None:
        options = self._planned_proactive_route_delivery_options(user)
        if _single_line(options.get("recent_chat_policy"), 40) == "short_defer":
            self._defer_or_replace_planned_impulse(
                user,
                now=now,
                note=note,
                delay_minutes=(5.0, 15.0),
                block_current=False,
            )
            self._mark_planned_candidate_status(user, "deferred", note)
            return
        self._defer_proactive_for_recent_chat(user, now=now, note=note)

    def _settle_proactive_route_state(
        self,
        user: dict[str, Any],
        *,
        route_key: str,
        settlement: dict[str, Any],
        sent_at: float,
        count_delivery: bool = True,
    ) -> None:
        user["last_proactive_kind"] = route_key
        _unanswered_proactive_count(user)
        if count_delivery:
            route_counts = user.setdefault("proactive_route_sent_counts", {})
            if not isinstance(route_counts, dict):
                route_counts = {}
                user["proactive_route_sent_counts"] = route_counts
            route_counts[route_key] = _safe_int(route_counts.get(route_key), 0, 0) + 1
            if bool(settlement.get("await_reply")):
                _record_unanswered_proactive(user, sent_at=sent_at)
                user["awaiting_reply_since"] = sent_at
        for context_key in settlement.get("clear_context_keys", ()):
            clean_key = _single_line(context_key, 80)
            if clean_key:
                user[clean_key] = {}
