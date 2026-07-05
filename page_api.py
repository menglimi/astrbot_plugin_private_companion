# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import time
import re
import shutil
import base64
import hashlib
import mimetypes
import secrets
import sqlite3
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from astrbot.api import logger
from quart import request, send_file

from .constants import _REASON_TEXT
from .config_migration import _ensure_config_parent_dir
from .helpers import _flat_get, _safe_int, _set_into_config, _strip_internal_message_blocks, _text_looks_garbled, _text_similarity, _today_key
from .page_api_qzone import PrivateCompanionPageApiQzoneMixin
from .page_api_users_groups import PrivateCompanionPageApiUsersGroupsMixin

PLUGIN_NAME = "astrbot_plugin_private_companion"
PAGE_API_PREFIX = f"/{PLUGIN_NAME}/page"


class PrivateCompanionPageApi(PrivateCompanionPageApiQzoneMixin, PrivateCompanionPageApiUsersGroupsMixin):
    """AstrBot 官方插件拓展页面 API。"""

    PERCENT_PROBABILITY_KEYS = {
        "group_repeat_follow_probability",
        "group_repeat_interrupt_probability",
        "group_repeat_interrupt_probability_step",
        "group_wakeup_interest_probability",
        "group_wakeup_topic_interest_max_boost",
        "group_wakeup_debounce_pending_penalty",
        "tts_trigger_probability",
        "auto_voice_probability",
        "main_user_mention_voice_probability",
        "rest_reply_probability",
        "proactive_photo_text_probability",
        "proactive_share_probability",
    }
    INHERIT_PERCENT_PROBABILITY_KEYS = {
        "tts_private_trigger_probability",
        "tts_group_trigger_probability",
        "main_user_voice_probability",
    }
    FRACTIONAL_PERCENT_SETTING_KEYS = {
        "bilibili_share_probability",
        "news_share_probability",
        "external_event_self_link_probability",
        "web_exploration_share_probability",
        "qzone_life_publish_probability",
        "qzone_generated_image_probability",
        "qzone_emotional_vent_probability",
        "proactive_review_hard_risk_threshold",
        "proactive_review_low_score_threshold",
        "proactive_review_pressure_threshold",
        "smart_silence_min_confidence",
        "private_reading_share_probability",
        "private_reading_ask_probability",
        "creative_inspiration_probability",
        "creative_share_probability",
        "skill_growth_schedule_influence_strength",
    }

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self._schema_key_index_cache: dict[str, Any] | None = None

    def register_routes(self) -> None:
        register = self.plugin.context.register_web_api
        routes = [
            ("/overview", self.get_overview, ["GET"], "Private Companion Page overview"),
            ("/users", self.list_users, ["GET"], "Private Companion Page users"),
            ("/user", self.get_user, ["GET"], "Private Companion Page user detail"),
            ("/user/update", self.update_user, ["POST"], "Private Companion Page update user"),
            ("/user/delete", self.delete_user, ["POST"], "Private Companion Page delete user"),
            ("/groups", self.list_groups, ["GET"], "Private Companion Page groups"),
            ("/group", self.get_group, ["GET"], "Private Companion Page group detail"),
            ("/group/update", self.update_group, ["POST"], "Private Companion Page update group"),
            ("/group/delete", self.delete_group, ["POST"], "Private Companion Page delete group"),
            ("/group/slang/update", self.update_group_slang, ["POST"], "Private Companion Page update group slang"),
            ("/settings/update", self.update_settings, ["POST"], "Private Companion Page update settings"),
            ("/config/export", self.export_migration_config, ["GET"], "Private Companion Page export migration config"),
            ("/config/backups", self.list_migration_backups, ["GET"], "Private Companion Page list migration backups"),
            ("/config/restore", self.restore_migration_backup, ["POST"], "Private Companion Page restore migration backup"),
            ("/config/import/preview", self.preview_migration_config_import, ["POST"], "Private Companion Page preview migration config import"),
            ("/config/import/apply", self.apply_migration_config_import, ["POST"], "Private Companion Page apply migration config import"),
            ("/proactive_only/unlock", self.update_proactive_only_unlock, ["POST"], "Private Companion Page proactive-only temporary unlock"),
            ("/proactive/candidate/delete", self.delete_proactive_candidate, ["POST"], "Private Companion Page delete proactive candidate"),
            ("/proactive/candidate/prune", self.prune_proactive_candidates, ["POST"], "Private Companion Page prune proactive candidates"),
            ("/diagnostics", self.get_diagnostics, ["GET"], "Private Companion Page diagnostics"),
            ("/troubleshooting", self.get_troubleshooting, ["GET"], "Private Companion Page troubleshooting"),
            ("/troubleshooting/test", self.run_troubleshooting_test, ["POST"], "Private Companion Page troubleshooting test"),
            ("/token/stats", self.get_token_stats, ["GET"], "Private Companion Page token stats"),
            ("/token/reset", self.reset_token_stats, ["POST"], "Private Companion Page reset token stats"),
            ("/image_cache/list", self.list_image_cache, ["GET"], "Private Companion Page image cache list"),
            ("/image_cache/preview", self.get_image_cache_preview, ["GET"], "Private Companion Page image cache preview"),
            ("/image_cache/update", self.update_image_cache_item, ["POST"], "Private Companion Page update image cache item"),
            ("/image_cache/delete", self.delete_image_cache_item, ["POST"], "Private Companion Page delete image cache item"),
            ("/daily_outfit/image", self.get_daily_outfit_image, ["GET"], "Private Companion Page daily outfit image"),
            ("/daily_outfit/image_data", self.get_daily_outfit_image_data, ["GET"], "Private Companion Page daily outfit image data"),
            ("/bookshelf/unlock", self.unlock_bookshelf, ["POST"], "Private Companion Page unlock bookshelf"),
            ("/bookshelf/image", self.get_bookshelf_image, ["GET"], "Private Companion Page bookshelf image"),
            ("/bookshelf/image_data", self.get_bookshelf_image_data, ["GET"], "Private Companion Page bookshelf image data"),
            ("/bookshelf/delete", self.delete_bookshelf_item, ["POST"], "Private Companion Page delete bookshelf item"),
            ("/bookshelf/rate", self.rate_bookshelf_item, ["POST"], "Private Companion Page rate bookshelf item"),
            ("/bookshelf/tags", self.update_bookshelf_item_tags, ["POST"], "Private Companion Page update bookshelf item tags"),
            ("/bookshelf/comments/update", self.update_bookshelf_item_comments, ["POST"], "Private Companion Page update bookshelf item comments"),
            ("/qzone/status", self.get_qzone_status, ["GET"], "Private Companion Page qzone status"),
            ("/qzone/health", self.get_qzone_status, ["GET"], "Private Companion Page qzone status alias"),
            ("/qzone/summary", self.get_qzone_status, ["GET"], "Private Companion Page qzone status alias"),
            ("/qzone/state", self.get_qzone_status, ["GET"], "Private Companion Page qzone status alias"),
            ("/qzone/feed", self.get_qzone_feed, ["GET"], "Private Companion Page qzone feed"),
            ("/qzone/feeds", self.get_qzone_feed, ["GET"], "Private Companion Page qzone feed alias"),
            ("/qzone/list", self.get_qzone_feed, ["GET"], "Private Companion Page qzone feed alias"),
            ("/qzone/detail", self.get_qzone_detail, ["GET"], "Private Companion Page qzone detail"),
            ("/qzone/post", self.get_qzone_detail, ["GET"], "Private Companion Page qzone detail alias"),
            ("/qzone/item", self.get_qzone_detail, ["GET"], "Private Companion Page qzone detail alias"),
            ("/qzone/refresh_cookies", self.refresh_qzone_cookies, ["POST"], "Private Companion Page qzone refresh cookies"),
            ("/qzone/refresh-cookies", self.refresh_qzone_cookies, ["POST"], "Private Companion Page qzone refresh cookies alias"),
            ("/qzone/cookies/refresh", self.refresh_qzone_cookies, ["POST"], "Private Companion Page qzone refresh cookies alias"),
            ("/qzone/cookie/refresh", self.refresh_qzone_cookies, ["POST"], "Private Companion Page qzone refresh cookies alias"),
            ("/qzone/refresh", self.refresh_qzone_cookies, ["POST"], "Private Companion Page qzone refresh cookies alias"),
            ("/qzone/publish", self.publish_qzone_post, ["POST"], "Private Companion Page qzone publish"),
            ("/qzone/post/publish", self.publish_qzone_post, ["POST"], "Private Companion Page qzone publish alias"),
            ("/qzone/post", self.publish_qzone_post, ["POST"], "Private Companion Page qzone publish alias"),
            ("/qzone/like", self.like_qzone_post, ["POST"], "Private Companion Page qzone like"),
            ("/qzone/post/like", self.like_qzone_post, ["POST"], "Private Companion Page qzone like alias"),
            ("/qzone/comment", self.comment_qzone_post, ["POST"], "Private Companion Page qzone comment"),
            ("/qzone/post/comment", self.comment_qzone_post, ["POST"], "Private Companion Page qzone comment alias"),
            ("/qzone/delete", self.delete_qzone_post, ["POST"], "Private Companion Page qzone delete"),
            ("/qzone/post/delete", self.delete_qzone_post, ["POST"], "Private Companion Page qzone delete alias"),
            ("/creative/project", self.get_creative_project, ["GET"], "Private Companion Page creative project detail"),
            ("/creative/project/update", self.update_creative_project, ["POST"], "Private Companion Page update creative project"),
            ("/creative/project/chunk/update", self.update_creative_chunk, ["POST"], "Private Companion Page update creative chunk"),
            ("/creative/project/outline/update", self.update_creative_outline, ["POST"], "Private Companion Page update creative outline"),
            ("/creative/project/characters/update", self.update_creative_characters, ["POST"], "Private Companion Page update creative characters"),
            ("/creative/project/reanalyze", self.reanalyze_creative_project, ["POST"], "Private Companion Page reanalyze creative project"),
            ("/creative/project/rebuild_memory", self.rebuild_creative_memory, ["POST"], "Private Companion Page rebuild creative memory"),
            ("/creative/project/delete", self.delete_creative_project, ["POST"], "Private Companion Page delete creative project"),
            ("/worldbook/import", self.import_worldbook, ["POST"], "Private Companion Page import worldbook"),
            ("/worldbook/member/livingmemory", self.get_worldbook_member_livingmemory, ["GET"], "Private Companion Page worldbook member LivingMemory"),
            ("/worldbook/member/update", self.update_worldbook_member, ["POST"], "Private Companion Page update worldbook member"),
            ("/worldbook/observations/clear", self.clear_worldbook_pending_observations, ["POST"], "Private Companion Page clear worldbook pending observations"),
            ("/worldbook/group/update", self.update_worldbook_group, ["POST"], "Private Companion Page update worldbook group"),
            ("/skill/update", self.update_skill_growth, ["POST"], "Private Companion Page update skill growth"),
            ("/food_menu/update", self.update_food_menu, ["POST"], "Private Companion Page update food menu"),
            ("/food_menu/bulk_update", self.bulk_update_food_menu, ["POST"], "Private Companion Page bulk update food menu"),
            ("/external_ability/update", self.update_external_ability, ["POST"], "Private Companion Page update external proactive ability"),
            ("/roleplay/draft_from_persona", self.generate_roleplay_draft_from_persona, ["POST"], "Private Companion Page roleplay draft from persona"),
            ("/preset/apply", self.apply_preset, ["POST"], "Private Companion Page apply preset"),
            ("/providers/available", self.list_available_providers, ["GET"], "Private Companion Page available providers"),
            ("/provider/test", self.test_provider, ["POST"], "Private Companion Page test provider"),
        ]
        for path, handler, methods, desc in routes:
            register(f"{PAGE_API_PREFIX}{path}", handler, methods, desc)

    async def get_overview(self) -> dict[str, Any]:
        start = time.perf_counter()
        try:
            async with self.plugin._data_lock:
                refresher = getattr(self.plugin, "_refresh_sleep_runtime_state", None)
                if callable(refresher):
                    refresher()
                detail_sanitizer = getattr(self.plugin, "_sanitize_detail_enhanced_segments_inplace", None)
                if callable(detail_sanitizer):
                    enhanced = self.plugin.data.get("detail_enhanced_segments")
                    if isinstance(enhanced, dict) and detail_sanitizer(enhanced):
                        self.plugin._save_data_sync()
                data = self._overview_data_snapshot_locked(self.plugin.data)
                token_stats = self._token_overview_payload(self.plugin.data.get("token_usage", {}))
            users = data.get("users") if isinstance(data.get("users"), dict) else {}
            groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}
            enabled_users = sum(1 for item in users.values() if isinstance(item, dict) and item.get("enabled", True))
            visible_groups = {
                str(group_id): group
                for group_id, group in groups.items()
                if isinstance(group, dict) and not self._looks_like_member_shadow_group(str(group_id), group)
            }
            enabled_groups = sum(1 for item in visible_groups.values() if isinstance(item, dict) and item.get("enabled", True))
            payload = {
                "plugin": {
                    "enabled": bool(getattr(self.plugin, "enabled", False)),
                    "bot_name": getattr(self.plugin, "bot_name", ""),
                    "data_file": getattr(self.plugin, "data_file", ""),
                    "storage_backend": getattr(self.plugin, "storage_backend", "json"),
                    "storage_sqlite_path": getattr(
                        self.plugin,
                        "storage_sqlite_effective_path",
                        getattr(self.plugin, "storage_sqlite_path", ""),
                    ),
                    "data_version": data.get("version"),
                },
                "private": {
                    "user_count": len(users),
                    "enabled_user_count": enabled_users,
                    "require_opt_in": bool(getattr(self.plugin, "require_private_opt_in", True)),
                    "admin_ids": list(self.plugin._configured_admin_ids()) if hasattr(self.plugin, "_configured_admin_ids") else [],
                    "target_user_ids": list(self.plugin._configured_target_ids()) if hasattr(self.plugin, "_configured_target_ids") else [],
                    "max_daily_messages": getattr(self.plugin, "max_daily_messages", 0),
                    "idle_minutes": getattr(self.plugin, "idle_minutes", 0),
                    "min_interval_minutes": getattr(self.plugin, "min_interval_minutes", 0),
                },
                "group": {
                    "enabled": bool(getattr(self.plugin, "enable_group_companion", False)),
                    "group_count": len(visible_groups),
                    "enabled_group_count": enabled_groups,
                    "shadow_group_count": max(0, len(groups) - len(visible_groups)),
                    "access_mode": getattr(self.plugin, "group_access_mode", "whitelist"),
                    "whitelist": self.plugin._configured_group_ids(),
                    "blacklist": self.plugin._configured_group_blacklist_ids(),
                    "interjection_enabled": bool(getattr(self.plugin, "enable_group_interjection", False)),
                    "repeat_follow_enabled": bool(getattr(self.plugin, "enable_group_repeat_follow", False)),
                },
                "features": self._feature_flags(),
                "proactive_intensity": self._proactive_intensity_summary(),
                "proactive_only": self._proactive_only_mode_snapshot(),
                "providers": self._provider_settings(),
                "settings": self._runtime_settings(),
                "cache": self._cache_summary(data),
                "livingmemory": self._livingmemory_summary(),
                "screen_companion": self._screen_companion_summary(data),
                "knowledge": self.plugin._roleplay_knowledge_summary(),
                "worldbook": self._worldbook_summary(data),
                "proactive_candidates": self._proactive_candidate_summary(data),
                "proactive_tasks": self._proactive_task_summary(data),
                "message_debounce": self._message_debounce_summary(data),
                "bilibili": self._bilibili_summary(data),
                "news": self._news_summary(data),
                "web_exploration": self._web_exploration_summary(data),
                "qzone": self._qzone_summary(data),
                "private_reading": self._jm_cosmos_summary(data),
                "creative": self._creative_summary(data),
                "bookshelf": await self._bookshelf_summary(data, unlocked=False),
                "skill_growth": self._skill_growth_summary(data),
                "food_menu": self._food_menu_summary(data),
                "external_abilities": self._external_ability_summary(data),
                "life_observation": self._life_observation_summary(data),
                "daily_state": self._daily_state_summary(data.get("daily_state")),
                "daily_timeline": self._daily_timeline_summary(data),
                "daily_outfit": self._daily_outfit_summary(data),
                "token_stats": token_stats,
            }
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            if elapsed_ms > 1200:
                logger.warning(
                    "[PrivateCompanionPage] 总览接口耗时较高: elapsed=%sms users=%s groups=%s",
                    elapsed_ms,
                    len(users),
                    len(visible_groups),
                )
            return self._ok(payload)
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 获取总览失败: {exc}", exc_info=True)
            return self._error(str(exc))

    def _token_overview_payload(self, usage: Any) -> dict[str, Any]:
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
        return {
            "updated_at": self._single_line(usage.get("updated_at"), 24),
            "totals": totals,
            "budget": budget,
            "memory_plugin": self._token_memory_plugin_payload(self._memory_plugin_token_usage_raw()),
            "partial": True,
        }

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
            "jm_cosmos_integration",
            "private_reading_state",
            "skill_growth",
            "food_menu",
            "external_proactive_abilities",
            "proactive_runtime",
            "message_debounce",
            "smart_message_debounce",
        ):
            value = raw_data.get(key)
            if isinstance(value, dict):
                data[key] = dict(value)
            elif value is not None:
                data[key] = value

        for key, limit in (
            ("proactive_candidate_pool", 240),
            ("proactive_audit_log", 120),
            ("bot_diaries", 8),
            ("dream_fragments", 40),
            ("schedule_adjustments", 24),
            ("bookshelf_items", 60),
            ("creative_projects", 24),
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

    def _daily_outfit_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        item = data.get("daily_outfit_photo") if isinstance(data.get("daily_outfit_photo"), dict) else {}
        path = self._single_line(item.get("path"), 300)
        exists = False
        if path:
            try:
                exists = Path(path).exists() and Path(path).is_file()
            except Exception:
                exists = False
        date_key = self._single_line(item.get("date"), 20)
        image_query = f"?date={quote(date_key)}&ts={self._single_line(item.get('generated_at'), 40)}" if exists else ""
        return {
            "enabled": bool(getattr(self.plugin, "enable_daily_outfit_photo", False)),
            "date": date_key,
            "available": bool(exists),
            "path": path if exists else "",
            "image_url": f"/daily_outfit/image{image_query}" if exists else "",
            "image_data_url": f"/daily_outfit/image_data{image_query}" if exists else "",
            "backend": self._single_line(item.get("backend"), 80),
            "error": self._single_line(item.get("error"), 220),
            "generated_at": self.plugin._format_timestamp_elapsed(item.get("generated_at", 0)) if item else "",
            "retry_count": int(item.get("retry_count", 0) or 0),
            "retry_max": 5,
        }

    def _daily_outfit_image_path(self) -> Path | None:
        data = getattr(self.plugin, "data", {}) if isinstance(getattr(self.plugin, "data", {}), dict) else {}
        item = data.get("daily_outfit_photo") if isinstance(data.get("daily_outfit_photo"), dict) else {}
        path_text = self._single_line(item.get("path"), 500)
        if not path_text:
            return None
        try:
            path = Path(path_text).resolve()
        except Exception:
            return None
        if not path.exists() or not path.is_file():
            return None
        return path

    async def get_daily_outfit_image(self):
        path = self._daily_outfit_image_path()
        if path is None:
            return self._error("今日穿搭图片不存在")
        response = await send_file(str(path))
        response.headers["Cache-Control"] = "private, max-age=3600"
        return response

    async def get_daily_outfit_image_data(self) -> dict[str, Any]:
        path = self._daily_outfit_image_path()
        if path is None:
            return self._error("今日穿搭图片不存在")
        try:
            mime = mimetypes.guess_type(str(path))[0] or "image/png"
            raw = await asyncio.to_thread(path.read_bytes)
            return self._ok(
                {
                    "mime": mime,
                    "size": len(raw),
                    "data_url": f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}",
                }
            )
        except Exception as exc:
            logger.warning("[PrivateCompanionPage] 读取每日穿搭图片失败: %s", self._single_line(exc, 160))
            return self._error("读取每日穿搭图片失败")

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
        return {
            "private_image_vision": {
                "enabled": bool(getattr(self.plugin, "enable_private_image_vision_cache", False)),
                "items": image_cache_count,
                "max_items": int(getattr(self.plugin, "private_image_vision_cache_max_items", 0) or 0),
                "private": metric_row("image_vision:private_image"),
                "forward": metric_row("image_vision:forward_image"),
            },
            "atrelay_member_cache": {
                "items": atrelay_count,
                "ttl_minutes": int(getattr(self.plugin, "atrelay_member_cache_minutes", 0) or 0),
            },
            "weather": {
                "cached": bool(weather),
                "age": weather_age,
            },
        }

    async def list_image_cache(self) -> dict[str, Any]:
        try:
            scope_filter = self._single_line(request.args.get("scope"), 40)
            keyword = self._single_line(request.args.get("q"), 120).lower()
            limit = self._query_int("limit", 80, 1, 300)
            offset = self._query_int("offset", 0, 0, 100000)
            async with self.plugin._data_lock:
                data = deepcopy(self.plugin.data)
            cache = data.get("private_image_vision_cache") if isinstance(data.get("private_image_vision_cache"), dict) else {}
            rows: list[dict[str, Any]] = []
            scopes: set[str] = set()
            for key, raw in cache.items():
                if not isinstance(raw, dict):
                    continue
                item = self._image_cache_item_summary(str(key), raw)
                scope = item.get("scope") or "private_image"
                scopes.add(str(scope))
                if scope_filter and scope_filter != "all" and scope != scope_filter:
                    continue
                if keyword:
                    haystack = " ".join(
                        str(item.get(name) or "")
                        for name in (
                            "key",
                            "text",
                            "provider_id",
                            "scope",
                            "image_keys_text",
                            "image_aliases_text",
                            "image_type",
                            "ownership",
                            "intent",
                        )
                    ).lower()
                    if keyword not in haystack:
                        continue
                rows.append(item)
            rows.sort(
                key=lambda item: (
                    self._float(item.get("last_hit_ts"))
                    or self._float(item.get("created_ts")),
                    self._float(item.get("created_ts")),
                ),
                reverse=True,
            )
            total = len(rows)
            return self._ok(
                {
                    "items": rows[offset : offset + limit],
                    "total": total,
                    "offset": offset,
                    "limit": limit,
                    "scopes": sorted(scopes),
                    "enabled": bool(getattr(self.plugin, "enable_private_image_vision_cache", False)),
                    "max_items": int(getattr(self.plugin, "private_image_vision_cache_max_items", 0) or 0),
                }
            )
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 获取图片缓存失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def update_image_cache_item(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        key = self._single_line(payload.get("key"), 120)
        if not key:
            return self._error("缺少缓存 key")
        text = str(payload.get("text") or "").strip()
        if not text:
            return self._error("视觉摘要不能为空")
        try:
            async with self.plugin._data_lock:
                cache = self.plugin.data.get("private_image_vision_cache")
                if not isinstance(cache, dict) or not isinstance(cache.get(key), dict):
                    return self._error("缓存条目不存在")
                item = cache[key]
                scope = self._single_line(item.get("scope"), 40) or "private_image"
                item["text"] = self._single_line(text, 900 if scope == "forward_image" else 600)
                if "provider_id" in payload:
                    item["provider_id"] = self._single_line(payload.get("provider_id"), 160)
                if "scope" in payload:
                    next_scope = self._single_line(payload.get("scope"), 40)
                    if next_scope:
                        item["scope"] = next_scope
                item["edited_ts"] = time.time()
                self.plugin._save_data_sync()
                updated = deepcopy(item)
            return self._ok(self._image_cache_item_summary(key, updated))
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 更新图片缓存失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def get_image_cache_preview(self) -> Any:
        key = self._single_line(request.args.get("key"), 120)
        if not key:
            return self._error("缺少缓存 key")
        try:
            async with self.plugin._data_lock:
                cache = deepcopy(self.plugin.data.get("private_image_vision_cache") or {})
            item = cache.get(key) if isinstance(cache, dict) else None
            if not isinstance(item, dict):
                return self._error("缓存条目不存在")
            preview_path = self._single_line(item.get("preview_path"), 260)
            if not preview_path:
                return self._error("该缓存没有预览图")
            path = Path(preview_path).resolve()
            base = (Path(getattr(self.plugin, "data_dir", "")) / "private_image_cache_previews").resolve()
            if not path.is_file() or not path.is_relative_to(base):
                return self._error("预览图不存在")
            response = await send_file(path)
            response.headers["Cache-Control"] = "private, max-age=3600"
            return response
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 获取图片缓存预览失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def delete_image_cache_item(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        key = self._single_line(payload.get("key"), 120)
        if not key:
            return self._error("缺少缓存 key")
        try:
            preview_path = ""
            async with self.plugin._data_lock:
                cache = self.plugin.data.get("private_image_vision_cache")
                if not isinstance(cache, dict) or key not in cache:
                    return self._error("缓存条目不存在")
                removed = cache.pop(key, None)
                if isinstance(removed, dict):
                    preview_path = self._single_line(removed.get("preview_path"), 260)
                self.plugin._save_data_sync()
                remaining = len(cache)
            self._remove_image_cache_preview_file(preview_path)
            return self._ok({"key": key, "remaining": remaining})
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 删除图片缓存失败: {exc}", exc_info=True)
            return self._error(str(exc))

    def _remove_image_cache_preview_file(self, preview_path: str) -> None:
        if not preview_path:
            return
        try:
            path = Path(preview_path).resolve()
            base = (Path(getattr(self.plugin, "data_dir", "")) / "private_image_cache_previews").resolve()
            if path.is_file() and path.is_relative_to(base):
                path.unlink(missing_ok=True)
        except Exception:
            pass

    def _image_cache_item_summary(self, key: str, raw: dict[str, Any]) -> dict[str, Any]:
        text = str(raw.get("text") or "").strip()
        image_keys = [str(value).strip() for value in raw.get("image_keys", []) if str(value or "").strip()]
        image_aliases = [str(value).strip() for value in raw.get("image_aliases", []) if str(value or "").strip()]
        scope = self._single_line(raw.get("scope"), 40) or "private_image"
        created_ts = self._float(raw.get("created_ts"))
        last_hit_ts = self._float(raw.get("last_hit_ts"))
        edited_ts = self._float(raw.get("edited_ts"))
        preview_path = self._single_line(raw.get("preview_path"), 260)
        preview_exists = False
        if preview_path:
            try:
                preview_exists = Path(preview_path).is_file()
            except Exception:
                preview_exists = False
        return {
            "key": self._single_line(key, 120),
            "text": self._single_line(text, 900 if scope == "forward_image" else 600),
            "provider_id": self._single_line(raw.get("provider_id"), 160),
            "scope": scope,
            "prompt_sig": self._single_line(raw.get("prompt_sig"), 32),
            "image_keys": image_keys[:8],
            "image_aliases": image_aliases[:12],
            "image_keys_text": " ".join(image_keys[:8]),
            "image_aliases_text": " ".join(image_aliases[:12]),
            "image_count": _safe_int(raw.get("image_count"), len(image_keys), 0),
            "preview_url": f"{PAGE_API_PREFIX}/image_cache/preview?key={quote(key, safe='')}" if preview_exists else "",
            "preview_size": _safe_int(raw.get("preview_size"), 0, 0),
            "preview_width": _safe_int(raw.get("preview_width"), 0, 0),
            "preview_height": _safe_int(raw.get("preview_height"), 0, 0),
            "hits": _safe_int(raw.get("hits"), 0, 0),
            "created_ts": created_ts,
            "last_hit_ts": last_hit_ts,
            "edited_ts": edited_ts,
            "created": self.plugin._format_timestamp_elapsed(created_ts),
            "last_hit": self.plugin._format_timestamp_elapsed(last_hit_ts),
            "edited": self.plugin._format_timestamp_elapsed(edited_ts),
            "image_type": self._extract_labeled_text(text, "图片类型", 40),
            "visible": self._extract_labeled_text(text, "可见内容", 180),
            "intent": self._extract_labeled_text(text, "图像表达意图", 180),
            "ownership": self._extract_labeled_text(text, "图像归属判断", 80),
        }

    @staticmethod
    def _extract_labeled_text(text: str, label: str, limit: int) -> str:
        source = str(text or "")
        pattern = rf"{re.escape(label)}\s*[：:]\s*(.+?)(?=(?:\s+[^\s：:]{{2,20}}\s*[：:])|$)"
        match = re.search(pattern, source)
        if not match:
            return ""
        return PrivateCompanionPageApi._single_line(match.group(1), limit)

    def _auto_import_worldbook_if_needed_locked(self) -> None:
        if not bool(getattr(self.plugin, "worldbook_auto_import", False)):
            return
        if not bool(getattr(self.plugin, "enable_worldbook_member_recognition", False)):
            return
        data = getattr(self.plugin, "data", {})
        if not isinstance(data, dict):
            return
        has_imported = bool(data.get("worldbook_entries")) or bool(data.get("worldbook_member_profiles")) or bool(data.get("worldbook_group_profiles"))
        if has_imported:
            return
        importer = getattr(self.plugin, "_import_worldbook_entries_from_sources", None)
        if not callable(importer):
            return
        if importer():
            self.plugin._save_data_sync()


    async def update_settings(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        try:
            changed: dict[str, Any] = {}
            if "group_access_mode" in payload:
                mode = str(payload.get("group_access_mode") or "").strip().lower()
                if mode not in {"whitelist", "blacklist"}:
                    return self._error("group_access_mode 只能是 whitelist 或 blacklist")
                changed["group_access_mode"] = mode
            if "group_whitelist_ids" in payload:
                changed["group_whitelist_ids"] = self._normalize_id_list(payload.get("group_whitelist_ids"))
            if "group_blacklist_ids" in payload:
                changed["group_blacklist_ids"] = self._normalize_id_list(payload.get("group_blacklist_ids"))
            for key, value in (payload.get("features") or {}).items():
                if key in self._allowed_feature_keys():
                    changed[key] = self._normalize_bool_value(value)
                elif key in self._schema_bool_keys() and key in self._allowed_setting_keys():
                    changed[key] = self._normalize_bool_value(value)
            provider_payload: dict[str, Any] = {}
            for key, value in (payload.get("providers") or {}).items():
                if key in self._allowed_provider_keys():
                    provider_payload[key] = self._single_line(value, 160)
            for key, value in (payload.get("settings") or {}).items():
                if key in self._allowed_setting_keys():
                    changed[key] = self._normalize_setting_value(key, value)
            if provider_payload:
                if bool(payload.get("overwrite_provider_modes")):
                    mode_value = changed.get("provider_config_mode") or self._config_get("provider_config_mode") or getattr(self.plugin, "provider_config_mode", "quick")
                    provider_payload = self._expand_provider_overwrite_bundle(str(mode_value), provider_payload)
                changed.update(provider_payload)
            storage_changed = bool({"storage_backend", "storage_sqlite_path"} & set(changed))
            apply_overrides = dict(changed)
            if storage_changed:
                apply_overrides["__defer_storage_rebuild"] = True
            for key, value in changed.items():
                self._apply_config_value(key, value, apply_overrides)
            if storage_changed:
                rebuild = getattr(self.plugin, "_rebuild_store_manager", None)
                if callable(rebuild):
                    rebuild(reload_data=True)
            if any(key in self._allowed_provider_keys() for key in changed) or "provider_config_mode" in changed:
                apply_quick = getattr(self.plugin, "_apply_quick_provider_defaults", None)
                if callable(apply_quick):
                    apply_quick()
            clearer = getattr(self.plugin, "_clear_proactive_only_temp_unlocks_if_mode_off", None)
            if callable(clearer):
                clearer()
            config_saved = True
            if changed:
                config_saved = await self._save_config_if_possible()
            overview = await self.get_overview()
            if overview.get("success"):
                overview["data"]["changed"] = changed
                overview["data"]["config_saved"] = config_saved
                data = overview.get("data") if isinstance(overview.get("data"), dict) else {}
                features = data.get("features") if isinstance(data.get("features"), dict) else {}
                settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
                for key, value in changed.items():
                    if key in self._allowed_feature_keys():
                        features[key] = self._normalize_bool_value(value)
                    if key in self._allowed_setting_keys():
                        settings[key] = value
            return overview
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 更新设置失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def export_migration_config(self) -> dict[str, Any]:
        try:
            package = await self._build_migration_package(self._migration_export_options_from_request())
            return self._ok(package)
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 导出配置备份失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def list_migration_backups(self) -> dict[str, Any]:
        try:
            return self._ok({"items": self._list_migration_backup_items(limit=8)})
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 读取配置备份列表失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def restore_migration_backup(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        try:
            backup_id = self._single_line(payload.get("id") or payload.get("name"), 160)
            path = self._resolve_migration_backup_path(backup_id)
            package = json.loads(path.read_text(encoding="utf-8-sig"))
            package = self._extract_migration_package(package)
            normalized = self._normalize_migration_package(package)
            overview = await self._apply_migration_normalized(normalized, mode="replace", conflict="use_backup")
            data = overview.get("data") if isinstance(overview.get("data"), dict) else {}
            data["message"] = "已从自动备份恢复。"
            data["restored_from"] = path.name
            overview["data"] = data
            return overview
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 恢复配置备份失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def preview_migration_config_import(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        try:
            package = self._extract_migration_package(
                payload,
                allow_checksum_mismatch=self._normalize_bool_value(payload.get("allow_checksum_mismatch")),
            )
            normalized = self._normalize_migration_package(package)
            summary = await self._migration_import_summary(normalized)
            summary["message"] = "已读取备份，确认后才会写入。"
            return self._ok(summary)
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 预览配置导入失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def apply_migration_config_import(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        try:
            package = self._extract_migration_package(
                payload,
                allow_checksum_mismatch=self._normalize_bool_value(payload.get("allow_checksum_mismatch")),
            )
            mode = str(payload.get("mode") or "merge").strip().lower()
            if mode not in {"merge", "replace"}:
                mode = "merge"
            conflict = str(payload.get("conflict") or "use_backup").strip().lower()
            if conflict not in {"use_backup", "keep_current", "fill_empty"}:
                conflict = "use_backup"
            normalized = self._normalize_migration_package(package)
            if normalized.get("legacy_snapshot") and mode == "replace":
                mode = "merge"
            return await self._apply_migration_normalized(normalized, mode=mode, conflict=conflict)
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 应用配置导入失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def update_proactive_only_unlock(self) -> dict[str, Any]:
        try:
            payload = await request.get_json(silent=True) or {}
            key = self._single_line(payload.get("key"), 80)
            action = self._single_line(payload.get("action"), 20) or "unlock"
            sync_related = bool(payload.get("sync_related"))
            normalizer = getattr(self.plugin, "_normalize_proactive_only_unlock_key", None)
            normalized = normalizer(key) if callable(normalizer) else key
            if not normalized:
                return self._error("缺少要临时放行的功能")
            applier = getattr(self.plugin, "_apply_proactive_only_temp_unlock", None)
            if not callable(applier):
                return self._error("当前插件缺少主动专用模式临时放行接口")
            clear = action in {"clear", "remove", "cancel", "关闭", "取消"}
            message = applier(normalized, sync_related=sync_related, clear=clear)
            return self._ok(
                {
                    "message": message,
                    "proactive_only": self._proactive_only_mode_snapshot(),
                }
            )
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 更新主动专用临时放行失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def delete_proactive_candidate(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        candidate_id = self._single_line(payload.get("candidate_id") or payload.get("id"), 40)
        if not candidate_id:
            return self._error("缺少 candidate_id")
        try:
            async with self.plugin._data_lock:
                raw = self.plugin.data.get("proactive_candidate_pool")
                if not isinstance(raw, list):
                    raw = []
                    self.plugin.data["proactive_candidate_pool"] = raw
                removed_item = None
                kept = []
                for item in raw:
                    if not isinstance(item, dict):
                        continue
                    if self._single_line(item.get("id"), 40) == candidate_id and removed_item is None:
                        removed_item = dict(item)
                        continue
                    kept.append(item)
                if removed_item is None:
                    return self._error("没有找到对应主动候选")
                self.plugin.data["proactive_candidate_pool"] = kept
                user_id = self._single_line(removed_item.get("user_id"), 40)
                users = self.plugin.data.get("users") if isinstance(self.plugin.data.get("users"), dict) else {}
                cleared_current_plan = False
                if user_id and isinstance(users.get(user_id), dict):
                    user = users[user_id]
                    if self._single_line(user.get("planned_candidate_id"), 40) == candidate_id:
                        clearer = getattr(self.plugin, "_clear_pending_proactive_plan", None)
                        scheduler = getattr(self.plugin, "_schedule_next_proactive", None)
                        if callable(clearer):
                            clearer(user)
                            cleared_current_plan = True
                        if callable(scheduler):
                            scheduler(user, now=time.time())
                    shrinker = getattr(self.plugin, "_shrink_user_proactive_candidates", None)
                    if callable(shrinker):
                        shrinker(user_id, note="page_delete")
                self.plugin._save_data_sync()
                data = self._overview_data_snapshot_locked(self.plugin.data)
            message = "已删除主动候选"
            if cleared_current_plan:
                message += "，并重新安排了下一次主动检查"
            return self._ok(
                {
                    "message": message,
                    "removed": True,
                    "cleared_current_plan": cleared_current_plan,
                    "proactive_candidates": self._proactive_candidate_summary(data),
                    "proactive_tasks": self._proactive_task_summary(data),
                }
            )
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 删除主动候选失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def prune_proactive_candidates(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        user_id = self._single_line(payload.get("user_id"), 40)
        if not user_id:
            return self._error("缺少 user_id")
        keep = self._int(payload.get("keep"), 160, 1, 400)
        try:
            async with self.plugin._data_lock:
                shrinker = getattr(self.plugin, "_shrink_user_proactive_candidates", None)
                if not callable(shrinker):
                    return self._error("当前插件缺少主动候选收缩能力")
                removed = int(shrinker(user_id, pending_cap=keep, note="page_prune") or 0)
                self.plugin._save_data_sync()
                data = self._overview_data_snapshot_locked(self.plugin.data)
            return self._ok(
                {
                    "message": f"已为用户压缩 {removed} 条未发送候选",
                    "removed": removed,
                    "kept_limit": keep,
                    "proactive_candidates": self._proactive_candidate_summary(data),
                    "proactive_tasks": self._proactive_task_summary(data),
                }
            )
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 压缩主动候选失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def get_diagnostics(self) -> dict[str, Any]:
        try:
            async with self.plugin._data_lock:
                raw_users = self.plugin.data.get("users") if isinstance(self.plugin.data.get("users"), dict) else {}
                raw_groups = self.plugin.data.get("groups") if isinstance(self.plugin.data.get("groups"), dict) else {}
                users = {str(key): dict(value) for key, value in raw_users.items() if isinstance(value, dict)}
                groups = {str(key): dict(value) for key, value in raw_groups.items() if isinstance(value, dict)}
            items = self._build_diagnostics(users, groups)
            return self._ok({"items": items})
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 获取诊断失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def get_troubleshooting(self) -> dict[str, Any]:
        try:
            async with self.plugin._data_lock:
                data = deepcopy(self.plugin.data)
            users = data.get("users") if isinstance(data.get("users"), dict) else {}
            groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}
            diagnostics = self._build_diagnostics(users, groups)
            proactive_tasks = self._proactive_task_summary(data)
            proactive_candidates = self._proactive_candidate_summary(data)
            token_stats = self._token_stats_payload(data.get("token_usage", {}))
            cache = self._cache_summary(data)
            tts = self._tts_runtime_summary(users)
            sqlite_status = await self._sqlite_wal_status_summary()
            passive_no_reply = self._passive_no_reply_summary(data)
            screen_companion = self._screen_companion_summary(data)
            qzone = self._qzone_summary(data)
            recent_events = self._troubleshooting_recent_events(
                diagnostics=diagnostics,
                proactive_tasks=proactive_tasks,
                proactive_candidates=proactive_candidates,
                token_stats=token_stats,
                passive_no_reply=passive_no_reply,
            )
            checks = self._troubleshooting_checks(
                data=data,
                users=users,
                diagnostics=diagnostics,
                proactive_tasks=proactive_tasks,
                proactive_candidates=proactive_candidates,
                token_stats=token_stats,
                cache=cache,
                tts=tts,
                sqlite_status=sqlite_status,
            )
            counts = {
                "error": sum(1 for item in recent_events if item.get("level") == "error") + sum(1 for item in checks if item.get("level") == "error"),
                "warn": sum(1 for item in recent_events if item.get("level") == "warn") + sum(1 for item in checks if item.get("level") == "warn"),
                "info": sum(1 for item in recent_events if item.get("level") == "info") + sum(1 for item in checks if item.get("level") == "info"),
                "ok": sum(1 for item in checks if item.get("level") == "ok"),
            }
            headline_level = "error" if counts["error"] else ("warn" if counts["warn"] else "ok")
            headline = "发现需要处理的异常" if headline_level == "error" else ("有可关注项" if headline_level == "warn" else "运行状态正常")
            return self._ok(
                {
                    "summary": {
                        "level": headline_level,
                        "headline": headline,
                        "counts": counts,
                        "generated_at": self.plugin._format_timestamp_elapsed(time.time()),
                    },
                    "recent_events": recent_events[:80],
                    "checks": checks,
                    "diagnostics": diagnostics,
                    "sqlite": sqlite_status,
                    "chain_tests": self._troubleshooting_test_results(data),
                    "recent_photo_generations": self._recent_photo_generation_summary(data),
                    "passive_no_reply": passive_no_reply,
                    "prompt_injections": self._prompt_injection_summary(data),
                    "screen_companion": screen_companion,
                    "qzone": qzone,
                    "proactive_intensity": self._proactive_intensity_summary(),
                    "proactive_runtime": proactive_tasks.get("runtime", {}),
                    "token_budget": token_stats.get("budget", {}),
                    "cache": cache,
                }
            )
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 获取排障信息失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def run_troubleshooting_test(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        test_type = self._single_line(payload.get("type"), 40)
        start = time.time()
        try:
            if test_type in {"image_generation", "image_generation_text2img", "image_generation_selfie"}:
                image_payload = dict(payload)
                if test_type == "image_generation_text2img":
                    image_payload["workflow_kind"] = "text2img"
                elif test_type == "image_generation_selfie":
                    image_payload["workflow_kind"] = "selfie"
                result = await self._run_image_generation_chain_test(image_payload)
            elif test_type == "tts_generation":
                result = await self._run_tts_generation_chain_test(payload)
            elif test_type == "screen_peek":
                result = await self._run_screen_peek_chain_test(payload)
            elif test_type == "qzone_integration":
                result = await self._run_qzone_chain_test(payload)
            elif test_type == "proactive_message":
                result = await self._run_proactive_message_chain_test(payload)
            elif test_type in {"skill_similarity", "model_diagnostics"}:
                result = await self._run_model_diagnostics_check(payload)
            else:
                return self._error("未知排障测试类型")
        except Exception as exc:
            logger.warning("[PrivateCompanionPage] 排障链路测试失败: %s", self._single_line(exc, 160), exc_info=True)
            result = {
                "type": test_type,
                "ok": False,
                "title": self._troubleshooting_test_title(test_type),
                "error": str(exc),
            }
        result["type"] = test_type
        result["elapsed_ms"] = self._int(result.get("elapsed_ms")) or int((time.time() - start) * 1000)
        result["ran_at"] = time.time()
        result["ran_at_text"] = self.plugin._format_timestamp_elapsed(result["ran_at"])
        await self._remember_troubleshooting_test_result(test_type, result)
        return self._ok(result)

    async def _run_image_generation_chain_test(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._sync_photo_generation_runtime_config()
        generator = getattr(self.plugin, "_generate_photo_image", None)
        if not callable(generator):
            return {
                "ok": False,
                "title": "图片生成链路测试",
                "error": "插件缺少图片生成入口 _generate_photo_image",
            }
        reference_getter = getattr(self.plugin, "_photo_persona_reference_image_path", None)
        reference_image_path = ""
        if callable(reference_getter):
            try:
                reference_image_path = self._single_line(reference_getter(), 260)
            except Exception:
                reference_image_path = ""
        configured_reference = self._single_line(getattr(self.plugin, "photo_persona_reference_image_path", ""), 1000).strip().strip('"').strip("'")
        has_reference_source = bool(reference_image_path or re.match(r"^https?://", configured_reference, flags=re.I))
        workflow_kind = self._single_line(payload.get("workflow_kind"), 20)
        if not workflow_kind:
            workflow_kind = "selfie" if has_reference_source else "text2img"
        prompt_text = self._single_line(payload.get("prompt"), 600)
        if not prompt_text and workflow_kind in {"selfie", "portrait", "自拍", "人像"}:
            prompt_text = (
                "排障测试自拍图，保持参考图中的人物身份和外观一致，手机随手自拍构图，"
                "自然室内光，画面干净清晰，真实摄影风格，不包含文字水印"
            )
        if not prompt_text:
            role_appearance = self._troubleshooting_role_appearance_prompt()
            if role_appearance:
                prompt_text = (
                    f"画面主体是这个角色：{role_appearance}。"
                    "角色面向镜头，手举一个简洁的小牌子，牌子上只有一个绿色对钩符号，"
                    "室内日常背景，画面干净清晰，构图自然，真实摄影或精致插画质感；"
                    "不要出现其他文字、水印、额外人物或变形手。"
                )
            else:
                prompt_text = (
                    "排障测试图，一枚小小的绿色对勾贴纸放在白色桌面上，旁边有柔和台灯光，"
                    "画面干净清晰，真实摄影风格，不包含人物、不包含文字水印"
                )
        started = time.time()
        logger.info(
            "[PrivateCompanionPage] 图片生成排障测试开始: workflow_kind=%s prompt_chars=%s reference=%s prompt=%s",
            self._single_line(workflow_kind, 40),
            len(str(prompt_text or "")),
            has_reference_source,
            self._single_line(prompt_text, 180),
        )
        timeout = max(
            45,
            self._int(getattr(self.plugin, "comfyui_photo_wait_seconds", 90)) + 30,
            self._int(getattr(self.plugin, "external_image_api_timeout_seconds", 180)) + 30,
        )
        backend_name, image_path, note = await asyncio.wait_for(
            generator(
                workflow_kind=workflow_kind,
                prompt_text=prompt_text,
                session_key="private_companion_troubleshooting",
            ),
            timeout=timeout,
        )
        elapsed_ms = int((time.time() - started) * 1000)
        exists = False
        file_size = 0
        if image_path:
            try:
                image_file = Path(str(image_path))
                exists = image_file.exists()
                file_size = image_file.stat().st_size if exists else 0
            except Exception:
                exists = False
        logger.info(
            "[PrivateCompanionPage] 图片生成排障测试结束: ok=%s backend=%s elapsed=%sms path=%s exists=%s size=%s note=%s",
            bool(image_path and exists),
            self._single_line(backend_name, 80),
            elapsed_ms,
            self._single_line(image_path, 180),
            exists,
            file_size,
            self._single_line(note, 180),
        )
        return {
            "ok": bool(image_path and exists),
            "title": "图片生成链路测试",
            "backend": self._single_line(backend_name, 80),
            "path": self._single_line(image_path, 260),
            "file_size": file_size,
            "detail": self._single_line(note, 220) or ("已生成图片" if image_path else "未返回图片路径"),
            "prompt": self._single_line(prompt_text, 220),
            "workflow_kind": self._single_line(workflow_kind, 20),
            "reference_image": self._single_line(reference_image_path, 260),
            "used_reference": bool(reference_image_path and workflow_kind in {"selfie", "portrait", "自拍", "人像"}),
            "image_model": self._single_line(getattr(self.plugin, "external_image_api_model", ""), 80),
            "elapsed_ms": elapsed_ms,
            "error": "" if image_path and exists else (self._single_line(note, 220) or "图片生成未返回有效文件"),
        }

    def _troubleshooting_role_appearance_prompt(self) -> str:
        persona = str(getattr(self.plugin, "schedule_persona_prompt", "") or self._config_get("schedule_persona_prompt") or "")
        recognition = str(
            getattr(self.plugin, "private_image_self_recognition_hint", "")
            or self._config_get("private_image_self_recognition_hint")
            or ""
        )
        if not persona and not recognition:
            return ""
        label_prefix = {
            "姓名": "角色名",
            "种族": "种族",
            "性别": "性别",
            "识别点": "主要识别点",
            "外貌": "外貌",
            "主要识别点": "主要识别点",
            "发型发色": "发型发色",
            "发色": "发色",
            "发型": "发型",
            "瞳色": "瞳色",
            "眼睛": "眼睛",
            "服饰风格": "服饰风格",
            "服装": "服装",
            "衣着": "衣着",
        }
        visual_labels = {
            "识别点",
            "外貌",
            "主要识别点",
            "发型发色",
            "发色",
            "发型",
            "瞳色",
            "眼睛",
            "服饰风格",
            "服装",
            "衣着",
        }
        parts: list[str] = []
        has_visual = bool(recognition)
        for line in str(persona or "").replace("\r", "\n").split("\n"):
            text = line.strip()
            if not text or ("：" not in text and ":" not in text):
                continue
            label, value = text.split("：", 1) if "：" in text else text.split(":", 1)
            label = label.strip()
            value = self._single_line(value, 140)
            if label in label_prefix and value:
                if label in visual_labels:
                    has_visual = True
                parts.append(f"{label_prefix[label]}：{value}")
        if recognition:
            parts.append(f"补充识别线索{self._single_line(recognition, 180)}")
        if not has_visual:
            return ""
        seen: set[str] = set()
        unique = []
        for item in parts:
            if item in seen:
                continue
            seen.add(item)
            unique.append(item)
        return self._single_line("，".join(unique), 420)

    async def _run_screen_peek_chain_test(self, payload: dict[str, Any]) -> dict[str, Any]:
        getter = getattr(self.plugin, "_get_screen_companion_plugin", None)
        screen_plugin = getter() if callable(getter) else None
        if screen_plugin is None or not callable(getattr(screen_plugin, "_invoke_screen_skill", None)):
            return {
                "ok": False,
                "title": "窥屏链路测试",
                "error": "未检测到可用的 screen_companion 插件或识屏入口",
            }
        async with self.plugin._data_lock:
            users = deepcopy(self.plugin.data.get("users") if isinstance(self.plugin.data.get("users"), dict) else {})
        umo = self._single_line(payload.get("umo"), 180) or self._preferred_tts_test_umo(users)
        event = None
        if umo and callable(getattr(screen_plugin, "_create_virtual_event", None)):
            try:
                event = screen_plugin._create_virtual_event(umo)
            except Exception as exc:
                logger.info(
                    "[PrivateCompanionPage] 窥屏排障虚拟事件创建失败,将无事件调用: umo=%s error=%s",
                    self._single_line(umo, 120),
                    self._single_line(exc, 120),
                )
        prompt = self._single_line(payload.get("prompt"), 500) or (
            "这是一次插件排障中心发起的授权识屏链路测试。请只判断当前屏幕观察能力是否可用，"
            "用一句很短的内部摘要描述大概画面类型；不要输出账号、完整聊天内容、隐私细节或长文本。"
        )
        started = time.time()
        try:
            raw_result = await asyncio.wait_for(
                screen_plugin._invoke_screen_skill(
                    event,
                    request_prompt=prompt,
                    history_user_text="Private Companion 排障中心正在测试 screen_companion 识屏链路。",
                    task_id="private_companion_troubleshooting_screen_peek",
                ),
                timeout=max(15, self._int(payload.get("timeout_seconds"), 60, 5, 180)),
            )
        except Exception as exc:
            elapsed_ms = int((time.time() - started) * 1000)
            error = self._single_line(exc, 220)
            logger.warning("[PrivateCompanionPage] 窥屏排障测试失败: %s", error, exc_info=True)
            return {
                "ok": False,
                "title": "窥屏链路测试",
                "umo": umo,
                "elapsed_ms": elapsed_ms,
                "error": error or repr(exc),
            }
        elapsed_ms = int((time.time() - started) * 1000)
        context = "screen_peek：\n" + (self._single_line(raw_result, 500) if raw_result else "没有得到屏幕观察结果")
        unusable_checker = getattr(self.plugin, "_is_unusable_screen_peek_context", None)
        unusable = bool(unusable_checker(context)) if callable(unusable_checker) else not bool(raw_result)
        preview = self._single_line(raw_result, 220)
        logger.info(
            "[PrivateCompanionPage] 窥屏排障测试结束: ok=%s elapsed=%sms umo=%s preview=%s",
            not unusable,
            elapsed_ms,
            self._single_line(umo, 120),
            preview,
        )
        return {
            "ok": not unusable,
            "title": "窥屏链路测试",
            "umo": umo,
            "provider": "screen_companion",
            "detail": "已成功获得屏幕观察摘要" if not unusable else "识屏返回为空或不可用结果",
            "text_preview": preview,
            "context_chars": len(str(raw_result or "")),
            "elapsed_ms": elapsed_ms,
            "error": "" if not unusable else (preview or "没有得到屏幕观察结果"),
        }

    async def _run_qzone_chain_test(self, payload: dict[str, Any]) -> dict[str, Any]:
        steps: list[dict[str, str]] = []

        def add_step(name: str, status: str, detail: str) -> None:
            steps.append(
                {
                    "name": self._single_line(name, 40),
                    "status": self._single_line(status, 16),
                    "detail": self._single_line(detail, 180),
                }
            )

        started = time.time()
        service_available = bool(
            callable(getattr(self.plugin, "_test_qzone_integration", None))
            and callable(getattr(self.plugin, "_qzone_get_cookies", None))
        )
        enabled = bool(getattr(self.plugin, "enable_qzone_integration", False))
        comment_enabled = bool(getattr(self.plugin, "enable_qzone_comment_inbox", False))
        add_step("内置服务", "ok" if service_available else "error", "可用" if service_available else "QQ 空间模块入口不可用")
        add_step("整合开关", "ok" if enabled else "warn", "已开启" if enabled else "已关闭")
        if not service_available or not enabled:
            return {
                "ok": False,
                "title": "QQ 空间链路测试",
                "provider": "qzone",
                "detail": "QQ 空间整合未启用或模块入口不可用",
                "text_preview": "开启 QQ 空间整合后再测试 Cookie、读取和发布工具链路。",
                "steps": steps,
                "elapsed_ms": int((time.time() - started) * 1000),
                "error": "QQ 空间整合未启用或模块入口不可用",
            }

        target_id = self._single_line(payload.get("target_id"), 40)
        read_text = ""
        read_ok = False
        read_detail = ""
        reader = getattr(self.plugin, "_test_qzone_integration", None)
        if callable(reader):
            try:
                read_text = await asyncio.wait_for(
                    reader(None, target_id=target_id),
                    timeout=max(15, self._int(payload.get("timeout_seconds"), 45, 10, 180)),
                )
                read_ok = ("读取链路正常" in read_text) or ("读取链路可调用" in read_text)
                read_detail = (
                    (self._qzone_test_line_with_prefix(read_text, "查询结果：失败") if not read_ok else "")
                    or self._qzone_test_last_result_line(read_text)
                    or self._single_line(read_text, 180)
                )
                add_step("Cookie/读取", "ok" if read_ok else "error", read_detail or "读取测试未返回明确结果")
            except Exception as exc:
                read_detail = self._single_line(exc, 180)
                add_step("Cookie/读取", "error", read_detail or "读取测试异常")
        else:
            add_step("Cookie/读取", "error", "缺少 _test_qzone_integration 测试入口")

        publish_ok = False
        publish_detail = ""
        publisher = getattr(self.plugin, "_pc_qzone_publish_feed_impl", None)
        if callable(publisher):
            try:
                raw = await asyncio.wait_for(publisher(None, ""), timeout=15)
                parsed = json.loads(raw) if isinstance(raw, str) else {}
                status = self._single_line(parsed.get("status"), 40) if isinstance(parsed, dict) else ""
                message = self._single_line(parsed.get("message"), 160) if isinstance(parsed, dict) else self._single_line(raw, 160)
                publish_ok = status == "need_text"
                publish_detail = "空参数返回 need_text，发布工具入口正常" if publish_ok else (message or f"返回 {status or '未知状态'}")
                add_step("发布模拟", "ok" if publish_ok else "warn", publish_detail)
            except Exception as exc:
                publish_detail = self._single_line(exc, 180)
                add_step("发布模拟", "error", publish_detail or "发布工具空参数测试异常")
        else:
            add_step("发布模拟", "warn", "缺少发布工具入口，无法测试空参数模拟")

        async with self.plugin._data_lock:
            qzone_state = deepcopy(
                self.plugin.data.get("qzone_integration")
                if isinstance(self.plugin.data.get("qzone_integration"), dict)
                else {}
            )
        list_len = lambda key: len(qzone_state.get(key)) if isinstance(qzone_state.get(key), list) else 0
        seen_count = list_len("comment_inbox_seen_ids") + list_len("comment_inbox_seen_keys")
        replied_count = list_len("comment_inbox_replied_ids") + list_len("comment_inbox_replied_keys")
        inbox_status = self._single_line(qzone_state.get("last_comment_inbox_status"), 120)
        inbox_detail = (
            f"{'已开启' if comment_enabled else '未开启'}；已见 {seen_count}，已回复 {replied_count}"
            + (f"；最近 {inbox_status}" if inbox_status else "")
        )
        add_step("评论收件箱", "ok" if comment_enabled else "info", inbox_detail)

        ok = bool(read_ok and publish_ok)
        preview_parts = [
            read_detail,
            publish_detail,
            inbox_detail,
        ]
        if read_text:
            preview_parts.append(self._single_line(read_text, 500))
        return {
            "ok": ok,
            "title": "QQ 空间链路测试",
            "provider": "qzone",
            "detail": "QQ 空间读取和发布模拟正常" if ok else "QQ 空间链路存在需要处理的项",
            "text_preview": self._single_line("；".join(part for part in preview_parts if part), 500),
            "steps": steps,
            "elapsed_ms": int((time.time() - started) * 1000),
            "error": "" if ok else (read_detail or publish_detail or "QQ 空间链路测试未通过"),
        }

    @staticmethod
    def _qzone_test_last_result_line(text: str) -> str:
        for line in reversed(str(text or "").replace("\r", "\n").split("\n")):
            clean = line.strip().lstrip("-").strip()
            if clean.startswith("结果："):
                return clean
        return ""

    @staticmethod
    def _qzone_test_line_with_prefix(text: str, prefix: str) -> str:
        for line in str(text or "").replace("\r", "\n").split("\n"):
            clean = line.strip().lstrip("-").strip()
            if clean.startswith(prefix):
                return clean
        return ""

    async def _run_tts_generation_chain_test(self, payload: dict[str, Any]) -> dict[str, Any]:
        context = getattr(self.plugin, "context", None)
        if context is None:
            return {
                "ok": False,
                "title": "TTS 生成链路测试",
                "error": "AstrBot context 不可用",
            }
        async with self.plugin._data_lock:
            users = deepcopy(self.plugin.data.get("users") if isinstance(self.plugin.data.get("users"), dict) else {})
        umo = self._single_line(payload.get("umo"), 180) or self._preferred_tts_test_umo(users)
        config: dict[str, Any] = {}
        getter = getattr(context, "get_config", None)
        if callable(getter):
            try:
                config = getter(umo) if umo else getter()
                if not isinstance(config, dict):
                    config = {}
            except Exception:
                config = {}
        provider_getter = getattr(context, "get_using_tts_provider", None)
        tts_provider = None
        if callable(provider_getter):
            try:
                tts_provider = provider_getter(umo) if umo else provider_getter()
            except Exception:
                tts_provider = None
        if tts_provider is None:
            return {
                "ok": False,
                "title": "TTS 生成链路测试",
                "umo": umo,
                "error": "当前会话没有可用 TTS provider",
            }
        provider_settings = dict((config or {}).get("provider_tts_settings", {}) or {})
        spoken = self._single_line(payload.get("text"), 240) or "这是一条排障测试语音，用来确认 TTS 生成链路可以跑通。"
        record_builder = getattr(self.plugin, "_tts_record_component", None)
        started = time.time()
        if callable(record_builder):
            component = await record_builder(
                spoken,
                tts_provider,
                provider_settings,
                config,
                source_text=spoken,
            )
            refs_getter = getattr(self.plugin, "_tts_record_refs", None)
            refs = refs_getter(component) if callable(refs_getter) and component is not None else []
        else:
            audio_path = await tts_provider.get_audio(spoken)
            component = None
            refs = [str(audio_path)] if audio_path else []
        elapsed_ms = int((time.time() - started) * 1000)
        audio_ref = self._single_line(refs[0] if refs else "", 260)
        exists = False
        file_size = 0
        if audio_ref and not re.match(r"^https?://", audio_ref, flags=re.IGNORECASE):
            try:
                audio_file = Path(audio_ref)
                exists = audio_file.exists()
                file_size = audio_file.stat().st_size if exists else 0
            except Exception:
                exists = False
        else:
            exists = bool(audio_ref)
        provider_id = self._provider_id(tts_provider)
        provider_label = self._provider_name(tts_provider, provider_id) if provider_id else getattr(tts_provider, "__class__", type(tts_provider)).__name__
        return {
            "ok": bool(audio_ref and exists),
            "title": "TTS 生成链路测试",
            "umo": umo,
            "provider": self._single_line(provider_label, 100),
            "path": audio_ref,
            "file_size": file_size,
            "detail": "已生成语音组件" if component is not None else "已调用 TTS provider 生成音频",
            "text": spoken,
            "elapsed_ms": elapsed_ms,
            "error": "" if audio_ref and exists else "TTS provider 未返回有效音频文件",
        }

    async def _run_proactive_message_chain_test(self, payload: dict[str, Any]) -> dict[str, Any]:
        steps: list[dict[str, str]] = []

        def add_step(name: str, status: str, detail: str = "") -> None:
            steps.append(
                {
                    "name": self._single_line(name, 40),
                    "status": self._single_line(status, 16) or "info",
                    "detail": self._single_line(detail, 180),
                }
            )

        context = getattr(self.plugin, "context", None)
        if context is None:
            add_step("上下文", "error", "AstrBot context 不可用")
            return {
                "ok": False,
                "title": "主动消息链路测试",
                "steps": steps,
                "error": "AstrBot context 不可用",
            }

        target_user_id = self._single_line(payload.get("user_id"), 80)
        async with self.plugin._data_lock:
            users = deepcopy(self.plugin.data.get("users") if isinstance(self.plugin.data.get("users"), dict) else {})
        target_user_id, target_user = self._preferred_proactive_test_user(users, target_user_id)
        if not target_user_id or not target_user:
            add_step("目标会话", "error", "没有找到已启用且带私聊会话的私聊对象")
            return {
                "ok": False,
                "title": "主动消息链路测试",
                "steps": steps,
                "error": "没有找到可用于测试的私聊对象",
            }
        umo = self._single_line(target_user.get("umo"), 180)
        if not umo:
            add_step("目标会话", "error", "目标用户缺少 umo")
            return {
                "ok": False,
                "title": "主动消息链路测试",
                "user_id": target_user_id,
                "steps": steps,
                "error": "目标用户缺少私聊会话",
            }
        add_step("目标会话", "ok", f"用户 {target_user.get('nickname') or target_user_id} / {umo}")

        now = time.time()
        scheduled_ts = now + 60
        test_id = secrets.token_hex(6)
        plan_keys = (
            "next_proactive_at",
            "planned_proactive_reason",
            "planned_proactive_action",
            "planned_proactive_source",
            "planned_proactive_motive",
            "planned_proactive_topic",
            "planned_proactive_impulse_id",
            "planned_proactive_window_start_at",
            "planned_proactive_best_until_at",
            "planned_proactive_expire_at",
            "planned_proactive_semantic_kind",
            "planned_proactive_anchor_type",
            "planned_proactive_semantic_score",
            "planned_proactive_semantic_note",
            "planned_proactive_model_judge_signature",
            "planned_proactive_model_judge_result",
            "planned_proactive_model_judge_at",
            "planned_event_chain",
            "planned_opener_mode",
            "planned_followup_kind",
            "planned_proactive_quota_exempt",
            "planned_candidate_id",
            "planned_proactive_trigger_message_id",
            "planned_proactive_trigger_umo",
            "planned_proactive_trigger_ts",
            "planned_proactive_trigger_created_at",
            "llm_timer_event",
            "sent_today",
            "proactive_sent_count",
            "ignored_streak",
            "awaiting_reply_since",
            "last_sent",
            "last_companion_message",
            "last_proactive_reason",
            "last_proactive_action",
            "last_proactive_behavior_summary",
            "last_proactive_motive",
            "recent_proactive_topics",
            "proactive_daypart_counts",
            "proactive_afterglow",
            "recent_proactive_afterglows",
            "recent_proactive_hesitations",
            "last_proactive_hesitation_at",
            "last_proactive_hesitation_note",
            "state_continuity",
            "pending_followup_event",
            "suspended_proactive",
            "poke_daily_limit",
            "photo_daily_limit",
            "screen_peek_daily_limit",
        )

        async with self.plugin._data_lock:
            current = self.plugin._get_user(target_user_id)
            if not isinstance(current, dict) or not current.get("enabled", True):
                add_step("临时任务", "error", "目标私聊对象未启用")
                return {
                    "ok": False,
                    "title": "主动消息链路测试",
                    "user_id": target_user_id,
                    "umo": umo,
                    "steps": steps,
                    "error": "目标私聊对象未启用",
                }
            if current.get("proactive_sending"):
                add_step("临时任务", "error", "该用户已有主动发送正在进行")
                return {
                    "ok": False,
                    "title": "主动消息链路测试",
                    "user_id": target_user_id,
                    "umo": umo,
                    "steps": steps,
                    "error": "该用户已有主动发送正在进行",
                }
            if (
                str(current.get("planned_proactive_source") or "") == "troubleshooting"
                and isinstance(current.get("troubleshooting_proactive_restore"), dict)
            ):
                existing_steps = current.get("troubleshooting_proactive_steps") if isinstance(current.get("troubleshooting_proactive_steps"), list) else []
                return {
                    "ok": True,
                    "pending": True,
                    "title": "主动消息链路测试",
                    "user_id": target_user_id,
                    "umo": umo,
                    "steps": existing_steps,
                    "detail": "已有一个排障临时主动任务在等待执行，请约 1 分钟后刷新查看结果",
                    "action": "message",
                    "reason": self._single_line(current.get("planned_proactive_reason"), 40) or "check_in",
                    "error": "",
                }
            restore = {
                "values": {key: deepcopy(current[key]) for key in plan_keys if key in current},
                "missing": [key for key in plan_keys if key not in current],
            }
            current["troubleshooting_proactive_restore"] = restore
            current["troubleshooting_proactive_test_id"] = test_id
            current["troubleshooting_proactive_started_at"] = now
            current["troubleshooting_proactive_steps"] = [
                {"name": "目标会话", "status": "ok", "detail": f"用户 {current.get('nickname') or target_user_id} / {umo}"},
                {"name": "临时任务", "status": "ok", "detail": "已预约 60 秒后由主动循环执行"},
            ]
            current["user_id"] = str(current.get("user_id") or target_user_id)
            current["umo"] = umo
            current["next_proactive_at"] = scheduled_ts
            current["planned_proactive_reason"] = "check_in"
            current["planned_proactive_action"] = "message"
            current["planned_proactive_source"] = "troubleshooting"
            current["planned_proactive_motive"] = "对方希望你主动来找一下,轻轻开口确认主动消息链路能正常工作。"
            current["planned_proactive_topic"] = "主动来找对方一下"
            current["planned_proactive_impulse_id"] = ""
            current["planned_proactive_window_start_at"] = scheduled_ts
            active_span, grace_span = self.plugin._proactive_impulse_default_window_seconds("check_in")
            current["planned_proactive_best_until_at"] = scheduled_ts + active_span
            current["planned_proactive_expire_at"] = scheduled_ts + active_span + grace_span
            semantics = self.plugin._planned_proactive_semantics(current)
            current["planned_proactive_semantic_kind"] = self._single_line(semantics.get("kind"), 40)
            current["planned_proactive_anchor_type"] = self._single_line(semantics.get("anchor_type"), 40)
            current["planned_proactive_semantic_score"] = int(max(0.0, min(1.0, self._float(semantics.get("score")))) * 100)
            current["planned_proactive_semantic_note"] = self._single_line(semantics.get("note"), 180)
            current["planned_event_chain"] = []
            current["planned_opener_mode"] = ""
            current["planned_followup_kind"] = ""
            current["planned_proactive_quota_exempt"] = True
            current["planned_candidate_id"] = f"troubleshooting_{test_id}"
            current["llm_timer_event"] = {}
            current["poke_daily_limit"] = 0
            current["photo_daily_limit"] = 0
            current["screen_peek_daily_limit"] = 0
            result = {
                "ok": True,
                "pending": True,
                "title": "主动消息链路测试",
                "user_id": target_user_id,
                "umo": umo,
                "steps": list(current["troubleshooting_proactive_steps"]),
                "detail": "已预约 1 分钟后的排障临时主动任务；到点后由主动循环走完整生成、复核、发送和归档流程",
                "action": "message",
                "reason": "check_in",
                "error": "",
            }
            raw = self.plugin.data.setdefault("troubleshooting_test_results", {})
            if not isinstance(raw, dict):
                raw = {}
                self.plugin.data["troubleshooting_test_results"] = raw
            raw["proactive_message"] = self._sanitize_troubleshooting_test_result(result)
            self.plugin._save_data_sync()
        add_step("临时任务", "ok", "已预约 60 秒后由主动循环执行")
        return result

    def _preferred_tts_test_umo(self, users: dict[str, Any]) -> str:
        fallback = ""
        for user_id, item in users.items():
            if not isinstance(item, dict) or not item.get("enabled", True) or not item.get("umo"):
                continue
            umo = self._single_line(item.get("umo"), 180)
            if not fallback:
                fallback = umo
            if self.plugin._private_user_role(item, str(item.get("user_id") or user_id)) == "owner":
                return umo
        return fallback

    def _preferred_proactive_test_user(
        self,
        users: dict[str, Any],
        preferred_user_id: str = "",
    ) -> tuple[str, dict[str, Any] | None]:
        if preferred_user_id:
            item = users.get(preferred_user_id)
            if isinstance(item, dict) and item.get("enabled", True) and item.get("umo"):
                item = deepcopy(item)
                item["user_id"] = str(item.get("user_id") or preferred_user_id)
                return preferred_user_id, item
        fallback: tuple[str, dict[str, Any] | None] = ("", None)
        for user_id, item in users.items():
            if not isinstance(item, dict) or not item.get("enabled", True) or not item.get("umo"):
                continue
            candidate = deepcopy(item)
            candidate["user_id"] = str(candidate.get("user_id") or user_id)
            if not fallback[0]:
                fallback = (str(user_id), candidate)
            if self.plugin._private_user_role(candidate, str(candidate.get("user_id") or user_id)) == "owner":
                return str(user_id), candidate
        return fallback

    async def _run_skill_similarity_check(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._run_model_diagnostics_check(payload)

    async def _run_model_diagnostics_check(self, payload: dict[str, Any]) -> dict[str, Any]:
        started = time.time()
        use_model = self._normalize_bool_value(payload.get("use_model", True))
        async with self.plugin._data_lock:
            data = deepcopy(self.plugin.data)
        skill_items = self._skill_similarity_local_candidates(data)
        slang_items = self._model_diagnostics_slang_candidates(data)
        pending_items = self._model_diagnostics_pending_observation_candidates(data)
        memory_items = self._model_diagnostics_companion_memory_candidates(data)
        expression_items = self._model_diagnostics_expression_candidates(data)
        total_local = len(skill_items) + len(slang_items) + len(pending_items) + len(memory_items) + len(expression_items)
        sections: list[dict[str, Any]] = [
            {
                "key": "skills",
                "title": "技能相似项",
                "local_count": len(skill_items),
                "model_count": 0,
                "suggestions": [
                    f"技能｜{item.get('a')} ↔ {item.get('b')}：{item.get('reason')}"
                    for item in skill_items[:4]
                ],
            },
            {
                "key": "slang",
                "title": "群黑话杂音",
                "local_count": len(slang_items),
                "model_count": 0,
                "suggestions": [
                    f"黑话｜{item.get('group_name') or item.get('group_id')}｜{item.get('term')}：{item.get('reason')}"
                    for item in slang_items[:4]
                ],
            },
            {
                "key": "worldbook",
                "title": "关系网待确认观察",
                "local_count": len(pending_items),
                "model_count": 0,
                "suggestions": [
                    f"关系网｜{item.get('name') or item.get('user_id')}：{item.get('reason')}｜{item.get('evidence')}"
                    for item in pending_items[:4]
                ],
            },
            {
                "key": "memory",
                "title": "长期画像噪音",
                "local_count": len(memory_items),
                "model_count": 0,
                "suggestions": [
                    f"长期画像｜{item.get('name') or item.get('user_id')}｜{item.get('field')}：{item.get('reason')}｜{item.get('text')}"
                    for item in memory_items[:4]
                ],
            },
            {
                "key": "expression",
                "title": "表达学习污染",
                "local_count": len(expression_items),
                "model_count": 0,
                "suggestions": [
                    f"表达学习｜{item.get('name') or item.get('user_id')}：{item.get('reason')}｜{item.get('text')}"
                    for item in expression_items[:4]
                ],
            },
        ]
        steps: list[dict[str, str]] = [
            {
                "name": "本地规则",
                "status": "ok" if total_local else "info",
                "detail": (
                    (
                        f"发现 {total_local} 条候选：技能 {len(skill_items)}、黑话 {len(slang_items)}、"
                        f"关系网 {len(pending_items)}、长期画像 {len(memory_items)}、表达学习 {len(expression_items)}"
                    )
                    if total_local
                    else "未发现明显技能冲突、黑话杂音、无效关系观察或私聊学习污染"
                ),
            }
        ]
        model_items: list[str] = []
        provider_id = ""
        if use_model and total_local:
            caller = getattr(self.plugin, "_llm_call", None)
            if callable(caller):
                provider_selector = getattr(self.plugin, "_task_provider", None)
                if callable(provider_selector):
                    provider_id = provider_selector(
                        getattr(self.plugin, "troubleshooting_provider_id", ""),
                        getattr(self.plugin, "response_review_provider_id", ""),
                        getattr(self.plugin, "mai_style_provider_id", ""),
                        getattr(self.plugin, "llm_provider_id", ""),
                    )
                else:
                    provider_id = str(
                        getattr(self.plugin, "troubleshooting_provider_id", "")
                        or getattr(self.plugin, "response_review_provider_id", "")
                        or getattr(self.plugin, "mai_style_provider_id", "")
                        or getattr(self.plugin, "llm_provider_id", "")
                        or ""
                    )
                prompt = self._model_diagnostics_review_prompt(
                    data,
                    skill_items,
                    slang_items,
                    pending_items,
                    memory_items,
                    expression_items,
                )
                try:
                    raw = await caller(
                        prompt,
                        max_tokens=700,
                        provider_id=provider_id,
                        task="troubleshooting_model_diagnostics",
                    )
                    model_items = self._parse_skill_similarity_model_result(raw)
                    self._attach_model_diagnostics_section_suggestions(sections, model_items)
                    steps.append(
                        {
                            "name": "模型复核",
                            "status": "ok" if model_items else "info",
                            "detail": f"模型给出 {len(model_items)} 条建议" if model_items else "模型未给出额外排障建议",
                        }
                    )
                except Exception as exc:
                    steps.append({"name": "模型复核", "status": "warn", "detail": f"调用失败: {self._single_line(exc, 120)}"})
            else:
                steps.append({"name": "模型复核", "status": "warn", "detail": "插件缺少 _llm_call，无法调用模型"})
        elif not use_model:
            steps.append({"name": "模型复核", "status": "info", "detail": "本次按请求仅执行本地规则检查"})
        elif not total_local:
            steps.append({"name": "模型复核", "status": "info", "detail": "没有本地候选，未调用模型"})

        local_preview = []
        for section in sections:
            local_preview.extend(section.get("suggestions") if isinstance(section.get("suggestions"), list) else [])
        all_suggestions = [*local_preview]
        for item in model_items:
            if item not in all_suggestions:
                all_suggestions.append(item)
        detail = (
            f"本地发现 {total_local} 条候选，模型给出 {len(model_items)} 条建议"
            if all_suggestions
            else "模型相关数据目前没有明显杂音"
        )
        return {
            "ok": True,
            "title": "模型数据排障",
            "provider": self._single_line(provider_id, 100),
            "detail": self._single_line(detail, 220),
            "text_preview": self._single_line(" | ".join(all_suggestions[:5]), 220),
            "suggestions": [self._single_line(item, 220) for item in all_suggestions[:12]],
            "sections": sections,
            "local_count": total_local,
            "model_count": len(model_items),
            "suggestion_count": len(all_suggestions),
            "extra_count": max(0, len(all_suggestions) - 5),
            "steps": steps,
            "elapsed_ms": int((time.time() - started) * 1000),
            "error": "",
        }

    def _skill_similarity_local_candidates(self, data: dict[str, Any]) -> list[dict[str, str]]:
        state = data.get("skill_growth") if isinstance(data.get("skill_growth"), dict) else {}
        skills = state.get("skills") if isinstance(state.get("skills"), dict) else {}
        generic_terms = {
            "学习",
            "上课",
            "下课",
            "作业",
            "复习",
            "预习",
            "考试",
            "测验",
            "练习",
            "刷题",
            "做题",
            "题目",
            "课堂",
            "课程",
            "笔记",
            "讲题",
            "错题",
            "背诵",
            "背书",
            "阅读",
            "听课",
            "训练",
        }
        items: list[dict[str, Any]] = []
        for raw in skills.values():
            if not isinstance(raw, dict):
                continue
            name = self._single_line(raw.get("name"), 32)
            if not name:
                continue
            keywords = raw.get("keywords") if isinstance(raw.get("keywords"), list) else []
            aliases = raw.get("aliases") if isinstance(raw.get("aliases"), list) else []
            keyword_terms: set[str] = set()
            for raw_term in keywords:
                term = self._single_line(raw_term, 24)
                if term and term not in generic_terms:
                    keyword_terms.add(term)
            identity_terms: set[str] = set()
            for raw_term in [name, *aliases]:
                term = self._single_line(raw_term, 24)
                if term:
                    identity_terms.add(term)
            items.append(
                {
                    "name": name,
                    "category": self._single_line(raw.get("category"), 24),
                    "hidden": bool(raw.get("hidden")),
                    "frozen": bool(raw.get("frozen")),
                    "keyword_terms": keyword_terms,
                    "identity_terms": identity_terms,
                }
            )
        candidates: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for idx, left in enumerate(items):
            for right in items[idx + 1:]:
                reason = ""
                if left["name"] == right["name"]:
                    reason = "名称完全重复"
                elif left["name"] in right["identity_terms"] or right["name"] in left["identity_terms"]:
                    reason = "名称与对方合并别名冲突"
                elif len(left["name"]) >= 2 and len(right["name"]) >= 2 and (left["name"] in right["name"] or right["name"] in left["name"]):
                    reason = "名称相互包含"
                else:
                    overlap = left["keyword_terms"] & right["keyword_terms"]
                    if left["category"] and left["category"] == right["category"] and len(overlap) >= 3:
                        reason = f"同分类专有关键词重叠: {'、'.join(sorted(overlap)[:4])}"
                if not reason:
                    continue
                key = tuple(sorted([left["name"], right["name"]]) + [reason])
                if key in seen:
                    continue
                seen.add(key)
                candidates.append({"a": left["name"], "b": right["name"], "reason": reason})
        return candidates[:12]

    def _model_diagnostics_slang_candidates(self, data: dict[str, Any]) -> list[dict[str, str]]:
        groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}
        common_terms = {
            "什么",
            "这个",
            "那个",
            "就是",
            "可以",
            "没有",
            "真的",
            "一下",
            "今天",
            "明天",
            "昨天",
            "然后",
            "现在",
            "等等",
            "不是",
            "因为",
            "所以",
            "但是",
            "感觉",
            "可能",
            "应该",
            "好像",
            "知道",
            "看看",
            "消息",
            "图片",
        }
        reaction_terms = {"哈哈", "哈哈哈", "笑死", "草", "好的", "收到", "嗯嗯", "啊啊", "救命", "失败", "报错"}
        media_markers = ("[图片]", "[视频]", "[语音]", "[表情]", "[文件]", "[CQ:", "http://", "https://")
        candidates: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for group_id, group in groups.items():
            if not isinstance(group, dict):
                continue
            group_name = self._single_line(group.get("name") or group.get("group_name") or group_id, 40)
            terms = group.get("slang_terms") if isinstance(group.get("slang_terms"), list) else []
            meanings = group.get("slang_meanings") if isinstance(group.get("slang_meanings"), dict) else {}
            indexed: list[dict[str, Any]] = []
            for raw in terms:
                if isinstance(raw, dict):
                    indexed.append(raw)
                else:
                    indexed.append({"term": raw})
            for raw in indexed[:160]:
                term = self._single_line(raw.get("term") if isinstance(raw, dict) else raw, 50)
                if not term:
                    continue
                if isinstance(raw, dict) and self._single_line(raw.get("source"), 20) == "manual":
                    continue
                compact = re.sub(r"\s+", "", term)
                reason = ""
                if any(marker.lower() in term.lower() for marker in media_markers):
                    reason = "像媒体占位或链接，不像黑话"
                elif re.fullmatch(r"\d{5,}", compact):
                    reason = "像纯数字 ID，不像黑话"
                elif compact in common_terms:
                    reason = "像普通高频词，不像群内黑话"
                elif compact in reaction_terms:
                    reason = "像一次性语气/反应词，容易污染黑话"
                elif len(compact) <= 1 and not re.fullmatch(r"[a-zA-Z]+", compact):
                    reason = "过短，缺少可解释语义"
                elif len(compact) >= 18 and re.search(r"[。！？!?，,]", term):
                    reason = "像整句聊天内容，不像词条"
                else:
                    raw_meaning = meanings.get(term) if isinstance(meanings.get(term), dict) else {}
                    meaning = self._single_line(raw_meaning.get("meaning"), 80) if isinstance(raw_meaning, dict) else ""
                    confidence = self._float(raw_meaning.get("confidence")) if isinstance(raw_meaning, dict) else 0.0
                    count = self._int(raw.get("count")) if isinstance(raw, dict) else 0
                    if meaning and confidence < 0.35 and count <= 2:
                        reason = "低频且释义置信度很低"
                    elif meaning and any(marker in meaning for marker in ("无法判断", "语境不明", "不确定")) and count <= 2:
                        reason = "释义长期不确定，建议复核"
                if not reason:
                    continue
                key = (str(group_id), term)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(
                    {
                        "group_id": self._single_line(group_id, 40),
                        "group_name": group_name,
                        "term": term,
                        "reason": reason,
                        "count": str(self._int(raw.get("count")) if isinstance(raw, dict) else 0),
                    }
                )
                if len(candidates) >= 24:
                    return candidates
        return candidates

    def _model_diagnostics_pending_observation_candidates(self, data: dict[str, Any]) -> list[dict[str, str]]:
        profiles = data.get("worldbook_member_profiles") if isinstance(data.get("worldbook_member_profiles"), dict) else {}
        generic_reactions = {"哈哈", "哈哈哈", "笑死", "草", "好的", "收到", "嗯嗯", "啊啊啊", "救命", "离谱", "不是吧"}
        log_markers = ("Traceback", "[ERROR]", "[WARN]", "Exception", "File \"", "```", "ERROR", "WARN")
        candidates: list[dict[str, str]] = []
        seen_global: set[str] = set()
        for user_id, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            name = self._single_line(profile.get("name") or user_id, 40)
            pending = profile.get("pending_observations") if isinstance(profile.get("pending_observations"), list) else []
            seen_local: set[str] = set()
            for raw in pending[:12]:
                if not isinstance(raw, dict):
                    continue
                evidence = self._single_line(raw.get("evidence") or raw.get("content") or raw.get("title"), 140)
                content = self._single_line(raw.get("content") or evidence, 180)
                compact = re.sub(r"[\s，。！？!?~～…、,.]+", "", evidence)
                norm = compact.lower()
                reason = ""
                if not compact or len(compact) <= 2:
                    reason = "内容过短，难以沉淀成人物观察"
                elif compact in generic_reactions:
                    reason = "像临时语气反应，不像稳定人物信息"
                elif any(marker in evidence or marker in content for marker in log_markers):
                    reason = "像日志/代码片段，不适合写入关系网"
                elif norm in seen_local:
                    reason = "同一成员下重复观察"
                elif norm in seen_global:
                    reason = "跨成员重复泛化观察，可能没有辨识度"
                elif re.fullmatch(r"[\dA-Za-z_-]{8,}", compact):
                    reason = "像 ID 或文件名，不像人物观察"
                elif any(marker in evidence for marker in ("今天", "刚刚", "现在", "一会儿", "等下")) and self._int(raw.get("count")) <= 1:
                    reason = "像临时状态，缺少长期价值"
                seen_local.add(norm)
                seen_global.add(norm)
                if not reason:
                    continue
                candidates.append(
                    {
                        "user_id": self._single_line(user_id, 40),
                        "name": name,
                        "title": self._single_line(raw.get("title"), 50),
                        "evidence": evidence,
                        "reason": reason,
                    }
                )
                if len(candidates) >= 24:
                    return candidates
        return candidates

    def _model_diagnostics_companion_memory_candidates(self, data: dict[str, Any]) -> list[dict[str, str]]:
        users = data.get("users") if isinstance(data.get("users"), dict) else {}
        temporary_tokens = ("今天", "刚刚", "刚才", "现在", "今晚", "明天", "这次", "暂时", "一会儿", "等会儿", "刚睡醒", "刚下课")
        joke_tokens = ("开玩笑", "不是认真的", "随口", "口嗨", "逗你的", "反话", "阴阳怪气")
        log_markers = ("Traceback", "Error code:", "Exception", "[INFO]", "[WARN]", "[ERRO]", "[Core]", "```", "git ", "python ", "node ")
        internal_markers = ("提示词", "系统提示", "内部控制标签", "插件配置", "schema", "token", "cache hit", "PCTTS", "<pc_tts")
        profile_fields = {
            "strong_memories": "强记忆",
            "weak_preferences": "弱偏好",
            "user_traits": "用户画像",
            "interests": "兴趣/偏好",
            "boundaries": "边界/雷点",
            "relationship_notes": "关系线索",
            "speaking_style": "说话习惯",
        }

        def reason_for(text: str) -> str:
            if any(marker.lower() in text.lower() for marker in log_markers):
                return "像日志/代码/报错内容，不适合长期画像"
            if any(marker.lower() in text.lower() for marker in internal_markers):
                return "像系统或插件内部文本，不适合长期画像"
            if any(token in text for token in joke_tokens):
                return "带有玩笑/反讽不确定性，建议人工确认"
            if any(token in text for token in temporary_tokens) and not any(token in text for token in ("以后", "长期", "一直", "固定", "默认", "记住", "记得")):
                return "像临时状态，不像长期画像"
            if re.fullmatch(r"[\dA-Za-z_-]{8,}", re.sub(r"\s+", "", text)):
                return "像 ID 或文件名，不像用户画像"
            return ""

        candidates: list[dict[str, str]] = []
        for user_id, user in users.items():
            if not isinstance(user, dict):
                continue
            name = self._single_line(user.get("nickname") or user.get("name") or user_id, 40)
            memory = user.get("companion_memory") if isinstance(user.get("companion_memory"), dict) else {}
            raw_items = memory.get("items") if isinstance(memory.get("items"), list) else []
            for raw in raw_items[:24]:
                if not isinstance(raw, dict):
                    continue
                text = self._single_line(raw.get("text"), 160)
                reason = reason_for(text)
                if not reason:
                    continue
                candidates.append(
                    {
                        "user_id": self._single_line(user_id, 40),
                        "name": name,
                        "field": self._single_line(raw.get("kind") or "原始记录", 30),
                        "text": text,
                        "reason": reason,
                    }
                )
                if len(candidates) >= 24:
                    return candidates
            profile = memory.get("profile") if isinstance(memory.get("profile"), dict) else {}
            for key, label in profile_fields.items():
                value = profile.get(key)
                values = value if isinstance(value, list) else ([value] if value else [])
                for raw_text in values[:8]:
                    text = self._single_line(raw_text, 160)
                    reason = reason_for(text)
                    if not reason:
                        continue
                    candidates.append(
                        {
                            "user_id": self._single_line(user_id, 40),
                            "name": name,
                            "field": label,
                            "text": text,
                            "reason": reason,
                        }
                    )
                    if len(candidates) >= 24:
                        return candidates
        return candidates

    def _model_diagnostics_expression_candidates(self, data: dict[str, Any]) -> list[dict[str, str]]:
        users = data.get("users") if isinstance(data.get("users"), dict) else {}
        log_markers = ("Traceback", "Error code:", "Exception", "[INFO]", "[WARN]", "[ERRO]", "[Core]", "```", "commit ", "diff ")
        model_markers = ("<pc_tts", "</pc_tts>", "[[PCTTS:", "send_message_to_user", "assistant", "system prompt", "提示词")
        political_markers = (
            "习近平",
            "共产党",
            "中共",
            "六四",
            "天安门",
            "法轮功",
            "台独",
            "港独",
            "藏独",
            "疆独",
            "民主运动",
            "政治敏感",
        )

        def reason_for(text: str) -> str:
            lowered = text.lower()
            if any(marker.lower() in lowered for marker in log_markers):
                return "像日志/代码/报错内容，不该作为表达习惯"
            if any(marker.lower() in lowered for marker in model_markers):
                return "像模型输出格式或内部标签，不该作为表达习惯"
            if any(marker in text for marker in political_markers):
                return "含政治敏感内容，不适合进入表达学习"
            if text.count("…") + text.count("～") + text.count("~") >= 5:
                return "标点留白过多，容易污染表达节奏"
            if text.count("？") + text.count("?") + text.count("！") + text.count("!") >= 6:
                return "疑问/感叹标点过多，容易污染表达节奏"
            return ""

        candidates: list[dict[str, str]] = []
        for user_id, user in users.items():
            if not isinstance(user, dict):
                continue
            name = self._single_line(user.get("nickname") or user.get("name") or user_id, 40)
            profile = user.get("expression_profile") if isinstance(user.get("expression_profile"), dict) else {}
            samples = profile.get("samples") if isinstance(profile.get("samples"), list) else []
            for raw in samples[:36]:
                if not isinstance(raw, dict):
                    continue
                text = self._single_line(raw.get("phrase") or raw.get("ending"), 120)
                marks = raw.get("punctuation") if isinstance(raw.get("punctuation"), dict) else {}
                pause_total = sum(self._int(marks.get(mark)) for mark in ("…", "～", "~"))
                strong_total = sum(self._int(marks.get(mark)) for mark in ("？", "?", "！", "!"))
                direct_reason = ""
                if pause_total >= 5:
                    direct_reason = "标点留白过多，容易污染表达节奏"
                elif strong_total >= 6:
                    direct_reason = "疑问/感叹标点过多，容易污染表达节奏"
                if not text:
                    text = " ".join(f"{mark}×{self._int(count)}" for mark, count in marks.items() if self._int(count) > 0)
                reason = direct_reason or reason_for(text)
                if not reason:
                    continue
                candidates.append(
                    {
                        "user_id": self._single_line(user_id, 40),
                        "name": name,
                        "text": text,
                        "reason": reason,
                    }
                )
                if len(candidates) >= 24:
                    return candidates
            for raw_text in [*(profile.get("recent_phrases") if isinstance(profile.get("recent_phrases"), list) else []), *(profile.get("endings") if isinstance(profile.get("endings"), list) else [])][:36]:
                text = self._single_line(raw_text, 120)
                reason = reason_for(text)
                if not reason:
                    continue
                candidates.append(
                    {
                        "user_id": self._single_line(user_id, 40),
                        "name": name,
                        "text": text,
                        "reason": reason,
                    }
                )
                if len(candidates) >= 24:
                    return candidates
        return candidates

    def _model_diagnostics_review_prompt(
        self,
        data: dict[str, Any],
        skill_candidates: list[dict[str, str]],
        slang_candidates: list[dict[str, str]],
        pending_candidates: list[dict[str, str]],
        memory_candidates: list[dict[str, str]],
        expression_candidates: list[dict[str, str]],
    ) -> str:
        skill_lines = [f"- {item.get('a')} / {item.get('b')}：{item.get('reason')}" for item in skill_candidates[:12]]
        slang_lines = [
            f"- {item.get('group_name') or item.get('group_id')}｜{item.get('term')}｜{item.get('reason')}｜次数:{item.get('count') or 0}"
            for item in slang_candidates[:18]
        ]
        pending_lines = [
            f"- {item.get('name') or item.get('user_id')}｜{item.get('reason')}｜{item.get('evidence')}"
            for item in pending_candidates[:18]
        ]
        memory_lines = [
            f"- {item.get('name') or item.get('user_id')}｜{item.get('field')}｜{item.get('reason')}｜{item.get('text')}"
            for item in memory_candidates[:18]
        ]
        expression_lines = [
            f"- {item.get('name') or item.get('user_id')}｜{item.get('reason')}｜{item.get('text')}"
            for item in expression_candidates[:18]
        ]
        return (
            "你是陪伴插件的数据排障助手。请复核下面这些由本地规则挑出的候选项，只指出明显会影响模型理解的杂音。\n"
            "范围包括：技能相似项、群黑话杂音、关系网待确认观察、长期画像噪音、表达学习污染。不要修改数据，不要发散，不要把正常口癖或真实群梗误报。\n"
            "表达学习污染只关注日志、复制模型格式、政治敏感内容和过度标点等，不要扩展到普通语气判断。\n"
            "输出 1-10 条短建议，每条不超过 45 字，必须用分类前缀：技能｜、黑话｜、关系网｜、长期画像｜、表达学习｜。\n"
            "如果某一类没有明显问题，不要为了凑数输出。若全部无明显问题，输出“未发现明显模型数据杂音”。\n\n"
            "技能候选：\n" + ("\n".join(skill_lines) if skill_lines else "- 无") + "\n\n"
            "群黑话候选：\n" + ("\n".join(slang_lines) if slang_lines else "- 无") + "\n\n"
            "关系网待确认观察候选：\n" + ("\n".join(pending_lines) if pending_lines else "- 无") + "\n\n"
            "长期画像候选：\n" + ("\n".join(memory_lines) if memory_lines else "- 无") + "\n\n"
            "表达学习候选：\n" + ("\n".join(expression_lines) if expression_lines else "- 无")
        )

    def _attach_model_diagnostics_section_suggestions(self, sections: list[dict[str, Any]], suggestions: list[str]) -> None:
        section_by_key = {str(section.get("key")): section for section in sections if isinstance(section, dict)}
        prefix_map = {
            "技能": "skills",
            "黑话": "slang",
            "关系网": "worldbook",
            "长期画像": "memory",
            "表达学习": "expression",
        }
        for suggestion in suggestions:
            prefix = self._single_line(str(suggestion).split("｜", 1)[0], 20)
            key = prefix_map.get(prefix)
            section = section_by_key.get(key or "")
            if not section:
                continue
            items = section.setdefault("suggestions", [])
            if isinstance(items, list):
                items.append(self._single_line(suggestion, 180))
            section["model_count"] = self._int(section.get("model_count")) + 1

    def _skill_similarity_review_prompt(self, data: dict[str, Any], candidates: list[dict[str, str]]) -> str:
        summary = self._skill_growth_summary(data)
        skills = summary.get("items") if isinstance(summary.get("items"), list) else []
        skill_lines = []
        for item in skills[:80]:
            aliases = "、".join(item.get("aliases") or [])
            keywords = "、".join((item.get("keywords") or [])[:8])
            flags = " ".join(flag for flag in ("隐藏" if item.get("hidden") else "", "冻结" if item.get("frozen") else "") if flag)
            skill_lines.append(
                f"- {item.get('name')}｜{item.get('category')}｜{item.get('level_title')}｜别名:{aliases or '-'}｜关键词:{keywords or '-'}｜{flags or '正常'}"
            )
        candidate_lines = [f"- {item.get('a')} / {item.get('b')}：{item.get('reason')}" for item in candidates[:12]]
        return (
            "你是插件排障助手。请检查技能成长列表中是否存在疑似重复技能、别名冲突、过泛关键词或应该隐藏/冻结的项。\n"
            "只根据给出的列表判断，不要发散。不要修改数据，只给建议。\n"
            "不要把“上课、作业、复习、考试、练习、笔记、题目”等通用学习流程词当作技能相似证据。\n"
            "只有名称/别名明显指向同一能力，或多个专有关键词高度重叠时，才建议合并。\n"
            "请输出 1-6 条短建议，每条不超过 45 字；如果没有问题，只输出“未发现需要合并的技能”。\n\n"
            "本地候选：\n" + "\n".join(candidate_lines) + "\n\n"
            "技能列表：\n" + "\n".join(skill_lines)
        )

    def _parse_skill_similarity_model_result(self, raw: Any) -> list[str]:
        text = str(raw or "").strip()
        if not text:
            return []
        lines = []
        for line in re.split(r"[\r\n]+", text):
            item = re.sub(r"^\s*[-*•\d.、)）]+\s*", "", line).strip()
            item = self._single_line(item, 90)
            if re.search(r"^(未发现|没有发现|暂无|无明显|无额外)", item):
                continue
            if item and item not in lines:
                lines.append(item)
        return lines[:10]

    async def _remember_troubleshooting_test_result(self, test_type: str, result: dict[str, Any]) -> None:
        if not test_type:
            return
        try:
            async with self.plugin._data_lock:
                raw = self.plugin.data.setdefault("troubleshooting_test_results", {})
                if not isinstance(raw, dict):
                    raw = {}
                    self.plugin.data["troubleshooting_test_results"] = raw
                raw[test_type] = self._sanitize_troubleshooting_test_result(result)
                self.plugin._save_data_sync()
        except Exception as exc:
            logger.warning("[PrivateCompanionPage] 保存排障测试结果失败: %s", self._single_line(exc, 120))

    def _troubleshooting_test_results(self, data: dict[str, Any]) -> dict[str, Any]:
        raw = data.get("troubleshooting_test_results")
        if not isinstance(raw, dict):
            return {}
        return {
            key: self._sanitize_troubleshooting_test_result(value)
            for key, value in raw.items()
            if isinstance(value, dict)
        }

    def _recent_photo_generation_summary(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        raw = data.get("recent_photo_generations")
        if not isinstance(raw, list):
            return []
        items: list[dict[str, Any]] = []
        for item in raw[:8]:
            if not isinstance(item, dict):
                continue
            ts = self._float(item.get("ts"))
            prompt = str(item.get("prompt") or "")
            items.append(
                {
                    "ts": ts,
                    "time": self.plugin._format_timestamp_elapsed(ts),
                    "trace": self._single_line(item.get("trace"), 40),
                    "session": self._single_line(item.get("session"), 100),
                    "kind": self._single_line(item.get("kind"), 30),
                    "backend": self._single_line(item.get("backend"), 80),
                    "ok": bool(item.get("ok")),
                    "prompt": prompt[:500],
                    "prompt_preview": self._single_line(prompt, 180),
                    "path": self._single_line(item.get("path"), 260),
                    "note": self._single_line(item.get("note"), 220),
                    "reference": bool(item.get("reference")),
                    "reference_path": self._single_line(item.get("reference_path"), 260),
                    "image_size": self._single_line(item.get("image_size"), 40),
                    "elapsed_ms": self._int(item.get("elapsed_ms")),
                    "presets": [
                        self._single_line(name, 40)
                        for name in (item.get("presets") if isinstance(item.get("presets"), list) else [])
                        if self._single_line(name, 40)
                    ][:6],
                }
            )
        return items

    def _prompt_injection_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        raw = data.get("recent_prompt_injections")
        if not isinstance(raw, dict):
            raw = {}
        raw_events = data.get("recent_prompt_injection_events")
        if not isinstance(raw_events, list):
            raw_events = []

        def preview_is_internal_prompt(value: Any) -> bool:
            cleaned = self._single_line(value, 260)
            if not cleaned:
                return False
            internal_markers = (
                "【语音消息规则】",
                "<pc_tts>",
                "</pc_tts>",
                "语音消息规则",
                "提示词片段",
                "请求级环境感知注入",
                "被动回复注入",
                "当前语音正文目标语种",
                "自然聊天时用中文文字推进对话",
                "不要写“中文含义”",
            )
            if any(marker in cleaned for marker in internal_markers):
                return True
            return cleaned.startswith("【") and "规则" in cleaned[:40]

        def safe_message_preview(value: Any, limit: int = 120) -> str:
            preview = self._single_line(value, limit)
            if preview_is_internal_prompt(preview):
                return ""
            return preview

        def normalize_item(item: Any) -> dict[str, Any] | None:
            if not isinstance(item, dict):
                return None
            ts = self._float(item.get("ts"))
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            raw_modules = item.get("modules") if isinstance(item.get("modules"), list) else []
            if not raw_modules:
                normalizer = getattr(self.plugin, "_normalize_prompt_injection_modules", None)
                if callable(normalizer):
                    try:
                        raw_modules = normalizer(str(item.get("content") or ""), None)
                    except Exception:
                        raw_modules = []
            modules: list[dict[str, Any]] = []
            for index, module in enumerate(raw_modules[:28]):
                if not isinstance(module, dict):
                    continue
                content = str(module.get("content") or "")[:6500]
                if not content.strip():
                    continue
                modules.append(
                    {
                        "key": self._single_line(module.get("key"), 100) or f"module.{index + 1}",
                        "source": self._single_line(module.get("source"), 80),
                        "priority": self._int(module.get("priority")),
                        "title": self._single_line(module.get("title"), 80) or "提示词片段",
                        "description": self._single_line(module.get("description"), 260),
                        "chars": self._int(module.get("chars")),
                        "truncated": bool(module.get("truncated")),
                        "preview": self._single_line(module.get("preview"), 220),
                        "content": content,
                    }
                )
            return {
                "ts": ts,
                "time": self.plugin._format_timestamp_elapsed(ts),
                "kind": self._single_line(item.get("kind"), 20),
                "session": self._single_line(item.get("session"), 160),
                "title": self._single_line(item.get("title"), 80),
                "mode": self._single_line(item.get("mode"), 40),
                "chars": self._int(item.get("chars")),
                "truncated": bool(item.get("truncated")),
                "preview": self._single_line(item.get("preview"), 260),
                "trace_seq": self._int(item.get("trace_seq")),
                "content": str(item.get("content") or "")[:13000],
                "modules": modules,
                "metadata": {
                    self._single_line(key, 40): self._single_line(value, 240)
                    for key, value in metadata.items()
                    if self._single_line(key, 40) and self._single_line(value, 240)
                },
            }

        def normalize_message(item: Any) -> dict[str, Any] | None:
            if not isinstance(item, dict):
                return None
            raw_items = item.get("items") if isinstance(item.get("items"), list) else []
            normalized_items = [entry for entry in (normalize_item(raw_item) for raw_item in raw_items[:32]) if entry]
            normalized_items.sort(
                key=lambda entry: (
                    self._float(entry.get("ts")),
                    self._int(entry.get("trace_seq")),
                )
            )
            if not normalized_items:
                return None
            first_ts = self._float(item.get("first_ts")) or min(self._float(entry.get("ts")) for entry in normalized_items)
            last_ts = self._float(item.get("last_ts")) or max(self._float(entry.get("ts")) for entry in normalized_items)
            kinds: list[str] = []
            for entry in normalized_items:
                kind = self._single_line(entry.get("kind"), 20)
                if kind and kind not in kinds:
                    kinds.append(kind)
            message_preview = safe_message_preview(item.get("message_preview"), 120)
            if not message_preview:
                message_preview = next(
                    (
                        safe_message_preview(entry.get("metadata", {}).get("触发消息"), 120)
                        for entry in normalized_items
                        if isinstance(entry, dict)
                        and safe_message_preview(entry.get("metadata", {}).get("触发消息"), 120)
                    ),
                    "",
                )
            return {
                "trace_id": self._single_line(item.get("trace_id"), 80),
                "session": self._single_line(item.get("session"), 160) or self._single_line(normalized_items[-1].get("session"), 160),
                "sender_label": self._single_line(item.get("sender_label"), 80),
                "message_preview": message_preview,
                "first_ts": first_ts,
                "first_time": self.plugin._format_timestamp_elapsed(first_ts),
                "last_ts": last_ts,
                "time": self.plugin._format_timestamp_elapsed(last_ts),
                "item_count": len(normalized_items),
                "module_count": sum(len(entry.get("modules") or []) for entry in normalized_items),
                "kinds": kinds,
                "items": normalized_items,
            }

        result: dict[str, Any] = {}
        for kind in ("tts", "proactive", "passive", "request"):
            items = raw.get(kind) if isinstance(raw.get(kind), list) else []
            limit = 8 if kind == "tts" else 5
            normalized = [entry for entry in (normalize_item(item) for item in items[:limit]) if entry]
            result[kind] = normalized
        messages = [entry for entry in (normalize_message(item) for item in raw_events[:10]) if entry]
        if not messages:
            legacy_messages: list[dict[str, Any]] = []
            for kind in ("request", "passive", "proactive", "tts"):
                for index, item in enumerate(result.get(kind, [])):
                    if not isinstance(item, dict):
                        continue
                    ts = self._float(item.get("ts"))
                    legacy_messages.append(
                        {
                            "trace_id": self._single_line(item.get("trace_id"), 80) or f"legacy-{kind}-{index}",
                            "session": self._single_line(item.get("session"), 160),
                            "sender_label": self._single_line(item.get("metadata", {}).get("发送者"), 80),
                            "message_preview": safe_message_preview(item.get("metadata", {}).get("触发消息"), 120),
                            "first_ts": ts,
                            "first_time": self.plugin._format_timestamp_elapsed(ts),
                            "last_ts": ts,
                            "time": self.plugin._format_timestamp_elapsed(ts),
                            "item_count": 1,
                            "module_count": len(item.get("modules") or []),
                            "kinds": [kind],
                            "items": [item],
                        }
                    )
            legacy_messages.sort(key=lambda entry: self._float(entry.get("last_ts")), reverse=True)
            messages = legacy_messages[:10]
        else:
            messages.sort(key=lambda entry: self._float(entry.get("last_ts")), reverse=True)
        result["messages"] = messages[:10]
        result["message_total"] = len(result["messages"])
        result["total"] = (
            len(result.get("tts", []))
            + len(result.get("proactive", []))
            + len(result.get("passive", []))
            + len(result.get("request", []))
        )
        return result

    def _sanitize_troubleshooting_test_result(self, result: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": self._single_line(result.get("type"), 40),
            "ok": bool(result.get("ok")),
            "pending": bool(result.get("pending")),
            "title": self._single_line(result.get("title"), 60),
            "backend": self._single_line(result.get("backend"), 80),
            "image_model": self._single_line(result.get("image_model"), 80),
            "workflow_kind": self._single_line(result.get("workflow_kind"), 20),
            "reference_image": self._single_line(result.get("reference_image"), 260),
            "used_reference": bool(result.get("used_reference")),
            "provider": self._single_line(result.get("provider"), 100),
            "umo": self._single_line(result.get("umo"), 180),
            "path": self._single_line(result.get("path"), 260),
            "file_size": self._int(result.get("file_size")),
            "detail": self._single_line(result.get("detail"), 220),
            "error": self._single_line(result.get("error"), 220),
            "prompt": self._single_line(result.get("prompt"), 500),
            "text_preview": self._single_line(result.get("text_preview"), 220),
            "original_text_preview": self._single_line(result.get("original_text_preview"), 220),
            "final_text_preview": self._single_line(result.get("final_text_preview"), 220),
            "context_chars": self._int(result.get("context_chars")),
            "action": self._single_line(result.get("action"), 60),
            "reason": self._single_line(result.get("reason"), 40),
            "extra_count": self._int(result.get("extra_count")),
            "local_count": self._int(result.get("local_count")),
            "model_count": self._int(result.get("model_count")),
            "suggestion_count": self._int(result.get("suggestion_count")),
            "suggestions": [
                self._single_line(item, 220)
                for item in (result.get("suggestions") if isinstance(result.get("suggestions"), list) else [])[:12]
                if self._single_line(item, 220)
            ],
            "sections": [
                {
                    "key": self._single_line(section.get("key"), 40),
                    "title": self._single_line(section.get("title"), 60),
                    "local_count": self._int(section.get("local_count")),
                    "model_count": self._int(section.get("model_count")),
                    "suggestions": [
                        self._single_line(item, 220)
                        for item in (section.get("suggestions") if isinstance(section.get("suggestions"), list) else [])[:8]
                        if self._single_line(item, 220)
                    ],
                }
                for section in (result.get("sections") if isinstance(result.get("sections"), list) else [])[:6]
                if isinstance(section, dict)
            ],
            "steps": [
                {
                    "name": self._single_line(step.get("name"), 40),
                    "status": self._single_line(step.get("status"), 16),
                    "detail": self._single_line(step.get("detail"), 180),
                }
                for step in (result.get("steps") if isinstance(result.get("steps"), list) else [])[:12]
                if isinstance(step, dict)
            ],
            "elapsed_ms": self._int(result.get("elapsed_ms")),
            "ran_at": self._float(result.get("ran_at")),
            "ran_at_text": self._single_line(result.get("ran_at_text"), 40),
        }

    @staticmethod
    def _troubleshooting_test_title(test_type: str) -> str:
        return {
            "image_generation": "图片生成链路测试",
            "image_generation_text2img": "文生图链路测试",
            "image_generation_selfie": "自拍参考图链路测试",
            "tts_generation": "TTS 生成链路测试",
            "screen_peek": "窥屏链路测试",
            "qzone_integration": "QQ 空间链路测试",
            "proactive_message": "主动消息链路测试",
            "model_diagnostics": "模型数据排障",
            "skill_similarity": "技能相似项检查",
        }.get(test_type, "排障链路测试")

    @staticmethod
    def _token_task_label(task: Any) -> str:
        normalized = str(task or "").strip()
        if not normalized:
            return "模型调用"
        labels = {
            "daily_plan": "日程生成",
            "detail": "日程细化",
            "dream": "梦境内容",
            "diary": "日记整理",
            "memory_profile": "长期画像",
            "dialogue_episode": "私聊片段",
            "response_review": "回复/主动复核",
            "emotion_judgement": "情绪判断",
            "relationship": "关系分析",
            "group_interject": "群聊插话",
            "group_episode": "群聊片段",
            "group_slang": "黑话释义",
            "group_question_wakeup_reply_review": "群聊答疑复核",
            "group_followup_judge": "群聊续接判断",
            "worldbook_registration": "关系网自登记",
            "web_exploration_query": "探索选题",
            "web_exploration_digest": "探索笔记",
            "external_event_self_link": "外界信息关联",
            "news_digest": "新闻整理",
            "creative_project": "创作立项",
            "creative_outline": "创作大纲",
            "creative_writing": "文本创作",
            "creative_review": "创作审校",
            "creative_extract": "创作抽取",
            "photo_prompt": "生图提示",
            "screen_narration": "识屏转述",
            "forward_message": "合并转发转述",
            "private_reading_vision": "夹层视觉",
            "private_image_vision": "私聊图片识别",
            "private_image_only_framework": "单图回复主链",
            "private_image_only_fallback": "单图兜底回复",
            "voice": "语音文本",
            "proactive_framework": "主动主回复",
            "proactive_persona_judge": "主动人格判定",
            "voice_framework": "框架语音",
            "voice_repair": "语音格式修复",
            "smart_message_debounce": "智能收口防抖",
            "rest_wakeup_judge": "休息醒来判断",
            "yesterday_summary": "昨日摘要",
            "full_test_detail": "完整测试细化",
            "provider_test": "模型测试",
            "qzone_comment": "空间评论",
            "qzone_comment_inbox_decision": "空间评论判断",
            "qzone_publish": "空间说说",
            "qzone_publish_test": "空间发布测试",
            "qzone_publish_sanitize": "空间文案清理",
            "companion_manual_diagnosis": "陪伴答疑",
            "astrbot_private_reply": "非插件私聊主回复",
            "astrbot_group_reply": "非插件群聊主回复",
            "astrbot_reply": "非插件主回复",
            "other": "其他调用",
        }
        if normalized in labels:
            return labels[normalized]
        if normalized.startswith("qzone_") and normalized.endswith("_photo_prompt"):
            return "空间配图提示"
        if normalized.startswith("qzone_"):
            return "QQ 空间任务"
        if normalized.startswith("astrbot_"):
            return "AstrBot 主回复"
        if normalized.startswith("private_image_"):
            return "私聊图片处理"
        if normalized.startswith("web_exploration_"):
            return "主动搜索"
        return normalized

    def _troubleshooting_recent_events(
        self,
        *,
        diagnostics: list[dict[str, Any]],
        proactive_tasks: dict[str, Any],
        proactive_candidates: dict[str, Any],
        token_stats: dict[str, Any],
        passive_no_reply: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []

        def add(level: str, source: str, title: str, detail: str = "", *, ts: float = 0, action: str = "", jump: str = "") -> None:
            events.append(
                {
                    "level": level,
                    "source": source,
                    "title": self._single_line(title, 90),
                    "detail": self._single_line(detail, 220),
                    "action": self._single_line(action, 160),
                    "jump": self._single_line(jump, 40),
                    "ts": self._float(ts),
                    "time": self.plugin._format_timestamp_elapsed(ts) if ts else "",
                }
            )

        for item in diagnostics:
            level = self._single_line(item.get("level"), 12)
            if level not in {"error", "warn"}:
                continue
            add(level, "配置诊断", item.get("title", ""), item.get("text", ""), action=item.get("action", ""), jump="troubleshooting")

        for item in proactive_tasks.get("audit_items", [])[:40]:
            status = self._single_line(item.get("status"), 24)
            if status not in {"failed", "dropped", "deferred"}:
                continue
            note = self._single_line(item.get("note"), 180)
            if status in {"dropped", "deferred"} and not self._proactive_audit_note_needs_troubleshooting(note):
                continue
            level = "error" if status == "failed" else "warn"
            title = item.get("topic") or item.get("reason") or item.get("note") or "主动执行异常"
            detail = "；".join(
                part
                for part in [
                    f"用户 {item.get('user_label') or item.get('user_id') or '-'}",
                    f"动作 {item.get('action') or 'message'}",
                    note,
                    item.get("text_preview") or "",
                ]
                if part
            )
            add(level, "主动审计", title, detail, ts=self._float(item.get("updated_ts") or item.get("created_ts")), jump="proactive")

        for item in proactive_candidates.get("items", [])[:40]:
            if self._single_line(item.get("status"), 24) != "blocked":
                continue
            note = self._single_line(item.get("note"), 180)
            if self._proactive_candidate_block_is_normal(note):
                continue
            title = item.get("topic") or item.get("reason") or "主动候选被拦截"
            detail = "；".join(
                part
                for part in [
                    f"用户 {item.get('user_label') or item.get('user_id') or '-'}",
                    f"动作 {item.get('action') or 'message'}",
                    note,
                ]
                if part
            )
            add("warn", "主动候选", title, detail, ts=self._float(item.get("last_seen_ts") or item.get("created_ts")), jump="proactive")

        for item in self._active_token_failures(token_stats.get("recent", []), limit=50):
            title = f"{self._token_task_label(item.get('task'))}失败"
            detail = "；".join(
                part
                for part in [
                    f"Provider {item.get('provider') or '-'}",
                    item.get("error") or "无错误详情",
                ]
                if part
            )
            add("error", "模型调用", title, detail, ts=self._float(item.get("ts")), jump="tokens")

        passive_items = passive_no_reply.get("items", []) if isinstance(passive_no_reply, dict) else []
        for item in passive_items[:40]:
            if not isinstance(item, dict):
                continue
            count = self._int(item.get("count"))
            source = self._single_line(item.get("source"), 40) or "被动未回复"
            reason = self._single_line(item.get("reason"), 100) or "未说明原因"
            inbound = self._single_line(item.get("last_inbound"), 100)
            detail = "；".join(
                part
                for part in [
                    f"已合并 {count} 次" if count > 1 else "最近 1 次",
                    f"会话 {self._single_line(item.get('last_session'), 80)}" if item.get("last_session") else "",
                    f"消息 {inbound}" if inbound else "",
                    self._single_line(item.get("last_detail"), 120),
                ]
                if part
            )
            add(
                self._single_line(item.get("level"), 12) or "info",
                source,
                reason,
                detail,
                ts=self._float(item.get("last_ts")),
                action="同类原因已合并计数，刷新后可查看最近样本",
                jump="troubleshooting",
            )

        events.sort(key=lambda item: self._float(item.get("ts")), reverse=True)
        return events

    def _passive_no_reply_item_is_obsolete_fixed_error(self, item: dict[str, Any]) -> bool:
        checker = getattr(self.plugin, "_proactive_audit_note_is_obsolete_fixed_error", None)
        texts = [
            item.get("reason"),
            item.get("last_detail"),
            item.get("last_action"),
            item.get("last_reply_preview"),
        ]
        samples = item.get("samples") if isinstance(item.get("samples"), list) else []
        for sample in samples[:5]:
            if not isinstance(sample, dict):
                continue
            texts.extend([sample.get("detail"), sample.get("reply_preview")])
        joined = "\n".join(str(value or "") for value in texts)
        if callable(checker) and checker(joined):
            return True
        return "NameError" in joined and any(
            token in joined
            for token in (
                "name 'topic' is not defined",
                "name 'name' is not defined",
            )
        )

    def _passive_no_reply_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        raw = data.get("passive_no_reply_records")
        if not isinstance(raw, dict):
            return {"total": 0, "items": []}
        items: list[dict[str, Any]] = []
        now = time.time()
        max_age_seconds = 2 * 60 * 60
        hidden_stale = 0
        hidden_obsolete = 0
        for item in raw.get("items", []):
            if not isinstance(item, dict):
                continue
            last_ts = self._float(item.get("last_ts"))
            if self._passive_no_reply_item_is_obsolete_fixed_error(item):
                hidden_obsolete += 1
                continue
            if last_ts > 0 and now - last_ts > max_age_seconds:
                hidden_stale += 1
                continue
            samples = []
            for sample in item.get("samples", []) if isinstance(item.get("samples"), list) else []:
                if not isinstance(sample, dict):
                    continue
                ts = self._float(sample.get("ts"))
                samples.append(
                    {
                        "time": self.plugin._format_timestamp_elapsed(ts) if ts else self._single_line(sample.get("time"), 40),
                        "session": self._single_line(sample.get("session"), 120),
                        "sender_id": self._single_line(sample.get("sender_id"), 80),
                        "inbound": self._single_line(sample.get("inbound"), 120),
                        "detail": self._single_line(sample.get("detail"), 160),
                        "reply_preview": self._single_line(sample.get("reply_preview"), 140),
                        "ts": ts,
                    }
                )
            items.append(
                {
                    "key": self._single_line(item.get("key"), 32),
                    "level": self._single_line(item.get("level"), 12) or "info",
                    "source": self._single_line(item.get("source"), 40) or "被动未回复",
                    "reason": self._single_line(item.get("reason"), 120) or "未说明原因",
                    "count": self._int(item.get("count")),
                    "first_ts": self._float(item.get("first_ts")),
                    "last_ts": last_ts,
                    "last_time": self.plugin._format_timestamp_elapsed(last_ts) if last_ts else "",
                    "last_session": self._single_line(item.get("last_session"), 120),
                    "last_sender_id": self._single_line(item.get("last_sender_id"), 80),
                    "last_inbound": self._single_line(item.get("last_inbound"), 120),
                    "last_detail": self._single_line(item.get("last_detail"), 160),
                    "last_action": self._single_line(item.get("last_action"), 120),
                    "last_reply_preview": self._single_line(item.get("last_reply_preview"), 140),
                    "samples": samples[:5],
                }
            )
        items.sort(key=lambda item: self._float(item.get("last_ts")), reverse=True)
        total = self._int(raw.get("total")) or sum(self._int(item.get("count")) for item in items)
        return {
            "total": total,
            "last_ts": self._float(raw.get("last_ts")),
            "items": items[:80],
            "hidden_stale": hidden_stale,
            "hidden_obsolete": hidden_obsolete,
            "max_age_seconds": max_age_seconds,
        }

    def _active_token_failures(
        self,
        recent: Any,
        *,
        max_age_seconds: float = 30 * 60,
        limit: int = 80,
    ) -> list[dict[str, Any]]:
        if not isinstance(recent, list):
            return []
        now = time.time()
        recovered: set[tuple[str, str]] = set()
        failures: list[dict[str, Any]] = []
        for item in recent[:limit]:
            if not isinstance(item, dict):
                continue
            task = self._single_line(item.get("task"), 40) or "LLM 调用"
            provider = self._single_line(item.get("provider"), 80) or "-"
            key = (task, provider)
            if bool(item.get("success", True)):
                recovered.add(key)
                continue
            ts = self._float(item.get("ts"))
            if ts > 0 and now - ts > max_age_seconds:
                continue
            if key in recovered:
                continue
            failures.append(item)
        return failures

    def _proactive_candidate_block_is_normal(self, note: str) -> bool:
        text = self._single_line(note, 180)
        if not text:
            return True
        exact_normal = {
            "已有更早主动候选",
            "近期主题过于相似",
            "已有用户预约/定时主动",
            "用户明确休息中",
            "当前时段主动已足够,已避开扎堆",
            "朋友主动已按日内节奏延后",
            "已有更早主动候选",
            "用户在该问候窗口内已经活跃过",
            "潜在念头窗口已过期",
            "多来源合并",
            "群聊分享候选已过期",
        }
        if text in exact_normal:
            return True
        normal_tokens = (
            "已有更早",
            "近期主题",
            "过于相似",
            "用户明确休息",
            "休息中",
            "免打扰",
            "已避开扎堆",
            "按日内节奏延后",
            "额度",
            "冷却",
            "间隔",
            "频率",
            "调度过滤",
            "窗口已过期",
            "已经活跃过",
            "多来源合并",
            "候选已过期",
        )
        return any(token in text for token in normal_tokens)

    def _proactive_audit_note_needs_troubleshooting(self, note: str) -> bool:
        text = self._single_line(note, 180)
        if not text:
            return False
        normal_tokens = (
            "主动行为失败或不适合发送",
            "不适合发送",
            "用户明确休息",
            "休息中",
            "免打扰",
            "已有更早",
            "调度过滤",
            "已避开扎堆",
            "按日内节奏延后",
            "冷却",
            "间隔",
            "频率",
            "额度",
            "低分",
            "未达到阈值",
            "窗口已过期",
            "已经活跃过",
            "多来源合并",
            "候选已过期",
        )
        if any(token in text for token in normal_tokens):
            return False
        error_tokens = (
            "异常",
            "报错",
            "Traceback",
            "Error",
            "Exception",
            "provider",
            "模型",
            "超时",
            "不可用",
            "发送失败",
            "生成失败",
            "调用失败",
            "保存失败",
            "处理失败",
            "database is locked",
        )
        return any(token in text for token in error_tokens)

    def _troubleshooting_checks(
        self,
        *,
        data: dict[str, Any],
        users: dict[str, Any],
        diagnostics: list[dict[str, Any]],
        proactive_tasks: dict[str, Any],
        proactive_candidates: dict[str, Any],
        token_stats: dict[str, Any],
        cache: dict[str, Any],
        tts: dict[str, Any],
        sqlite_status: dict[str, Any],
    ) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []

        def add(level: str, title: str, text: str, action: str = "", jump: str = "") -> None:
            checks.append(
                {
                    "level": level,
                    "title": self._single_line(title, 90),
                    "text": self._single_line(text, 240),
                    "action": self._single_line(action, 180),
                    "jump": self._single_line(jump, 40),
                }
            )

        enabled_users = [item for item in users.values() if isinstance(item, dict) and item.get("enabled", True)]
        runtime = proactive_tasks.get("runtime", {})
        daily_limit = self._int(getattr(self.plugin, "max_daily_messages", 0))
        budget = token_stats.get("budget", {})
        if not enabled_users:
            add("warn", "主动消息没有私聊对象", "当前没有启用的私聊对象，主动消息不会有目标。", "到私聊页新增或启用对象", "private")
        elif daily_limit <= 0:
            add("warn", "私聊主动总额度为 0", "每日主动上限为 0 时，主动消息不会发送。", "到模块配置调高每日主动上限", "modules")
        elif not runtime.get("healthy"):
            add("warn", "主动循环心跳不新鲜", runtime.get("last_tick_error") or "最近没有检测到主动循环心跳。", "查看主动页的循环状态", "proactive")
        else:
            add("ok", "主动循环可运行", f"启用对象 {len(enabled_users)} 个，最近心跳 {runtime.get('last_tick_started') or '-'}。", "", "proactive")

        if budget.get("exceeded"):
            add("error", "今日 Token 硬限额已耗尽", f"今日已用 {budget.get('used')}，硬限额 {budget.get('limit')}。", "调高每日 Token 限额或等待明日重置", "tokens")
        elif budget.get("soft_active"):
            add("warn", "Token 软限额正在暂缓后台任务", f"今日已用 {budget.get('used')}，软限额 {budget.get('soft_limit')}；主动生图、新闻、创作等低优先级任务会延后。", "到 Token 页或模块配置检查限额", "tokens")
        else:
            add("ok", "Token 预算未阻塞", f"今日已用 {budget.get('used', 0)}；软限额剩余 {budget.get('soft_remaining') if budget.get('soft_remaining') is not None else '不限'}。", "", "tokens")

        photo_enabled = bool(getattr(self.plugin, "enable_photo_text_action", False))
        photo_available = bool(getattr(self.plugin, "_photo_text_available", lambda *args, **kwargs: False)())
        photo_blocked = [
            item for item in proactive_candidates.get("items", [])
            if "photo_text" in str(item.get("action") or "") and str(item.get("status") or "") == "blocked"
        ]
        photo_blocked_abnormal = [
            item for item in photo_blocked
            if not self._proactive_candidate_block_is_normal(self._single_line(item.get("note"), 180))
        ]
        if not photo_enabled:
            add("warn", "主动带图功能未开启", "enable_photo_text_action 关闭时不会生成主动图片。", "到功能开关打开主动拍照/生图", "config")
        elif not photo_available:
            add("warn", "主动带图后端或额度不可用", "生图后端不可用、每日生图额度用完，或当前对象不允许 photo_text。", "检查生图后端、每日生图上限和用户关系角色", "modules")
        elif photo_blocked_abnormal:
            add("warn", "近期带图候选被拦截", self._single_line(photo_blocked_abnormal[0].get("note"), 160) or "最近 photo_text 候选没有进入发送。", "到主动页筛选 photo_text", "proactive")
        else:
            add("ok", "主动带图链路可尝试", "开关和可用性检查通过；是否出现取决于主动动机、天气/日程和候选权重。", "", "proactive")

        if bool(tts.get("enhancement_enabled")):
            if bool(tts.get("provider_available")):
                add("ok", "TTS provider 可用", f"模式 {tts.get('mode')}，语种 {tts.get('language')}，provider {tts.get('provider_label') or '-'}。", "", "modules")
            else:
                add("warn", "TTS 强化开启但合成 provider 不可用", "插件能处理 TTS 标签，但真实语音合成需要 AstrBot 当前会话启用 TTS provider。", "到 AstrBot 会话 TTS 配置启用 provider", "modules")
        else:
            add("info", "TTS 强化未开启", "模型不应被要求生成 TTS 标签；如仍出现标签，发送前会清理。", "", "modules")

        sqlite_bad = [item for item in sqlite_status.get("items", []) if item.get("level") in {"warn", "error"}]
        if sqlite_bad:
            add("warn", "SQLite 并发状态需要关注", sqlite_bad[0].get("text") or "有数据库未处于 WAL 或检查失败。", "重启插件后查看是否仍有 database is locked", "troubleshooting")
        else:
            add("ok", "SQLite WAL 检查通过", f"已检查 {len(sqlite_status.get('items', []))} 个数据库文件。", "", "troubleshooting")

        token_errors = self._active_token_failures(token_stats.get("recent", []))
        if token_errors:
            first = token_errors[0]
            add("error", "最近存在模型调用失败", first.get("error") or f"{first.get('task') or '任务'} 调用失败。", "到 Token 页查看失败任务和 provider", "tokens")
        else:
            add("ok", "近期模型调用无待处理失败", "近 30 分钟内没有未恢复的模型调用失败；历史失败可在 Token 页查看。", "", "tokens")

        image_cache = cache.get("private_image_vision", {})
        if image_cache.get("enabled"):
            add("ok", "图片视觉缓存已开启", f"当前缓存 {image_cache.get('items', 0)}/{image_cache.get('max_items') or '不限'} 条。", "", "image-cache")
        else:
            add("info", "图片视觉缓存未开启", "重复表情包会重复调用视觉模型，但不影响首次识图。", "到模块配置开启重复图片缓存", "modules")

        diag_warns = [item for item in diagnostics if item.get("level") in {"warn", "error"}]
        if diag_warns:
            add("warn", "配置诊断仍有待处理项", f"{len(diag_warns)} 项需要关注：{diag_warns[0].get('title') or '-'}。", diag_warns[0].get("action") or "查看下方最近异常", "troubleshooting")
        else:
            add("ok", "配置诊断无警告", "现有诊断项没有 warn/error。", "", "dashboard")
        return checks

    async def _sqlite_wal_status_summary(self) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        paths_getter = getattr(self.plugin, "_sqlite_wal_candidate_paths", None)
        paths = paths_getter() if callable(paths_getter) else []

        def inspect(path: Path) -> dict[str, Any]:
            try:
                conn = sqlite3.connect(str(path), timeout=2.0)
                try:
                    mode_row = conn.execute("PRAGMA journal_mode").fetchone()
                    timeout_row = conn.execute("PRAGMA busy_timeout").fetchone()
                    mode = str(mode_row[0] if mode_row else "").lower()
                    timeout_ms = self._int(timeout_row[0] if timeout_row else 0)
                finally:
                    conn.close()
                level = "ok" if mode == "wal" and timeout_ms >= 1000 else "warn"
                text = f"journal_mode={mode or '-'}，busy_timeout={timeout_ms}ms"
                return {"path": str(path), "name": path.name, "level": level, "text": text, "journal_mode": mode, "busy_timeout_ms": timeout_ms}
            except Exception as exc:
                return {"path": str(path), "name": path.name, "level": "error", "text": self._single_line(exc, 180)}

        for path in paths[:12]:
            items.append(await self._to_thread_sqlite_inspect(inspect, path))
        return {
            "items": items,
            "ok": sum(1 for item in items if item.get("level") == "ok"),
            "warn": sum(1 for item in items if item.get("level") == "warn"),
            "error": sum(1 for item in items if item.get("level") == "error"),
        }

    async def _to_thread_sqlite_inspect(self, func: Any, path: Path) -> dict[str, Any]:
        try:
            import asyncio

            return await asyncio.to_thread(func, path)
        except Exception:
            return func(path)

    async def get_token_stats(self) -> dict[str, Any]:
        try:
            async with self.plugin._data_lock:
                usage = deepcopy(self.plugin.data.get("token_usage", {}))
            return self._ok(self._token_stats_payload(usage))
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 获取 Token 统计失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def reset_token_stats(self) -> dict[str, Any]:
        try:
            async with self.plugin._data_lock:
                self.plugin.data["token_usage"] = {}
                self.plugin._save_data_sync()
            return self._ok(self._token_stats_payload({}))
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 重置 Token 统计失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def unlock_bookshelf(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        password = str(payload.get("password", "")).strip()
        try:
            expected = await self.plugin._ensure_bookshelf_password_async()
            async with self.plugin._data_lock:
                if not self._bookshelf_password_matches(password, expected):
                    return self._error("密码不对。需要在聊天里自然向 Bot 询问。")
                access_token = self._issue_bookshelf_access_token()
                data = deepcopy(self.plugin.data)
            return self._ok({"bookshelf": await self._bookshelf_summary(data, unlocked=True, access_token=access_token)})
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 解锁书柜夹层失败: {exc}", exc_info=True)
            return self._error(str(exc))

    @staticmethod
    def _normalize_bookshelf_password(value: Any) -> str:
        text = _strip_internal_message_blocks(value)
        text = re.sub(r"\s+", "", text)
        text = text.strip("「」『』“”\"'` 。，,.;；:：!！?？、~～（）()[]【】")
        return text.lower()

    def _bookshelf_password_matches(self, provided: Any, expected: Any) -> bool:
        normalized_expected = self._normalize_bookshelf_password(expected)
        normalized_provided = self._normalize_bookshelf_password(provided)
        if not normalized_expected or not normalized_provided:
            return False
        if normalized_provided == normalized_expected:
            return True
        return len(normalized_expected) >= 2 and normalized_expected in normalized_provided

    def _bookshelf_access_tokens(self) -> dict[str, float]:
        store = getattr(self.plugin, "_bookshelf_access_tokens", None)
        if not isinstance(store, dict):
            store = {}
            setattr(self.plugin, "_bookshelf_access_tokens", store)
        now = time.time()
        for token, expires_at in list(store.items()):
            if self._float(expires_at) <= now:
                store.pop(token, None)
        return store

    def _issue_bookshelf_access_token(self) -> str:
        token = secrets.token_urlsafe(24)
        self._bookshelf_access_tokens()[token] = time.time() + 2 * 3600
        return token

    def _bookshelf_access_token_valid(self, token: Any) -> bool:
        token_text = self._single_line(token, 120)
        if not token_text:
            return False
        expires_at = self._bookshelf_access_tokens().get(token_text)
        return bool(expires_at and self._float(expires_at) > time.time())

    def _bookshelf_request_token(self, payload: dict[str, Any] | None = None) -> str:
        if isinstance(payload, dict):
            token = self._single_line(payload.get("access_token") or payload.get("token"), 120)
            if token:
                return token
        return self._single_line(request.args.get("access_token") or request.args.get("token"), 120)

    def _bookshelf_access_error(self) -> dict[str, str]:
        return {"error": "夹层访问已过期，请重新输入密码打开抽屉"}

    async def get_bookshelf_image(self):
        if not self._bookshelf_access_token_valid(self._bookshelf_request_token()):
            return self._error(self._bookshelf_access_error()["error"])
        resolved = await self._resolve_bookshelf_image_path_from_request()
        if isinstance(resolved, dict):
            return self._error(str(resolved.get("error") or "图片不存在"))
        path = resolved
        try:
            response = await send_file(path)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 读取书柜图片失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def get_bookshelf_image_data(self) -> dict[str, Any]:
        if not self._bookshelf_access_token_valid(self._bookshelf_request_token()):
            return self._error(self._bookshelf_access_error()["error"])
        resolved = await self._resolve_bookshelf_image_path_from_request()
        if isinstance(resolved, dict):
            return self._error(str(resolved.get("error") or "图片不存在"))
        path = resolved
        try:
            mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
            data = await self._read_file_base64(path)
            return self._ok(
                {
                    "mime": mime,
                    "data_url": f"data:{mime};base64,{data}",
                    "size": path.stat().st_size,
                    "mtime": int(path.stat().st_mtime),
                }
            )
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 读取书柜图片数据失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def _read_file_base64(self, path: Path) -> str:
        import asyncio

        raw = await asyncio.to_thread(path.read_bytes)
        return base64.b64encode(raw).decode("ascii")

    async def _resolve_bookshelf_image_path_from_request(self) -> Path | dict[str, str]:
        album_id = self._single_line(request.args.get("album_id"), 40)
        page_index = self._int(request.args.get("page"))
        cover_requested = str(request.args.get("cover") or "").lower() in {"1", "true", "yes"}
        if not album_id or (page_index < 1 and not cover_requested):
            return {"error": "缺少图片参数"}
        try:
            async with self.plugin._data_lock:
                data = deepcopy(self.plugin.data)
            shelf_items = data.get("bookshelf_items") if isinstance(data.get("bookshelf_items"), list) else []
            target = None
            for item in shelf_items:
                if not isinstance(item, dict) or item.get("type") != "jm_album":
                    continue
                if self._single_line(item.get("album_id") or item.get("id"), 40) == album_id:
                    target = item
                    break
            if target is None:
                jm_state = data.get("jm_cosmos_integration") if isinstance(data.get("jm_cosmos_integration"), dict) else {}
                last_album = jm_state.get("last_album") if isinstance(jm_state.get("last_album"), dict) else {}
                if self._single_line(last_album.get("id") or last_album.get("album_id"), 40) == album_id:
                    target = last_album
            pages = target.get("pages") if isinstance(target, dict) and isinstance(target.get("pages"), list) else []
            data_root = Path(str(getattr(self.plugin, "data_dir", ""))).resolve()
            path: Path | None = None
            if cover_requested and isinstance(target, dict):
                cover_path = self._single_line(target.get("cover_path"), 300)
                if cover_path:
                    path = Path(cover_path).resolve()
            if path is None:
                page = next((item for item in pages if isinstance(item, dict) and self._int(item.get("index")) == page_index), None)
                if cover_requested and not isinstance(page, dict):
                    page = next((item for item in pages if isinstance(item, dict) and self._int(item.get("index")) > 0), None)
                if not isinstance(page, dict):
                    return {"error": "图片不存在"}
                path = Path(str(page.get("path") or "")).resolve()
            try:
                path.relative_to(data_root)
            except ValueError:
                return {"error": "图片路径不在书柜目录内"}
            if not path.exists() or not path.is_file():
                return {"error": "图片文件不存在"}
            return path
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 读取书柜图片失败: {exc}", exc_info=True)
            return {"error": str(exc)}

    async def delete_bookshelf_item(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        access_token = self._bookshelf_request_token(payload)
        if not self._bookshelf_access_token_valid(access_token):
            return self._error(self._bookshelf_access_error()["error"])
        kind = self._single_line(payload.get("kind"), 32)
        item_id = self._single_line(payload.get("id"), 80)
        album_payload_id = self._single_line(payload.get("album_id"), 80)
        title_payload = self._single_line(payload.get("title"), 120)
        date_key = self._single_line(payload.get("date"), 32)
        try:
            async with self.plugin._data_lock:
                changed = False
                if kind == "creative":
                    projects = self.plugin.data.setdefault("creative_projects", [])
                    if not isinstance(projects, list):
                        projects = []
                    before = len(projects)
                    self.plugin.data["creative_projects"] = [
                        item
                        for item in projects
                        if not (isinstance(item, dict) and self._single_line(item.get("id"), 80) == item_id)
                    ]
                    changed = len(self.plugin.data["creative_projects"]) != before
                elif kind == "diary":
                    diaries = self.plugin.data.setdefault("bot_diaries", [])
                    if not isinstance(diaries, list):
                        diaries = []
                    before = len(diaries)
                    self.plugin.data["bot_diaries"] = [
                        item
                        for item in diaries
                        if not (isinstance(item, dict) and self._single_line(item.get("date"), 32) == date_key)
                    ]
                    changed = len(self.plugin.data["bot_diaries"]) != before
                elif kind == "jm_album":
                    album_id = album_payload_id or item_id.removeprefix("jm-")
                    album_id = album_id.removeprefix("jm-").removeprefix("jm_album:")
                    match_keys = {
                        value
                        for value in {
                            album_id,
                            item_id,
                            item_id.removeprefix("jm-"),
                            f"jm-{album_id}" if album_id else "",
                            f"jm_album:{album_id}" if album_id else "",
                        }
                        if value
                    }
                    items = self.plugin.data.setdefault("bookshelf_items", [])
                    if not isinstance(items, list):
                        items = []
                    removed_pages: list[dict[str, Any]] = []
                    removed_album_ids: set[str] = set()
                    kept = []
                    for item in items:
                        if not isinstance(item, dict) or item.get("type") != "jm_album":
                            kept.append(item)
                            continue
                        item_values = {
                            self._single_line(item.get("album_id"), 80),
                            self._single_line(item.get("id"), 80),
                            self._single_line(item.get("key"), 100),
                        }
                        title_matched = bool(title_payload and self._single_line(item.get("title"), 120) == title_payload)
                        if match_keys.intersection(value for value in item_values if value) or title_matched:
                            removed_album_id = self._single_line(item.get("album_id") or item.get("id"), 80)
                            if removed_album_id:
                                removed_album_ids.add(removed_album_id)
                            if isinstance(item.get("pages"), list):
                                removed_pages.extend(page for page in item.get("pages", []) if isinstance(page, dict))
                            changed = True
                            continue
                        kept.append(item)
                    self.plugin.data["bookshelf_items"] = kept
                    state = self.plugin.data.get("jm_cosmos_integration")
                    if isinstance(state, dict):
                        last_album = state.get("last_album")
                        last_values = {
                            self._single_line(last_album.get("id"), 80),
                            self._single_line(last_album.get("album_id"), 80),
                            self._single_line(last_album.get("key"), 100),
                        } if isinstance(last_album, dict) else set()
                        last_title_matched = bool(
                            isinstance(last_album, dict)
                            and title_payload
                            and self._single_line(last_album.get("title"), 120) == title_payload
                        )
                        if isinstance(last_album, dict) and (match_keys.intersection(value for value in last_values if value) or last_title_matched):
                            removed_album_id = self._single_line(last_album.get("id") or last_album.get("album_id"), 80)
                            if removed_album_id:
                                removed_album_ids.add(removed_album_id)
                            state["last_album"] = {}
                            changed = True
                    if not changed and album_id:
                        data_root = Path(str(getattr(self.plugin, "data_dir", ""))).resolve()
                        if (data_root / "bookshelf_pages" / album_id).exists():
                            removed_album_ids.add(album_id)
                            changed = True
                    if removed_album_ids or title_payload:
                        state = self.plugin.data.setdefault("jm_cosmos_integration", {})
                        if not isinstance(state, dict):
                            state = {}
                            self.plugin.data["jm_cosmos_integration"] = state
                        deleted_ids = state.setdefault("deleted_album_ids", [])
                        if not isinstance(deleted_ids, list):
                            deleted_ids = []
                            state["deleted_album_ids"] = deleted_ids
                        for removed_id in sorted(removed_album_ids):
                            if removed_id and removed_id not in deleted_ids:
                                deleted_ids.append(removed_id)
                        del deleted_ids[:-300]
                        deleted_titles = state.setdefault("deleted_titles", [])
                        if not isinstance(deleted_titles, list):
                            deleted_titles = []
                            state["deleted_titles"] = deleted_titles
                        removed_titles = [
                            self._single_line(item.get("title"), 120)
                            for item in items
                            if isinstance(item, dict)
                            and item.get("type") == "jm_album"
                            and self._single_line(item.get("album_id") or item.get("id"), 80) in removed_album_ids
                        ]
                        if title_payload:
                            removed_titles.append(title_payload)
                        for removed_title in removed_titles:
                            if removed_title and removed_title not in deleted_titles:
                                deleted_titles.append(removed_title)
                        del deleted_titles[:-300]
                    self._cleanup_bookshelf_page_files(removed_pages)
                    self._cleanup_bookshelf_album_dirs(removed_album_ids)
                    logger.info(
                        "[PrivateCompanionPage] 书柜夹层移除: changed=%s id=%s album_id=%s title=%s removed=%s",
                        changed,
                        item_id,
                        album_id,
                        title_payload,
                        sorted(removed_album_ids),
                    )
                else:
                    return self._error("不支持的书柜项目类型")
                if changed:
                    self.plugin._save_data_sync()
                data = deepcopy(self.plugin.data)
            return self._ok({"changed": changed, "bookshelf": await self._bookshelf_summary(data, unlocked=True, access_token=access_token)})
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 删除书柜项目失败: {exc}", exc_info=True)
            return self._error(str(exc))

    def _cleanup_bookshelf_page_files(self, pages: list[dict[str, Any]]) -> None:
        data_root = Path(str(getattr(self.plugin, "data_dir", ""))).resolve()
        touched_dirs: set[Path] = set()
        for page in pages:
            path = Path(str(page.get("path") or "")).resolve()
            try:
                path.relative_to(data_root)
            except ValueError:
                continue
            if not path.exists() or not path.is_file():
                continue
            touched_dirs.add(path.parent)
            try:
                path.unlink()
            except Exception:
                pass
        for folder in touched_dirs:
            try:
                folder.relative_to(data_root / "bookshelf_pages")
            except ValueError:
                continue
            try:
                if folder.exists() and not any(folder.iterdir()):
                    shutil.rmtree(folder, ignore_errors=True)
            except Exception:
                pass

    def _cleanup_bookshelf_album_dirs(self, album_ids: set[str]) -> None:
        if not album_ids:
            return
        data_root = Path(str(getattr(self.plugin, "data_dir", ""))).resolve()
        page_root = data_root / "bookshelf_pages"
        for album_id in album_ids:
            safe_id = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(album_id or ""))
            if not safe_id:
                continue
            folder = (page_root / safe_id).resolve()
            try:
                folder.relative_to(page_root.resolve())
            except ValueError:
                continue
            try:
                shutil.rmtree(folder, ignore_errors=True)
            except Exception:
                pass

    async def rate_bookshelf_item(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        access_token = self._bookshelf_request_token(payload)
        if not self._bookshelf_access_token_valid(access_token):
            return self._error(self._bookshelf_access_error()["error"])
        album_id = self._single_line(payload.get("album_id") or payload.get("id"), 32)
        rating = self._int(payload.get("rating"))
        reason = self._single_line(payload.get("reason"), 160)
        if not album_id:
            return self._error("缺少 album_id")
        if rating < 1 or rating > 10:
            return self._error("评分必须是 1 到 10")
        try:
            async with self.plugin._data_lock:
                items = self.plugin.data.setdefault("bookshelf_items", [])
                if not isinstance(items, list):
                    items = []
                    self.plugin.data["bookshelf_items"] = items
                target: dict[str, Any] | None = None
                for item in items:
                    if not isinstance(item, dict) or item.get("type") != "jm_album":
                        continue
                    if str(item.get("album_id") or item.get("id") or "") == album_id:
                        item["user_rating"] = rating
                        item["user_rating_reason"] = reason
                        item["user_rated_ts"] = time.time()
                        target = item
                        break
                state = self.plugin.data.setdefault("jm_cosmos_integration", {})
                if isinstance(state, dict):
                    last_album = state.get("last_album")
                    if isinstance(last_album, dict) and str(last_album.get("id") or last_album.get("album_id") or "") == album_id:
                        last_album["user_rating"] = rating
                        last_album["user_rating_reason"] = reason
                        last_album["user_rated_ts"] = time.time()
                        if target is None:
                            target = last_album
                if target is None:
                    return self._error("没有找到这条私密阅读记录")
                updater = getattr(self.plugin, "_update_private_reading_preference_profile", None)
                if callable(updater):
                    updater(target)
                self.plugin._save_data_sync()
                data = deepcopy(self.plugin.data)
            return self._ok({"bookshelf": await self._bookshelf_summary(data, unlocked=True, access_token=access_token)})
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 保存私密阅读评分失败: {exc}", exc_info=True)
            return self._error(str(exc))

    def _normalize_bookshelf_tag_list(self, value: Any, *, limit: int = 8) -> list[str]:
        raw_items: list[Any]
        if isinstance(value, str):
            raw_items = re.split(r"[,，、\s\n\r]+", value)
        elif isinstance(value, list):
            raw_items = value
        else:
            raw_items = []
        tags: list[str] = []
        seen: set[str] = set()
        for raw in raw_items:
            tag = self._single_line(raw, 24)
            if not tag:
                continue
            normalized = tag.casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            tags.append(tag)
            if len(tags) >= limit:
                break
        return tags

    def _normalize_bookshelf_page_comment(self, value: Any, *, limit: int = 100) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        page_no = self._int(value.get("page"))
        comment_text = self._single_line(value.get("comment"), limit)
        if page_no <= 0 or not comment_text:
            return None
        return {
            "page": page_no,
            "comment": comment_text,
            "raw_page": self._int(value.get("raw_page")),
            "sample_order": self._int(
                value.get("sample_order")
                or value.get("sample_index")
                or value.get("reference_index")
                or value.get("image_index")
            ),
        }

    def _merge_bookshelf_page_comments(self, *sources: Any, limit: int = 24) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[tuple[int, str]] = set()
        for source in sources:
            if not isinstance(source, list):
                continue
            for item in source:
                normalized = self._normalize_bookshelf_page_comment(item)
                if not normalized:
                    continue
                key = (normalized["page"], normalized["comment"])
                if key in seen:
                    continue
                seen.add(key)
                merged.append(normalized)
                if len(merged) >= limit:
                    return merged
        return merged

    async def update_bookshelf_item_tags(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        access_token = self._bookshelf_request_token(payload)
        if not self._bookshelf_access_token_valid(access_token):
            return self._error(self._bookshelf_access_error()["error"])
        album_id = self._single_line(payload.get("album_id") or payload.get("id"), 32)
        liked_tags = self._normalize_bookshelf_tag_list(payload.get("liked_tags"))
        disliked_tags_raw = self._normalize_bookshelf_tag_list(payload.get("disliked_tags"))
        liked_seen = {tag.casefold() for tag in liked_tags}
        disliked_tags = [tag for tag in disliked_tags_raw if tag.casefold() not in liked_seen]
        if not album_id:
            return self._error("缺少 album_id")
        try:
            async with self.plugin._data_lock:
                items = self.plugin.data.setdefault("bookshelf_items", [])
                if not isinstance(items, list):
                    items = []
                    self.plugin.data["bookshelf_items"] = items
                target: dict[str, Any] | None = None
                for item in items:
                    if not isinstance(item, dict) or item.get("type") != "jm_album":
                        continue
                    if str(item.get("album_id") or item.get("id") or "") == album_id:
                        item["user_liked_tags"] = liked_tags
                        item["user_disliked_tags"] = disliked_tags
                        item["user_tags_updated_ts"] = time.time()
                        target = item
                        break
                state = self.plugin.data.setdefault("jm_cosmos_integration", {})
                if isinstance(state, dict):
                    last_album = state.get("last_album")
                    if isinstance(last_album, dict) and str(last_album.get("id") or last_album.get("album_id") or "") == album_id:
                        last_album["user_liked_tags"] = liked_tags
                        last_album["user_disliked_tags"] = disliked_tags
                        last_album["user_tags_updated_ts"] = time.time()
                        if target is None:
                            target = last_album
                if target is None:
                    return self._error("没有找到这条私密阅读记录")
                updater = getattr(self.plugin, "_update_private_reading_preference_profile", None)
                if callable(updater):
                    updater(target)
                self.plugin._save_data_sync()
                data = deepcopy(self.plugin.data)
            return self._ok({"bookshelf": await self._bookshelf_summary(data, unlocked=True, access_token=access_token)})
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 保存私密阅读标签失败: {exc}", exc_info=True)
            return self._error(str(exc))

    def _resolve_bookshelf_data_file(self, value: Any) -> Path | None:
        path_text = self._single_line(value, 500)
        if not path_text:
            return None
        data_root = Path(str(getattr(self.plugin, "data_dir", ""))).resolve()
        try:
            raw_path = Path(path_text)
            path = raw_path.resolve() if raw_path.is_absolute() else (data_root / raw_path).resolve()
            path.relative_to(data_root)
            if path.exists() and path.is_file():
                return path
        except Exception:
            return None
        return None

    def _jm_album_comment_sample(self, item: dict[str, Any]) -> tuple[Path | None, list[Path], list[int]]:
        cover_path = self._resolve_bookshelf_data_file(item.get("cover_path"))
        pages = item.get("pages") if isinstance(item.get("pages"), list) else []
        page_by_index: dict[int, Path] = {}
        for page in pages:
            if not isinstance(page, dict):
                continue
            page_index = self._int(page.get("index"))
            page_path = self._resolve_bookshelf_data_file(page.get("path"))
            if page_index > 0 and page_path:
                page_by_index[page_index] = page_path
        sampled_pages = [
            self._int(page)
            for page in (item.get("sampled_pages") if isinstance(item.get("sampled_pages"), list) else [])
            if self._int(page) > 0 and self._int(page) in page_by_index
        ][:5]
        if not sampled_pages:
            sampled_pages = sorted(page_by_index)[:5]
        return cover_path, [page_by_index[page] for page in sampled_pages if page in page_by_index], sampled_pages

    async def update_bookshelf_item_comments(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        access_token = self._bookshelf_request_token(payload)
        if not self._bookshelf_access_token_valid(access_token):
            return self._error(self._bookshelf_access_error()["error"])
        album_id = self._single_line(payload.get("album_id") or payload.get("id"), 32)
        if not album_id:
            return self._error("缺少 album_id")
        try:
            async with self.plugin._data_lock:
                items = self.plugin.data.get("bookshelf_items") if isinstance(self.plugin.data.get("bookshelf_items"), list) else []
                target = next(
                    (
                        item
                        for item in items
                        if isinstance(item, dict)
                        and item.get("type") == "jm_album"
                        and str(item.get("album_id") or item.get("id") or "") == album_id
                    ),
                    None,
                )
                if target is None:
                    state = self.plugin.data.get("jm_cosmos_integration") if isinstance(self.plugin.data.get("jm_cosmos_integration"), dict) else {}
                    last_album = state.get("last_album") if isinstance(state.get("last_album"), dict) else None
                    if last_album and str(last_album.get("id") or last_album.get("album_id") or "") == album_id:
                        target = last_album
                if target is None:
                    return self._error("没有找到这条私密阅读记录")
                target_snapshot = deepcopy(target)
            cover_path, page_paths, sampled_pages = self._jm_album_comment_sample(target_snapshot)
            if not page_paths:
                return self._error("没有找到可用于重读的本地图片")
            vision = getattr(self.plugin, "_call_jm_cosmos_vision", None)
            if not callable(vision):
                return self._error("当前插件版本不支持让 Bot 重读")
            vision_result = await vision(cover_path, target_snapshot, page_paths=page_paths, sampled_pages=sampled_pages)
            if not isinstance(vision_result, dict) or not (vision_result.get("impression") or vision_result.get("page_comments")):
                return self._error("这次没有生成新的读后感或批注")
            updates: dict[str, Any] = {
                "comments_updated_ts": time.time(),
                "sampled_pages": sampled_pages,
            }
            impression = self._single_line(vision_result.get("impression"), 600)
            if impression:
                updates["impression"] = impression
                updates["reading_impression"] = impression
            rating = self._int(vision_result.get("rating"))
            if 1 <= rating <= 10:
                updates["rating"] = rating
            rating_reason = self._single_line(vision_result.get("rating_reason"), 160)
            if rating_reason:
                updates["rating_reason"] = rating_reason
            preference_tags = self._normalize_bookshelf_tag_list(vision_result.get("preference_tags"))
            if preference_tags:
                updates["preference_tags"] = preference_tags
            page_comments = vision_result.get("page_comments") if isinstance(vision_result.get("page_comments"), list) else []
            normalized_comments: list[dict[str, Any]] = []
            for comment in page_comments[:8]:
                normalized = self._normalize_bookshelf_page_comment(comment, limit=80)
                if normalized:
                    normalized_comments.append(normalized)
            if normalized_comments:
                existing_comments = target_snapshot.get("page_comments") if isinstance(target_snapshot.get("page_comments"), list) else []
                previous_comments = (
                    target_snapshot.get("page_comments_previous")
                    if isinstance(target_snapshot.get("page_comments_previous"), list)
                    else []
                )
                updates["page_comments"] = self._merge_bookshelf_page_comments(
                    existing_comments,
                    normalized_comments,
                    previous_comments,
                    limit=24,
                )
                updates["page_comments_previous"] = existing_comments[:12]
            async with self.plugin._data_lock:
                items = self.plugin.data.get("bookshelf_items") if isinstance(self.plugin.data.get("bookshelf_items"), list) else []
                written = False
                for item in items:
                    if not isinstance(item, dict) or item.get("type") != "jm_album":
                        continue
                    if str(item.get("album_id") or item.get("id") or "") == album_id:
                        item.update(updates)
                        target = item
                        written = True
                        break
                state = self.plugin.data.setdefault("jm_cosmos_integration", {})
                if isinstance(state, dict):
                    last_album = state.get("last_album")
                    if isinstance(last_album, dict) and str(last_album.get("id") or last_album.get("album_id") or "") == album_id:
                        last_album.update(updates)
                        if not written:
                            target = last_album
                            written = True
                if not written:
                    return self._error("没有找到可写回的私密阅读记录")
                updater = getattr(self.plugin, "_update_private_reading_preference_profile", None)
                if callable(updater):
                    updater(target)
                self.plugin._save_data_sync()
                data = deepcopy(self.plugin.data)
            return self._ok({"message": "Bot 已重新读过并更新读后感", "bookshelf": await self._bookshelf_summary(data, unlocked=True, access_token=access_token)})
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 更新私密阅读批注失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def import_worldbook(self) -> dict[str, Any]:
        try:
            async with self.plugin._data_lock:
                changed = bool(self.plugin._import_worldbook_entries_from_sources())
                self.plugin._save_data_sync()
                data = deepcopy(self.plugin.data)
            return self._ok({"changed": changed, "worldbook": self._worldbook_summary(data)})
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 导入世界书失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def update_worldbook_member(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        raw_user_id = self._single_line(payload.get("user_id"), 80)
        user_id = self._normalize_worldbook_member_id(raw_user_id)
        if not user_id:
            return self._error("缺少 user_id")
        try:
            async with self.plugin._data_lock:
                profiles = self.plugin.data.setdefault("worldbook_member_profiles", {})
                if not isinstance(profiles, dict):
                    profiles = {}
                    self.plugin.data["worldbook_member_profiles"] = profiles
                if payload.get("delete"):
                    deleted = self.plugin.data.setdefault("worldbook_deleted_member_ids", [])
                    if not isinstance(deleted, list):
                        deleted = []
                        self.plugin.data["worldbook_deleted_member_ids"] = deleted
                    if user_id not in deleted:
                        deleted.append(user_id)
                    changed = profiles.pop(user_id, None) is not None
                    self.plugin._save_data_sync()
                    data = deepcopy(self.plugin.data)
                    return self._ok({"changed": changed, "message": "已删除关系节点", "worldbook": self._worldbook_summary(data)})
                if not self._worldbook_member_id_valid(user_id):
                    return self._error("关系节点必须使用有效 QQ 号或 B 站外部身份键")
                linked_qq_user_id = self._single_line(
                    payload.get("linked_qq_user_id") or payload.get("bound_qq_user_id") or payload.get("qq_user_id"),
                    40,
                )
                if linked_qq_user_id and (not linked_qq_user_id.isdigit() or len(linked_qq_user_id) < 5):
                    return self._error("绑定目标必须是有效 QQ 号")
                deleted = self.plugin.data.setdefault("worldbook_deleted_member_ids", [])
                if isinstance(deleted, list) and user_id in deleted:
                    self.plugin.data["worldbook_deleted_member_ids"] = [item for item in deleted if str(item) != user_id]
                profile = profiles.get(user_id)
                if not isinstance(profile, dict):
                    profile = {
                        "user_id": user_id,
                        "name": user_id,
                        "gender": "",
                        "aliases": [],
                        "content": "",
                        "identity_note": "",
                        "boundary_note": "",
                        "important_memories": [],
                        "enabled": True,
                        "priority": 120,
                        "source_entries": ["手动维护"],
                        "observed_names": [],
                    }
                    profiles[user_id] = profile
                profile["user_id"] = user_id
                profile["identity_type"] = "qq" if user_id.isdigit() else "external"
                if "enabled" in payload:
                    profile["enabled"] = bool(payload.get("enabled"))
                if "name" in payload:
                    profile["name"] = self._single_line(payload.get("name"), 80) or user_id
                if "gender" in payload:
                    profile["gender"] = self._single_line(payload.get("gender"), 40)
                if "aliases" in payload and isinstance(payload.get("aliases"), list):
                    profile["aliases"] = [self._single_line(item, 40) for item in payload.get("aliases", []) if self._single_line(item, 40)]
                if "content" in payload:
                    profile["content"] = str(payload.get("content") or "").strip()[:2000]
                if "note" in payload:
                    profile["note"] = str(payload.get("note") or "").strip()[:2000]
                if "identity_note" in payload:
                    profile["identity_note"] = str(payload.get("identity_note") or "").strip()[:2000]
                if "boundary_note" in payload:
                    profile["boundary_note"] = str(payload.get("boundary_note") or "").strip()[:1200]
                if "important_memories" in payload:
                    profile["important_memories"] = self._normalize_important_memories(payload.get("important_memories"))
                if "accept_pending_observation_id" in payload or "reject_pending_observation_id" in payload:
                    pending = profile.get("pending_observations") if isinstance(profile.get("pending_observations"), list) else []
                    target_id = self._single_line(
                        payload.get("accept_pending_observation_id") or payload.get("reject_pending_observation_id"),
                        40,
                    )
                    kept = []
                    accepted = None
                    for item in pending:
                        if not isinstance(item, dict):
                            continue
                        if self._single_line(item.get("id"), 40) == target_id:
                            accepted = item
                            continue
                        kept.append(item)
                    profile["pending_observations"] = kept[:8]
                    if accepted and payload.get("accept_pending_observation_id"):
                        memories = self._normalize_important_memories(profile.get("important_memories"))
                        memories.insert(
                            0,
                            {
                                "title": self._single_line(accepted.get("title"), 60) or "群聊观察",
                                "content": str(accepted.get("content") or accepted.get("evidence") or "").strip()[:500],
                                "weight": self._clamp_int(accepted.get("weight"), 35, 0, 100),
                                "privacy": "internal",
                                "source": "group_observation",
                                "enabled": True,
                                "updated_at": time.time(),
                            },
                        )
                        profile["important_memories"] = self._normalize_important_memories(memories)
                if "priority" in payload:
                    profile["priority"] = self._clamp_int(payload.get("priority"), 120, -1000, 10000)
                profile["manual_edit_ts"] = time.time()
                bind_result: dict[str, Any] | None = None
                if linked_qq_user_id and linked_qq_user_id != user_id:
                    bind_result = self._bind_worldbook_external_member_locked(profiles, user_id, linked_qq_user_id, profile)
                self.plugin._save_data_sync()
                data = deepcopy(self.plugin.data)
            if payload.keys() <= {"user_id", "enabled"}:
                message = "已更新关系节点状态"
            elif "important_memories" in payload and len(payload) <= 2:
                message = "已更新重要记忆"
            elif bind_result:
                message = "已绑定到 QQ 关系节点"
            else:
                message = "已保存关系节点"
            response = {"message": message, "worldbook": self._worldbook_summary(data)}
            if bind_result:
                response["bind"] = bind_result
            return self._ok(response)
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 更新关系节点失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def get_worldbook_member_livingmemory(self) -> dict[str, Any]:
        user_id = self._normalize_worldbook_member_id(self._single_line(request.args.get("user_id"), 80))
        if not user_id:
            return self._error("缺少 user_id")
        limit = self._query_int("limit", 20, 1, 60)
        try:
            async with self.plugin._data_lock:
                profiles = self.plugin.data.get("worldbook_member_profiles") if isinstance(self.plugin.data.get("worldbook_member_profiles"), dict) else {}
                profile = profiles.get(user_id)
                if not isinstance(profile, dict):
                    return self._error("没有找到对应关系节点")
                profile_copy = deepcopy(profile)
            token_bundle = self._worldbook_member_livingmemory_tokens(user_id, profile_copy)
            tokens = token_bundle.get("tokens", [])
            db_path = self._livingmemory_db_path()
            if not db_path:
                return self._ok(
                    {
                        "available": False,
                        "user_id": user_id,
                        "tokens": tokens,
                        "items": [],
                        "total": 0,
                        "message": "未找到 LivingMemory 数据库",
                    }
                )
            items = await asyncio.to_thread(self._query_livingmemory_for_tokens, db_path, token_bundle, limit)
            return self._ok(
                {
                    "available": True,
                    "db_path": str(db_path),
                    "user_id": user_id,
                    "tokens": tokens,
                    "primary_tokens": token_bundle.get("primary_tokens", []),
                    "support_tokens": token_bundle.get("support_tokens", []),
                    "items": items,
                    "total": len(items),
                    "filter_note": "默认仅召回命中 QQ/绑定身份/关系节点名称的记忆；别名和群名片只用于加分。",
                    "message": f"已找到 {len(items)} 条 LivingMemory 相关记忆",
                }
            )
        except sqlite3.OperationalError as exc:
            logger.warning(f"[PrivateCompanionPage] 查询 LivingMemory 失败: {exc}")
            return self._error(f"LivingMemory 数据库暂时不可读：{exc}")
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 查询关系节点 LivingMemory 失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def clear_worldbook_pending_observations(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        user_id = self._single_line(payload.get("user_id"), 40)
        try:
            async with self.plugin._data_lock:
                profiles = self.plugin.data.setdefault("worldbook_member_profiles", {})
                if not isinstance(profiles, dict):
                    profiles = {}
                    self.plugin.data["worldbook_member_profiles"] = profiles
                cleared = 0
                touched = 0
                for profile_id, profile in profiles.items():
                    if user_id and str(profile_id) != user_id:
                        continue
                    if not isinstance(profile, dict):
                        continue
                    pending = profile.get("pending_observations")
                    if not isinstance(pending, list) or not pending:
                        continue
                    cleared += len([item for item in pending if isinstance(item, dict)])
                    profile["pending_observations"] = []
                    profile["pending_observations_cleared_at"] = time.time()
                    touched += 1
                if user_id and not touched:
                    return self._error("没有找到可清理的待确认观察")
                if touched:
                    self.plugin._save_data_sync()
                data = deepcopy(self.plugin.data)
            message = f"已清理 {cleared} 条待确认观察" if cleared else "没有待确认观察需要清理"
            return self._ok({"message": message, "cleared": cleared, "worldbook": self._worldbook_summary(data)})
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 清理待确认观察失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def update_worldbook_group(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        group_id = self._single_line(payload.get("group_id"), 40)
        if not group_id:
            return self._error("缺少 group_id")
        try:
            async with self.plugin._data_lock:
                groups = self.plugin.data.setdefault("worldbook_group_profiles", {})
                if not isinstance(groups, dict):
                    groups = {}
                    self.plugin.data["worldbook_group_profiles"] = groups
                if payload.get("delete"):
                    deleted = self.plugin.data.setdefault("worldbook_deleted_group_ids", [])
                    if not isinstance(deleted, list):
                        deleted = []
                        self.plugin.data["worldbook_deleted_group_ids"] = deleted
                    if group_id not in deleted:
                        deleted.append(group_id)
                    changed = groups.pop(group_id, None) is not None
                    self.plugin._save_data_sync()
                    data = deepcopy(self.plugin.data)
                    return self._ok({"changed": changed, "message": "已删除群资料", "worldbook": self._worldbook_summary(data)})
                deleted = self.plugin.data.setdefault("worldbook_deleted_group_ids", [])
                if isinstance(deleted, list) and group_id in deleted:
                    self.plugin.data["worldbook_deleted_group_ids"] = [item for item in deleted if str(item) != group_id]
                group = groups.get(group_id)
                if not isinstance(group, dict):
                    group = {
                        "group_id": group_id,
                        "name": group_id,
                        "content": "",
                        "enabled": True,
                        "priority": 110,
                        "aliases": [],
                        "source_entries": ["手动维护"],
                    }
                    groups[group_id] = group
                group["group_id"] = group_id
                if "enabled" in payload:
                    group["enabled"] = bool(payload.get("enabled"))
                if "name" in payload:
                    group["name"] = self._single_line(payload.get("name"), 80) or group_id
                if "content" in payload:
                    group["content"] = str(payload.get("content") or "").strip()[:2000]
                if "priority" in payload:
                    group["priority"] = self._clamp_int(payload.get("priority"), 110, -1000, 10000)
                group["manual_edit_ts"] = time.time()
                self.plugin._save_data_sync()
                data = deepcopy(self.plugin.data)
            return self._ok({"message": "已保存群资料", "worldbook": self._worldbook_summary(data)})
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 更新群资料失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def apply_preset(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        name = str(payload.get("name", "")).strip()
        presets = self._presets()
        if name not in presets:
            return self._error("未知预设")
        preset = presets[name]
        try:
            for key, value in preset.get("settings", {}).items():
                self._apply_config_value(key, self._normalize_setting_value(key, value))
            for key, value in preset.get("features", {}).items():
                if key in self._allowed_feature_keys():
                    self._apply_config_value(key, self._normalize_bool_value(value))
            if any(key in self._allowed_provider_keys() for key in preset.get("settings", {})) or "provider_config_mode" in preset.get("settings", {}):
                apply_quick = getattr(self.plugin, "_apply_quick_provider_defaults", None)
                if callable(apply_quick):
                    apply_quick()
            config_saved = await self._save_config_if_possible()
            overview = await self.get_overview()
            if overview.get("success"):
                overview["data"]["preset"] = name
                overview["data"]["preset_label"] = preset.get("label", name)
                overview["data"]["config_saved"] = config_saved
            return overview
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 应用预设失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def update_skill_growth(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        skill_id = self._single_line(payload.get("id"), 40)
        name = self._single_line(payload.get("name"), 32)
        if not skill_id and not name:
            return self._error("缺少技能名称")
        def _parse_bool(value: Any, default: bool = False) -> bool:
            if value is None:
                return default
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            return str(value).strip().lower() in {"1", "true", "yes", "on", "启用", "开启"}

        def _parse_terms(value: Any, *, limit: int = 16) -> list[str]:
            if isinstance(value, list):
                raw_items = value
            else:
                raw_items = re.split(r"[,，、\n]+", str(value or ""))
            items: list[str] = []
            for raw in raw_items:
                item = self._single_line(raw, 24)
                if item and item not in items:
                    items.append(item)
            return items[:limit]

        try:
            async with self.plugin._data_lock:
                state = self.plugin.data.setdefault("skill_growth", {})
                if not isinstance(state, dict):
                    state = {}
                    self.plugin.data["skill_growth"] = state
                skills = state.setdefault("skills", {})
                if not isinstance(skills, dict):
                    skills = {}
                    state["skills"] = skills

                if payload.get("delete"):
                    changed = bool(skill_id and skills.pop(skill_id, None) is not None)
                    state["updated_ts"] = time.time()
                    self.plugin._save_data_sync()
                    return self._ok({"changed": changed, "message": "已删除技能", "skill_growth": self._skill_growth_summary(self.plugin.data)})

                if not name:
                    return self._error("缺少技能名称")
                if not skill_id:
                    for existing_id, existing_skill in skills.items():
                        if not isinstance(existing_skill, dict):
                            continue
                        existing_name = self._single_line(existing_skill.get("name"), 32)
                        existing_aliases = existing_skill.get("aliases") if isinstance(existing_skill.get("aliases"), list) else []
                        alias_set = {self._single_line(item, 24) for item in existing_aliases}
                        if name == existing_name:
                            skill_id = self._single_line(existing_id, 40)
                            break
                        if name in alias_set:
                            return self._error(f"“{name}”已经是“{existing_name or '未命名技能'}”的合并别名，请直接编辑该技能")
                if not skill_id:
                    skill_id = hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]
                existing = skills.get(skill_id) if isinstance(skills.get(skill_id), dict) else {}
                level = max(1, min(6, self._int(payload.get("level")) or self._int(existing.get("level")) or 1))
                exp = self._float(payload.get("exp"))
                if exp <= 0 and isinstance(existing, dict):
                    exp = self._float(existing.get("exp"))
                if exp <= 0:
                    exp = {1: 0, 2: 100, 3: 260, 4: 520, 5: 900, 6: 1400}.get(level, 0)
                if hasattr(self.plugin, "_skill_level_from_exp"):
                    level = self.plugin._skill_level_from_exp(exp)
                keywords = _parse_terms(payload.get("keywords")) or [name]
                aliases = _parse_terms(payload.get("aliases"), limit=12)
                hidden = _parse_bool(payload.get("hidden"), bool(existing.get("hidden")) if isinstance(existing, dict) else False)
                frozen = _parse_bool(payload.get("frozen"), bool(existing.get("frozen")) if isinstance(existing, dict) else False)
                skill = dict(existing) if isinstance(existing, dict) else {}
                skill.update(
                    {
                        "id": skill_id,
                        "name": name,
                        "category": self._single_line(payload.get("category"), 20) or self._single_line(skill.get("category"), 20) or "能力",
                        "keywords": keywords,
                        "aliases": [item for item in aliases if item != name],
                        "hidden": hidden,
                        "frozen": frozen,
                        "exp": round(max(0.0, exp), 2),
                        "level": level,
                        "level_title": self.plugin._skill_level_title(level) if hasattr(self.plugin, "_skill_level_title") else self._single_line(skill.get("level_title"), 24),
                        "created_ts": self._float(skill.get("created_ts")) or time.time(),
                        "last_trained_ts": self._float(skill.get("last_trained_ts")),
                        "training_count": self._int(skill.get("training_count")),
                        "recent_logs": skill.get("recent_logs") if isinstance(skill.get("recent_logs"), list) else [],
                    }
                )
                skills[skill_id] = skill
                state["updated_ts"] = time.time()
                self.plugin._save_data_sync()
                return self._ok({"message": "已保存技能", "skill_growth": self._skill_growth_summary(self.plugin.data)})
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 更新技能失败: {exc}", exc_info=True)
            return self._error(str(exc))

    @staticmethod
    def _food_menu_parse_bool(value: Any, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value).strip().lower() in {"1", "true", "yes", "on", "启用", "开启", "是", "常吃"}

    def _food_menu_parse_terms(self, value: Any, *, limit: int = 12, item_limit: int = 24) -> list[str]:
        if isinstance(value, list):
            raw_items = value
        else:
            raw_items = re.split(r"[,，、\n/|#]+", str(value or ""))
        items: list[str] = []
        for raw in raw_items:
            item = self._single_line(raw, item_limit)
            if item and item not in items:
                items.append(item)
        return items[:limit]

    def _food_menu_normalize_times(self, value: Any) -> list[str]:
        mapping = {
            "早餐": "breakfast",
            "早饭": "breakfast",
            "早上": "breakfast",
            "breakfast": "breakfast",
            "午餐": "lunch",
            "午饭": "lunch",
            "中午": "lunch",
            "lunch": "lunch",
            "晚餐": "dinner",
            "晚饭": "dinner",
            "晚上": "dinner",
            "dinner": "dinner",
            "夜宵": "late_night",
            "宵夜": "late_night",
            "深夜": "late_night",
            "late_night": "late_night",
            "latenight": "late_night",
            "加餐": "snack",
            "零食": "snack",
            "下午茶": "snack",
            "snack": "snack",
        }
        normalized: list[str] = []
        for raw in self._food_menu_parse_terms(value, limit=5, item_limit=16):
            key = mapping.get(str(raw).strip().lower()) or mapping.get(str(raw).strip())
            if key and key not in normalized:
                normalized.append(key)
        return normalized

    def _food_menu_infer_fields(self, name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        text = " ".join(
            str(payload.get(key) or "")
            for key in ("type", "category", "tags", "times", "note")
        )
        text = f"{name} {text}"
        item_type = self._single_line(payload.get("type"), 20)
        if not item_type:
            if any(token in text for token in ("奶茶", "咖啡", "甜品", "蛋糕", "水果", "零食", "饮料", "酸奶")):
                item_type = "drink_snack"
            elif any(token in text for token in ("外卖", "美团", "饿了么", "配送", "到家")):
                item_type = "takeout"
            elif any(token in text for token in ("店", "馆", "楼下", "附近", "食堂", "餐厅", "小吃街")):
                item_type = "restaurant"
            elif any(token in text for token in ("泡面", "速食", "面包", "麦片", "饼干", "应急")):
                item_type = "emergency"
            else:
                item_type = "dish"
        allowed_types = {"dish", "restaurant", "takeout", "drink_snack", "emergency"}
        if item_type not in allowed_types:
            item_type = "dish"

        category = self._single_line(payload.get("category"), 24)
        if not category:
            category_rules = [
                ("面食", ("面", "粉", "馄饨", "饺子", "抄手", "米线", "米粉", "螺蛳粉")),
                ("米饭", ("饭", "盖浇", "便当", "煲仔", "黄焖鸡", "咖喱")),
                ("快餐", ("汉堡", "炸鸡", "披萨", "麦当劳", "肯德基", "华莱士", "塔斯汀")),
                ("甜口", ("奶茶", "甜品", "蛋糕", "水果", "酸奶")),
                ("热锅", ("火锅", "麻辣烫", "冒菜", "砂锅", "关东煮")),
                ("应急", ("泡面", "速食", "面包", "麦片", "饼干")),
            ]
            category = next((label for label, tokens in category_rules if any(token in text for token in tokens)), "")

        tags = self._food_menu_parse_terms(payload.get("tags"), limit=10, item_limit=16)
        inferred_tags: list[str] = []
        tag_rules = [
            ("热乎", ("面", "粉", "粥", "汤", "馄饨", "米线", "火锅", "麻辣烫", "砂锅", "关东煮")),
            ("快", ("泡面", "速食", "便当", "外卖", "汉堡", "炸鸡", "麦当劳", "肯德基", "华莱士", "塔斯汀")),
            ("清淡", ("粥", "汤", "沙拉", "蒸", "清淡")),
            ("辣", ("辣", "麻辣", "川", "火锅", "冒菜", "麻辣烫", "螺蛳粉")),
            ("甜", ("奶茶", "甜品", "蛋糕", "水果", "酸奶")),
            ("顶饱", ("饭", "面", "粉", "汉堡", "便当", "盖浇", "黄焖鸡")),
            ("便宜", ("食堂", "快餐", "兰州", "沙县", "华莱士")),
        ]
        for tag, tokens in tag_rules:
            if any(token in text for token in tokens) and tag not in tags and tag not in inferred_tags:
                inferred_tags.append(tag)
        tags = (tags + inferred_tags)[:10]

        times = self._food_menu_normalize_times(payload.get("times"))
        if not times:
            if any(token in text for token in ("早餐", "早饭", "早上", "包子", "豆浆", "油条", "麦片", "面包")):
                times.append("breakfast")
            if any(token in text for token in ("奶茶", "咖啡", "甜品", "水果", "零食", "酸奶")):
                times.append("snack")
            if any(token in text for token in ("夜宵", "宵夜", "烧烤", "炸鸡", "泡面", "螺蛳粉")):
                times.append("late_night")
        return {"type": item_type, "category": category, "tags": tags, "times": times}

    def _food_menu_payload_from_line(self, line: str) -> dict[str, Any]:
        text = self._single_line(line, 220)
        parts = [self._single_line(part, 80) for part in re.split(r"[|｜\t]+", text) if self._single_line(part, 80)]
        name = self._single_line(parts[0] if parts else text, 40)
        payload: dict[str, Any] = {"name": name}
        if len(parts) >= 2:
            payload["category"] = parts[1]
        if len(parts) >= 3:
            payload["tags"] = parts[2]
        if len(parts) >= 4:
            payload["times"] = parts[3]
        return payload

    async def update_food_menu(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        item_id = self._single_line(payload.get("id"), 48)
        name = self._single_line(payload.get("name"), 40)

        try:
            async with self.plugin._data_lock:
                state = self.plugin.data.setdefault("food_menu", {})
                if not isinstance(state, dict):
                    state = {}
                    self.plugin.data["food_menu"] = state
                items = state.setdefault("items", [])
                if not isinstance(items, list):
                    items = []
                    state["items"] = items

                if payload.get("delete"):
                    before = len(items)
                    state["items"] = [item for item in items if not (isinstance(item, dict) and self._single_line(item.get("id"), 48) == item_id)]
                    state["updated_ts"] = time.time()
                    self.plugin._save_data_sync()
                    return self._ok({
                        "changed": len(state["items"]) != before,
                        "message": "已移出候选",
                        "food_menu": self._food_menu_summary(self.plugin.data),
                    })

                if not name:
                    return self._error("缺少名称")
                if not item_id:
                    for existing in items:
                        if not isinstance(existing, dict):
                            continue
                        existing_name = self._single_line(existing.get("name"), 40)
                        aliases = self._food_menu_parse_terms(existing.get("aliases"), limit=12, item_limit=24)
                        if name == existing_name or name in aliases:
                            item_id = self._single_line(existing.get("id"), 48)
                            break
                if not item_id:
                    item_id = f"food_{hashlib.sha1((name + str(time.time())).encode('utf-8')).hexdigest()[:12]}"

                existing_index = -1
                existing_item: dict[str, Any] = {}
                for index, item in enumerate(items):
                    if isinstance(item, dict) and self._single_line(item.get("id"), 48) == item_id:
                        existing_index = index
                        existing_item = dict(item)
                        break

                inferred = self._food_menu_infer_fields(name, payload)
                if "type" in payload:
                    item_type = self._single_line(payload.get("type"), 20) or inferred.get("type") or "dish"
                else:
                    item_type = self._single_line(existing_item.get("type"), 20) or inferred.get("type") or "dish"
                allowed_types = {"dish", "restaurant", "takeout", "drink_snack", "emergency"}
                if item_type not in allowed_types:
                    item_type = "dish"
                if "category" in payload:
                    category = self._single_line(payload.get("category"), 24)
                    if not category and existing_index < 0:
                        category = self._single_line(inferred.get("category"), 24)
                else:
                    category = self._single_line(existing_item.get("category"), 24) or self._single_line(inferred.get("category"), 24)
                if "tags" in payload:
                    tags = self._food_menu_parse_terms(payload.get("tags"), limit=10, item_limit=16)
                else:
                    tags = self._food_menu_parse_terms(existing_item.get("tags"), limit=10, item_limit=16)
                if not tags and existing_index < 0:
                    tags = list(inferred.get("tags") or [])
                if "times" in payload:
                    times = self._food_menu_normalize_times(payload.get("times"))
                else:
                    times = self._food_menu_normalize_times(existing_item.get("times"))
                if not times and existing_index < 0:
                    times = list(inferred.get("times") or [])
                item = dict(existing_item)
                item.update(
                    {
                        "id": item_id,
                        "name": name,
                        "type": item_type,
                        "category": category,
                        "tags": tags[:10],
                        "times": times[:5],
                        "avoid": self._food_menu_parse_terms(payload.get("avoid"), limit=8, item_limit=24),
                        "aliases": [value for value in self._food_menu_parse_terms(payload.get("aliases"), limit=10, item_limit=24) if value != name],
                        "note": self._single_line(payload.get("note"), 100),
                        "favorite": self._food_menu_parse_bool(payload.get("favorite"), bool(existing_item.get("favorite"))),
                        "hidden": self._food_menu_parse_bool(payload.get("hidden"), bool(existing_item.get("hidden"))),
                        "use_count": self._int(payload.get("use_count")) if "use_count" in payload else self._int(existing_item.get("use_count")),
                        "created_ts": self._float(existing_item.get("created_ts")) or time.time(),
                        "updated_ts": time.time(),
                        "last_used_at": self._float(existing_item.get("last_used_at")),
                        "last_recommended_at": self._float(existing_item.get("last_recommended_at")),
                    }
                )
                if existing_index >= 0:
                    items[existing_index] = item
                else:
                    items.append(item)
                state["updated_ts"] = time.time()
                self.plugin._save_data_sync()
                return self._ok({"message": "已保存候选", "food_menu": self._food_menu_summary(self.plugin.data)})
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 更新吃什么候选失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def bulk_update_food_menu(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        raw_text = str(payload.get("text") or payload.get("items") or "").strip()
        favorite = self._food_menu_parse_bool(payload.get("favorite"), False)
        hidden = self._food_menu_parse_bool(payload.get("hidden"), False)
        if not raw_text:
            return self._error("先粘贴几样候选")
        lines = [
            self._single_line(part, 220)
            for part in re.split(r"[\r\n;；]+", raw_text)
            if self._single_line(part, 220)
        ]
        if not lines:
            return self._error("没有读到候选")
        added = 0
        updated = 0
        skipped = 0
        try:
            async with self.plugin._data_lock:
                state = self.plugin.data.setdefault("food_menu", {})
                if not isinstance(state, dict):
                    state = {}
                    self.plugin.data["food_menu"] = state
                items = state.setdefault("items", [])
                if not isinstance(items, list):
                    items = []
                    state["items"] = items
                for line in lines[:80]:
                    item_payload = self._food_menu_payload_from_line(line)
                    name = self._single_line(item_payload.get("name"), 40)
                    if not name:
                        skipped += 1
                        continue
                    existing_index = -1
                    existing_item: dict[str, Any] = {}
                    for index, item in enumerate(items):
                        if not isinstance(item, dict):
                            continue
                        existing_name = self._single_line(item.get("name"), 40)
                        aliases = self._food_menu_parse_terms(item.get("aliases"), limit=12, item_limit=24)
                        if name == existing_name or name in aliases:
                            existing_index = index
                            existing_item = dict(item)
                            break
                    inferred = self._food_menu_infer_fields(name, item_payload)
                    item_id = self._single_line(existing_item.get("id"), 48)
                    if not item_id:
                        item_id = f"food_{hashlib.sha1((name + str(time.time())).encode('utf-8')).hexdigest()[:12]}"
                    now = time.time()
                    existing_tags = self._food_menu_parse_terms(existing_item.get("tags"), limit=10, item_limit=16)
                    inferred_tags = [tag for tag in (inferred.get("tags") or []) if tag not in existing_tags]
                    existing_times = self._food_menu_normalize_times(existing_item.get("times"))
                    inferred_times = [time_key for time_key in (inferred.get("times") or []) if time_key not in existing_times]
                    item = dict(existing_item)
                    item.update(
                        {
                            "id": item_id,
                            "name": name,
                            "type": self._single_line(existing_item.get("type"), 20) or inferred.get("type") or "dish",
                            "category": self._single_line(item_payload.get("category"), 24)
                            or self._single_line(existing_item.get("category"), 24)
                            or self._single_line(inferred.get("category"), 24),
                            "tags": (existing_tags + inferred_tags)[:10],
                            "times": (existing_times + inferred_times)[:5],
                            "note": self._single_line(existing_item.get("note"), 100),
                            "favorite": bool(existing_item.get("favorite")) or favorite,
                            "hidden": bool(existing_item.get("hidden")) or hidden,
                            "use_count": self._int(existing_item.get("use_count")),
                            "created_ts": self._float(existing_item.get("created_ts")) or now,
                            "updated_ts": now,
                            "last_used_at": self._float(existing_item.get("last_used_at")),
                            "last_recommended_at": self._float(existing_item.get("last_recommended_at")),
                        }
                    )
                    if existing_index >= 0:
                        items[existing_index] = item
                        updated += 1
                    else:
                        items.append(item)
                        added += 1
                state["updated_ts"] = time.time()
                self.plugin._save_data_sync()
                return self._ok(
                    {
                        "message": f"已加入 {added} 个，更新 {updated} 个" + (f"，跳过 {skipped} 个" if skipped else ""),
                        "added": added,
                        "updated": updated,
                        "skipped": skipped,
                        "food_menu": self._food_menu_summary(self.plugin.data),
                    }
                )
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 批量更新吃什么候选失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def update_external_ability(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        normalizer = getattr(self.plugin, "_normalize_external_ability_name", None)
        name = self._single_line(payload.get("name"), 80)
        name = normalizer(name) if callable(normalizer) else name
        if not name:
            return self._error("缺少外部能力名称")
        try:
            async with self.plugin._data_lock:
                store_getter = getattr(self.plugin, "_external_ability_store", None)
                store = store_getter() if callable(store_getter) else self.plugin.data.setdefault("external_proactive_abilities", {})
                if not isinstance(store, dict):
                    store = {}
                    self.plugin.data["external_proactive_abilities"] = store
                item = store.get(name) if isinstance(store.get(name), dict) else {"name": name}
                if "enabled" in payload:
                    item["enabled"] = bool(payload.get("enabled"))
                if "share_probability" in payload:
                    item["share_probability"] = max(0.0, min(1.0, self._float(payload.get("share_probability"))))
                if "min_interval_hours" in payload:
                    item["min_interval_hours"] = max(0.0, self._float(payload.get("min_interval_hours")))
                if "config" in payload:
                    config = payload.get("config")
                    if isinstance(config, str):
                        import json
                        config = json.loads(config or "{}")
                    if not isinstance(config, dict):
                        return self._error("自定义配置必须是 JSON 对象")
                    item["config"] = config
                item["updated_ts"] = time.time()
                store[name] = item
                self.plugin._save_data_sync()
                data = deepcopy(self.plugin.data)
            return self._ok({"message": "已保存外部主动能力", "external_abilities": self._external_ability_summary(data)})
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 更新外部主动能力失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def generate_roleplay_draft_from_persona(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        umo = self._single_line(payload.get("umo"), 220)
        scopes = self._normalize_roleplay_draft_scopes(payload.get("scopes"))
        try:
            refresher = getattr(self.plugin, "_refresh_default_persona_prompt", None)
            if callable(refresher):
                persona_prompt = await refresher(umo)
            else:
                getter = getattr(self.plugin, "_get_default_persona_prompt", None)
                persona_prompt = getter() if callable(getter) else ""
            persona_prompt = str(persona_prompt or "").strip()
            if not persona_prompt or persona_prompt.startswith("未读取到 AstrBot 默认人格"):
                return self._error("还没有读取到可用的主回复人格文本，请先让 Bot 触发一次对话或检查人格配置")
            caller = getattr(self.plugin, "_llm_call", None)
            if not callable(caller):
                return self._error("当前插件运行态无法调用主模型")
            provider_id = str(getattr(self.plugin, "llm_provider_id", "") or "").strip()
            raw = await caller(
                self._roleplay_draft_from_persona_prompt(persona_prompt, scopes),
                max_tokens=1400,
                provider_id=provider_id,
                task="roleplay_draft_from_persona",
            )
            draft = self._normalize_roleplay_draft_result(self._loads_json_object(raw), scopes)
            return self._ok(
                {
                    "draft": draft,
                    "scopes": scopes,
                    "provider_id": provider_id,
                    "persona_id": self._single_line(getattr(self.plugin, "plugin_specific_persona_id", ""), 120),
                    "source_chars": len(persona_prompt),
                    "source_preview": self._single_line(persona_prompt, 220),
                    "raw_preview": self._single_line(raw, 220),
                }
            )
        except Exception as exc:
            logger.warning(f"[PrivateCompanionPage] 根据主回复人格生成设定草稿失败: {exc}", exc_info=True)
            return self._error(f"生成草稿失败: {self._single_line(exc, 160)}")

    def _normalize_roleplay_draft_scopes(self, raw: Any) -> list[str]:
        allowed = {"persona", "world", "user"}
        aliases = {
            "角色": "persona",
            "角色设定": "persona",
            "worldview": "world",
            "世界观": "world",
            "世界观设定": "world",
            "owner": "user",
            "master": "user",
            "主人": "user",
            "主人设定": "user",
            "用户": "user",
            "用户设定": "user",
        }
        if isinstance(raw, list):
            items = raw
        else:
            items = str(raw or "").split(",") if raw else []
        result: list[str] = []
        for item in items:
            text = str(item or "").strip()
            normalized = aliases.get(text, text.lower())
            if normalized in allowed and normalized not in result:
                result.append(normalized)
        return result or ["persona"]

    def _roleplay_draft_from_persona_prompt(self, persona_prompt: str, scopes: list[str] | None = None) -> str:
        source = str(persona_prompt or "").strip()
        if len(source) > 9000:
            source = source[:9000] + "\n（后文已截断）"
        selected = set(scopes or ["persona"])
        scope_lines = [
            "本次需要整理的范围：",
            f"- 角色设定：{'生成' if 'persona' in selected else '不要生成，字段留空'}",
            f"- 世界观设定：{'生成' if 'world' in selected else '不要生成，字段留空'}",
            f"- 主人/用户设定：{'生成' if 'user' in selected else '不要生成，字段留空'}",
        ]
        user_rule = (
            "主人/用户设定只在原文明确写出对用户的称呼、用户身份或相处方式时抽取；"
            "不要推断用户性别、生日、职业、权限、隐私偏好或亲密关系。"
            if "user" in selected
            else "不要生成任何用户资料、主人资料、用户关系或用户偏好。"
        )
        return (
            "你要把一段 AstrBot 主回复人格文本谨慎整理成陪伴插件的角色/世界观设定草稿。\n"
            + "\n".join(scope_lines)
            + "\n"
            "只允许从原文明确抽取或保守改写，不要扩写成新人格，不要补完缺失设定。\n"
            "不确定、没有出现、只是氛围暗示的字段一律留空。\n"
            f"{user_rule}\n"
            "外貌线索只写角色自己的可视特征，方便识图，不要写回复策略。\n"
            "世界观只写原文明确存在的背景；现代日常默认不需要硬写世界观。\n"
            "翻译词只在原文有明确世界观替代表达时填写，否则留空。\n"
            "所有字段尽量短，适合用户二次编辑。只输出 JSON 对象，不要 Markdown，不要解释。\n"
            "JSON 结构如下，缺失字段也要保留为空字符串：\n"
            "{\n"
            '  "persona_parts": {"name":"","species":"","age":"","gender":"","appearance":"","hair":"","eyes":"","clothing":"","identity":"","personality":"","desire":"","hobbies":"","taboo":"","key_lore":"","extra":""},\n'
            '  "world_parts": {"world":"","era":"","tone":"","rules":"","scenes":"","network":"","extra":""},\n'
            '  "user_parts": {"nickname":"","user_gender":"","user_age":"","user_occupation":"","role_relation":"","interaction":"","extra":""},\n'
            '  "translations": {"群聊":"","识屏":"","B站":"","QQ空间":"","书柜":""},\n'
            '  "image_self_recognition_hint": "",\n'
            '  "notes": []\n'
            "}\n\n"
            "主回复人格原文：\n"
            f"{source}"
        )

    def _loads_json_object(self, raw: Any) -> dict[str, Any]:
        text = str(raw or "").strip()
        if not text:
            raise ValueError("模型返回为空")
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
            text = re.sub(r"\s*```$", "", text).strip()
        try:
            parsed = json.loads(text)
        except Exception:
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("模型没有返回 JSON 对象")
            parsed = json.loads(text[start : end + 1])
        if not isinstance(parsed, dict):
            raise ValueError("模型返回的 JSON 不是对象")
        return parsed

    def _normalize_roleplay_draft_result(self, raw: dict[str, Any], scopes: list[str] | None = None) -> dict[str, Any]:
        selected = set(scopes or ["persona"])
        persona_keys = {
            "name",
            "species",
            "age",
            "gender",
            "appearance",
            "hair",
            "eyes",
            "clothing",
            "identity",
            "personality",
            "desire",
            "hobbies",
            "taboo",
            "key_lore",
            "extra",
        }
        world_keys = {"world", "era", "tone", "rules", "scenes", "network", "extra"}
        user_keys = {"nickname", "user_gender", "user_age", "user_occupation", "role_relation", "interaction", "extra"}
        translation_keys = {"群聊", "识屏", "B站", "QQ空间", "书柜"}

        def text_map(value: Any, allowed: set[str], limit: int = 220) -> dict[str, str]:
            source = value if isinstance(value, dict) else {}
            return {key: self._multi_line(source.get(key), limit) for key in allowed}

        notes_raw = raw.get("notes")
        notes: list[str] = []
        if isinstance(notes_raw, list):
            for item in notes_raw[:8]:
                note = self._single_line(item, 120)
                if note:
                    notes.append(note)
        return {
            "persona_parts": text_map(raw.get("persona_parts"), persona_keys) if "persona" in selected else text_map({}, persona_keys),
            "world_parts": text_map(raw.get("world_parts"), world_keys) if "world" in selected else text_map({}, world_keys),
            "user_parts": text_map(raw.get("user_parts"), user_keys) if "user" in selected else text_map({}, user_keys),
            "translations": text_map(raw.get("translations"), translation_keys, 80) if "world" in selected else text_map({}, translation_keys, 80),
            "image_self_recognition_hint": self._multi_line(raw.get("image_self_recognition_hint"), 360) if "persona" in selected else "",
            "notes": notes,
        }

    async def list_available_providers(self) -> dict[str, Any]:
        try:
            items = self._available_provider_items()
            return self._ok({"items": items, "total": len(items)})
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 获取 Provider 列表失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def test_provider(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        key = str(payload.get("key", "")).strip()
        provider_id = self._single_line(payload.get("provider_id"), 160)
        if key and key not in self._allowed_provider_keys():
            return self._error("不允许测试该 Provider 配置项")
        start = time.time()
        try:
            text = await self.plugin._llm_call(
                "请只回复两个字：正常",
                max_tokens=16,
                provider_id=provider_id,
                task="provider_test",
            )
            elapsed_ms = int((time.time() - start) * 1000)
            ok = bool(text)
            return self._ok(
                {
                    "ok": ok,
                    "key": key,
                    "provider_id": provider_id,
                    "elapsed_ms": elapsed_ms,
                    "sample": self._single_line(text, 80),
                }
            )
        except Exception as exc:
            return self._ok(
                {
                    "ok": False,
                    "key": key,
                    "provider_id": provider_id,
                    "elapsed_ms": int((time.time() - start) * 1000),
                    "error": str(exc),
                }
            )

    def _user_summary(self, user_id: str, user: dict[str, Any]) -> dict[str, Any]:
        last_seen = user.get("last_seen", 0)
        last_sent = user.get("last_sent", 0)
        user_id_text = str(user_id)
        is_qq_user = user_id_text.isdigit()
        umo = str(user.get("umo", "") or "")
        source = self._single_line(umo.split(":", 1)[0], 40) if ":" in umo else ""
        nickname = self._single_line(user.get("nickname"), 40)
        generic_names = {"用户", "主人", "默认用户"}
        if is_qq_user:
            display_name = nickname if nickname and nickname not in generic_names else user_id_text
        else:
            display_name = f"临时会话 · {user_id_text[:8]}"
        relationship_stage = ""
        rel_state = user.get("relationship_state") if isinstance(user.get("relationship_state"), dict) else {}
        if isinstance(rel_state, dict):
            relationship_stage = self._single_line(rel_state.get("stage"), 12)
        profile_getter = getattr(self.plugin, "_relationship_profile", None)
        if not relationship_stage and callable(profile_getter):
            try:
                profile = profile_getter(user)
                if isinstance(profile, dict):
                    relationship_stage = self._single_line(profile.get("level"), 12)
            except Exception:
                relationship_stage = ""
        if relationship_stage not in {"亲近", "熟悉", "陌生"}:
            persona_profile = user.get("persona_relationship") if isinstance(user.get("persona_relationship"), dict) else {}
            relationship_stage = self._single_line(persona_profile.get("level"), 12) if isinstance(persona_profile, dict) else ""
        if relationship_stage not in {"亲近", "熟悉", "陌生"}:
            score = self._int(user.get("relationship_score"))
            inbound_count = self._int(user.get("inbound_count"))
            proactive_count = self._int(user.get("proactive_sent_count"))
            reply_count = self._int(user.get("reply_count"))
            reply_rate = reply_count / proactive_count if proactive_count > 0 else 0.0
            if score >= 16 and reply_rate >= 0.35:
                relationship_stage = "亲近"
            elif score >= 3 or inbound_count >= 1 or reply_rate >= 0.2:
                relationship_stage = "熟悉"
            else:
                relationship_stage = "陌生"
        role = self.plugin._private_user_role(user, user_id_text) if hasattr(self.plugin, "_private_user_role") else ""
        role_labeler = getattr(self.plugin, "_private_user_role_label", None)
        role_label = role_labeler(role) if callable(role_labeler) else ("主人" if role == "owner" else "朋友")
        return {
            "user_id": user_id_text,
            "display_name": display_name,
            "is_qq_user": is_qq_user,
            "source": source,
            "enabled": bool(user.get("enabled", True)),
            "relationship_role": role,
            "relationship_role_label": role_label,
            "nickname": user.get("nickname", ""),
            "style": user.get("style", ""),
            "umo": user.get("umo", ""),
            "last_seen_ts": last_seen,
            "last_seen": self.plugin._format_timestamp_elapsed(last_seen),
            "last_sent_ts": last_sent,
            "last_sent": self.plugin._format_timestamp_elapsed(last_sent),
            "sent_today": user.get("sent_today", 0),
            "last_proactive_skip_ts": self._float(user.get("last_proactive_skip_at")),
            "last_proactive_skip": self.plugin._format_timestamp_elapsed(user.get("last_proactive_skip_at", 0)),
            "last_proactive_skip_reason": self._single_line(user.get("last_proactive_skip_reason"), 120),
            "last_proactive_skip_prefix": self._single_line(user.get("last_proactive_skip_prefix"), 20),
            "effective_daily_limit": (
                self.plugin._effective_user_daily_limit(user)
                if hasattr(self.plugin, "_effective_user_daily_limit")
                else getattr(self.plugin, "max_daily_messages", 0)
            ),
            "effective_idle_minutes": (
                self.plugin._effective_user_idle_minutes(user)
                if hasattr(self.plugin, "_effective_user_idle_minutes")
                else getattr(self.plugin, "idle_minutes", 0)
            ),
            "effective_min_interval_minutes": (
                self.plugin._effective_user_min_interval_minutes(user)
                if hasattr(self.plugin, "_effective_user_min_interval_minutes")
                else getattr(self.plugin, "min_interval_minutes", 0)
            ),
            "effective_screen_peek_daily_limit": (
                self.plugin._effective_user_screen_peek_daily_limit(user)
                if hasattr(self.plugin, "_effective_user_screen_peek_daily_limit")
                else getattr(self.plugin, "screen_peek_max_daily", 0)
            ),
            "effective_photo_daily_limit": (
                self.plugin._effective_user_photo_daily_limit(user)
                if hasattr(self.plugin, "_effective_user_photo_daily_limit")
                else getattr(self.plugin, "photo_action_max_daily", 0)
            ),
            "proactive_daily_limit": user.get("proactive_daily_limit", -1),
            "proactive_idle_minutes": user.get("proactive_idle_minutes", -1),
            "proactive_min_interval_minutes": user.get("proactive_min_interval_minutes", -1),
            "photo_daily_limit": user.get("photo_daily_limit", -1),
            "screen_peek_daily_limit": user.get("screen_peek_daily_limit", -1),
            "poke_daily_limit": user.get("poke_daily_limit", -1),
            "proactive_boundary_note": user.get("proactive_boundary_note", ""),
            "inbound_count": user.get("inbound_count", 0),
            "reply_count": user.get("reply_count", 0),
            "proactive_sent_count": user.get("proactive_sent_count", 0),
            "relationship_score": user.get("relationship_score", 0),
            "relationship_stage": relationship_stage,
            "planned_reason": user.get("planned_proactive_reason", ""),
            "planned_action": user.get("planned_proactive_action", ""),
            "next_proactive_ts": user.get("next_proactive_at", 0),
            "next_proactive": self.plugin._format_next_proactive(user),
            "memory_items": self._memory_item_count(user.get("companion_memory")),
            "dialogue_episode_count": len(user.get("dialogue_episodes") or []),
            "open_loop_count": len(user.get("open_loops") or []),
            "habit_count": len(self._behavior_habit_summary(user).get("items", [])),
            "alias_user_ids": [
                self._single_line(item, 80)
                for item in (user.get("alias_user_ids") if isinstance(user.get("alias_user_ids"), list) else [])
                if self._single_line(item, 80)
            ],
        }

    def _worldbook_member_for_private_user_locked(
        self,
        data: dict[str, Any],
        user_id: str,
        user: dict[str, Any],
    ) -> dict[str, Any] | None:
        profiles = data.get("worldbook_member_profiles") if isinstance(data.get("worldbook_member_profiles"), dict) else {}
        if not profiles:
            return None
        candidate_ids = [str(user_id)]
        if isinstance(user, dict):
            candidate_ids.extend(
                self._single_line(item, 80)
                for item in (user.get("alias_user_ids") if isinstance(user.get("alias_user_ids"), list) else [])
                if self._single_line(item, 80)
            )
        for candidate_id in candidate_ids:
            profile = profiles.get(candidate_id)
            if isinstance(profile, dict):
                return self._worldbook_member_profile_summary(candidate_id, profile)
        for profile_id, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            linked_id = self._single_line(profile.get("linked_qq_user_id") or profile.get("merged_into_user_id"), 80)
            external_ids = profile.get("external_ids") if isinstance(profile.get("external_ids"), list) else []
            external_id_set = {self._single_line(item, 80) for item in external_ids if self._single_line(item, 80)}
            if linked_id in candidate_ids or any(candidate_id in external_id_set for candidate_id in candidate_ids):
                return self._worldbook_member_profile_summary(str(profile_id), profile)
        return None

    def _worldbook_member_profile_summary(self, user_id: str, item: dict[str, Any]) -> dict[str, Any]:
        aliases = item.get("aliases") if isinstance(item.get("aliases"), list) else []
        observed = item.get("observed_names") if isinstance(item.get("observed_names"), list) else []
        external_ids = item.get("external_ids") if isinstance(item.get("external_ids"), list) else []
        memories = self._normalize_important_memories(item.get("important_memories"))
        pending = item.get("pending_observations") if isinstance(item.get("pending_observations"), list) else []
        return {
            "user_id": self._single_line(user_id, 40),
            "identity_type": self._single_line(item.get("identity_type") or ("qq" if str(user_id).isdigit() else "external"), 20),
            "name": self._single_line(item.get("name"), 60),
            "gender": self._single_line(item.get("gender"), 40),
            "enabled": bool(item.get("enabled", True)),
            "priority": item.get("priority", 120),
            "aliases": [self._single_line(alias, 40) for alias in aliases if self._single_line(alias, 40)],
            "observed_names": [self._single_line(name, 40) for name in observed if self._single_line(name, 40)],
            "external_ids": [self._single_line(ext, 80) for ext in external_ids if self._single_line(ext, 80)],
            "linked_qq_user_id": self._single_line(item.get("linked_qq_user_id") or item.get("merged_into_user_id"), 40),
            "linked_bili_profile_id": self._single_line(item.get("linked_bili_profile_id"), 80),
            "content": self._single_line(item.get("content"), 260),
            "identity_note": self._single_line(item.get("identity_note") or item.get("note") or item.get("content"), 500),
            "boundary_note": self._single_line(item.get("boundary_note"), 500),
            "important_memories": memories[:6],
            "pending_observation_count": len(pending),
            "source_entries": item.get("source_entries") if isinstance(item.get("source_entries"), list) else [],
            "note": self._single_line(item.get("note"), 500),
        }

    def _behavior_habit_summary(self, user: dict[str, Any]) -> dict[str, Any]:
        formatter = getattr(self.plugin, "_qualified_user_behavior_habits", None)
        if callable(formatter):
            try:
                items = formatter(user)
            except Exception:
                items = []
        else:
            raw = user.get("behavior_habits") if isinstance(user.get("behavior_habits"), dict) else {}
            patterns = raw.get("patterns") if isinstance(raw.get("patterns"), list) else []
            items = [item for item in patterns if isinstance(item, dict)]
        normalized = []
        for item in items[:12]:
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    "bucket": self._single_line(item.get("bucket"), 12),
                    "category": self._single_line(item.get("category"), 20),
                    "topic": self._single_line(item.get("topic"), 80),
                    "count": self._int(item.get("count")),
                    "avg_time": self.plugin._format_user_habit_time(item.get("avg_minute")) if hasattr(self.plugin, "_format_user_habit_time") else "",
                    "last_seen": self.plugin._format_timestamp_elapsed(item.get("last_seen_ts", 0)),
                    "last_seen_text": self._single_line(item.get("last_seen_text"), 100),
                }
            )
        raw_habits = user.get("behavior_habits") if isinstance(user.get("behavior_habits"), dict) else {}
        return {
            "enabled": bool(getattr(self.plugin, "enable_user_habit_learning", False)),
            "updated_at": self._single_line(raw_habits.get("updated_at"), 30) if isinstance(raw_habits, dict) else "",
            "items": normalized,
        }

    def _expression_profile_summary(self, user: dict[str, Any]) -> dict[str, Any]:
        profile = user.get("expression_profile") if isinstance(user.get("expression_profile"), dict) else {}

        def sample_row(item: Any, index: int) -> dict[str, Any]:
            raw = item if isinstance(item, dict) else {}
            text = self._single_line(raw.get("text") or raw.get("phrase") or raw.get("ending"), 120)
            punctuation = raw.get("punctuation") if isinstance(raw.get("punctuation"), dict) else {}
            marks = "".join(f"{key}{value}" for key, value in punctuation.items() if self._int(value) > 0)
            return {
                "id": self._single_line(raw.get("id"), 40) or str(index),
                "index": index,
                "text": text,
                "phrase": self._single_line(raw.get("phrase"), 80),
                "ending": self._single_line(raw.get("ending"), 20),
                "length": self._int(raw.get("length")),
                "punctuation": marks,
                "created_at": self._single_line(raw.get("created_at"), 30),
                "time": self.plugin._format_timestamp_elapsed(raw.get("ts", 0)),
            }

        samples = profile.get("samples") if isinstance(profile.get("samples"), list) else []
        pending = profile.get("pending_samples") if isinstance(profile.get("pending_samples"), list) else []
        formatter = getattr(self.plugin, "_format_expression_profile_for_prompt", None)
        try:
            prompt_preview = formatter(user) if callable(formatter) else ""
        except Exception:
            prompt_preview = ""
        return {
            "enabled": bool(getattr(self.plugin, "enable_expression_learning", False)),
            "mode": self._single_line(getattr(self.plugin, "expression_learning_mode", "balanced"), 20),
            "manual_review": bool(getattr(self.plugin, "enable_expression_manual_review", False)),
            "style_review": bool(getattr(self.plugin, "enable_expression_style_review", True)),
            "updated_at": self._single_line(profile.get("updated_at"), 30),
            "sample_count": len(samples),
            "pending_count": len(pending),
            "short_count": self._int(profile.get("short_count")),
            "endings": [self._single_line(item, 20) for item in (profile.get("endings") if isinstance(profile.get("endings"), list) else [])[:8]],
            "recent_phrases": [self._single_line(item, 80) for item in (profile.get("recent_phrases") if isinstance(profile.get("recent_phrases"), list) else [])[:8]],
            "samples": [sample_row(item, idx) for idx, item in enumerate(samples[:12])],
            "pending_samples": [sample_row(item, idx) for idx, item in enumerate(pending[:24])],
            "prompt_preview": self._multi_line(prompt_preview, 500),
        }

    def _apply_expression_profile_action(self, user: dict[str, Any], payload: dict[str, Any]) -> str:
        profile = user.setdefault("expression_profile", {})
        if not isinstance(profile, dict):
            profile = {}
            user["expression_profile"] = profile
        action = self._single_line(payload.get("expression_action"), 40)
        pending = profile.get("pending_samples") if isinstance(profile.get("pending_samples"), list) else []
        samples = profile.get("samples") if isinstance(profile.get("samples"), list) else []
        sample_id = self._single_line(payload.get("sample_id"), 40)
        sample_index = self._int(payload.get("sample_index"))

        def find_index(items: list[Any]) -> int:
            if sample_id:
                for idx, item in enumerate(items):
                    if isinstance(item, dict) and self._single_line(item.get("id"), 40) == sample_id:
                        return idx
            if 0 <= sample_index < len(items):
                return sample_index
            return -1

        if action == "clear_pending":
            profile["pending_samples"] = []
            profile["pending_count"] = 0
            profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            return "已清空待审核表达样本"
        if action in {"approve", "reject"}:
            idx = find_index(pending)
            if idx < 0:
                return "没有找到待审核样本"
            item = pending.pop(idx)
            profile["pending_samples"] = pending
            profile["pending_count"] = len(pending)
            if action == "reject":
                profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                return "已删除待审核样本"
            if isinstance(item, dict):
                approved = dict(item)
                approved.pop("review_status", None)
                approved["approved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                samples.insert(0, approved)
                limit = max(4, int(getattr(self.plugin, "max_learned_expression_items", 18) or 18))
                profile["samples"] = samples[:limit]
                refresher = getattr(self.plugin, "_refresh_expression_profile_legacy_summary", None)
                if callable(refresher):
                    refresher(profile)
                profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                return "已通过表达样本"
            return "待审核样本格式异常"
        if action == "delete_sample":
            idx = find_index(samples)
            if idx < 0:
                return "没有找到已入库样本"
            samples.pop(idx)
            profile["samples"] = samples
            refresher = getattr(self.plugin, "_refresh_expression_profile_legacy_summary", None)
            if callable(refresher):
                refresher(profile)
            profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            return "已删除表达样本"
        return "未知表达样本操作"

    @classmethod
    def _display_message_text(cls, value: Any, limit: int = 500) -> str:
        source = str(value or "").strip()
        source = source.strip("\"'“”‘’` ")
        if cls._looks_like_internal_delivery_receipt(source):
            return ""
        if re.fullmatch(r"[.。…~～\s\"'“”‘’`-]{0,12}", source):
            return ""
        if re.search(r"<t{2,}s\b[^>]*>.*?</t{2,}s>", source, flags=re.IGNORECASE | re.DOTALL):
            outside = re.sub(r"<t{2,}s\b[^>]*>.*?</t{2,}s>", "", source, flags=re.IGNORECASE | re.DOTALL)
            outside = re.sub(r"</?t{2,}s\b[^>]*>", "", outside, flags=re.IGNORECASE).strip()
            if re.search(r"[\u4e00-\u9fff]", outside):
                source = outside
            else:
                source = re.sub(r"</?t{2,}s\b[^>]*>", "", source, flags=re.IGNORECASE)
        if re.search(r"[\u3040-\u30ff]", source) and re.search(r"[\u4e00-\u9fff]", source):
            units = re.findall(r".*?[。！？!?…~～]+|.+$", source, flags=re.DOTALL)
            kept = [unit.strip() for unit in units if unit.strip() and not re.search(r"[\u3040-\u30ff]", unit)]
            if kept and any(re.search(r"[\u4e00-\u9fff]", item) for item in kept):
                source = "".join(kept)
        return cls._single_line(_strip_internal_message_blocks(source), limit)

    @staticmethod
    def _looks_like_internal_delivery_receipt(text: Any) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        compact = re.sub(r"[\s。.!！?？,，；;:：、~～\"'“”‘’（）()【】\[\]]+", "", raw).lower()
        if compact in {"已发送", "发送成功", "发送完成", "发送完毕", "已成功发送", "消息已发送", "消息发送成功"}:
            return True
        markers = (
            ("已经把", "转给"),
            ("已把", "转给"),
            ("已经将", "转给"),
            ("已将", "转给"),
            ("已经发给", "就假装"),
            ("已经发送给", "就假装"),
            ("就假装", "语气很自然"),
            ("随手分享", "语气很自然"),
        )
        if any(all(token in raw for token in pair) for pair in markers):
            return True
        return (
            any(token in compact for token in ("视频链接转给", "链接转给", "消息转给", "内容转给"))
            and any(token in compact for token in ("已经", "已", "完成", "成功"))
        )

    def _group_wakeup_runtime(self, group: dict[str, Any]) -> dict[str, Any]:
        fatigue = group.get("group_wakeup_fatigue") if isinstance(group.get("group_wakeup_fatigue"), dict) else {}
        high_intensity = {}
        if hasattr(self.plugin, "_group_high_intensity_state"):
            try:
                high_intensity = self.plugin._group_high_intensity_state(group, mutate=False)
            except Exception:
                high_intensity = {}
        value = self._float(fatigue.get("value"))
        limit = self._int(fatigue.get("limit")) or int(getattr(self.plugin, "group_wakeup_fatigue_limit", 5) or 5)
        ratio = max(0.0, min(1.0, value / max(1, limit)))
        if ratio >= 1.0:
            label = "疲劳高"
            level = "high"
        elif ratio >= 0.55:
            label = "有点累"
            level = "medium"
        elif value >= 0.4:
            label = "轻微"
            level = "low"
        else:
            label = "无"
            level = "none"
        return {
            "value": round(value, 2),
            "limit": limit,
            "ratio": round(ratio, 3),
            "label": label,
            "level": level,
            "updated": self.plugin._format_timestamp_elapsed(fatigue.get("updated_ts", 0)),
            "high_intensity": {
                "active": bool(high_intensity.get("active")) if isinstance(high_intensity, dict) else False,
                "merge_active": bool(high_intensity.get("merge_active")) if isinstance(high_intensity, dict) else False,
                "reason": self._single_line(high_intensity.get("reason"), 40) if isinstance(high_intensity, dict) else "",
                "recent_wakeups": self._int(high_intensity.get("recent_wakeups")) if isinstance(high_intensity, dict) else 0,
                "threshold": self._int(high_intensity.get("threshold")) if isinstance(high_intensity, dict) else 0,
                "merge_recent_floor": self._int(high_intensity.get("merge_recent_floor")) if isinstance(high_intensity, dict) else 0,
                "remaining_seconds": self._float(high_intensity.get("remaining_seconds")) if isinstance(high_intensity, dict) else 0.0,
                "merge_seconds": self._float(getattr(self.plugin, "group_high_intensity_merge_seconds", 8)),
                "max_merge_messages": self._int(getattr(self.plugin, "group_high_intensity_max_merge_messages", 8)),
                "merge_scope": self._single_line(getattr(self.plugin, "group_high_intensity_merge_scope", "group"), 20),
            },
        }

    def _group_wakeup_logs(self, group: dict[str, Any], limit: int = 30) -> list[dict[str, Any]]:
        logs = group.get("group_wakeup_logs") if isinstance(group.get("group_wakeup_logs"), list) else []
        items: list[dict[str, Any]] = []
        for raw in reversed(logs[-limit:]):
            if not isinstance(raw, dict):
                continue
            items.append(
                {
                    "ts": self._float(raw.get("ts")),
                    "time": self.plugin._format_timestamp_elapsed(raw.get("ts", 0)),
                    "result": self._single_line(raw.get("result"), 32),
                    "type": self._single_line(raw.get("type"), 40),
                    "word": self._single_line(raw.get("word"), 60),
                    "strength": self._single_line(raw.get("strength"), 24),
                    "strength_label": self._single_line(raw.get("strength_label"), 24),
                    "probability": round(self._float(raw.get("probability")), 3),
                    "score": self._int(raw.get("score")),
                    "threshold": self._int(raw.get("threshold")),
                    "intensity": self._single_line(raw.get("intensity"), 20),
                    "help_type": self._single_line(raw.get("help_type"), 30),
                    "reason": self._single_line(raw.get("reason"), 80),
                    "reason_label": self._single_line(raw.get("reason_label"), 80),
                    "reason_detail": self._single_line(raw.get("reason_detail"), 180),
                    "topic_weight": raw.get("topic_weight") if isinstance(raw.get("topic_weight"), dict) else {},
                    "note": self._single_line(raw.get("note"), 180),
                    "sender_id": self._single_line(raw.get("sender_id"), 40),
                    "sender_name": self._single_line(raw.get("sender_name"), 40),
                    "text": self._display_message_text(raw.get("text"), 160),
                    "fatigue_value": round(self._float(raw.get("fatigue_value")), 2),
                    "fatigue_label": self._single_line(raw.get("fatigue_label"), 20),
                }
            )
        return items

    def _group_slang_items(self, group: dict[str, Any], limit: int = 120) -> list[dict[str, Any]]:
        terms = group.get("slang_terms") if isinstance(group.get("slang_terms"), list) else []
        meanings = group.get("slang_meanings") if isinstance(group.get("slang_meanings"), dict) else {}
        indexed: dict[str, dict[str, Any]] = {}
        for raw in terms:
            if isinstance(raw, dict):
                term = self._single_line(raw.get("term"), 40)
                if not term:
                    continue
                indexed[term] = {
                    "term": term,
                    "count": self._int(raw.get("count")),
                    "last_seen_ts": self._float(raw.get("last_seen")),
                    "last_seen": self.plugin._format_timestamp_elapsed(raw.get("last_seen", 0)),
                    "learned": True,
                }
            else:
                term = self._single_line(raw, 40)
                if term:
                    indexed[term] = {"term": term, "count": 0, "last_seen_ts": 0.0, "last_seen": "", "learned": True}
        for term, raw in meanings.items():
            key = self._single_line(term, 40)
            if key and key not in indexed:
                indexed[key] = {"term": key, "count": 0, "last_seen_ts": 0.0, "last_seen": "", "learned": False}

        uncertain_checker = getattr(self.plugin, "_is_uncertain_group_slang_meaning", None)
        items: list[dict[str, Any]] = []
        for term, base in indexed.items():
            raw_meaning = meanings.get(term) if isinstance(meanings.get(term), dict) else {}
            meaning = self._single_line(raw_meaning.get("meaning"), 120) if isinstance(raw_meaning, dict) else ""
            usage = self._single_line(raw_meaning.get("usage"), 120) if isinstance(raw_meaning, dict) else ""
            confidence = min(1.0, self._float(raw_meaning.get("confidence"))) if isinstance(raw_meaning, dict) else 0.0
            web_match = min(1.0, self._float(raw_meaning.get("web_match"))) if isinstance(raw_meaning, dict) else 0.0
            is_uncertain = True
            if meaning:
                if callable(uncertain_checker):
                    is_uncertain = bool(uncertain_checker(meaning, usage))
                else:
                    is_uncertain = any(marker in f"{meaning} {usage}" for marker in ("不确定", "无法判断", "语境不明", "可能是"))
            if meaning and confidence >= 0.55 and not is_uncertain:
                status = "injectable"
                status_label = "会注入"
            elif meaning and confidence < 0.55:
                status = "low_confidence"
                status_label = "低置信度"
            elif meaning and is_uncertain:
                status = "uncertain"
                status_label = "释义不足"
            else:
                status = "pending"
                status_label = "尚未释义"
            items.append(
                {
                    **base,
                    "meaning": meaning,
                    "usage": usage,
                    "type": self._single_line(raw_meaning.get("type"), 24) if isinstance(raw_meaning, dict) else "",
                    "not_owner": self._single_line(raw_meaning.get("not_owner"), 90) if isinstance(raw_meaning, dict) else "",
                    "evidence": self._single_line(raw_meaning.get("evidence"), 160) if isinstance(raw_meaning, dict) else "",
                    "source": self._single_line(raw_meaning.get("source"), 32) if isinstance(raw_meaning, dict) else "",
                    "updated_at": self._single_line(raw_meaning.get("updated_at"), 32) if isinstance(raw_meaning, dict) else "",
                    "confidence": round(confidence, 2),
                    "web_match": round(web_match, 2),
                    "web_evidence": self._single_line(raw_meaning.get("web_evidence"), 220) if isinstance(raw_meaning, dict) else "",
                    "status": status,
                    "status_label": status_label,
                }
            )
        items.sort(
            key=lambda item: (
                item.get("status") != "injectable",
                -self._int(item.get("count")),
                -self._float(item.get("last_seen_ts")),
                item.get("term") or "",
            )
        )
        return items[:limit]

    def _group_summary(self, group_id: str, group: dict[str, Any]) -> dict[str, Any]:
        atmosphere = group.get("atmosphere") if isinstance(group.get("atmosphere"), dict) else {}
        slang_terms = group.get("slang_terms") if isinstance(group.get("slang_terms"), list) else []
        slang_meanings = group.get("slang_meanings") if isinstance(group.get("slang_meanings"), dict) else {}
        members = group.get("members") if isinstance(group.get("members"), dict) else {}
        group_id_text = str(group_id)
        group_name = self._single_line(group.get("name") or group.get("group_name") or group.get("display_name"), 80)
        if group_name == group_id_text:
            group_name = ""
        cleaner = getattr(self.plugin, "_cleanup_group_slang_terms", None)
        if callable(cleaner):
            try:
                group_for_filter = {
                    "slang_terms": [dict(item) if isinstance(item, dict) else item for item in slang_terms],
                    "slang_meanings": {str(key): dict(value) if isinstance(value, dict) else value for key, value in slang_meanings.items()},
                    "members": {
                        str(user_id): {
                            key: member.get(key)
                            for key in ("name", "identity_name", "display_name", "nickname", "card")
                            if isinstance(member, dict) and key in member
                        }
                        for user_id, member in members.items()
                        if isinstance(member, dict)
                    },
                }
                if cleaner(group_for_filter):
                    slang_terms = group_for_filter.get("slang_terms") if isinstance(group_for_filter.get("slang_terms"), list) else []
            except Exception:
                pass
        identity_count = sum(1 for item in members.values() if isinstance(item, dict) and item.get("identity_known"))
        wakeup_logs = group.get("group_wakeup_logs") if isinstance(group.get("group_wakeup_logs"), list) else []
        last_wakeup = group.get("last_group_wakeup") if isinstance(group.get("last_group_wakeup"), dict) else {}
        last_interjection = self._sanitize_last_bot_interjection(group.get("last_bot_interjection"))
        return {
            "group_id": group_id_text,
            "name": group_name,
            "group_name": group_name,
            "display_name": group_name or "未命名群聊",
            "enabled": bool(group.get("enabled", True)),
            "allowed_by_mode": self.plugin._group_allowed_by_access_mode(group_id_text),
            "message_count": group.get("message_count", 0),
            "last_seen_ts": group.get("last_seen", 0),
            "last_seen": self.plugin._format_timestamp_elapsed(group.get("last_seen", 0)),
            "member_count": len(members),
            "recognized_member_count": identity_count,
            "recent_message_count": len(group.get("recent_messages") or []),
            "slang_count": len(slang_terms),
            "slang_meaning_count": len(slang_meanings),
            "slang_terms": slang_terms[:16],
            "topic_count": len(group.get("topic_threads") or []),
            "episode_count": len(group.get("group_episodes") or []),
            "relationship_edge_count": len(group.get("relationship_edges") or {}),
            "interject_today": group.get("interject_today", 0),
            "effective_interject_max_daily": (
                self.plugin._effective_group_interject_max_daily()
                if hasattr(self.plugin, "_effective_group_interject_max_daily")
                else getattr(self.plugin, "group_interject_max_daily", 0)
            ),
            "effective_interject_min_interval_minutes": (
                self.plugin._effective_group_interject_min_interval_minutes()
                if hasattr(self.plugin, "_effective_group_interject_min_interval_minutes")
                else getattr(self.plugin, "group_interject_min_interval_minutes", 0)
            ),
            "last_interject": self.plugin._format_timestamp_elapsed(group.get("last_interject_at", 0)),
            "last_bot_interjection": last_interjection,
            "wakeup_log_count": len(wakeup_logs),
            "wakeup_fatigue": self._group_wakeup_runtime(group),
            "last_group_wakeup": {
                "time": self.plugin._format_timestamp_elapsed(last_wakeup.get("ts", 0)),
                "type": self._single_line(last_wakeup.get("type"), 40),
                "word": self._single_line(last_wakeup.get("word"), 60),
                "strength_label": self._single_line(last_wakeup.get("strength_label"), 24),
                "score": self._int(last_wakeup.get("score")),
                "threshold": self._int(last_wakeup.get("threshold")),
                "intensity": self._single_line(last_wakeup.get("intensity"), 20),
                "help_type": self._single_line(last_wakeup.get("help_type"), 30),
                "reason": self._single_line(last_wakeup.get("reason"), 80),
                "reason_label": self._single_line(last_wakeup.get("reason_label"), 80),
                "reason_detail": self._single_line(last_wakeup.get("reason_detail"), 180),
                "sender_name": self._single_line(last_wakeup.get("sender_name"), 40),
                "text": self._display_message_text(last_wakeup.get("text"), 120),
            } if last_wakeup else {},
            "atmosphere": {
                "mood": atmosphere.get("mood", ""),
                "heat": atmosphere.get("heat", ""),
                "last_summary": atmosphere.get("summary", ""),
            },
        }

    def _sanitize_last_bot_interjection(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or not value:
            return {}
        item = dict(value)
        item["text"] = self._display_message_text(item.get("text"), 120)
        if not item["text"] and not item.get("has_image"):
            return {}
        return item

    def _group_topic_thread_items(self, group: dict[str, Any], limit: int = 16) -> list[dict[str, Any]]:
        threads = group.get("topic_threads") if isinstance(group.get("topic_threads"), list) else []
        members = group.get("members") if isinstance(group.get("members"), dict) else {}
        now = time.time()
        items: list[dict[str, Any]] = []
        for index, raw in enumerate(threads[:limit]):
            if not isinstance(raw, dict):
                continue
            started_ts = self._float(raw.get("started_ts"))
            last_ts = self._float(raw.get("last_ts"))
            duration_seconds = max(0.0, (last_ts or started_ts) - started_ts) if started_ts else 0.0
            participants = raw.get("participants") if isinstance(raw.get("participants"), list) else []
            participant_items = []
            for user_id in participants[:8]:
                uid = self._single_line(user_id, 40)
                member = members.get(uid) if isinstance(members.get(uid), dict) else {}
                name = self._single_line(
                    member.get("display_name")
                    or member.get("nickname")
                    or member.get("name")
                    or member.get("card")
                    or uid,
                    24,
                )
                if uid:
                    participant_items.append({"id": uid, "name": name or uid})
            examples = []
            for example in (raw.get("recent_examples") if isinstance(raw.get("recent_examples"), list) else [])[-4:]:
                if not isinstance(example, dict):
                    continue
                examples.append(
                    {
                        "name": self._single_line(example.get("name"), 24),
                        "text": self._single_line(example.get("text"), 120),
                        "time": self.plugin._format_timestamp_elapsed(example.get("ts", 0)),
                    }
                )
            message_count = self._int(raw.get("message_count"))
            freshness = max(0.0, now - last_ts) if last_ts else 0.0
            heat = min(100, max(8, message_count * 10 + len(participant_items) * 8 - int(freshness / 600) * 5))
            status = "活跃" if freshness <= 15 * 60 else "刚冷却" if freshness <= 90 * 60 else "历史"
            title = self._single_line(raw.get("title") or raw.get("topic") or raw.get("summary"), 80)
            items.append(
                {
                    "rank": index + 1,
                    "title": title or "未命名话题",
                    "summary": self._single_line(raw.get("summary"), 180),
                    "message_count": message_count,
                    "participant_count": len(participants),
                    "participants": participant_items,
                    "recent_examples": examples,
                    "started": self.plugin._format_timestamp_elapsed(started_ts),
                    "last_seen": self.plugin._format_timestamp_elapsed(last_ts),
                    "duration": self._format_duration(duration_seconds),
                    "heat": heat,
                    "status": status,
                    "bot_joined": bool(raw.get("bot_joined")),
                }
            )
        return items

    @staticmethod
    def _format_duration(seconds: float) -> str:
        seconds = max(0, int(seconds or 0))
        if seconds < 60:
            return "不到 1 分钟"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes} 分钟"
        hours = minutes // 60
        rest = minutes % 60
        return f"{hours} 小时 {rest} 分钟" if rest else f"{hours} 小时"

    def _skill_growth_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        state = data.get("skill_growth") if isinstance(data.get("skill_growth"), dict) else {}
        skills = state.get("skills") if isinstance(state.get("skills"), dict) else {}
        items: list[dict[str, Any]] = []
        for raw in skills.values():
            if not isinstance(raw, dict):
                continue
            level = self._int(raw.get("level")) or 1
            exp = self._float(raw.get("exp"))
            next_exp = self.plugin._skill_next_exp(level) if hasattr(self.plugin, "_skill_next_exp") else None
            prev_exp = {1: 0, 2: 100, 3: 260, 4: 520, 5: 900, 6: 1400}.get(level, 0)
            if next_exp:
                progress = max(0, min(100, int(((exp - prev_exp) / max(1, next_exp - prev_exp)) * 100)))
            else:
                progress = 100
            logs = raw.get("recent_logs") if isinstance(raw.get("recent_logs"), list) else []
            keywords = raw.get("keywords") if isinstance(raw.get("keywords"), list) else []
            aliases = raw.get("aliases") if isinstance(raw.get("aliases"), list) else []
            items.append(
                {
                    "id": self._single_line(raw.get("id"), 32),
                    "name": self._single_line(raw.get("name"), 32),
                    "category": self._single_line(raw.get("category"), 24),
                    "keywords": [self._single_line(item, 24) for item in keywords if self._single_line(item, 24)][:16],
                    "aliases": [self._single_line(item, 24) for item in aliases if self._single_line(item, 24)][:12],
                    "hidden": bool(raw.get("hidden")),
                    "frozen": bool(raw.get("frozen")),
                    "level": level,
                    "level_title": self.plugin._skill_level_title(level) if hasattr(self.plugin, "_skill_level_title") else self._single_line(raw.get("level_title"), 24),
                    "description": self.plugin._skill_level_description(level) if hasattr(self.plugin, "_skill_level_description") else "",
                    "exp": round(exp, 2),
                    "next_exp": next_exp,
                    "progress": progress,
                    "training_count": self._int(raw.get("training_count")),
                    "last_trained": self.plugin._format_timestamp_elapsed(raw.get("last_trained_ts", 0)),
                    "recent_logs": [
                        {
                            "activity": self._single_line(log.get("activity"), 80),
                            "exp": self._float(log.get("exp")),
                            "time": self.plugin._format_timestamp_elapsed(log.get("ts", 0)),
                            "level_up": bool(log.get("level_up")),
                        }
                        for log in logs[-4:]
                        if isinstance(log, dict)
                    ],
                }
            )
        items.sort(key=lambda item: (bool(item.get("hidden")), -item["level"], -item["exp"], -item["training_count"], item.get("category") or "", item.get("name") or ""))
        return {
            "enabled": bool(getattr(self.plugin, "enable_skill_growth_simulation", False)),
            "rate": float(getattr(self.plugin, "skill_growth_rate", 1.0) or 1.0),
            "passive_injection": bool(getattr(self.plugin, "enable_skill_growth_passive_injection", False)),
            "schedule_influence": bool(getattr(self.plugin, "enable_skill_growth_schedule_influence", False)),
            "schedule_influence_strength": float(getattr(self.plugin, "skill_growth_schedule_influence_strength", 0.35) or 0.0),
            "updated": self.plugin._format_timestamp_elapsed(state.get("updated_ts", 0)),
            "skill_count": len(items),
            "hidden_count": sum(1 for item in items if item.get("hidden")),
            "frozen_count": sum(1 for item in items if item.get("frozen")),
            "items": items[:120],
        }

    def _food_menu_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        state = data.get("food_menu") if isinstance(data.get("food_menu"), dict) else {}
        raw_items = state.get("items") if isinstance(state.get("items"), list) else []
        type_label = getattr(self.plugin, "_food_menu_type_label", None)
        time_label = getattr(self.plugin, "_food_menu_time_label", None)

        def _list(value: Any, *, limit: int = 12, item_limit: int = 24) -> list[str]:
            raw = value if isinstance(value, list) else re.split(r"[,，、\n/|]+", str(value or ""))
            items: list[str] = []
            for part in raw:
                item = self._single_line(part, item_limit)
                if item and item not in items:
                    items.append(item)
            return items[:limit]

        items: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            name = self._single_line(raw.get("name"), 40)
            if not name:
                continue
            item_type = self._single_line(raw.get("type"), 20) or "dish"
            counts[item_type] = counts.get(item_type, 0) + 1
            times = _list(raw.get("times"), limit=5, item_limit=16)
            items.append(
                {
                    "id": self._single_line(raw.get("id"), 48),
                    "name": name,
                    "type": item_type,
                    "type_label": type_label(item_type) if callable(type_label) else item_type,
                    "category": self._single_line(raw.get("category"), 24),
                    "tags": _list(raw.get("tags"), limit=10, item_limit=16),
                    "times": times,
                    "time_labels": [time_label(value) if callable(time_label) else value for value in times],
                    "avoid": _list(raw.get("avoid"), limit=8, item_limit=24),
                    "aliases": _list(raw.get("aliases"), limit=10, item_limit=24),
                    "note": self._single_line(raw.get("note"), 100),
                    "favorite": bool(raw.get("favorite")),
                    "hidden": bool(raw.get("hidden")),
                    "use_count": self._int(raw.get("use_count")),
                    "last_used": self.plugin._format_timestamp_elapsed(raw.get("last_used_at", 0)),
                    "last_recommended": self.plugin._format_timestamp_elapsed(raw.get("last_recommended_at", 0)),
                    "updated": self.plugin._format_timestamp_elapsed(raw.get("updated_ts", 0)),
                }
            )
        items.sort(key=lambda item: (bool(item.get("hidden")), not bool(item.get("favorite")), item.get("type_label", ""), item.get("name", "")))
        return {
            "items": items[:160],
            "total": len(items),
            "visible_count": sum(1 for item in items if not item.get("hidden")),
            "favorite_count": sum(1 for item in items if item.get("favorite")),
            "hidden_count": sum(1 for item in items if item.get("hidden")),
            "counts": counts,
            "updated": self.plugin._format_timestamp_elapsed(state.get("updated_ts", 0)),
        }

    def _external_ability_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        runtime_getter = getattr(self.plugin, "external_proactive_abilities", None)
        if callable(runtime_getter):
            raw_items = runtime_getter()
        else:
            store = data.get("external_proactive_abilities") if isinstance(data.get("external_proactive_abilities"), dict) else {}
            raw_items = list(store.values()) if isinstance(store, dict) else []
        items: list[dict[str, Any]] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            config = raw.get("config") if isinstance(raw.get("config"), dict) else {}
            schema = raw.get("config_schema") if isinstance(raw.get("config_schema"), dict) else {}
            items.append(
                {
                    "name": self._single_line(raw.get("name"), 64),
                    "module": self._single_line(raw.get("module"), 24) or "外部主动能力",
                    "label": self._single_line(raw.get("label"), 32) or self._single_line(raw.get("name"), 64),
                    "description": self._single_line(raw.get("description"), 180),
                    "when": self._single_line(raw.get("when"), 140),
                    "use_for": self._single_line(raw.get("use_for"), 140),
                    "avoid": self._single_line(raw.get("avoid"), 140),
                    "enabled": bool(raw.get("enabled")),
                    "available": bool(raw.get("available")),
                    "registered": bool(raw.get("registered")),
                    "share_probability": max(0.0, min(1.0, self._float(raw.get("share_probability")))),
                    "min_interval_hours": max(0.0, self._float(raw.get("min_interval_hours"))),
                    "config": config,
                    "config_schema": schema,
                    "last_executed": self.plugin._format_timestamp_elapsed(raw.get("last_executed_ts", 0)),
                    "last_status": self._single_line(raw.get("last_status"), 160),
                    "last_summary": self._single_line(raw.get("last_summary"), 160),
                    "success_count": self._int(raw.get("success_count")),
                    "failure_count": self._int(raw.get("failure_count")),
                    "updated": self.plugin._format_timestamp_elapsed(raw.get("updated_ts", 0)),
                }
            )
        items.sort(key=lambda item: (not item["enabled"], not item["available"], item["module"], item["label"]))
        return {
            "total": len(items),
            "enabled_count": sum(1 for item in items if item["enabled"]),
            "available_count": sum(1 for item in items if item["available"]),
            "items": items,
        }

    def _feature_flags(self) -> dict[str, bool]:
        keys = [
            "enable_proactive_only_mode",
            "enable_mai_style_integration",
            "enable_companion_memory",
            "enable_expression_learning",
            "enable_intent_emotion_analysis",
            "enable_response_self_review",
            "enable_smart_silence",
            "enable_llm_timer_scheduling",
            "enable_llm_proactive_message",
            "enable_llm_proactive_persona_judge",
            "enable_passive_topic_suppression",
            "enable_relationship_state_machine",
            "enable_emotion_simulation",
            "enable_dialogue_episode_memory",
            "enable_open_loop_tracking",
            "enable_user_habit_learning",
            "enable_food_menu_recommendation",
            "enable_humanized_states",
            "enable_health_state",
            "enable_hunger_state",
            "enable_segmented_proactive_reply",
            "enable_proactive_quote_trigger_message",
            "enable_quote_group_reply",
            "enable_quote_group_interjection",
            "enable_quote_private_proactive",
            "enable_photo_text_action",
            "inject_passive_states",
            "enable_passive_state_delta_injection",
            "enable_cycle_state",
            "enable_skill_growth_simulation",
            "enable_skill_growth_passive_injection",
            "enable_message_debounce",
            "enable_smart_message_debounce",
            "enable_recall_enhancement",
            "enable_recall_cancel_reply",
            "enable_recall_message_cache",
            "enable_recall_transcribe_command",
            "enable_forbidden_word_recall",
            "enable_semantic_message_debounce",
            "enable_environment_perception",
            "enable_holiday_perception",
            "enable_platform_perception",
            "enable_model_perception",
            "enable_worldview_perception",
            "enable_lunar_perception",
            "enable_solar_term_perception",
            "enable_almanac_perception",
            "enable_group_companion",
            "enable_group_slang_learning",
            "enable_group_member_profiles",
            "enable_group_context_injection",
            "enable_group_injection_guard",
            "enable_group_persona_denoise",
            "enable_forward_message_adaptation",
            "enable_group_scene_awareness",
            "enable_group_reality_promise_guard",
            "enable_group_wakeup_enhancement",
            "enable_group_wakeup_question",
            "enable_group_wakeup_cold_group",
            "enable_group_high_intensity_mode",
            "enable_private_image_self_recognition",
            "enable_backup_external_image_api",
            "enable_private_image_gif_enhancement",
            "enable_group_conversation_followup",
            "enable_group_interjection",
            "enable_group_repeat_follow",
            "group_repeat_count_distinct_users_only",
            "enable_group_topic_threads",
            "enable_group_episode_memory",
            "enable_group_interjection_feedback",
            "enable_group_slang_meanings",
            "enable_group_slang_web_search",
            "enable_group_relationship_graph",
            "enable_group_privacy_guard",
            "enable_worldbook_member_recognition",
            "enable_cross_user_memory_bridge",
            "enable_atrelay_tools",
            "enable_livingmemory_integration",
            "enable_bilibili_integration",
            "enable_bilibili_boredom_watch",
            "enable_news_integration",
            "enable_news_daily_hot_read",
            "enable_news_boredom_read",
            "enable_ai_daily_watch",
            "enable_external_event_self_link",
            "enable_web_exploration",
            "enable_web_exploration_boredom_search",
            "enable_qzone_integration",
            "enable_qzone_life_publish",
            "enable_qzone_generated_image_publish",
            "enable_qzone_comment_inbox",
            "enable_qzone_emotional_vent_publish",
            "enable_private_reading_integration",
            "enable_private_reading_boredom_read",
            "enable_private_reading_ask_recommendation",
            "enable_private_reading_preference_influence",
            "enable_unanswered_screen_peek_followup",
            "enable_yesterday_screen_diary_context",
            "enable_tts_enhancement",
            "enable_creative_writing",
            "creative_hidden_mode",
        ]
        values = {key: bool(getattr(self.plugin, key, False)) for key in keys}
        try:
            livingmemory_available = bool(getattr(self.plugin, "_livingmemory_available", lambda: False)())
        except Exception:
            livingmemory_available = False
        try:
            bilibili_available = bool(getattr(self.plugin, "_bilibili_available", lambda: False)())
        except Exception:
            bilibili_available = False
        try:
            screen_companion_available = bool(self._screen_companion_available())
        except Exception:
            screen_companion_available = False
        try:
            private_reading_available = bool(getattr(self.plugin, "_jm_cosmos_available", lambda: False)())
        except Exception:
            private_reading_available = False
        values["enable_livingmemory_integration"] = bool(livingmemory_available and getattr(self.plugin, "enable_livingmemory_integration", False))
        values["enable_bilibili_integration"] = bool(bilibili_available and getattr(self.plugin, "enable_bilibili_integration", False))
        values["enable_bilibili_boredom_watch"] = bool(bilibili_available and getattr(self.plugin, "enable_bilibili_boredom_watch", False))
        values["enable_qzone_integration"] = bool(getattr(self.plugin, "enable_qzone_integration", False))
        values["enable_qzone_life_publish"] = bool(getattr(self.plugin, "enable_qzone_life_publish", False))
        values["enable_qzone_generated_image_publish"] = bool(getattr(self.plugin, "enable_qzone_generated_image_publish", False))
        values["enable_qzone_comment_inbox"] = bool(getattr(self.plugin, "enable_qzone_comment_inbox", False))
        values["enable_qzone_emotional_vent_publish"] = bool(getattr(self.plugin, "enable_qzone_emotional_vent_publish", False))
        values["enable_yesterday_screen_diary_context"] = bool(screen_companion_available and getattr(self.plugin, "enable_yesterday_screen_diary_context", False))
        values["enable_private_reading_integration"] = bool(private_reading_available and getattr(self.plugin, "enable_jm_cosmos_integration", False))
        values["enable_private_reading_boredom_read"] = bool(private_reading_available and getattr(self.plugin, "enable_jm_cosmos_boredom_read", False))
        values["enable_private_reading_ask_recommendation"] = bool(private_reading_available and getattr(self.plugin, "enable_private_reading_ask_recommendation", False))
        values["enable_private_reading_preference_influence"] = bool(private_reading_available and getattr(self.plugin, "enable_private_reading_preference_influence", True))
        return values

    def _proactive_only_mode_snapshot(self) -> dict[str, Any]:
        clearer = getattr(self.plugin, "_clear_proactive_only_temp_unlocks_if_mode_off", None)
        if callable(clearer):
            clearer()
        unlocks_getter = getattr(self.plugin, "_proactive_only_unlock_store", None)
        label_getter = getattr(self.plugin, "_proactive_only_unlock_label", None)
        related_getter = getattr(self.plugin, "_related_proactive_only_unlock_keys", None)
        unlocks = sorted(unlocks_getter() if callable(unlocks_getter) else [])

        def label(key: str) -> str:
            return label_getter(key) if callable(label_getter) else key

        related: dict[str, list[dict[str, str]]] = {}
        locked_keys = [
            "inject_passive_states",
            "enable_intent_emotion_analysis",
            "enable_llm_timer_scheduling",
            "enable_passive_topic_suppression",
            "enable_environment_perception",
            "enable_message_debounce",
            "enable_recall_enhancement",
            "enable_private_image_self_recognition",
            "enable_forward_message_adaptation",
            "enable_group_companion",
            "enable_skill_growth_passive_injection",
            "enable_food_menu_recommendation",
            "enable_private_reading_preference_influence",
            "enable_worldbook_member_recognition",
            "enable_atrelay_tools",
            "enable_livingmemory_integration",
            "enable_tts_enhancement",
            "enable_segmented_proactive_reply",
        ]
        for key in locked_keys:
            keys = related_getter(key) if callable(related_getter) else []
            related[key] = [{"key": item, "label": label(item)} for item in keys]
        return {
            "enabled": bool(getattr(self.plugin, "enable_proactive_only_mode", False)),
            "unlocked": [{"key": key, "label": label(key)} for key in unlocks],
            "related": related,
        }

    def _provider_settings(self) -> dict[str, str]:
        keys = [
            "FAST_RESPONSE_PROVIDER_ID",
            "COMPLEX_REASONING_PROVIDER_ID",
            "CREATIVE_MODEL_PROVIDER_ID",
            "LLM_PROVIDER_ID",
            "MAI_STYLE_PROVIDER_ID",
            "DAILY_PLAN_PROVIDER_ID",
            "DETAIL_ENHANCEMENT_PROVIDER_ID",
            "DREAM_DIARY_PROVIDER_ID",
            "CREATIVE_PROVIDER_ID",
            "CREATIVE_OUTLINE_PROVIDER_ID",
            "CREATIVE_REVIEW_PROVIDER_ID",
            "VOICE_PROMPT_PROVIDER_ID",
            "tts_conversion_provider_id",
            "PHOTO_PROMPT_PROVIDER_ID",
            "NARRATION_PROVIDER_ID",
            "HISTORY_SUMMARY_PROVIDER_ID",
            "RESPONSE_REVIEW_PROVIDER_ID",
            "SMART_SILENCE_PROVIDER_ID",
            "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID",
            "TROUBLESHOOTING_PROVIDER_ID",
            "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID",
            "REST_WAKEUP_PROVIDER_ID",
            "RELATIONSHIP_ANALYSIS_PROVIDER_ID",
            "EMOTION_JUDGEMENT_PROVIDER_ID",
            "COMPANION_MEMORY_PROVIDER_ID",
            "DIALOGUE_EPISODE_PROVIDER_ID",
            "GROUP_INTERJECT_PROVIDER_ID",
            "GROUP_EPISODE_PROVIDER_ID",
            "GROUP_SLANG_PROVIDER_ID",
            "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID",
            "FORWARD_MESSAGE_PROVIDER_ID",
            "PLUGIN_VISION_PROVIDER_ID",
            "PRIVATE_READING_VISION_PROVIDER_ID",
            "NEWS_PROVIDER_ID",
            "WEB_EXPLORATION_PROVIDER_ID",
        ]
        for key in sorted(self._schema_provider_keys(public_only=True)):
            if key not in keys:
                keys.append(key)
        values = {key: self._config_get(key) for key in keys}
        if not values.get("FAST_RESPONSE_PROVIDER_ID"):
            values["FAST_RESPONSE_PROVIDER_ID"] = str(getattr(self.plugin, "fast_response_provider_id", "") or "")
        if not values.get("COMPLEX_REASONING_PROVIDER_ID"):
            values["COMPLEX_REASONING_PROVIDER_ID"] = str(getattr(self.plugin, "complex_reasoning_provider_id", "") or "")
        if not values.get("CREATIVE_MODEL_PROVIDER_ID"):
            values["CREATIVE_MODEL_PROVIDER_ID"] = str(getattr(self.plugin, "creative_model_provider_id", "") or "")
        if not values.get("PLUGIN_VISION_PROVIDER_ID"):
            values["PLUGIN_VISION_PROVIDER_ID"] = str(getattr(self.plugin, "plugin_vision_provider_id", "") or "")
        if not values.get("PRIVATE_READING_VISION_PROVIDER_ID"):
            values["PRIVATE_READING_VISION_PROVIDER_ID"] = str(getattr(self.plugin, "jm_cosmos_vision_provider_id", "") or "")
        if not values.get("DREAM_DIARY_PROVIDER_ID"):
            values["DREAM_DIARY_PROVIDER_ID"] = str(getattr(self.plugin, "dream_diary_provider_id", "") or "")
        if not values.get("SMART_MESSAGE_DEBOUNCE_PROVIDER_ID"):
            values["SMART_MESSAGE_DEBOUNCE_PROVIDER_ID"] = str(getattr(self.plugin, "smart_message_debounce_provider_id", "") or "")
        if not values.get("SMART_SILENCE_PROVIDER_ID"):
            values["SMART_SILENCE_PROVIDER_ID"] = str(getattr(self.plugin, "smart_silence_provider_id", "") or "")
        if not values.get("REST_WAKEUP_PROVIDER_ID"):
            values["REST_WAKEUP_PROVIDER_ID"] = str(getattr(self.plugin, "rest_wakeup_provider_id", "") or "")
        if not values.get("PROACTIVE_PERSONA_JUDGE_PROVIDER_ID"):
            values["PROACTIVE_PERSONA_JUDGE_PROVIDER_ID"] = str(getattr(self.plugin, "proactive_persona_judge_provider_id", "") or "")
        if not values.get("tts_conversion_provider_id"):
            values["tts_conversion_provider_id"] = str(getattr(self.plugin, "tts_conversion_provider_id", "") or "")
        return values

    @staticmethod
    def _normalize_provider_mode_value(value: Any) -> str:
        text = str(value or "").strip().lower()
        return "precision" if text in {"precision", "precise", "advanced", "精准", "精准配置", "分流"} else "quick"

    def _precision_bundle_from_quick(self, values: dict[str, str]) -> dict[str, str]:
        fast = self._single_line(values.get("FAST_RESPONSE_PROVIDER_ID"), 160)
        complex_model = self._single_line(values.get("COMPLEX_REASONING_PROVIDER_ID") or values.get("LLM_PROVIDER_ID"), 160)
        creative = self._single_line(values.get("CREATIVE_MODEL_PROVIDER_ID"), 160)
        plugin_vision = self._single_line(values.get("PLUGIN_VISION_PROVIDER_ID"), 160)
        return {
            "LLM_PROVIDER_ID": complex_model,
            "MAI_STYLE_PROVIDER_ID": fast or complex_model,
            "DAILY_PLAN_PROVIDER_ID": complex_model,
            "DETAIL_ENHANCEMENT_PROVIDER_ID": complex_model,
            "HISTORY_SUMMARY_PROVIDER_ID": complex_model,
            "RELATIONSHIP_ANALYSIS_PROVIDER_ID": complex_model,
            "COMPANION_MEMORY_PROVIDER_ID": complex_model,
            "DIALOGUE_EPISODE_PROVIDER_ID": complex_model,
            "GROUP_EPISODE_PROVIDER_ID": complex_model,
            "FORWARD_MESSAGE_PROVIDER_ID": complex_model,
            "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID": complex_model,
            "RESPONSE_REVIEW_PROVIDER_ID": fast or complex_model,
            "SMART_SILENCE_PROVIDER_ID": fast or complex_model,
            "TROUBLESHOOTING_PROVIDER_ID": fast or complex_model,
            "EMOTION_JUDGEMENT_PROVIDER_ID": fast or complex_model,
            "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID": fast or complex_model,
            "REST_WAKEUP_PROVIDER_ID": fast or complex_model,
            "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID": fast,
            "GROUP_INTERJECT_PROVIDER_ID": fast or complex_model,
            "GROUP_SLANG_PROVIDER_ID": fast or complex_model,
            "VOICE_PROMPT_PROVIDER_ID": fast or complex_model,
            "tts_conversion_provider_id": fast or complex_model,
            "NARRATION_PROVIDER_ID": fast or complex_model,
            "NEWS_PROVIDER_ID": fast or complex_model,
            "WEB_EXPLORATION_PROVIDER_ID": fast or complex_model,
            "CREATIVE_PROVIDER_ID": creative or complex_model,
            "CREATIVE_OUTLINE_PROVIDER_ID": creative or complex_model,
            "CREATIVE_REVIEW_PROVIDER_ID": creative or complex_model,
            "DREAM_DIARY_PROVIDER_ID": creative or complex_model,
            "PHOTO_PROMPT_PROVIDER_ID": creative or complex_model,
            "PRIVATE_READING_VISION_PROVIDER_ID": plugin_vision,
        }

    def _quick_bundle_from_precision(self, values: dict[str, str]) -> dict[str, str]:
        fast = self._single_line(
            values.get("FAST_RESPONSE_PROVIDER_ID")
            or values.get("RESPONSE_REVIEW_PROVIDER_ID")
            or values.get("SMART_MESSAGE_DEBOUNCE_PROVIDER_ID")
            or values.get("SMART_SILENCE_PROVIDER_ID")
            or values.get("MAI_STYLE_PROVIDER_ID"),
            160,
        )
        complex_model = self._single_line(
            values.get("COMPLEX_REASONING_PROVIDER_ID")
            or values.get("LLM_PROVIDER_ID")
            or values.get("DAILY_PLAN_PROVIDER_ID")
            or values.get("COMPANION_MEMORY_PROVIDER_ID")
            or values.get("MAI_STYLE_PROVIDER_ID"),
            160,
        )
        creative = self._single_line(
            values.get("CREATIVE_MODEL_PROVIDER_ID")
            or values.get("CREATIVE_PROVIDER_ID")
            or values.get("DREAM_DIARY_PROVIDER_ID")
            or values.get("PHOTO_PROMPT_PROVIDER_ID")
            or complex_model,
            160,
        )
        plugin_vision = self._single_line(
            values.get("PLUGIN_VISION_PROVIDER_ID")
            or values.get("PRIVATE_READING_VISION_PROVIDER_ID")
            or values.get("NARRATION_PROVIDER_ID"),
            160,
        )
        return {
            "FAST_RESPONSE_PROVIDER_ID": fast,
            "COMPLEX_REASONING_PROVIDER_ID": complex_model,
            "CREATIVE_MODEL_PROVIDER_ID": creative,
            "PLUGIN_VISION_PROVIDER_ID": plugin_vision,
        }

    def _expand_provider_overwrite_bundle(self, mode: str, values: dict[str, str]) -> dict[str, str]:
        merged = {key: self._single_line(value, 160) for key, value in self._provider_settings().items()}
        for key, value in values.items():
            if key in self._allowed_provider_keys():
                merged[key] = self._single_line(value, 160)
        if self._normalize_provider_mode_value(mode) == "quick":
            merged.update(self._precision_bundle_from_quick(merged))
        else:
            merged.update(self._quick_bundle_from_precision(merged))
        return {key: self._single_line(value, 160) for key, value in merged.items() if key in self._allowed_provider_keys()}

    def _migration_export_options_from_request(self) -> set[str]:
        raw = request.args.get("sections") or ""
        if not raw:
            return {"basic", "relations", "food_skills"}
        options = {part.strip().lower() for part in re.split(r"[,，\s]+", raw) if part.strip()}
        allowed = {"basic", "relations", "food_skills", "providers", "sensitive"}
        selected = {item for item in options if item in allowed}
        return selected or {"basic", "relations", "food_skills"}

    async def _apply_migration_normalized(self, normalized: dict[str, Any], *, mode: str, conflict: str) -> dict[str, Any]:
        before = await self._build_migration_package(include_all=True)
        backup_path = self._write_migration_backup(before)

        changed_config: dict[str, Any] = {}
        for key, value in normalized.get("features", {}).items():
            normalized_value = self._normalize_bool_value(value)
            current_value = self._normalize_bool_value(getattr(self.plugin, key, self._config_get(key)))
            if self._should_apply_migration_value(current_value, normalized_value, conflict):
                changed_config[key] = normalized_value
        for key, value in normalized.get("providers", {}).items():
            normalized_value = self._single_line(value, 160)
            current_value = self._single_line(self._provider_settings().get(key, ""), 160)
            if self._should_apply_migration_value(current_value, normalized_value, conflict):
                changed_config[key] = normalized_value
        for key, value in normalized.get("settings", {}).items():
            if key in {"storage_backend", "storage_sqlite_path"}:
                continue
            if key in {"group_whitelist_ids", "group_blacklist_ids"}:
                normalized_value = self._normalize_id_list(value)
            elif key == "group_access_mode":
                normalized_mode = str(value or "").strip().lower()
                normalized_value = normalized_mode if normalized_mode in {"whitelist", "blacklist"} else "whitelist"
            else:
                normalized_value = self._normalize_setting_value(key, value)
            current_value = self._migration_current_setting_value(key)
            if self._should_apply_migration_value(current_value, normalized_value, conflict):
                changed_config[key] = normalized_value
        for key, value in changed_config.items():
            self._apply_config_value(key, value, changed_config)
        if any(key in self._allowed_provider_keys() for key in changed_config) or "provider_config_mode" in changed_config:
            apply_quick = getattr(self.plugin, "_apply_quick_provider_defaults", None)
            if callable(apply_quick):
                apply_quick()
        config_saved = True
        if changed_config:
            config_saved = await self._save_config_if_possible()

        applied_sections: list[dict[str, Any]] = []
        data_payload = normalized.get("data") if isinstance(normalized.get("data"), dict) else {}
        if data_payload:
            async with self.plugin._data_lock:
                for section, imported_value in data_payload.items():
                    current_value = self.plugin.data.get(section)
                    if mode == "replace" or not isinstance(current_value, dict) or not isinstance(imported_value, dict):
                        self.plugin.data[section] = deepcopy(imported_value)
                    else:
                        merged = deepcopy(current_value)
                        self._deep_merge_dict(merged, imported_value, conflict=conflict)
                        self.plugin.data[section] = merged
                    applied_sections.append(
                        {
                            "key": section,
                            "label": self._migration_section_label(section),
                            "count": self._migration_count_items(imported_value),
                        }
                    )
                self.plugin._save_data_sync()
            self._refresh_migration_runtime_caches(data_payload)

        overview = await self.get_overview()
        data = overview.get("data") if isinstance(overview.get("data"), dict) else {}
        checks = await self._migration_post_import_checks(config_saved=config_saved)
        data["message"] = "配置已导入。"
        data["mode"] = mode
        data["conflict"] = conflict
        data["backup_path"] = backup_path
        data["config_saved"] = config_saved
        data["changed_config_count"] = len(changed_config)
        data["applied_sections"] = applied_sections
        data["post_import_checks"] = checks
        data["migration_backups"] = self._list_migration_backup_items(limit=8)
        overview["data"] = data
        return overview

    async def _build_migration_package(self, options: set[str] | None = None, *, include_all: bool = False) -> dict[str, Any]:
        selected = {"basic", "relations", "food_skills", "providers", "sensitive"} if include_all else (options or {"basic", "relations", "food_skills"})
        async with self.plugin._data_lock:
            raw_data = deepcopy(self.plugin.data)
        package = {
            "kind": "private_companion_config_backup",
            "plugin": PLUGIN_NAME,
            "schema": 1,
            "version": self._plugin_version(),
            "exported_at": int(time.time()),
            "included_sections": sorted(selected),
            "settings": self._migration_settings_snapshot(selected),
            "features": self._migration_feature_snapshot(selected),
            "data": self._migration_data_snapshot(raw_data, selected),
            "excluded": [
                "Token 消耗统计",
                "图片/视觉摘要缓存",
                "最近消息和输入状态",
                "主动消息审计与冷却队列",
                "临时任务、排障记录和运行时缓存",
                "本机存储后端与 SQLite 路径",
            ],
        }
        if "providers" in selected:
            package["providers"] = self._migration_provider_snapshot()
        package["checksum_algorithm"] = "sha256"
        package["checksum"] = self._migration_checksum(package)
        return package

    def _migration_settings_snapshot(self, selected: set[str]) -> dict[str, Any]:
        if "basic" not in selected and "sensitive" not in selected:
            return {}
        runtime = self._runtime_settings()
        allowed = self._allowed_setting_keys()
        settings = {}
        for key, value in runtime.items():
            if key in {"storage_backend", "storage_sqlite_path"}:
                continue
            if key not in allowed:
                continue
            group = self._migration_setting_group(key)
            if group == "sensitive":
                if "sensitive" in selected:
                    settings[key] = deepcopy(value)
            elif group == "providers":
                if "providers" in selected:
                    settings[key] = deepcopy(value)
            elif "basic" in selected:
                settings[key] = deepcopy(value)
        if "basic" in selected:
            settings["group_access_mode"] = str(getattr(self.plugin, "group_access_mode", "whitelist") or "whitelist")
            settings["group_whitelist_ids"] = list(getattr(self.plugin, "group_whitelist_ids", []) or [])
            settings["group_blacklist_ids"] = list(getattr(self.plugin, "group_blacklist_ids", []) or [])
        if "sensitive" in selected:
            qzone_cookie = self._config_get("QZONE_COOKIE") or str(getattr(self.plugin, "qzone_cookie", "") or "")
            if qzone_cookie:
                settings["QZONE_COOKIE"] = qzone_cookie
            search_api_key = self._config_get("WEB_EXPLORATION_API_KEY") or str(getattr(self.plugin, "web_exploration_api_key", "") or "")
            if search_api_key:
                settings["WEB_EXPLORATION_API_KEY"] = search_api_key
        return settings

    def _migration_feature_snapshot(self, selected: set[str]) -> dict[str, bool]:
        if "basic" not in selected and "sensitive" not in selected:
            return {}
        features: dict[str, bool] = {}
        for key in sorted(self._allowed_feature_keys()):
            group = self._migration_setting_group(key)
            if group == "sensitive" and "sensitive" not in selected:
                continue
            if group == "providers" and "providers" not in selected:
                continue
            if group not in {"sensitive", "providers"} and "basic" not in selected:
                continue
            if hasattr(self.plugin, key):
                features[key] = self._normalize_bool_value(getattr(self.plugin, key))
                continue
            raw = self._config_get(key)
            if raw != "":
                features[key] = self._normalize_bool_value(raw)
        return features

    def _migration_provider_snapshot(self) -> dict[str, str]:
        allowed = self._allowed_provider_keys()
        return {key: value for key, value in self._provider_settings().items() if key in allowed}

    def _migration_data_snapshot(self, raw_data: dict[str, Any], selected: set[str]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for section in self._migration_data_sections(selected):
            if section not in raw_data:
                continue
            value = deepcopy(raw_data.get(section))
            if section in {"users", "groups"}:
                value = self._strip_runtime_data(value)
            if self._migration_count_items(value) > 0:
                result[section] = value
        return result

    @classmethod
    def _migration_data_sections(cls, selected: set[str] | None = None) -> tuple[str, ...]:
        selected = selected or {"basic", "relations", "food_skills"}
        sections: list[str] = []
        if "relations" in selected:
            sections.extend(
                [
                    "users",
                    "groups",
                    "worldbook_entries",
                    "worldbook_member_profiles",
                    "worldbook_group_profiles",
                ]
            )
        if "food_skills" in selected:
            sections.extend(["skill_growth", "food_menu", "external_proactive_abilities", "important_dates", "can_do"])
        if "sensitive" in selected:
            sections.extend(["jm_cosmos_integration", "bookshelf_items"])
        return tuple(sections)

    @staticmethod
    def _migration_setting_group(key: str) -> str:
        text = str(key)
        if text in {"storage_backend", "storage_sqlite_path"}:
            return "environment"
        if text == "provider_config_mode":
            return "providers"
        if text == "QZONE_COOKIE":
            return "sensitive"
        if text == "WEB_EXPLORATION_API_KEY":
            return "sensitive"
        if text.startswith("private_reading_") or text.startswith("enable_private_reading_"):
            return "sensitive"
        if text in {"PRIVATE_READING_VISION_PROVIDER_ID"}:
            return "sensitive"
        if text.endswith("_PROVIDER_ID") or text in {"LLM_PROVIDER_ID", "tts_conversion_provider_id"}:
            return "providers"
        return "basic"

    @staticmethod
    def _migration_section_label(key: str) -> str:
        return {
            "users": "私聊对象资料",
            "groups": "群聊观测资料",
            "worldbook_entries": "关系网原始条目",
            "worldbook_member_profiles": "关系网成员资料",
            "worldbook_group_profiles": "关系网群资料",
            "skill_growth": "技能熟练度",
            "food_menu": "吃什么候选菜单",
            "external_proactive_abilities": "外部主动能力",
            "important_dates": "重要日期",
            "can_do": "自定义能力",
            "jm_cosmos_integration": "私密阅读状态",
            "bookshelf_items": "书柜阅读记录",
        }.get(key, key)

    def _extract_migration_package(self, payload: Any, *, allow_checksum_mismatch: bool = False) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("导入内容必须是 JSON 对象")
        package = self._unwrap_migration_package_payload(payload)
        if not isinstance(package, dict):
            raise ValueError("没有读取到可导入的配置备份")
        if package.get("kind") != "private_companion_config_backup" or package.get("plugin") != PLUGIN_NAME:
            legacy = self._legacy_snapshot_to_migration_package(package)
            if legacy is None:
                raise ValueError("这不是 Private Companion 的配置备份")
            return legacy
        checksum = str(package.get("checksum") or "").strip()
        if checksum and not self._migration_checksum_matches(package, checksum):
            if not allow_checksum_mismatch:
                raise ValueError("备份校验失败：文件可能被截断或手动修改过。如确认只是手动脱敏/删改敏感字段，请勾选“校验失败仍继续预览/导入”。")
            package = deepcopy(package)
            package["_checksum_mismatch_allowed"] = True
        return package

    def _unwrap_migration_package_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = payload
        for _ in range(4):
            if not isinstance(current, dict):
                return {}
            if current.get("kind") == "private_companion_config_backup" or (
                isinstance(current.get("overview"), dict)
                and ("users" in current or "groups" in current)
            ):
                return current
            for key in ("package", "data", "payload", "result"):
                nested = current.get(key)
                if isinstance(nested, dict):
                    current = nested
                    break
            else:
                return current
        return current if isinstance(current, dict) else {}

    def _legacy_snapshot_to_migration_package(self, package: dict[str, Any]) -> dict[str, Any] | None:
        overview = package.get("overview") if isinstance(package.get("overview"), dict) else {}
        has_snapshot_shape = bool(overview) and ("users" in package or "groups" in package)
        if not has_snapshot_shape:
            return None

        settings: dict[str, Any] = {}
        raw_settings = overview.get("settings") if isinstance(overview.get("settings"), dict) else {}
        for key, value in raw_settings.items():
            if key in {"storage_backend", "storage_sqlite_path"}:
                continue
            if key in self._allowed_setting_keys():
                settings[key] = deepcopy(value)
        group_overview = overview.get("group") if isinstance(overview.get("group"), dict) else {}
        if group_overview:
            settings["group_access_mode"] = str(group_overview.get("access_mode") or "whitelist")
            settings["group_whitelist_ids"] = self._normalize_id_list(group_overview.get("whitelist"))
            settings["group_blacklist_ids"] = self._normalize_id_list(group_overview.get("blacklist"))

        features: dict[str, bool] = {}
        raw_features = overview.get("features") if isinstance(overview.get("features"), dict) else {}
        for key, value in raw_features.items():
            if key in self._allowed_feature_keys():
                features[key] = self._normalize_bool_value(value)

        providers: dict[str, str] = {}
        raw_providers = overview.get("providers") if isinstance(overview.get("providers"), dict) else {}
        for key, value in raw_providers.items():
            if key in self._allowed_provider_keys():
                providers[key] = self._single_line(value, 160)

        converted = {
            "kind": "private_companion_config_backup",
            "plugin": PLUGIN_NAME,
            "schema": 1,
            "version": str(package.get("version") or overview.get("plugin", {}).get("version") or self._plugin_version()),
            "exported_at": int(time.time()),
            "included_sections": ["basic", "relations"],
            "settings": settings,
            "features": features,
            "providers": providers,
            "data": {},
            "legacy_snapshot": True,
            "excluded": [
                "由旧版页面快照转换；仅导入快照中可识别的配置、名单、开关和模型指向",
                "旧版页面快照中的私聊/群聊列表是展示摘要，不会写回数据文件",
                "最近消息、缓存、Token、运行日志和临时队列不会导入",
            ],
        }
        converted["checksum_algorithm"] = "sha256"
        converted["checksum"] = self._migration_checksum(converted)
        return converted

    def _normalize_migration_package(self, package: dict[str, Any]) -> dict[str, Any]:
        settings: dict[str, Any] = {}
        features: dict[str, bool] = {}
        providers: dict[str, str] = {}
        ignored: list[str] = []

        raw_settings = package.get("settings") if isinstance(package.get("settings"), dict) else {}
        for key, value in raw_settings.items():
            if key in {"storage_backend", "storage_sqlite_path"}:
                ignored.append(str(key))
                continue
            if key == "group_access_mode":
                mode = str(value or "").strip().lower()
                if mode in {"whitelist", "blacklist"}:
                    settings[key] = mode
                continue
            if key in {"group_whitelist_ids", "group_blacklist_ids"}:
                settings[key] = self._normalize_id_list(value)
                continue
            if key in self._allowed_setting_keys():
                settings[key] = self._normalize_setting_value(key, value)
            else:
                ignored.append(str(key))

        raw_features = package.get("features") if isinstance(package.get("features"), dict) else {}
        for key, value in raw_features.items():
            if key in self._allowed_feature_keys():
                features[key] = self._normalize_bool_value(value)
            else:
                ignored.append(str(key))

        raw_providers = package.get("providers") if isinstance(package.get("providers"), dict) else {}
        for key, value in raw_providers.items():
            if key in self._allowed_provider_keys():
                providers[key] = self._single_line(value, 160)
            else:
                ignored.append(str(key))

        raw_data = package.get("data") if isinstance(package.get("data"), dict) else {}
        data: dict[str, Any] = {}
        for section in self._migration_data_sections({"relations", "food_skills", "sensitive"}):
            if section not in raw_data:
                continue
            value = deepcopy(raw_data.get(section))
            if section in {"users", "groups"}:
                value = self._strip_runtime_data(value)
            data[section] = value
        for section in raw_data:
            if section not in data:
                ignored.append(str(section))

        return {
            "version": package.get("version"),
            "exported_at": package.get("exported_at"),
            "included_sections": [str(item) for item in package.get("included_sections", []) if str(item).strip()],
            "checksum": str(package.get("checksum") or ""),
            "checksum_ok": bool(package.get("checksum")) and self._migration_checksum_matches(package, str(package.get("checksum") or "")),
            "checksum_bypassed": bool(package.get("_checksum_mismatch_allowed")),
            "legacy_snapshot": bool(package.get("legacy_snapshot")),
            "settings": settings,
            "features": features,
            "providers": providers,
            "data": data,
            "ignored": sorted(set(ignored))[:80],
        }

    async def _migration_import_summary(self, normalized: dict[str, Any]) -> dict[str, Any]:
        async with self.plugin._data_lock:
            current_data = deepcopy(self.plugin.data)
        config_diff = self._migration_config_diff(normalized)
        config_count = len(normalized.get("settings", {})) + len(normalized.get("features", {})) + len(normalized.get("providers", {}))
        compatibility = self._migration_compatibility(normalized.get("version"))
        sections: list[dict[str, Any]] = []
        for key, value in (normalized.get("data") or {}).items():
            current_value = current_data.get(key)
            diff = self._migration_diff_counts(current_value, value)
            sections.append(
                {
                    "key": key,
                    "label": self._migration_section_label(key),
                    "count": self._migration_count_items(value),
                    "current_count": self._migration_count_items(current_value),
                    **diff,
                }
            )
        return {
            "version": normalized.get("version") or "",
            "current_version": self._plugin_version(),
            "compatibility": compatibility,
            "exported_at": normalized.get("exported_at") or 0,
            "included_sections": normalized.get("included_sections", []),
            "checksum": normalized.get("checksum") or "",
            "checksum_ok": bool(normalized.get("checksum_ok")),
            "checksum_bypassed": bool(normalized.get("checksum_bypassed")),
            "legacy_snapshot": bool(normalized.get("legacy_snapshot")),
            "config_count": config_count,
            "config_diff": config_diff,
            "settings_count": len(normalized.get("settings", {})),
            "features_count": len(normalized.get("features", {})),
            "providers_count": len(normalized.get("providers", {})),
            "sections": sections,
            "ignored": normalized.get("ignored", []),
            "excluded": [
                "不会导入 Token 统计、缓存、最近消息、审计日志和临时队列。",
                "导入前会自动保存一份当前可迁移配置备份。",
            ],
        }

    def _write_migration_backup(self, package: dict[str, Any]) -> str:
        data_dir = Path(getattr(self.plugin, "data_dir", "") or Path(getattr(self.plugin, "data_file", ".")).parent)
        backup_dir = data_dir / "config_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        path = backup_dir / f"private_companion_before_import_{stamp}_{secrets.token_hex(3)}.json"
        if not package.get("checksum"):
            package["checksum_algorithm"] = "sha256"
            package["checksum"] = self._migration_checksum(package)
        path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)

    def _migration_backup_dir(self) -> Path:
        data_dir = Path(getattr(self.plugin, "data_dir", "") or Path(getattr(self.plugin, "data_file", ".")).parent)
        return data_dir / "config_backups"

    def _resolve_migration_backup_path(self, backup_id: str) -> Path:
        safe_name = Path(str(backup_id or "")).name
        if not safe_name or not safe_name.endswith(".json"):
            raise ValueError("没有找到要恢复的备份文件")
        path = (self._migration_backup_dir() / safe_name).resolve()
        root = self._migration_backup_dir().resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError("备份路径不合法") from exc
        if not path.exists() or not path.is_file():
            raise ValueError("备份文件不存在")
        return path

    def _list_migration_backup_items(self, *, limit: int = 8) -> list[dict[str, Any]]:
        backup_dir = self._migration_backup_dir()
        if not backup_dir.exists():
            return []
        items: list[dict[str, Any]] = []
        for path in sorted(backup_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[: max(1, limit)]:
            stat = path.stat()
            item: dict[str, Any] = {
                "id": path.name,
                "name": path.name,
                "size": stat.st_size,
                "mtime": int(stat.st_mtime),
                "version": "",
                "exported_at": 0,
                "checksum_ok": False,
            }
            try:
                package = json.loads(path.read_text(encoding="utf-8-sig"))
                item["version"] = str(package.get("version") or "")
                item["exported_at"] = int(float(package.get("exported_at") or 0))
                item["included_sections"] = [str(value) for value in package.get("included_sections", []) if str(value).strip()]
                checksum = str(package.get("checksum") or "")
                item["checksum_ok"] = bool(checksum) and self._migration_checksum_matches(package, checksum)
            except Exception as exc:
                item["error"] = self._single_line(exc, 120)
            items.append(item)
        return items

    def _migration_checksum(self, package: dict[str, Any]) -> str:
        payload = deepcopy(package)
        payload.pop("checksum", None)
        payload.pop("checksum_algorithm", None)
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _migration_checksum_digest(payload: dict[str, Any], *, sort_keys: bool, compact: bool) -> str:
        separators = (",", ":") if compact else None
        text = json.dumps(payload, ensure_ascii=False, sort_keys=sort_keys, separators=separators)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _migration_checksum_candidates(self, package: dict[str, Any]) -> set[str]:
        candidates: set[str] = set()
        for remove_algorithm in (True, False):
            payload = deepcopy(package)
            payload.pop("checksum", None)
            if remove_algorithm:
                payload.pop("checksum_algorithm", None)
            for sort_keys in (True, False):
                for compact in (True, False):
                    try:
                        candidates.add(
                            self._migration_checksum_digest(
                                payload,
                                sort_keys=sort_keys,
                                compact=compact,
                            )
                        )
                    except Exception:
                        continue
        return candidates

    def _migration_checksum_matches(self, package: dict[str, Any], expected: str) -> bool:
        checksum = str(expected or "").strip().lower()
        if not checksum:
            return False
        return checksum in self._migration_checksum_candidates(package)

    def _plugin_version(self) -> str:
        for source in (self.plugin, getattr(self.plugin, "metadata", None)):
            for attr in ("version", "__version__", "plugin_version"):
                value = getattr(source, attr, None)
                if value:
                    return str(value).strip()
        try:
            metadata_path = Path(__file__).with_name("metadata.yaml")
            text = metadata_path.read_text(encoding="utf-8")
            match = re.search(r"(?m)^version:\s*['\"]?([^'\"\s#]+)", text)
            if match:
                return match.group(1).strip()
        except Exception:
            pass
        return "unknown"

    def _parse_migration_version(self, value: Any) -> tuple[int, int, int] | None:
        match = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", str(value or ""))
        if not match:
            return None
        return tuple(int(part or 0) for part in match.groups())

    def _migration_compatibility(self, backup_version: Any) -> dict[str, Any]:
        current_text = self._plugin_version()
        backup_text = str(backup_version or "")
        current = self._parse_migration_version(current_text)
        backup = self._parse_migration_version(backup_text)
        level = "ok"
        message = "版本接近，可以按预览结果导入。"
        if not backup or not current:
            level = "unknown"
            message = "无法判断版本跨度，建议合并导入，不建议覆盖。"
        elif backup[0] != current[0] or abs((current[1] if len(current) > 1 else 0) - (backup[1] if len(backup) > 1 else 0)) >= 2:
            level = "warn"
            message = "版本跨度较大，建议合并导入，不建议覆盖。"
        elif backup > current:
            level = "warn"
            message = "备份来自更新版本，建议合并导入，不建议覆盖。"
        return {
            "level": level,
            "backup_version": backup_text or "未知",
            "current_version": current_text,
            "message": message,
        }

    async def _migration_post_import_checks(self, *, config_saved: bool) -> list[dict[str, str]]:
        async with self.plugin._data_lock:
            data = deepcopy(self.plugin.data)
        checks: list[dict[str, str]] = []

        def add(level: str, title: str, detail: str) -> None:
            checks.append({"level": level, "title": title, "detail": detail})

        add("ok" if config_saved else "warn", "配置保存", "配置已写入并保存。" if config_saved else "运行态已写入，但配置持久化可能失败。")
        mode = str(getattr(self.plugin, "group_access_mode", "whitelist") or "whitelist")
        whitelist = list(getattr(self.plugin, "group_whitelist_ids", []) or [])
        blacklist = list(getattr(self.plugin, "group_blacklist_ids", []) or [])
        if mode == "whitelist" and not whitelist:
            add("warn", "群聊名单", "当前是白名单模式，但白名单为空；群聊能力可能不会生效。")
        else:
            add("ok", "群聊名单", f"当前为{'白名单' if mode == 'whitelist' else '黑名单'}模式，白名单 {len(whitelist)} 个，黑名单 {len(blacklist)} 个。")

        available_ids = {str(item.get("id") or "") for item in self._available_provider_items()}
        configured = {key: value for key, value in self._provider_settings().items() if str(value or "").strip()}
        missing = [value for value in configured.values() if available_ids and value not in available_ids]
        if configured and not available_ids:
            add("warn", "模型配置", f"已配置 {len(configured)} 个模型指向，但当前无法读取 AstrBot Provider 列表，暂不能校验是否存在。")
        elif missing:
            add("warn", "模型配置", f"有 {len(missing)} 个 Provider ID 当前未在 AstrBot 中找到。")
        else:
            add("ok", "模型配置", f"已配置 {len(configured)} 个模型指向，当前未发现缺失 Provider。")

        members = data.get("worldbook_member_profiles") if isinstance(data.get("worldbook_member_profiles"), dict) else {}
        groups = data.get("worldbook_group_profiles") if isinstance(data.get("worldbook_group_profiles"), dict) else {}
        if not members and not groups:
            add("warn", "关系网", "当前没有关系网成员或群资料；如果刚导入关系网备份，可能需要检查导入内容。")
        else:
            add("ok", "关系网", f"成员资料 {len(members)} 个，群资料 {len(groups)} 个。")

        food = data.get("food_menu") if isinstance(data.get("food_menu"), dict) else {}
        food_items = food.get("items") if isinstance(food.get("items"), dict) else food
        if isinstance(food_items, dict):
            add("ok" if food_items else "warn", "吃什么候选", f"候选菜单可读取，当前 {len(food_items)} 项。")
        else:
            add("warn", "吃什么候选", "候选菜单结构不是预期格式，请到功能页检查。")
        return checks

    def _strip_runtime_data(self, value: Any) -> Any:
        runtime_keys = {
            "recent_messages",
            "recent_message_ids",
            "recent_replies",
            "recent_group_messages",
            "proactive_sending",
            "proactive_audit_log",
            "proactive_candidates",
            "pending_followup_event",
            "pending_timer_events",
            "pending_atrelay_requests",
            "suspended_proactive",
            "input_status",
            "current_input_status",
            "recall_message_cache",
            "image_cache",
            "visual_summary_cache",
            "token_usage",
            "token_stats",
            "troubleshooting_records",
            "maintenance_records",
        }
        runtime_prefixes = (
            "recent_",
            "pending_",
            "last_message",
            "last_reply",
            "last_sent",
            "last_proactive",
            "cooldown_",
            "session_",
        )
        if isinstance(value, dict):
            cleaned: dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                if key_text in runtime_keys or any(key_text.startswith(prefix) for prefix in runtime_prefixes):
                    continue
                if key_text.endswith("_cache") or key_text.endswith("_audit_log"):
                    continue
                cleaned[key_text] = self._strip_runtime_data(item)
            return cleaned
        if isinstance(value, list):
            return [self._strip_runtime_data(item) for item in value]
        return value

    @staticmethod
    def _migration_count_items(value: Any) -> int:
        if isinstance(value, dict):
            return len(value)
        if isinstance(value, list):
            return len(value)
        return 1 if value not in (None, "", [], {}) else 0

    def _migration_current_setting_value(self, key: str) -> Any:
        if key == "group_access_mode":
            return str(getattr(self.plugin, "group_access_mode", "whitelist") or "whitelist")
        if key == "group_whitelist_ids":
            return list(getattr(self.plugin, "group_whitelist_ids", []) or [])
        if key == "group_blacklist_ids":
            return list(getattr(self.plugin, "group_blacklist_ids", []) or [])
        if hasattr(self.plugin, key):
            return deepcopy(getattr(self.plugin, key))
        return self._config_get(key)

    @staticmethod
    def _migration_value_empty(value: Any) -> bool:
        return value is None or value == "" or value == [] or value == {}

    def _should_apply_migration_value(self, current_value: Any, incoming_value: Any, conflict: str) -> bool:
        if current_value == incoming_value:
            return False
        if conflict == "keep_current":
            return False
        if conflict == "fill_empty":
            return self._migration_value_empty(current_value)
        return True

    def _migration_diff_counts(self, current: Any, incoming: Any) -> dict[str, int]:
        if isinstance(incoming, dict):
            current_dict = current if isinstance(current, dict) else {}
            added = 0
            overwritten = 0
            unchanged = 0
            for key, value in incoming.items():
                if key not in current_dict:
                    added += 1
                elif current_dict.get(key) == value:
                    unchanged += 1
                else:
                    overwritten += 1
            return {"added": added, "overwritten": overwritten, "unchanged": unchanged}
        if isinstance(incoming, list):
            if not isinstance(current, list) or not current:
                return {"added": len(incoming), "overwritten": 0, "unchanged": 0}
            if current == incoming:
                return {"added": 0, "overwritten": 0, "unchanged": len(incoming)}
            return {"added": 0, "overwritten": len(incoming), "unchanged": 0}
        return {
            "added": 1 if self._migration_value_empty(current) and not self._migration_value_empty(incoming) else 0,
            "overwritten": 1 if not self._migration_value_empty(current) and current != incoming else 0,
            "unchanged": 1 if current == incoming else 0,
        }

    def _migration_config_diff(self, normalized: dict[str, Any]) -> dict[str, int]:
        counts = {"added": 0, "overwritten": 0, "unchanged": 0}
        for key, value in (normalized.get("settings") or {}).items():
            diff = self._migration_diff_counts(self._migration_current_setting_value(key), value)
            for item in counts:
                counts[item] += diff[item]
        provider_snapshot = self._provider_settings()
        for key, value in (normalized.get("providers") or {}).items():
            diff = self._migration_diff_counts(provider_snapshot.get(key, ""), value)
            for item in counts:
                counts[item] += diff[item]
        for key, value in (normalized.get("features") or {}).items():
            diff = self._migration_diff_counts(self._normalize_bool_value(getattr(self.plugin, key, self._config_get(key))), self._normalize_bool_value(value))
            for item in counts:
                counts[item] += diff[item]
        return counts

    def _deep_merge_dict(self, target: dict[str, Any], incoming: dict[str, Any], *, conflict: str = "use_backup") -> None:
        for key, value in incoming.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                self._deep_merge_dict(target[key], value, conflict=conflict)
            elif key in target and not self._should_apply_migration_value(target.get(key), value, conflict):
                continue
            else:
                target[key] = deepcopy(value)

    def _refresh_migration_runtime_caches(self, data_payload: dict[str, Any]) -> None:
        if "external_proactive_abilities" in data_payload and hasattr(self.plugin, "_external_proactive_abilities"):
            store = self.plugin.data.get("external_proactive_abilities")
            if isinstance(store, dict):
                self.plugin._external_proactive_abilities = deepcopy(store)

    def _available_provider_items(self) -> list[dict[str, Any]]:
        providers: list[Any] = []
        context = getattr(self.plugin, "context", None)
        get_all = getattr(context, "get_all_providers", None)
        if callable(get_all):
            try:
                providers = list(get_all() or [])
            except Exception:
                providers = []
        if not providers:
            manager = getattr(context, "provider_manager", None)
            inst_map = getattr(manager, "inst_map", None)
            if isinstance(inst_map, dict):
                providers = list(inst_map.values())

        using_id = ""
        get_using = getattr(context, "get_using_provider", None)
        if callable(get_using):
            try:
                using_id = self._provider_id(get_using())
            except Exception:
                using_id = ""

        items: list[dict[str, str]] = []
        seen: set[str] = set()
        for provider in providers:
            provider_id = self._provider_id(provider)
            if not provider_id or provider_id in seen:
                continue
            seen.add(provider_id)
            items.append(
                {
                    "id": provider_id,
                    "name": self._provider_name(provider, provider_id),
                    "type": self._provider_type(provider),
                    "model": self._provider_model(provider),
                    "is_default": provider_id == using_id,
                }
            )
        items.sort(key=lambda item: (not item["is_default"], item["name"].lower(), item["id"].lower()))
        return items

    @staticmethod
    def _provider_config(provider: Any) -> Any:
        return getattr(provider, "provider_config", None) or getattr(provider, "config", None) or {}

    @classmethod
    def _provider_config_value(cls, provider: Any, *keys: str) -> str:
        config = cls._provider_config(provider)
        for key in keys:
            value = ""
            if isinstance(config, dict):
                value = str(config.get(key, "") or "")
            else:
                value = str(getattr(config, key, "") or "")
            if value:
                return value.strip()
        return ""

    @classmethod
    def _provider_id(cls, provider: Any) -> str:
        if provider is None:
            return ""
        return (
            cls._provider_config_value(provider, "id", "provider_id")
            or str(getattr(provider, "provider_id", "") or "").strip()
            or str(getattr(provider, "id", "") or "").strip()
        )

    @classmethod
    def _provider_name(cls, provider: Any, provider_id: str) -> str:
        return (
            cls._provider_config_value(provider, "name", "display_name", "provider")
            or str(getattr(provider, "name", "") or "").strip()
            or provider_id
        )

    @classmethod
    def _provider_model(cls, provider: Any) -> str:
        return cls._provider_config_value(provider, "model", "model_name", "api_model", "model_id")

    @classmethod
    def _provider_type(cls, provider: Any) -> str:
        return (
            cls._provider_config_value(provider, "type", "provider_type")
            or provider.__class__.__name__
        )

    def _runtime_settings(self) -> dict[str, Any]:
        keys = [
            "bot_name",
            "page_font_family",
            "page_theme",
            "provider_config_mode",
            "plugin_specific_persona_id",
            "target_user_ids",
            "private_user_aliases",
            "private_user_delivery_aliases",
            "target_platform",
            "require_private_opt_in",
            "environment_perception_timezone",
            "holiday_country",
            "enable_environment_perception",
            "enable_holiday_perception",
            "enable_platform_perception",
            "enable_model_perception",
            "enable_worldview_perception",
            "enable_lunar_perception",
            "enable_solar_term_perception",
            "enable_almanac_perception",
            "default_nickname",
            "default_style",
            "reply_style_prompt",
            "enable_llm_timer_scheduling",
            "proactive_prompt_template",
            "proactive_persona_judge_send_threshold",
            "proactive_persona_judge_cache_minutes",
            "schedule_persona_prompt",
            "schedule_worldview_prompt",
            "roleplay_user_profile_prompt",
            "roleplay_knowledge_source_ids",
            "worldview_adaptation_mode",
            "worldview_adaptation_prompt",
            "quiet_hours",
            "passive_injection_position",
            "framework_session_lock_mode",
            "response_review_mode",
            "smart_silence_min_confidence",
            "smart_silence_model_timeout_seconds",
            "proactive_review_strength",
            "proactive_review_hard_risk_threshold",
            "proactive_review_low_score_threshold",
            "proactive_review_pressure_threshold",
            "response_review_max_chars",
            "passive_topic_memory_hours",
            "tts_generation_mode",
            "tts_voice_language",
            "tts_conversion_provider_id",
            "tts_extra_prompt",
            "tts_frequency_control_mode",
            "tts_constraint_mode",
            "tts_session_min_interval_seconds",
            "tts_private_min_interval_seconds",
            "tts_group_min_interval_seconds",
            "tts_trigger_probability",
            "tts_private_trigger_probability",
            "tts_group_trigger_probability",
            "enable_tts_local_playback",
            "enable_tts_local_playback_live_only",
            "enable_tts_live_subtitle_sync",
            "tts_live_subtitle_url",
            "tts_local_playback_volume",
            "tts_local_playback_min_interval_seconds",
            "auto_voice_enabled",
            "auto_voice_full_conversion_enabled",
            "auto_voice_probability",
            "auto_voice_max_chars",
            "auto_voice_cooldown_seconds",
            "main_user_voice_probability",
            "main_user_mention_voice_keywords",
            "main_user_mention_voice_probability",
            "main_user_mention_voice_prompt",
            "daily_token_limit",
            "enable_daily_token_soft_limit",
            "daily_token_soft_limit",
            "humanized_state_intensity",
            "enable_health_state",
            "enable_hunger_state",
            "enable_rest_reply_simulation",
            "rest_reply_mode",
            "rest_reply_probability",
            "rest_reply_llm_threshold",
            "rest_reply_active_windows",
            "rest_reply_awake_grace_minutes",
            "enable_rest_backlog_reply",
            "rest_backlog_max_messages",
            "REST_WAKEUP_PROVIDER_ID",
            "check_interval_seconds",
            "proactive_intensity_preset",
            "idle_minutes",
            "min_interval_minutes",
            "proactive_unanswered_slowdown_start",
            "proactive_unanswered_max_interval_multiplier",
            "friend_unanswered_max_cooldown_hours",
            "max_daily_messages",
            "proactive_photo_text_probability",
            "inbound_message_debounce_seconds",
            "enable_message_debounce",
            "enable_smart_message_debounce",
            "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID",
            "smart_message_debounce_model_timeout_seconds",
            "smart_message_debounce_wait_seconds",
            "smart_message_debounce_learning_window_seconds",
            "smart_message_debounce_examples_limit",
            "enable_recall_enhancement",
            "enable_recall_cancel_reply",
            "enable_recall_message_cache",
            "enable_recall_transcribe_command",
            "enable_forbidden_word_recall",
            "recall_forbidden_words",
            "recall_forbidden_scope",
            "recall_forbidden_word_case_sensitive",
            "text_message_debounce_seconds",
            "image_message_debounce_seconds",
            "forward_message_debounce_seconds",
            "text_message_debounce_max_wait_seconds",
            "message_debounce_max_merge_messages",
            "enable_semantic_message_debounce",
            "semantic_message_debounce_seconds",
            "enable_proactive_quote_trigger_message",
            "enable_quote_group_reply",
            "enable_quote_group_interjection",
            "enable_quote_private_proactive",
            "quote_skip_short_reply_chars",
            "quote_target_strategy",
            "enable_photo_text_action",
            "photo_action_max_daily",
            "photo_generation_backend",
            "COMFYUI_TEXT2IMG_WORKFLOW_NAME",
            "COMFYUI_SELFIE_WORKFLOW_NAME",
            "photo_persona_reference_image_path",
            "enable_daily_outfit_photo",
            "daily_outfit_photo_prompt",
            "enable_natural_language_photo_generation",
            "natural_language_photo_generation_max_daily",
            "natural_language_photo_extra_prompt",
            "comfyui_photo_wait_seconds",
            "enable_local_photo_load_guard",
            "local_photo_cpu_busy_percent",
            "local_photo_memory_busy_percent",
            "local_photo_defer_minutes",
            "external_image_api_platform",
            "EXTERNAL_IMAGE_API_BASE_URL",
            "EXTERNAL_IMAGE_API_KEY",
            "EXTERNAL_IMAGE_API_MODEL",
            "external_image_api_size",
            "external_image_api_timeout_seconds",
            "external_image_api_custom_headers",
            "enable_backup_external_image_api",
            "backup_external_image_api_platform",
            "BACKUP_EXTERNAL_IMAGE_API_BASE_URL",
            "BACKUP_EXTERNAL_IMAGE_API_KEY",
            "BACKUP_EXTERNAL_IMAGE_API_MODEL",
            "backup_external_image_api_size",
            "backup_external_image_api_timeout_seconds",
            "backup_external_image_api_custom_headers",
            "photo_generation_style",
            "photo_generation_style_custom_prompt",
            "photo_generation_fixed_prompt",
            "photo_generation_scene_presets",
            "private_image_vision_wait_seconds",
            "enable_context_image_captioning",
            "context_image_caption_max_items",
            "context_image_caption_timeout_seconds",
            "enable_private_image_gif_enhancement",
            "private_image_gif_max_frames",
            "enable_private_image_self_recognition",
            "private_image_self_recognition_hint",
            "enable_private_image_vision_cache",
            "private_image_vision_cache_max_items",
            "screen_diary_context_max_chars",
            "enable_segmented_proactive_reply",
            "segmented_proactive_scope",
            "segmented_proactive_chat_scope",
            "segmented_proactive_threshold",
            "segmented_proactive_min_segment_chars",
            "segmented_proactive_max_segments",
            "segmented_proactive_send_as_forward",
            "segmented_proactive_split_mode",
            "segmented_proactive_regex",
            "segmented_proactive_split_words",
            "enable_segmented_proactive_content_cleanup",
            "segmented_proactive_content_cleanup_scope",
            "segmented_proactive_content_cleanup_rule",
            "segmented_proactive_content_cleanup_words",
            "segmented_proactive_interval_method",
            "segmented_proactive_interval_min",
            "segmented_proactive_interval_max",
            "segmented_proactive_log_base",
            "group_conversation_followup_seconds",
            "group_conversation_followup_max_turns",
            "enable_group_conversation_followup",
            "enable_group_repeat_follow",
            "group_repeat_trigger_threshold",
            "group_repeat_count_distinct_users_only",
            "group_interject_min_interval_minutes",
            "group_interject_max_daily",
            "group_repeat_follow_probability",
            "group_repeat_interrupt_probability",
            "group_repeat_interrupt_probability_step",
            "group_repeat_interrupt_text",
            "group_repeat_interrupt_image_path",
            "group_scene_recent_limit",
            "enable_group_reality_promise_guard",
            "group_wakeup_direct_words",
            "group_wakeup_context_words",
            "group_wakeup_interest_keywords",
            "group_wakeup_interest_probability",
            "group_wakeup_short_text_wait_seconds",
            "group_wakeup_question_threshold",
            "group_wakeup_cold_group_threshold",
            "group_wakeup_cooldown_seconds",
            "group_wakeup_cold_group_idle_minutes",
            "group_wakeup_generated_keyword_limit",
            "group_wakeup_topic_interest_max_boost",
            "group_wakeup_debounce_pending_penalty",
            "group_wakeup_fatigue_limit",
            "group_wakeup_fatigue_decay_minutes",
            "group_wakeup_log_limit",
            "enable_group_high_intensity_mode",
            "group_high_intensity_wakeup_window_seconds",
            "group_high_intensity_wakeup_threshold",
            "group_high_intensity_cooldown_seconds",
            "group_high_intensity_merge_seconds",
            "group_high_intensity_max_merge_messages",
            "group_high_intensity_merge_scope",
            "enable_forward_message_adaptation",
            "forward_message_mode",
            "forward_message_max_messages",
            "forward_message_max_chars",
            "forward_message_parse_nested",
            "forward_message_image_vision",
            "forward_message_image_limit",
            "enable_recall_cancel_reply",
            "enable_recall_message_cache",
            "enable_recall_transcribe_command",
            "recall_message_cache_ttl_seconds",
            "recall_message_cache_max_items",
            "recall_message_image_cache_max_mb",
            "enable_forbidden_word_recall",
            "recall_forbidden_words",
            "recall_forbidden_scope",
            "recall_forbidden_word_case_sensitive",
            "screen_diary_context_max_chars",
            "max_group_recent_messages",
            "max_group_slang_terms",
            "group_slang_web_search_terms",
            "group_slang_web_search_results",
            "memory_refresh_interval_minutes",
            "episode_memory_refresh_messages",
            "episode_memory_refresh_minutes",
            "max_companion_memory_items",
            "max_learned_expression_items",
            "expression_learning_mode",
            "enable_expression_manual_review",
            "enable_expression_style_review",
            "max_dialogue_episodes",
            "user_habit_min_count",
            "user_habit_max_items",
            "emotional_gate_hurt_threshold",
            "emotional_gate_refuse_threshold",
            "emotional_gate_recovery_per_hour",
            "emotional_gate_max_hurt_minutes",
            "enable_llm_emotion_judgement",
            "emotion_judgement_mode",
            "enable_skill_growth_simulation",
            "skill_growth_rate",
            "skill_growth_custom_skills",
            "enable_skill_growth_passive_injection",
            "enable_skill_growth_schedule_influence",
            "skill_growth_schedule_influence_strength",
            "enable_bilibili_integration",
            "enable_bilibili_boredom_watch",
            "bilibili_boredom_min_interval_hours",
            "bilibili_share_probability",
            "bilibili_share_min_score",
            "enable_news_integration",
            "enable_news_boredom_read",
            "enable_news_daily_hot_read",
            "enable_ai_daily_watch",
            "ai_daily_sources",
            "ai_daily_prefer_text_version",
            "enable_external_event_self_link",
            "news_min_interval_hours",
            "news_share_probability",
            "external_event_self_link_probability",
            "external_event_self_link_cooldown_hours",
            "news_max_items_per_source",
            "news_sources",
            "news_hot_sources",
            "news_hot_max_items",
            "enable_web_exploration",
            "enable_web_exploration_boredom_search",
            "web_exploration_min_interval_hours",
            "web_exploration_share_probability",
            "web_exploration_max_results",
            "web_exploration_interests",
            "WEB_EXPLORATION_API_BASE_URL",
            "WEB_EXPLORATION_API_KEY",
            "WEB_EXPLORATION_API_MODEL",
            "enable_qzone_integration",
            "QZONE_COOKIE",
            "enable_qzone_life_publish",
            "qzone_life_publish_min_interval_hours",
            "qzone_life_publish_probability",
            "enable_qzone_generated_image_publish",
            "qzone_generated_image_probability",
            "enable_qzone_comment_inbox",
            "qzone_comment_inbox_interval_minutes",
            "qzone_comment_inbox_recent_posts",
            "qzone_comment_inbox_max_replies_per_tick",
            "enable_qzone_emotional_vent_publish",
            "qzone_emotional_vent_threshold",
            "qzone_emotional_vent_cooldown_hours",
            "qzone_emotional_vent_probability",
            "enable_private_reading_integration",
            "enable_private_reading_boredom_read",
            "enable_private_reading_ask_recommendation",
            "enable_private_reading_preference_influence",
            "enable_unanswered_screen_peek_followup",
            "unanswered_screen_peek_after_minutes",
            "unanswered_screen_peek_cooldown_minutes",
            "private_reading_min_interval_hours",
            "private_reading_max_photo_count",
            "private_reading_share_probability",
            "private_reading_ask_probability",
            "private_reading_preference_min_ratings",
            "private_reading_preference_max_terms",
            "private_reading_default_keywords",
            "private_reading_blocked_tags",
            "enable_unanswered_screen_peek_followup",
            "unanswered_screen_peek_after_minutes",
            "unanswered_screen_peek_cooldown_minutes",
            "enable_creative_writing",
            "creative_inspiration_probability",
            "creative_share_probability",
            "creative_chars_per_session",
            "creative_max_active_projects",
            "creative_hidden_mode",
            "enable_worldbook_member_recognition",
            "worldbook_auto_import",
            "worldbook_member_match_aliases",
            "worldbook_self_registration",
            "worldbook_self_registration_block_words",
            "worldbook_self_registration_block_reply",
            "worldbook_auto_pending_observations",
            "worldbook_member_inject_limit",
            "worldbook_config_paths",
            "cross_user_memory_owner_only",
            "enable_atrelay_tools",
            "atrelay_require_worldbook_first",
            "atrelay_member_cache_minutes",
            "atrelay_sensitive_confirm",
            "enable_atrelay_llm_rewrite",
            "atrelay_default_relay_style",
            "atrelay_multi_target_limit",
        ]
        provider_keys = self._schema_provider_keys(public_only=True)
        for key in sorted(self._schema_setting_keys(public_only=True)):
            if key not in keys and key not in provider_keys:
                keys.append(key)
        values = {key: getattr(self.plugin, key, self._config_get(key)) for key in keys}
        values["private_user_aliases"] = self._private_alias_config_text("private_user_aliases")
        values["private_user_delivery_aliases"] = self._private_alias_config_text("private_user_delivery_aliases")
        values.update(
            {
                "enable_private_reading_integration": bool(getattr(self.plugin, "enable_jm_cosmos_integration", False)),
                "enable_private_reading_boredom_read": bool(getattr(self.plugin, "enable_jm_cosmos_boredom_read", False)),
                "enable_private_reading_ask_recommendation": bool(getattr(self.plugin, "enable_private_reading_ask_recommendation", False)),
                "enable_private_reading_preference_influence": bool(getattr(self.plugin, "enable_private_reading_preference_influence", True)),
                "private_reading_min_interval_hours": getattr(self.plugin, "jm_cosmos_min_interval_hours", 18),
                "private_reading_max_photo_count": getattr(self.plugin, "jm_cosmos_max_photo_count", 60),
                "private_reading_share_probability": getattr(self.plugin, "jm_cosmos_share_probability", 0.18),
                "private_reading_ask_probability": getattr(self.plugin, "private_reading_ask_probability", 0.16),
                "private_reading_preference_min_ratings": getattr(self.plugin, "private_reading_preference_min_ratings", 5),
                "private_reading_preference_max_terms": getattr(self.plugin, "private_reading_preference_max_terms", 8),
                "private_reading_default_keywords": getattr(self.plugin, "jm_cosmos_default_keywords", ""),
                "private_reading_blocked_tags": getattr(self.plugin, "private_reading_blocked_tags", "連載中,長篇,青年漫"),
                "group_repeat_trigger_threshold": int(getattr(self.plugin, "group_repeat_trigger_threshold", 4) or 4),
                "group_repeat_count_distinct_users_only": bool(getattr(self.plugin, "group_repeat_count_distinct_users_only", False)),
                "group_repeat_follow_probability": int(round(float(getattr(self.plugin, "group_repeat_follow_probability", 0.18) or 0) * 100)),
                "group_repeat_interrupt_probability": int(round(float(getattr(self.plugin, "group_repeat_interrupt_probability", 0.10) or 0) * 100)),
                "group_repeat_interrupt_probability_step": int(round(float(getattr(self.plugin, "group_repeat_interrupt_probability_step", 0.12) or 0) * 100)),
            }
        )
        def _percent_attr(name: str, default: float = 0.0, *, inherit: bool = False) -> int:
            try:
                raw = float(getattr(self.plugin, name, default) or 0.0)
            except (TypeError, ValueError):
                raw = default
            if inherit and raw < 0:
                return -1
            if raw <= 1:
                raw *= 100
            return max(-1 if inherit else 0, min(100, int(round(raw))))

        values.update(
            {
                "tts_trigger_probability": _percent_attr("tts_trigger_probability", 0.25),
                "tts_private_trigger_probability": _percent_attr("tts_private_trigger_probability", -0.01, inherit=True),
                "tts_group_trigger_probability": _percent_attr("tts_group_trigger_probability", -0.01, inherit=True),
                "auto_voice_probability": _percent_attr("auto_voice_probability", 0.25),
                "main_user_voice_probability": _percent_attr("main_user_voice_probability", -0.01, inherit=True),
                "main_user_mention_voice_probability": _percent_attr("main_user_mention_voice_probability", 0.0),
                "rest_reply_probability": _percent_attr("rest_reply_probability", 0.18),
            }
        )
        for key in self._schema_bool_keys():
            if key in values:
                values[key] = self._normalize_bool_value(values[key])
        return values

    def _proactive_intensity_summary(self) -> dict[str, Any]:
        runtime_getter = getattr(self.plugin, "_proactive_intensity_runtime", None)
        runtime = runtime_getter() if callable(runtime_getter) else {}
        if not isinstance(runtime, dict):
            runtime = {}
        effects = runtime.get("effects") if isinstance(runtime.get("effects"), dict) else {}

        def call_or_attr(method_name: str, attr_name: str, default: Any) -> Any:
            method = getattr(self.plugin, method_name, None)
            if callable(method):
                try:
                    return method()
                except Exception:
                    pass
            return getattr(self.plugin, attr_name, default)

        effective = {
            "max_daily_messages": call_or_attr("_runtime_max_daily_messages", "max_daily_messages", 0),
            "idle_minutes": effects.get("idle_minutes", getattr(self.plugin, "idle_minutes", 0)),
            "min_interval_minutes": effects.get("min_interval_minutes", getattr(self.plugin, "min_interval_minutes", 0)),
            "proactive_persona_judge_send_threshold": call_or_attr(
                "_effective_proactive_persona_judge_send_threshold",
                "proactive_persona_judge_send_threshold",
                62,
            ),
            "proactive_review_strength": call_or_attr("_effective_proactive_review_strength", "proactive_review_strength", "lenient"),
            "group_wakeup_cooldown_seconds": call_or_attr(
                "_effective_group_wakeup_cooldown_seconds",
                "group_wakeup_cooldown_seconds",
                90,
            ),
            "group_high_intensity_cooldown_seconds": call_or_attr(
                "_effective_group_high_intensity_cooldown_seconds",
                "group_high_intensity_cooldown_seconds",
                150,
            ),
            "group_interject_min_interval_minutes": call_or_attr(
                "_effective_group_interject_min_interval_minutes",
                "group_interject_min_interval_minutes",
                180,
            ),
            "group_interject_max_daily": call_or_attr(
                "_effective_group_interject_max_daily",
                "group_interject_max_daily",
                2,
            ),
        }
        configured = {
            "max_daily_messages": getattr(self.plugin, "max_daily_messages", 0),
            "idle_minutes": getattr(self.plugin, "idle_minutes", 0),
            "min_interval_minutes": getattr(self.plugin, "min_interval_minutes", 0),
            "proactive_persona_judge_send_threshold": getattr(self.plugin, "proactive_persona_judge_send_threshold", 62),
            "proactive_review_strength": getattr(self.plugin, "proactive_review_strength", "lenient"),
            "group_wakeup_cooldown_seconds": getattr(self.plugin, "group_wakeup_cooldown_seconds", 90),
            "group_high_intensity_cooldown_seconds": getattr(self.plugin, "group_high_intensity_cooldown_seconds", 150),
            "group_interject_min_interval_minutes": getattr(self.plugin, "group_interject_min_interval_minutes", 180),
            "group_interject_max_daily": getattr(self.plugin, "group_interject_max_daily", 2),
        }
        changed = [
            key
            for key, value in effective.items()
            if str(value) != str(configured.get(key))
        ]
        return {
            "preset": self._single_line(runtime.get("preset") or "off", 40),
            "enabled": bool(runtime.get("enabled")),
            "label": self._single_line(runtime.get("label") or "关闭预设", 40),
            "description": self._single_line(runtime.get("description"), 160),
            "configured": configured,
            "effective": effective,
            "changed_keys": changed,
            "note": "预设只覆盖运行态有效频率，不改写手动参数；免打扰、休息、用户拒绝、隐私和成本闸门仍然生效。",
        }

    def _build_diagnostics(self, users: dict[str, Any], groups: dict[str, Any]) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []

        def add(level: str, title: str, text: str, action: str = "") -> None:
            items.append({"level": level, "title": title, "text": text, "action": action})

        features = self._feature_flags()
        providers = self._provider_settings()
        group_mode = str(getattr(self.plugin, "group_access_mode", "whitelist") or "whitelist")
        whitelist = self.plugin._configured_group_ids()
        blacklist = self.plugin._configured_group_blacklist_ids()
        enabled_users = sum(1 for item in users.values() if isinstance(item, dict) and item.get("enabled", True))
        enabled_groups = sum(1 for item in groups.values() if isinstance(item, dict) and item.get("enabled", True))

        if getattr(self.plugin, "enabled", False):
            add("ok", "插件已启用", "后台主动检查与事件处理会正常运行")
        else:
            add("error", "插件未启用", "当前不会进行私聊主动陪伴或群聊观察", "在配置中打开 enabled")

        if providers.get("LLM_PROVIDER_ID") or getattr(self.plugin, "llm_provider_id", ""):
            add("ok", "主模型可见", providers.get("LLM_PROVIDER_ID") or "运行态已配置")
        else:
            add("info", "主模型留空", "会回退到 AstrBot 默认模型；建议为陪伴插件单独配置主模型")

        intensity = self._proactive_intensity_summary()
        if intensity.get("enabled"):
            effective = intensity.get("effective") if isinstance(intensity.get("effective"), dict) else {}
            add(
                "warn",
                f"正在使用主动强度预设：{intensity.get('label') or intensity.get('preset')}",
                (
                    f"私聊有效上限 {effective.get('max_daily_messages')} 条/天，"
                    f"空闲 {effective.get('idle_minutes')} 分钟，"
                    f"最小间隔 {effective.get('min_interval_minutes')} 分钟；"
                    f"群唤醒冷却 {effective.get('group_wakeup_cooldown_seconds')} 秒，"
                    f"群插话间隔 {effective.get('group_interject_min_interval_minutes')} 分钟。"
                    "预设只覆盖运行态有效频率，不改写手动参数，也不会绕过免打扰、休息、用户拒绝、隐私和成本闸门。"
                ),
                "需要恢复原配置时，将“主动强度预设”改为关闭",
            )
        else:
            add(
                "info",
                "主动强度预设未启用",
                "当前完全沿用手动主动频率参数；如果想要高频主动，可在配置 → 功能开关 → 通用能力里选择预设。",
            )

        tts_summary = self._tts_runtime_summary(users)
        if tts_summary.get("enhancement_enabled"):
            if tts_summary.get("provider_available"):
                add(
                    "ok",
                    "TTS 语音链路可用",
                    f"模式 {tts_summary.get('mode')}，语种 {tts_summary.get('language')}，真实 TTS provider：{tts_summary.get('provider_label')}",
                )
            elif tts_summary.get("settings_enabled"):
                add(
                    "warn",
                    "TTS 配置已开但 provider 不可用",
                    f"会话 {tts_summary.get('umo') or '-'} 已启用 TTS 设置，但当前取不到可用 TTS provider",
                    "在 AstrBot 会话 TTS 配置里选择并启用真实语音合成 provider",
                )
            else:
                add(
                    "warn",
                    "TTS 强化已开但会话 TTS 未启用",
                    "本插件只能处理 <tts> 标签和文本转换；真正合成音频需要 AstrBot 当前会话启用 TTS provider",
                    "到 AstrBot 配置中为目标会话启用 TTS provider",
                )
        else:
            add(
                "info",
                "TTS 强化未开启",
                "VOICE_PROMPT_PROVIDER_ID / TTS文本转换模型都是文本模型；语音合成模型请在 AstrBot TTS provider 中配置",
            )

        if enabled_users:
            add("ok", "私聊对象已就绪", f"已启用 {enabled_users} 个私聊对象")
        else:
            add("warn", "暂无启用的私聊对象", "私聊主动陪伴没有明确目标", "在私聊页新增对象或配置 target_user_ids")

        max_daily = int(getattr(self.plugin, "max_daily_messages", 0) or 0)
        effective_max_daily = max_daily
        max_daily_getter = getattr(self.plugin, "_runtime_max_daily_messages", None)
        if callable(max_daily_getter):
            try:
                effective_max_daily = int(max_daily_getter() or 0)
            except Exception:
                effective_max_daily = max_daily
        if effective_max_daily > 0:
            detail = f"每日有效上限 {effective_max_daily} 条"
            if effective_max_daily != max_daily:
                detail += f"（手动配置 {max_daily} 条）"
            add("ok", "私聊主动额度可用", detail)
        else:
            add("warn", "私聊主动已关闭", "每日主动上限为 0", "在模块配置里调高每日主动上限")

        if getattr(self.plugin, "enable_daily_token_soft_limit", True):
            soft_limit = int(getattr(self.plugin, "daily_token_soft_limit", 0) or 0)
            today_tokens = int(getattr(self.plugin, "_today_llm_token_total", lambda: 0)() or 0)
            if soft_limit > 0 and today_tokens >= soft_limit:
                add(
                    "warn",
                    "每日 Token 软限额已接管",
                    f"今日已用约 {today_tokens} Token，低优先级后台 LLM 任务会暂缓",
                )
            elif soft_limit > 0:
                add("ok", "每日 Token 软限额已启用", f"软限额 {soft_limit}，当前约 {today_tokens}")
            else:
                add("info", "每日 Token 软限额未设置", "只使用每日硬限额")
        else:
            add("info", "每日 Token 软限额已关闭", "功能全开时后台任务会按各自开关正常运行")

        if features.get("enable_companion_memory") and features.get("enable_expression_learning"):
            add("ok", "私聊学习链路完整", "长期画像与表达学习均已打开")
        else:
            add("warn", "私聊学习链路不完整", "画像记忆或表达学习未开启", "在配置页打开对应功能开关")

        if features.get("enable_livingmemory_integration"):
            living_level = "ok" if self.plugin._livingmemory_available() else "warn"
            living_text = "LivingMemory 插件可被调用" if living_level == "ok" else "已启用协同，但当前未检测到可用工具"
            add(living_level, "LivingMemory 协同", living_text)

        if features.get("enable_bilibili_integration"):
            bili_available = bool(getattr(self.plugin, "_bilibili_available", lambda: False)())
            add(
                "ok" if bili_available else "info",
                "B站 AI Bot 联动",
                "已检测到 B站 AI Bot 或观看日志" if bili_available else "联动开关已开，但暂未检测到 B站 AI Bot 实例或日志",
            )

        if features.get("enable_private_reading_integration"):
            jm_available = bool(getattr(self.plugin, "_jm_cosmos_available", lambda: False)())
            add(
                "ok" if jm_available else "info",
                "夹层阅读素材",
                "已检测到可用素材能力" if jm_available else "开关已开，但暂未检测到可用素材能力",
            )

        if features.get("enable_photo_text_action") and getattr(self.plugin, "enable_local_photo_load_guard", False):
            load_state = getattr(self.plugin, "_local_photo_generation_load_state", lambda: {})()
            if isinstance(load_state, dict):
                if load_state.get("available"):
                    add(
                        "warn" if load_state.get("busy") else "ok",
                        "本地生图负载保护",
                        str(load_state.get("reason") or "负载正常"),
                    )
                else:
                    add("info", "本地生图负载保护未采样", str(load_state.get("reason") or "无法读取系统负载"))

        if features.get("enable_photo_text_action") and getattr(self.plugin, "_external_photo_available", lambda: False)():
            model_checker = getattr(self.plugin, "_external_image_model_misconfiguration_note", None)
            model_note = model_checker() if callable(model_checker) else ""
            if model_note:
                add(
                    "error",
                    "在线图片模型配置错误",
                    model_note,
                    "把 EXTERNAL_IMAGE_API_MODEL 改成该平台的图片模型名，不要填聊天模型",
                )

        llm_perception_available = bool(getattr(self.plugin, "_llmperception_available", lambda: False)())
        if llm_perception_available:
            if features.get("enable_environment_perception"):
                add(
                    "warn",
                    "检测到 LLMPerception 插件",
                    "本插件已内置时间、节假日、农历节气和平台环境感知；两者同时启用会重复注入并增加 Token 消耗",
                    "建议手动二选一；本插件不会再自动改写 enable_environment_perception",
                )
            else:
                add("ok", "环境感知由外部插件接管", "检测到 LLMPerception，且本插件内置环境感知当前为关闭")

        if features.get("enable_creative_writing"):
            projects = self._creative_summary({"creative_projects": getattr(self.plugin, "data", {}).get("creative_projects", [])})
            active = projects.get("active_projects", 0)
            add(
                "ok" if active else "info",
                "私下创作行为",
                f"当前进行中创作 {active} 个" if active else "已开启；会在生活/梦境触发后慢慢开坑",
            )

        if getattr(self.plugin, "enable_group_companion", False):
            if group_mode == "whitelist" and not whitelist:
                add("warn", "群聊白名单为空", "白名单模式下所有群都会被拦截", "在配置页加入群号或切换为黑名单模式")
            elif group_mode == "blacklist":
                add("ok", "群聊黑名单模式", f"已屏蔽 {len(blacklist)} 个群，其余群可观察")
            else:
                add("ok", "群聊白名单模式", f"允许 {len(whitelist)} 个群")

            if enabled_groups:
                add("ok", "已有群聊观测数据", f"已启用 {enabled_groups} 个群")
            else:
                add("info", "暂无群聊观测数据", "收到群消息后会逐步建立群内观察")
        else:
            add("info", "群聊陪伴未开启", "当前不会记录群聊上下文")

        if features.get("enable_group_interjection"):
            limit_getter = getattr(self.plugin, "_effective_group_interject_max_daily", None)
            limit = int(limit_getter() if callable(limit_getter) else getattr(self.plugin, "group_interject_max_daily", 0) or 0)
            if limit > 0:
                add("ok", "群聊插话可用", f"每群每日上限 {limit} 次")
            else:
                add("warn", "群聊插话开关已开但额度为 0", "功能不会真正触发", "在模块配置里调高每群每日插话上限")
        elif getattr(self.plugin, "enable_group_companion", False):
            add("info", "群聊以观察为主", "当前只积累群上下文，不主动插话")

        context_aware_installed = bool(getattr(self.plugin, "_context_aware_available", lambda: False)())
        if context_aware_installed:
            if features.get("enable_group_scene_awareness"):
                add(
                    "warn",
                    "检测到上下文场景感知增强插件",
                    "不会造成代码级冲突，但若两个插件同时注入群聊场景，会增加重复上下文和 Token 消耗",
                    "建议手动二选一；本插件不会再自动改写 enable_group_scene_awareness",
                )
            else:
                add("ok", "群聊场景感知由外部插件接管", "检测到 context_aware，且本插件对应内置功能当前为关闭")

        atrelay_installed = bool(getattr(self.plugin, "_atrelay_plugin_available", lambda: False)())
        if atrelay_installed:
            if features.get("enable_atrelay_tools"):
                add(
                    "warn",
                    "检测到艾特群友插件",
                    "本插件已内置跨群转述与 @ 群友工具；两者同时启用可能让模型看到重复工具",
                    "建议手动二选一；本插件不会再自动改写 enable_atrelay_tools",
                )
            else:
                add("ok", "跨群转述由外部插件接管", "检测到 atrelay，且本插件对应内置工具当前为关闭")

        if not features.get("enable_group_privacy_guard"):
            add("warn", "群聊隐私保护未开启", "私聊记忆注入群聊时缺少额外防护", "建议打开 enable_group_privacy_guard")

        refresh_minutes = int(getattr(self.plugin, "memory_refresh_interval_minutes", 0) or 0)
        if refresh_minutes and refresh_minutes < 60:
            add("warn", "长期记忆整理过于频繁", f"当前 {refresh_minutes} 分钟，可能增加模型调用量", "建议设置为 120 分钟以上")

        return items

    def _tts_runtime_summary(self, users: dict[str, Any]) -> dict[str, Any]:
        enabled_user = None
        for item in users.values():
            if isinstance(item, dict) and item.get("enabled", True) and item.get("umo"):
                enabled_user = item
                if self.plugin._private_user_role(item, str(item.get("user_id") or "")) == "owner":
                    break
        umo = self._single_line((enabled_user or {}).get("umo"), 180) if isinstance(enabled_user, dict) else ""
        config: dict[str, Any] = {}
        provider_settings: dict[str, Any] = {}
        provider = None
        context = getattr(self.plugin, "context", None)
        if context is not None:
            getter = getattr(context, "get_config", None)
            if callable(getter):
                try:
                    config = getter(umo) if umo else getter()
                    if not isinstance(config, dict):
                        config = {}
                except Exception:
                    config = {}
            provider_settings = dict((config or {}).get("provider_tts_settings", {}) or {})
            provider_getter = getattr(context, "get_using_tts_provider", None)
            if callable(provider_getter):
                try:
                    provider = provider_getter(umo) if umo else provider_getter()
                except Exception:
                    provider = None
        provider_label = ""
        if provider is not None:
            provider_id = self._provider_id(provider)
            provider_label = self._provider_name(provider, provider_id) if provider_id else getattr(provider, "__class__", type(provider)).__name__
        return {
            "enhancement_enabled": bool(getattr(self.plugin, "enable_tts_enhancement", False)),
            "mode": self._single_line(getattr(self.plugin, "tts_generation_mode", ""), 24) or "fast_tag",
            "language": self.plugin._tts_language_label() if hasattr(self.plugin, "_tts_language_label") else "",
            "umo": umo,
            "settings_enabled": bool(provider_settings.get("enable", False)),
            "provider_available": provider is not None,
            "provider_label": self._single_line(provider_label, 80) or "未知 provider",
        }

    def _message_debounce_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        raw = data.get("smart_message_debounce")
        if not isinstance(raw, dict):
            raw = {}
        logs_raw = raw.get("recent_logs") if isinstance(raw.get("recent_logs"), list) else []
        examples_raw = raw.get("examples") if isinstance(raw.get("examples"), list) else []
        logs: list[dict[str, Any]] = []
        for item in logs_raw[-30:][::-1]:
            if not isinstance(item, dict):
                continue
            logs.append(
                {
                    "ts": self._float(item.get("ts")),
                    "time": self.plugin._format_timestamp_elapsed(self._float(item.get("ts"))) if self._float(item.get("ts")) else "",
                    "scope": self._single_line(item.get("scope"), 80),
                    "sender_id": self._single_line(item.get("sender_id"), 40),
                    "chat": self._single_line(item.get("chat"), 20),
                    "text": self._single_line(item.get("text"), 180),
                    "decision": self._single_line(item.get("decision"), 40),
                    "confidence": self._float(item.get("confidence")),
                    "reason": self._single_line(item.get("reason"), 120),
                    "wait_seconds": self._float(item.get("wait_seconds")),
                    "outcome": self._single_line(item.get("outcome"), 40),
                    "note": self._single_line(item.get("note"), 160),
                    "source": self._single_line(item.get("source"), 40),
                    "raw": self._single_line(item.get("raw"), 180),
                    "message_count": self._int(item.get("message_count")),
                }
            )
        examples: list[dict[str, Any]] = []
        for item in examples_raw[-10:][::-1]:
            if not isinstance(item, dict):
                continue
            messages = item.get("messages") if isinstance(item.get("messages"), list) else []
            examples.append(
                {
                    "time": self.plugin._format_timestamp_elapsed(self._float(item.get("ts"))) if self._float(item.get("ts")) else "",
                    "kind": self._single_line(item.get("kind"), 40),
                    "scope": self._single_line(item.get("scope"), 80),
                    "sender_id": self._single_line(item.get("sender_id"), 40),
                    "messages": [self._single_line(message, 120) for message in messages[:4]],
                    "previous_decision": self._single_line(item.get("previous_decision"), 40),
                    "note": self._single_line(item.get("note"), 120),
                }
            )
        return {
            "enabled": bool(getattr(self.plugin, "enable_message_debounce", False)),
            "smart_enabled": bool(getattr(self.plugin, "enable_smart_message_debounce", False)),
            "text_wait": self._float(getattr(self.plugin, "text_message_debounce_seconds", 0.0)),
            "max_wait": self._float(getattr(self.plugin, "text_message_debounce_max_wait_seconds", 0.0)),
            "max_merge": self._int(getattr(self.plugin, "message_debounce_max_merge_messages", 0)),
            "smart_wait": self._float(getattr(self.plugin, "smart_message_debounce_wait_seconds", 0.0)),
            "learning_window": self._float(getattr(self.plugin, "smart_message_debounce_learning_window_seconds", 0.0)),
            "provider_id": self._single_line(getattr(self.plugin, "smart_message_debounce_provider_id", ""), 160),
            "recent_logs": logs,
            "examples": examples,
        }

    @staticmethod
    def _presets() -> dict[str, dict[str, Any]]:
        return {
            "safe": {
                "label": "保守低打扰",
                "settings": {
                    "max_daily_messages": 3,
                    "idle_minutes": 180,
                    "min_interval_minutes": 360,
                    "group_interject_max_daily": 0,
                    "group_interject_min_interval_minutes": 360,
                    "memory_refresh_interval_minutes": 720,
                    "episode_memory_refresh_messages": 12,
                    "episode_memory_refresh_minutes": 180,
                },
                "features": {
                    "enable_group_companion": True,
                    "enable_group_interjection": False,
                    "enable_companion_memory": True,
                    "enable_expression_learning": True,
                    "enable_response_self_review": True,
                    "enable_livingmemory_integration": True,
                },
            },
            "standard": {
                "label": "标准陪伴",
                "settings": {
                    "max_daily_messages": 6,
                    "idle_minutes": 60,
                    "min_interval_minutes": 120,
                    "group_interject_max_daily": 1,
                    "group_interject_min_interval_minutes": 240,
                    "memory_refresh_interval_minutes": 360,
                    "episode_memory_refresh_messages": 8,
                    "episode_memory_refresh_minutes": 90,
                },
                "features": {
                    "enable_group_companion": True,
                    "enable_group_interjection": False,
                    "enable_group_context_injection": True,
                    "enable_group_injection_guard": True,
                    "enable_companion_memory": True,
                    "enable_expression_learning": True,
                    "enable_dialogue_episode_memory": True,
                    "enable_open_loop_tracking": True,
                    "enable_response_self_review": True,
                },
            },
            "active": {
                "label": "高互动学习",
                "settings": {
                    "max_daily_messages": 10,
                    "idle_minutes": 30,
                    "min_interval_minutes": 60,
                    "group_interject_max_daily": 2,
                    "group_interject_min_interval_minutes": 180,
                    "memory_refresh_interval_minutes": 240,
                    "episode_memory_refresh_messages": 5,
                    "episode_memory_refresh_minutes": 60,
                },
                "features": {
                    "enable_group_companion": True,
                    "enable_group_injection_guard": True,
                    "enable_companion_memory": True,
                    "enable_expression_learning": True,
                    "enable_intent_emotion_analysis": True,
                    "enable_response_self_review": True,
                    "enable_dialogue_episode_memory": True,
                    "enable_open_loop_tracking": True,
                    "enable_group_interjection": True,
                    "enable_group_interjection_feedback": True,
                },
            },
            "group_observer": {
                "label": "群聊观察优先",
                "settings": {
                    "max_daily_messages": 4,
                    "idle_minutes": 90,
                    "min_interval_minutes": 180,
                    "group_interject_max_daily": 0,
                    "group_interject_min_interval_minutes": 240,
                    "max_group_recent_messages": 120,
                    "max_group_slang_terms": 80,
                },
                "features": {
                    "enable_group_companion": True,
                    "enable_group_context_injection": True,
                    "enable_group_injection_guard": True,
                    "enable_group_slang_learning": True,
                    "enable_group_member_profiles": True,
                    "enable_group_topic_threads": True,
                    "enable_group_episode_memory": True,
                    "enable_group_slang_meanings": True,
                    "enable_group_relationship_graph": True,
                    "enable_group_privacy_guard": True,
                    "enable_group_interjection": False,
                },
            },
        }

    def _apply_config_value(self, key: str, value: Any, overrides: dict[str, Any] | None = None) -> None:
        self._set_config_value(key, value)
        if key == "provider_config_mode":
            normalizer = getattr(self.plugin, "_normalize_provider_config_mode", None)
            self.plugin.provider_config_mode = (
                normalizer(value, getattr(self.plugin, "config", None))
                if callable(normalizer)
                else str(value or "quick").strip().lower()
            )
            return
        if key == "proactive_intensity_preset":
            normalizer = getattr(self.plugin, "_normalize_proactive_intensity_preset", None)
            self.plugin.proactive_intensity_preset = (
                normalizer(value)
                if callable(normalizer)
                else str(value or "off").strip().lower()
            )
            return
        if key == "page_font_family":
            text = str(value or "original").strip().lower()
            self.plugin.page_font_family = text if text in {"original", "cheng"} else "original"
            return
        if key == "page_theme":
            text = str(value or "classic").strip().lower()
            self.plugin.page_theme = text if text in {"classic", "dark", "warm", "forest", "sakura", "ocean", "lavender", "ink", "sunset"} else "classic"
            return
        if key == "storage_backend":
            backend = str(value or "json").strip().lower() or "json"
            self.plugin.storage_backend = backend if backend in {"json", "sqlite"} else "json"
            rebuild = getattr(self.plugin, "_rebuild_store_manager", None)
            if callable(rebuild) and not bool((overrides or {}).get("__defer_storage_rebuild")):
                rebuild(reload_data=True)
            return
        if key == "storage_sqlite_path":
            self.plugin.storage_sqlite_path = str(value or "").strip()
            rebuild = getattr(self.plugin, "_rebuild_store_manager", None)
            if callable(rebuild) and not bool((overrides or {}).get("__defer_storage_rebuild")):
                rebuild(reload_data=True)
            return
        attr_map = {
            "FAST_RESPONSE_PROVIDER_ID": "fast_response_provider_id",
            "COMPLEX_REASONING_PROVIDER_ID": "complex_reasoning_provider_id",
            "CREATIVE_MODEL_PROVIDER_ID": "creative_model_provider_id",
            "LLM_PROVIDER_ID": "llm_provider_id",
            "MAI_STYLE_PROVIDER_ID": "mai_style_provider_id",
            "DAILY_PLAN_PROVIDER_ID": "daily_plan_provider_id",
            "DETAIL_ENHANCEMENT_PROVIDER_ID": "detail_enhancement_provider_id",
            "DREAM_DIARY_PROVIDER_ID": "dream_diary_provider_id",
            "CREATIVE_PROVIDER_ID": "creative_provider_id",
            "CREATIVE_OUTLINE_PROVIDER_ID": "creative_outline_provider_id",
            "CREATIVE_REVIEW_PROVIDER_ID": "creative_review_provider_id",
            "VOICE_PROMPT_PROVIDER_ID": "voice_prompt_provider_id",
            "PHOTO_PROMPT_PROVIDER_ID": "photo_prompt_provider_id",
            "NARRATION_PROVIDER_ID": "narration_provider_id",
            "HISTORY_SUMMARY_PROVIDER_ID": "history_summary_provider_id",
            "RESPONSE_REVIEW_PROVIDER_ID": "response_review_provider_id",
            "SMART_SILENCE_PROVIDER_ID": "smart_silence_provider_id",
            "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID": "proactive_persona_judge_provider_id",
            "TROUBLESHOOTING_PROVIDER_ID": "troubleshooting_provider_id",
            "RELATIONSHIP_ANALYSIS_PROVIDER_ID": "relationship_analysis_provider_id",
            "EMOTION_JUDGEMENT_PROVIDER_ID": "emotion_judgement_provider_id",
            "COMPANION_MEMORY_PROVIDER_ID": "companion_memory_provider_id",
            "DIALOGUE_EPISODE_PROVIDER_ID": "dialogue_episode_provider_id",
            "GROUP_INTERJECT_PROVIDER_ID": "group_interject_provider_id",
            "GROUP_EPISODE_PROVIDER_ID": "group_episode_provider_id",
            "GROUP_SLANG_PROVIDER_ID": "group_slang_provider_id",
            "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID": "group_followup_judge_provider_id",
            "FORWARD_MESSAGE_PROVIDER_ID": "forward_message_provider_id",
            "PLUGIN_VISION_PROVIDER_ID": "plugin_vision_provider_id",
            "PRIVATE_READING_VISION_PROVIDER_ID": "jm_cosmos_vision_provider_id",
            "NEWS_PROVIDER_ID": "news_provider_id",
            "WEB_EXPLORATION_PROVIDER_ID": "web_exploration_provider_id",
            "WEB_EXPLORATION_API_BASE_URL": "web_exploration_api_base_url",
            "WEB_EXPLORATION_API_KEY": "web_exploration_api_key",
            "WEB_EXPLORATION_API_MODEL": "web_exploration_api_model",
            "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID": "smart_message_debounce_provider_id",
            "REST_WAKEUP_PROVIDER_ID": "rest_wakeup_provider_id",
            "COMFYUI_TEXT2IMG_WORKFLOW_NAME": "comfyui_text2img_workflow_name",
            "COMFYUI_SELFIE_WORKFLOW_NAME": "comfyui_selfie_workflow_name",
            "external_image_api_platform": "external_image_api_platform",
            "EXTERNAL_IMAGE_API_BASE_URL": "external_image_api_base_url",
            "EXTERNAL_IMAGE_API_KEY": "external_image_api_key",
            "EXTERNAL_IMAGE_API_MODEL": "external_image_api_model",
            "external_image_api_custom_headers": "external_image_api_custom_headers",
            "backup_external_image_api_platform": "backup_external_image_api_platform",
            "BACKUP_EXTERNAL_IMAGE_API_BASE_URL": "backup_external_image_api_base_url",
            "BACKUP_EXTERNAL_IMAGE_API_KEY": "backup_external_image_api_key",
            "BACKUP_EXTERNAL_IMAGE_API_MODEL": "backup_external_image_api_model",
            "backup_external_image_api_custom_headers": "backup_external_image_api_custom_headers",
        }
        if key in attr_map:
            setattr(self.plugin, attr_map[key], str(value or "").strip())
            if key == "DREAM_DIARY_PROVIDER_ID":
                shared = str(value or "").strip()
                self.plugin.dream_provider_id = shared
                self.plugin.diary_provider_id = shared
            return
        if key == "group_access_mode":
            self.plugin.group_access_mode = str(value or "whitelist").lower()
            return
        if key == "group_whitelist_ids":
            self.plugin.group_whitelist_ids = list(value or [])
            return
        if key == "group_blacklist_ids":
            self.plugin.group_blacklist_ids = list(value or [])
            return
        if key == "target_user_ids":
            self.plugin.target_user_ids = [
                str(item).strip()
                for item in (value or [])
                if str(item or "").strip()
            ]
            return
        if key == "QZONE_COOKIE":
            self.plugin.qzone_cookie = str(value or "").strip()
            return
        if key == "roleplay_knowledge_source_ids":
            normalizer = getattr(self.plugin, "_normalize_roleplay_knowledge_source_ids", None)
            self.plugin.roleplay_knowledge_source_ids = normalizer(value) if callable(normalizer) else list(value or [])
            return
        private_reading_attr_map = {
            "enable_private_reading_integration": "enable_jm_cosmos_integration",
            "enable_private_reading_boredom_read": "enable_jm_cosmos_boredom_read",
            "enable_private_reading_ask_recommendation": "enable_private_reading_ask_recommendation",
            "enable_private_reading_preference_influence": "enable_private_reading_preference_influence",
            "private_reading_min_interval_hours": "jm_cosmos_min_interval_hours",
            "private_reading_max_photo_count": "jm_cosmos_max_photo_count",
            "private_reading_share_probability": "jm_cosmos_share_probability",
            "private_reading_ask_probability": "private_reading_ask_probability",
            "private_reading_preference_min_ratings": "private_reading_preference_min_ratings",
            "private_reading_preference_max_terms": "private_reading_preference_max_terms",
            "private_reading_default_keywords": "jm_cosmos_default_keywords",
            "private_reading_blocked_tags": "private_reading_blocked_tags",
        }
        if key in private_reading_attr_map:
            setattr(self.plugin, private_reading_attr_map[key], value)
            return
        if key == "plugin_specific_persona_id":
            self.plugin.plugin_specific_persona_id = str(value or "").strip()
            self.plugin._default_persona_prompt_cache = ""
            self.plugin._default_persona_prompt_cache_persona_id = ""
            return
        if key == "private_user_aliases":
            self.plugin.private_user_aliases = self.plugin._parse_private_user_aliases(value)
            if self.plugin._merge_private_user_alias_records():
                self.plugin._save_data_sync()
            return
        if key == "private_user_delivery_aliases":
            self.plugin.private_user_delivery_aliases = self.plugin._parse_private_user_aliases(value)
            users = self.plugin.data.get("users", {})
            if isinstance(users, dict):
                for raw_user_id, user in users.items():
                    if isinstance(user, dict):
                        self.plugin._ensure_private_user_umo(str(raw_user_id), user)
                self.plugin._save_data_sync()
            return
        if key == "worldbook_self_registration_block_words":
            parser = getattr(self.plugin, "_parse_text_list_config", None)
            if callable(parser):
                self.plugin.worldbook_self_registration_block_words = parser(value, limit=120)
            else:
                self.plugin.worldbook_self_registration_block_words = value
            return
        if key == "worldbook_self_registration_block_reply":
            reply = str(value or "").strip()
            self.plugin.worldbook_self_registration_block_reply = "这个称呼我不记。" if reply in {"这个称呼我先不记。", "你是小猪"} else reply
            return
        if key in {"group_repeat_follow_probability", "group_repeat_interrupt_probability", "group_repeat_interrupt_probability_step"}:
            raw = float(value or 0)
            setattr(self.plugin, key, max(0.0, min(1.0, raw / 100.0 if raw > 1 else raw)))
            return
        if key == "rest_reply_probability":
            raw = float(value or 0)
            setattr(self.plugin, key, max(0.0, min(1.0, raw / 100.0 if raw > 1 else raw)))
            return
        if key in {"proactive_photo_text_probability", "proactive_share_probability"}:
            raw = float(value or 0)
            setattr(self.plugin, key, max(0.0, min(1.0, raw / 100.0 if raw > 1 else raw)))
            return
        tts_runtime_keys = {
            "tts_generation_mode",
            "tts_voice_language",
            "tts_conversion_provider_id",
            "tts_extra_prompt",
            "tts_frequency_control_mode",
            "tts_constraint_mode",
            "tts_session_min_interval_seconds",
            "tts_private_min_interval_seconds",
            "tts_group_min_interval_seconds",
            "tts_trigger_probability",
            "tts_private_trigger_probability",
            "tts_group_trigger_probability",
            "enable_tts_local_playback",
            "enable_tts_local_playback_live_only",
            "enable_tts_live_subtitle_sync",
            "tts_live_subtitle_url",
            "tts_local_playback_volume",
            "tts_local_playback_min_interval_seconds",
            "auto_voice_enabled",
            "auto_voice_full_conversion_enabled",
            "auto_voice_probability",
            "auto_voice_max_chars",
            "auto_voice_cooldown_seconds",
            "main_user_voice_probability",
            "main_user_mention_voice_keywords",
            "main_user_mention_voice_probability",
            "main_user_mention_voice_prompt",
        }
        if key == "enable_tts_enhancement" or key in tts_runtime_keys:
            loader = getattr(self.plugin, "_load_tts_enhancement_config", None)
            if callable(loader):
                loader(self._config_overlay(overrides or {key: value}))
            else:
                setattr(self.plugin, key, value)
            return
        if key in self._allowed_feature_keys():
            setattr(self.plugin, key, self._normalize_bool_value(value))
            return
        if key in self._allowed_setting_keys():
            setattr(self.plugin, key, value)

    def _sync_photo_generation_runtime_config(self) -> None:
        enabled_backup = self._config_get("enable_backup_external_image_api")
        if enabled_backup not in ("", None):
            self.plugin.enable_backup_external_image_api = self._normalize_bool_value(enabled_backup)
        mapping = {
            "photo_generation_backend": "photo_generation_backend",
            "COMFYUI_TEXT2IMG_WORKFLOW_NAME": "comfyui_text2img_workflow_name",
            "COMFYUI_SELFIE_WORKFLOW_NAME": "comfyui_selfie_workflow_name",
            "photo_persona_reference_image_path": "photo_persona_reference_image_path",
            "daily_outfit_photo_prompt": "daily_outfit_photo_prompt",
            "external_image_api_platform": "external_image_api_platform",
            "EXTERNAL_IMAGE_API_BASE_URL": "external_image_api_base_url",
            "EXTERNAL_IMAGE_API_KEY": "external_image_api_key",
            "EXTERNAL_IMAGE_API_MODEL": "external_image_api_model",
            "external_image_api_size": "external_image_api_size",
            "external_image_api_custom_headers": "external_image_api_custom_headers",
            "backup_external_image_api_platform": "backup_external_image_api_platform",
            "BACKUP_EXTERNAL_IMAGE_API_BASE_URL": "backup_external_image_api_base_url",
            "BACKUP_EXTERNAL_IMAGE_API_KEY": "backup_external_image_api_key",
            "BACKUP_EXTERNAL_IMAGE_API_MODEL": "backup_external_image_api_model",
            "backup_external_image_api_size": "backup_external_image_api_size",
            "backup_external_image_api_custom_headers": "backup_external_image_api_custom_headers",
            "photo_generation_style": "photo_generation_style",
            "photo_generation_style_custom_prompt": "photo_generation_style_custom_prompt",
            "photo_generation_fixed_prompt": "photo_generation_fixed_prompt",
            "photo_generation_scene_presets": "photo_generation_scene_presets",
        }
        for key, attr in mapping.items():
            value = self._config_get(key)
            if key == "photo_persona_reference_image_path":
                setattr(self.plugin, attr, str(value or "").strip())
                continue
            if value not in ("", None):
                text = str(value).strip()
                if key == "photo_generation_backend":
                    text = text.lower()
                    if text not in {"auto", "comfyui", "sdgen", "external"}:
                        text = "auto"
                elif key in {"external_image_api_platform", "backup_external_image_api_platform"}:
                    normalizer = getattr(self.plugin, "_normalize_external_image_api_platform", None)
                    text = normalizer(text) if callable(normalizer) else text.lower()
                    if text not in {"auto", "openai", "bailian"}:
                        text = "auto"
                setattr(self.plugin, attr, text)
        timeout = self._config_get("external_image_api_timeout_seconds")
        if timeout not in ("", None):
            try:
                self.plugin.external_image_api_timeout_seconds = max(20, min(600, int(float(timeout))))
            except Exception:
                pass
        backup_timeout = self._config_get("backup_external_image_api_timeout_seconds")
        if backup_timeout not in ("", None):
            try:
                self.plugin.backup_external_image_api_timeout_seconds = max(20, min(600, int(float(backup_timeout))))
            except Exception:
                pass
        wait_seconds = self._config_get("comfyui_photo_wait_seconds")
        if wait_seconds not in ("", None):
            try:
                self.plugin.comfyui_photo_wait_seconds = max(5, min(600, int(float(wait_seconds))))
            except Exception:
                pass

    @staticmethod
    def _normalize_bool_value(value: Any) -> bool:
        if isinstance(value, str):
            text = value.strip().lower()
            if text in {"true", "1", "yes", "y", "on", "enable", "enabled", "启用", "开启", "开", "是"}:
                return True
            if text in {"false", "0", "no", "n", "off", "disable", "disabled", "停用", "关闭", "关", "否", ""}:
                return False
        return bool(value)

    def _set_config_value(self, key: str, value: Any) -> None:
        config = getattr(self.plugin, "config", None)
        if config is None:
            return
        if key == "provider_config_mode":
            self._set_provider_config_mode_value(config, value)
            return
        if key == "proactive_intensity_preset":
            self._set_schema_compat_value(config, key, value)
            return
        if key in {"page_font_family", "page_theme"}:
            self._set_schema_compat_value(config, key, value)
            return
        if _set_into_config(config, key, value, allow_flat_fallback=False):
            return
        if self._set_schema_group_config_value(config, key, value):
            return
        _set_into_config(config, key, value)
        return

    def _set_provider_config_mode_value(self, config: Any, value: Any) -> None:
        # Keep the visible schema group and the hidden legacy flat key in sync.
        # AstrBot validates unknown flat keys during plugin load, while older
        # page/API paths still read or write the flat name directly.
        self._set_schema_group_config_value(config, "provider_config_mode", value, create_group=True)
        _set_into_config(config, "provider_config_mode", value)

    def _set_schema_compat_value(self, config: Any, key: str, value: Any) -> None:
        self._set_schema_group_config_value(config, key, value, create_group=True)
        _set_into_config(config, key, value)

    def _set_schema_group_config_value(self, config: Any, key: str, value: Any, *, create_group: bool = False) -> bool:
        group_key = self._schema_group_for_key(key)
        if not group_key:
            return False

        def set_in_group(target: dict[str, Any]) -> bool:
            group = target.get(group_key)
            if isinstance(group, dict):
                group[key] = value
                return True
            if create_group:
                target[group_key] = {key: value}
                return True
            return False

        if isinstance(config, dict) and set_in_group(config):
            return True
        for attr in ("data", "config"):
            target = getattr(config, attr, None)
            if isinstance(target, dict) and set_in_group(target):
                return True
        return False

    def _config_overlay(self, overrides: dict[str, Any]) -> Any:
        base = getattr(self.plugin, "config", {}) or {}

        class _Overlay:
            def get(self, item: str, default: Any = None) -> Any:
                if item in overrides:
                    return overrides[item]
                return _flat_get(base, item, getattr(base, item, default))

        return _Overlay()

    def _config_get(self, key: str) -> str:
        config = getattr(self.plugin, "config", None)
        value = _flat_get(config, key, None)
        if value not in (None, ""):
            return str(value)
        data = getattr(config, "data", None)
        if isinstance(data, dict):
            value = _flat_get(data, key, None)
            if value not in (None, ""):
                return str(value)
        raw = getattr(config, "config", None)
        if isinstance(raw, dict):
            value = _flat_get(raw, key, None)
            if value not in (None, ""):
                return str(value)
        try:
            return str(getattr(config, key, "") or "")
        except Exception:
            return ""

    def _private_alias_config_text(self, key: str) -> str:
        raw = self._config_get(key)
        if raw:
            return raw
        mapping = getattr(self.plugin, key, None)
        if not isinstance(mapping, dict):
            return ""
        lines: list[str] = []
        for alias, target in mapping.items():
            left = str(alias or "").strip()
            right = str(target or "").strip()
            if left and right:
                lines.append(f"{left}={right}")
        return "\n".join(lines)

    async def _save_config_if_possible(self) -> bool:
        config = getattr(self.plugin, "config", None)
        for method_name in ("save_config", "save", "save_conf"):
            save = getattr(config, method_name, None)
            if callable(save):
                try:
                    _ensure_config_parent_dir(config, logger=logger)
                    result = save()
                    if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                        await result
                    return True
                except TypeError:
                    continue
                except FileNotFoundError as exc:
                    if _ensure_config_parent_dir(config, error=exc, logger=logger):
                        try:
                            result = save()
                            if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                                await result
                            return True
                        except Exception as retry_exc:
                            logger.warning("[PrivateCompanionPage] 配置保存重试失败(%s): %s", method_name, self._single_line(retry_exc, 160))
                            return False
                    logger.warning("[PrivateCompanionPage] 配置保存失败(%s): %s", method_name, self._single_line(exc, 160))
                    return False
                except Exception as exc:
                    logger.warning("[PrivateCompanionPage] 配置保存失败(%s): %s", method_name, self._single_line(exc, 160))
                    return False
        logger.warning("[PrivateCompanionPage] 当前配置对象没有可用保存方法,本次改动可能只在运行态生效")
        return False

    def _can_save_config(self) -> bool:
        config = getattr(self.plugin, "config", None)
        return any(callable(getattr(config, method_name, None)) for method_name in ("save_config", "save", "save_conf"))

    def _allowed_feature_keys(self) -> set[str]:
        return {
            "enable_proactive_only_mode",
            "enable_mai_style_integration",
            "enable_companion_memory",
            "enable_expression_learning",
            "enable_intent_emotion_analysis",
            "enable_response_self_review",
            "enable_smart_silence",
            "enable_llm_timer_scheduling",
            "enable_llm_proactive_message",
            "enable_llm_proactive_persona_judge",
            "enable_passive_topic_suppression",
            "enable_relationship_state_machine",
            "enable_emotion_simulation",
            "enable_dialogue_episode_memory",
            "enable_open_loop_tracking",
            "enable_user_habit_learning",
            "enable_food_menu_recommendation",
            "enable_humanized_states",
            "enable_health_state",
            "enable_hunger_state",
            "enable_segmented_proactive_reply",
            "enable_proactive_quote_trigger_message",
            "enable_quote_group_reply",
            "enable_quote_group_interjection",
            "enable_quote_private_proactive",
            "enable_photo_text_action",
            "inject_passive_states",
            "enable_passive_state_delta_injection",
            "enable_cycle_state",
            "enable_skill_growth_simulation",
            "enable_skill_growth_passive_injection",
            "enable_message_debounce",
            "enable_smart_message_debounce",
            "enable_recall_enhancement",
            "enable_recall_cancel_reply",
            "enable_recall_message_cache",
            "enable_recall_transcribe_command",
            "enable_forbidden_word_recall",
            "enable_semantic_message_debounce",
            "enable_environment_perception",
            "enable_holiday_perception",
            "enable_platform_perception",
            "enable_model_perception",
            "enable_worldview_perception",
            "enable_lunar_perception",
            "enable_solar_term_perception",
            "enable_almanac_perception",
            "enable_group_companion",
            "enable_group_slang_learning",
            "enable_group_member_profiles",
            "enable_group_context_injection",
            "enable_group_injection_guard",
            "enable_group_persona_denoise",
            "enable_forward_message_adaptation",
            "enable_group_reality_promise_guard",
            "enable_group_wakeup_enhancement",
            "enable_group_wakeup_question",
            "enable_group_wakeup_cold_group",
            "enable_group_high_intensity_mode",
            "enable_private_image_self_recognition",
            "enable_private_image_gif_enhancement",
            "enable_group_conversation_followup",
            "enable_group_interjection",
            "enable_group_repeat_follow",
            "group_repeat_count_distinct_users_only",
            "enable_group_topic_threads",
            "enable_group_episode_memory",
            "enable_group_interjection_feedback",
            "enable_group_slang_meanings",
            "enable_group_slang_web_search",
            "enable_group_relationship_graph",
            "enable_group_privacy_guard",
            "enable_worldbook_member_recognition",
            "enable_cross_user_memory_bridge",
            "enable_group_scene_awareness",
            "enable_group_reality_promise_guard",
            "enable_atrelay_tools",
            "enable_livingmemory_integration",
            "enable_bilibili_integration",
            "enable_bilibili_boredom_watch",
            "enable_news_integration",
            "enable_news_boredom_read",
            "enable_news_daily_hot_read",
            "enable_ai_daily_watch",
            "enable_external_event_self_link",
            "enable_web_exploration",
            "enable_web_exploration_boredom_search",
            "enable_qzone_integration",
            "enable_qzone_life_publish",
            "enable_qzone_generated_image_publish",
            "enable_qzone_comment_inbox",
            "enable_qzone_emotional_vent_publish",
            "enable_private_reading_integration",
            "enable_private_reading_boredom_read",
            "enable_private_reading_ask_recommendation",
            "enable_private_reading_preference_influence",
            "enable_unanswered_screen_peek_followup",
            "enable_yesterday_screen_diary_context",
            "enable_tts_enhancement",
            "enable_creative_writing",
            "creative_hidden_mode",
        }

    def _allowed_provider_keys(self) -> set[str]:
        keys = {
            "FAST_RESPONSE_PROVIDER_ID",
            "COMPLEX_REASONING_PROVIDER_ID",
            "CREATIVE_MODEL_PROVIDER_ID",
            "LLM_PROVIDER_ID",
            "MAI_STYLE_PROVIDER_ID",
            "DAILY_PLAN_PROVIDER_ID",
            "DETAIL_ENHANCEMENT_PROVIDER_ID",
            "DREAM_DIARY_PROVIDER_ID",
            "CREATIVE_PROVIDER_ID",
            "CREATIVE_OUTLINE_PROVIDER_ID",
            "CREATIVE_REVIEW_PROVIDER_ID",
            "VOICE_PROMPT_PROVIDER_ID",
            "tts_conversion_provider_id",
            "PHOTO_PROMPT_PROVIDER_ID",
            "NARRATION_PROVIDER_ID",
            "HISTORY_SUMMARY_PROVIDER_ID",
            "RESPONSE_REVIEW_PROVIDER_ID",
            "SMART_SILENCE_PROVIDER_ID",
            "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID",
            "TROUBLESHOOTING_PROVIDER_ID",
            "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID",
            "REST_WAKEUP_PROVIDER_ID",
            "RELATIONSHIP_ANALYSIS_PROVIDER_ID",
            "COMPANION_MEMORY_PROVIDER_ID",
            "DIALOGUE_EPISODE_PROVIDER_ID",
            "GROUP_INTERJECT_PROVIDER_ID",
            "GROUP_EPISODE_PROVIDER_ID",
            "GROUP_SLANG_PROVIDER_ID",
            "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID",
            "FORWARD_MESSAGE_PROVIDER_ID",
            "PLUGIN_VISION_PROVIDER_ID",
            "PRIVATE_READING_VISION_PROVIDER_ID",
            "NEWS_PROVIDER_ID",
            "WEB_EXPLORATION_PROVIDER_ID",
            "EMOTION_JUDGEMENT_PROVIDER_ID",
        }
        keys.update(self._schema_provider_keys(public_only=True))
        return keys

    def _allowed_setting_keys(self) -> set[str]:
        keys = {
            "bot_name",
            "page_font_family",
            "page_theme",
            "provider_config_mode",
            "enable_proactive_only_mode",
            "plugin_specific_persona_id",
            "target_user_ids",
            "private_user_aliases",
            "private_user_delivery_aliases",
            "target_platform",
            "require_private_opt_in",
            "environment_perception_timezone",
            "holiday_country",
            "enable_environment_perception",
            "enable_holiday_perception",
            "enable_platform_perception",
            "enable_model_perception",
            "enable_worldview_perception",
            "enable_lunar_perception",
            "enable_solar_term_perception",
            "enable_almanac_perception",
            "default_nickname",
            "default_style",
            "reply_style_prompt",
            "response_review_mode",
            "smart_silence_min_confidence",
            "smart_silence_model_timeout_seconds",
            "proactive_review_strength",
            "proactive_review_hard_risk_threshold",
            "proactive_review_low_score_threshold",
            "proactive_review_pressure_threshold",
            "response_review_max_chars",
            "enable_llm_emotion_judgement",
            "emotion_judgement_mode",
            "schedule_persona_prompt",
            "schedule_worldview_prompt",
            "roleplay_user_profile_prompt",
            "roleplay_knowledge_source_ids",
            "worldview_adaptation_mode",
            "worldview_adaptation_prompt",
            "quiet_hours",
            "framework_session_lock_mode",
            "proactive_intensity_preset",
            "proactive_prompt_template",
            "proactive_persona_judge_send_threshold",
            "proactive_persona_judge_cache_minutes",
            "proactive_photo_text_probability",
            "passive_topic_memory_hours",
            "tts_generation_mode",
            "tts_voice_language",
            "tts_conversion_provider_id",
            "tts_extra_prompt",
            "tts_frequency_control_mode",
            "tts_constraint_mode",
            "tts_session_min_interval_seconds",
            "tts_private_min_interval_seconds",
            "tts_group_min_interval_seconds",
            "tts_trigger_probability",
            "tts_private_trigger_probability",
            "tts_group_trigger_probability",
            "enable_tts_local_playback",
            "enable_tts_local_playback_live_only",
            "enable_tts_live_subtitle_sync",
            "tts_live_subtitle_url",
            "tts_local_playback_volume",
            "tts_local_playback_min_interval_seconds",
            "auto_voice_enabled",
            "auto_voice_full_conversion_enabled",
            "auto_voice_probability",
            "auto_voice_max_chars",
            "auto_voice_cooldown_seconds",
            "main_user_voice_probability",
            "main_user_mention_voice_keywords",
            "main_user_mention_voice_probability",
            "main_user_mention_voice_prompt",
            "daily_token_limit",
            "enable_daily_token_soft_limit",
            "daily_token_soft_limit",
            "humanized_state_intensity",
            "passive_injection_position",
            "enable_rest_reply_simulation",
            "rest_reply_mode",
            "rest_reply_probability",
            "rest_reply_llm_threshold",
            "rest_reply_active_windows",
            "rest_reply_awake_grace_minutes",
            "enable_rest_backlog_reply",
            "rest_backlog_max_messages",
            "REST_WAKEUP_PROVIDER_ID",
            "check_interval_seconds",
            "idle_minutes",
            "min_interval_minutes",
            "proactive_unanswered_slowdown_start",
            "proactive_unanswered_max_interval_multiplier",
            "friend_unanswered_max_cooldown_hours",
            "max_daily_messages",
            "inbound_message_debounce_seconds",
            "enable_message_debounce",
            "enable_smart_message_debounce",
            "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID",
            "smart_message_debounce_model_timeout_seconds",
            "smart_message_debounce_wait_seconds",
            "smart_message_debounce_learning_window_seconds",
            "smart_message_debounce_examples_limit",
            "text_message_debounce_seconds",
            "image_message_debounce_seconds",
            "forward_message_debounce_seconds",
            "text_message_debounce_max_wait_seconds",
            "message_debounce_max_merge_messages",
            "enable_semantic_message_debounce",
            "semantic_message_debounce_seconds",
            "enable_proactive_quote_trigger_message",
            "enable_quote_group_reply",
            "enable_quote_group_interjection",
            "enable_quote_private_proactive",
            "quote_skip_short_reply_chars",
            "quote_target_strategy",
            "photo_action_max_daily",
            "photo_generation_backend",
            "COMFYUI_TEXT2IMG_WORKFLOW_NAME",
            "COMFYUI_SELFIE_WORKFLOW_NAME",
            "photo_persona_reference_image_path",
            "enable_daily_outfit_photo",
            "daily_outfit_photo_prompt",
            "enable_natural_language_photo_generation",
            "natural_language_photo_generation_max_daily",
            "natural_language_photo_extra_prompt",
            "comfyui_photo_wait_seconds",
            "enable_local_photo_load_guard",
            "local_photo_cpu_busy_percent",
            "local_photo_memory_busy_percent",
            "local_photo_defer_minutes",
            "external_image_api_platform",
            "EXTERNAL_IMAGE_API_BASE_URL",
            "EXTERNAL_IMAGE_API_KEY",
            "EXTERNAL_IMAGE_API_MODEL",
            "external_image_api_size",
            "external_image_api_timeout_seconds",
            "external_image_api_custom_headers",
            "enable_backup_external_image_api",
            "backup_external_image_api_platform",
            "BACKUP_EXTERNAL_IMAGE_API_BASE_URL",
            "BACKUP_EXTERNAL_IMAGE_API_KEY",
            "BACKUP_EXTERNAL_IMAGE_API_MODEL",
            "backup_external_image_api_size",
            "backup_external_image_api_timeout_seconds",
            "backup_external_image_api_custom_headers",
            "photo_generation_style",
            "photo_generation_style_custom_prompt",
            "photo_generation_fixed_prompt",
            "photo_generation_scene_presets",
            "private_image_vision_wait_seconds",
            "enable_context_image_captioning",
            "context_image_caption_max_items",
            "context_image_caption_timeout_seconds",
            "enable_private_image_gif_enhancement",
            "private_image_gif_max_frames",
            "enable_private_image_self_recognition",
            "private_image_self_recognition_hint",
            "enable_private_image_vision_cache",
            "private_image_vision_cache_max_items",
            "enable_segmented_proactive_reply",
            "segmented_proactive_scope",
            "segmented_proactive_chat_scope",
            "segmented_proactive_threshold",
            "segmented_proactive_min_segment_chars",
            "segmented_proactive_max_segments",
            "segmented_proactive_send_as_forward",
            "segmented_proactive_split_mode",
            "segmented_proactive_regex",
            "segmented_proactive_split_words",
            "enable_segmented_proactive_content_cleanup",
            "segmented_proactive_content_cleanup_scope",
            "segmented_proactive_content_cleanup_rule",
            "segmented_proactive_content_cleanup_words",
            "segmented_proactive_interval_method",
            "segmented_proactive_interval_min",
            "segmented_proactive_interval_max",
            "segmented_proactive_log_base",
            "group_conversation_followup_seconds",
            "group_conversation_followup_max_turns",
            "enable_group_conversation_followup",
            "enable_group_repeat_follow",
            "group_repeat_trigger_threshold",
            "group_repeat_count_distinct_users_only",
            "group_interject_min_interval_minutes",
            "group_interject_max_daily",
            "group_repeat_follow_probability",
            "group_repeat_interrupt_probability",
            "group_repeat_interrupt_probability_step",
            "group_repeat_interrupt_text",
            "group_repeat_interrupt_image_path",
            "group_scene_recent_limit",
            "enable_group_injection_guard",
            "enable_group_persona_denoise",
            "group_wakeup_direct_words",
            "group_wakeup_context_words",
            "group_wakeup_interest_keywords",
            "group_wakeup_interest_probability",
            "group_wakeup_short_text_wait_seconds",
            "group_wakeup_question_threshold",
            "group_wakeup_cold_group_threshold",
            "group_wakeup_cooldown_seconds",
            "group_wakeup_cold_group_idle_minutes",
            "group_wakeup_generated_keyword_limit",
            "group_wakeup_topic_interest_max_boost",
            "group_wakeup_debounce_pending_penalty",
            "group_wakeup_fatigue_limit",
            "group_wakeup_fatigue_decay_minutes",
            "group_wakeup_log_limit",
            "enable_group_high_intensity_mode",
            "group_high_intensity_wakeup_window_seconds",
            "group_high_intensity_wakeup_threshold",
            "group_high_intensity_cooldown_seconds",
            "group_high_intensity_merge_seconds",
            "group_high_intensity_max_merge_messages",
            "group_high_intensity_merge_scope",
            "enable_forward_message_adaptation",
            "forward_message_mode",
            "forward_message_max_messages",
            "forward_message_max_chars",
            "forward_message_parse_nested",
            "forward_message_image_vision",
            "forward_message_image_limit",
            "enable_recall_cancel_reply",
            "enable_recall_message_cache",
            "enable_recall_transcribe_command",
            "recall_message_cache_ttl_seconds",
            "recall_message_cache_max_items",
            "recall_message_image_cache_max_mb",
            "enable_forbidden_word_recall",
            "recall_forbidden_words",
            "recall_forbidden_scope",
            "recall_forbidden_word_case_sensitive",
            "screen_diary_context_max_chars",
            "max_group_recent_messages",
            "max_group_slang_terms",
            "group_slang_web_search_terms",
            "group_slang_web_search_results",
            "memory_refresh_interval_minutes",
            "episode_memory_refresh_messages",
            "episode_memory_refresh_minutes",
            "max_companion_memory_items",
            "max_learned_expression_items",
            "expression_learning_mode",
            "enable_expression_manual_review",
            "enable_expression_style_review",
            "max_dialogue_episodes",
            "user_habit_min_count",
            "user_habit_max_items",
            "emotional_gate_hurt_threshold",
            "emotional_gate_refuse_threshold",
            "emotional_gate_recovery_per_hour",
            "emotional_gate_max_hurt_minutes",
            "enable_llm_emotion_judgement",
            "emotion_judgement_mode",
            "enable_skill_growth_simulation",
            "skill_growth_rate",
            "skill_growth_custom_skills",
            "enable_skill_growth_passive_injection",
            "enable_skill_growth_schedule_influence",
            "skill_growth_schedule_influence_strength",
            "enable_bilibili_integration",
            "enable_bilibili_boredom_watch",
            "bilibili_boredom_min_interval_hours",
            "bilibili_share_probability",
            "bilibili_share_min_score",
            "enable_news_integration",
            "enable_news_boredom_read",
            "enable_news_daily_hot_read",
            "enable_ai_daily_watch",
            "ai_daily_sources",
            "ai_daily_prefer_text_version",
            "enable_external_event_self_link",
            "news_min_interval_hours",
            "news_share_probability",
            "external_event_self_link_probability",
            "external_event_self_link_cooldown_hours",
            "news_max_items_per_source",
            "news_sources",
            "news_hot_sources",
            "news_hot_max_items",
            "enable_web_exploration",
            "enable_web_exploration_boredom_search",
            "web_exploration_min_interval_hours",
            "web_exploration_share_probability",
            "external_event_self_link_probability",
            "external_event_self_link_cooldown_hours",
            "web_exploration_max_results",
            "web_exploration_interests",
            "WEB_EXPLORATION_API_BASE_URL",
            "WEB_EXPLORATION_API_KEY",
            "WEB_EXPLORATION_API_MODEL",
            "enable_qzone_integration",
            "QZONE_COOKIE",
            "enable_qzone_life_publish",
            "qzone_life_publish_min_interval_hours",
            "qzone_life_publish_probability",
            "enable_qzone_generated_image_publish",
            "qzone_generated_image_probability",
            "enable_qzone_comment_inbox",
            "qzone_comment_inbox_interval_minutes",
            "qzone_comment_inbox_recent_posts",
            "qzone_comment_inbox_max_replies_per_tick",
            "enable_qzone_emotional_vent_publish",
            "qzone_emotional_vent_threshold",
            "qzone_emotional_vent_cooldown_hours",
            "qzone_emotional_vent_probability",
            "enable_private_reading_integration",
            "enable_private_reading_boredom_read",
            "enable_private_reading_ask_recommendation",
            "private_reading_min_interval_hours",
            "private_reading_max_photo_count",
            "private_reading_share_probability",
            "private_reading_ask_probability",
            "private_reading_preference_min_ratings",
            "private_reading_preference_max_terms",
            "private_reading_default_keywords",
            "private_reading_blocked_tags",
            "enable_unanswered_screen_peek_followup",
            "unanswered_screen_peek_after_minutes",
            "unanswered_screen_peek_cooldown_minutes",
            "enable_creative_writing",
            "creative_inspiration_probability",
            "creative_share_probability",
            "creative_chars_per_session",
            "creative_max_active_projects",
            "creative_hidden_mode",
            "enable_worldbook_member_recognition",
            "worldbook_auto_import",
            "worldbook_member_match_aliases",
            "worldbook_self_registration",
            "worldbook_self_registration_block_words",
            "worldbook_self_registration_block_reply",
            "worldbook_auto_pending_observations",
            "worldbook_member_inject_limit",
            "worldbook_config_paths",
            "cross_user_memory_owner_only",
            "enable_atrelay_tools",
            "atrelay_require_worldbook_first",
            "atrelay_member_cache_minutes",
            "atrelay_sensitive_confirm",
            "enable_atrelay_llm_rewrite",
            "atrelay_default_relay_style",
            "atrelay_multi_target_limit",
        }
        keys.update(self._schema_setting_keys(public_only=True))
        return keys

    def _normalize_setting_value(self, key: str, value: Any) -> Any:
        if key in self._schema_bool_keys():
            return self._normalize_bool_value(value)
        if key == "target_user_ids":
            return self._normalize_id_list(value)
        if key == "plugin_specific_persona_id":
            return str(value or "").strip()[:160]
        if key == "page_font_family":
            text = str(value or "original").strip().lower()
            return text if text in {"original", "cheng"} else "original"
        if key == "page_theme":
            text = str(value or "classic").strip().lower()
            return text if text in {"classic", "dark", "warm", "forest", "sakura", "ocean", "lavender", "ink", "sunset"} else "classic"
        if key == "provider_config_mode":
            normalizer = getattr(self.plugin, "_normalize_provider_config_mode", None)
            if callable(normalizer):
                return normalizer(value, getattr(self.plugin, "config", None))
            text = str(value or "quick").strip().lower()
            aliases = {
                "fast": "quick",
                "simple": "quick",
                "快速": "quick",
                "快速配置": "quick",
                "precise": "precision",
                "advanced": "precision",
                "精准": "precision",
                "精准配置": "precision",
                "分流": "precision",
            }
            text = aliases.get(text, text)
            return text if text in {"quick", "precision"} else "quick"
        if key == "storage_backend":
            text = str(value or "json").strip().lower()
            return text if text in {"json", "sqlite"} else "json"
        if key == "storage_sqlite_path":
            return str(value or "").strip()[:1000]
        if key == "passive_injection_position":
            normalizer = getattr(self.plugin, "_normalize_passive_injection_position", None)
            if callable(normalizer):
                return normalizer(value)
            text = str(value or "prompt").strip().lower()
            return text if text in {"auto", "prompt", "system_prompt"} else "prompt"
        if key == "framework_session_lock_mode":
            normalizer = getattr(self.plugin, "_normalize_framework_session_lock_mode", None)
            if callable(normalizer):
                return normalizer(value)
            text = str(value or "auto").strip().lower()
            return text if text in {"auto", "always", "off"} else "auto"
        if key == "rest_reply_active_windows":
            return re.sub(r"\s+", "", str(value or ""))[:160]
        if key == "quote_target_strategy":
            text = str(value or "current").strip().lower()
            aliases = {
                "当前": "current",
                "当前消息": "current",
                "触发消息": "current",
                "引用旧消息": "quoted",
                "旧消息": "quoted",
                "被引用消息": "quoted",
                "自动": "auto",
            }
            text = aliases.get(text, text)
            return text if text in {"current", "quoted", "auto"} else "current"
        if key == "group_high_intensity_merge_scope":
            text = str(value or "group").strip().lower()
            aliases = {
                "sender": "same_user",
                "same_sender": "same_user",
                "user": "same_user",
                "同一用户": "same_user",
                "同一发送者": "same_user",
                "全群": "group",
            }
            text = aliases.get(text, text)
            return text if text in {"group", "same_user"} else "group"
        if key in {"private_user_aliases", "private_user_delivery_aliases"}:
            return str(value or "").strip()[:4000]
        if key == "worldbook_config_paths":
            return str(value or "").strip()[:1000]
        if key in {"news_sources", "ai_daily_sources"}:
            return self._normalize_multiline_source_config(value, limit=4000)
        if key in {"news_hot_sources", "web_exploration_interests", "private_reading_default_keywords", "private_reading_blocked_tags"}:
            return str(value or "").strip()[:1200]
        if key == "WEB_EXPLORATION_API_BASE_URL":
            raw = str(value or "").strip()[:800]
            if not raw or raw.startswith(("http://", "https://")):
                return raw
            if re.match(r"^[a-z][a-z0-9+.-]*://", raw, flags=re.I):
                return raw
            local_pattern = r"^(localhost|127\.|10\.|172\.(1[6-9]|2\d|3[0-1])\.|192\.168\.|\[?::1\]?)"
            scheme = "http://" if re.match(local_pattern, raw, flags=re.I) else "https://"
            return f"{scheme}{raw}"
        if key == "WEB_EXPLORATION_API_KEY":
            return str(value or "").strip()[:800]
        if key == "WEB_EXPLORATION_API_MODEL":
            return str(value or "").strip()[:160]
        if key == "worldbook_self_registration_block_words":
            return str(value or "").strip()[:1200]
        if key == "worldbook_self_registration_block_reply":
            reply = str(value or "").strip()[:200]
            return "这个称呼我不记。" if reply in {"这个称呼我先不记。", "你是小猪"} else reply
        if key == "QZONE_COOKIE":
            return str(value or "").replace("\r", ";").replace("\n", ";").strip()[:8000]
        if key in {"group_wakeup_direct_words", "group_wakeup_context_words", "group_wakeup_interest_keywords", "recall_forbidden_words"}:
            parser = getattr(self.plugin, "_parse_text_list_config", None)
            if callable(parser):
                limit = 300 if key == "recall_forbidden_words" else 120
                return parser(value, limit=limit)
            if isinstance(value, list):
                limit = 300 if key == "recall_forbidden_words" else 120
                return [str(item).strip() for item in value if str(item or "").strip()][:limit]
            text = str(value or "").strip()[:1200]
            if not text:
                return []
            return [part.strip() for part in re.split(r"[\n,，、;；]+", text) if part.strip()]
        if key == "recall_forbidden_scope":
            scope = str(value or "bot_and_group").strip().lower()
            return scope if scope in {"bot_only", "group_only", "bot_and_group"} else "bot_and_group"
        if key == "private_image_self_recognition_hint":
            return str(value or "").strip()[:1200]
        if key == "worldview_adaptation_mode":
            mode = str(value or "auto").strip()
            return mode if mode in {"auto", "modern", "fantasy", "sci_fi", "custom", "off"} else "auto"
        if key == "rest_reply_mode":
            mode = str(value or "probability").strip().lower()
            aliases = {
                "概率": "probability",
                "仅概率": "probability",
                "仅概率醒来": "probability",
                "模型": "llm",
                "模型判断": "llm",
                "模型判断是否醒来": "llm",
                "model": "llm",
                "llm_judge": "llm",
            }
            mode = aliases.get(mode, mode)
            return mode if mode in {"probability", "llm"} else "probability"
        if key == "REST_WAKEUP_PROVIDER_ID":
            return str(value or "").strip()[:160]
        if key == "tts_generation_mode":
            mode = str(value or "fast_tag").strip().lower()
            aliases = {
                "hybrid": "fast_tag",
                "direct": "fast_tag",
                "tag": "fast_tag",
                "fast": "fast_tag",
                "快速": "fast_tag",
                "标签": "fast_tag",
                "标签直出": "fast_tag",
                "convert": "postprocess",
                "post": "postprocess",
                "llm": "postprocess",
                "后处理": "postprocess",
                "判断翻译": "postprocess",
            }
            mode = aliases.get(mode, mode)
            return mode if mode in {"fast_tag", "postprocess"} else "fast_tag"
        if key == "tts_frequency_control_mode":
            mode = str(value or "global").strip().lower()
            aliases = {
                "全局": "global",
                "全局频控": "global",
                "新版": "global",
                "旧版": "legacy",
                "旧版行为": "legacy",
                "legacy_mode": "legacy",
            }
            mode = aliases.get(mode, mode)
            return mode if mode in {"global", "legacy"} else "global"
        if key == "tts_constraint_mode":
            mode = str(value or "weak").strip().lower()
            aliases = {
                "弱": "weak",
                "弱约束": "weak",
                "软": "weak",
                "软约束": "weak",
                "强": "strong",
                "强约束": "strong",
                "硬": "strong",
                "硬约束": "strong",
            }
            mode = aliases.get(mode, mode)
            return mode if mode in {"weak", "strong"} else "weak"
        if key == "tts_voice_language":
            lang = str(value or "ja").strip().lower()
            return lang if lang in {"ja", "zh", "en"} else "ja"
        if key in {"tts_extra_prompt", "main_user_mention_voice_prompt"}:
            return str(value or "").strip()[:1200]
        if key in {"natural_language_photo_extra_prompt", "photo_generation_fixed_prompt", "photo_generation_scene_presets"}:
            return str(value or "").strip()[:5000]
        if key == "tts_conversion_provider_id":
            return str(value or "").strip()[:160]
        if key == "tts_session_min_interval_seconds":
            try:
                return max(0.0, min(3600.0, float(value)))
            except (TypeError, ValueError):
                return 90.0
        if key in {"tts_private_min_interval_seconds", "tts_group_min_interval_seconds"}:
            try:
                return max(-1.0, min(3600.0, float(value)))
            except (TypeError, ValueError):
                return -1.0
        if key == "main_user_mention_voice_keywords":
            return str(value or "").strip()[:1200]
        if key == "forward_message_mode":
            mode = str(value or "inject").strip().lower()
            if mode in {"注入", "injection"}:
                return "inject"
            if mode in {"转述", "summary", "summarize", "narrate", "relay"}:
                return "transcribe"
            return mode if mode in {"inject", "transcribe"} else "inject"
        if key == "photo_generation_backend":
            mode = str(value or "auto").strip().lower()
            return mode if mode in {"auto", "comfyui", "sdgen", "external"} else "auto"
        if key in {"external_image_api_platform", "backup_external_image_api_platform"}:
            mode = str(value or "auto").strip().lower()
            aliases = {
                "openai兼容": "openai",
                "openai-compatible": "openai",
                "百炼": "bailian",
                "阿里云百炼": "bailian",
                "dashscope": "bailian",
            }
            mode = aliases.get(mode, mode)
            return mode if mode in {"auto", "openai", "bailian"} else "auto"
        if key == "segmented_proactive_split_mode":
            mode = str(value or "regex").strip().lower()
            return mode if mode in {"regex", "words"} else "regex"
        if key == "segmented_proactive_scope":
            mode = str(value or "proactive_only").strip().lower()
            aliases = {
                "plugin": "proactive_only",
                "plugins": "proactive_only",
                "proactive": "proactive_only",
                "插件": "proactive_only",
                "插件主动": "proactive_only",
                "all": "all_llm",
                "llm": "all_llm",
                "全部": "all_llm",
                "全部分段": "all_llm",
            }
            mode = aliases.get(mode, mode)
            return mode if mode in {"proactive_only", "all_llm"} else "proactive_only"
        if key == "segmented_proactive_chat_scope":
            mode = str(value or "all").strip().lower()
            aliases = {
                "全部": "all",
                "all_chat": "all",
                "both": "all",
                "私聊": "private",
                "仅私聊": "private",
                "private_only": "private",
                "群聊": "group",
                "仅群聊": "group",
                "group_only": "group",
            }
            mode = aliases.get(mode, mode)
            return mode if mode in {"all", "private", "group"} else "all"
        if key == "segmented_proactive_interval_method":
            mode = str(value or "log").strip().lower()
            return mode if mode in {"log", "random"} else "log"
        if key == "segmented_proactive_content_cleanup_scope":
            mode = str(value or "all").strip().lower()
            return mode if mode in {"all", "trailing"} else "all"
        if key in {"segmented_proactive_split_words", "segmented_proactive_content_cleanup_words"}:
            def _decode_segmented_word(raw: Any) -> str:
                text = str(raw or "")
                stripped = text.strip()
                lowered = stripped.lower()
                if lowered in {"<space>", "{space}", "[space]", "\\s", "\\u0020", "空格"}:
                    return " "
                if lowered in {"<newline>", "{newline}", "[newline]", "\\n", "换行"}:
                    return "\n"
                if lowered in {"<tab>", "{tab}", "[tab]", "\\t", "tab"}:
                    return "\t"
                if lowered in {"<comma>", "{comma}", "[comma]", "comma", "英文逗号"}:
                    return ","
                if lowered in {"<zh_comma>", "{zh_comma}", "[zh_comma]", "zh_comma", "中文逗号", "逗号"}:
                    return "，"
                if text and text.isspace():
                    return text[:1]
                return stripped

            if isinstance(value, list):
                words = [_decode_segmented_word(item) for item in value]
            else:
                raw_words = str(value or "")
                parts = re.split(r"\r?\n", raw_words) if ("\n" in raw_words or "\r" in raw_words) else re.split(r"[,、]+", raw_words)
                words = [_decode_segmented_word(part) for part in parts]
            words = [word for word in words if word != ""]
            return words[:80]
        if key in {"segmented_proactive_regex", "segmented_proactive_content_cleanup_rule"}:
            return str(value or "").strip()[:800]
        if key == "atrelay_default_relay_style":
            mode = str(value or "persona").strip()
            return mode if mode in {"persona", "soft", "original"} else "persona"
        if key in {"reply_style_prompt", "worldview_adaptation_prompt"}:
            return str(value or "").strip()[:1200]
        if key == "roleplay_knowledge_source_ids":
            normalizer = getattr(self.plugin, "_normalize_roleplay_knowledge_source_ids", None)
            if callable(normalizer):
                return normalizer(value)
            return []
        if key in {"schedule_persona_prompt", "schedule_worldview_prompt", "roleplay_user_profile_prompt"}:
            return str(value or "").strip()[:2000]
        if key == "humanized_state_intensity":
            try:
                return max(0, min(100, int(value)))
            except (TypeError, ValueError):
                return 50
        if key in {"local_photo_cpu_busy_percent", "local_photo_memory_busy_percent"}:
            try:
                return max(1, min(100, int(value)))
            except (TypeError, ValueError):
                return 85 if key == "local_photo_cpu_busy_percent" else 88
        if key == "local_photo_defer_minutes":
            try:
                return max(1, min(240, int(value)))
            except (TypeError, ValueError):
                return 30
        if key == "comfyui_photo_wait_seconds":
            try:
                return max(5, min(600, int(value)))
            except (TypeError, ValueError):
                return 90
        if key in {"external_image_api_timeout_seconds", "backup_external_image_api_timeout_seconds"}:
            try:
                return max(20, min(600, int(value)))
            except (TypeError, ValueError):
                return 180
        if key == "photo_action_max_daily":
            try:
                return max(0, min(5, int(value)))
            except (TypeError, ValueError):
                return 1
        if key == "natural_language_photo_generation_max_daily":
            try:
                return max(0, min(100, int(value)))
            except (TypeError, ValueError):
                return 2
        if key in self.PERCENT_PROBABILITY_KEYS:
            try:
                raw = float(value)
                return max(0, min(100, int(round(raw * 100 if 0 <= raw <= 1 else raw))))
            except (TypeError, ValueError):
                if key == "rest_reply_probability":
                    return 18
                if key in {"tts_trigger_probability", "auto_voice_probability"}:
                    return 25
                return 20
        if key in self.INHERIT_PERCENT_PROBABILITY_KEYS:
            try:
                raw = float(value)
                if raw < 0:
                    return -1
                return max(0, min(100, int(round(raw * 100 if 0 <= raw <= 1 else raw))))
            except (TypeError, ValueError):
                return -1
        if key in {"rest_reply_llm_threshold", "group_wakeup_question_threshold", "group_wakeup_cold_group_threshold"}:
            try:
                return max(0, min(100, int(value)))
            except (TypeError, ValueError):
                return 65
        if key == "rest_reply_awake_grace_minutes":
            try:
                return max(0, min(240, int(value)))
            except (TypeError, ValueError):
                return 30
        if key == "proactive_persona_judge_send_threshold":
            try:
                return max(0, min(100, int(value)))
            except (TypeError, ValueError):
                return 62
        if key == "proactive_intensity_preset":
            normalizer = getattr(self.plugin, "_normalize_proactive_intensity_preset", None)
            return normalizer(value) if callable(normalizer) else str(value or "off").strip().lower()
        if key == "quote_skip_short_reply_chars":
            try:
                return max(0, min(120, int(value)))
            except (TypeError, ValueError):
                return 0
        if key == "rest_backlog_max_messages":
            try:
                return max(1, min(12, int(value)))
            except (TypeError, ValueError):
                return 4
        if key == "proactive_reply_context_hours":
            try:
                return max(1, min(72, int(value)))
            except (TypeError, ValueError):
                return 12
        if key == "proactive_persona_judge_cache_minutes":
            try:
                return max(5, min(720, int(value)))
            except (TypeError, ValueError):
                return 180
        if key == "max_proactive_plan_lag_minutes":
            try:
                return max(5, min(1440, int(value)))
            except (TypeError, ValueError):
                return 180
        if key == "web_exploration_min_interval_hours":
            try:
                return max(1, min(168, int(value)))
            except (TypeError, ValueError):
                return 8
        if key == "web_exploration_max_results":
            try:
                return max(3, min(20, int(value)))
            except (TypeError, ValueError):
                return 6
        if key in {
            "check_interval_seconds",
            "daily_token_limit",
            "daily_token_soft_limit",
            "rest_reply_llm_threshold",
            "idle_minutes",
            "min_interval_minutes",
            "max_daily_messages",
            "segmented_proactive_threshold",
            "segmented_proactive_min_segment_chars",
            "segmented_proactive_max_segments",
            "group_conversation_followup_seconds",
            "group_conversation_followup_max_turns",
            "group_interject_min_interval_minutes",
            "group_interject_max_daily",
            "group_scene_recent_limit",
            "group_wakeup_cooldown_seconds",
            "group_wakeup_cold_group_idle_minutes",
            "group_wakeup_generated_keyword_limit",
            "group_wakeup_topic_interest_max_boost",
            "group_wakeup_debounce_pending_penalty",
            "group_wakeup_fatigue_limit",
            "group_wakeup_fatigue_decay_minutes",
            "group_wakeup_log_limit",
            "group_high_intensity_wakeup_window_seconds",
            "group_high_intensity_wakeup_threshold",
            "group_high_intensity_cooldown_seconds",
            "group_high_intensity_merge_seconds",
            "group_high_intensity_max_merge_messages",
            "photo_action_max_daily",
            "comfyui_photo_wait_seconds",
            "local_photo_cpu_busy_percent",
            "local_photo_memory_busy_percent",
            "local_photo_defer_minutes",
            "external_image_api_timeout_seconds",
            "forward_message_max_messages",
            "forward_message_max_chars",
            "forward_message_image_limit",
            "max_group_recent_messages",
            "max_group_slang_terms",
            "memory_refresh_interval_minutes",
            "episode_memory_refresh_messages",
            "episode_memory_refresh_minutes",
            "max_companion_memory_items",
            "max_learned_expression_items",
            "max_dialogue_episodes",
            "user_habit_min_count",
            "user_habit_max_items",
            "emotional_gate_hurt_threshold",
            "emotional_gate_refuse_threshold",
            "emotional_gate_recovery_per_hour",
            "emotional_gate_max_hurt_minutes",
            "bilibili_boredom_min_interval_hours",
            "bilibili_share_min_score",
            "news_min_interval_hours",
            "news_max_items_per_source",
            "news_hot_max_items",
            "ai_daily_check_interval_minutes",
            "external_event_self_link_cooldown_hours",
            "qzone_life_publish_min_interval_hours",
            "qzone_emotional_vent_threshold",
            "qzone_emotional_vent_cooldown_hours",
            "private_reading_min_interval_hours",
            "private_reading_max_photo_count",
            "private_reading_preference_min_ratings",
            "private_reading_preference_max_terms",
            "unanswered_screen_peek_after_minutes",
            "unanswered_screen_peek_cooldown_minutes",
            "creative_chars_per_session",
            "creative_max_active_projects",
            "worldbook_member_inject_limit",
            "atrelay_member_cache_minutes",
            "atrelay_multi_target_limit",
            "private_image_vision_cache_max_items",
            "context_image_caption_max_items",
            "group_slang_web_search_terms",
            "group_slang_web_search_results",
            "auto_voice_max_chars",
            "auto_voice_cooldown_seconds",
        }:
            try:
                if key == "group_high_intensity_max_merge_messages":
                    return max(0, min(50, int(value)))
                parsed = max(0, int(value))
                return parsed
            except (TypeError, ValueError):
                if key == "group_high_intensity_max_merge_messages":
                    return 8
                return 0
        if key == "group_wakeup_interest_probability":
            try:
                raw = float(value)
                return max(0, min(100, int(round(raw * 100 if 0 <= raw <= 1 else raw))))
            except (TypeError, ValueError):
                return 0
        if key == "inbound_message_debounce_seconds":
            try:
                return max(0.0, min(30.0, float(value)))
            except (TypeError, ValueError):
                return 3.0
        if key == "semantic_message_debounce_seconds":
            try:
                return max(0.0, min(15.0, float(value)))
            except (TypeError, ValueError):
                return 8.0
        if key in {"text_message_debounce_seconds", "image_message_debounce_seconds", "forward_message_debounce_seconds"}:
            try:
                return max(0.0, min(15.0, float(value)))
            except (TypeError, ValueError):
                return 0.0 if key != "image_message_debounce_seconds" else 8.0
        if key == "text_message_debounce_max_wait_seconds":
            try:
                return max(0.0, min(30.0, float(value)))
            except (TypeError, ValueError):
                return 12.0
        if key == "message_debounce_max_merge_messages":
            try:
                return max(0, min(30, int(value)))
            except (TypeError, ValueError):
                return 8
        if key in {"smart_message_debounce_model_timeout_seconds", "smart_message_debounce_wait_seconds", "smart_message_debounce_learning_window_seconds"}:
            try:
                upper = 5.0 if key == "smart_message_debounce_model_timeout_seconds" else 30.0
                lower = 0.2 if key == "smart_message_debounce_model_timeout_seconds" else 0.0
                return max(lower, min(upper, float(value)))
            except (TypeError, ValueError):
                if key == "smart_message_debounce_model_timeout_seconds":
                    return 0.8
                return 3.0 if key == "smart_message_debounce_wait_seconds" else 8.0
        if key == "smart_message_debounce_examples_limit":
            try:
                return max(0, min(30, int(value)))
            except (TypeError, ValueError):
                return 8
        if key == "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID":
            return self._single_line(value, 160)
        if key == "SMART_SILENCE_PROVIDER_ID":
            return self._single_line(value, 160)
        if key == "smart_silence_model_timeout_seconds":
            try:
                return max(0.2, min(5.0, float(value)))
            except (TypeError, ValueError):
                return 1.2
        if key == "private_image_vision_wait_seconds":
            try:
                return max(0.0, min(90.0, float(value)))
            except (TypeError, ValueError):
                return 30.0
        if key == "context_image_caption_timeout_seconds":
            try:
                return max(0.0, min(30.0, float(value)))
            except (TypeError, ValueError):
                return 8.0
        if key == "private_image_gif_max_frames":
            try:
                return max(1, min(8, int(value)))
            except (TypeError, ValueError):
                return 4
        if key == "group_repeat_trigger_threshold":
            try:
                return max(3, min(20, int(value)))
            except (TypeError, ValueError):
                return 4
        if key in {
            "group_repeat_follow_probability",
            "group_repeat_interrupt_probability",
            "group_repeat_interrupt_probability_step",
        }:
            try:
                raw = float(value)
                return max(0, min(100, int(round(raw * 100 if 0 <= raw <= 1 else raw))))
            except (TypeError, ValueError):
                return 0
        if key in self.FRACTIONAL_PERCENT_SETTING_KEYS:
            return self._normalize_fractional_percent_value(value)
        if key == "skill_growth_rate":
            try:
                return max(0.1, min(3.0, float(value)))
            except (TypeError, ValueError):
                return 1.0
        if key in {
            "segmented_proactive_interval_min",
            "segmented_proactive_interval_max",
            "segmented_proactive_log_base",
        }:
            try:
                raw = float(value)
                if key == "segmented_proactive_log_base":
                    return max(1.1, min(10.0, raw))
                return max(0.1, min(30.0, raw))
            except (TypeError, ValueError):
                return 1.8 if key == "segmented_proactive_log_base" else 1.5
        if key in {
            "enable_daily_token_soft_limit",
            "enable_bilibili_integration",
            "enable_bilibili_boredom_watch",
            "enable_news_integration",
            "enable_news_boredom_read",
            "enable_news_daily_hot_read",
            "enable_ai_daily_watch",
            "ai_daily_prefer_text_version",
            "enable_external_event_self_link",
            "enable_web_exploration",
            "enable_web_exploration_boredom_search",
            "enable_qzone_integration",
            "enable_qzone_life_publish",
            "enable_qzone_generated_image_publish",
            "enable_qzone_comment_inbox",
            "enable_qzone_emotional_vent_publish",
            "enable_private_reading_integration",
            "enable_private_reading_boredom_read",
            "enable_private_reading_ask_recommendation",
            "enable_private_reading_preference_influence",
            "enable_unanswered_screen_peek_followup",
            "enable_creative_writing",
            "creative_hidden_mode",
            "enable_environment_perception",
            "enable_holiday_perception",
            "enable_platform_perception",
            "enable_model_perception",
            "enable_worldview_perception",
            "enable_lunar_perception",
            "enable_solar_term_perception",
            "enable_almanac_perception",
            "auto_voice_enabled",
            "auto_voice_full_conversion_enabled",
            "enable_humanized_states",
            "inject_passive_states",
            "enable_health_state",
            "enable_hunger_state",
            "enable_cycle_state",
            "enable_worldbook_member_recognition",
            "enable_group_scene_awareness",
            "enable_group_reality_promise_guard",
            "enable_group_wakeup_enhancement",
            "enable_group_high_intensity_mode",
            "enable_group_injection_guard",
            "enable_group_persona_denoise",
            "enable_group_repeat_follow",
            "group_repeat_count_distinct_users_only",
            "enable_forward_message_adaptation",
            "enable_skill_growth_simulation",
            "enable_skill_growth_passive_injection",
            "enable_skill_growth_schedule_influence",
            "forward_message_parse_nested",
            "forward_message_image_vision",
            "enable_message_debounce",
            "enable_smart_message_debounce",
            "enable_recall_enhancement",
            "enable_recall_cancel_reply",
            "enable_recall_message_cache",
            "enable_recall_transcribe_command",
            "enable_forbidden_word_recall",
            "recall_forbidden_word_case_sensitive",
            "enable_semantic_message_debounce",
            "enable_proactive_quote_trigger_message",
            "enable_quote_group_reply",
            "enable_quote_group_interjection",
            "enable_quote_private_proactive",
            "enable_local_photo_load_guard",
            "enable_private_image_self_recognition",
            "enable_context_image_captioning",
            "enable_private_image_gif_enhancement",
            "enable_private_image_vision_cache",
            "enable_segmented_proactive_reply",
            "segmented_proactive_send_as_forward",
            "enable_segmented_proactive_content_cleanup",
            "enable_humanized_states",
            "inject_passive_states",
            "enable_health_state",
            "enable_hunger_state",
            "enable_cycle_state",
            "enable_group_conversation_followup",
            "worldbook_auto_import",
            "worldbook_member_match_aliases",
            "worldbook_self_registration",
            "enable_atrelay_tools",
            "enable_cross_user_memory_bridge",
            "atrelay_require_worldbook_first",
            "cross_user_memory_owner_only",
            "atrelay_sensitive_confirm",
            "enable_atrelay_llm_rewrite",
        }:
            return self._normalize_bool_value(value)
        schema_item = self._schema_item_for_key(key)
        if schema_item:
            return self._normalize_schema_setting_value(value, schema_item)
        return self._single_line(value, 240)

    def _normalize_schema_setting_value(self, value: Any, schema_item: dict[str, Any]) -> Any:
        item_type = str(schema_item.get("type") or "").lower()
        default = schema_item.get("default")
        slider = schema_item.get("slider") if isinstance(schema_item.get("slider"), dict) else {}

        def clamp_number(raw: float) -> float:
            if "min" in slider:
                try:
                    raw = max(float(slider.get("min")), raw)
                except (TypeError, ValueError):
                    pass
            if "max" in slider:
                try:
                    raw = min(float(slider.get("max")), raw)
                except (TypeError, ValueError):
                    pass
            return raw

        if item_type == "bool":
            return self._normalize_bool_value(value)
        if item_type == "int":
            try:
                return int(round(clamp_number(float(value))))
            except (TypeError, ValueError):
                try:
                    return int(default)
                except (TypeError, ValueError):
                    return 0
        if item_type == "float":
            try:
                return clamp_number(float(value))
            except (TypeError, ValueError):
                try:
                    return float(default)
                except (TypeError, ValueError):
                    return 0.0
        if item_type == "list":
            if isinstance(value, list):
                return [self._single_line(item, 160) for item in value if self._single_line(item, 160)]
            text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
            parts = re.split(r"[\n,，、\s]+", text)
            return [self._single_line(item, 160) for item in parts if self._single_line(item, 160)]
        limit = 4000 if item_type == "text" else 1000
        return str(value if value is not None else default or "").strip()[:limit]

    @staticmethod
    def _normalize_fractional_percent_value(value: Any, default: float = 0.0) -> float:
        try:
            raw = float(value)
        except (TypeError, ValueError):
            raw = default
        if raw > 1.0:
            raw /= 100.0
        return max(0.0, min(1.0, raw))

    @staticmethod
    def _normalize_multiline_source_config(value: Any, *, limit: int = 4000) -> str:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if text and "\n" not in text:
            markers = list(re.finditer(r"(?:^|\s+)(#?\s*[^|\n]+?)\|(?=(?:https?://|bilibili:|bvid:))", text, flags=re.I))
            if len(markers) > 1:
                recovered: list[str] = []
                for index, match in enumerate(markers):
                    start = match.end()
                    end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
                    name = str(match.group(1) or "").strip()
                    target = text[start:end].strip()
                    if name and target:
                        recovered.append(f"{name}|{target}")
                if recovered:
                    text = "\n".join(recovered)
        lines: list[str] = []
        for raw_line in text.split("\n"):
            line = raw_line.strip()
            if line:
                lines.append(line)
        return "\n".join(lines)[:limit].strip()

    def _schema_key_index(self) -> dict[str, Any]:
        cached = self._schema_key_index_cache
        if cached is not None:
            return cached
        index: dict[str, Any] = {
            "all": set(),
            "public": set(),
            "bool": set(),
            "public_bool": set(),
            "provider": set(),
            "public_provider": set(),
            "group": {},
            "item": {},
        }

        def visit(items: dict[str, Any], group_key: str = "") -> None:
            for raw_key, item in items.items():
                if not isinstance(item, dict):
                    continue
                key = str(raw_key)
                item_type = str(item.get("type") or "")
                if item_type == "object" and isinstance(item.get("items"), dict):
                    visit(item["items"], key)
                    continue
                hidden = bool(item.get("invisible"))
                index["group"][key] = group_key
                index["item"][key] = item
                index["all"].add(key)
                if not hidden:
                    index["public"].add(key)
                if item_type == "bool":
                    index["bool"].add(key)
                    if not hidden:
                        index["public_bool"].add(key)
                if self._schema_item_is_provider(key, item):
                    index["provider"].add(key)
                    if not hidden:
                        index["public_provider"].add(key)

        try:
            raw = json.loads(Path(__file__).with_name("_conf_schema.json").read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                visit(raw)
        except Exception as exc:
            logger.debug("[PrivateCompanionPage] 读取配置 schema 索引失败: %s", exc)
        self._schema_key_index_cache = index
        return index

    @staticmethod
    def _schema_item_is_provider(key: str, item: dict[str, Any]) -> bool:
        if item.get("_special") == "select_provider":
            return True
        return key.endswith("PROVIDER_ID") or key.endswith("_provider_id") or key.endswith("provider_id")

    def _schema_setting_keys(self, *, public_only: bool = False) -> set[str]:
        index = self._schema_key_index()
        key_name = "public" if public_only else "all"
        provider_key_name = "public_provider" if public_only else "provider"
        return set(index[key_name]) - set(index[provider_key_name])

    def _schema_provider_keys(self, *, public_only: bool = False) -> set[str]:
        index = self._schema_key_index()
        return set(index["public_provider" if public_only else "provider"])

    def _schema_bool_keys(self) -> set[str]:
        return set(self._schema_key_index()["bool"])

    def _schema_group_for_key(self, key: str) -> str:
        group_map = self._schema_key_index().get("group")
        return str(group_map.get(key, "") if isinstance(group_map, dict) else "")

    def _schema_item_for_key(self, key: str) -> dict[str, Any]:
        item_map = self._schema_key_index().get("item")
        item = item_map.get(key) if isinstance(item_map, dict) else None
        return item if isinstance(item, dict) else {}

    @classmethod
    def _normalize_worldbook_member_id(cls, value: Any) -> str:
        text = cls._single_line(value, 80)
        if not text:
            return ""
        lowered = text.lower()
        if lowered.startswith("bili:") or lowered.startswith("bilibili:"):
            digits = re.sub(r"\D+", "", lowered.split(":", 1)[1])
            return f"bili:{digits}" if digits else ""
        if lowered.startswith("bili_live_"):
            return re.sub(r"[^A-Za-z0-9_:-]+", "_", text)[:80]
        if text.isdigit():
            return text
        return re.sub(r"[^A-Za-z0-9_:-]+", "_", text)[:80]

    @staticmethod
    def _worldbook_member_id_valid(user_id: str) -> bool:
        if user_id.isdigit():
            return len(user_id) >= 5
        lowered = user_id.lower()
        if lowered.startswith("bili:"):
            return bool(re.fullmatch(r"bili:\d{2,}", lowered))
        if lowered.startswith("bili_live_"):
            return bool(re.fullmatch(r"bili_live_[A-Za-z0-9_-]{6,64}", user_id))
        return False

    def _bind_worldbook_external_member_locked(
        self,
        profiles: dict[str, Any],
        source_id: str,
        target_id: str,
        source_profile: dict[str, Any],
    ) -> dict[str, Any]:
        target = profiles.get(target_id)
        if not isinstance(target, dict):
            target = {
                "user_id": target_id,
                "name": self._single_line(source_profile.get("name"), 80) or target_id,
                "gender": self._single_line(source_profile.get("gender"), 40),
                "aliases": [],
                "content": "",
                "identity_note": f"QQ {target_id}，由外部身份绑定创建。",
                "boundary_note": "",
                "important_memories": [],
                "enabled": True,
                "priority": 120,
                "source_entries": [],
                "observed_names": [],
            }
            profiles[target_id] = target
        target["user_id"] = target_id
        target["identity_type"] = "qq"
        source_gender = self._single_line(source_profile.get("gender"), 40)
        if source_gender and not self._single_line(target.get("gender"), 40):
            target["gender"] = source_gender
        live_names = self._worldbook_string_list(source_profile.get("observed_names"), limit=12, item_limit=40)
        live_name = self._single_line(source_profile.get("name"), 40)
        if live_name and live_name != target_id:
            live_names.insert(0, live_name)
        aliases = self._worldbook_string_list(target.get("aliases"), limit=30, item_limit=40)
        for alias in live_names:
            if alias and alias != target_id and alias not in aliases:
                aliases.append(alias)
        target["aliases"] = aliases[:30]

        external_ids = self._worldbook_string_list(target.get("external_ids"), limit=20, item_limit=80)
        if source_id not in external_ids:
            external_ids.insert(0, source_id)
        target["external_ids"] = external_ids[:20]
        target["linked_bili_profile_id"] = source_id
        target["manual_edit_ts"] = time.time()

        for field, limit in (("content", 2000), ("identity_note", 2000), ("boundary_note", 1200)):
            source_text = str(source_profile.get(field) or "").strip()
            target_text = str(target.get(field) or "").strip()
            if source_text and source_text not in target_text:
                glue = "\n" if target_text else ""
                target[field] = (target_text + glue + source_text)[:limit]

        source_memories = self._normalize_important_memories(source_profile.get("important_memories"))
        target_memories = self._normalize_important_memories(target.get("important_memories"))
        seen = {self._single_line(item.get("content"), 160) for item in target_memories if isinstance(item, dict)}
        for memory in source_memories:
            content = self._single_line(memory.get("content"), 160)
            if content and content not in seen:
                target_memories.append(memory)
                seen.add(content)
        target["important_memories"] = target_memories[:30]

        source_entries = self._worldbook_string_list(target.get("source_entries"), limit=30, item_limit=80)
        for entry in self._worldbook_string_list(source_profile.get("source_entries"), limit=12, item_limit=80):
            if entry not in source_entries:
                source_entries.append(entry)
        if "live_stream_companion" not in source_entries:
            source_entries.append("live_stream_companion")
        target["source_entries"] = source_entries[:30]

        source_profile["enabled"] = False
        source_profile["linked_qq_user_id"] = target_id
        source_profile["merged_into_user_id"] = target_id
        source_profile["manual_edit_ts"] = time.time()
        self._merge_live_viewer_activity_for_worldbook_bind(source_id, target_id, live_names)
        return {"source_user_id": source_id, "target_user_id": target_id}

    def _worldbook_string_list(self, value: Any, *, limit: int = 20, item_limit: int = 60) -> list[str]:
        raw_items: list[Any]
        if isinstance(value, list):
            raw_items = value
        elif isinstance(value, str):
            raw_items = re.split(r"[\n,，;；]+", value)
        else:
            raw_items = []
        result: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            text = self._single_line(item, item_limit)
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
            if len(result) >= limit:
                break
        return result

    def _merge_live_viewer_activity_for_worldbook_bind(self, source_id: str, target_id: str, live_names: list[str]) -> None:
        store = self.plugin.data.get("live_stream_companion")
        if not isinstance(store, dict):
            return
        activity = store.get("viewer_activity")
        if not isinstance(activity, dict):
            return
        target_key = f"user:{target_id}"
        source_keys = [f"user:{source_id}"]
        source_keys.extend(f"live:{name}" for name in live_names if name)
        target = activity.setdefault(target_key, {"viewer_key": target_key, "user_id": target_id, "recent_events": [], "recent_danmaku": [], "event_counts": {}})
        if not isinstance(target, dict):
            target = {"viewer_key": target_key, "user_id": target_id, "recent_events": [], "recent_danmaku": [], "event_counts": {}}
            activity[target_key] = target
        target["viewer_key"] = target_key
        target["user_id"] = target_id
        aliases = target.setdefault("live_usernames", [])
        if not isinstance(aliases, list):
            aliases = []
            target["live_usernames"] = aliases
        for name in live_names:
            if name and name not in aliases:
                aliases.insert(0, name)
        del aliases[8:]
        for key in source_keys:
            item = activity.get(key)
            if not isinstance(item, dict) or item is target:
                continue
            self._merge_activity_item(target, item)
            activity.pop(key, None)

    @staticmethod
    def _merge_activity_item(target: dict[str, Any], source: dict[str, Any]) -> None:
        def as_float(value: Any, default: float = 0.0) -> float:
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        def as_int(value: Any, default: int = 0) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        target["total_events"] = as_int(target.get("total_events")) + as_int(source.get("total_events"))
        if source.get("display_name") and not target.get("display_name"):
            target["display_name"] = source.get("display_name")
        if source.get("live_username") and not target.get("live_username"):
            target["live_username"] = source.get("live_username")
        target["first_seen"] = min(as_float(target.get("first_seen"), time.time()), as_float(source.get("first_seen"), time.time()))
        target["last_seen"] = max(as_float(target.get("last_seen")), as_float(source.get("last_seen")))
        counts = target.setdefault("event_counts", {})
        if isinstance(counts, dict) and isinstance(source.get("event_counts"), dict):
            for key, value in source["event_counts"].items():
                counts[key] = as_int(counts.get(key)) + as_int(value)
        for field, limit in (("recent_events", 12), ("recent_danmaku", 8)):
            rows = []
            for row in [*(target.get(field) if isinstance(target.get(field), list) else []), *(source.get(field) if isinstance(source.get(field), list) else [])]:
                if isinstance(row, dict):
                    rows.append(row)
            rows.sort(key=lambda row: as_float(row.get("ts")), reverse=True)
            target[field] = rows[:limit]

    def _worldbook_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        profiles = data.get("worldbook_member_profiles") if isinstance(data.get("worldbook_member_profiles"), dict) else {}
        groups = data.get("worldbook_group_profiles") if isinstance(data.get("worldbook_group_profiles"), dict) else {}
        entries = data.get("worldbook_entries") if isinstance(data.get("worldbook_entries"), list) else []
        state = data.get("worldbook_import_state") if isinstance(data.get("worldbook_import_state"), dict) else {}
        member_count = self._int(data.get("worldbook_member_profile_count")) if "worldbook_member_profile_count" in data else 0
        enabled_member_count = self._int(data.get("worldbook_enabled_member_profile_count")) if "worldbook_enabled_member_profile_count" in data else 0
        pending_observation_total = self._int(data.get("worldbook_pending_observation_total")) if "worldbook_pending_observation_total" in data else 0
        group_count = self._int(data.get("worldbook_group_profile_count")) if "worldbook_group_profile_count" in data else 0
        entry_count = self._int(data.get("worldbook_entry_count")) if "worldbook_entry_count" in data else len(entries)
        profile_items = []
        for user_id, item in profiles.items():
            if not isinstance(item, dict):
                continue
            aliases = item.get("aliases") if isinstance(item.get("aliases"), list) else []
            observed = item.get("observed_names") if isinstance(item.get("observed_names"), list) else []
            external_ids = item.get("external_ids") if isinstance(item.get("external_ids"), list) else []
            memories = self._normalize_important_memories(item.get("important_memories"))
            pending = item.get("pending_observations") if isinstance(item.get("pending_observations"), list) else []
            pending_items = []
            for raw in pending[:8]:
                if not isinstance(raw, dict):
                    continue
                pending_items.append(
                    {
                        "id": self._single_line(raw.get("id"), 40),
                        "title": self._single_line(raw.get("title"), 60) or "群聊观察",
                        "content": self._single_line(raw.get("content"), 260),
                        "evidence": self._single_line(raw.get("evidence"), 160),
                        "group_id": self._single_line(raw.get("group_id"), 40),
                        "weight": self._clamp_int(raw.get("weight"), 35, 0, 100),
                        "count": self._clamp_int(raw.get("count"), 1, 1, 999),
                        "created_at": self.plugin._format_timestamp_elapsed(raw.get("created_at", 0)),
                    }
                )
            profile_items.append(
                {
                    "user_id": self._single_line(user_id, 40),
                    "identity_type": self._single_line(item.get("identity_type") or ("qq" if str(user_id).isdigit() else "external"), 20),
                    "name": self._single_line(item.get("name"), 60),
                    "gender": self._single_line(item.get("gender"), 40),
                    "enabled": bool(item.get("enabled", True)),
                    "priority": item.get("priority", 120),
                    "aliases": [self._single_line(alias, 40) for alias in aliases if self._single_line(alias, 40)],
                    "observed_names": [self._single_line(name, 40) for name in observed if self._single_line(name, 40)],
                    "external_ids": [self._single_line(ext, 80) for ext in external_ids if self._single_line(ext, 80)],
                    "linked_qq_user_id": self._single_line(item.get("linked_qq_user_id") or item.get("merged_into_user_id"), 40),
                    "linked_bili_profile_id": self._single_line(item.get("linked_bili_profile_id"), 80),
                    "auto_registration_pending": bool(item.get("auto_registration_pending", False)),
                    "content": self._single_line(item.get("content"), 260),
                    "identity_note": self._single_line(item.get("identity_note") or item.get("note") or item.get("content"), 500),
                    "boundary_note": self._single_line(item.get("boundary_note"), 500),
                    "important_memories": memories,
                    "pending_observations": pending_items,
                    "pending_observation_count": len(pending_items),
                    "source_entries": item.get("source_entries") if isinstance(item.get("source_entries"), list) else [],
                    "note": self._single_line(item.get("note"), 500),
                }
            )
        profile_items.sort(key=lambda item: (not item.get("enabled", True), item.get("name") or item.get("user_id")))
        if "worldbook_member_profile_count" not in data:
            member_count = len(profile_items)
            enabled_member_count = sum(1 for item in profile_items if item.get("enabled", True))
            pending_observation_total = sum(self._clamp_int(item.get("pending_observation_count"), 0, 0, 999) for item in profile_items)
        group_items = [
            {
                "group_id": self._single_line(group_id, 40),
                "name": self._single_line(item.get("name"), 60),
                "enabled": bool(item.get("enabled", True)),
                "priority": item.get("priority", 110),
                "content": self._single_line(item.get("content"), 220),
            }
            for group_id, item in groups.items()
            if isinstance(item, dict)
        ]
        if "worldbook_group_profile_count" not in data:
            group_count = len(group_items)
        return {
            "enabled": bool(getattr(self.plugin, "enable_worldbook_member_recognition", False)),
            "auto_import": bool(getattr(self.plugin, "worldbook_auto_import", False)),
            "match_aliases": bool(getattr(self.plugin, "worldbook_member_match_aliases", False)),
            "self_registration": bool(getattr(self.plugin, "worldbook_self_registration", False)),
            "self_registration_block_word_count": len(
                getattr(self.plugin, "worldbook_self_registration_block_words", [])
                if isinstance(getattr(self.plugin, "worldbook_self_registration_block_words", []), list)
                else []
            ),
            "auto_pending_observations": bool(getattr(self.plugin, "worldbook_auto_pending_observations", False)),
            "inject_limit": getattr(self.plugin, "worldbook_member_inject_limit", 0),
            "entry_count": entry_count,
            "member_count": member_count,
            "enabled_member_count": enabled_member_count,
            "pending_observation_total": pending_observation_total,
            "group_count": group_count,
            "last_import": self.plugin._format_timestamp_elapsed(state.get("last_import_at", 0)),
            "source_files": state.get("source_files") if isinstance(state.get("source_files"), list) else [],
            "members": profile_items[:120],
            "groups": group_items[:80],
        }

    def _normalize_important_memories(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        memories: list[dict[str, Any]] = []
        for raw in value[:12]:
            if not isinstance(raw, dict):
                continue
            content = str(raw.get("content") or "").strip()[:500]
            if not content:
                continue
            privacy = self._single_line(raw.get("privacy"), 20).lower()
            if privacy not in {"public", "private", "internal"}:
                privacy = "internal"
            memories.append(
                {
                    "title": self._single_line(raw.get("title"), 60),
                    "content": content,
                    "weight": self._clamp_int(raw.get("weight"), 50, 0, 100),
                    "privacy": privacy,
                    "source": self._single_line(raw.get("source"), 40),
                    "enabled": bool(raw.get("enabled", True)),
                    "updated_at": float(raw.get("updated_at") or time.time()),
                }
            )
        memories.sort(key=lambda item: (item.get("enabled", True), item.get("weight", 50), item.get("updated_at", 0)), reverse=True)
        return memories[:8]

    def _livingmemory_summary(self) -> dict[str, Any]:
        try:
            available = bool(self.plugin._livingmemory_available())
        except Exception:
            available = False
        try:
            plugin_dir = str(self.plugin._livingmemory_plugin_dir())
        except Exception:
            plugin_dir = ""
        try:
            status = self.plugin._format_livingmemory_status()
        except Exception:
            status = "LivingMemory：状态探测失败，已跳过协同。"
        return {
            "enabled": bool(available and getattr(self.plugin, "enable_livingmemory_integration", False)),
            "available": available,
            "tool_name": getattr(self.plugin, "livingmemory_tool_name", ""),
            "plugin_dir": plugin_dir,
            "status": status,
        }

    def _screen_companion_available(self) -> bool:
        getter = getattr(self.plugin, "_get_screen_companion_plugin", None)
        if callable(getter):
            try:
                return getter() is not None
            except Exception:
                return False
        return False

    def _screen_companion_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        available = self._screen_companion_available()
        context = data.get("screen_diary_context") if isinstance(data.get("screen_diary_context"), dict) else {}
        return {
            "enabled": bool(available and getattr(self.plugin, "enable_yesterday_screen_diary_context", False)),
            "available": available,
            "source": context.get("source", ""),
            "source_date": context.get("source_date", ""),
            "context_available": bool(context.get("available")),
            "summary_chars": len(str(context.get("summary") or "")),
        }

    def _bilibili_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        state = data.get("bilibili_integration") if isinstance(data.get("bilibili_integration"), dict) else {}
        try:
            available = bool(getattr(self.plugin, "_bilibili_available", lambda: False)())
        except Exception:
            available = False
        latest = None
        try:
            latest_getter = getattr(self.plugin, "_latest_bilibili_video_candidate", None)
            latest = latest_getter(include_memory_api=False) if callable(latest_getter) else None
        except Exception:
            latest = None
        try:
            watch_log = str(getattr(self.plugin, "_bilibili_watch_log_file", lambda: "")())
        except Exception:
            watch_log = ""
        try:
            memory_checker = getattr(self.plugin, "_bilibili_memory_api_available", None)
            if callable(memory_checker):
                try:
                    memory_api_available = bool(memory_checker(allow_probe=False))
                except TypeError:
                    memory_api_available = bool(memory_checker())
            else:
                memory_api_available = False
        except Exception:
            memory_api_available = False
        return {
            "enabled": bool(available and getattr(self.plugin, "enable_bilibili_integration", False)),
            "boredom_watch_enabled": bool(available and getattr(self.plugin, "enable_bilibili_boredom_watch", False)),
            "available": available,
            "memory_api_available": memory_api_available,
            "watch_log": watch_log,
            "last_boredom_watch_at": self.plugin._format_timestamp_elapsed(state.get("last_boredom_watch_at", 0)),
            "last_status": state.get("last_boredom_watch_status", ""),
            "latest_video": latest if isinstance(latest, dict) else {},
        }

    def _news_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        state = data.get("news_integration") if isinstance(data.get("news_integration"), dict) else {}
        digest = state.get("last_digest") if isinstance(state.get("last_digest"), dict) else {}
        latest_items = state.get("latest_items") if isinstance(state.get("latest_items"), list) else []
        ai_daily = state.get("ai_daily") if isinstance(state.get("ai_daily"), dict) else {}
        ai_digest = ai_daily.get("last_digest") if isinstance(ai_daily.get("last_digest"), dict) else {}
        ai_digest_items = ai_digest.get("items") if isinstance(ai_digest.get("items"), list) else []
        ai_digest_first_item = ai_digest_items[0] if ai_digest_items and isinstance(ai_digest_items[0], dict) else {}
        try:
            ai_text_chars = max(0, int(ai_daily.get("last_text_chars") or 0))
        except (TypeError, ValueError):
            ai_text_chars = 0
        if not ai_text_chars and ai_digest_first_item:
            ai_text_chars = len(str(ai_digest_first_item.get("article_text") or ""))
        try:
            ai_subtitle_chars = max(0, int(ai_daily.get("last_video_subtitle_chars") or 0))
        except (TypeError, ValueError):
            ai_subtitle_chars = 0
        if not ai_subtitle_chars and ai_digest_first_item:
            ai_subtitle_chars = len(str(ai_digest_first_item.get("video_subtitle_text") or ""))
        try:
            ai_video_context_chars = max(0, int(ai_daily.get("last_video_context_chars") or 0))
        except (TypeError, ValueError):
            ai_video_context_chars = 0
        if not ai_video_context_chars and ai_digest_first_item:
            ai_video_context_chars = len(str(ai_digest_first_item.get("video_context_text") or ""))
        try:
            ai_video_duration = max(0, int(ai_daily.get("last_video_duration") or ai_digest_first_item.get("video_duration") or 0))
        except (TypeError, ValueError):
            ai_video_duration = 0
        ai_video_tags_raw = ai_daily.get("last_video_tags") if isinstance(ai_daily.get("last_video_tags"), list) else ai_digest_first_item.get("video_tags")
        ai_video_tags = [
            self._single_line(tag, 40)
            for tag in ai_video_tags_raw
            if self._single_line(tag, 40)
        ] if isinstance(ai_video_tags_raw, list) else []
        ai_video_comments_raw = ai_daily.get("last_video_hot_comments") if isinstance(ai_daily.get("last_video_hot_comments"), list) else ai_digest_first_item.get("video_hot_comments")
        ai_video_comments = [
            self._single_line(comment, 120)
            for comment in ai_video_comments_raw
            if self._single_line(comment, 120)
        ] if isinstance(ai_video_comments_raw, list) else []
        try:
            source_count = len(getattr(self.plugin, "_news_source_items", lambda: [])())
        except Exception:
            source_count = 0
        return {
            "enabled": bool(getattr(self.plugin, "enable_news_integration", False)),
            "boredom_read_enabled": bool(getattr(self.plugin, "enable_news_boredom_read", False)),
            "daily_hot_enabled": bool(getattr(self.plugin, "enable_news_daily_hot_read", False)),
            "ai_daily_enabled": bool(getattr(self.plugin, "enable_ai_daily_watch", False)),
            "source_count": source_count,
            "last_read_at": self.plugin._format_timestamp_elapsed(state.get("last_read_at", 0)),
            "last_status": self._single_line(state.get("last_status"), 80),
            "ai_daily": {
                "status": self._single_line(ai_daily.get("status"), 80),
                "date": self._single_line(ai_daily.get("date"), 20),
                "last_checked_at": self.plugin._format_timestamp_elapsed(ai_daily.get("last_checked_at", 0)),
                "last_success_date": self._single_line(ai_daily.get("last_success_date"), 20),
                "last_source_name": self._single_line(ai_daily.get("last_source_name"), 40),
                "last_source_author": self._single_line(ai_daily.get("last_source_author"), 60),
                "last_source_mid": self._single_line(ai_daily.get("last_source_mid"), 40),
                "last_source_schedule": self._single_line(ai_daily.get("last_source_schedule"), 10),
                "last_video_title": self._single_line(ai_daily.get("last_video_title"), 120),
                "last_video_link": self._single_line(ai_daily.get("last_video_link"), 400),
                "last_video_owner_name": self._single_line(ai_daily.get("last_video_owner_name") or ai_digest_first_item.get("video_owner_name"), 80),
                "last_video_tname": self._single_line(ai_daily.get("last_video_tname") or ai_digest_first_item.get("video_tname"), 60),
                "last_video_duration": ai_video_duration,
                "last_video_context_chars": ai_video_context_chars,
                "last_video_tags": ai_video_tags[:10],
                "last_video_hot_comments": ai_video_comments[:5],
                "last_text_link": self._single_line(ai_daily.get("last_text_link"), 400),
                "last_text_readable": bool(ai_daily.get("last_text_readable")) if "last_text_readable" in ai_daily else bool(ai_digest_first_item.get("article_readable") and ai_digest_first_item.get("article_text")),
                "last_text_chars": ai_text_chars,
                "last_video_subtitle_readable": bool(ai_daily.get("last_video_subtitle_readable")) if "last_video_subtitle_readable" in ai_daily else bool(ai_digest_first_item.get("video_subtitle_readable") and ai_digest_first_item.get("video_subtitle_text")),
                "last_video_subtitle_chars": ai_subtitle_chars,
                "last_video_subtitle_status": self._single_line(ai_daily.get("last_video_subtitle_status") or ai_digest_first_item.get("video_subtitle_status"), 40),
                "last_read_basis": self._single_line(ai_daily.get("last_read_basis"), 40),
                "sources": [
                    {
                        "key": self._single_line(item.get("key"), 80),
                        "name": self._single_line(item.get("name"), 40),
                        "author_name": self._single_line(item.get("author_name"), 60),
                        "mid": self._single_line(item.get("mid"), 40),
                        "schedule": self._single_line(item.get("schedule"), 10),
                    }
                    for item in (ai_daily.get("sources") if isinstance(ai_daily.get("sources"), list) else [])
                    if isinstance(item, dict)
                ],
                "source_states": {
                    self._single_line(key, 80): {
                        "name": self._single_line(value.get("name"), 40),
                        "author_name": self._single_line(value.get("author_name"), 60),
                        "mid": self._single_line(value.get("mid"), 40),
                        "schedule": self._single_line(value.get("schedule"), 10),
                        "status": self._single_line(value.get("status"), 80),
                        "last_checked_at": self.plugin._format_timestamp_elapsed(value.get("last_checked_at", 0)),
                        "last_success_date": self._single_line(value.get("last_success_date"), 20),
                        "last_video_title": self._single_line(value.get("last_video_title"), 120),
                    }
                    for key, value in (ai_daily.get("source_states") if isinstance(ai_daily.get("source_states"), dict) else {}).items()
                    if isinstance(value, dict)
                },
                "topic": self._single_line(ai_digest.get("topic"), 60),
                "headline": self._single_line(ai_digest.get("headline"), 120),
            },
            "last_digest": {
                "topic": self._single_line(digest.get("topic"), 60),
                "headline": self._single_line(digest.get("headline"), 120),
                "source": self._single_line(digest.get("selected_source"), 40),
                "impression": self._sanitize_news_text(
                    digest.get("impression"),
                    180,
                    fallback="这条新闻的正文解析异常，建议稍后重新阅读。",
                ),
                "link": self._single_line(digest.get("selected_link"), 400),
            },
            "latest_items": [
                {
                    "source": self._single_line(item.get("source"), 40),
                    "title": self._single_line(item.get("title"), 120),
                    "summary": self._sanitize_news_text(
                        item.get("summary"),
                        160,
                        fallback="这条新闻摘要暂时解析异常，建议打开原文查看。",
                    ),
                    "link": self._single_line(item.get("link"), 400),
                }
                for item in latest_items[:8]
                if isinstance(item, dict)
            ],
        }

    def _web_exploration_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        state = data.get("web_exploration") if isinstance(data.get("web_exploration"), dict) else {}
        digest = state.get("last_digest") if isinstance(state.get("last_digest"), dict) else {}
        notes = state.get("notes") if isinstance(state.get("notes"), list) else []
        history = self._browsing_history_entries(data)
        custom_available = bool(getattr(self.plugin, "_custom_web_exploration_search_configured", lambda: False)())
        try:
            astrbot_available = bool(getattr(self.plugin, "_astrbot_any_web_search_available", lambda: False)())
        except Exception:
            astrbot_available = False
        available = bool(custom_available or astrbot_available)
        search_backend = "custom" if custom_available else ("astrbot" if astrbot_available else "none")
        return {
            "enabled": bool(getattr(self.plugin, "enable_web_exploration", False)),
            "boredom_search_enabled": bool(getattr(self.plugin, "enable_web_exploration_boredom_search", False)),
            "available": available,
            "search_backend": search_backend,
            "custom_search_enabled": custom_available,
            "astrbot_search_available": astrbot_available,
            "last_explore_at": self.plugin._format_timestamp_elapsed(state.get("last_explore_at", 0)),
            "last_status": self._single_line(state.get("last_status"), 80),
            "last_query": {
                "query": self._single_line((state.get("last_query") or {}).get("query") if isinstance(state.get("last_query"), dict) else "", 80),
                "reason": self._single_line((state.get("last_query") or {}).get("reason") if isinstance(state.get("last_query"), dict) else "", 120),
                "topic": self._single_line((state.get("last_query") or {}).get("topic") if isinstance(state.get("last_query"), dict) else "", 20),
            },
            "last_digest": {
                "topic": self._single_line(digest.get("topic"), 80),
                "note": self._single_line(digest.get("note"), 220),
                "source_title": self._single_line(digest.get("source_title"), 120),
                "source_url": self._single_line(digest.get("source_url"), 400),
            },
            "note_count": len(notes),
            "history_count": len(history),
            "history": history,
            "recent_notes": [
                {
                    "topic": self._single_line(item.get("topic"), 80),
                    "note": self._single_line(item.get("note"), 180),
                    "query": self._single_line(item.get("query"), 80),
                    "created_at": self.plugin._format_timestamp_elapsed(item.get("created_ts", 0)),
                }
                for item in notes[-8:]
                if isinstance(item, dict)
            ],
        }

    def _browsing_history_entries(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        news_state = data.get("news_integration") if isinstance(data.get("news_integration"), dict) else {}
        news_digest = news_state.get("last_digest") if isinstance(news_state.get("last_digest"), dict) else {}
        news_digests = news_state.get("digests") if isinstance(news_state.get("digests"), list) else []
        news_items = [item for item in news_digests if isinstance(item, dict)]
        if news_digest and not any(
            self._single_line(item.get("selected_key"), 32) == self._single_line(news_digest.get("selected_key"), 32)
            and self._float(item.get("created_ts")) == self._float(news_digest.get("created_ts"))
            for item in news_items
        ):
            news_items.append(news_digest)
        for news_digest in news_items:
            headline = self._single_line(news_digest.get("headline") or news_digest.get("topic"), 120)
            impression = self._single_line(news_digest.get("impression"), 1000)
            selected_source = self._single_line(news_digest.get("selected_source"), 40)
            selected_link = self._single_line(news_digest.get("selected_link"), 400)
            created_ts = self._float(news_digest.get("created_ts"))
            entries.append(
                {
                    "_ts": created_ts,
                    "source": "news",
                    "source_label": "新闻阅读",
                    "date": self.plugin._format_timestamp_elapsed(created_ts) or "今日新闻",
                    "generated_at": self.plugin._format_timestamp_elapsed(created_ts),
                    "title": headline or "新闻阅读",
                    "query": "",
                    "intro": impression or "这次新闻阅读没有留下明显印象。",
                    "content": "\n\n".join(
                        part
                        for part in (
                            f"新闻见闻：{headline}" if headline else "",
                            impression,
                            f"来源：{selected_source}" if selected_source else "",
                            f"链接：{selected_link}" if selected_link else "",
                        )
                        if part
                    ) or "这次新闻阅读没有留下正文。",
                    "source_title": selected_source,
                    "source_url": selected_link,
                    "tags": ["新闻阅读"],
                }
            )
        web_state = data.get("web_exploration") if isinstance(data.get("web_exploration"), dict) else {}
        web_notes = web_state.get("notes") if isinstance(web_state.get("notes"), list) else []
        web_items = [note for note in web_notes if isinstance(note, dict)]
        last_digest = web_state.get("last_digest") if isinstance(web_state.get("last_digest"), dict) else {}
        if last_digest:
            last_key = "|".join(
                self._single_line(last_digest.get(key), 120)
                for key in ("query", "topic", "created_ts")
            )
            if not any(
                "|".join(
                    self._single_line(item.get(key), 120)
                    for key in ("query", "topic", "created_ts")
                ) == last_key
                for item in web_items
            ):
                web_items.append(last_digest)
        for item in web_items:
            topic = self._single_line(item.get("topic"), 100)
            query = self._single_line(item.get("query"), 100)
            note = self._single_line(
                item.get("note")
                or item.get("summary")
                or item.get("impression")
                or item.get("content"),
                1000,
            )
            source_title = self._single_line(item.get("source_title"), 120)
            source_url = self._single_line(item.get("source_url"), 400)
            created_ts = self._float(item.get("created_ts"))
            result_lines = []
            raw_results = item.get("results") if isinstance(item.get("results"), list) else []
            for result in raw_results[:4]:
                if not isinstance(result, dict):
                    continue
                title = self._single_line(result.get("title"), 100)
                snippet = self._single_line(result.get("snippet"), 180)
                if title and snippet:
                    result_lines.append(f"{title}：{snippet}")
                elif title:
                    result_lines.append(title)
                elif snippet:
                    result_lines.append(snippet)
            result_excerpt = self._single_line("；".join(result_lines), 1000)
            if not note:
                note = result_excerpt
            entries.append(
                {
                    "_ts": created_ts,
                    "source": self._single_line(item.get("source"), 40) or "web_exploration",
                    "source_label": self._single_line(item.get("source_label"), 40) or "主动搜索",
                    "date": self.plugin._format_timestamp_elapsed(created_ts) or "某次搜索",
                    "generated_at": self.plugin._format_timestamp_elapsed(created_ts),
                    "title": topic or query or "主动搜索",
                    "query": query,
                    "intro": note or "这次搜索没有留下明显印象。",
                    "content": "\n\n".join(
                        part
                        for part in (
                            f"搜索词：{query}" if query else "",
                            f"搜索动机：{self._single_line(item.get('reason'), 160)}" if self._single_line(item.get("reason"), 160) else "",
                            f"笔记：{note}" if note else "",
                            f"结果摘录：{result_excerpt}" if result_excerpt and result_excerpt != note else "",
                            f"主要来源：{source_title}" if source_title else "",
                            f"链接：{source_url}" if source_url else "",
                        )
                        if part
                    ) or "这次主动搜索没有留下正文。",
                    "source_title": source_title,
                    "source_url": source_url,
                    "tags": ["主动搜索"],
                }
            )
        entries.sort(key=lambda item: self._float(item.get("_ts")))
        for item in entries:
            item.pop("_ts", None)
        return entries

    def _qzone_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        state = data.get("qzone_integration") if isinstance(data.get("qzone_integration"), dict) else {}
        available = bool(
            callable(getattr(self.plugin, "_qzone_get_cookies", None))
            and callable(getattr(self.plugin, "_qzone_query_feeds", None))
        )
        enabled = bool(available and getattr(self.plugin, "enable_qzone_integration", False))
        return {
            "enabled": enabled,
            "life_publish_enabled": bool(enabled and getattr(self.plugin, "enable_qzone_life_publish", False)),
            "comment_inbox_enabled": bool(enabled and getattr(self.plugin, "enable_qzone_comment_inbox", False)),
            "emotional_vent_enabled": bool(
                enabled
                and getattr(self.plugin, "enable_emotion_simulation", False)
                and getattr(self.plugin, "enable_qzone_emotional_vent_publish", False)
            ),
            "available": available,
            "last_life_publish_at": self.plugin._format_timestamp_elapsed(state.get("last_life_publish_at", 0)),
            "last_status": state.get("last_life_publish_status", ""),
            "last_text": state.get("last_life_publish_text", ""),
            "generated_image_enabled": bool(enabled and getattr(self.plugin, "enable_qzone_generated_image_publish", False)),
            "generated_image_probability": self._float(getattr(self.plugin, "qzone_generated_image_probability", 0)),
            "last_life_publish_images": self._int(state.get("last_life_publish_images")),
            "last_life_publish_generated_image_status": state.get("last_life_publish_generated_image_status", ""),
            "last_life_publish_generated_image_note": state.get("last_life_publish_generated_image_note", ""),
            "last_life_publish_generated_image_backend": state.get("last_life_publish_generated_image_backend", ""),
            "last_life_publish_generated_image_caption": state.get("last_life_publish_generated_image_caption", ""),
            "last_life_publish_generated_image_reference": state.get("last_life_publish_generated_image_reference", ""),
            "last_life_publish_generated_image_reference_exists": bool(state.get("last_life_publish_generated_image_reference_exists", False)),
            "last_life_publish_generated_image_anchor": state.get("last_life_publish_generated_image_anchor", ""),
            "last_life_publish_generated_image_composition": state.get("last_life_publish_generated_image_composition", ""),
            "last_manual_publish_generated_image_status": state.get("last_manual_publish_generated_image_status", ""),
            "last_manual_publish_generated_image_note": state.get("last_manual_publish_generated_image_note", ""),
            "last_manual_publish_generated_image_backend": state.get("last_manual_publish_generated_image_backend", ""),
            "last_manual_publish_generated_image_caption": state.get("last_manual_publish_generated_image_caption", ""),
            "last_manual_publish_generated_image_reference": state.get("last_manual_publish_generated_image_reference", ""),
            "last_manual_publish_generated_image_reference_exists": bool(state.get("last_manual_publish_generated_image_reference_exists", False)),
            "last_manual_publish_generated_image_anchor": state.get("last_manual_publish_generated_image_anchor", ""),
            "last_manual_publish_generated_image_composition": state.get("last_manual_publish_generated_image_composition", ""),
            "last_emotional_vent_at": self.plugin._format_timestamp_elapsed(state.get("last_emotional_vent_at", 0)),
            "last_emotional_vent_status": state.get("last_emotional_vent_status", ""),
            "last_emotional_vent_text": state.get("last_emotional_vent_text", ""),
            "last_emotional_vent_images": self._int(state.get("last_emotional_vent_images")),
            "last_emotional_vent_generated_image_status": state.get("last_emotional_vent_generated_image_status", ""),
            "last_emotional_vent_generated_image_note": state.get("last_emotional_vent_generated_image_note", ""),
            "last_emotional_vent_generated_image_backend": state.get("last_emotional_vent_generated_image_backend", ""),
            "last_emotional_vent_generated_image_caption": state.get("last_emotional_vent_generated_image_caption", ""),
            "last_emotional_vent_generated_image_reference": state.get("last_emotional_vent_generated_image_reference", ""),
            "last_emotional_vent_generated_image_reference_exists": bool(state.get("last_emotional_vent_generated_image_reference_exists", False)),
            "last_emotional_vent_generated_image_anchor": state.get("last_emotional_vent_generated_image_anchor", ""),
            "last_emotional_vent_generated_image_composition": state.get("last_emotional_vent_generated_image_composition", ""),
            "last_comment_inbox_checked_at": self.plugin._format_timestamp_elapsed(state.get("last_comment_inbox_checked_at", 0)),
            "last_comment_inbox_status": state.get("last_comment_inbox_status", ""),
            "last_comment_inbox_reply_text": state.get("last_comment_inbox_reply_text", ""),
            "auth_block_until": self.plugin._format_timestamp_elapsed(state.get("auth_block_until", 0)),
            "auth_failure_reason": state.get("last_auth_failure_reason", ""),
            "auth_failure_count": self._int(state.get("auth_failure_count")),
            "auth_status": state.get("last_auth_status", ""),
        }

    def _jm_cosmos_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        state = data.get("jm_cosmos_integration") if isinstance(data.get("jm_cosmos_integration"), dict) else {}
        try:
            available = bool(getattr(self.plugin, "_jm_cosmos_available", lambda: False)())
        except Exception:
            available = False
        album = state.get("last_album") if isinstance(state.get("last_album"), dict) else {}
        return {
            "enabled": bool(available and getattr(self.plugin, "enable_jm_cosmos_integration", False)),
            "boredom_read_enabled": bool(available and getattr(self.plugin, "enable_jm_cosmos_boredom_read", False)),
            "ask_recommendation_enabled": bool(available and getattr(self.plugin, "enable_private_reading_ask_recommendation", False)),
            "available": available,
            "last_read_at": self.plugin._format_timestamp_elapsed(state.get("last_read_at", 0)),
            "last_status": state.get("last_status", ""),
            "last_keyword": state.get("last_keyword", ""),
            "last_album": {
                "id": self._single_line(album.get("id"), 32),
                "title": self._single_line(album.get("title"), 100),
                "impression": self._single_line(album.get("impression"), 160),
                "rating": self._int(album.get("rating")),
                "user_rating": self._int(album.get("user_rating")),
            },
        }

    def _bookshelf_image_url(
        self,
        album_id: str,
        *,
        data_root: Path,
        page_index: int = 0,
        cover: bool = False,
        path_value: Any = "",
        access_token: str = "",
    ) -> str:
        if not album_id or (page_index < 1 and not cover):
            return ""
        url = f"{PAGE_API_PREFIX}/bookshelf/image?album_id={quote(str(album_id), safe='')}"
        if cover:
            url += "&cover=1"
        elif page_index > 0:
            url += f"&page={page_index}"
        if access_token:
            url += f"&access_token={quote(str(access_token), safe='')}"
        raw_path = self._single_line(path_value, 500)
        if raw_path:
            try:
                path = Path(raw_path).resolve()
                path.relative_to(data_root)
                if path.exists() and path.is_file():
                    stat = path.stat()
                    url += f"&v={int(stat.st_mtime)}-{stat.st_size}"
            except Exception:
                pass
        return url

    def _bookshelf_cover_url(
        self,
        album_id: str,
        item: dict[str, Any],
        page_items: list[dict[str, Any]],
        data_root: Path,
        *,
        access_token: str = "",
    ) -> str:
        cover_path = self._single_line(item.get("cover_path"), 500)
        if cover_path:
            return self._bookshelf_image_url(
                album_id,
                data_root=data_root,
                cover=True,
                path_value=cover_path,
                access_token=access_token,
            )
        first_page = page_items[0] if page_items else {}
        if isinstance(first_page, dict):
            return self._single_line(first_page.get("src"), 500)
        return ""

    async def _bookshelf_summary(self, data: dict[str, Any], *, unlocked: bool, access_token: str = "") -> dict[str, Any]:
        projects = data.get("creative_projects") if isinstance(data.get("creative_projects"), list) else []
        diaries = data.get("bot_diaries") if isinstance(data.get("bot_diaries"), list) else []
        shelf_items = data.get("bookshelf_items") if isinstance(data.get("bookshelf_items"), list) else []
        jm_items = [item for item in shelf_items if isinstance(item, dict) and item.get("type") == "jm_album"]
        jm_state = data.get("jm_cosmos_integration") if isinstance(data.get("jm_cosmos_integration"), dict) else {}
        secret_state = data.get("bookshelf_secret") if isinstance(data.get("bookshelf_secret"), dict) else {}
        reason_sanitizer = getattr(self.plugin, "_sanitize_bookshelf_password_reason", None)
        password_hint = ""
        if callable(reason_sanitizer):
            try:
                password_hint = reason_sanitizer(secret_state.get("reason"))
            except Exception:
                password_hint = ""
        else:
            password_hint = self._single_line(secret_state.get("reason"), 80)
        if not password_hint:
            password_hint = "提示会在通过“陪伴 输出夹层密码”生成后显示。"
        last_album = jm_state.get("last_album") if isinstance(jm_state.get("last_album"), dict) else {}
        if last_album and not any(str(item.get("album_id") or item.get("id") or "") == str(last_album.get("id") or "") for item in jm_items):
            jm_items.append(
                {
                    "type": "jm_album",
                    "title": last_album.get("title"),
                    "album_id": last_album.get("id"),
                    "description": last_album.get("description") or last_album.get("intro") or last_album.get("summary"),
                    "keyword": last_album.get("keyword"),
                    "author": last_album.get("author"),
                    "tags": last_album.get("tags"),
                    "photo_count": last_album.get("photo_count"),
                    "impression": last_album.get("impression"),
                    "reading_impression": last_album.get("reading_impression") or last_album.get("impression"),
                    "vision": last_album.get("vision"),
                    "rating": last_album.get("rating"),
                    "rating_reason": last_album.get("rating_reason"),
                    "user_rating": last_album.get("user_rating"),
                    "user_rating_reason": last_album.get("user_rating_reason"),
                    "user_rated_ts": last_album.get("user_rated_ts"),
                    "preference_tags": last_album.get("preference_tags") if isinstance(last_album.get("preference_tags"), list) else [],
                    "user_liked_tags": last_album.get("user_liked_tags") if isinstance(last_album.get("user_liked_tags"), list) else [],
                    "user_disliked_tags": last_album.get("user_disliked_tags") if isinstance(last_album.get("user_disliked_tags"), list) else [],
                    "user_tags_updated_ts": last_album.get("user_tags_updated_ts"),
                    "page_comments": last_album.get("page_comments") if isinstance(last_album.get("page_comments"), list) else [],
                    "image_count": last_album.get("image_count"),
                    "pages": last_album.get("pages") if isinstance(last_album.get("pages"), list) else [],
                    "sampled_pages": last_album.get("sampled_pages") if isinstance(last_album.get("sampled_pages"), list) else [],
                    "created_ts": last_album.get("created_ts"),
                }
            )
        data_root = Path(str(getattr(self.plugin, "data_dir", ""))).resolve()
        covers_root = data_root / "jm_cosmos_covers"
        if unlocked:
            pages_root = data_root / "bookshelf_pages"
            known_jm_ids = {
                self._single_line(item.get("album_id") or item.get("id"), 80)
                for item in jm_items
                if isinstance(item, dict) and self._single_line(item.get("album_id") or item.get("id"), 80)
            }
            preference_history = []
            profile = jm_state.get("preference_profile") if isinstance(jm_state.get("preference_profile"), dict) else {}
            if isinstance(profile.get("history"), list):
                preference_history = [item for item in profile.get("history", []) if isinstance(item, dict)]
            history_by_album: dict[str, dict[str, Any]] = {}
            for item in preference_history:
                album_id = self._single_line(item.get("album_id") or item.get("id"), 80)
                if album_id:
                    history_by_album[album_id] = {**history_by_album.get(album_id, {}), **item}
            try:
                orphan_dirs = [
                    path
                    for path in pages_root.iterdir()
                    if path.is_dir() and self._single_line(path.name, 80) and self._single_line(path.name, 80) not in known_jm_ids
                ] if pages_root.exists() else []
            except Exception:
                orphan_dirs = []
            for path in orphan_dirs:
                album_id = self._single_line(path.name, 80)
                page_files = sorted(
                    file
                    for file in path.iterdir()
                    if file.is_file() and file.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}
                )
                if not album_id or not page_files:
                    continue
                meta = history_by_album.get(album_id, {})
                created_ts = self._float(meta.get("created_ts")) or max((file.stat().st_mtime for file in page_files), default=0.0)
                cover_path = covers_root / f"{album_id}.jpg"
                jm_items.append(
                    {
                        "type": "jm_album",
                        "album_id": album_id,
                        "title": meta.get("title") or f"私密阅读 {album_id}",
                        "description": meta.get("reason") or "",
                        "tags": meta.get("terms") if isinstance(meta.get("terms"), list) else [],
                        "rating": meta.get("bot_rating") or meta.get("rating"),
                        "user_rating": meta.get("user_rating"),
                        "rating_reason": meta.get("reason") or "",
                        "user_rating_reason": meta.get("reason") or "",
                        "cover_path": str(cover_path) if cover_path.exists() else "",
                        "pages": [
                            {
                                "index": index + 1,
                                "path": str(file),
                                "name": file.name,
                            }
                            for index, file in enumerate(page_files)
                        ],
                        "image_count": len(page_files),
                        "created_ts": created_ts,
                        "source": "bookshelf_orphan_recovered",
                        "locked": True,
                    }
                )
        public_books = []
        browsing_entries = self._browsing_history_entries(data)
        for item in [project for project in projects if isinstance(project, dict)][-12:]:
            chunks = item.get("draft_chunks") if isinstance(item.get("draft_chunks"), list) else []
            full_text = "\n\n".join(
                self._single_line(chunk.get("text"), 2000)
                for chunk in chunks
                if isinstance(chunk, dict) and self._single_line(chunk.get("text"), 2000)
            )
            chunk_entries = [
                {
                    "index": index + 1,
                    "text": self._single_line(chunk.get("text"), 2000),
                    "created": self.plugin._format_timestamp_elapsed(chunk.get("created_ts", 0) or chunk.get("created_at", 0)),
                }
                for index, chunk in enumerate(chunks)
                if isinstance(chunk, dict) and self._single_line(chunk.get("text"), 2000)
            ]
            status = self._single_line(item.get("status"), 24)
            progress = f"{self._int(item.get('current_chars'))}/{self._int(item.get('target_chars')) or '-'} 字"
            public_books.append(
                {
                    "id": self._single_line(item.get("id"), 32) or f"creative-{len(public_books)}",
                    "kind": "creative",
                    "category": self._single_line(item.get("work_type"), 30) or "创作",
                    "work_type": self._single_line(item.get("work_type"), 30) or "短篇小说",
                    "title": self._single_line(item.get("title"), 60) or "未定标题",
                    "intro": self._single_line(item.get("premise"), 240) or "这本书还没整理出简介。",
                    "status": status,
                    "tone": self._single_line(item.get("tone"), 40),
                    "point_of_view": self._single_line(item.get("point_of_view"), 40) or "第三人称有限视角",
                    "progress": progress,
                    "content": full_text or self._single_line(chunks[-1].get("text") if chunks else "", 2000) or "这本书还没有正文。",
                    "chunks": chunk_entries,
                    "outline_count": len(item.get("outline") or []) if isinstance(item.get("outline"), list) else 0,
                    "character_count": len(item.get("characters") or []) if isinstance(item.get("characters"), list) else 0,
                    "review_count": len(item.get("quality_reviews") or []) if isinstance(item.get("quality_reviews"), list) else 0,
                    "manual_edit_count": len(item.get("manual_edits") or []) if isinstance(item.get("manual_edits"), list) else 0,
                    "has_story_bible": bool(item.get("story_bible")) if isinstance(item.get("story_bible"), dict) else False,
                    "last_manual_edit_summary": self._single_line(item.get("last_manual_edit_summary"), 120),
                    "created": self.plugin._format_timestamp_elapsed(item.get("created_at", 0)),
                }
            )
        if browsing_entries:
            latest = browsing_entries[-1]
            public_books.append(
                {
                    "id": "browsing-history-main",
                    "kind": "browsing",
                    "category": "浏览记录",
                    "title": "浏览记录",
                    "intro": f"这里收着 {len(browsing_entries)} 条新闻阅读和主动搜索记录。打开后可以选择记录。",
                    "content": self._single_line(latest.get("content"), 2000) or self._single_line(latest.get("intro"), 1200),
                    "entries": browsing_entries,
                    "created": self._single_line(latest.get("generated_at") or latest.get("date"), 32),
                    "progress": f"{len(browsing_entries)} 条记录",
                    "tags": ["新闻阅读", "主动搜索"],
                }
            )
        locked_count = (1 if diaries else 0) + len(jm_items)
        secret_books: list[dict[str, Any]] = []
        if unlocked:
            diary_entries = []
            for item in [entry for entry in diaries if isinstance(entry, dict)][-60:]:
                body = self._single_line(item.get("body"), 1600)
                summary = self._single_line(item.get("summary"), 1000)
                share_seed = self._single_line(item.get("share_seed"), 1000)
                content = body or "\n\n".join(part for part in (summary, share_seed) if part)
                diary_entries.append(
                    {
                        "date": self._single_line(item.get("date"), 24) or "某天",
                        "generated_at": self._single_line(item.get("generated_at"), 32),
                        "title": f"{self._single_line(item.get('date'), 24) or '某天'}",
                        "intro": summary or "这一天没有留下摘要。",
                        "content": content or "这一天的日记暂时没有写出正文。",
                        "tags": [self._single_line(tag, 24) for tag in item.get("tags", [])[:8] if self._single_line(tag, 24)]
                        if isinstance(item.get("tags"), list)
                        else [],
                    }
                )
            if diary_entries:
                secret_books.append(
                    {
                        "id": "diary-main",
                        "kind": "diary",
                        "category": "日记",
                        "title": "日记本",
                        "intro": f"这里收着 {len(diary_entries)} 天的日记。打开后可以选择日期。",
                        "content": diary_entries[-1].get("content") or "这本日记暂时没有可读内容。",
                        "entries": diary_entries,
                        "created": diary_entries[-1].get("generated_at", ""),
                    }
            )
            recent_jm_items = sorted(
                jm_items,
                key=lambda item: self._float(item.get("created_ts") or item.get("created_at") or item.get("ts")),
                reverse=True,
            )[:18]
            for item in recent_jm_items:
                album_id = self._single_line(item.get("album_id") or item.get("id"), 32)
                pages = item.get("pages") if isinstance(item.get("pages"), list) else []
                reading_impression = self._single_line(item.get("reading_impression") or item.get("impression"), 1000)
                vision_impression = self._single_line(item.get("vision"), 1000)
                show_vision_impression = bool(
                    vision_impression
                    and (
                        not reading_impression
                        or _text_similarity(vision_impression, reading_impression) < 0.72
                    )
                )
                bot_rating = self._int(item.get("rating"))
                user_rating = self._int(item.get("user_rating"))
                rating_reason = self._single_line(item.get("rating_reason"), 180)
                user_rating_reason = self._single_line(item.get("user_rating_reason"), 180)
                album_description = self._single_line(
                    item.get("description")
                    or item.get("intro")
                    or item.get("summary")
                    or item.get("desc"),
                    600,
                )
                if not album_description:
                    detail_parts = []
                    author_text = self._single_line(item.get("author"), 40)
                    photo_count = self._int(item.get("photo_count")) or self._int(item.get("image_count"))
                    tag_text = "、".join(
                        self._single_line(tag, 24)
                        for tag in (item.get("tags") if isinstance(item.get("tags"), list) else [])[:6]
                        if self._single_line(tag, 24)
                    )
                    if author_text:
                        detail_parts.append(f"作者：{author_text}")
                    if photo_count:
                        detail_parts.append(f"页数：{photo_count}")
                    if tag_text:
                        detail_parts.append(f"标签：{tag_text}")
                    album_description = "；".join(detail_parts) or "这条阅读记录暂时没有整理出明确简介。"
                page_comment_map: dict[int, list[str]] = {}
                raw_comments = self._merge_bookshelf_page_comments(
                    item.get("page_comments") if isinstance(item.get("page_comments"), list) else [],
                    item.get("page_comments_previous") if isinstance(item.get("page_comments_previous"), list) else [],
                    limit=32,
                )
                for comment_item in raw_comments:
                    if not isinstance(comment_item, dict):
                        continue
                    page_no = self._int(comment_item.get("page"))
                    comment_text = self._single_line(comment_item.get("comment"), 100)
                    if page_no > 0 and comment_text:
                        page_comments = page_comment_map.setdefault(page_no, [])
                        if comment_text not in page_comments:
                            page_comments.append(comment_text)
                page_items = []
                for page in pages:
                    if not isinstance(page, dict):
                        continue
                    index = self._int(page.get("index"))
                    if index <= 0:
                        continue
                    page_src = self._bookshelf_image_url(
                        album_id,
                        data_root=data_root,
                        page_index=index,
                        path_value=page.get("path"),
                        access_token=access_token,
                    )
                    page_items.append(
                        {
                            "index": index,
                            "src": page_src,
                            "comment": "\n".join(page_comment_map.get(index, [])),
                        }
                    )
                cover_src = ""
                if album_id:
                    cover_src = self._bookshelf_cover_url(album_id, item, page_items, data_root, access_token=access_token)
                secret_books.append(
                    {
                        "id": f"jm-{album_id or len(secret_books)}",
                        "kind": "jm_album",
                        "category": "私密阅读",
                        "album_id": album_id,
                        "title": self._single_line(item.get("title"), 100) or "未命名阅读记录",
                        "intro": self._single_line(album_description, 600),
                        "reading_impression": reading_impression or vision_impression,
                        "rating": bot_rating,
                        "rating_reason": rating_reason,
                        "user_rating": user_rating,
                        "user_rating_reason": user_rating_reason,
                        "user_rated": bool(user_rating),
                        "author": self._single_line(item.get("author"), 40),
                        "progress": f"{len(page_items) or self._int(item.get('image_count')) or self._int(item.get('photo_count'))} 页",
                        "created": self.plugin._format_timestamp_elapsed(item.get("created_ts", 0)),
                        "content": "\n\n".join(
                            part
                            for part in (
                                f"读后感：{reading_impression}" if reading_impression else "",
                                f"Bot 评分：{bot_rating}/10" if bot_rating else "",
                                f"用户评分：{user_rating}/10" if user_rating else "",
                                f"评分理由：{user_rating_reason or rating_reason}" if (user_rating_reason or rating_reason) else "",
                                f"画面记录：{vision_impression}" if show_vision_impression else "",
                                f"关键词：{self._single_line(item.get('keyword'), 80)}" if self._single_line(item.get("keyword"), 80) else "",
                            )
                            if part
                        ) or "这本只留下了一点很含糊的阅读印象。",
                        "tags": [self._single_line(tag, 24) for tag in item.get("tags", [])[:8] if self._single_line(tag, 24)]
                        if isinstance(item.get("tags"), list)
                        else [],
                        "preference_tags": [
                            self._single_line(tag, 24)
                            for tag in (item.get("preference_tags") if isinstance(item.get("preference_tags"), list) else [])[:8]
                            if self._single_line(tag, 24)
                        ],
                        "user_liked_tags": [
                            self._single_line(tag, 24)
                            for tag in (item.get("user_liked_tags") if isinstance(item.get("user_liked_tags"), list) else [])[:8]
                            if self._single_line(tag, 24)
                        ],
                        "user_disliked_tags": [
                            self._single_line(tag, 24)
                            for tag in (item.get("user_disliked_tags") if isinstance(item.get("user_disliked_tags"), list) else [])[:8]
                            if self._single_line(tag, 24)
                        ],
                        "user_tags_updated": bool(item.get("user_tags_updated_ts")),
                        "cover_src": cover_src,
                        "pages": page_items,
                        "page_comment_count": sum(len(comments) for comments in page_comment_map.values()),
                        "page_comments": [
                            {"page": page, "comment": comment}
                            for page, comments in sorted(page_comment_map.items())
                            for comment in comments
                        ],
                    }
                )
        return {
            "unlocked": unlocked,
            "access_token": access_token if unlocked and self._bookshelf_access_token_valid(access_token) else "",
            "access_expires_in": int(max(0, self._bookshelf_access_tokens().get(access_token, 0) - time.time()))
            if unlocked and access_token
            else 0,
            "public_count": len(public_books),
            "secret_count": locked_count,
            "diary_count": 1 if diaries else 0,
            "jm_album_count": len(jm_items),
            "password_hint": password_hint,
            "public_books": public_books,
            "secret_books": secret_books,
        }

    def _proactive_template_text(self, value: Any, *, target_name: Any = "", limit: int = 220) -> str:
        text = self._single_line(value, limit)
        if not text:
            return ""
        name = self._single_line(target_name, 40) or "对方"
        return self._single_line(text.replace("{name}", name).replace("{{name}}", name), limit)

    def _proactive_reason_label(self, reason: Any, *, target_name: Any = "") -> str:
        key = self._single_line(reason, 40)
        if not key:
            return "未记录原因"
        extra = {
            "bookshelf_reading_share": "跟你提起刚翻到的漫画本子",
            "bookshelf_recommendation_request": "想问你要不要推荐阅读",
            "web_exploration_share": "分享主动搜索后的发现",
            "news_share": "分享刚读到的新闻",
            "timer": "聊天中形成的临时约定",
            "troubleshooting_test": "排障测试触发",
        }
        return self._proactive_template_text(extra.get(key) or _REASON_TEXT.get(key) or key, target_name=target_name, limit=80)

    def _proactive_source_meta(self, source: Any) -> dict[str, str]:
        key = self._single_line(source, 40)
        if not key:
            return {"label": "插件主动", "note": ""}
        meta = {
            "random": {
                "label": "轻微想念",
                "note": "没有明确外部触发，更像安静一阵后轻轻冒出来、想靠近你一下。",
            },
            "daily_greeting": {
                "label": "日常招呼",
                "note": "到了早中晚合适的那个点，顺手来冒个头，不是专门执行问候任务。",
            },
            "pending_followup": {
                "label": "补一句",
                "note": "前面那句还留着一个具体点没落地，所以隔一阵再接一句。",
            },
            "state": {
                "label": "身体小需求",
                "note": "不是汇报状态，而是身体上那点小事挂着，顺手拿来找你说一句。",
            },
            "event": {"label": "生活事件", "note": ""},
            "story": {"label": "日常剧情", "note": ""},
            "habit": {"label": "习惯关心", "note": ""},
            "bilibili": {"label": "B站分享", "note": ""},
            "bookshelf_reading": {"label": "私密阅读", "note": ""},
            "creative_writing": {"label": "创作灵感", "note": ""},
            "group_share": {"label": "群聊见闻", "note": ""},
            "web_exploration": {"label": "主动搜索", "note": ""},
            "news": {"label": "新闻阅读", "note": ""},
            "candidate": {"label": "主动候选", "note": ""},
            "followup": {"label": "补一句", "note": "前面的话还差个具体点，所以顺手再接一句。"},
            "external": {"label": "外部主动能力", "note": ""},
            "timer": {"label": "官方定时计划", "note": ""},
            "proactive": {"label": "插件主动", "note": ""},
            "unknown": {"label": "未记录来源", "note": ""},
        }
        return dict(meta.get(key) or {"label": key, "note": ""})

    def _proactive_source_label(self, source: Any) -> str:
        return self._proactive_source_meta(source).get("label") or "插件主动"

    def _proactive_source_note(self, source: Any) -> str:
        return self._single_line(self._proactive_source_meta(source).get("note"), 120)

    def _proactive_reason_detail(
        self,
        *,
        reason: Any,
        source: Any = "",
        topic: Any = "",
        motive: Any = "",
        note: Any = "",
        target_name: Any = "",
    ) -> str:
        label = self._proactive_reason_label(reason, target_name=target_name)
        parts = [label]
        topic_text = self._proactive_template_text(topic, target_name=target_name, limit=80)
        motive_text = self._proactive_template_text(motive, target_name=target_name, limit=140)
        note_text = self._proactive_template_text(note, target_name=target_name, limit=120)
        source_text = self._proactive_source_label(source)
        if topic_text:
            parts.append(f"话题：{topic_text}")
        if motive_text:
            parts.append(f"动机：{motive_text}")
        if note_text:
            parts.append(f"记录：{note_text}")
        if source_text:
            parts.append(f"来源：{source_text}")
        return self._single_line("；".join(parts), 220)

    def _proactive_candidate_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        raw = data.get("proactive_candidate_pool") if isinstance(data.get("proactive_candidate_pool"), list) else []
        users = data.get("users") if isinstance(data.get("users"), dict) else {}
        now = time.time()
        buckets: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        source_counts: dict[str, int] = {}
        user_counts: dict[str, dict[str, Any]] = {}
        total_attempts = 0
        pending_total = 0

        def candidate_user_meta(user_id: str, user: Any) -> dict[str, str]:
            if not isinstance(user, dict):
                return {"label": user_id or "未知用户", "role": "unknown", "role_label": "未知"}
            role = self.plugin._private_user_role(user, user_id) if hasattr(self.plugin, "_private_user_role") else ""
            role_labeler = getattr(self.plugin, "_private_user_role_label", None)
            role_label = role_labeler(role) if callable(role_labeler) else ("主人" if role == "owner" else "朋友")
            nickname = self._single_line(user.get("nickname"), 40)
            generic_names = {"用户", "主人", "默认用户"}
            if str(user_id).isdigit():
                label = nickname if nickname and nickname not in generic_names else user_id
            else:
                label = f"临时会话 · {str(user_id)[:8]}"
            return {"label": label or user_id or "未知用户", "role": role or "friend", "role_label": role_label}

        for item in raw[-240:]:
            if not isinstance(item, dict):
                continue
            repeat_count = max(1, self._int(item.get("repeat_count")))
            status = self._single_line(item.get("status"), 24) or "unknown"
            note = self._single_line(item.get("note"), 160)
            if status == "blocked" and note == "朋友关系不接收敏感主动":
                continue
            user_id = self._single_line(item.get("user_id"), 32)
            user = users.get(user_id) if isinstance(users, dict) else None
            reason_raw = self._single_line(item.get("reason"), 40)
            action_raw = self._single_line(item.get("action"), 40)
            if (
                isinstance(user, dict)
                and hasattr(self.plugin, "_friend_can_receive_proactive_reason")
                and not self.plugin._friend_can_receive_proactive_reason(user, reason_raw, action_raw)
            ):
                continue
            if status != "sent" and not bool(
                isinstance(user, dict)
                and getattr(self.plugin, "_user_enabled_for_proactive", lambda uid, profile: bool(profile and profile.get("enabled", True)))(
                    user_id,
                    user,
                )
            ):
                continue
            total_attempts += repeat_count
            if status != "sent":
                pending_total += repeat_count
            user_meta = candidate_user_meta(user_id, user)
            user_bucket = user_counts.setdefault(
                user_id or "unknown",
                {
                    "user_id": user_id,
                    "label": user_meta["label"],
                    "role": user_meta["role"],
                    "role_label": user_meta["role_label"],
                    "total": 0,
                    "pending_total": 0,
                    "counts": {},
                },
            )
            user_bucket["total"] = self._int(user_bucket.get("total")) + repeat_count
            if status != "sent":
                user_bucket["pending_total"] = self._int(user_bucket.get("pending_total")) + repeat_count
            bucket_counts = user_bucket.get("counts")
            if not isinstance(bucket_counts, dict):
                bucket_counts = {}
                user_bucket["counts"] = bucket_counts
            bucket_counts[status] = self._int(bucket_counts.get(status)) + repeat_count
            source = self._single_line(item.get("source"), 40) or "unknown"
            display_source = "bookshelf_reading" if source == "jm_cosmos" else source
            counts[status] = counts.get(status, 0) + repeat_count
            source_counts[display_source] = source_counts.get(display_source, 0) + repeat_count
            scheduled = self._float(item.get("scheduled_ts"))
            created = self._float(item.get("created_ts"))
            last_seen = self._float(item.get("last_seen_ts")) or created
            reason = reason_raw
            action = action_raw
            if reason == "jm_cosmos_share":
                reason = "bookshelf_reading_share"
            if reason == "jm_cosmos_recommendation_request":
                reason = "bookshelf_recommendation_request"
            if action == "jm_cosmos_read":
                action = "bookshelf_reading"
            signature = self._single_line(item.get("signature"), 120)
            topic = self._single_line(item.get("topic"), 100)
            motive = self._single_line(item.get("motive"), 180)
            semantic_kind = self._single_line(item.get("semantic_kind"), 40)
            semantic_anchor_type = self._single_line(item.get("semantic_anchor_type"), 40)
            semantic_score = self._int(item.get("semantic_score"))
            semantic_pressure = self._int(item.get("semantic_pressure"))
            semantic_risk = self._int(item.get("semantic_risk"))
            semantic_note = self._single_line(item.get("semantic_note"), 180)
            sanitizer = getattr(self.plugin, "_sanitize_friend_proactive_plan_fields", None)
            if isinstance(user, dict) and callable(sanitizer):
                sanitized = sanitizer(
                    user,
                    reason=reason,
                    action=action,
                    topic=topic,
                    motive=motive,
                )
                reason = self._single_line(sanitized.get("reason"), 40) or reason
                action = self._single_line(sanitized.get("action"), 40) or action
                topic = self._single_line(sanitized.get("topic"), 100)
                motive = self._single_line(sanitized.get("motive"), 180)
            merged = None
            for existing in reversed(buckets):
                if existing.get("status") != status:
                    continue
                if existing.get("user_id") != user_id:
                    continue
                old_signature = str(existing.get("_signature") or "")
                if signature and old_signature:
                    similar = bool(getattr(self.plugin, "_topic_signature_similar", lambda a, b: a == b)(signature, old_signature))
                else:
                    similar = (topic or motive) == (existing.get("topic") or existing.get("motive"))
                if not similar:
                    continue
                if max(last_seen, scheduled, created) - self._float(existing.get("_first_ts")) > 36 * 3600:
                    continue
                merged = existing
                break
            if merged is None:
                buckets.append(
                    {
                        "id": self._single_line(item.get("id"), 20),
                        "user_id": user_id,
                        "user_label": user_meta["label"],
                        "user_role": user_meta["role"],
                        "user_role_label": user_meta["role_label"],
                        "source": display_source,
                        "source_label": self._proactive_source_label(display_source),
                        "source_note": self._proactive_source_note(display_source),
                        "reason": reason,
                        "reason_label": self._proactive_reason_label(reason, target_name=user_meta["label"]),
                        "reason_detail": self._proactive_reason_detail(
                            reason=reason,
                            source=display_source,
                            topic=topic,
                            motive=motive,
                            note=note,
                            target_name=user_meta["label"],
                        ),
                        "action": action,
                        "topic": topic,
                        "motive": motive,
                        "score": self._int(item.get("score")),
                        "semantic_kind": semantic_kind,
                        "semantic_anchor_type": semantic_anchor_type,
                        "semantic_score": semantic_score,
                        "semantic_pressure": semantic_pressure,
                        "semantic_risk": semantic_risk,
                        "semantic_note": semantic_note,
                        "status": status,
                        "note": note,
                        "repeat_count": repeat_count,
                        "created_ts": created,
                        "last_seen_ts": last_seen,
                        "scheduled_ts": scheduled,
                        "is_due": bool(scheduled and scheduled <= now),
                        "_signature": signature,
                        "_first_ts": max(created, scheduled, last_seen),
                    }
                )
                continue
            merged["repeat_count"] = self._int(merged.get("repeat_count")) + repeat_count
            merged["last_seen_ts"] = max(self._float(merged.get("last_seen_ts")), last_seen, created)
            merged["scheduled_ts"] = max(self._float(merged.get("scheduled_ts")), scheduled)
            merged["score"] = max(self._int(merged.get("score")), self._int(item.get("score")))
            if semantic_score >= self._int(merged.get("semantic_score")):
                merged["semantic_kind"] = semantic_kind
                merged["semantic_anchor_type"] = semantic_anchor_type
                merged["semantic_score"] = semantic_score
                merged["semantic_pressure"] = semantic_pressure
                merged["semantic_risk"] = semantic_risk
                merged["semantic_note"] = semantic_note
            if topic:
                merged["topic"] = topic
            if motive:
                merged["motive"] = motive
            if note and note != merged.get("note"):
                merged["note"] = "多来源合并"
            merged["is_due"] = bool(merged.get("scheduled_ts") and self._float(merged.get("scheduled_ts")) <= now)
        items: list[dict[str, Any]] = []
        for item in buckets:
            created = self._float(item.get("created_ts"))
            last_seen = self._float(item.get("last_seen_ts")) or created
            scheduled = self._float(item.get("scheduled_ts"))
            item.pop("_signature", None)
            item.pop("_first_ts", None)
            item["created"] = self.plugin._format_timestamp_elapsed(created)
            item["last_seen"] = self.plugin._format_timestamp_elapsed(last_seen)
            item["scheduled"] = self.plugin._format_timestamp_elapsed(scheduled)
            items.append(item)
        items.sort(key=lambda item: item.get("last_seen_ts") or item.get("scheduled_ts") or 0, reverse=True)
        return {
            "total": total_attempts,
            "pending_total": pending_total,
            "visible_total": len(items),
            "counts": counts,
            "source_counts": source_counts,
            "users": sorted(
                user_counts.values(),
                key=lambda item: (self._int(item.get("total")), self._single_line(item.get("label"), 40)),
                reverse=True,
            ),
            "items": items[:60],
        }

    def _proactive_task_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        users = data.get("users") if isinstance(data.get("users"), dict) else {}
        now = time.time()
        items: list[dict[str, Any]] = []
        source_counts: dict[str, int] = {}
        status_counts: dict[str, int] = {}
        audit_items: list[dict[str, Any]] = []
        audit_status_counts: dict[str, int] = {}
        user_states: list[dict[str, Any]] = []

        def readiness_snapshot(user: dict[str, Any]) -> dict[str, Any]:
            getter = getattr(self.plugin, "_proactive_inner_readiness", None)
            if not callable(getter):
                return {}
            try:
                raw = getter(user, now=now)
            except Exception:
                return {}
            if not isinstance(raw, dict):
                return {}
            drive = raw.get("drive") if isinstance(raw.get("drive"), dict) else {}
            temperature = raw.get("temperature") if isinstance(raw.get("temperature"), dict) else {}
            return {
                "score": round(self._float(raw.get("score")), 3),
                "label": self._single_line(raw.get("label"), 80),
                "detail": self._single_line(raw.get("detail"), 220),
                "drive_score": round(self._float(drive.get("score")), 3) if drive else 0,
                "drive_label": self._single_line(drive.get("label"), 40) if drive else "",
                "drive_detail": self._single_line(drive.get("detail"), 140) if drive else "",
                "temperature_score": round(self._float(temperature.get("score")), 3) if temperature else 0,
                "temperature_label": self._single_line(temperature.get("label"), 40) if temperature else "",
                "temperature_detail": self._single_line(temperature.get("detail"), 140) if temperature else "",
            }

        def planned_semantic_snapshot(user: dict[str, Any]) -> dict[str, Any]:
            semantic_kind = self._single_line(user.get("planned_proactive_semantic_kind"), 40)
            anchor_type = self._single_line(user.get("planned_proactive_anchor_type"), 40)
            semantic_score = self._int(user.get("planned_proactive_semantic_score"))
            semantic_note = self._single_line(user.get("planned_proactive_semantic_note"), 180)
            pressure = 0
            risk = 0
            getter = getattr(self.plugin, "_planned_proactive_semantics", None)
            if callable(getter):
                try:
                    semantics = getter(user)
                except Exception:
                    semantics = {}
                if isinstance(semantics, dict):
                    semantic_kind = semantic_kind or self._single_line(semantics.get("kind"), 40)
                    anchor_type = anchor_type or self._single_line(semantics.get("anchor_type"), 40)
                    if semantic_score <= 0:
                        semantic_score = int(max(0.0, min(1.0, self._float(semantics.get("score")))) * 100)
                    pressure = int(max(0.0, min(1.0, self._float(semantics.get("pressure")))) * 100)
                    risk = int(max(0.0, min(1.0, self._float(semantics.get("risk")))) * 100)
                    semantic_note = semantic_note or self._single_line(semantics.get("note"), 180)
            return {
                "semantic_kind": semantic_kind,
                "semantic_anchor_type": anchor_type,
                "semantic_score": semantic_score,
                "semantic_pressure": pressure,
                "semantic_risk": risk,
                "semantic_note": semantic_note,
            }

        def planned_window_snapshot(user: dict[str, Any]) -> dict[str, Any]:
            start_ts = self._float(user.get("planned_proactive_window_start_at"))
            best_until_ts = self._float(user.get("planned_proactive_best_until_at"))
            expire_ts = self._float(user.get("planned_proactive_expire_at"))
            phase = ""
            detail = ""
            phase_getter = getattr(self.plugin, "_planned_impulse_window_phase", None)
            if callable(phase_getter):
                try:
                    phase, detail = phase_getter(user, now=now)
                except Exception:
                    phase, detail = "", ""
            impulse_value = 0
            value_getter = getattr(self.plugin, "_planned_impulse_value", None)
            if callable(value_getter):
                try:
                    impulse_value = int(max(0.0, min(1.0, self._float(value_getter(user, now=now)))) * 100)
                except Exception:
                    impulse_value = 0
            return {
                "window_start_ts": start_ts,
                "window_start": self.plugin._format_timestamp_elapsed(start_ts),
                "best_until_ts": best_until_ts,
                "best_until": self.plugin._format_timestamp_elapsed(best_until_ts),
                "expire_ts": expire_ts,
                "expire": self.plugin._format_timestamp_elapsed(expire_ts),
                "window_phase": self._single_line(phase, 24),
                "window_detail": self._single_line(detail, 160),
                "impulse_value": impulse_value,
            }

        def afterglow_snapshot(user: dict[str, Any]) -> dict[str, Any]:
            raw = user.get("proactive_afterglow")
            if not isinstance(raw, dict):
                return {}
            ts = self._float(raw.get("ts"))
            return {
                "ts": ts,
                "time": self.plugin._format_timestamp_elapsed(ts),
                "status": self._single_line(raw.get("status"), 32),
                "label": self._single_line(raw.get("label"), 180),
                "next_tendency": self._single_line(raw.get("next_tendency"), 180),
                "reason": self._single_line(raw.get("reason"), 50),
                "action": self._single_line(raw.get("action"), 50),
                "semantic_kind": self._single_line(raw.get("semantic_kind"), 40),
                "anchor_type": self._single_line(raw.get("anchor_type"), 40),
                "semantic_score": self._int(raw.get("semantic_score")),
            }

        def hesitation_snapshot(user: dict[str, Any]) -> dict[str, Any]:
            raw = user.get("recent_proactive_hesitations")
            latest = raw[-1] if isinstance(raw, list) and raw and isinstance(raw[-1], dict) else {}
            ts = self._float(latest.get("ts") if isinstance(latest, dict) else 0) or self._float(user.get("last_proactive_hesitation_at"))
            note = self._single_line(latest.get("note") if isinstance(latest, dict) else "", 160) or self._single_line(user.get("last_proactive_hesitation_note"), 160)
            if not ts and not note:
                return {}
            return {
                "ts": ts,
                "time": self.plugin._format_timestamp_elapsed(ts),
                "note": note,
                "count": self._int(latest.get("count") if isinstance(latest, dict) else 0),
                "topic": self._single_line(latest.get("topic") if isinstance(latest, dict) else "", 80),
                "motive": self._single_line(latest.get("motive") if isinstance(latest, dict) else "", 140),
            }

        for user_id, user in users.items():
            if not isinstance(user, dict):
                continue
            readiness = readiness_snapshot(user)
            afterglow = afterglow_snapshot(user)
            hesitation = hesitation_snapshot(user)
            if bool(user.get("enabled", True)):
                user_summary_for_state = self._user_summary(str(user_id), user)
                effective_limit = (
                    self.plugin._effective_user_daily_limit(user)
                    if hasattr(self.plugin, "_effective_user_daily_limit")
                    else getattr(self.plugin, "max_daily_messages", 0)
                )
                next_for_state = self._float(user.get("next_proactive_at"))
                user_states.append(
                    {
                        "user_id": str(user_id),
                        "user_label": user_summary_for_state.get("display_name") or str(user_id),
                        "user_role": user_summary_for_state.get("relationship_role") or "",
                        "user_role_label": user_summary_for_state.get("relationship_role_label") or "",
                        "sent_today": self._int(user.get("sent_today")),
                        "effective_daily_limit": effective_limit,
                        "next_proactive_ts": next_for_state,
                        "next_proactive": self.plugin._format_timestamp_elapsed(next_for_state),
                        "proactive_sending": bool(user.get("proactive_sending")),
                        "last_skip_ts": self._float(user.get("last_proactive_skip_at")),
                        "last_skip": self.plugin._format_timestamp_elapsed(user.get("last_proactive_skip_at", 0)),
                        "last_skip_reason": self._single_line(user.get("last_proactive_skip_reason"), 160),
                        "last_skip_prefix": self._single_line(user.get("last_proactive_skip_prefix"), 20),
                        "last_sent_ts": self._float(user.get("last_sent")),
                        "last_sent": self.plugin._format_timestamp_elapsed(user.get("last_sent", 0)),
                        "inner_readiness": readiness,
                        "afterglow": afterglow,
                        "hesitation": hesitation,
                    }
                )
            scheduled_ts = self._float(user.get("next_proactive_at"))
            timer_event = user.get("llm_timer_event") if isinstance(user.get("llm_timer_event"), dict) else {}
            if scheduled_ts <= 0 and timer_event:
                scheduled_ts = self._float(timer_event.get("scheduled_ts"))
            if scheduled_ts <= 0:
                continue
            source = self._single_line(user.get("planned_proactive_source"), 40)
            if not source and timer_event:
                source = "timer"
            source = source or "proactive"
            status = "due" if scheduled_ts <= now else "scheduled"
            if scheduled_ts < now - 15 * 60:
                status = "overdue"
            if timer_event and self._single_line(timer_event.get("backend"), 40) == "astrbot_cron" and scheduled_ts <= now:
                status = "handed_off"
            timer_status = self._single_line(timer_event.get("status"), 40)
            if timer_status in {"failed", "cancelled", "cancel_failed", "cancel_skipped"}:
                status = timer_status
            user_summary = self._user_summary(str(user_id), user)
            source_counts[source] = source_counts.get(source, 0) + 1
            status_counts[status] = status_counts.get(status, 0) + 1
            action = (
                self._single_line(user.get("planned_proactive_action"), 40)
                or self._single_line(timer_event.get("action"), 40)
                or "message"
            )
            reason = (
                self._single_line(user.get("planned_proactive_reason"), 40)
                or self._single_line(timer_event.get("reason"), 40)
            )
            topic = (
                self._single_line(user.get("planned_proactive_topic"), 80)
                or self._single_line(timer_event.get("topic"), 80)
            )
            motive = (
                self._single_line(user.get("planned_proactive_motive"), 180)
                or self._single_line(timer_event.get("motive"), 180)
            )
            sanitizer = getattr(self.plugin, "_sanitize_friend_proactive_plan_fields", None)
            if callable(sanitizer):
                sanitized = sanitizer(
                    user,
                    reason=reason,
                    action=action,
                    topic=topic,
                    motive=motive,
                )
                reason = self._single_line(sanitized.get("reason"), 40) or reason
                action = self._single_line(sanitized.get("action"), 40) or action
                topic = self._single_line(sanitized.get("topic"), 80)
                motive = self._single_line(sanitized.get("motive"), 180)
            semantic = planned_semantic_snapshot(user)
            window = planned_window_snapshot(user)
            model_result = user.get("planned_proactive_model_judge_result")
            if not isinstance(model_result, dict):
                model_result = {}
            judged_at = self._float(user.get("planned_proactive_model_judge_at"))
            items.append(
                {
                    "user_id": str(user_id),
                    "user_label": user_summary.get("display_name") or str(user_id),
                    "user_role": user_summary.get("relationship_role") or "",
                    "user_role_label": user_summary.get("relationship_role_label") or "",
                    "source": source,
                    "source_label": self._proactive_source_label(source),
                    "source_note": self._proactive_source_note(source),
                    "status": status,
                    "action": action,
                    "reason": reason,
                    "reason_label": self._proactive_reason_label(reason, target_name=user_summary.get("display_name") or str(user_id)),
                    "reason_detail": self._proactive_reason_detail(
                        reason=reason,
                        source=source,
                        topic=topic,
                        motive=motive,
                        note=user.get("last_proactive_skip_reason"),
                        target_name=user_summary.get("display_name") or str(user_id),
                    ),
                    "topic": topic,
                    "motive": motive,
                    "planned_impulse_id": self._single_line(user.get("planned_proactive_impulse_id"), 40),
                    "planned_candidate_id": self._single_line(user.get("planned_candidate_id"), 40),
                    **semantic,
                    **window,
                    "inner_readiness": readiness,
                    "afterglow": afterglow,
                    "hesitation": hesitation,
                    "model_judge": {
                        "decision": self._single_line(model_result.get("decision"), 24),
                        "score": self._int(model_result.get("score")),
                        "reason": self._single_line(model_result.get("reason"), 140),
                        "cached": bool(model_result.get("cached")),
                        "judged_ts": judged_at,
                        "judged": self.plugin._format_timestamp_elapsed(judged_at),
                    } if model_result or judged_at > 0 else {},
                    "scheduled_ts": scheduled_ts,
                    "scheduled": self.plugin._format_timestamp_elapsed(scheduled_ts),
                    "last_skip": self.plugin._format_timestamp_elapsed(user.get("last_proactive_skip_at", 0)),
                    "last_skip_ts": self._float(user.get("last_proactive_skip_at")),
                    "last_skip_reason": self._single_line(user.get("last_proactive_skip_reason"), 120),
                    "last_skip_prefix": self._single_line(user.get("last_proactive_skip_prefix"), 20),
                    "created_ts": self._float(timer_event.get("created_at")),
                    "created": self.plugin._format_timestamp_elapsed(timer_event.get("created_at", 0)),
                    "origin": self._single_line(timer_event.get("origin"), 40),
                    "backend": self._single_line(timer_event.get("backend"), 40),
                    "job_id": self._single_line(timer_event.get("job_id"), 80),
                    "timer_status": timer_status,
                    "timer_error": self._single_line(timer_event.get("error") or timer_event.get("replace_error"), 180),
                    "replaced_job_id": self._single_line(timer_event.get("replaced_job_id"), 80),
                    "cancelled_job_id": self._single_line(timer_event.get("cancelled_job_id"), 80),
                    "raw_time": self._single_line(timer_event.get("raw_time"), 40),
                    "trigger_message_id": self._single_line(timer_event.get("trigger_message_id"), 120),
                    "trigger_umo": self._single_line(timer_event.get("trigger_umo"), 160),
                    "has_timer_event": bool(timer_event),
                    "silence_until_due": bool(timer_event.get("silence_until_due")) if timer_event else False,
                }
            )

        items.sort(key=lambda item: self._float(item.get("scheduled_ts")))
        raw_audit = data.get("proactive_audit_log") if isinstance(data.get("proactive_audit_log"), list) else []
        seen_audit_signatures: dict[str, dict[str, Any]] = {}
        for raw in raw_audit:
            if not isinstance(raw, dict):
                continue
            meta_leak_checker = getattr(self.plugin, "_framework_agent_meta_summary_leak", None)
            if callable(meta_leak_checker) and (
                meta_leak_checker(str(raw.get("text_preview") or ""))
                or meta_leak_checker(str(raw.get("original_text_preview") or ""))
                or meta_leak_checker(str(raw.get("final_text_preview") or ""))
                or meta_leak_checker(str(raw.get("text") or ""))
                or meta_leak_checker(str(raw.get("note") or ""))
            ):
                continue
            user_id = str(raw.get("user_id") or "")
            user = users.get(user_id) if isinstance(users.get(user_id), dict) else {}
            user_summary = self._user_summary(user_id, user) if user_id else {}
            status = self._single_line(raw.get("status"), 32) or "unknown"
            note = self._single_line(raw.get("note"), 180)
            obsolete_checker = getattr(self.plugin, "_proactive_audit_note_is_obsolete_fixed_error", None)
            if callable(obsolete_checker) and obsolete_checker(note):
                status = "obsolete"
                note = "旧版本主动发送变量错误，当前版本已修复"
            if status == "obsolete":
                continue
            audit_reason = self._single_line(raw.get("reason"), 40)
            audit_action = self._single_line(raw.get("action"), 60)
            audit_topic = self._single_line(raw.get("topic"), 100)
            audit_motive = self._single_line(raw.get("motive"), 180)
            sanitizer = getattr(self.plugin, "_sanitize_friend_proactive_plan_fields", None)
            if isinstance(user, dict) and callable(sanitizer):
                sanitized = sanitizer(
                    user,
                    reason=audit_reason,
                    action=audit_action,
                    topic=audit_topic,
                    motive=audit_motive,
                )
                audit_reason = self._single_line(sanitized.get("reason"), 40) or audit_reason
                audit_action = self._single_line(sanitized.get("action"), 60) or audit_action
                audit_topic = self._single_line(sanitized.get("topic"), 100)
                audit_motive = self._single_line(sanitized.get("motive"), 180)
            updated_ts = self._float(raw.get("updated_ts"))
            bucket = int(updated_ts // 300) if updated_ts > 0 else 0
            signature = "|".join(
                self._single_line(value, 120)
                for value in (
                    user_id,
                    status,
                    self._single_line(raw.get("source"), 40),
                    audit_reason,
                    audit_action,
                    audit_topic,
                    audit_motive,
                    note,
                    bucket,
                )
            )
            text_preview = self._display_message_text(raw.get("text_preview"), 180)
            original_text_preview = self._display_message_text(raw.get("original_text_preview"), 180)
            final_text_preview = self._display_message_text(raw.get("final_text_preview"), 180)
            existing = seen_audit_signatures.get(signature)
            if existing is not None:
                previous_updated = self._float(existing.get("updated_ts"))
                existing["updated_ts"] = max(previous_updated, updated_ts)
                existing["updated"] = self.plugin._format_timestamp_elapsed(existing["updated_ts"])
                existing["duplicate_count"] = max(1, self._int(existing.get("duplicate_count"))) + max(1, self._int(raw.get("duplicate_count")))
                if updated_ts >= previous_updated:
                    if text_preview:
                        existing["text_preview"] = text_preview
                    if original_text_preview:
                        existing["original_text_preview"] = original_text_preview
                    if final_text_preview:
                        existing["final_text_preview"] = final_text_preview
                continue
            audit_status_counts[status] = audit_status_counts.get(status, 0) + 1
            item = {
                "id": self._single_line(raw.get("id"), 40),
                "user_id": user_id,
                "user_label": user_summary.get("display_name") or user_id,
                "user_role": user_summary.get("relationship_role") or "",
                "user_role_label": user_summary.get("relationship_role_label") or "",
                "status": status,
                "source": self._single_line(raw.get("source"), 40),
                "source_label": self._proactive_source_label(raw.get("source")),
                "source_note": self._proactive_source_note(raw.get("source")),
                "reason": audit_reason,
                "reason_label": self._proactive_reason_label(audit_reason, target_name=user_summary.get("display_name") or user_id),
                "reason_detail": self._proactive_reason_detail(
                    reason=audit_reason,
                    source=raw.get("source"),
                    topic=audit_topic,
                    motive=audit_motive,
                    note=note,
                    target_name=user_summary.get("display_name") or user_id,
                ),
                "action": audit_action,
                "topic": audit_topic,
                "motive": audit_motive,
                "semantic_kind": self._single_line(raw.get("semantic_kind"), 40),
                "semantic_anchor_type": self._single_line(raw.get("semantic_anchor_type"), 40),
                "semantic_score": self._int(raw.get("semantic_score")),
                "semantic_pressure": self._int(raw.get("semantic_pressure")),
                "semantic_risk": self._int(raw.get("semantic_risk")),
                "semantic_note": self._single_line(raw.get("semantic_note"), 180),
                "note": note,
                "text_preview": text_preview,
                "original_text_preview": original_text_preview,
                "final_text_preview": final_text_preview,
                "scheduled_ts": self._float(raw.get("scheduled_ts")),
                "scheduled": self.plugin._format_timestamp_elapsed(raw.get("scheduled_ts", 0)),
                "created_ts": self._float(raw.get("created_ts")),
                "created": self.plugin._format_timestamp_elapsed(raw.get("created_ts", 0)),
                "updated_ts": updated_ts,
                "updated": self.plugin._format_timestamp_elapsed(raw.get("updated_ts", 0)),
                "candidate_id": self._single_line(raw.get("candidate_id"), 40),
                "has_image": bool(self._single_line(raw.get("image_path"), 260)),
                "extra_count": self._int(raw.get("extra_count")),
                "duplicate_count": max(1, self._int(raw.get("duplicate_count"))),
            }
            seen_audit_signatures[signature] = item
            audit_items.append(item)
        audit_items.sort(key=lambda item: self._float(item.get("updated_ts") or item.get("created_ts")), reverse=True)
        runtime = data.get("proactive_runtime") if isinstance(data.get("proactive_runtime"), dict) else {}
        last_tick_started = self._float(runtime.get("last_tick_started_at")) if runtime else 0
        last_tick_finished = self._float(runtime.get("last_tick_finished_at")) if runtime else 0
        tick_age = now - max(last_tick_started, last_tick_finished) if max(last_tick_started, last_tick_finished) > 0 else -1
        expected_interval = max(30, self._int(getattr(self.plugin, "check_interval_seconds", 60)))
        return {
            "total": len(items),
            "source_counts": source_counts,
            "status_counts": status_counts,
            "items": items[:30],
            "audit_total": len(audit_items),
            "audit_status_counts": audit_status_counts,
            "audit_items": audit_items[:40],
            "user_states": sorted(
                user_states,
                key=lambda item: (
                    0 if item.get("user_role") == "owner" else 1,
                    self._float(item.get("next_proactive_ts")) if self._float(item.get("next_proactive_ts")) > 0 else 9999999999,
                    self._single_line(item.get("user_label"), 40),
                ),
            )[:40],
            "runtime": {
                "last_tick_started_ts": last_tick_started,
                "last_tick_started": self.plugin._format_timestamp_elapsed(last_tick_started),
                "last_tick_finished_ts": last_tick_finished,
                "last_tick_finished": self.plugin._format_timestamp_elapsed(last_tick_finished),
                "tick_age_seconds": round(tick_age, 1) if tick_age >= 0 else -1,
                "expected_interval_seconds": expected_interval,
                "healthy": bool(tick_age >= 0 and tick_age <= max(180, expected_interval * 4)),
                "last_tick_error": self._single_line(runtime.get("last_tick_error"), 180) if runtime else "",
            },
        }

    # ============================================================
    # Creative Project Management Endpoints
    # ============================================================

    def _creative_project_payload(self, project: dict[str, Any]) -> dict[str, Any]:
        chunks = project.get("draft_chunks") if isinstance(project.get("draft_chunks"), list) else []
        chunk_items = []
        for idx, chunk in enumerate(chunks):
            if not isinstance(chunk, dict):
                continue
            chunk_ts = chunk.get("at", 0) or chunk.get("created_at", 0) or chunk.get("created_ts", 0)
            chunk_items.append(
                {
                    "index": idx,
                    "text": self._single_line(chunk.get("text"), 8000),
                    "chars": self._int(chunk.get("chars")),
                    "at": chunk_ts,
                    "created": self.plugin._format_timestamp_elapsed(chunk_ts),
                    "manually_edited": bool(chunk.get("manually_edited")),
                }
            )
        return {
            "id": self._single_line(project.get("id"), 32),
            "title": self._single_line(project.get("title"), 80),
            "work_type": self._single_line(project.get("work_type"), 40) or "短篇小说",
            "premise": self._single_line(project.get("premise"), 500),
            "tone": self._single_line(project.get("tone"), 80),
            "point_of_view": self._single_line(project.get("point_of_view"), 60),
            "source": self._single_line(project.get("source"), 40),
            "source_text": self._single_line(project.get("source_text"), 500),
            "status": self._single_line(project.get("status"), 24),
            "current_chars": self._int(project.get("current_chars")),
            "target_chars": self._int(project.get("target_chars")),
            "next_hint": self._single_line(project.get("next_hint"), 240),
            "outline": project.get("outline") if isinstance(project.get("outline"), list) else [],
            "characters": project.get("characters") if isinstance(project.get("characters"), list) else [],
            "story_bible": project.get("story_bible") if isinstance(project.get("story_bible"), dict) else {},
            "revision_notes": self._limited_list(project.get("revision_notes"), 20),
            "quality_reviews": self._limited_list(project.get("quality_reviews"), 20),
            "manual_edits": self._limited_list(project.get("manual_edits"), 30),
            "creative_memory_pool": self._limited_list(project.get("creative_memory_pool"), 50),
            "last_manual_edit_at": self.plugin._format_timestamp_elapsed(project.get("last_manual_edit_at", 0)),
            "last_manual_edit_summary": self._single_line(project.get("last_manual_edit_summary"), 160),
            "chunks": chunk_items,
            "chunk_count": len(chunk_items),
            "milestones": project.get("disclosed_milestones") if isinstance(project.get("disclosed_milestones"), list) else [],
            "created_at": self.plugin._format_timestamp_elapsed(project.get("created_at", 0)),
            "last_advanced": self.plugin._format_timestamp_elapsed(project.get("last_advanced_at", 0)),
            "next_advance": self.plugin._format_timestamp_elapsed(project.get("next_advance_at", 0)),
        }

    async def get_creative_project(self) -> dict[str, Any]:
        project_id = str(request.args.get("id", "")).strip()
        if not project_id:
            return self._error("缺少 id")
        try:
            async with self.plugin._data_lock:
                projects = self.plugin.data.get("creative_projects") if isinstance(self.plugin.data.get("creative_projects"), list) else []
                project = next((p for p in projects if isinstance(p, dict) and p.get("id") == project_id), None)
                if not project:
                    return self._error("作品不存在")
                snapshot = deepcopy(project)
            return self._ok(self._creative_project_payload(snapshot))
        except Exception as exc:
            logger.error("[PrivateCompanionPage] 获取创作项目详情失败: %s", exc, exc_info=True)
            return self._error(str(exc))

    async def update_creative_project(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        project_id = str(payload.get("id", "")).strip()
        if not project_id:
            return self._error("缺少 id")
        try:
            changed_notes: list[str] = []
            async with self.plugin._data_lock:
                projects = self.plugin.data.get("creative_projects") if isinstance(self.plugin.data.get("creative_projects"), list) else []
                project = next((p for p in projects if isinstance(p, dict) and p.get("id") == project_id), None)
                if not project:
                    return self._error("作品不存在")
                for key, limit in (
                    ("title", 80),
                    ("work_type", 40),
                    ("premise", 500),
                    ("tone", 80),
                    ("point_of_view", 60),
                    ("next_hint", 240),
                    ("source_text", 500),
                ):
                    if key not in payload:
                        continue
                    value = self._single_line(payload.get(key), limit)
                    if value != self._single_line(project.get(key), limit):
                        project[key] = value
                        changed_notes.append(f"{key}: {value}")
                if "status" in payload:
                    status = self._single_line(payload.get("status"), 24)
                    if status in ("drafting", "finished", "paused") and status != project.get("status"):
                        project["status"] = status
                        changed_notes.append(f"status: {status}")
                if "target_chars" in payload:
                    target_chars = _safe_int(payload.get("target_chars"), 2000, 300, 5200)
                    if target_chars != self._int(project.get("target_chars")):
                        project["target_chars"] = target_chars
                        changed_notes.append(f"target_chars: {target_chars}")
                if changed_notes:
                    story_bible_getter = getattr(self.plugin, "_get_or_create_story_bible", None)
                    if callable(story_bible_getter):
                        story_bible = story_bible_getter(project)
                        if "premise" in payload:
                            story_bible["mainline_direction"] = self._single_line(project.get("premise"), 160)
                        if "next_hint" in payload:
                            story_bible["next_direction"] = self._single_line(project.get("next_hint"), 160)
                    edits = project.setdefault("manual_edits", [])
                    if not isinstance(edits, list):
                        edits = []
                        project["manual_edits"] = edits
                    now = time.time()
                    note = "；".join(changed_notes)
                    edits.append(
                        {
                            "id": secrets.token_hex(6),
                            "type": "project_meta",
                            "title": "更新作品信息",
                            "content": note[:2000],
                            "chunk_index": -1,
                            "created_at": now,
                        }
                    )
                    del edits[:-10]
                    project["last_manual_edit_at"] = now
                    project["last_manual_edit_summary"] = "更新作品信息"
                    pool_getter = getattr(self.plugin, "_get_or_create_memory_pool", None)
                    add_memory = getattr(self.plugin, "_add_memory_entry", None)
                    extract_keywords = getattr(self.plugin, "_extract_creative_keywords", None)
                    if callable(pool_getter) and callable(add_memory):
                        keywords = extract_keywords(note, limit=8) if callable(extract_keywords) else ["作品信息"]
                        add_memory(
                            pool_getter(project),
                            project_id,
                            "revision",
                            f"更新作品信息: {self._single_line(note, 180)}",
                            keywords or ["作品信息"],
                            importance=5,
                        )
                self.plugin._save_data_sync()
            return self._ok({"project_id": project_id, "changed": bool(changed_notes), "message": "作品已更新"})
        except Exception as exc:
            logger.error("[PrivateCompanionPage] 更新创作项目失败: %s", exc, exc_info=True)
            return self._error(str(exc))

    async def update_creative_chunk(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        project_id = str(payload.get("id", "")).strip()
        chunk_index = _safe_int(payload.get("chunk_index"), -1, -1)
        text = str(payload.get("text", "")).strip()
        if not project_id:
            return self._error("缺少 id")
        if chunk_index < 0:
            return self._error("缺少 chunk_index")
        if not text:
            return self._error("缺少 text")
        try:
            apply_edit = getattr(self.plugin, "_apply_creative_manual_edit", None)
            if not callable(apply_edit):
                return self._error("创作编辑能力不可用")
            result = await apply_edit(project_id, "chunk_text", text, f"修改第{chunk_index + 1}段", chunk_index)
            if not result.get("success"):
                return self._error(result.get("error") or "片段更新失败")
            return self._ok({"project_id": project_id, "chunk_index": chunk_index, "message": "片段已更新"})
        except Exception as exc:
            logger.error("[PrivateCompanionPage] 更新创作片段失败: %s", exc, exc_info=True)
            return self._error(str(exc))

    async def update_creative_outline(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        project_id = str(payload.get("id", "")).strip()
        outline_text = str(payload.get("outline", "")).strip()
        if not project_id:
            return self._error("缺少 id")
        try:
            apply_edit = getattr(self.plugin, "_apply_creative_manual_edit", None)
            if not callable(apply_edit):
                return self._error("创作编辑能力不可用")
            result = await apply_edit(project_id, "outline", outline_text, "更新大纲", -1)
            if not result.get("success"):
                return self._error(result.get("error") or "大纲更新失败")
            return self._ok({"project_id": project_id, "message": "大纲已更新"})
        except Exception as exc:
            logger.error("[PrivateCompanionPage] 更新创作大纲失败: %s", exc, exc_info=True)
            return self._error(str(exc))

    async def update_creative_characters(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        project_id = str(payload.get("id", "")).strip()
        raw_characters = payload.get("characters")
        if not project_id:
            return self._error("缺少 id")
        if not isinstance(raw_characters, list):
            return self._error("角色必须是数组")
        try:
            characters = [item for item in raw_characters if isinstance(item, dict)]
            apply_edit = getattr(self.plugin, "_apply_creative_manual_edit", None)
            if not callable(apply_edit):
                return self._error("创作编辑能力不可用")
            result = await apply_edit(
                project_id,
                "characters",
                json.dumps(characters, ensure_ascii=False),
                "更新角色表",
                -1,
            )
            if not result.get("success"):
                return self._error(result.get("error") or "角色更新失败")
            return self._ok({"project_id": project_id, "message": "角色已更新"})
        except Exception as exc:
            logger.error("[PrivateCompanionPage] 更新创作角色失败: %s", exc, exc_info=True)
            return self._error(str(exc))

    async def reanalyze_creative_project(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        project_id = str(payload.get("id", "")).strip()
        if not project_id:
            return self._error("缺少 id")
        try:
            async with self.plugin._data_lock:
                projects = self.plugin.data.get("creative_projects") if isinstance(self.plugin.data.get("creative_projects"), list) else []
                project = next((p for p in projects if isinstance(p, dict) and p.get("id") == project_id), None)
                if not project:
                    return self._error("作品不存在")
                snapshot = deepcopy(project)
            chunks = snapshot.get("draft_chunks") if isinstance(snapshot.get("draft_chunks"), list) else []
            latest = next((c for c in reversed(chunks) if isinstance(c, dict) and self._single_line(c.get("text"), 80)), None)
            if not latest:
                return self._error("还没有正文片段，无法分析")
            story_bible = snapshot.get("story_bible") if isinstance(snapshot.get("story_bible"), dict) else {}
            outline = "\n".join(snapshot.get("outline", [])) if isinstance(snapshot.get("outline"), list) else ""
            safe_recent_chunks = [c for c in chunks[-5:] if isinstance(c, dict)]
            reviewer = getattr(self.plugin, "_review_creative_chunk", None)
            if not callable(reviewer):
                return self._error("创作审校能力不可用")
            review = await reviewer(snapshot, story_bible, outline, latest.get("text", ""), safe_recent_chunks)
            if not isinstance(review, dict):
                review = {}
            async with self.plugin._data_lock:
                projects = self.plugin.data.get("creative_projects") if isinstance(self.plugin.data.get("creative_projects"), list) else []
                project = next((p for p in projects if isinstance(p, dict) and p.get("id") == project_id), None)
                if not project:
                    return self._error("作品不存在")
                review["id"] = secrets.token_hex(6)
                review["chunk_index"] = len(project.get("draft_chunks") or []) - 1
                review["created_at"] = time.time()
                reviews = project.setdefault("quality_reviews", [])
                if not isinstance(reviews, list):
                    reviews = []
                    project["quality_reviews"] = reviews
                reviews.append(review)
                del reviews[:-20]
                self.plugin._save_data_sync()
            return self._ok({"project_id": project_id, "review": review})
        except Exception as exc:
            logger.error("[PrivateCompanionPage] 创作项目质量分析失败: %s", exc, exc_info=True)
            return self._error(str(exc))

    async def rebuild_creative_memory(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        project_id = str(payload.get("id", "")).strip()
        if not project_id:
            return self._error("缺少 id")
        try:
            rebuild = getattr(self.plugin, "_rebuild_creative_memory_from_project", None)
            if not callable(rebuild):
                return self._error("创作记忆重建能力不可用")
            result = await rebuild(project_id)
            if not result.get("success"):
                return self._error(result.get("error") or "重建失败")
            return self._ok(result)
        except Exception as exc:
            logger.error("[PrivateCompanionPage] 重建创作记忆失败: %s", exc, exc_info=True)
            return self._error(str(exc))

    async def delete_creative_project(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        project_id = str(payload.get("id", "")).strip()
        if not project_id:
            return self._error("缺少 id")
        try:
            async with self.plugin._data_lock:
                projects = self.plugin.data.get("creative_projects") if isinstance(self.plugin.data.get("creative_projects"), list) else []
                before = len(projects)
                self.plugin.data["creative_projects"] = [
                    p for p in projects if not (isinstance(p, dict) and p.get("id") == project_id)
                ]
                removed = before - len(self.plugin.data["creative_projects"])
                self.plugin._save_data_sync()
            return self._ok({"project_id": project_id, "removed": removed})
        except Exception as exc:
            logger.error("[PrivateCompanionPage] 删除创作项目失败: %s", exc, exc_info=True)
            return self._error(str(exc))

    def _creative_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        projects = data.get("creative_projects") if isinstance(data.get("creative_projects"), list) else []
        items = [item for item in projects if isinstance(item, dict)]
        active = [item for item in items if item.get("status") == "drafting"]
        latest = items[-1] if items else {}
        return {
            "enabled": bool(getattr(self.plugin, "enable_creative_writing", False)),
            "hidden_mode": bool(getattr(self.plugin, "creative_hidden_mode", False)),
            "project_count": len(items),
            "active_projects": len(active),
            "latest_title": self._single_line(latest.get("title"), 60) if isinstance(latest, dict) else "",
            "latest_status": self._single_line(latest.get("status"), 24) if isinstance(latest, dict) else "",
            "latest_progress": {
                "current_chars": self._int(latest.get("current_chars")) if isinstance(latest, dict) else 0,
                "target_chars": self._int(latest.get("target_chars")) if isinstance(latest, dict) else 0,
            },
            "items": [
                {
                    "id": self._single_line(item.get("id"), 20),
                    "title": self._single_line(item.get("title"), 60),
                    "work_type": self._single_line(item.get("work_type"), 30) or "短篇小说",
                    "premise": self._single_line(item.get("premise"), 160),
                    "tone": self._single_line(item.get("tone"), 40),
                    "point_of_view": self._single_line(item.get("point_of_view"), 40) or "第三人称有限视角",
                    "source": self._single_line(item.get("source_text"), 160),
                    "status": self._single_line(item.get("status"), 24),
                    "current_chars": self._int(item.get("current_chars")),
                    "target_chars": self._int(item.get("target_chars")),
                    "chunk_count": len(item.get("draft_chunks") or []) if isinstance(item.get("draft_chunks"), list) else 0,
                    "outline_count": len(item.get("outline") or []) if isinstance(item.get("outline"), list) else 0,
                    "character_count": len(item.get("characters") or []) if isinstance(item.get("characters"), list) else 0,
                    "has_story_bible": bool(item.get("story_bible")) if isinstance(item.get("story_bible"), dict) else False,
                    "review_count": len(item.get("quality_reviews") or []) if isinstance(item.get("quality_reviews"), list) else 0,
                    "manual_edit_count": len(item.get("manual_edits") or []) if isinstance(item.get("manual_edits"), list) else 0,
                    "latest_snippet": self._single_line(
                        (item.get("draft_chunks") or [])[-1].get("text") if isinstance(item.get("draft_chunks"), list) and item.get("draft_chunks") else "",
                        260,
                    ),
                    "milestones": item.get("disclosed_milestones") if isinstance(item.get("disclosed_milestones"), list) else [],
                    "created_at": self.plugin._format_timestamp_elapsed(item.get("created_at", 0)),
                    "last_advanced": self.plugin._format_timestamp_elapsed(item.get("last_advanced_at", 0)),
                    "next_advance": self.plugin._format_timestamp_elapsed(item.get("next_advance_at", 0)),
                }
                for item in items[-6:]
            ],
        }

    def _daily_state_summary(self, state: Any) -> dict[str, Any]:
        if not isinstance(state, dict):
            return {}
        keys = ["date", "sleep", "dream", "health", "hunger", "body_cycle", "location", "weather", "mood_bias", "energy", "note"]
        summary = {key: state.get(key, "") for key in keys}
        runtime = state.get("sleep_runtime") if isinstance(state.get("sleep_runtime"), dict) else {}
        if runtime:
            summary["sleep_phase"] = self._single_line(runtime.get("label") or runtime.get("phase"), 40)
            summary["sleep_runtime"] = {
                "phase": self._single_line(runtime.get("phase"), 40),
                "label": self._single_line(runtime.get("label") or runtime.get("phase"), 40),
                "last_event": self._single_line(runtime.get("last_event"), 120),
                "source": self._single_line(runtime.get("source"), 40),
                "woken_count": self._int(runtime.get("woken_count")),
                "updated_at": self.plugin._format_timestamp_elapsed(runtime.get("updated_at", 0)),
            }
        return summary

    def _life_observation_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        dream = data.get("daily_dream") if isinstance(data.get("daily_dream"), dict) else {}
        diaries = data.get("bot_diaries") if isinstance(data.get("bot_diaries"), list) else []
        fragments = data.get("dream_fragments") if isinstance(data.get("dream_fragments"), list) else []
        plan = data.get("daily_plan") if isinstance(data.get("daily_plan"), dict) else {}
        story = data.get("daily_story_plan") if isinstance(data.get("daily_story_plan"), dict) else {}
        current_item = {}
        try:
            picked = self.plugin._get_current_plan_item(plan)
            current_item = picked if isinstance(picked, dict) else {}
        except Exception:
            current_item = {}

        return {
            "dream": {
                "date": self._single_line(dream.get("date"), 24),
                "label": self._single_line(dream.get("label"), 120),
                "dream_type": self._single_line(dream.get("dream_type"), 40),
                "content": self._single_line(dream.get("content"), 1000),
                "afterglow": self._single_line(dream.get("afterglow"), 220),
                "mood": self._single_line(dream.get("mood"), 30),
                "energy_delta": self._int(dream.get("energy_delta")),
                "duration_hours": self._int(dream.get("duration_hours")),
                "generated_at": self._single_line(dream.get("generated_at"), 24),
                "factors": [self._single_line(item, 40) for item in dream.get("factors", [])[:8] if self._single_line(item, 40)]
                if isinstance(dream.get("factors"), list)
                else [],
            },
            "diaries": [
                {
                    "date": self._single_line(item.get("date"), 24),
                    "summary": self._single_line(item.get("summary"), 180),
                    "body": self._single_line(item.get("body"), 500),
                    "share_seed": self._single_line(item.get("share_seed"), 140),
                    "tags": [self._single_line(tag, 20) for tag in item.get("tags", [])[:6] if self._single_line(tag, 20)]
                    if isinstance(item.get("tags"), list)
                    else [],
                    "generated_at": self._single_line(item.get("generated_at"), 24),
                }
                for item in diaries[-4:]
                if isinstance(item, dict)
            ],
            "dream_fragments": self._limited_dream_fragments(fragments),
            "current_plan": {
                "time": self._single_line(current_item.get("time"), 12),
                "activity": self._single_line(current_item.get("activity"), 600),
                "mood": self._single_line(current_item.get("mood"), 40),
                "message_seed": self._single_line(current_item.get("message_seed"), 500),
            },
            "story": {
                "date": self._single_line(story.get("date"), 24),
                "today_events": self._limited_story_items(story.get("today_events"), 4),
                "proactive_events": self._limited_story_items(story.get("proactive_events"), 4),
            },
        }

    def _daily_timeline_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        plan = data.get("daily_plan") if isinstance(data.get("daily_plan"), dict) else {}
        enhanced = data.get("detail_enhanced_segments") if isinstance(data.get("detail_enhanced_segments"), dict) else {}
        story = data.get("daily_story_plan") if isinstance(data.get("daily_story_plan"), dict) else {}
        adjustments = data.get("schedule_adjustments") if isinstance(data.get("schedule_adjustments"), list) else []
        presence = data.get("qq_presence_state") if isinstance(data.get("qq_presence_state"), dict) else {}

        segments: list[dict[str, Any]] = []
        for key, snapshot in enhanced.items():
            if not isinstance(snapshot, dict):
                continue
            segment = self._segment_from_key(str(key), plan, snapshot)
            segments.append(
                {
                    "key": str(key),
                    "window": segment.get("window", str(key)),
                    "start": segment.get("start", 99999),
                    "status": snapshot.get("status", ""),
                    "started_at": snapshot.get("started_at", ""),
                    "summary": snapshot.get("summary", ""),
                    "error": snapshot.get("error", ""),
                    "retry_after": snapshot.get("retry_after", ""),
                    "state_variables": self._limited_state_variables(snapshot.get("state_variables")),
                    "presence_status": snapshot.get("presence_status") if isinstance(snapshot.get("presence_status"), dict) else {},
                    "interaction_updates": self._limited_interaction_updates(snapshot.get("interaction_updates")),
                    "today_events": self._limited_story_items(snapshot.get("today_events"), 5),
                    "proactive_events": self._limited_story_items(snapshot.get("proactive_events"), 4),
                }
            )
        segments.sort(key=lambda item: item.get("start", 99999))

        return {
            "plan_date": plan.get("date", ""),
            "story_date": story.get("date", ""),
            "detail_day": data.get("detail_enhanced_day", ""),
            "segment_count": len(segments),
            "segments": segments,
            "story_today_events": self._limited_story_items(story.get("today_events"), 48),
            "story_proactive_events": self._limited_story_items(story.get("proactive_events"), 32),
            "adjustments": self._limited_adjustments(adjustments),
            "qq_presence_state": presence,
        }

    def _segment_from_key(self, key: str, plan: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
        keyed = re.fullmatch(r"(\d{4}-\d{2}-\d{2}):(\d+):(\d{1,2}:\d{2})", key)
        if keyed:
            index = int(keyed.group(2))
            start = self.plugin._parse_hhmm_to_minutes(keyed.group(3))
            items = plan.get("items") if isinstance(plan, dict) else []
            end = None
            if isinstance(items, list):
                for next_item in items[index + 1:]:
                    if isinstance(next_item, dict):
                        end = self.plugin._parse_hhmm_to_minutes(next_item.get("time"))
                        if end is not None:
                            break
            if start is not None:
                if end is None:
                    item = items[index] if isinstance(items, list) and index < len(items) and isinstance(items[index], dict) else None
                    end = self.plugin._segment_end_minutes(start, item)
                return {"window": f"{self.plugin._minutes_to_hhmm(start)}-{self.plugin._minutes_to_hhmm(end)}", "start": start}

        inferred = self._segment_from_story_windows(snapshot)
        if inferred:
            return inferred

        match = re.search(r"(?:^|[:|_])(\d{1,4})[-_](\d{1,4})(?:$|[:|_])", key)
        if not match:
            return {"window": key, "start": 99999}
        start = int(match.group(1))
        end = int(match.group(2))
        if not (0 <= start < 24 * 60 and 0 < end <= 28 * 60):
            return {"window": key, "start": 99999}
        return {"window": f"{self.plugin._minutes_to_hhmm(start)}-{self.plugin._minutes_to_hhmm(end)}", "start": start}

    def _memory_plugin_token_usage_raw(self) -> dict[str, Any]:
        getter = getattr(self.plugin, "_memory_companion_token_usage_summary", None)
        if not callable(getter):
            return {"available": False, "display_name": "我会牢牢记住你", "reason": "陪伴插件当前未接入记忆插件桥"}
        try:
            usage = getter()
        except Exception as exc:
            return {"available": False, "display_name": "我会牢牢记住你", "reason": self._single_line(exc, 160)}
        if not isinstance(usage, dict):
            return {"available": False, "display_name": "我会牢牢记住你", "reason": "记忆插件返回的 Token 统计格式无效"}
        usage.setdefault("available", True)
        usage.setdefault("display_name", "我会牢牢记住你")
        usage.setdefault("counted_in_private_companion_budget", False)
        return usage

    def _token_stats_payload(self, usage: Any) -> dict[str, Any]:
        if not isinstance(usage, dict):
            usage = {}
        external_usage = usage.get("external") if isinstance(usage.get("external"), dict) else {}
        memory_plugin_usage = self._memory_plugin_token_usage_raw()
        totals = self._token_bucket(usage.get("totals"))
        by_provider = self._token_ranked_map(usage.get("by_provider"))
        by_task = self._token_ranked_map(usage.get("by_task"))
        by_day = self._token_series_map(usage.get("by_day"), limit=30)
        by_day_provider_raw = usage.get("by_day_provider") if isinstance(usage.get("by_day_provider"), dict) else {}
        by_day_task_raw = usage.get("by_day_task") if isinstance(usage.get("by_day_task"), dict) else {}
        by_day_detail = []
        for item in by_day:
            day_key = item.get("key", "")
            providers = self._token_ranked_map(by_day_provider_raw.get(day_key))[:5]
            tasks = self._token_ranked_map(by_day_task_raw.get(day_key))[:6]
            by_day_detail.append({**item, "providers": providers, "tasks": tasks})
        by_hour = self._token_series_map(usage.get("by_hour"), limit=48)
        today_key = _today_key()
        today_bucket = usage.get("by_day", {}).get(today_key, {}) if isinstance(usage.get("by_day"), dict) else {}
        today_total_tokens = self._int(today_bucket.get("total_tokens")) if isinstance(today_bucket, dict) else 0
        exempt_by_day = usage.get("budget_exempt_by_day") if isinstance(usage.get("budget_exempt_by_day"), dict) else {}
        today_exempt_bucket = exempt_by_day.get(today_key, {}) if isinstance(exempt_by_day, dict) else {}
        today_exempt_tokens = self._int(today_exempt_bucket.get("total_tokens")) if isinstance(today_exempt_bucket, dict) else 0
        if today_exempt_tokens <= 0:
            today_tasks = by_day_task_raw.get(today_key, {}) if isinstance(by_day_task_raw, dict) else {}
            if isinstance(today_tasks, dict):
                is_exempt_task = getattr(self.plugin, "_is_llm_budget_exempt_task", None)
                today_exempt_tokens = sum(
                    self._int(bucket.get("total_tokens"))
                    for task, bucket in today_tasks.items()
                    if (
                        (
                            callable(is_exempt_task)
                            and is_exempt_task(task)
                        )
                        or (
                            not callable(is_exempt_task)
                            and str(task) in {"proactive_framework", "voice_framework"}
                        )
                    )
                    and isinstance(bucket, dict)
                )
        today_tokens = max(0, today_total_tokens - today_exempt_tokens)
        daily_limit = self._int(getattr(self.plugin, "daily_token_limit", 0))
        soft_limit = self._int(getattr(self.plugin, "daily_token_soft_limit", 0))
        soft_enabled = bool(getattr(self.plugin, "enable_daily_token_soft_limit", True))
        budget_skips = usage.get("budget_skips", {})
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
        recent_raw = usage.get("recent")
        recent = []
        if isinstance(recent_raw, list):
            for item in recent_raw[-80:][::-1]:
                if not isinstance(item, dict):
                    continue
                recent.append(
                    {
                        "time": self._single_line(item.get("time"), 24),
                        "ts": self._float(item.get("ts")),
                        "provider": self._single_line(item.get("provider"), 80),
                        "task": self._single_line(item.get("task"), 40),
                        "success": bool(item.get("success", True)),
                        "prompt_tokens": self._int(item.get("prompt_tokens")),
                        "completion_tokens": self._int(item.get("completion_tokens")),
                        "total_tokens": self._int(item.get("total_tokens")),
                        "cached_tokens": self._int(item.get("cached_tokens")),
                        "cache_read_tokens": self._int(item.get("cache_read_tokens")),
                        "cache_write_tokens": self._int(item.get("cache_write_tokens")),
                        "estimated": bool(item.get("estimated", False)),
                        "elapsed_ms": self._int(item.get("elapsed_ms")),
                        "prompt_chars": self._int(item.get("prompt_chars")),
                        "completion_chars": self._int(item.get("completion_chars")),
                        "error": self._single_line(item.get("error"), 160),
                        "budget_exempt": bool(item.get("budget_exempt", False)),
                    }
                )
        return {
            "updated_at": self._single_line(usage.get("updated_at"), 24),
            "totals": totals,
            "by_provider": by_provider,
            "by_task": by_task,
            "by_day": by_day,
            "by_day_detail": by_day_detail,
            "by_hour": by_hour,
            "budget": budget,
            "recent": recent,
            "external": self._token_external_payload(external_usage),
            "memory_plugin": self._token_memory_plugin_payload(memory_plugin_usage),
        }

    def _token_external_payload(self, usage: Any) -> dict[str, Any]:
        if not isinstance(usage, dict):
            usage = {}
        by_day = self._token_series_map(usage.get("by_day"), limit=30)
        by_day_provider_raw = usage.get("by_day_provider") if isinstance(usage.get("by_day_provider"), dict) else {}
        by_day_task_raw = usage.get("by_day_task") if isinstance(usage.get("by_day_task"), dict) else {}
        by_day_session_raw = usage.get("by_day_session") if isinstance(usage.get("by_day_session"), dict) else {}
        by_day_detail = []
        for item in by_day:
            day_key = item.get("key", "")
            by_day_detail.append(
                {
                    **item,
                    "providers": self._token_ranked_map(by_day_provider_raw.get(day_key))[:5],
                    "tasks": self._token_ranked_map(by_day_task_raw.get(day_key))[:6],
                    "sessions": self._token_ranked_map(by_day_session_raw.get(day_key))[:8],
                }
            )
        recent = []
        recent_raw = usage.get("recent")
        if isinstance(recent_raw, list):
            for item in recent_raw[-80:][::-1]:
                if not isinstance(item, dict):
                    continue
                recent.append(
                    {
                        "time": self._single_line(item.get("time"), 24),
                        "ts": self._float(item.get("ts")),
                        "provider": self._single_line(item.get("provider"), 80),
                        "task": self._single_line(item.get("task"), 40),
                        "session": self._single_line(item.get("session"), 160),
                        "sender": self._single_line(item.get("sender"), 80),
                        "message_type": self._single_line(item.get("message_type"), 20),
                        "success": bool(item.get("success", True)),
                        "prompt_tokens": self._int(item.get("prompt_tokens")),
                        "completion_tokens": self._int(item.get("completion_tokens")),
                        "total_tokens": self._int(item.get("total_tokens")),
                        "cached_tokens": self._int(item.get("cached_tokens")),
                        "cache_read_tokens": self._int(item.get("cache_read_tokens")),
                        "cache_write_tokens": self._int(item.get("cache_write_tokens")),
                        "estimated": bool(item.get("estimated", False)),
                        "elapsed_ms": self._int(item.get("elapsed_ms")),
                        "prompt_chars": self._int(item.get("prompt_chars")),
                        "completion_chars": self._int(item.get("completion_chars")),
                        "error": self._single_line(item.get("error"), 160),
                        "external": True,
                    }
                )
        return {
            "updated_at": self._single_line(usage.get("updated_at"), 24),
            "totals": self._token_bucket(usage.get("totals")),
            "by_provider": self._token_ranked_map(usage.get("by_provider")),
            "by_task": self._token_ranked_map(usage.get("by_task")),
            "by_session": self._token_ranked_map(usage.get("by_session")),
            "by_day": by_day,
            "by_day_detail": by_day_detail,
            "by_hour": self._token_series_map(usage.get("by_hour"), limit=48),
            "recent": recent,
        }

    def _token_memory_plugin_payload(self, usage: Any) -> dict[str, Any]:
        if not isinstance(usage, dict):
            usage = {"available": False}
        available = bool(usage.get("available", True))
        by_day = self._token_series_map(usage.get("by_day"), limit=30)
        by_day_provider_raw = usage.get("by_day_provider") if isinstance(usage.get("by_day_provider"), dict) else {}
        by_day_task_raw = usage.get("by_day_task") if isinstance(usage.get("by_day_task"), dict) else {}
        by_day_detail = []
        for item in by_day:
            day_key = item.get("key", "")
            by_day_detail.append(
                {
                    **item,
                    "providers": self._token_ranked_map(by_day_provider_raw.get(day_key))[:5],
                    "tasks": self._token_ranked_map(by_day_task_raw.get(day_key))[:8],
                }
            )
        recent = []
        recent_raw = usage.get("recent")
        if isinstance(recent_raw, list):
            for item in recent_raw[-80:][::-1]:
                if not isinstance(item, dict):
                    continue
                recent.append(
                    {
                        "time": self._single_line(item.get("time"), 24),
                        "ts": self._float(item.get("ts")),
                        "provider": self._single_line(item.get("provider"), 80),
                        "task": self._single_line(item.get("task"), 60),
                        "success": bool(item.get("success", True)),
                        "prompt_tokens": self._int(item.get("prompt_tokens")),
                        "completion_tokens": self._int(item.get("completion_tokens")),
                        "total_tokens": self._int(item.get("total_tokens")),
                        "cached_tokens": self._int(item.get("cached_tokens")),
                        "cache_read_tokens": self._int(item.get("cache_read_tokens")),
                        "cache_write_tokens": self._int(item.get("cache_write_tokens")),
                        "estimated": bool(item.get("estimated", False)),
                        "elapsed_ms": self._int(item.get("elapsed_ms")),
                        "prompt_chars": self._int(item.get("prompt_chars")),
                        "completion_chars": self._int(item.get("completion_chars")),
                        "error": self._single_line(item.get("error"), 160),
                    }
                )
        return {
            "available": available,
            "display_name": self._single_line(usage.get("display_name") or "我会牢牢记住你", 80),
            "plugin_name": self._single_line(usage.get("plugin_name") or "astrbot_plugin_memory_companion", 80),
            "reason": self._single_line(usage.get("reason"), 160),
            "note": self._single_line(
                usage.get("note") or "仅展示记忆插件自身模型消耗，不计入陪伴插件每日 Token 限额。",
                220,
            ),
            "counted_in_private_companion_budget": bool(usage.get("counted_in_private_companion_budget", False)),
            "updated_at": self._single_line(usage.get("updated_at"), 24),
            "totals": self._token_bucket(usage.get("totals")),
            "by_provider": self._token_ranked_map(usage.get("by_provider")),
            "by_task": self._token_ranked_map(usage.get("by_task")),
            "by_day": by_day,
            "by_day_detail": by_day_detail,
            "by_hour": self._token_series_map(usage.get("by_hour"), limit=48),
            "recent": recent,
        }

    @classmethod
    def _token_bucket(cls, value: Any) -> dict[str, Any]:
        bucket = value if isinstance(value, dict) else {}
        calls = cls._int(bucket.get("calls"))
        elapsed = cls._int(bucket.get("elapsed_ms"))
        total_tokens = cls._int(bucket.get("total_tokens"))
        estimated_tokens = cls._int(bucket.get("estimated_tokens"))
        cached_tokens = cls._int(bucket.get("cached_tokens"))
        cache_read_tokens = cls._int(bucket.get("cache_read_tokens"))
        cache_write_tokens = cls._int(bucket.get("cache_write_tokens"))
        return {
            "calls": calls,
            "success": cls._int(bucket.get("success")),
            "errors": cls._int(bucket.get("errors")),
            "prompt_tokens": cls._int(bucket.get("prompt_tokens")),
            "completion_tokens": cls._int(bucket.get("completion_tokens")),
            "total_tokens": total_tokens,
            "cached_tokens": cached_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
            "cached_ratio": round(cached_tokens / total_tokens, 4) if total_tokens > 0 else 0,
            "estimated_tokens": estimated_tokens,
            "estimated_ratio": round(estimated_tokens / total_tokens, 4) if total_tokens > 0 else 0,
            "avg_tokens": round(total_tokens / calls, 1) if calls > 0 else 0,
            "avg_latency_ms": round(elapsed / calls, 1) if calls > 0 else 0,
            "last_ts": cls._float(bucket.get("last_ts")),
        }

    @classmethod
    def _token_ranked_map(cls, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, dict):
            return []
        rows = []
        for key, bucket in value.items():
            item = cls._token_bucket(bucket)
            item["key"] = cls._single_line(key, 120)
            rows.append(item)
        rows.sort(key=lambda item: item.get("total_tokens", 0), reverse=True)
        return rows

    @classmethod
    def _token_series_map(cls, value: Any, *, limit: int) -> list[dict[str, Any]]:
        if not isinstance(value, dict):
            return []
        rows = []
        for key, bucket in value.items():
            item = cls._token_bucket(bucket)
            item["key"] = cls._single_line(key, 32)
            rows.append(item)
        rows.sort(key=lambda item: item.get("key", ""))
        return rows[-limit:]

    @staticmethod
    def _int(value: Any, default: int = 0, minimum: int | None = None, maximum: int | None = None) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        if minimum is not None:
            parsed = max(minimum, parsed)
        if maximum is not None:
            parsed = min(maximum, parsed)
        return parsed

    @staticmethod
    def _float(value: Any, default: float = 0.0, minimum: float | None = None, maximum: float | None = None) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = default
        if minimum is not None:
            parsed = max(minimum, parsed)
        if maximum is not None:
            parsed = min(maximum, parsed)
        return parsed

    def _segment_from_story_windows(self, snapshot: dict[str, Any]) -> dict[str, Any] | None:
        minutes: list[int] = []
        for key in ("today_events", "proactive_events"):
            items = snapshot.get(key)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                start, end = self.plugin._parse_window_minutes(str(item.get("window") or ""))
                if start is not None:
                    minutes.append(start)
                if end is not None:
                    minutes.append(end)
        if not minutes:
            return None
        start = min(minutes)
        end = max(minutes)
        return {"window": f"{self.plugin._minutes_to_hhmm(start)}-{self.plugin._minutes_to_hhmm(end)}", "start": start}

    @staticmethod
    def _limited_state_variables(value: Any) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        items: list[dict[str, str]] = []
        for item in value[:8]:
            if not isinstance(item, dict):
                continue
            items.append(
                {
                    "name": PrivateCompanionPageApi._single_line(item.get("name") or item.get("key"), 40),
                    "value": PrivateCompanionPageApi._single_line(item.get("value"), 80),
                    "note": PrivateCompanionPageApi._single_line(item.get("note"), 100),
                }
            )
        return items

    @staticmethod
    def _limited_interaction_updates(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        items: list[dict[str, Any]] = []
        for item in value[-8:]:
            if not isinstance(item, dict):
                continue
            updates = item.get("state_updates")
            items.append(
                {
                    "at": PrivateCompanionPageApi._single_line(item.get("at"), 12),
                    "source": PrivateCompanionPageApi._single_line(item.get("source"), 24),
                    "reaction": PrivateCompanionPageApi._single_line(item.get("reaction"), 140),
                    "state_updates": [
                        PrivateCompanionPageApi._single_line(update, 80)
                        for update in updates[:6]
                        if PrivateCompanionPageApi._single_line(update, 80)
                    ]
                    if isinstance(updates, list)
                    else [],
                }
            )
        return items

    @staticmethod
    def _limited_story_items(value: Any, limit: int) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        items: list[dict[str, str]] = []
        indexed_items = [
            (PrivateCompanionPageApi._story_item_start_minutes(item), index, item)
            for index, item in enumerate(value)
            if isinstance(item, dict)
        ]
        indexed_items.sort(key=lambda row: (row[0], row[1]))
        for _, _, item in indexed_items[:limit]:
            if not isinstance(item, dict):
                continue
            items.append(
                {
                    "window": PrivateCompanionPageApi._single_line(item.get("window") or item.get("time"), 24),
                    "text": PrivateCompanionPageApi._single_line(
                        item.get("event")
                        or item.get("topic")
                        or item.get("summary")
                        or item.get("text")
                        or item.get("content"),
                        160,
                    ),
                    "mood": PrivateCompanionPageApi._single_line(item.get("mood"), 24),
                    "action": PrivateCompanionPageApi._single_line(item.get("action"), 24),
                    "reason": PrivateCompanionPageApi._single_line(item.get("reason"), 32),
                }
            )
        return items

    @staticmethod
    def _story_item_start_minutes(item: Any) -> int:
        if not isinstance(item, dict):
            return 99999
        text = PrivateCompanionPageApi._single_line(item.get("window") or item.get("time"), 32)
        match = re.search(r"(\d{1,2}):(\d{2})", text)
        if not match:
            return 99999
        hour = int(match.group(1))
        minute = int(match.group(2))
        if hour > 23 or minute > 59:
            return 99999
        return hour * 60 + minute

    @staticmethod
    def _limited_adjustments(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        items: list[dict[str, Any]] = []
        for item in value[-10:]:
            if not isinstance(item, dict):
                continue
            updates = item.get("state_updates")
            items.append(
                {
                    "date": PrivateCompanionPageApi._single_line(item.get("date"), 12),
                    "source": PrivateCompanionPageApi._single_line(item.get("source"), 24),
                    "note": PrivateCompanionPageApi._single_line(item.get("note"), 140),
                    "reaction": PrivateCompanionPageApi._single_line(item.get("immediate_reaction"), 140),
                    "state_updates": [
                        PrivateCompanionPageApi._single_line(update, 80)
                        for update in updates[:6]
                        if PrivateCompanionPageApi._single_line(update, 80)
                    ]
                    if isinstance(updates, list)
                    else [],
                }
            )
        return items

    @staticmethod
    def _limited_dream_fragments(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        items: list[dict[str, Any]] = []
        for raw in value[-18:]:
            if isinstance(raw, dict):
                text = PrivateCompanionPageApi._single_line(
                    raw.get("text") or raw.get("keyword") or raw.get("label"),
                    42,
                )
                if not text:
                    continue
                items.append(
                    {
                        "text": text,
                        "weight": PrivateCompanionPageApi._float(raw.get("effective_weight") or raw.get("weight")),
                        "source": PrivateCompanionPageApi._single_line(raw.get("source"), 24),
                        "created_at": PrivateCompanionPageApi._single_line(raw.get("created_at") or raw.get("created_ts"), 24),
                    }
                )
            else:
                text = PrivateCompanionPageApi._single_line(raw, 42)
                if text:
                    items.append({"text": text, "weight": 1.0, "source": "", "created_at": ""})
        items.sort(key=lambda item: float(item.get("weight") or 0), reverse=True)
        return items[:14]

    @staticmethod
    def _limited_list(value: Any, limit: int) -> list[Any]:
        return list(value[:limit]) if isinstance(value, list) else []

    @staticmethod
    def _memory_item_count(memory: Any) -> int:
        if not isinstance(memory, dict):
            return 0
        count = 0
        for value in memory.values():
            if isinstance(value, list):
                count += len(value)
            elif value:
                count += 1
        return count

    def _livingmemory_db_path(self) -> Path | None:
        candidates: list[Path] = []
        data_dir = Path(str(getattr(self.plugin, "data_dir", "") or "")).resolve()
        if data_dir:
            candidates.append(data_dir.parent / "astrbot_plugin_livingmemory" / "livingmemory.db")
            candidates.append(data_dir.parent / "astrbot_plugin_livingmemory" / "livingmemory_graph_documents.db")
        candidates.append(Path.home() / ".astrbot" / "data" / "plugin_data" / "astrbot_plugin_livingmemory" / "livingmemory.db")
        for path in candidates:
            try:
                if path.exists() and path.is_file():
                    return path
            except OSError:
                continue
        return None

    def _worldbook_member_livingmemory_tokens(self, user_id: str, profile: dict[str, Any]) -> dict[str, list[str]]:
        primary_raw: list[Any] = [
            user_id,
            profile.get("linked_qq_user_id"),
            profile.get("merged_into_user_id"),
            profile.get("linked_bili_profile_id"),
            profile.get("name"),
        ]
        external_ids = profile.get("external_ids")
        if isinstance(external_ids, list):
            primary_raw.extend(external_ids)
        support_raw: list[Any] = []
        for key in ("aliases", "observed_names"):
            value = profile.get(key)
            if isinstance(value, list):
                support_raw.extend(value)

        def normalize(raw_items: list[Any], seen: set[str]) -> list[str]:
            tokens: list[str] = []
            for raw in raw_items:
                text = self._single_line(raw, 80)
                if not text or text in seen:
                    continue
                if text.isdigit():
                    if len(text) < 5:
                        continue
                elif len(text) < 2:
                    continue
                seen.add(text)
                tokens.append(text)
            return tokens

        def stable_support(raw_items: list[Any]) -> list[Any]:
            stable: list[Any] = []
            for raw in raw_items:
                text = self._single_line(raw, 40)
                if not text or len(text) > 16:
                    continue
                if any(mark in text for mark in ("，", ",", "。", "！", "？", " ", "：", ":", "|", "\n")):
                    continue
                stable.append(text)
            return stable

        seen_tokens: set[str] = set()
        primary_tokens = normalize(primary_raw, seen_tokens)
        support_tokens = normalize(stable_support(support_raw), seen_tokens)
        tokens = [*primary_tokens, *support_tokens]
        return {
            "tokens": tokens[:18],
            "primary_tokens": primary_tokens[:8],
            "support_tokens": support_tokens[:12],
        }

    @staticmethod
    def _sqlite_like_pattern(token: str) -> str:
        escaped = str(token).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"

    @staticmethod
    def _json_dict(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if not isinstance(value, str) or not value.strip():
            return {}
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _json_list(value: Any) -> list[Any]:
        if isinstance(value, list):
            return value
        if not isinstance(value, str) or not value.strip():
            return []
        try:
            parsed = json.loads(value)
        except Exception:
            return []
        return parsed if isinstance(parsed, list) else []

    @staticmethod
    def _coerce_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _livingmemory_match_info(self, content: str, metadata_text: str, token_bundle: dict[str, list[str]]) -> dict[str, Any]:
        haystack = f"{content}\n{metadata_text}".lower()
        score = 0.0
        primary_tokens = token_bundle.get("primary_tokens", [])
        support_tokens = token_bundle.get("support_tokens", [])
        matched_primary: list[str] = []
        matched_support: list[str] = []
        for token in primary_tokens:
            text = token.lower()
            if not text or text not in haystack:
                continue
            count = max(1, haystack.count(text))
            if token.isdigit():
                score += 9.0 + min(count, 4)
            else:
                score += min(8.0, 3.5 + len(token) * 0.65) + min(count - 1, 3) * 0.7
            matched_primary.append(token)
        for token in support_tokens:
            text = token.lower()
            if not text or text not in haystack:
                continue
            count = max(1, haystack.count(text))
            score += min(3.0, 0.8 + len(token) * 0.25) + min(count - 1, 2) * 0.25
            matched_support.append(token)
        accepted = bool(matched_primary) or (not primary_tokens and len(matched_support) >= 2)
        return {
            "accepted": accepted,
            "score": round(score, 3),
            "matched_tokens": [*matched_primary, *matched_support][:8],
            "primary_hits": len(matched_primary),
            "support_hits": len(matched_support),
        }

    def _livingmemory_item_from_document(self, row: sqlite3.Row, token_bundle: dict[str, list[str]]) -> dict[str, Any] | None:
        metadata = self._json_dict(row["metadata"])
        content = str(row["text"] or "").strip()
        match = self._livingmemory_match_info(content, str(row["metadata"] or ""), token_bundle)
        if not match.get("accepted"):
            return None
        create_time = self._coerce_float(metadata.get("create_time"))
        last_access = self._coerce_float(metadata.get("last_access_time"))
        topics = metadata.get("topics") if isinstance(metadata.get("topics"), list) else []
        key_facts = metadata.get("key_facts") if isinstance(metadata.get("key_facts"), list) else []
        return {
            "source": "documents",
            "source_label": "长期记忆文档",
            "id": row["doc_id"] or row["id"],
            "score": match.get("score"),
            "matched_tokens": match.get("matched_tokens", []),
            "primary_hits": match.get("primary_hits", 0),
            "support_hits": match.get("support_hits", 0),
            "session_id": self._single_line(metadata.get("session_id"), 80),
            "persona_id": self._single_line(metadata.get("persona_id"), 80),
            "importance": metadata.get("importance"),
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
            "create_time": create_time,
            "last_access_time": last_access,
            "topics": [self._single_line(item, 40) for item in topics[:8] if self._single_line(item, 40)],
            "key_facts": [self._single_line(item, 120) for item in key_facts[:8] if self._single_line(item, 120)],
            "preview": self._single_line(metadata.get("canonical_summary") or content, 260),
            "content": content[:1800],
        }

    def _livingmemory_item_from_atom(self, row: sqlite3.Row, token_bundle: dict[str, list[str]]) -> dict[str, Any] | None:
        content = str(row["content"] or "").strip()
        entities = self._json_list(row["entities"])
        metadata = self._json_dict(row["metadata"])
        metadata_text = " ".join([str(row["entities"] or ""), str(row["metadata"] or "")])
        match = self._livingmemory_match_info(content, metadata_text, token_bundle)
        if not match.get("accepted"):
            return None
        return {
            "source": "atoms",
            "source_label": "原子记忆",
            "id": row["id"],
            "parent_memory_id": row["parent_memory_id"],
            "score": match.get("score"),
            "matched_tokens": match.get("matched_tokens", []),
            "primary_hits": match.get("primary_hits", 0),
            "support_hits": match.get("support_hits", 0),
            "session_id": self._single_line(row["session_id"], 80),
            "persona_id": self._single_line(row["persona_id"], 80),
            "importance": row["importance"],
            "confidence": row["confidence"],
            "created_at": str(row["created_at"] or ""),
            "create_time": self._coerce_float(row["created_at"]),
            "last_access_time": self._coerce_float(row["last_accessed_at"]),
            "topics": [self._single_line(item, 40) for item in entities[:8] if self._single_line(item, 40)],
            "key_facts": [self._single_line(item, 120) for item in metadata.get("key_facts", [])[:6]] if isinstance(metadata.get("key_facts"), list) else [],
            "preview": self._single_line(content, 260),
            "content": content[:1200],
        }

    def _livingmemory_item_from_graph_entry(self, row: sqlite3.Row, token_bundle: dict[str, list[str]]) -> dict[str, Any] | None:
        metadata = self._json_dict(row["metadata"])
        content = str(row["content"] or "").strip()
        match = self._livingmemory_match_info(content, str(row["metadata"] or ""), token_bundle)
        if not match.get("accepted"):
            return None
        return {
            "source": "graph",
            "source_label": "关系图谱",
            "id": row["entry_key"] or row["id"],
            "parent_memory_id": row["source_memory_id"],
            "score": match.get("score"),
            "matched_tokens": match.get("matched_tokens", []),
            "primary_hits": match.get("primary_hits", 0),
            "support_hits": match.get("support_hits", 0),
            "session_id": self._single_line(row["session_id"] or metadata.get("session_id"), 80),
            "persona_id": self._single_line(row["persona_id"] or metadata.get("persona_id"), 80),
            "importance": metadata.get("importance"),
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
            "create_time": self._coerce_float(metadata.get("create_time")),
            "last_access_time": self._coerce_float(metadata.get("last_access_time")),
            "topics": [],
            "key_facts": [],
            "preview": self._single_line(content, 260),
            "content": content[:1600],
        }

    def _query_livingmemory_for_tokens(self, db_path: Path, token_bundle: dict[str, list[str]], limit: int) -> list[dict[str, Any]]:
        tokens = token_bundle.get("primary_tokens") or token_bundle.get("tokens", [])
        if not tokens:
            return []
        db_uri = f"file:{db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(db_uri, uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("PRAGMA busy_timeout=1500")
            like_tokens = [self._sqlite_like_pattern(token) for token in tokens]
            table_limit = max(80, limit * 8)
            items: list[dict[str, Any]] = []

            def where_for(columns: list[str]) -> tuple[str, list[str]]:
                parts: list[str] = []
                params: list[str] = []
                for pattern in like_tokens:
                    sub = []
                    for column in columns:
                        sub.append(f"{column} LIKE ? ESCAPE '\\'")
                        params.append(pattern)
                    parts.append("(" + " OR ".join(sub) + ")")
                return " OR ".join(parts), params

            if self._sqlite_table_exists(conn, "documents"):
                where, params = where_for(["text", "metadata"])
                for row in conn.execute(
                    f"SELECT id, doc_id, text, metadata, created_at, updated_at FROM documents WHERE {where} ORDER BY id DESC LIMIT ?",
                    [*params, table_limit],
                ).fetchall():
                    item = self._livingmemory_item_from_document(row, token_bundle)
                    if item:
                        items.append(item)

            if self._sqlite_table_exists(conn, "memory_atoms"):
                where, params = where_for(["content", "entities", "metadata"])
                for row in conn.execute(
                    f"""SELECT id, parent_memory_id, atom_type, content, entities, importance, confidence,
                              created_at, last_accessed_at, session_id, persona_id, metadata
                         FROM memory_atoms
                        WHERE (status IS NULL OR status != 'expired') AND ({where})
                        ORDER BY id DESC LIMIT ?""",
                    [*params, table_limit],
                ).fetchall():
                    item = self._livingmemory_item_from_atom(row, token_bundle)
                    if item:
                        items.append(item)

            if self._sqlite_table_exists(conn, "graph_entries"):
                where, params = where_for(["content", "metadata", "session_id"])
                for row in conn.execute(
                    f"""SELECT id, entry_key, source_memory_id, session_id, persona_id, entry_type,
                              relation_type, content, metadata, created_at, updated_at
                         FROM graph_entries
                        WHERE {where}
                        ORDER BY id DESC LIMIT ?""",
                    [*params, table_limit],
                ).fetchall():
                    item = self._livingmemory_item_from_graph_entry(row, token_bundle)
                    if item:
                        items.append(item)

            seen: set[tuple[str, str]] = set()
            unique: list[dict[str, Any]] = []
            for item in sorted(items, key=lambda entry: (entry.get("score") or 0, entry.get("last_access_time") or entry.get("create_time") or 0), reverse=True):
                key = (str(item.get("source") or ""), str(item.get("id") or ""))
                if key in seen:
                    continue
                seen.add(key)
                unique.append(item)
                if len(unique) >= limit:
                    break
            return unique
        finally:
            conn.close()

    @staticmethod
    def _sqlite_table_exists(conn: sqlite3.Connection, table: str) -> bool:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)).fetchone()
        return row is not None

    @staticmethod
    def _query_int(name: str, default: int, minimum: int, maximum: int) -> int:
        raw = request.args.get(name, default)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    @staticmethod
    def _clamp_int(raw: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    @staticmethod
    def _normalize_id_list(value: Any) -> list[str]:
        if isinstance(value, str):
            raw_items = value.replace("，", ",").replace("\n", ",").split(",")
        elif isinstance(value, list):
            raw_items = value
        else:
            raw_items = []
        result: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            text = PrivateCompanionPageApi._single_line(item, 64)
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    @staticmethod
    def _single_line(value: Any, limit: int) -> str:
        text = " ".join(str(value or "").strip().split())
        return text[:limit]

    @classmethod
    def _sanitize_news_text(cls, value: Any, limit: int, *, fallback: str = "") -> str:
        text = cls._single_line(value, limit)
        if text and _text_looks_garbled(text):
            return fallback
        return text

    @staticmethod
    def _multi_line(value: Any, limit: int) -> str:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text[:limit].strip()

    @staticmethod
    def _ok(data: Any = None) -> dict[str, Any]:
        return {"success": True, "data": data, "ts": int(time.time())}

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        return {"success": False, "error": str(message), "ts": int(time.time())}
