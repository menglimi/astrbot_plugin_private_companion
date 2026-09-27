# -*- coding: utf-8 -*-
"""NewsExplorationBilibiliTriggerNewsMixin。

由 tools/split_mixin_domain.py 从 news_exploration.py 机械抽取（15 个方法 + 0 个模块级名字 + 0 个类级赋值 / 450 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 NewsExplorationMixin）。
"""
from __future__ import annotations

from .news_exploration_shared import DEFAULT_AI_DAILY_JUYA_UID, DEFAULT_AI_DAILY_SOURCES, logger
from .news_exploration_shared import Any
from .news_exploration_shared import _now_ts
from .news_exploration_shared import _safe_float
from .news_exploration_shared import _safe_int
from .news_exploration_shared import _single_line
from .news_exploration_shared import asyncio
from .news_exploration_shared import datetime
from .news_exploration_shared import hashlib
from .news_exploration_shared import html
from .news_exploration_shared import parsedate_to_datetime
from .news_exploration_shared import random
from .news_exploration_shared import re
from .news_exploration_shared import runtime_persona_setting
from .news_exploration_shared import urlparse



class NewsExplorationBilibiliTriggerNewsMixin:
    """NewsExplorationBilibiliTriggerNewsMixin（从 NewsExplorationMixin 拆出）。"""


    def _bot_currently_bored_enough_for_bilibili(self) -> bool:
        now_dt = datetime.now()
        if now_dt.hour < 10 or now_dt.hour >= 23:
            return False
        state = self.data.get("daily_state", {})
        energy = _safe_int(state.get("energy") if isinstance(state, dict) else 70, 70, 0, 100)
        mood = _single_line(state.get("mood_bias") if isinstance(state, dict) else "", 24)
        current_item = self._news_current_agenda_item()
        activity = _single_line((current_item or {}).get("activity"), 80)
        text = f"{mood} {activity}"
        boredom_tokens = ("无聊", "发呆", "摸鱼", "刷视频", "短视频", "休息", "闲", "空")
        if any(token in text for token in boredom_tokens):
            return True
        return 35 <= energy <= 72 and random.random() < 0.34

    async def _maybe_trigger_bilibili_boredom_watch(self) -> None:
        if not (
            runtime_persona_setting(self, "enable_bilibili_integration", True)
            and runtime_persona_setting(self, "enable_bilibili_boredom_watch", True)
        ):
            return
        if not self._bilibili_available() or not self._bot_currently_bored_enough_for_bilibili():
            return
        state = self.data.setdefault("bilibili_integration", {})
        if not isinstance(state, dict):
            self.data["bilibili_integration"] = {}
            state = self.data["bilibili_integration"]
        now = _now_ts()
        min_interval = max(
            2,
            runtime_persona_setting(self, "bilibili_boredom_min_interval_hours", 8),
        ) * 3600
        if now - _safe_float(state.get("last_boredom_watch_at"), 0) < min_interval:
            return
        if now - _safe_float(state.get("last_boredom_watch_probe_at"), 0) < 30 * 60:
            return
        if random.random() > 0.38:
            return
        bili = self._find_bilibili_bot_instance()
        if bili is None:
            state["last_boredom_watch_probe_at"] = now
            self._save_data_sync(sections={"bilibili_integration"})
            return
        task = getattr(bili, "_proactive_task", None)
        if task is not None and not task.done():
            return
        operation = bili._run_proactive(max_watch=1)
        try:
            creator = getattr(self, "_create_lifecycle_background_task", None)
            bili._proactive_task = (
                creator(operation, label="bilibili_boredom_watch")
                if callable(creator)
                else asyncio.create_task(operation, name="private-companion-bilibili-boredom-watch")
            )
            if bili._proactive_task is None:
                close = getattr(operation, "close", None)
                if callable(close):
                    close()
                return False
            if not callable(creator):
                def consume(done_task: asyncio.Task) -> None:
                    try:
                        done_task.result()
                    except asyncio.CancelledError:
                        pass
                    except Exception as exc:
                        logger.warning(
                            "B站无聊刷视频后台任务失败: %s",
                            _single_line(exc, 160),
                        )

                bili._proactive_task.add_done_callback(consume)
            state["last_boredom_watch_at"] = now
            state["last_boredom_watch_status"] = "triggered"
            self._save_data_sync(sections={"bilibili_integration"})
            logger.info("已触发 B站 AI Bot 无聊刷视频联动")
        except Exception as e:
            state["last_boredom_watch_status"] = f"failed:{_single_line(str(e), 80)}"
            self._save_data_sync(sections={"bilibili_integration"})
            logger.debug(f"触发 B站 AI Bot 刷视频失败: {e}")

    def _maybe_schedule_bilibili_video_share(self) -> bool:
        if not runtime_persona_setting(self, "enable_bilibili_integration", True):
            return False
        include_memory_api = bool(self._bilibili_memory_api_available(allow_probe=False))
        candidates = self._latest_bilibili_video_candidates(include_memory_api=include_memory_api, limit=8)
        if not candidates:
            return False
        users = self.data.get("users")
        if not isinstance(users, dict):
            return False
        now = _now_ts()
        changed = False
        for user_id, user in users.items():
            if not isinstance(user, dict) or not self._is_target_private_user(str(user_id), user) or not user.get("enabled", True) or not user.get("umo"):
                continue
            if now - _safe_float(user.get("last_seen"), 0) < max(
                runtime_persona_setting(self, "idle_minutes", 60),
                _safe_int(
                    runtime_persona_setting(self, "external_event_idle_minutes", 90),
                    90,
                    5,
                    1440,
                ),
            ) * 60:
                continue
            if now - _safe_float(user.get("last_bilibili_share_at"), 0) < _safe_int(
                runtime_persona_setting(self, "bilibili_share_cooldown_hours", 10),
                10,
                0,
                168,
            ) * 3600:
                continue
            if self._external_link_share_cooldown_remaining(user, now=now) > 0:
                continue
            timer_event = self._get_active_llm_timer(user)
            if (
                _safe_float(user.get("next_proactive_at"), 0) > 0
                and str(user.get("planned_proactive_source") or "") == "timer"
                and self._llm_timer_can_use_internal_scheduler(timer_event if isinstance(timer_event, dict) else None)
            ):
                continue
            selection = self._select_external_event_for_user(user, candidates, source_type="bilibili", now=now)
            candidate = selection.get("payload") if isinstance(selection, dict) else None
            if not isinstance(candidate, dict):
                continue
            key = str(candidate.get("key") or candidate.get("bvid") or "")
            if key and str(user.get("last_bilibili_share_key") or "") == key:
                continue
            score = _safe_int(candidate.get("score"), 0, 0, 10)
            decision = self._external_event_share_decision(
                user,
                candidate,
                source_type="bilibili",
                wish={
                    "relevance": score,
                    "desire": max(score - 1, 0),
                    "should_share": score >= runtime_persona_setting(self, "bilibili_share_min_score", 7),
                },
                base_probability=runtime_persona_setting(self, "bilibili_share_probability", 0.35),
                now=now,
            )
            if not decision.get("should_share"):
                continue
            base_probability = runtime_persona_setting(self, "bilibili_share_probability", 0.35)
            chance = min(
                0.9,
                max(base_probability, _safe_float(decision.get("probability"), base_probability)),
            )
            preference = selection.get("preference") if isinstance(selection.get("preference"), dict) else {}
            if _safe_int(preference.get("score"), 0) >= 8:
                chance = min(0.95, chance + 0.12)
            if random.random() > chance:
                continue
            delay_minutes = random.randint(12, 70)
            scheduled = now + delay_minutes * 60
            title = _single_line(candidate.get("title"), 70) or "视频"
            accepted = self._offer_proactive_candidate(
                str(user_id),
                user,
                {
                    "source": "bilibili",
                    "reason": "bili_video_share",
                    "action": "message",
                    "scheduled_ts": scheduled,
                    "topic": title,
                    "score": max(score, _safe_int(decision.get("score"), score, 0, 100)),
                    "motive": "这条视频和对方兴趣相关",
                    "context_key": "bilibili_video_context",
                    "context": {**candidate, "created_ts": now, "share_decision": decision, "preference_match": preference},
                },
            )
            if not accepted:
                continue
            self._record_bilibili_share_to_memory(str(user_id), candidate)
            user["last_bilibili_share_key"] = key
            user["last_bilibili_share_at"] = now
            self._note_external_link_candidate(user, now=now)
            self._remember_external_event(candidate, source_type="bilibili", reason="bili_video_share")
            changed = True
        return changed

    def _news_source_items(self) -> list[dict[str, str]]:
        raw = str(runtime_persona_setting(self, "news_sources", "") or "")
        items: list[dict[str, str]] = []
        seen: set[str] = set()
        for line in self._split_news_source_lines(raw):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "|" in line:
                name, url = line.split("|", 1)
            else:
                name, url = "", line
            url = url.strip()
            source_type = "rss"
            mid = ""
            bvid = ""
            if url.lower().startswith("bilibili:"):
                mid = re.sub(r"\D+", "", url.split(":", 1)[1])
                source_type = "bilibili"
            elif url.lower().startswith("bvid:"):
                match = re.search(r"(BV[0-9A-Za-z]+)", url, flags=re.I)
                bvid = match.group(1) if match else ""
                source_type = "bilibili_video"
            elif re.fullmatch(r"\d{4,}", url):
                mid = url
                source_type = "bilibili"
            elif url.startswith(("http://", "https://")):
                parsed = urlparse(url)
                if parsed.netloc.endswith("bilibili.com") and parsed.path.startswith("/video/"):
                    match = re.search(r"/video/(BV[0-9A-Za-z]+)", parsed.path, flags=re.I)
                    if match:
                        bvid = match.group(1)
                        source_type = "bilibili_video"
                if "space.bilibili.com" in parsed.netloc or parsed.path.startswith("/space.bilibili.com"):
                    match = re.search(r"/(\d+)", parsed.path)
                    if match:
                        mid = match.group(1)
                        source_type = "bilibili"
            if source_type == "bilibili_video":
                if not bvid:
                    continue
                key = f"bilibili_video:{bvid}"
                if key in seen:
                    continue
                seen.add(key)
                items.append(
                    {
                        "name": _single_line(name, 40) or f"B站视频 {bvid}",
                        "url": f"https://www.bilibili.com/video/{bvid}",
                        "type": "bilibili_video",
                        "bvid": bvid,
                    }
                )
                if len(items) >= 12:
                    break
                continue
            if source_type == "bilibili":
                if not mid:
                    continue
                key = f"bilibili:{mid}"
                if key in seen:
                    continue
                seen.add(key)
                items.append(
                    {
                        "name": _single_line(name, 40) or f"B站 UP {mid}",
                        "url": f"https://space.bilibili.com/{mid}",
                        "type": "bilibili",
                        "mid": mid,
                    }
                )
                if len(items) >= 12:
                    break
                continue
            if not url.startswith(("http://", "https://")) or url in seen:
                continue
            seen.add(url)
            items.append({"name": _single_line(name, 40) or _single_line(url, 40), "url": url, "type": "rss"})
            if len(items) >= 12:
                break
        return items

    @staticmethod
    def _split_news_source_lines(raw: str) -> list[str]:
        text = str(raw or "").strip()
        if not text:
            return []
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) != 1:
            return lines
        line = lines[0]
        markers = list(re.finditer(r"(?:^|\s+)(#?\s*[^|\n]+?)\|(?=(?:https?://|bilibili:|bvid:))", line, flags=re.I))
        if len(markers) <= 1:
            return lines
        recovered: list[str] = []
        for index, match in enumerate(markers):
            start = match.end()
            end = markers[index + 1].start() if index + 1 < len(markers) else len(line)
            name = str(match.group(1) or "").strip()
            target = line[start:end].strip()
            if name and target:
                recovered.append(f"{name}|{target}")
        return recovered or lines

    @staticmethod
    def _news_xml_text(node: Any, *paths: str) -> str:
        if node is None:
            return ""
        for path in paths:
            found = node.find(path)
            if found is not None and found.text:
                text = re.sub(r"<[^>]+>", "", html.unescape(str(found.text)))
                text = re.sub(r"\s+", " ", text).strip()
                if text:
                    return text
        return ""

    @staticmethod
    def _news_parse_time(value: str) -> float:
        text = str(value or "").strip()
        if not text:
            return 0.0
        try:
            return parsedate_to_datetime(text).timestamp()
        except Exception:
            pass
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0.0

    def _news_item_key(self, item: dict[str, Any]) -> str:
        raw = "|".join(
            _single_line(item.get(key), 240)
            for key in ("link", "title", "source")
            if _single_line(item.get(key), 240)
        )
        return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16] if raw else ""

    @staticmethod
    def _bilibili_bvid_from_url(value: Any) -> str:
        text = str(value or "")
        match = re.search(r"(BV[0-9A-Za-z]+)", text)
        return match.group(1) if match else ""

    @staticmethod
    def _ai_daily_time_minutes(value: Any) -> int | None:
        text = str(value or "").strip().replace("：", ":")
        match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
        if not match:
            return None
        hour, minute = [int(part) for part in match.groups()]
        if not 0 <= minute <= 59:
            return None
        return (hour % 24) * 60 + minute

    def _ai_daily_source_key(self, source: dict[str, Any]) -> str:
        mid = re.sub(r"\D+", "", str(source.get("mid") or ""))
        if mid:
            return f"bilibili:{mid}"
        name = _single_line(source.get("name"), 40)
        return hashlib.sha1(name.encode("utf-8", errors="ignore")).hexdigest()[:12] if name else "unknown"

    def _ai_daily_source_items(self) -> list[dict[str, Any]]:
        raw = (
            str(runtime_persona_setting(self, "ai_daily_sources", "") or "").strip()
            or DEFAULT_AI_DAILY_SOURCES
        )
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [part.strip() for part in line.split("|")]
            if len(parts) < 3:
                continue
            name = _single_line(parts[0], 40) or "AI日报"
            author = _single_line(parts[1], 60) or name
            mid = re.sub(r"\D+", "", parts[2])
            if not mid:
                continue
            keywords = [token for token in re.split(r"[\s,，、/|]+", parts[3] if len(parts) >= 4 else "") if token]
            if not keywords:
                keywords = ["日报"] if "日报" in name else ["早报"]
            schedule = _single_line(parts[4] if len(parts) >= 5 else "", 10) or ("23:00" if "日报" in name else "12:00")
            schedule_minutes = self._ai_daily_time_minutes(schedule)
            if schedule_minutes is None:
                schedule = "23:00" if "日报" in name else "12:00"
                schedule_minutes = self._ai_daily_time_minutes(schedule) or 0
            item = {
                "name": name,
                "author_name": author,
                "type": "bilibili",
                "mid": mid,
                "url": f"https://space.bilibili.com/{mid}",
                "keywords": keywords[:6],
                "schedule": schedule,
                "schedule_minutes": schedule_minutes,
            }
            key = self._ai_daily_source_key(item)
            if key in seen:
                continue
            seen.add(key)
            item["key"] = key
            items.append(item)
            if len(items) >= 8:
                break
        if items:
            return items
        legacy_mid = (
            re.sub(
                r"\D+",
                "",
                str(
                    runtime_persona_setting(self, "ai_daily_source_uid", "")
                    or DEFAULT_AI_DAILY_JUYA_UID
                ),
            )
            or DEFAULT_AI_DAILY_JUYA_UID
        )
        return [
            {
                "name": "AI日报",
                "author_name": "橘鸦Juya",
                "type": "bilibili",
                "mid": legacy_mid,
                "url": f"https://space.bilibili.com/{legacy_mid}",
                "keywords": ["日报", "早报"],
                "schedule": "23:00",
                "schedule_minutes": 23 * 60,
                "key": f"bilibili:{legacy_mid}",
            }
        ]

    def _ai_daily_date_tokens(self, now_dt: datetime | None = None) -> list[str]:
        now_dt = now_dt or datetime.now()
        tokens = [
            now_dt.strftime("%Y-%m-%d"),
            now_dt.strftime("%Y/%m/%d"),
            now_dt.strftime("%Y年%m月%d日").replace("年0", "年").replace("月0", "月"),
            f"{now_dt.month}月{now_dt.day}日",
            f"{now_dt.month}.{now_dt.day}",
            f"{now_dt.month}-{now_dt.day}",
            f"{now_dt.month}/{now_dt.day}",
            f"{now_dt.month:02d}{now_dt.day:02d}",
            f"{now_dt.month}{now_dt.day:02d}",
        ]
        return list(dict.fromkeys(token for token in tokens if token))

    def _ai_daily_item_matches_source(self, item: dict[str, Any], source: dict[str, Any], today: str) -> bool:
        if not self._news_item_is_today(item, today):
            return False
        mid = re.sub(r"\D+", "", str(source.get("mid") or ""))
        owner_mid = re.sub(r"\D+", "", str(item.get("video_owner_mid") or ""))
        if mid and owner_mid and mid != owner_mid:
            return False
        if mid and owner_mid == mid:
            return True
        text = f"{item.get('title') or ''} {item.get('summary') or ''} {item.get('video_owner_name') or ''}"
        keywords = [str(token) for token in source.get("keywords") or [] if str(token).strip()]
        if keywords and not any(token in text for token in keywords):
            return False
        return True

    def _ai_daily_due_sources(self, now_dt: datetime, today: str, source_states: dict[str, Any], *, force: bool = False) -> list[dict[str, Any]]:
        now_minute = now_dt.hour * 60 + now_dt.minute
        due: list[dict[str, Any]] = []
        for source in self._ai_daily_source_items():
            key = str(source.get("key") or self._ai_daily_source_key(source))
            source["key"] = key
            state = source_states.get(key) if isinstance(source_states.get(key), dict) else {}
            if not force and state.get("last_success_date") == today:
                continue
            schedule_minutes = _safe_int(source.get("schedule_minutes"), 0, 0)
            if not force and now_minute < schedule_minutes:
                continue
            if not force and state.get("last_attempt_date") == today:
                continue
            due.append(source)
        due.sort(key=lambda item: _safe_int(item.get("schedule_minutes"), 0, 0))
        return due
