# -*- coding: utf-8 -*-
"""ProactivePart01Mixin。

由 tools/split_mixin_domain.py 从 proactive.py 机械抽取（28 个方法 + 0 个模块级名字 + 0 个类级赋值 / 313 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMixin）。
"""
from __future__ import annotations

from .proactive_core_shared import _proactive_setting_value
from .proactive_core_shared import Any
from .proactive_core_shared import PROACTIVE_ROUTE_REGISTRY
from .proactive_core_shared import PromptRenderMode
from .proactive_core_shared import PromptSection
from .proactive_core_shared import _now_ts
from .proactive_core_shared import _safe_float
from .proactive_core_shared import _safe_int
from .proactive_core_shared import _single_line
from .proactive_core_shared import _today_key
from .proactive_core_shared import _unanswered_proactive_count
from .proactive_core_shared import prompt_section
from .proactive_core_shared import render_prompt_sections



class ProactivePart01Mixin:
    """ProactivePart01Mixin（从 ProactiveMixin 拆出）。"""


    def _proactive_setting(self, key: str, default: Any = None) -> Any:
        """Read an active-persona setting without mutating shared attrs."""
        return _proactive_setting_value(self, key, default)

    def _normalize_proactive_intensity_preset(self, value: Any) -> str:
        preset = str(value or "off").strip().lower()
        aliases = {
            "": "off",
            "manual": "off",
            "none": "off",
            "default": "off",
            "standard": "balanced",
            "active": "high_private",
            "private": "high_private",
            "group": "high_group",
            "online": "live",
            "直播": "live",
            "高频": "live",
        }
        preset = aliases.get(preset, preset)
        return preset if preset in self._PROACTIVE_INTENSITY_PRESETS else "off"

    def _proactive_intensity_runtime(self) -> dict[str, Any]:
        preset = self._normalize_proactive_intensity_preset(
            _proactive_setting_value(self, "proactive_intensity_preset", "off")
        )
        spec = self._PROACTIVE_INTENSITY_PRESETS.get(preset) or self._PROACTIVE_INTENSITY_PRESETS["off"]
        effects = dict(spec.get("effects") or {})
        return {
            "preset": preset,
            "enabled": preset != "off",
            "label": str(spec.get("label") or preset),
            "description": str(spec.get("description") or ""),
            "effects": effects,
        }

    def _proactive_intensity_effect(self, key: str, default: Any = None) -> Any:
        runtime = self._proactive_intensity_runtime()
        if not runtime.get("enabled"):
            return default
        return runtime.get("effects", {}).get(key, default)

    @classmethod
    def _proactive_daily_limit_is_unlimited(cls, value: Any) -> bool:
        return _safe_int(value, 0, 0) >= cls._PROACTIVE_DAILY_LIMIT_UNLIMITED

    @classmethod
    def _format_proactive_daily_limit(cls, value: Any) -> str:
        return "不限" if cls._proactive_daily_limit_is_unlimited(value) else str(_safe_int(value, 0, 0))

    def _proactive_intensity_ignores_daily_limit(self) -> bool:
        return bool(self._proactive_intensity_effect("ignore_daily_limit", False))

    @classmethod
    def _proactive_quota_tier_for_limit(cls, value: Any) -> int:
        quota = max(0, _safe_int(value, 0, 0))
        if quota <= 0:
            return 0
        if quota <= 3:
            return 1
        if quota <= 7:
            return 2
        if quota <= 12:
            return 3
        if quota <= 18:
            return 4
        return 5

    def _proactive_quota_policy(self, user: dict[str, Any] | None = None) -> dict[str, Any]:
        limit = self._effective_user_daily_limit(user or {}) if isinstance(user, dict) else self._runtime_max_daily_messages()
        if self._proactive_daily_limit_is_unlimited(limit):
            limit = self._PROACTIVE_DAILY_QUOTA_MAX
        quota = max(0, min(self._PROACTIVE_USER_DAILY_QUOTA_MAX, _safe_int(limit, 0, 0)))
        tier = self._proactive_quota_tier_for_limit(quota)
        policy = dict(self._PROACTIVE_QUOTA_TIER_POLICIES[tier])
        policy.update({"tier": tier, "quota": quota})
        return policy

    def _proactive_message_kind(
        self,
        *,
        reason: Any = "",
        source: Any = "",
        semantic_kind: Any = "",
    ) -> str:
        return PROACTIVE_ROUTE_REGISTRY.route_for(
            source=source,
            reason=reason,
            semantic_kind=semantic_kind,
        ).key

    def _proactive_route_for(
        self,
        *,
        reason: Any = "",
        source: Any = "",
        semantic_kind: Any = "",
        kind: Any = "",
    ):
        return PROACTIVE_ROUTE_REGISTRY.route_for(
            source=source,
            reason=reason,
            semantic_kind=semantic_kind,
            kind=kind,
        )

    def _proactive_kind_policy(self, kind: Any) -> dict[str, Any]:
        route = self._proactive_route_for(kind=kind)
        return {
            "label": route.label,
            "interval_multiplier": route.interval_multiplier,
            "unanswered_score_penalty": route.unanswered_score_penalty,
            "score_bias": route.score_bias,
            "response_expectation": route.response_expectation,
        }

    def _planned_proactive_kind(self, user: dict[str, Any]) -> str:
        persisted = _single_line(user.get("planned_proactive_kind"), 40).lower()
        if persisted in {route.key for route in PROACTIVE_ROUTE_REGISTRY.all_routes()}:
            return persisted
        return self._proactive_message_kind(
            reason=user.get("planned_proactive_reason"),
            source=user.get("planned_proactive_source"),
            semantic_kind=user.get("planned_proactive_semantic_kind"),
        )

    def _proactive_adaptive_contact_guidance(self, user: dict[str, Any], route: Any) -> str:
        """Return soft, survey-informed guidance without adding a new hard gate.

        The scheduler still owns eligibility and budgets.  This text only helps the
        model choose a useful expression and a lower-cost delivery form for the
        current route, especially after an unanswered contact.
        """
        unanswered = _unanswered_proactive_count(user)
        parts = [
            "先满足帮助性和最近对话相关性，再考虑是否需要主动表达；没有具体内容时宁可合并、延后或仅记录，也不要发固定问候、神秘短句或重复旧话题",
            "根据当前上下文选择单独发送、合并当前回复、下次提起、摘要或仅记录等形式，不把‘想联系’自动等同于立刻发消息",
        ]
        if unanswered > 0:
            parts.append(
                f"此前有 {unanswered} 次主动接触未得到回应；本次不要追问或复述同一主题，按本路线的时效和重要性决定降级形式"
            )
        if getattr(route, "response_expectation", "optional") == "none":
            parts.append("本路线不要求对方回应，避免用问题句制造回应压力")
        return "；".join(parts) + "。"

    def _proactive_route_prompt_section(
        self,
        user: dict[str, Any],
        *,
        reason: Any = "",
        source: Any = "",
    ) -> PromptSection:
        kind = self._proactive_message_kind(
            reason=reason or user.get("planned_proactive_reason"),
            source=source or user.get("planned_proactive_source"),
            semantic_kind=user.get("planned_proactive_semantic_kind"),
        )
        tier_policy = self._proactive_quota_policy(user)
        route = self._proactive_route_for(kind=kind)
        tier_rule = (
            "当前属于高主动配额用户，可以自然、具体地开口，不要因为此前没有逐条回应就写得疏远；仍然避免催促和凑数。"
            if _safe_int(tier_policy.get("tier"), 0) >= 4
            else "按当前关系自然表达，不解释主动频率、配额、候选或调度机制。"
        )
        return prompt_section(
            key="proactive.route",
            title="本轮主动路线",
            source="proactive",
            content=(
                f"- 类型：{route.label}。\n"
                f"- 路线要求：{route.render_directive(quota_tier=_safe_int(tier_policy.get('tier'), 0))}\n"
                f"- 自适应接触：{self._proactive_adaptive_contact_guidance(user, route)}\n"
                f"- 终审重点：{route.review_directive()}\n"
                f"- 配额策略：L{tier_policy.get('tier', 0)} {tier_policy.get('label', '')}。{tier_rule}"
            ),
        )

    def _proactive_route_prompt(self, user: dict[str, Any], *, reason: Any = "", source: Any = "") -> str:
        return render_prompt_sections(
            [self._proactive_route_prompt_section(user, reason=reason, source=source)],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _prepare_proactive_route_candidate(
        self,
        user: dict[str, Any],
        candidate: dict[str, Any],
        *,
        source: str,
        now: float,
    ) -> dict[str, Any]:
        route = self._proactive_route_for(
            reason=candidate.get("reason"),
            source=source or candidate.get("source"),
            semantic_kind=candidate.get("semantic_kind"),
            kind=candidate.get("kind"),
        )
        prepared = route.prepare_candidate(
            candidate,
            source=source,
            now=now,
            date_key=_today_key(),
        )
        prepared["quota_tier"] = _safe_int(self._proactive_quota_policy(user).get("tier"), 0, 0, 5)
        return prepared

    def _planned_proactive_route(self, user: dict[str, Any]):
        return self._proactive_route_for(
            reason=user.get("planned_proactive_reason"),
            source=user.get("planned_proactive_source"),
            semantic_kind=user.get("planned_proactive_semantic_kind"),
            kind=user.get("planned_proactive_kind"),
        )

    def _planned_proactive_route_payload(self, user: dict[str, Any]) -> dict[str, Any]:
        return {
            "reason": user.get("planned_proactive_reason"),
            "source": user.get("planned_proactive_source"),
            "kind": self._planned_proactive_kind(user),
            "topic": user.get("planned_proactive_topic"),
            "motive": user.get("planned_proactive_motive"),
            "trigger_message_id": user.get("planned_proactive_trigger_message_id"),
            "trigger_ts": user.get("planned_proactive_trigger_ts"),
            "trigger_inbound_count": user.get("planned_proactive_trigger_inbound_count"),
            "private_inbound_count": user.get("private_inbound_count"),
            "origin_event_id": user.get("planned_proactive_origin_event_id"),
            "semantic_anchor_type": user.get("planned_proactive_anchor_type"),
            "followup_kind": user.get("planned_followup_kind"),
            "chain": user.get("planned_event_chain") if isinstance(user.get("planned_event_chain"), list) else [],
            "window_start_at": user.get("planned_proactive_window_start_at"),
            "best_until_at": user.get("planned_proactive_best_until_at"),
            "expire_at": user.get("planned_proactive_expire_at"),
            "window_timezone": user.get("planned_proactive_window_timezone"),
        }

    def _store_planned_proactive_route_fields(self, user: dict[str, Any], item: dict[str, Any]) -> None:
        route = self._proactive_route_for(
            reason=item.get("reason") or user.get("planned_proactive_reason"),
            source=item.get("source") or user.get("planned_proactive_source"),
            semantic_kind=item.get("semantic_kind") or user.get("planned_proactive_semantic_kind"),
            kind=item.get("kind") or user.get("planned_proactive_kind"),
        )
        route_item = route.prepare_candidate(
            {
                **self._planned_proactive_route_payload(user),
                **item,
            },
            source=_single_line(item.get("source") or user.get("planned_proactive_source"), 40),
            now=_now_ts(),
            date_key=_today_key(),
        )
        options = route.delivery_options(route_item)
        user["planned_proactive_kind"] = route.key
        user["planned_proactive_route_version"] = _safe_int(route_item.get("route_version"), 2, 0)
        user["planned_proactive_route_dedupe_key"] = _single_line(route_item.get("route_dedupe_key"), 180)
        user["planned_proactive_route_review_profile"] = _single_line(
            route_item.get("route_review_profile") or route.review_profile,
            40,
        )
        user["planned_proactive_route_retry_profile"] = _single_line(
            route_item.get("route_retry_profile") or route.retry_profile,
            40,
        )
        user["planned_proactive_route_cancel_if_new_inbound"] = bool(
            route_item.get("route_cancel_if_new_inbound", options.get("cancel_if_new_inbound", True))
        )
        user["planned_proactive_route_recent_chat_policy"] = _single_line(
            route_item.get("route_recent_chat_policy") or options.get("recent_chat_policy"),
            40,
        )
        user["planned_proactive_route_allow_automatic_followup"] = bool(
            route_item.get("route_allow_automatic_followup", route.allow_automatic_followup)
        )
        user["planned_proactive_route_disable_segmenting"] = bool(
            route_item.get("route_disable_segmenting", options.get("disable_segmenting", False))
        )
        user["planned_proactive_response_expectation"] = _single_line(
            route_item.get("response_expectation") or route.response_expectation,
            24,
        )
        user["planned_proactive_origin_event_id"] = _single_line(route_item.get("origin_event_id"), 80)

    def _planned_proactive_route_preflight(self, user: dict[str, Any], *, now: float):
        route = self._planned_proactive_route(user)
        return route.preflight(user, self._planned_proactive_route_payload(user), now=now)

    def _planned_proactive_route_delivery_options(self, user: dict[str, Any]) -> dict[str, Any]:
        route = self._planned_proactive_route(user)
        return route.delivery_options(self._planned_proactive_route_payload(user))

    def _planned_proactive_route_settlement(self, user: dict[str, Any]) -> dict[str, Any]:
        route = self._planned_proactive_route(user)
        return route.settlement(self._planned_proactive_route_payload(user))

    def _effective_proactive_int(self, key: str, configured: int, *, minimum: int = 0, maximum: int | None = None) -> int:
        value = configured
        effect = self._proactive_intensity_effect(key, None)
        if effect is not None:
            value = _safe_int(effect, configured, minimum, maximum if maximum is not None else 10**9)
        value = max(minimum, value)
        if maximum is not None:
            value = min(maximum, value)
        return value

    def _effective_proactive_float(self, key: str, configured: float, *, minimum: float = 0.0, maximum: float | None = None) -> float:
        value = configured
        effect = self._proactive_intensity_effect(key, None)
        if effect is not None:
            value = _safe_float(effect, configured, minimum)
        value = max(minimum, float(value))
        if maximum is not None:
            value = min(maximum, value)
        return value

    def _effective_group_wakeup_cooldown_seconds(self) -> int:
        return self._effective_proactive_int(
            "group_wakeup_cooldown_seconds",
            _safe_int(_proactive_setting_value(self, "group_wakeup_cooldown_seconds", 90), 90, 0, 3600),
            minimum=0,
            maximum=3600,
        )

    def _effective_group_high_intensity_cooldown_seconds(self) -> int:
        return self._effective_proactive_int(
            "group_high_intensity_cooldown_seconds",
            _safe_int(_proactive_setting_value(self, "group_high_intensity_cooldown_seconds", 150), 150, 30, 1800),
            minimum=30,
            maximum=1800,
        )

    def _effective_group_interject_min_interval_minutes(self) -> int:
        base_interval = self._effective_proactive_int(
            "group_interject_min_interval_minutes",
            _safe_int(_proactive_setting_value(self, "group_interject_min_interval_minutes", 180), 180, 10, 1440),
            minimum=1,
            maximum=1440,
        )
        profile = self._cycle_proactive_frequency_profile()
        return max(1, min(1440, int(round(base_interval * profile["group_interval_multiplier"]))))
