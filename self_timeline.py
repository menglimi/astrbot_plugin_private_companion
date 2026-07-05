# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key


class SelfTimelineMixin:
    """Build a small on-demand timeline of what the bot has recently done."""

    @staticmethod
    def _self_timeline_normalized_text(text: Any) -> str:
        return re.sub(r"\s+", "", str(text or "")).strip()

    def _user_asks_self_timeline(self, text: Any) -> bool:
        normalized = self._self_timeline_normalized_text(text)
        if not normalized:
            return False
        if re.search(r"(我|用户|主人|对方).{0,8}(几点|什么时候|啥时候|做了什么|干嘛|在干嘛)", normalized):
            return False
        time_words = (
            "几点",
            "什么时候",
            "啥时候",
            "几时",
            "刚才",
            "刚刚",
            "今天",
            "昨天",
            "前天",
            "上午",
            "中午",
            "下午",
            "傍晚",
            "晚上",
            "夜里",
            "最近",
        )
        action_words = (
            "做了什么",
            "干了什么",
            "干嘛",
            "在干嘛",
            "忙什么",
            "写了什么",
            "写了啥",
            "写过什么",
            "写过啥",
            "创作",
            "创作了什么",
            "看了什么",
            "看了啥",
            "读了什么",
            "读了啥",
            "翻了什么",
            "翻了啥",
            "发了什么",
            "发了啥",
            "说了什么",
            "说了啥",
            "拍了什么",
            "拍了啥",
            "画了什么",
            "生成了什么",
        )
        has_self = any(token in normalized for token in ("你", "bot", "Bot", "星缘", "孩子"))
        has_time = any(token in normalized for token in time_words) or self._self_timeline_query_minutes(normalized) is not None
        asks_qzone_publish = (
            has_time
            and any(token in normalized for token in ("说说", "QQ空间", "qq空间", "空间动态", "动态"))
            and any(token in normalized for token in ("发", "发布", "写", "说了什么", "发了什么", "发了啥"))
        )
        if asks_qzone_publish:
            return True
        return has_self and has_time and any(token in normalized for token in action_words)

    def _format_self_timeline_context_for_reply(
        self,
        text: Any,
        user: dict[str, Any] | None = None,
        *,
        limit: int = 8,
    ) -> str:
        query = _single_line(text, 300)
        if not self._user_asks_self_timeline(query):
            return ""
        entries = self._collect_self_timeline_entries(query, user=user)
        if not entries:
            return (
                "【自我时间线检索】\n"
                "用户在问 Bot 自己某个时间做过什么，但当前没有检索到可靠记录。"
                "回复时可以说记不准或没有留下记录，不要编造具体事件。"
            )
        lines = [
            "【自我时间线检索】",
            "用户在问 Bot 自己某个时间做过什么。下面是从日程、细化、日记、主动行为、创作、私密阅读、生图和 QQ 空间发布记录里检索到的线索；只根据这些线索回答，不确定就说记不准。",
        ]
        for entry in entries[: max(1, limit)]:
            when = _single_line(entry.get("when"), 40) or "时间不详"
            source = _single_line(entry.get("source"), 24) or "记录"
            summary = _single_line(entry.get("summary"), 180)
            detail = _single_line(entry.get("detail"), 220)
            if detail:
                lines.append(f"- {when}｜{source}｜{summary}；{detail}")
            else:
                lines.append(f"- {when}｜{source}｜{summary}")
        return "\n".join(line for line in lines if line).strip()

    def _collect_self_timeline_entries(
        self,
        query: str,
        user: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        data = getattr(self, "data", {}) if isinstance(getattr(self, "data", {}), dict) else {}
        entries.extend(self._self_timeline_from_daily_plan(data))
        entries.extend(self._self_timeline_from_detail_segments(data))
        entries.extend(self._self_timeline_from_diaries(data))
        entries.extend(self._self_timeline_from_proactive_audit(data, user=user))
        entries.extend(self._self_timeline_from_creative(data))
        entries.extend(self._self_timeline_from_private_reading(data))
        entries.extend(self._self_timeline_from_photo_generation(data))
        entries.extend(self._self_timeline_from_qzone_publish(data))

        now = _now_ts()
        scored: list[tuple[float, float, dict[str, Any]]] = []
        for entry in entries:
            score = self._self_timeline_entry_score(entry, query, now=now)
            if score <= 0:
                continue
            ts = _safe_float(entry.get("ts"), 0)
            minutes = _safe_int(entry.get("minutes"), -1, -1, 24 * 60)
            sort_time = ts if ts > 0 else minutes
            scored.append((score, sort_time, entry))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in scored]

    def _self_timeline_from_daily_plan(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        plan = data.get("daily_plan") if isinstance(data.get("daily_plan"), dict) else {}
        items = plan.get("items") if isinstance(plan.get("items"), list) else plan.get("schedule")
        if not isinstance(items, list):
            return []
        date_key = _single_line(plan.get("date"), 24) or _today_key()
        entries = []
        for item in items:
            if not isinstance(item, dict):
                continue
            time_text = _single_line(item.get("time"), 12)
            activity = _single_line(item.get("activity"), 140)
            if not activity:
                continue
            mood = _single_line(item.get("mood"), 40)
            seed = _single_line(item.get("message_seed"), 160)
            entries.append(
                {
                    "source": "日程",
                    "date": date_key,
                    "time": time_text,
                    "minutes": self._self_timeline_parse_hhmm(time_text),
                    "when": self._self_timeline_when(date_key, time_text),
                    "summary": activity,
                    "detail": "；".join(part for part in (f"情绪:{mood}" if mood else "", seed) if part),
                    "keywords": "日程 做了什么 干嘛 忙 " + activity,
                }
            )
        return entries

    def _self_timeline_from_detail_segments(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        enhanced = data.get("detail_enhanced_segments") if isinstance(data.get("detail_enhanced_segments"), dict) else {}
        entries = []
        for key, snapshot in enhanced.items():
            if not isinstance(snapshot, dict):
                continue
            start, window = self._self_timeline_segment_window(str(key), snapshot)
            summary = _single_line(snapshot.get("summary") or snapshot.get("event"), 160)
            events = self._self_timeline_story_item_texts(snapshot.get("today_events"), limit=3)
            proactive = self._self_timeline_story_item_texts(snapshot.get("proactive_events"), limit=2)
            if not summary and not events and not proactive:
                continue
            date_key = self._self_timeline_date_from_key(str(key)) or _single_line(data.get("detail_enhanced_day"), 24) or _today_key()
            detail_parts = []
            if events:
                detail_parts.append("片段:" + " / ".join(events))
            if proactive:
                detail_parts.append("主动契机:" + " / ".join(proactive))
            entries.append(
                {
                    "source": "细化日程",
                    "date": date_key,
                    "time": window,
                    "minutes": start,
                    "when": self._self_timeline_when(date_key, window),
                    "summary": summary or events[0],
                    "detail": "；".join(detail_parts),
                    "keywords": "细化 日程 做了什么 干嘛 " + " ".join([summary, *events, *proactive]),
                }
            )
        return entries

    def _self_timeline_from_diaries(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        diaries = data.get("bot_diaries") if isinstance(data.get("bot_diaries"), list) else []
        entries = []
        for item in diaries[-8:]:
            if not isinstance(item, dict):
                continue
            date_key = _single_line(item.get("date"), 24)
            summary = _single_line(item.get("summary") or item.get("title"), 160)
            body = _single_line(item.get("body") or item.get("content"), 260)
            seed = _single_line(item.get("share_seed"), 140)
            if not summary and not body:
                continue
            entries.append(
                {
                    "source": "日记",
                    "date": date_key,
                    "ts": _safe_float(item.get("ts") or item.get("created_ts"), 0),
                    "when": self._self_timeline_when(date_key or "近日", _single_line(item.get("generated_at"), 20)),
                    "summary": summary or body,
                    "detail": seed or body,
                    "keywords": "日记 今天 昨天 做了什么 " + " ".join([summary, body, seed]),
                }
            )
        return entries

    def _self_timeline_from_proactive_audit(self, data: dict[str, Any], user: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        raw = data.get("proactive_audit_log") if isinstance(data.get("proactive_audit_log"), list) else []
        target_user_id = _single_line(user.get("user_id"), 80) if isinstance(user, dict) else ""
        entries = []
        for item in raw[-80:]:
            if not isinstance(item, dict):
                continue
            if target_user_id and _single_line(item.get("user_id"), 80) and _single_line(item.get("user_id"), 80) != target_user_id:
                continue
            status = _single_line(item.get("status"), 32)
            if status in {"obsolete"}:
                continue
            ts = _safe_float(item.get("updated_ts") or item.get("created_ts"), 0)
            topic = _single_line(item.get("topic"), 120)
            motive = _single_line(item.get("motive"), 180)
            text = _single_line(item.get("text_preview"), 180)
            action = _single_line(item.get("action"), 60)
            if not any((topic, motive, text, action)):
                continue
            entries.append(
                {
                    "source": "主动行为",
                    "ts": ts,
                    "date": self._self_timeline_date_from_ts(ts),
                    "when": self._self_timeline_when_from_ts(ts),
                    "summary": topic or text or motive or action,
                    "detail": "；".join(part for part in (f"行为:{action}" if action else "", motive, text) if part),
                    "keywords": "主动 发了什么 说了什么 拍了什么 语音 戳一戳 " + " ".join([topic, motive, text, action]),
                }
            )
        return entries

    def _self_timeline_from_creative(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        projects = data.get("creative_projects") if isinstance(data.get("creative_projects"), list) else []
        entries = []
        for project in projects[-12:]:
            if not isinstance(project, dict):
                continue
            title = _single_line(project.get("title"), 80)
            work_type = _single_line(project.get("work_type"), 40)
            premise = _single_line(project.get("premise"), 160)
            created_at = _safe_float(project.get("created_at"), 0)
            if title or premise:
                entries.append(
                    {
                        "source": "创作",
                        "ts": created_at,
                        "date": self._self_timeline_date_from_ts(created_at),
                        "when": self._self_timeline_when_from_ts(created_at),
                        "summary": f"开始构思《{title}》" if title else "开始构思一个创作项目",
                        "detail": "；".join(part for part in (work_type, premise) if part),
                        "keywords": "创作 写了什么 小说 诗 作品 " + " ".join([title, work_type, premise]),
                    }
                )
            for chunk in (project.get("draft_chunks") if isinstance(project.get("draft_chunks"), list) else [])[-4:]:
                if not isinstance(chunk, dict):
                    continue
                ts = _safe_float(chunk.get("at"), 0)
                text = _single_line(chunk.get("text"), 220)
                if not text:
                    continue
                entries.append(
                    {
                        "source": "创作",
                        "ts": ts,
                        "date": self._self_timeline_date_from_ts(ts),
                        "when": self._self_timeline_when_from_ts(ts),
                        "summary": f"续写《{title}》" if title else "续写了一段文字",
                        "detail": text,
                        "keywords": "创作 写了什么 小说 诗 作品 " + " ".join([title, text]),
                    }
                )
        return entries

    def _self_timeline_from_private_reading(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        state = data.get("jm_cosmos_integration") if isinstance(data.get("jm_cosmos_integration"), dict) else {}
        album = state.get("last_album") if isinstance(state.get("last_album"), dict) else {}
        if not album:
            return []
        ts = _safe_float(album.get("created_ts") or state.get("last_read_at"), 0)
        title = _single_line(album.get("title"), 100)
        impression = _single_line(album.get("reading_impression") or album.get("impression"), 220)
        keyword = _single_line(album.get("keyword"), 40)
        return [
            {
                "source": "私密阅读",
                "ts": ts,
                "date": self._self_timeline_date_from_ts(ts),
                "when": self._self_timeline_when_from_ts(ts),
                "summary": f"翻到《{title}》" if title else "翻了一会儿书柜夹层",
                "detail": "；".join(part for part in (f"关键词:{keyword}" if keyword else "", impression) if part),
                "keywords": "看了什么 读了什么 翻了什么 本子 漫画 夹层 阅读 " + " ".join([title, impression, keyword]),
            }
        ]

    def _self_timeline_from_photo_generation(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        raw = data.get("recent_photo_generations") if isinstance(data.get("recent_photo_generations"), list) else []
        entries = []
        for item in raw[:12]:
            if not isinstance(item, dict):
                continue
            ts = _safe_float(item.get("ts"), 0)
            kind = _single_line(item.get("kind"), 40)
            prompt = _single_line(item.get("prompt"), 220)
            note = _single_line(item.get("note"), 140)
            ok = bool(item.get("ok"))
            entries.append(
                {
                    "source": "生图/拍照",
                    "ts": ts,
                    "date": self._self_timeline_date_from_ts(ts),
                    "when": self._self_timeline_when_from_ts(ts),
                    "summary": f"{'生成了' if ok else '尝试生成'}{kind or '图片'}",
                    "detail": note or prompt,
                    "keywords": "图片 生图 拍照 自拍 画了什么 生成了什么 " + " ".join([kind, prompt, note]),
                }
            )
        return entries

    def _self_timeline_from_qzone_publish(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        state = data.get("qzone_integration") if isinstance(data.get("qzone_integration"), dict) else {}
        raw = state.get("recent_life_publish_texts") if isinstance(state, dict) else []
        if not isinstance(raw, list):
            return []
        reason_labels = {
            "life_publish": "生活说说",
            "emotional_vent": "心情说说",
            "manual_publish": "手动说说",
        }
        entries = []
        for item in raw[-12:]:
            if not isinstance(item, dict):
                text = _single_line(item, 180)
                ts = 0.0
                reason = ""
                image_count = 0
                tid = ""
            else:
                text = _single_line(item.get("text"), 180)
                ts = _safe_float(item.get("at"), 0)
                reason = _single_line(item.get("reason"), 40)
                image_count = _safe_int(item.get("image_count"), 0, 0, 99)
                tid = _single_line(item.get("tid"), 80)
            if not text:
                continue
            label = reason_labels.get(reason, "QQ 空间说说")
            detail_parts = [f"正文:{text}"]
            if image_count > 0:
                detail_parts.append(f"配图:{image_count}张")
            if tid:
                detail_parts.append(f"tid:{tid}")
            entries.append(
                {
                    "source": "QQ空间",
                    "ts": ts,
                    "date": self._self_timeline_date_from_ts(ts),
                    "when": self._self_timeline_when_from_ts(ts),
                    "summary": f"发布了{label}",
                    "detail": "；".join(detail_parts),
                    "keywords": "QQ空间 空间 说说 动态 发了什么 说了什么 刚才发了什么说说 " + " ".join([text, reason, label]),
                }
            )
        return entries

    def _self_timeline_entry_score(self, entry: dict[str, Any], query: str, *, now: float) -> float:
        normalized = self._self_timeline_normalized_text(query)
        score = 1.0
        date_score = self._self_timeline_date_score(entry, normalized, now=now)
        if date_score < 0:
            return 0.0
        score += date_score
        time_score = self._self_timeline_time_score(entry, normalized)
        if time_score < 0:
            return 0.0
        score += time_score
        keywords = str(entry.get("keywords") or entry.get("summary") or "")
        for token, boost in (
            ("写", 2.0),
            ("创作", 2.5),
            ("作品", 2.0),
            ("看", 1.6),
            ("读", 1.6),
            ("本子", 2.8),
            ("漫画", 2.4),
            ("发", 1.8),
            ("说", 1.2),
            ("主动", 2.0),
            ("图", 1.8),
            ("自拍", 2.4),
            ("拍", 2.0),
            ("生成", 2.0),
            ("日程", 1.6),
            ("干嘛", 1.2),
        ):
            if token in normalized and token in keywords:
                score += boost
        ts = _safe_float(entry.get("ts"), 0)
        if "刚" in normalized and ts > 0:
            age = max(0.0, now - ts)
            if age <= 2 * 3600:
                score += 3.0
            elif age > 12 * 3600:
                score -= 1.5
        return score

    def _self_timeline_date_score(self, entry: dict[str, Any], normalized_query: str, *, now: float) -> float:
        date_key = _single_line(entry.get("date"), 24)
        today = _today_key()
        yesterday = self._self_timeline_date_delta(-1)
        before_yesterday = self._self_timeline_date_delta(-2)
        if "前天" in normalized_query:
            return 4.0 if date_key == before_yesterday else -1.0
        if "昨天" in normalized_query:
            return 4.0 if date_key == yesterday else -1.0
        if any(token in normalized_query for token in ("今天", "刚才", "刚刚", "上午", "中午", "下午", "傍晚", "晚上", "夜里")):
            if not date_key or date_key == today:
                return 3.0
            return -1.0
        ts = _safe_float(entry.get("ts"), 0)
        if "最近" in normalized_query and ts > 0:
            return 3.0 if now - ts <= 7 * 86400 else -1.0
        return 0.5

    def _self_timeline_time_score(self, entry: dict[str, Any], normalized_query: str) -> float:
        query_minutes = self._self_timeline_query_minutes(normalized_query)
        if query_minutes is None:
            return 0.0
        entry_minutes = _safe_int(entry.get("minutes"), -1, -1, 24 * 60)
        if entry_minutes < 0 and _safe_float(entry.get("ts"), 0) > 0:
            try:
                dt = datetime.fromtimestamp(_safe_float(entry.get("ts"), 0))
                entry_minutes = dt.hour * 60 + dt.minute
            except Exception:
                entry_minutes = -1
        if entry_minutes < 0:
            return 0.0
        diff = abs(entry_minutes - query_minutes)
        if diff <= 20:
            return 5.0
        if diff <= 90:
            return 3.0
        if diff <= 180:
            return 1.0
        return -1.0

    @staticmethod
    def _self_timeline_query_minutes(normalized_query: str) -> int | None:
        match = re.search(r"(?<!\d)([01]?\d|2[0-3])[:：点](\d{1,2})?", normalized_query)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2) or 0)
            if minute >= 60:
                minute = 0
            return hour * 60 + minute
        chinese_match = re.search(
            r"(二十[一二三]?|十[一二三四五六七八九]?|[零一二两三四五六七八九])点(半)?",
            normalized_query,
        )
        if not chinese_match:
            return None
        hour = SelfTimelineMixin._self_timeline_chinese_hour(chinese_match.group(1))
        if hour is None:
            return None
        return hour * 60 + (30 if chinese_match.group(2) else 0)

    @staticmethod
    def _self_timeline_chinese_hour(text: str) -> int | None:
        mapping = {
            "零": 0,
            "一": 1,
            "二": 2,
            "两": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
            "十": 10,
        }
        if text in mapping:
            return mapping[text]
        if text.startswith("二十"):
            tail = text[2:]
            return 20 + (mapping.get(tail, 0) if tail else 0)
        if text.startswith("十"):
            tail = text[1:]
            return 10 + (mapping.get(tail, 0) if tail else 0)
        return None

    def _self_timeline_parse_hhmm(self, value: Any) -> int:
        parser = getattr(self, "_parse_hhmm_to_minutes", None)
        if callable(parser):
            parsed = parser(value)
            return _safe_int(parsed, -1, -1, 24 * 60)
        match = re.search(r"(\d{1,2}):(\d{2})", str(value or ""))
        if not match:
            return -1
        return int(match.group(1)) * 60 + int(match.group(2))

    def _self_timeline_segment_window(self, key: str, snapshot: dict[str, Any]) -> tuple[int, str]:
        keyed = re.fullmatch(r"(\d{4}-\d{2}-\d{2}):(\d+):(\d{1,2}:\d{2})", key)
        if keyed:
            start = self._self_timeline_parse_hhmm(keyed.group(3))
            if start >= 0:
                return start, keyed.group(3)
        for item in self._self_timeline_story_item_texts(snapshot.get("today_events"), limit=5):
            match = re.search(r"(\d{1,2}:\d{2})(?:\s*[-~—到至]\s*(\d{1,2}:\d{2}))?", item)
            if match:
                return self._self_timeline_parse_hhmm(match.group(1)), match.group(0)
        return -1, _single_line(key, 40)

    @staticmethod
    def _self_timeline_date_from_key(key: str) -> str:
        match = re.match(r"(\d{4}-\d{2}-\d{2})", str(key or ""))
        return match.group(1) if match else ""

    @staticmethod
    def _self_timeline_story_item_texts(value: Any, *, limit: int = 4) -> list[str]:
        if not isinstance(value, list):
            return []
        result = []
        for item in value:
            if isinstance(item, dict):
                text = _single_line(item.get("event") or item.get("summary") or item.get("topic") or item.get("motive"), 180)
            else:
                text = _single_line(item, 180)
            if text:
                result.append(text)
            if len(result) >= limit:
                break
        return result

    @staticmethod
    def _self_timeline_date_from_ts(ts: float) -> str:
        if ts <= 0:
            return ""
        try:
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        except Exception:
            return ""

    def _self_timeline_when_from_ts(self, ts: float) -> str:
        if ts <= 0:
            return "时间不详"
        try:
            dt = datetime.fromtimestamp(ts)
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return self._format_timestamp_elapsed(ts) if hasattr(self, "_format_timestamp_elapsed") else "时间不详"

    @staticmethod
    def _self_timeline_when(date_key: str, time_text: str) -> str:
        date_part = _single_line(date_key, 24)
        time_part = _single_line(time_text, 40)
        if date_part and time_part:
            return f"{date_part} {time_part}"
        return date_part or time_part or "时间不详"

    @staticmethod
    def _self_timeline_date_delta(days: int) -> str:
        try:
            return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
        except Exception:
            return ""
