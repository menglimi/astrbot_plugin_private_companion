# -*- coding: utf-8 -*-
"""外部内容与社交面板域。

由 tools/split_mixin_domain.py 从 page_api_summary_panel.py 机械抽取（7 个方法 + 0 个模块级名字 + 0 个类级赋值 / 497 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiSummaryPanelMixin）。
"""
from __future__ import annotations

from .helpers import _safe_int
from typing import Any



class PrivateCompanionPageApiSummaryPanelContentMixin:
    """外部内容与社交面板域（从 PrivateCompanionPageApiSummaryPanelMixin 拆出）。"""


    def _group_summary(self, group_id: str, group: dict[str, Any]) -> dict[str, Any]:
        atmosphere = group.get("atmosphere") if isinstance(group.get("atmosphere"), dict) else {}
        slang_terms = group.get("slang_terms") if isinstance(group.get("slang_terms"), list) else []
        slang_meanings = group.get("slang_meanings") if isinstance(group.get("slang_meanings"), dict) else {}
        members = group.get("members") if isinstance(group.get("members"), dict) else {}
        group_for_filter = group
        group_id_text = str(group_id)
        manual_group_name = self._single_line(group.get("manual_group_name"), 80)
        group_name = self._single_line(
            manual_group_name or group.get("name") or group.get("group_name") or group.get("display_name"),
            80,
        )
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
        promoter = getattr(self.plugin, "_group_slang_term_is_promoted", None)
        if callable(promoter):
            visible_terms: list[Any] = []
            for item in slang_terms:
                try:
                    if not promoter(group_for_filter, item):
                        continue
                except Exception:
                    continue
                if isinstance(item, dict):
                    visible_terms.append({**item, "promoted": True})
                else:
                    visible_terms.append(item)
            slang_terms = visible_terms
        identity_count = sum(1 for item in members.values() if isinstance(item, dict) and item.get("identity_known"))
        safety_getter = getattr(self.plugin, "_group_member_safety_compact_summary", None)
        member_safety = safety_getter(group) if callable(safety_getter) else {}
        wakeup_logs = group.get("group_wakeup_logs") if isinstance(group.get("group_wakeup_logs"), list) else []
        last_wakeup = group.get("last_group_wakeup") if isinstance(group.get("last_group_wakeup"), dict) else {}
        last_interjection = self._sanitize_last_bot_interjection(group.get("last_bot_interjection"))
        return {
            "group_id": group_id_text,
            "name": group_name,
            "group_name": group_name,
            "display_name": group_name or "未命名群聊",
            "manual_group_name": manual_group_name,
            "group_name_source": "manual" if manual_group_name else str(group.get("group_name_source") or "auto"),
            "global_enabled": bool(getattr(self.plugin, "enable_group_companion", False)),
            "enabled": bool(group.get("enabled", True)),
            "allowed_by_mode": self.plugin._group_allowed_by_access_mode(group_id_text),
            "message_count": group.get("message_count", 0),
            "last_seen_ts": group.get("last_seen", 0),
            "last_seen": self.plugin._format_timestamp_elapsed(group.get("last_seen", 0)),
            "member_count": len(members),
            "recognized_member_count": identity_count,
            "member_safety_blocked_count": _safe_int(member_safety.get("blocked_count"), 0),
            "member_safety_watching_count": _safe_int(member_safety.get("watching_count"), 0),
            "recent_message_count": len(group.get("recent_messages") or []),
            "recent_bot_reply_count": len(group.get("recent_bot_replies") or []),
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
                "pace": atmosphere.get("pace", ""),
                "heat": atmosphere.get("heat") or atmosphere.get("pace", ""),
                "last_summary": atmosphere.get("summary") or atmosphere.get("last_summary", ""),
                "recent_count": atmosphere.get("recent_count", 0),
                "active_speakers": atmosphere.get("active_speakers", 0),
                "updated_at": atmosphere.get("updated_at", ""),
            },
        }

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
        history = [
            item
            for item in self._browsing_history_entries(data)
            if item.get("source") == "news"
        ]
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
            "history_count": len(history),
            "history": history,
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
        history = [
            item
            for item in self._browsing_history_entries(data)
            if item.get("source") != "news"
        ]
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

    def _qzone_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        state = data.get("qzone_integration") if isinstance(data.get("qzone_integration"), dict) else {}
        daily_plan = state.get("life_publish_daily_plan") if isinstance(state.get("life_publish_daily_plan"), dict) else {}
        plan_items = daily_plan.get("items") if isinstance(daily_plan.get("items"), list) else []
        pending_times = [
            self._float(item.get("planned_at"))
            for item in plan_items
            if isinstance(item, dict) and item.get("status") == "planned" and self._float(item.get("planned_at")) > 0
        ]
        plan_status_counts: dict[str, int] = {}
        for item in plan_items:
            if not isinstance(item, dict):
                continue
            status = self._single_line(item.get("status"), 24) or "planned"
            plan_status_counts[status] = plan_status_counts.get(status, 0) + 1
        service_available = bool(
            callable(getattr(self.plugin, "_qzone_get_cookies", None))
            and callable(getattr(self.plugin, "_qzone_query_feeds", None))
        )
        platform_checker = getattr(self.plugin, "_qzone_platform_supported", None)
        platform_supported = bool(platform_checker(None)) if callable(platform_checker) else True
        available = bool(service_available and platform_supported)
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
            "service_available": service_available,
            "platform_supported": platform_supported,
            "unavailable_reason": "" if platform_supported else "QQ 官方机器人不支持 QQ 空间；仅 OneBot/aiocqhttp 可用。",
            "last_life_publish_at": self.plugin._format_timestamp_elapsed(state.get("last_life_publish_at", 0)),
            "last_status": state.get("last_life_publish_status", ""),
            "last_text": state.get("last_life_publish_text", ""),
            "life_publish_plan_date": self._single_line(daily_plan.get("date"), 24),
            "life_publish_plan_target_count": self._int(daily_plan.get("target_count")),
            "life_publish_plan_published_count": self._int(daily_plan.get("published_count")),
            "life_publish_plan_skip_reason": self._single_line(daily_plan.get("skip_reason"), 80),
            "life_publish_plan_status_counts": plan_status_counts,
            "life_publish_plan_next_at": self.plugin._format_timestamp_elapsed(min(pending_times)) if pending_times else "",
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
            "cookie_fetch_status": state.get("last_cookie_fetch_status", ""),
            "cookie_fetch_reason": state.get("last_cookie_fetch_reason", ""),
            "cookie_fetch_has_uin": bool(state.get("last_cookie_fetch_has_uin", False)),
            "cookie_fetch_has_skey": bool(state.get("last_cookie_fetch_has_skey", False)),
            "cookie_fetch_has_p_skey": bool(state.get("last_cookie_fetch_has_p_skey", False)),
            "cookie_fetch_uin": state.get("last_cookie_fetch_uin", ""),
            "cookie_fetch_at": self.plugin._format_timestamp_elapsed(state.get("last_cookie_fetch_at", 0)),
        }

    def _creative_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        projects = data.get("creative_projects") if isinstance(data.get("creative_projects"), list) else []
        items = [item for item in projects if isinstance(item, dict)]
        active = [item for item in items if item.get("status") == "drafting"]
        latest = items[-1] if items else {}
        return {
            "enabled": bool(getattr(self.plugin, "enable_creative_writing", False)),
            "hidden_mode": bool(getattr(self.plugin, "creative_hidden_mode", False)),
            "cover_generation_enabled": bool(getattr(self.plugin, "enable_creative_cover_generation", False)),
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
                    "cover_src": self._creative_project_cover_url(item),
                    "cover_status": self._single_line(item.get("cover_generation_status"), 24),
                    "cover_error": self._single_line(item.get("cover_generation_error"), 180),
                }
                for item in items[-6:]
            ],
        }
