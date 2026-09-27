# -*- coding: utf-8 -*-
"""NewsExplorationNewsReadingAiDailyMixin。

由 tools/split_mixin_domain.py 从 news_exploration.py 机械抽取（8 个方法 + 0 个模块级名字 + 0 个类级赋值 / 612 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 NewsExplorationMixin）。
"""
from __future__ import annotations

from .news_exploration_shared import logger
from .news_exploration_shared import Any
from .news_exploration_shared import _now_ts
from .news_exploration_shared import _safe_float
from .news_exploration_shared import _safe_int
from .news_exploration_shared import _single_line
from .news_exploration_shared import _today_key
from .news_exploration_shared import datetime
from .news_exploration_shared import random
from .news_exploration_shared import re
from .news_exploration_shared import runtime_persona_setting



class NewsExplorationNewsReadingAiDailyMixin:
    """NewsExplorationNewsReadingAiDailyMixin（从 NewsExplorationMixin 拆出）。"""


    async def _perform_news_reading(self, *, reason: str = "boredom", allow_share: bool = True, force: bool = False) -> None:
        if not runtime_persona_setting(self, "enable_news_integration", False):
            return
        state = self.data.setdefault("news_integration", {})
        if not isinstance(state, dict):
            self.data["news_integration"] = {}
            state = self.data["news_integration"]
        now = _now_ts()
        if not force and now - _safe_float(state.get("last_probe_at"), 0) < 45 * 60:
            return
        state["last_probe_at"] = now
        items = await self._fetch_news_reading_candidates()
        if not items:
            state["last_status"] = "no_items"
            self._save_data_sync(sections={"news_integration"})
            return
        read_keys = state.setdefault("read_keys", [])
        if not isinstance(read_keys, list):
            read_keys = []
            state["read_keys"] = read_keys
        fresh = [item for item in items if str(item.get("key") or "") not in set(str(key) for key in read_keys)]
        if not fresh:
            fresh = items[:8]
        digest = await self._summarize_news_items(fresh)
        if not digest:
            state["last_status"] = "digest_failed"
            self._save_data_sync(sections={"news_integration"})
            return
        selected_key = _single_line(digest.get("selected_key"), 32)
        if selected_key and selected_key not in read_keys:
            read_keys.append(selected_key)
            del read_keys[:-80]
        state["last_read_at"] = now
        if reason == "daily":
            state["last_daily_read_day"] = _today_key()
        state["last_status"] = "read"
        state["last_reason"] = reason
        state["last_digest"] = digest
        digests = state.setdefault("digests", [])
        if not isinstance(digests, list):
            digests = []
            state["digests"] = digests
        wish = await self._build_external_event_wish(digest, source_type="news")
        if wish:
            digest["self_link"] = wish
        share_attempts: list[dict[str, Any]] = []
        digest["share_attempts"] = share_attempts
        digest["share_status"] = "not_attempted"

        def _note_news_share(user_id_value: Any, status: str, reason_text: str, **extra: Any) -> None:
            item = {
                "user_id": str(user_id_value or ""),
                "status": _single_line(status, 32),
                "reason": _single_line(reason_text, 120),
                "ts": _now_ts(),
            }
            for key, value in extra.items():
                if isinstance(value, (int, float, bool)):
                    item[key] = value
                else:
                    item[key] = _single_line(value, 160)
            share_attempts.append(item)

        history_digest = {
            key: value
            for key, value in digest.items()
            if key not in {"items", "results", "raw_items", "articles"}
        }
        digests.append({**history_digest, "reason": reason})
        del digests[:-32]

        def _sync_digest_share_status() -> None:
            if not digests or not isinstance(digests[-1], dict):
                return
            if selected_key and _single_line(digests[-1].get("selected_key"), 32) != selected_key:
                return
            digests[-1].update({
                "share_status": digest.get("share_status", ""),
                "share_skip_reason": digest.get("share_skip_reason", ""),
                "share_attempts": share_attempts,
            })

        state["latest_items"] = items[:12]
        self_link_allows_share = bool(wish.get("should_share")) if isinstance(wish, dict) else False
        if not allow_share and not self_link_allows_share:
            digest["share_status"] = "blocked"
            digest["share_skip_reason"] = "本次新闻阅读不允许主动分享，且自我关联未通过"
            _sync_digest_share_status()
            self._save_data_sync(sections={"news_integration"})
            logger.info("已完成一次新闻阅读: %s", reason)
            return
        users = self.data.get("users")
        accepted_any = False
        if isinstance(users, dict):
            for user_id, user in users.items():
                if not isinstance(user, dict) or not self._is_target_private_user(str(user_id), user) or not user.get("enabled", True) or not user.get("umo"):
                    continue
                selection = self._select_external_event_for_user(user, fresh[:12] or items[:12], source_type="news", base_payload=digest, now=now)
                user_digest = selection.get("payload") if isinstance(selection, dict) else None
                if not isinstance(user_digest, dict):
                    user_digest = dict(digest)
                user_preference = selection.get("preference") if isinstance(selection.get("preference"), dict) else {}
                user_selected_key = _single_line(user_digest.get("selected_key"), 32) or selected_key
                user_wish = wish
                if user_selected_key and user_selected_key != selected_key:
                    user_wish = self._external_event_fallback_wish(user_digest, source_type="news")
                    if isinstance(wish, dict) and wish:
                        user_wish = {
                            **user_wish,
                            "should_share": bool(wish.get("should_share")) or bool(user_wish.get("should_share")),
                            "relevance": max(_safe_int(wish.get("relevance"), 0, 0, 10), _safe_int(user_wish.get("relevance"), 0, 0, 10)),
                            "desire": max(_safe_int(wish.get("desire"), 0, 0, 10), _safe_int(user_wish.get("desire"), 0, 0, 10)),
                            "share_probability": max(_safe_float(wish.get("share_probability"), 0.0), _safe_float(user_wish.get("share_probability"), 0.0)),
                        }
                    user_digest["self_link"] = user_wish
                strong_self_link = (
                    isinstance(user_wish, dict)
                    and bool(user_wish)
                    and (
                        _safe_int(user_wish.get("relevance"), 0, 0, 10) >= 8
                        or _safe_int(user_wish.get("desire"), 0, 0, 10) >= 8
                        or _safe_float(user_wish.get("share_probability"), 0.0) >= 0.8
                        or bool(user_wish.get("boost_reason"))
                        or _safe_int(user_preference.get("score"), 0) >= 10
                    )
                )
                idle_required = max(
                    runtime_persona_setting(self, "idle_minutes", 60),
                    _safe_int(
                        runtime_persona_setting(self, "external_event_idle_minutes", 90),
                        90,
                        5,
                        1440,
                    ),
                ) * 60
                if strong_self_link:
                    idle_required = min(
                        idle_required,
                        max(
                            _safe_int(
                                runtime_persona_setting(
                                    self, "external_event_idle_strong_minutes", 20
                                ),
                                20,
                                1,
                                1440,
                            ),
                            runtime_persona_setting(self, "idle_minutes", 60),
                        )
                        * 60,
                    )
                idle_elapsed = now - _safe_float(user.get("last_seen"), 0)
                if idle_elapsed < idle_required:
                    _note_news_share(user_id, "skipped", "用户近期仍活跃，暂不主动打扰", idle_elapsed_seconds=round(idle_elapsed, 1), idle_required_seconds=round(idle_required, 1))
                    continue
                if str(user.get("last_news_share_key") or "") == user_selected_key:
                    _note_news_share(user_id, "skipped", "这条新闻已经给该用户排过主动")
                    continue
                if now - _safe_float(user.get("last_news_share_at"), 0) < _safe_int(
                    runtime_persona_setting(self, "news_share_cooldown_hours", 8),
                    8,
                    0,
                    168,
                ) * 3600:
                    _note_news_share(user_id, "skipped", "新闻分享 8 小时冷却中")
                    continue
                shared_link_cooldown = self._external_link_share_cooldown_remaining(user, now=now)
                if shared_link_cooldown > 0:
                    _note_news_share(
                        user_id,
                        "skipped",
                        "主动外链统一冷却中",
                        cooldown_remaining_hours=round(shared_link_cooldown / 3600.0, 1),
                    )
                    continue
                if isinstance(user_wish, dict) and user_wish:
                    if (
                        now - _safe_float(user.get("last_external_event_self_link_at"), 0)
                        < runtime_persona_setting(self, "external_event_self_link_cooldown_hours", 12) * 3600
                    ):
                        _note_news_share(user_id, "skipped", "外界信息自我关联冷却中")
                        continue
                    if not user_wish.get("should_share") and _safe_int(user_preference.get("score"), 0) < 8:
                        _note_news_share(user_id, "skipped", "自我关联与用户偏好都不足以主动分享", relevance=_safe_int(user_wish.get("relevance"), 0), desire=_safe_int(user_wish.get("desire"), 0), user_preference=_safe_int(user_preference.get("score"), 0))
                        continue
                    decision = self._external_event_share_decision(
                        user,
                        user_digest,
                        source_type="news",
                        wish=user_wish,
                        base_probability=runtime_persona_setting(self, "news_share_probability", 0.22),
                        now=now,
                    )
                    if decision.get("duplicate"):
                        _note_news_share(user_id, "skipped", "近期同类外界信息已经处理过")
                        continue
                    if not decision.get("should_share"):
                        _note_news_share(
                            user_id,
                            "skipped",
                            "统一评分认为这条新闻不值得现在主动分享",
                            score=_safe_int(decision.get("score"), 0, 0, 100),
                            user_match=_safe_int(decision.get("user_match"), 0, 0, 10),
                            user_preference=_safe_int(user_preference.get("score"), 0),
                        )
                        continue
                    base_probability = runtime_persona_setting(self, "news_share_probability", 0.22)
                    share_probability = max(
                        base_probability,
                        _safe_float(decision.get("probability"), base_probability),
                    )
                    share_probability *= runtime_persona_setting(
                        self, "external_event_self_link_probability", 0.62
                    )
                    if strong_self_link:
                        share_probability = max(share_probability, min(0.95, _safe_float(user_wish.get("share_probability"), share_probability)))
                    if _safe_int(user_preference.get("score"), 0) >= 8:
                        share_probability = min(0.95, share_probability + 0.10)
                else:
                    decision = self._external_event_share_decision(
                        user,
                        user_digest,
                        source_type="news",
                        wish={"relevance": 4, "desire": 4, "should_share": True},
                        base_probability=runtime_persona_setting(self, "news_share_probability", 0.22),
                        now=now,
                    )
                    if decision.get("duplicate") or not decision.get("should_share"):
                        _note_news_share(user_id, "skipped", "统一评分认为这条新闻不值得现在主动分享")
                        continue
                    share_probability = runtime_persona_setting(self, "news_share_probability", 0.22)
                if random.random() > max(0.0, min(1.0, share_probability)):
                    _note_news_share(user_id, "skipped", "分享概率未命中", probability=round(max(0.0, min(1.0, share_probability)), 3))
                    continue
                self_link_motive = _single_line(user_wish.get("motive") if isinstance(user_wish, dict) else "", 180)
                self_link_tone = _single_line(user_wish.get("tone") if isinstance(user_wish, dict) else "", 60)
                self_link_boundary = _single_line(user_wish.get("boundary") if isinstance(user_wish, dict) else "", 140)
                accepted = self._offer_proactive_candidate(
                    str(user_id),
                    user,
                    {
                        "source": "news",
                        "reason": "news_share",
                        "action": "message",
                        "scheduled_ts": now + random.randint(10, 55) * 60,
                        "topic": _single_line(user_digest.get("topic"), 48) or "新闻",
                        "score": max(4 if self_link_motive else 3, _safe_int(decision.get("score"), 0, 0, 100)),
                        "motive": self_link_motive
                        or "这条新闻和对方兴趣相关",
                        "context_key": "news_context",
                        "context": {
                            **user_digest,
                            "share_tone": self_link_tone,
                            "share_boundary": self_link_boundary,
                            "share_decision": decision,
                            "preference_match": user_preference,
                        },
                    },
                )
                if accepted:
                    accepted_any = True
                    _note_news_share(user_id, "accepted", "已按用户偏好进入主动候选", probability=round(max(0.0, min(1.0, share_probability)), 3), user_preference=_safe_int(user_preference.get("score"), 0), selected_key=user_selected_key)
                    user["news_context"] = {
                        **user_digest,
                        "share_tone": self_link_tone,
                        "share_boundary": self_link_boundary,
                        "share_decision": decision,
                        "preference_match": user_preference,
                    }
                    user["last_news_share_key"] = user_selected_key
                    user["last_news_share_at"] = now
                    self._note_external_link_candidate(user, now=now)
                    if isinstance(user_wish, dict) and user_wish:
                        user["last_external_event_self_link_at"] = now
                    self._remember_external_event(user_digest, source_type="news", reason="news_share")
                else:
                    _note_news_share(user_id, "blocked", "主动候选被计划队列拒绝，可能已有更早主动或主题重复")
        if accepted_any:
            digest["share_status"] = "accepted"
        elif share_attempts:
            digest["share_status"] = "skipped"
            digest["share_skip_reason"] = share_attempts[-1].get("reason", "")
        else:
            digest["share_status"] = "no_target"
            digest["share_skip_reason"] = "没有可用的目标私聊用户"
        _sync_digest_share_status()
        self._save_data_sync(sections={"news_integration", "users", "proactive_candidate_pool", "external_event_pool", "external_event_self_link_cache"})
        logger.info("已完成一次新闻阅读: %s", reason)

    def _ai_daily_state(self) -> dict[str, Any]:
        state = self.data.setdefault("news_integration", {})
        if not isinstance(state, dict):
            self.data["news_integration"] = {}
            state = self.data["news_integration"]
        ai_state = state.setdefault("ai_daily", {})
        if not isinstance(ai_state, dict):
            ai_state = {}
            state["ai_daily"] = ai_state
        return ai_state

    @staticmethod
    def _news_item_is_today(item: dict[str, Any], today: str | None = None) -> bool:
        today = today or _today_key()
        published_ts = _safe_float(item.get("published_ts"), 0)
        if published_ts > 0:
            try:
                return datetime.fromtimestamp(published_ts).strftime("%Y-%m-%d") == today
            except Exception:
                pass
        text = f"{item.get('title') or ''} {item.get('published') or ''} {item.get('summary') or ''}"
        now_dt = datetime.now()
        today_cn = now_dt.strftime("%Y年%m月%d日").replace("年0", "年").replace("月0", "月")
        today_dash = now_dt.strftime("%Y-%m-%d")
        today_slash = now_dt.strftime("%Y/%m/%d")
        today_md = f"{now_dt.month}月{now_dt.day}日"
        today_compact = now_dt.strftime("%m%d")
        today_short_compact = f"{now_dt.month}{now_dt.day:02d}"
        return any(token in text for token in (today_cn, today_dash, today_slash, today_md, today_compact, today_short_compact))

    def _ai_daily_candidate_snapshot(self, items: list[dict[str, Any]], today: str) -> list[dict[str, Any]]:
        snapshot: list[dict[str, Any]] = []
        for item in items[:10]:
            if not isinstance(item, dict):
                continue
            published_ts = _safe_float(item.get("published_ts"), 0)
            published_date = ""
            if published_ts > 0:
                try:
                    published_date = datetime.fromtimestamp(published_ts).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    published_date = ""
            snapshot.append(
                {
                    "title": _single_line(item.get("title"), 140),
                    "published": published_date or _single_line(item.get("published"), 80),
                    "link": _single_line(item.get("video_link") or item.get("link"), 420),
                    "media_type": _single_line(item.get("media_type"), 40),
                    "is_today": self._news_item_is_today(item, today),
                }
            )
        return snapshot

    async def _read_ai_daily_source(
        self,
        source: dict[str, Any],
        *,
        ai_state: dict[str, Any],
        source_state: dict[str, Any],
        today: str,
        now: float,
    ) -> bool:
        source_key = str(source.get("key") or self._ai_daily_source_key(source))
        source_name = _single_line(source.get("name"), 40) or "AI日报"
        source_author = _single_line(source.get("author_name"), 60)
        items = await self._fetch_bilibili_news_search_fallback(source)
        today_items = [
            item for item in items
            if isinstance(item, dict) and self._ai_daily_item_matches_source(item, source, today)
        ]
        ai_state["last_candidates"] = self._ai_daily_candidate_snapshot(items, today)
        source_state.update(
            {
                "date": today,
                "last_attempt_date": today,
                "last_checked_at": now,
                "last_candidate_count": len(items),
                "last_candidates": self._ai_daily_candidate_snapshot(items, today),
            }
        )
        if not today_items:
            source_state["status"] = "waiting_today_video"
            ai_state.update(
                {
                    "date": today,
                    "status": "waiting_today_video",
                    "last_checked_at": now,
                    "last_candidate_count": len(items),
                    "last_source_name": source_name,
                    "last_source_author": source_author,
                    "last_source_mid": _single_line(source.get("mid"), 40),
                    "last_source_key": source_key,
                    "last_source_schedule": _single_line(source.get("schedule"), 10),
                }
            )
            return False
        today_items.sort(key=lambda item: (_safe_float(item.get("published_ts"), 0), _safe_float(item.get("fetched_ts"), 0)), reverse=True)
        item = today_items[0]
        bvid = _single_line(item.get("video_link"), 120)
        bvid_match = re.search(r"(BV[0-9A-Za-z]+)", bvid)
        bvid_key = bvid_match.group(1) if bvid_match else _single_line(item.get("key"), 32)
        if bvid_key and str(source_state.get("last_video_bvid") or "") == bvid_key:
            source_state["status"] = "already_read_today_video"
            source_state["last_success_date"] = today
            ai_state.update(
                {
                    "date": today,
                    "status": "already_read_today_video",
                    "last_success_date": today,
                    "last_checked_at": now,
                    "last_source_name": source_name,
                    "last_source_author": source_author,
                    "last_source_mid": _single_line(source.get("mid"), 40),
                    "last_source_key": source_key,
                    "last_source_schedule": _single_line(source.get("schedule"), 10),
                }
            )
            return True
        if runtime_persona_setting(self, "ai_daily_prefer_text_version", True) and not item.get(
            "article_readable"
        ):
            source_state.update(
                {
                    "status": "today_video_without_text",
                    "last_video_bvid": bvid_key,
                    "last_video_title": _single_line(item.get("title"), 120),
                }
            )
            # 仍然继续用简介整理，避免文字版偶发缺失时今天完全没读到。
        read_basis = "完整文字版正文" if item.get("article_readable") and item.get("article_text") else (
            "视频字幕" if item.get("video_subtitle_readable") and item.get("video_subtitle_text") else (
                "视频公开信息" if item.get("video_context_text") else "视频标题/简介"
            )
        )
        digest = await self._summarize_news_items([item])
        if not digest:
            source_state["status"] = "digest_failed"
            ai_state.update(
                {
                    "date": today,
                    "status": "digest_failed",
                    "last_checked_at": now,
                    "last_source_name": source_name,
                    "last_source_author": source_author,
                    "last_source_mid": _single_line(source.get("mid"), 40),
                    "last_source_key": source_key,
                    "last_source_schedule": _single_line(source.get("schedule"), 10),
                }
            )
            return False
        wish = await self._build_external_event_wish(digest, source_type="news")
        if wish:
            digest["self_link"] = wish
        state = self.data.setdefault("news_integration", {})
        if not isinstance(state, dict):
            self.data["news_integration"] = {}
            state = self.data["news_integration"]
        state["last_read_at"] = now
        state["last_status"] = "read"
        state["last_reason"] = "ai_daily"
        state["last_digest"] = digest
        state["latest_items"] = [item]
        digests = state.setdefault("digests", [])
        if not isinstance(digests, list):
            digests = []
            state["digests"] = digests
        history_digest = {
            key: value
            for key, value in digest.items()
            if key not in {"items", "results", "raw_items", "articles"}
        }
        digests.append({**history_digest, "reason": "ai_daily"})
        del digests[:-32]
        read_keys = state.setdefault("read_keys", [])
        if isinstance(read_keys, list):
            selected_key = _single_line(digest.get("selected_key"), 32)
            if selected_key and selected_key not in read_keys:
                read_keys.append(selected_key)
                del read_keys[:-80]
        result_state = {
            "status": "read",
            "last_success_date": today,
            "last_checked_at": now,
            "last_source_name": source_name,
            "last_source_author": source_author,
            "last_source_mid": _single_line(source.get("mid"), 40),
            "last_source_key": source_key,
            "last_source_schedule": _single_line(source.get("schedule"), 10),
            "last_video_bvid": bvid_key,
            "last_video_title": _single_line(item.get("title"), 120),
            "last_video_pub_ts": _safe_float(item.get("published_ts"), 0),
            "last_video_link": _single_line(item.get("video_link") or item.get("link"), 400),
            "last_video_owner_name": _single_line(item.get("video_owner_name"), 80),
            "last_video_owner_mid": _single_line(item.get("video_owner_mid"), 40),
            "last_video_tname": _single_line(item.get("video_tname"), 60),
            "last_video_duration": _safe_int(item.get("video_duration"), 0, 0),
            "last_video_context_chars": len(str(item.get("video_context_text") or "")),
            "last_video_tags": list(item.get("video_tags") or [])[:10] if isinstance(item.get("video_tags"), list) else [],
            "last_video_hot_comments": list(item.get("video_hot_comments") or [])[:5] if isinstance(item.get("video_hot_comments"), list) else [],
            "last_text_link": _single_line(item.get("article_link"), 400),
            "last_text_readable": bool(item.get("article_readable") and item.get("article_text")),
            "last_text_chars": len(str(item.get("article_text") or "")),
            "last_text_excerpt_chars": len(str(item.get("article_excerpt") or "")),
            "last_video_subtitle_readable": bool(item.get("video_subtitle_readable") and item.get("video_subtitle_text")),
            "last_video_subtitle_chars": len(str(item.get("video_subtitle_text") or "")),
            "last_video_subtitle_status": _single_line(item.get("video_subtitle_status"), 40),
            "last_read_basis": read_basis,
            "last_digest": digest,
        }
        source_state.update(result_state)
        ai_state.update({"date": today, **result_state})
        logger.info(
            "已读取今日 %s: %s text_readable=%s text_chars=%s basis=%s",
            source_name,
            _single_line(item.get("title"), 120),
            bool(item.get("article_readable") and item.get("article_text")),
            len(str(item.get("article_text") or "")),
            read_basis,
        )
        return True

    async def _maybe_track_ai_daily(self, *, force: bool = False) -> None:
        if not (
            runtime_persona_setting(self, "enable_news_integration", False)
            and runtime_persona_setting(self, "enable_ai_daily_watch", True)
        ):
            return
        ai_state = self._ai_daily_state()
        today = _today_key()
        now = _now_ts()
        now_dt = datetime.now()
        source_states = ai_state.setdefault("source_states", {})
        if not isinstance(source_states, dict):
            source_states = {}
            ai_state["source_states"] = source_states
        configured_sources = self._ai_daily_source_items()
        ai_state["sources"] = [
            {
                "key": _single_line(source.get("key") or self._ai_daily_source_key(source), 80),
                "name": _single_line(source.get("name"), 40),
                "author_name": _single_line(source.get("author_name"), 60),
                "mid": _single_line(source.get("mid"), 40),
                "schedule": _single_line(source.get("schedule"), 10),
                "keywords": [_single_line(token, 20) for token in source.get("keywords", [])[:6]],
            }
            for source in configured_sources
        ]
        due_sources = self._ai_daily_due_sources(now_dt, today, source_states, force=force)
        if not due_sources:
            now_minute = now_dt.hour * 60 + now_dt.minute
            future_sources = [
                source for source in configured_sources
                if _safe_int(source.get("schedule_minutes"), 0, 0) > now_minute
                and not (
                    isinstance(source_states.get(str(source.get("key") or "")), dict)
                    and source_states[str(source.get("key") or "")].get("last_success_date") == today
                )
            ]
            status = "waiting_schedule" if future_sources else "all_sources_done"
            if ai_state.get("date") != today or ai_state.get("status") != status:
                ai_state.update({"date": today, "status": status, "last_checked_at": ai_state.get("last_checked_at", 0)})
                self._save_data_sync(sections={"news_integration"})
            return
        ai_state.update({"date": today, "last_checked_at": now, "status": "checking"})
        self._save_data_sync(sections={"news_integration"})
        any_read = False
        for source in due_sources:
            key = str(source.get("key") or self._ai_daily_source_key(source))
            source_state = source_states.setdefault(key, {})
            if not isinstance(source_state, dict):
                source_state = {}
                source_states[key] = source_state
            source_state.update(
                {
                    "name": _single_line(source.get("name"), 40),
                    "author_name": _single_line(source.get("author_name"), 60),
                    "mid": _single_line(source.get("mid"), 40),
                    "schedule": _single_line(source.get("schedule"), 10),
                    "status": "checking",
                }
            )
            read = await self._read_ai_daily_source(
                source,
                ai_state=ai_state,
                source_state=source_state,
                today=today,
                now=now,
            )
            any_read = any_read or read
        if not any_read and ai_state.get("status") == "checking":
            ai_state["status"] = "waiting_today_video"
        self._save_data_sync(sections={"news_integration"})

    async def _maybe_trigger_news_boredom_read(self) -> None:
        if not (
            runtime_persona_setting(self, "enable_news_integration", False)
            and runtime_persona_setting(self, "enable_news_boredom_read", True)
        ):
            return
        if not self._bot_currently_bored_enough_for_news():
            return
        state = self.data.setdefault("news_integration", {})
        if not isinstance(state, dict):
            self.data["news_integration"] = {}
            state = self.data["news_integration"]
        now = _now_ts()
        min_interval = max(
            1,
            runtime_persona_setting(self, "news_min_interval_hours", 6),
        ) * 3600
        if now - _safe_float(state.get("last_read_at"), 0) < min_interval:
            return
        if random.random() > 0.42:
            return
        await self._perform_news_reading(reason="boredom", allow_share=True, force=False)

    async def _ensure_daily_news_reading(self, *, force: bool = False) -> None:
        if not (
            runtime_persona_setting(self, "enable_news_integration", False)
            and runtime_persona_setting(self, "enable_news_daily_hot_read", True)
        ):
            return
        state = self.data.setdefault("news_integration", {})
        if not isinstance(state, dict):
            self.data["news_integration"] = {}
            state = self.data["news_integration"]
        today = _today_key()
        if not force and state.get("last_daily_read_day") == today:
            return
        await self._perform_news_reading(reason="daily", allow_share=False, force=True)
