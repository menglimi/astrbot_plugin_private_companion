# -*- coding: utf-8 -*-
"""DailyStateDetailPart03Mixin。

由 tools/split_mixin_domain.py 从 daily_state_detail.py 机械抽取（12 个方法 + 0 个模块级名字 + 0 个类级赋值 / 461 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateDetailMixin）。
"""
from __future__ import annotations

from .daily_state_detail_shared import _now_ts, logger
from .daily_state_detail_shared import Any
from .daily_state_detail_shared import _safe_float
from .daily_state_detail_shared import _safe_int
from .daily_state_detail_shared import _single_line
from .daily_state_detail_shared import datetime
from .daily_state_detail_shared import deepcopy
from .daily_state_detail_shared import re
from .daily_state_detail_shared import scope_allows
from .daily_state_detail_shared import timedelta
from .daily_state_detail_shared import zoneinfo



class DailyStateDetailPart03Mixin:
    """DailyStateDetailPart03Mixin（从 DailyStateDetailMixin 拆出）。"""


    def _current_detail_model_location(self) -> str:
        segment = self._current_detail_segment_for_update()
        if not isinstance(segment, dict):
            return ""
        enhanced = self.data.get("detail_enhanced_segments", {})
        snapshot = enhanced.get(str(segment.get("key") or "")) if isinstance(enhanced, dict) else None
        if not isinstance(snapshot, dict) or _single_line(snapshot.get("status"), 24) != "done":
            return ""
        if not self._detail_model_location_policy_allowed(snapshot):
            return ""
        return _single_line(snapshot.get("location"), 60)

    def _current_location_state_text(self, state: dict[str, Any] | None = None) -> str:
        model_location = self._current_detail_model_location()
        if model_location:
            return model_location
        if isinstance(state, dict):
            override_ts = _safe_float(state.get("location_override_ts"), 0)
            if override_ts > 0:
                override_location = _single_line(state.get("location"), 40)
                if override_location and override_location not in {"", "地点感平稳", "地点无明显变化"}:
                    now = _now_ts()
                    if now - override_ts < 4 * 3600:
                        return override_location
        snapshot = self._current_story_plan_snapshot()
        for candidate in (
            snapshot.get("scene"),
            snapshot.get("event"),
        ):
            inferred = self._infer_location_from_text(str(candidate or ""))
            if inferred:
                return inferred
        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        if isinstance(current_item, dict):
            inferred = self._infer_location_from_text(
                f"{_single_line(current_item.get('activity'), 120)} {_single_line(current_item.get('message_seed'), 120)}"
            )
            if inferred:
                return inferred
        if isinstance(state, dict):
            fallback = _single_line(state.get("location"), 40)
            if fallback and fallback not in {"", "地点感平稳", "地点无明显变化"}:
                return fallback
        return ""

    def _coarse_roleplay_location_text(self, location: str) -> str:
        text = _single_line(location, 40)
        if not text:
            return ""
        if any(token in text for token in ("家", "房间", "卧室", "客厅", "书桌", "床", "被窝", "阳台")):
            return "家里"
        if any(token in text for token in ("学校", "教室", "食堂", "校门", "操场", "走廊", "自习")):
            return "学校"
        if any(token in text for token in ("工作", "办公室", "工位", "会议", "通勤")):
            return "工作地点"
        if any(token in text for token in ("路", "街", "外面", "楼下", "出门")):
            return "外面"
        if any(token in text for token in ("便利店", "超市", "商店")):
            return "外面"
        return text if text in {"家里", "学校", "工作地点", "外面", "路上"} else ""

    def _current_story_plan_snapshot(self) -> dict[str, Any]:
        plan = self.data.get("daily_story_plan", {})
        if not isinstance(plan, dict) or not self._is_plan_date_active(plan.get("date")):
            return {}
        now_minutes = self._effective_plan_now_minutes(str(plan.get("date") or ""))
        if now_minutes is None:
            return {}

        snapshot: dict[str, Any] = {}
        summary = _single_line(plan.get("summary"), 120)
        if summary:
            snapshot["summary"] = summary

        current_event = None
        for item in plan.get("today_events", []):
            if not isinstance(item, dict):
                continue
            if self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) == "cancelled":
                continue
            start, end = self._parse_window_minutes(str(item.get("window") or ""))
            if start is None or end is None:
                continue
            if start <= now_minutes < end:
                current_event = item
                break
        if isinstance(current_event, dict):
            snapshot["event"] = _single_line(current_event.get("event"), 100)
            snapshot["mood"] = _single_line(current_event.get("mood"), 24)

        current_proactive = None
        for item in plan.get("proactive_events", []):
            if not isinstance(item, dict):
                continue
            if self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) == "cancelled":
                continue
            start, end = self._parse_window_minutes(str(item.get("window") or ""))
            if start is None or end is None:
                continue
            if start <= now_minutes < end:
                current_proactive = item
                break
        if isinstance(current_proactive, dict):
            snapshot["topic"] = _single_line(current_proactive.get("topic"), 80)
            snapshot["scene"] = _single_line(current_proactive.get("scene"), 80)
            snapshot["tone"] = _single_line(current_proactive.get("tone"), 30)
            snapshot["impulse"] = _single_line(current_proactive.get("impulse"), 100)
        return snapshot

    def _detail_segment_bounds_for_snapshot_key(self, key: str) -> tuple[int, int] | None:
        match = re.fullmatch(r"\d{4}-\d{2}-\d{2}:(\d+):(\d{1,2}:\d{2})", str(key or ""))
        if not match:
            return None
        plan = getattr(self, "data", {}).get("daily_plan", {})
        items = plan.get("items") if isinstance(plan, dict) else None
        if not isinstance(items, list):
            return None
        index = _safe_int(match.group(1), -1, minimum=-1)
        start = self._parse_hhmm_to_minutes(match.group(2))
        if index < 0 or start is None:
            return None
        next_start = None
        for next_item in items[index + 1 :]:
            if not isinstance(next_item, dict):
                continue
            next_start = self._parse_hhmm_to_minutes(next_item.get("time"))
            if next_start is not None:
                break
        current_item = items[index] if index < len(items) and isinstance(items[index], dict) else None
        end = self._plan_item_end_minutes(start, current_item, next_start=next_start)
        return start, end

    @staticmethod
    def _deepseek_peak_minute(value: str, *, allow_24: bool = False) -> int | None:
        match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(value or ""))
        if not match:
            return None
        hour, minute = int(match.group(1)), int(match.group(2))
        if allow_24 and hour == 24 and minute == 0:
            return 1440
        if hour > 23 or minute > 59:
            return None
        return hour * 60 + minute

    def _parse_deepseek_peak_windows(self) -> list[tuple[int, int]]:
        raw = str(getattr(self, "deepseek_peak_windows", "") or "")
        windows: list[tuple[int, int]] = []
        for item in re.split(r"[,，;；\n]+", raw):
            text = item.strip()
            if not text:
                continue
            match = re.fullmatch(r"\s*(\d{1,2}:\d{2})\s*[-~～—至]+\s*(\d{1,2}:\d{2})\s*", text)
            if not match:
                continue
            start = self._deepseek_peak_minute(match.group(1))
            end = self._deepseek_peak_minute(match.group(2), allow_24=True)
            if start is None or end is None or start == end:
                continue
            windows.append((start, end))
        return windows

    def _deepseek_peak_status(self, now: datetime | None = None) -> dict[str, Any]:
        timezone_name = str(getattr(self, "deepseek_peak_timezone", "Asia/Shanghai") or "Asia/Shanghai").strip()
        try:
            timezone = zoneinfo.ZoneInfo(timezone_name)
        except Exception:
            timezone_name = "Asia/Shanghai"
            timezone = zoneinfo.ZoneInfo(timezone_name)
        if now is None:
            local_now = datetime.now(timezone)
        elif now.tzinfo is None:
            local_now = now.replace(tzinfo=timezone)
        else:
            local_now = now.astimezone(timezone)
        windows = self._parse_deepseek_peak_windows()
        minute = local_now.hour * 60 + local_now.minute
        active = any(
            (start <= minute < end) if start < end else (minute >= start or minute < end)
            for start, end in windows
        )
        transitions: list[datetime] = []
        base_day = local_now.date()
        # Include yesterday so a cross-midnight window can expose its upcoming
        # end transition while the current time is after midnight.
        for day_offset in range(-1, 3):
            day = base_day + timedelta(days=day_offset)
            for start, end in windows:
                start_dt = datetime.combine(day, datetime.min.time(), timezone) + timedelta(minutes=start)
                end_day = day + timedelta(days=1) if start > end else day
                end_minute = end if end < 1440 else 0
                if end == 1440:
                    end_day = day + timedelta(days=1)
                end_dt = datetime.combine(end_day, datetime.min.time(), timezone) + timedelta(minutes=end_minute)
                if start_dt > local_now:
                    transitions.append(start_dt)
                if end_dt > local_now:
                    transitions.append(end_dt)
        next_transition = min(transitions) if transitions else None
        replacement_id = str(getattr(self, "deepseek_peak_replacement_provider_id", "") or "").strip()
        enabled = bool(getattr(self, "enable_deepseek_peak_replacement", False))
        return {
            "enabled": enabled,
            "active": bool(enabled and active and replacement_id),
            "in_window": active,
            "configured": bool(replacement_id),
            "timezone": timezone_name,
            "current_time": local_now.strftime("%Y-%m-%d %H:%M"),
            "next_transition": next_transition.strftime("%Y-%m-%d %H:%M") if next_transition else "",
            "windows": [
                f"{start // 60:02d}:{start % 60:02d}-{('24:00' if end == 1440 else f'{end // 60:02d}:{end % 60:02d}')}"
                for start, end in windows
            ],
            "replacement_provider_id": replacement_id,
        }

    def _apply_deepseek_peak_replacement(
        self,
        provider_id: str,
        *,
        now: datetime | None = None,
        target: str = "plugin",
    ) -> str:
        original = str(provider_id or "").strip()
        if not scope_allows(getattr(self, "model_replacement_scope", "plugin"), target):
            return original
        status = self._deepseek_peak_status(now)
        replacement = str(status.get("replacement_provider_id") or "").strip()
        if not status.get("active") or not original or not replacement or replacement == original:
            return original
        if not self._provider_matches_deepseek(original):
            return original
        log_key = f"{local_day if (local_day := status.get('current_time', '')[:10]) else ''}|{original}|{replacement}"
        if getattr(self, "_deepseek_peak_last_log_key", "") != log_key:
            self._deepseek_peak_last_log_key = log_key
            logger.info("DeepSeek 高价时段临时路由: %s -> %s (%s)", original, replacement, status.get("current_time"))
        return replacement

    @staticmethod
    def _detail_event_text(item: dict[str, Any], limit: int = 160) -> str:
        if not isinstance(item, dict):
            return ""
        for key in (
            "event",
            "content",
            "detail",
            "description",
            "text",
            "narrative",
            "body",
            "细化",
            "细化内容",
            "细化叙述",
            "事件",
            "主要事件",
        ):
            text = _single_line(item.get(key), limit)
            if text:
                return text
        return ""

    def _format_current_detail_view(self) -> str:
        plan = self.data.get("daily_plan", {})
        if not isinstance(plan, dict) or not plan.get("items"):
            return "今天还没有日程，所以也没有可看的当前细化。"
        enhanced = self.data.get("detail_enhanced_segments", {})
        if not isinstance(enhanced, dict):
            enhanced = {}
        segment = self._current_detail_segment_for_update() or self._pick_detail_segment(plan, enhanced)
        if not segment:
            return "当前还没有可用的细化结果。先让今天的日程段完成细化，或者手动执行一次“陪伴 重置细化”。"
        key = str(segment.get("key") or "")
        snapshot = enhanced.get(key) if key else None
        if not isinstance(snapshot, dict):
            return "当前时间段还没有落地的细化内容。可以先执行一次“陪伴 重置细化”。"
        snapshot = deepcopy(snapshot)
        self._sanitize_detail_enhanced_segments_inplace({"current": snapshot})

        item = segment.get("item") if isinstance(segment, dict) else {}
        start_text = self._minutes_to_hhmm(_safe_int(segment.get("start"), 0))
        end_text = self._minutes_to_hhmm(_safe_int(segment.get("end"), 0))
        lines = [
            f"当前细化时段：{start_text}-{end_text}",
            f"对应日程：{_single_line((item or {}).get('activity'), 120)}",
        ]
        mood = _single_line((item or {}).get("mood"), 24)
        if mood:
            lines.append(f"日程情绪：{mood}")

        state_variables = snapshot.get("state_variables", [])
        if isinstance(state_variables, list) and state_variables:
            lines.append("状态变量：")
            for variable in state_variables[:8]:
                if not isinstance(variable, dict):
                    continue
                name = _single_line(variable.get("name"), 32)
                value = _single_line(variable.get("value"), 60)
                note = _single_line(variable.get("note"), 80)
                if name and value:
                    lines.append(f"- {name}: {value}" + (f"（{note}）" if note else ""))

        presence = snapshot.get("presence_status")
        if isinstance(presence, dict):
            mode = _single_line(presence.get("mode"), 24)
            reason = _single_line(presence.get("reason"), 80)
            if mode and mode != "unchanged":
                lines.append("QQ状态表现：")
                lines.append(f"- {mode}" + (f"｜{reason}" if reason else ""))

        interaction_updates = snapshot.get("interaction_updates", [])
        if isinstance(interaction_updates, list) and interaction_updates:
            update_lines: list[str] = []
            for update in interaction_updates[-4:]:
                if not isinstance(update, dict):
                    continue
                if _single_line(update.get("source_role"), 20) != "owner":
                    continue
                at = _single_line(update.get("at"), 8)
                user_text = _single_line(update.get("user_text"), 80)
                intensity = _single_line(update.get("intensity"), 12)
                reaction = _single_line(update.get("reaction"), 120)
                state_updates = update.get("state_updates")
                state_text = ""
                if isinstance(state_updates, list) and state_updates:
                    state_text = "；".join(_single_line(item, 60) for item in state_updates if _single_line(item, 60))
                if reaction or user_text:
                    prefix = f"- {at} " if at else "- "
                    parts = [
                        f"用户：{user_text}" if user_text else "",
                        f"强度：{intensity}" if intensity else "",
                        reaction,
                        state_text,
                    ]
                    update_lines.append(prefix + "｜".join(part for part in parts if part))
            if update_lines:
                lines.append("用户介入后的局部更新：")
                lines.extend(update_lines)

        today_events = snapshot.get("today_events", [])
        scoped_today_events = self._filter_snapshot_items_to_segment(today_events, segment)
        if scoped_today_events:
            lines.append("细化内容：")
            for detail_event in scoped_today_events[:8]:
                if not isinstance(detail_event, dict):
                    continue
                window = _single_line(detail_event.get("window"), 24)
                event_text = self._detail_event_text(detail_event, 160)
                mood_text = _single_line(detail_event.get("mood"), 24)
                if event_text:
                    tail = f"｜{mood_text}" if mood_text else ""
                    lines.append(f"- {window}｜{event_text}{tail}")
        else:
            summary = _single_line(snapshot.get("summary"), 160)
            if summary and summary not in {"这一段按原日程慢慢推进。", "这一段按原日程慢慢推进"}:
                lines.append(f"细化内容：{summary}")
            else:
                lines.append("细化内容：当前没有生成出可展示的细化正文。")

        proactive_events = snapshot.get("proactive_events", [])
        if isinstance(proactive_events, list) and proactive_events:
            scoped_proactive_events = self._filter_snapshot_items_to_segment(proactive_events, segment)
            if scoped_proactive_events:
                lines.append("这一段的主动契机：")
            for proactive_event in scoped_proactive_events[:10]:
                if not isinstance(proactive_event, dict):
                    continue
                window = _single_line(proactive_event.get("window"), 24)
                reason = _single_line(proactive_event.get("reason"), 24)
                action = _single_line(proactive_event.get("action"), 24) or "message"
                topic = _single_line(proactive_event.get("topic"), 48)
                motive = _single_line(proactive_event.get("motive"), 80)
                why = _single_line(proactive_event.get("why"), 100)
                scene = _single_line(proactive_event.get("scene"), 60)
                tone = _single_line(proactive_event.get("tone"), 24)
                impulse = _single_line(proactive_event.get("impulse"), 80)
                lines.append(f"- {window}｜{reason}｜{action}｜{topic or motive or '（无话题）'}")
                if why:
                    lines.append(f"  why：{why}")
                if motive:
                    lines.append(f"  motive：{motive}")
                meta_bits = []
                if scene:
                    meta_bits.append(f"scene={scene}")
                if tone:
                    meta_bits.append(f"tone={tone}")
                if impulse:
                    meta_bits.append(f"impulse={impulse}")
                if meta_bits:
                    lines.append("  " + "｜".join(meta_bits))
                chain = proactive_event.get("chain")
                if isinstance(chain, list) and chain:
                    lines.append("  chain：")
                    for step in chain[:4]:
                        if not isinstance(step, dict):
                            continue
                        kind = _single_line(step.get("kind"), 24)
                        after_minutes = _safe_int(step.get("after_minutes"), 0, 0)
                        step_reason = _single_line(step.get("reason"), 24)
                        step_topic = _single_line(step.get("topic"), 48)
                        step_motive = _single_line(step.get("motive"), 80)
                        step_tone = _single_line(step.get("tone"), 24)
                        extra = []
                        if after_minutes > 0:
                            extra.append(f"{after_minutes} 分钟后")
                        if step_reason:
                            extra.append(step_reason)
                        if step_topic:
                            extra.append(step_topic)
                        if step_tone:
                            extra.append(f"tone={step_tone}")
                        if step_motive:
                            extra.append(f"motive={step_motive}")
                        lines.append(f"    - {kind}" + (f"｜{'｜'.join(extra)}" if extra else ""))

        if len(lines) <= 4:
            lines.append("这段目前还比较空，说明细化结果里还没长出太多东西。")
        return "\n".join(lines)

    def _format_current_detail_brief(self) -> str:
        plan = self.data.get("daily_plan", {})
        if not isinstance(plan, dict) or not plan.get("items"):
            return "今天还没有日程，所以没有可细化的时间段。"
        enhanced = self.data.get("detail_enhanced_segments", {})
        if not isinstance(enhanced, dict):
            enhanced = {}
        segment = self._current_detail_segment_for_update() or self._pick_detail_segment(plan, enhanced)
        if not segment:
            return "当前还没有可用的细化结果。"
        key = str(segment.get("key") or "")
        snapshot = enhanced.get(key) if key else None
        if not isinstance(snapshot, dict):
            return "当前时间段还没有落地的细化内容。"
        snapshot = deepcopy(snapshot)
        self._sanitize_detail_enhanced_segments_inplace({"current": snapshot})

        item = segment.get("item") if isinstance(segment, dict) else {}
        start_text = self._minutes_to_hhmm(_safe_int(segment.get("start"), 0))
        end_text = self._minutes_to_hhmm(_safe_int(segment.get("end"), 0))
        lines = [
            f"{start_text}-{end_text}｜{_single_line((item or {}).get('activity'), 80)}",
        ]
        summary = _single_line(snapshot.get("summary"), 140)
        if summary:
            lines.append(summary)

        today_events = snapshot.get("today_events", [])
        if isinstance(today_events, list) and today_events:
            for detail_event in today_events[:3]:
                if not isinstance(detail_event, dict):
                    continue
                window = _single_line(detail_event.get("window"), 18)
                event_text = self._detail_event_text(detail_event, 120)
                mood_text = _single_line(detail_event.get("mood"), 20)
                if event_text:
                    lines.append(f"- {window} {event_text}" + (f"｜{mood_text}" if mood_text else ""))

        interaction_updates = snapshot.get("interaction_updates", [])
        if isinstance(interaction_updates, list) and interaction_updates:
            latest = next(
                (
                    item
                    for item in reversed(interaction_updates)
                    if isinstance(item, dict) and _single_line(item.get("source_role"), 20) == "owner"
                ),
                None,
            )
            if isinstance(latest, dict):
                user_text = _single_line(latest.get("user_text"), 60)
                reaction = _single_line(latest.get("reaction"), 100)
                if reaction or user_text:
                    lines.append("局部更新：" + "｜".join(part for part in (f"用户：{user_text}" if user_text else "", reaction) if part))

        return "\n".join(lines)
