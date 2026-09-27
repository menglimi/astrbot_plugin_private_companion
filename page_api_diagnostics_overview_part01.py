# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiDiagnosticsOverviewPart01Mixin。

由 tools/split_mixin_domain.py 从 page_api_diagnostics_overview.py 机械抽取（3 个方法 + 0 个模块级名字 + 0 个类级赋值 / 348 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiDiagnosticsOverviewMixin）。
"""
from __future__ import annotations

from .page_api_diagnostics_overview_shared import _multi_persona_page_context, logger
from .page_api_diagnostics_overview_shared import Any
from .page_api_diagnostics_overview_shared import _today_key
from .page_api_diagnostics_overview_shared import deepcopy
from .page_api_diagnostics_overview_shared import evaluate_daily_plan_quality
from .page_api_diagnostics_overview_shared import runtime_persona_setting
from .page_api_diagnostics_overview_shared import time



class PrivateCompanionPageApiDiagnosticsOverviewPart01Mixin:
    """PrivateCompanionPageApiDiagnosticsOverviewPart01Mixin（从 PrivateCompanionPageApiDiagnosticsOverviewMixin 拆出）。"""


    def _overview_section_value(
        self,
        section: str,
        builder: Any,
        *,
        fallback: Any = None,
        degraded_sections: list[str] | None = None,
    ) -> Any:
        """Keep one optional dashboard section from taking down the overview."""
        try:
            return builder()
        except Exception as exc:
            section_name = self._single_line(section, 80) or "unknown"
            if isinstance(degraded_sections, list) and section_name not in degraded_sections:
                degraded_sections.append(section_name)
            logger.warning(
                "总览区块读取失败: section=%s error=%s",
                section_name,
                self._single_line(exc, 200),
                exc_info=True,
            )
            return deepcopy(fallback)

    @_multi_persona_page_context
    async def get_overview(self) -> dict[str, Any]:
        start = time.perf_counter()
        degraded_sections: list[str] = []
        try:
            async with self.plugin._data_lock:
                data = self._overview_section_value(
                    "data_snapshot",
                    lambda: self._overview_data_snapshot_locked(self.plugin.data),
                    fallback={"users": {}, "groups": {}},
                    degraded_sections=degraded_sections,
                )
                reaction_expression = self._overview_section_value(
                    "reaction_expression",
                    lambda: self._reaction_expression_runtime_summary(self.plugin.data),
                    fallback={},
                    degraded_sections=degraded_sections,
                )
                token_stats = self._overview_section_value(
                    "token_stats",
                    lambda: self._token_overview_payload(
                        self.plugin.data.get("token_usage", {}),
                        self.plugin.data.get("balance_awareness", {}),
                    ),
                    fallback={},
                    degraded_sections=degraded_sections,
                )
            plan = deepcopy(data.get("daily_plan"))
            if isinstance(plan, dict):
                data["daily_plan"] = plan
                plan_sanitizer = getattr(self.plugin, "_sanitize_daily_plan_inplace", None)
                if callable(plan_sanitizer):
                    self._overview_section_value(
                        "daily_plan_sanitizer",
                        lambda: plan_sanitizer(plan),
                        fallback=False,
                        degraded_sections=degraded_sections,
                    )
                if isinstance(plan.get("items"), list) and not isinstance(plan.get("quality"), dict):
                    quality = self._overview_section_value(
                        "daily_plan_quality",
                        lambda: evaluate_daily_plan_quality(self.plugin, plan.get("items")),
                        fallback=None,
                        degraded_sections=degraded_sections,
                    )
                    if isinstance(quality, dict):
                        plan["quality"] = quality
            enhanced = deepcopy(data.get("detail_enhanced_segments"))
            if isinstance(enhanced, dict):
                data["detail_enhanced_segments"] = enhanced
                detail_sanitizer = getattr(self.plugin, "_sanitize_detail_enhanced_segments_inplace", None)
                if callable(detail_sanitizer):
                    self._overview_section_value(
                        "detail_segments_sanitizer",
                        lambda: detail_sanitizer(enhanced),
                        fallback=False,
                        degraded_sections=degraded_sections,
                    )
            story = deepcopy(data.get("daily_story_plan"))
            if isinstance(story, dict):
                data["daily_story_plan"] = story
                story_sanitizer = getattr(self.plugin, "_sanitize_story_plan_social_facts_inplace", None)
                if callable(story_sanitizer):
                    self._overview_section_value(
                        "story_plan_sanitizer",
                        lambda: story_sanitizer(story),
                        fallback=False,
                        degraded_sections=degraded_sections,
                    )
            users = data.get("users") if isinstance(data.get("users"), dict) else {}
            groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}
            enabled_users = len(users)
            proactive_enabled_users = sum(
                1
                for item in users.values()
                if isinstance(item, dict)
                and (
                    item.get("proactive_private_enabled") is True
                    or (
                        isinstance(item.get("unified_profile_capabilities"), dict)
                        and item["unified_profile_capabilities"].get("proactive_private_enabled") is True
                    )
                )
            )
            visible_groups = {
                str(group_id): group
                for group_id, group in groups.items()
                if isinstance(group, dict) and not self._looks_like_member_shadow_group(str(group_id), group)
            }
            enabled_groups = sum(1 for item in visible_groups.values() if isinstance(item, dict) and item.get("enabled", True))
            group_access_mode = str(getattr(self.plugin, "group_access_mode", "whitelist") or "whitelist")
            group_whitelist = self._overview_section_value(
                "group_whitelist",
                lambda: list(self.plugin._configured_group_ids()),
                fallback=[],
                degraded_sections=degraded_sections,
            )
            group_blacklist = self._overview_section_value(
                "group_blacklist",
                lambda: list(self.plugin._configured_group_blacklist_ids()),
                fallback=[],
                degraded_sections=degraded_sections,
            )
            effective_group_count = sum(
                1
                for group_id, item in visible_groups.items()
                if isinstance(item, dict)
                and item.get("enabled", True)
                and self.plugin._group_allowed_by_access_mode(str(group_id))
            )
            group_access_warning = ""
            if bool(getattr(self.plugin, "enable_group_companion", False)) and group_access_mode == "whitelist" and not group_whitelist:
                group_access_warning = "群聊观察已开启，但白名单为空，当前不会接收任何群聊观察。"

            def section(name: str, builder: Any, fallback: Any = None) -> Any:
                return self._overview_section_value(
                    name,
                    builder,
                    fallback=fallback,
                    degraded_sections=degraded_sections,
                )

            try:
                bookshelf = await self._bookshelf_summary(data, unlocked=False)
            except Exception as exc:
                degraded_sections.append("bookshelf")
                logger.warning(
                    "总览区块读取失败: section=bookshelf error=%s",
                    self._single_line(exc, 200),
                    exc_info=True,
                )
                bookshelf = {"available": False, "degraded": True, "reason": "summary_unavailable"}
            proactive_tasks = await self._proactive_task_summary_async(data)
            if proactive_tasks.get("degraded"):
                degraded_sections.append("proactive_tasks")

            payload = {
                "plugin": {
                    "enabled": bool(getattr(self.plugin, "enabled", False)),
                    "bot_name": runtime_persona_setting(
                        self.plugin,
                        "bot_name",
                        getattr(self.plugin, "bot_name", ""),
                    ),
                    "data_file": getattr(self.plugin, "data_file", ""),
                    "storage_backend": getattr(self.plugin, "storage_backend", "json"),
                    "storage_sqlite_path": getattr(
                        self.plugin,
                        "storage_sqlite_effective_path",
                        getattr(self.plugin, "storage_sqlite_path", ""),
                    ),
                    "enable_store_control_tag_sanitization": bool(
                        getattr(self.plugin, "enable_store_control_tag_sanitization", True)
                    ),
                    "data_version": data.get("version"),
                },
                "primary_store_ownership": deepcopy(
                    data.get("primary_store_ownership")
                    if isinstance(data.get("primary_store_ownership"), dict)
                    else {}
                ),
                "companion_plugins": section("companion_plugins", self._companion_plugins_summary, {}),
                "private": {
                    "user_count": len(users),
                    "enabled_user_count": enabled_users,
                    "proactive_enabled_user_count": proactive_enabled_users,
                    "require_opt_in": bool(getattr(self.plugin, "require_private_opt_in", True)),
                    "admin_ids": section(
                        "admin_ids",
                        lambda: list(self.plugin._configured_admin_ids()),
                        [],
                    ) if hasattr(self.plugin, "_configured_admin_ids") else [],
                    "target_user_ids": section(
                        "target_user_ids",
                        lambda: list(self.plugin._configured_target_ids()),
                        [],
                    ) if hasattr(self.plugin, "_configured_target_ids") else [],
                    "relationship_owner_ids": section(
                        "relationship_owner_ids",
                        lambda: list(self.plugin._relationship_owner_user_ids()),
                        [],
                    ) if hasattr(self.plugin, "_relationship_owner_user_ids") else [],
                    "max_daily_messages": getattr(self.plugin, "max_daily_messages", 0),
                    "idle_minutes": getattr(self.plugin, "idle_minutes", 0),
                    "min_interval_minutes": getattr(self.plugin, "min_interval_minutes", 0),
                },
                "group": {
                    "enabled": bool(getattr(self.plugin, "enable_group_companion", False)),
                    "group_count": len(visible_groups),
                    "enabled_group_count": enabled_groups,
                    "effective_group_count": effective_group_count,
                    "shadow_group_count": max(0, len(groups) - len(visible_groups)),
                    "access_mode": group_access_mode,
                    "access_warning": group_access_warning,
                    "whitelist": group_whitelist,
                    "blacklist": group_blacklist,
                    "interjection_enabled": bool(getattr(self.plugin, "enable_group_interjection", False)),
                    "repeat_follow_enabled": bool(getattr(self.plugin, "enable_group_repeat_follow", False)),
                },
                "platform_adaptation": section(
                    "platform_adaptation",
                    self.plugin._platform_adaptation_overview,
                    {},
                ) if hasattr(self.plugin, "_platform_adaptation_overview") else {},
                "features": section("features", self._feature_flags, {}),
                "reaction_expression": reaction_expression,
                "proactive_intensity": section("proactive_intensity", self._proactive_intensity_summary, {}),
                "proactive_only": section("proactive_only", self._proactive_only_mode_snapshot, {}),
                "proactive_chat": section("proactive_chat", lambda: self._proactive_chat_summary(data), {}),
                "body_monitor_integration": section("body_monitor_integration", self._body_monitor_integration_summary, {}),
                "expression_scope": section("expression_scope", lambda: self._expression_learning_scope_summary(data), {}),
                "providers": section("providers", self._provider_settings, {}),
                "settings": section("settings", self._runtime_settings, {}),
                "deepseek_peak_routing": section("deepseek_peak_routing", self._deepseek_peak_routing_summary, {}),
                "cache": section("cache", lambda: self._cache_summary(data), {}),
                "livingmemory": section("livingmemory", self._livingmemory_summary, {}),
                "screen_companion": section("screen_companion", lambda: self._screen_companion_summary(data), {}),
                "knowledge": section("knowledge", self.plugin._roleplay_knowledge_summary, {}),
                "worldbook": section("worldbook", lambda: self._worldbook_summary(data), {}),
                "proactive_candidates": section("proactive_candidates", lambda: self._proactive_candidate_summary(data), {}),
                "proactive_tasks": proactive_tasks,
                "message_debounce": section("message_debounce", lambda: self._message_debounce_summary(data), {}),
                "bilibili": section("bilibili", lambda: self._bilibili_summary(data), {}),
                "news": section("news", lambda: self._news_summary(data), {}),
                "web_exploration": section("web_exploration", lambda: self._web_exploration_summary(data), {}),
                "qzone": section("qzone", lambda: self._qzone_summary(data), {}),
                "reading_archive": section("reading_archive", lambda: self._reading_archive_summary(data), {}),
                "creative": section("creative", lambda: self._creative_summary(data), {}),
                "bookshelf": bookshelf,
                "skill_growth": section("skill_growth", lambda: self._skill_growth_summary(data), {}),
                "personal_goals": section("personal_goals", lambda: self._personal_goal_summary(data), {}),
                "food_menu": section("food_menu", lambda: self._food_menu_summary(data), {}),
                "external_abilities": section("external_abilities", lambda: self._external_ability_summary(data), {}),
                "life_observation": section("life_observation", lambda: self._life_observation_summary(data), {}),
                "daily_state": section("daily_state", lambda: self._daily_state_summary(data.get("daily_state")), {}),
                "daily_timeline": section("daily_timeline", lambda: self._daily_timeline_summary(data), {}),
                "daily_outfit": section("daily_outfit", lambda: self._daily_outfit_summary(data), {}),
                "token_stats": token_stats,
                "multi_persona": section(
                    "multi_persona",
                    getattr(self.plugin, "_multi_persona_status", lambda: {"enabled": False}),
                    {"enabled": False},
                ),
                "req041": section("req041", self._req041_runtime_summary, {}),
                "overview_health": {
                    "degraded": bool(degraded_sections),
                    "sections": degraded_sections,
                },
            }
            if not payload.get("multi_persona", {}).get("enabled"):
                payload.pop("multi_persona", None)
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            if elapsed_ms > 1200:
                logger.warning(
                    "总览接口耗时较高: elapsed=%sms users=%s groups=%s",
                    elapsed_ms,
                    len(users),
                    len(visible_groups),
                )
            return self._ok(payload)
        except Exception as exc:
            logger.error(f"获取总览失败: {exc}", exc_info=True)
            return self._exception_error("获取总览失败")

    def _token_overview_payload(self, usage: Any, balance_state: Any = None) -> dict[str, Any]:
        if not isinstance(usage, dict):
            usage = {}
        today_key = _today_key()
        totals = self._token_bucket(usage.get("totals"))
        today_bucket = usage.get("by_day", {}).get(today_key, {}) if isinstance(usage.get("by_day"), dict) else {}
        today_total_tokens = self._int(today_bucket.get("total_tokens")) if isinstance(today_bucket, dict) else 0
        exempt_by_day = usage.get("budget_exempt_by_day") if isinstance(usage.get("budget_exempt_by_day"), dict) else {}
        today_exempt_bucket = exempt_by_day.get(today_key, {}) if isinstance(exempt_by_day, dict) else {}
        today_exempt_tokens = self._int(today_exempt_bucket.get("total_tokens")) if isinstance(today_exempt_bucket, dict) else 0
        if today_exempt_tokens <= 0:
            by_day_task = usage.get("by_day_task") if isinstance(usage.get("by_day_task"), dict) else {}
            today_tasks = by_day_task.get(today_key, {}) if isinstance(by_day_task, dict) else {}
            if isinstance(today_tasks, dict):
                is_exempt_task = getattr(self.plugin, "_is_llm_budget_exempt_task", None)
                today_exempt_tokens = sum(
                    self._int(bucket.get("total_tokens"))
                    for task, bucket in today_tasks.items()
                    if isinstance(bucket, dict)
                    and (
                        (callable(is_exempt_task) and is_exempt_task(task))
                        or (not callable(is_exempt_task) and str(task) in {"proactive_framework", "voice_framework"})
                    )
                )
        today_tokens = max(0, today_total_tokens - today_exempt_tokens)
        daily_limit = self._int(getattr(self.plugin, "daily_token_limit", 0))
        soft_limit = self._int(getattr(self.plugin, "daily_token_soft_limit", 0))
        soft_enabled = bool(getattr(self.plugin, "enable_daily_token_soft_limit", True))
        budget_skips = usage.get("budget_skips", {}) if isinstance(usage.get("budget_skips"), dict) else {}
        today_skips = budget_skips.get(today_key, {}) if isinstance(budget_skips, dict) else {}
        budget = {
            "day": today_key,
            "limit": daily_limit,
            "soft_limit": soft_limit,
            "soft_enabled": soft_enabled,
            "soft_active": bool(soft_enabled and soft_limit > 0 and today_tokens >= soft_limit),
            "used": today_tokens,
            "total_used": today_total_tokens,
            "exempt_used": today_exempt_tokens,
            "remaining": max(0, daily_limit - today_tokens) if daily_limit > 0 else None,
            "soft_remaining": max(0, soft_limit - today_tokens) if soft_enabled and soft_limit > 0 else None,
            "ratio": round(today_tokens / daily_limit, 4) if daily_limit > 0 else 0,
            "soft_ratio": round(today_tokens / soft_limit, 4) if soft_enabled and soft_limit > 0 else 0,
            "exceeded": bool(daily_limit > 0 and today_tokens >= daily_limit),
            "deferred_calls": (
                self._int(today_skips.get("daily_token_soft_limit_deferred"))
                + self._int(today_skips.get("maintenance_token_saver_deferred"))
            )
            if isinstance(today_skips, dict)
            else 0,
            "skipped_calls": self._int(today_skips.get("count")) if isinstance(today_skips, dict) else 0,
        }
        payload = {
            "updated_at": self._single_line(usage.get("updated_at"), 24),
            "totals": totals,
            "budget": budget,
            "balance": self._balance_status_payload(balance_state),
            "memory_plugin": self._token_memory_plugin_payload(self._memory_plugin_token_usage_raw()),
            "together_plugin": self._token_memory_plugin_payload(self._safe_together_plugin_token_usage_raw()),
            "partial": True,
        }
        self._attach_multi_persona_token_stats(payload)
        return payload
