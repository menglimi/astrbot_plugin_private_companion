# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiDiagnosticsOverviewPart02Mixin。

由 tools/split_mixin_domain.py 从 page_api_diagnostics_overview.py 机械抽取（4 个方法 + 0 个模块级名字 + 0 个类级赋值 / 370 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiDiagnosticsOverviewMixin）。
"""
from __future__ import annotations

from .page_api_diagnostics_overview_shared import _multi_persona_page_context, logger
from .page_api_diagnostics_overview_shared import Any
from .page_api_diagnostics_overview_shared import deepcopy



class PrivateCompanionPageApiDiagnosticsOverviewPart02Mixin:
    """PrivateCompanionPageApiDiagnosticsOverviewPart02Mixin（从 PrivateCompanionPageApiDiagnosticsOverviewMixin 拆出）。"""


    def _overview_data_snapshot_locked(self, raw_data: Any) -> dict[str, Any]:
        """Build a light read-only snapshot for the dashboard.

        The full data store can contain large image/news/bookshelf/history blobs. The
        overview only needs recent slices and counters, so avoid deepcopying the
        entire store while holding the data lock.
        """
        if not isinstance(raw_data, dict):
            return {}

        def shallow_dict(value: Any) -> dict[str, Any]:
            return dict(value) if isinstance(value, dict) else {}

        def list_tail(value: Any, limit: int) -> list[Any]:
            if not isinstance(value, list):
                return []
            if limit <= 0:
                return []
            return list(value[-limit:])

        scalar_user_keys = (
            "enabled",
            "manual_enabled",
            "manual_disabled",
            "nickname",
            "style",
            "umo",
            "relationship_role",
            "last_seen",
            "last_sent",
            "proactive_chat_bridge_last_sent_at",
            "sent_today",
            "sent_day",
            "last_proactive_skip_at",
            "last_proactive_skip_reason",
            "last_proactive_skip_prefix",
            "proactive_daily_limit",
            "proactive_idle_minutes",
            "proactive_min_interval_minutes",
            "photo_daily_limit",
            "screen_peek_daily_limit",
            "poke_daily_limit",
            "proactive_boundary_note",
            "inbound_count",
            "reply_count",
            "proactive_sent_count",
            "relationship_score",
            "planned_proactive_reason",
            "planned_proactive_action",
            "planned_proactive_source",
            "planned_proactive_topic",
            "planned_proactive_motive",
            "planned_proactive_impulse_id",
            "planned_candidate_id",
            "planned_proactive_semantic_kind",
            "planned_proactive_anchor_type",
            "planned_proactive_semantic_score",
            "planned_proactive_semantic_note",
            "planned_proactive_window_start_at",
            "planned_proactive_best_until_at",
            "planned_proactive_expire_at",
            "planned_proactive_window_timezone",
            "planned_proactive_model_judge_at",
            "next_proactive_at",
            "proactive_sending",
            "last_proactive_hesitation_at",
            "last_proactive_hesitation_note",
        )

        def user_snapshot(value: Any) -> dict[str, Any]:
            if not isinstance(value, dict):
                return {}
            snapshot = {key: value.get(key) for key in scalar_user_keys if key in value}
            for key in (
                "relationship_state",
                "persona_relationship",
                "llm_timer_event",
                "planned_proactive_model_judge_result",
                "proactive_afterglow",
            ):
                raw = value.get(key)
                if isinstance(raw, dict):
                    snapshot[key] = dict(raw)
            aliases = value.get("alias_user_ids")
            if isinstance(aliases, list):
                snapshot["alias_user_ids"] = list(aliases[:8])
            hesitations = value.get("recent_proactive_hesitations")
            if isinstance(hesitations, list):
                snapshot["recent_proactive_hesitations"] = [
                    dict(item) for item in hesitations[-3:] if isinstance(item, dict)
                ]
            return snapshot

        data: dict[str, Any] = {
            "version": raw_data.get("version"),
            "users": {str(key): user_snapshot(value) for key, value in raw_data.get("users", {}).items() if isinstance(value, dict)}
            if isinstance(raw_data.get("users"), dict)
            else {},
            "groups": {
                str(key): {
                    "enabled": bool(value.get("enabled", True)),
                    "name": value.get("name") or value.get("group_name") or "",
                }
                for key, value in raw_data.get("groups", {}).items()
                if isinstance(value, dict)
            }
            if isinstance(raw_data.get("groups"), dict)
            else {},
        }

        for key in (
            "daily_state",
            "daily_plan",
            "daily_story_plan",
            "daily_dream",
            "daily_outfit_photo",
            "detail_enhanced_segments",
            "qq_presence_state",
            "screen_diary_context",
            "worldbook_import_state",
            "worldbook_group_profiles",
            "qzone_integration",
            "reading_archive_integration",
            "reading_archive_state",
            "skill_growth",
            "personal_goal_state",
            "food_menu",
            "external_proactive_abilities",
            "proactive_runtime",
            "message_debounce",
            "smart_message_debounce",
            "expression_voice_profile",
        ):
            value = raw_data.get(key)
            if isinstance(value, dict):
                data[key] = dict(value)
            elif value is not None:
                data[key] = value

        candidate_pool = raw_data.get("proactive_candidate_pool")
        data["proactive_candidate_pool"] = (
            [dict(item) if isinstance(item, dict) else item for item in candidate_pool]
            if isinstance(candidate_pool, list)
            else []
        )

        for key, limit in (
            ("proactive_audit_log", 120),
            ("bot_diaries", 8),
            ("dream_fragments", 40),
            ("schedule_adjustments", 24),
            ("bookshelf_items", 80),
            ("creative_projects", 24),
            ("personal_goals", 80),
            ("external_event_pool", 80),
        ):
            data[key] = list_tail(raw_data.get(key), limit)

        image_cache = raw_data.get("private_image_vision_cache")
        data["private_image_vision_cache_count"] = len(image_cache) if isinstance(image_cache, dict) else 0
        data["private_image_vision_cache"] = {}

        profiles = raw_data.get("worldbook_member_profiles")
        if isinstance(profiles, dict):
            profile_items = [(str(key), value) for key, value in profiles.items() if isinstance(value, dict)]
            data["worldbook_member_profile_count"] = len(profile_items)
            data["worldbook_enabled_member_profile_count"] = sum(1 for _, value in profile_items if bool(value.get("enabled", True)))
            data["worldbook_pending_observation_total"] = sum(
                len(value.get("pending_observations"))
                for _, value in profile_items
                if isinstance(value.get("pending_observations"), list)
            )
            data["worldbook_member_profiles"] = {key: value for key, value in profile_items[:160]}
        else:
            data["worldbook_member_profile_count"] = 0
            data["worldbook_enabled_member_profile_count"] = 0
            data["worldbook_pending_observation_total"] = 0
            data["worldbook_member_profiles"] = {}
        worldbook_groups = raw_data.get("worldbook_group_profiles")
        if isinstance(worldbook_groups, dict):
            group_items = [(str(key), value) for key, value in worldbook_groups.items() if isinstance(value, dict)]
            data["worldbook_group_profile_count"] = len(group_items)
            data["worldbook_group_profiles"] = {key: value for key, value in group_items[:120]}
        else:
            data["worldbook_group_profile_count"] = 0
            data["worldbook_group_profiles"] = {}
        entries = raw_data.get("worldbook_entries")
        data["worldbook_entry_count"] = len(entries) if isinstance(entries, list) else 0
        data["worldbook_entries"] = list_tail(entries, 300) if isinstance(entries, list) else []

        news_state = shallow_dict(raw_data.get("news_integration"))
        if news_state:
            news_state["latest_items"] = list_tail(news_state.get("latest_items"), 12)
            news_state["digests"] = list_tail(news_state.get("digests"), 40)
            ai_daily = shallow_dict(news_state.get("ai_daily"))
            if ai_daily:
                ai_digest = shallow_dict(ai_daily.get("last_digest"))
                if ai_digest:
                    ai_digest["items"] = list_tail(ai_digest.get("items"), 3)
                    ai_daily["last_digest"] = ai_digest
                news_state["ai_daily"] = ai_daily
            data["news_integration"] = news_state

        web_state = shallow_dict(raw_data.get("web_exploration"))
        if web_state:
            web_state["notes"] = list_tail(web_state.get("notes"), 40)
            web_state["latest_results"] = list_tail(web_state.get("latest_results"), 8)
            data["web_exploration"] = web_state

        bilibili_state = shallow_dict(raw_data.get("bilibili_integration"))
        if bilibili_state:
            data["bilibili_integration"] = bilibili_state

        return data

    def _cache_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        image_cache = data.get("private_image_vision_cache") if isinstance(data.get("private_image_vision_cache"), dict) else {}
        image_cache_count = self._int(data.get("private_image_vision_cache_count")) if "private_image_vision_cache_count" in data else len(image_cache)
        metrics = data.get("cache_metrics") if isinstance(data.get("cache_metrics"), dict) else {}

        def metric_row(name: str) -> dict[str, Any]:
            item = metrics.get(name) if isinstance(metrics.get(name), dict) else {}
            hits = 0
            misses = 0
            try:
                hits = max(0, int(item.get("hits") or 0))
            except (TypeError, ValueError):
                hits = 0
            try:
                misses = max(0, int(item.get("misses") or 0))
            except (TypeError, ValueError):
                misses = 0
            total = hits + misses
            return {
                "hits": hits,
                "misses": misses,
                "total": total,
                "hit_rate": round(hits / total, 4) if total else 0,
                "last_hit_at": self.plugin._format_timestamp_elapsed(item.get("last_hit_ts", 0)),
                "last_miss_at": self.plugin._format_timestamp_elapsed(item.get("last_miss_ts", 0)),
            }

        atrelay_cache = getattr(self.plugin, "_atrelay_member_cache", {})
        atrelay_count = len(atrelay_cache) if isinstance(atrelay_cache, dict) else 0
        weather = data.get("daily_weather") if isinstance(data.get("daily_weather"), dict) else {}
        weather_age = self.plugin._format_timestamp_elapsed(weather.get("fetched_ts", 0)) if weather else ""
        qweather_location = data.get("qweather_location") if isinstance(data.get("qweather_location"), dict) else {}
        location_label = ""
        weather_key_getter = getattr(self.plugin, "_weather_context_config_key", None)
        try:
            current_weather_key = self._single_line(weather_key_getter(), 96) if callable(weather_key_getter) else ""
        except Exception:
            current_weather_key = ""
        if not current_weather_key or self._single_line(weather.get("config_key"), 96) == current_weather_key:
            location_label = self._single_line(weather.get("location_label"), 120)
        location_key_getter = getattr(self.plugin, "_qweather_location_cache_key", None)
        has_location_key_getter = callable(location_key_getter)
        try:
            current_location_key = self._single_line(location_key_getter(), 96) if has_location_key_getter else ""
        except Exception:
            current_location_key = ""
        if (
            not location_label
            and (
                not has_location_key_getter
                or (
                    current_location_key
                    and self._single_line(qweather_location.get("config_key"), 96) == current_location_key
                )
            )
        ):
            weather_source = str(getattr(self.plugin, "weather_source", "qweather") or "qweather").strip().lower()
            qweather_location_allowed = weather_source == "qweather" or bool(
                getattr(self.plugin, "enable_weather_alerts", False)
            )
            if qweather_location_allowed:
                location_label = self._single_line(qweather_location.get("label"), 120)
        weather_alert_cache = data.get("weather_alerts") if isinstance(data.get("weather_alerts"), dict) else {}
        raw_alerts = weather_alert_cache.get("alerts") if isinstance(weather_alert_cache.get("alerts"), list) else []
        alert_cache_matches = True
        alert_config_getter = getattr(self.plugin, "_weather_alert_config_key", None)
        if callable(alert_config_getter):
            try:
                current_alert_config = self._single_line(alert_config_getter(), 96)
            except Exception:
                current_alert_config = ""
            if not current_alert_config or self._single_line(weather_alert_cache.get("config_key"), 96) != current_alert_config:
                alert_cache_matches = False
                raw_alerts = []
        alert_filter = getattr(self.plugin, "_filter_weather_alerts", None)
        try:
            visible_alerts = alert_filter(raw_alerts, getattr(self.plugin, "weather_alert_min_severity", "blue")) if callable(alert_filter) else raw_alerts
        except Exception:
            visible_alerts = raw_alerts
        alerts_enabled = bool(getattr(self.plugin, "enable_weather_context", True)) and bool(getattr(self.plugin, "enable_weather_alerts", False))
        if not alerts_enabled:
            visible_alerts = []
        alert_rank = getattr(self.plugin, "_qweather_alert_rank", None)
        if callable(alert_rank):
            try:
                visible_alerts = sorted(
                    [item for item in visible_alerts if isinstance(item, dict)],
                    key=lambda item: alert_rank(item.get("color_code") or item.get("color") or item.get("severity")),
                    reverse=True,
                )
            except Exception:
                visible_alerts = [item for item in visible_alerts if isinstance(item, dict)]
        weather_alert_age = self.plugin._format_timestamp_elapsed(weather_alert_cache.get("fetched_ts", 0)) if weather_alert_cache else ""
        top_alert = visible_alerts[0] if visible_alerts else {}
        alert_attributions = weather_alert_cache.get("attributions") if alert_cache_matches and isinstance(weather_alert_cache.get("attributions"), list) else []
        alert_attributions = [self._single_line(item, 320) for item in alert_attributions if self._single_line(item, 320)][:4]
        provider_runtime: dict[str, Any] = {}
        runtime_getter = getattr(self.plugin, "_private_image_visual_provider_runtime_summary", None)
        if callable(runtime_getter):
            try:
                provider_runtime = runtime_getter()
            except Exception as exc:
                provider_runtime = {"error": self._single_line(exc, 160)}
        return {
            "private_image_vision": {
                "enabled": bool(getattr(self.plugin, "enable_private_image_vision_cache", False)),
                "items": image_cache_count,
                "max_items": int(getattr(self.plugin, "private_image_vision_cache_max_items", 0) or 0),
                "private": metric_row("image_vision:private_image"),
                "group": metric_row("image_vision:group_image"),
                "forward": metric_row("image_vision:forward_image"),
                "provider_runtime": provider_runtime,
            },
            "atrelay_member_cache": {
                "items": atrelay_count,
                "ttl_minutes": int(getattr(self.plugin, "atrelay_member_cache_minutes", 0) or 0),
            },
            "weather": {
                "cached": bool(weather),
                "age": weather_age,
                "source": self._single_line(weather.get("source"), 40),
                "summary": self._single_line(weather.get("prompt"), 120),
                "location_label": location_label,
                "alerts_enabled": alerts_enabled,
                "alerts_cached": bool(weather_alert_cache) and alert_cache_matches,
                "alerts_count": len(visible_alerts),
                "alerts_highest_level": self._single_line(top_alert.get("color") or top_alert.get("severity"), 24),
                "alerts_age": weather_alert_age,
                "alerts_stale": bool(weather_alert_cache.get("stale")),
                "alerts_error": self._single_line(weather_alert_cache.get("error"), 100),
                "alerts_attributions": alert_attributions,
            },
        }

    @_multi_persona_page_context
    async def get_token_stats(self) -> dict[str, Any]:
        try:
            async with self.plugin._data_lock:
                usage = deepcopy(self.plugin.data.get("token_usage", {}))
                balance_state = deepcopy(self.plugin.data.get("balance_awareness", {}))
            stats = self._token_stats_payload(usage, balance_state)
            self._attach_multi_persona_token_stats(stats)
            return self._ok(stats)
        except Exception as exc:
            logger.error(f"获取 Token 统计失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    @_multi_persona_page_context
    async def reset_token_stats(self) -> dict[str, Any]:
        try:
            async with self.plugin._data_lock:
                self.plugin.data["token_usage"] = {}
                balance_state = deepcopy(self.plugin.data.get("balance_awareness", {}))
                self.plugin._save_data_sync(sections={"token_usage"})
            return self._ok(self._token_stats_payload({}, balance_state))
        except Exception as exc:
            logger.error(f"重置 Token 统计失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))
