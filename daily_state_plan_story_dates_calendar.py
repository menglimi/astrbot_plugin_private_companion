# -*- coding: utf-8 -*-
"""DailyStatePlanStoryDatesCalendarMixin。

由 tools/split_mixin_domain.py 从 daily_state_plan.py 机械抽取（16 个方法 + 0 个模块级名字 + 0 个类级赋值 / 613 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStatePlanMixin）。
"""
from __future__ import annotations
from .daily_state_plan_shared import Any
from .daily_state_plan_shared import _date_key
from .daily_state_plan_shared import _safe_int
from .daily_state_plan_shared import _single_line
from .daily_state_plan_shared import date
from .daily_state_plan_shared import datetime
from .daily_state_plan_shared import re
from .daily_state_plan_shared import runtime_persona_setting



class DailyStatePlanStoryDatesCalendarMixin:
    """DailyStatePlanStoryDatesCalendarMixin（从 DailyStatePlanMixin 拆出）。"""


    def _trim_story_plan_items(
        self,
        key: str,
        items: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        normalized = [item for item in items if isinstance(item, dict)]
        if not normalized:
            return []
        seen: set[tuple[Any, ...]] = set()
        deduped: list[dict[str, Any]] = []
        for item in normalized:
            identity = self._story_plan_item_identity(key, item)
            if identity in seen:
                continue
            seen.add(identity)
            deduped.append(item)
        if key == "long_term_events":
            return deduped[-limit:]
        ordered = sorted(deduped, key=self._story_plan_item_sort_key)
        if len(ordered) <= limit:
            return ordered
        return self._pick_story_items_with_coverage(ordered, limit)

    def _story_plan_item_identity(self, key: str, item: dict[str, Any]) -> tuple[Any, ...]:
        if key == "today_events":
            return (
                _single_line(item.get("window"), 20),
                _single_line(item.get("event"), 80),
            )
        if key == "proactive_events":
            return (
                _single_line(item.get("window"), 20),
                _single_line(item.get("reason"), 40),
                _single_line(item.get("action"), 40),
                _single_line(item.get("topic"), 80),
            )
        return (
            _single_line(item.get("title"), 80),
            _single_line(item.get("status"), 80),
        )

    def _story_plan_item_sort_key(self, item: dict[str, Any]) -> tuple[int, int, str]:
        start, end = self._parse_window_minutes(str(item.get("window") or ""))
        start_value = start if start is not None else 99_999
        end_value = end if end is not None else start_value
        if end_value < start_value:
            end_value += 24 * 60
        text = _single_line(
            item.get("event") or item.get("topic") or item.get("title"),
            80,
        )
        return (start_value, end_value, text)

    def _parse_date_value(self, value: Any) -> date | None:
        text = str(value or "").strip()
        for fmt in ("%Y-%m-%d", "%m-%d"):
            try:
                parsed = datetime.strptime(text, fmt)
                year = self._environment_now().year if fmt == "%m-%d" else parsed.year
                return date(year, parsed.month, parsed.day)
            except ValueError:
                continue
        return None

    def _next_occurrence(self, entry: dict[str, Any], now: datetime | None = None) -> date | None:
        base = self._parse_date_value(entry.get("date"))
        if base is None:
            return None
        today = (now or self._environment_now()).date()
        if entry.get("repeat_yearly", True):
            try:
                candidate = date(today.year, base.month, base.day)
            except ValueError:
                return None
            if candidate < today:
                try:
                    candidate = date(today.year + 1, base.month, base.day)
                except ValueError:
                    return None
            return candidate
        return base

    def _get_relevant_important_dates(self, now: datetime | None = None) -> list[dict[str, Any]]:
        entries = self.data.get("important_dates", [])
        if not isinstance(entries, list):
            return []
        current = now or self._environment_now()
        today = current.date()
        relevant = []
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("enabled", True):
                continue
            next_day = self._next_occurrence(entry, now=current)
            if next_day is None:
                continue
            days_until = (next_day - today).days
            remind_days = _safe_int(
                entry.get("remind_days"), runtime_persona_setting(self, "important_date_lookahead_days", 7), 0, 365
            )
            if 0 <= days_until <= remind_days:
                copy = dict(entry)
                copy["_next_date"] = _date_key(next_day)
                copy["_days_until"] = days_until
                relevant.append(copy)
        return sorted(
            relevant,
            key=lambda item: (
                _safe_int(item.get("_days_until"), 999),
                -_safe_int(item.get("priority"), 50),
            ),
        )

    def _format_important_dates_for_prompt(self) -> str:
        entries = self._get_relevant_important_dates()
        if not entries:
            return "（近期没有需要特别记住的日期）"
        lines = []
        for entry in entries[:8]:
            days = _safe_int(entry.get("_days_until"), 0)
            when = "今天" if days == 0 else f"{days} 天后"
            lines.append(
                f"- {when}｜{entry.get('title', '')}｜类型：{entry.get('type', '重要日期')}｜"
                f"备注：{entry.get('note', '')}"
            )
        return "\n".join(lines)

    def _format_calendar_context_for_prompt(self, now: datetime | None = None) -> str:
        current = now or self._environment_now()
        weekday_names = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
        weekday = weekday_names[current.weekday()]
        is_weekend = current.weekday() >= 5
        builtin_holidays = {
            "01-01": ("元旦", "节假日"),
            "05-01": ("劳动节", "节假日"),
            "10-01": ("国庆节", "节假日"),
        }
        month_day = current.strftime("%m-%d")
        today_dates = [
            entry
            for entry in self._get_relevant_important_dates(now=current)
            if _safe_int(entry.get("_days_until"), 999) == 0
        ]
        special_lines = []
        holiday_tokens = (
            "节",
            "节日",
            "假",
            "假期",
            "放假",
            "休息",
            "旅行",
            "生日",
            "纪念日",
            "春节",
            "元旦",
            "清明",
            "端午",
            "中秋",
            "国庆",
            "劳动",
            "圣诞",
        )
        has_holiday_signal = False
        builtin_holiday = builtin_holidays.get(month_day)
        if builtin_holiday:
            title, type_text = builtin_holiday
            special_lines.append(f"- 今天：{title}｜类型：{type_text}｜备注：内置公历节日")
            has_holiday_signal = True
        for entry in today_dates[:5]:
            title = _single_line(entry.get("title"), 40)
            type_text = _single_line(entry.get("type"), 30)
            note = _single_line(entry.get("note"), 80)
            joined = f"{title} {type_text} {note}"
            if any(token in joined for token in holiday_tokens):
                has_holiday_signal = True
            if title:
                special_lines.append(f"- 今天：{title}｜类型：{type_text or '重要日期'}｜备注：{note or '无'}")

        # The durable calendar is a constraint layer, not execution evidence.
        # Keep its wording explicit so a generated plan can use a confirmed
        # vacation/school rule without claiming that the event already
        # happened.  The fallback below preserves compatibility with hosts
        # that predate AgendaRuntimeMixin.
        calendar_snapshot = {}
        snapshot_getter = getattr(self, "_agenda_calendar_snapshot", None)
        if callable(snapshot_getter):
            try:
                candidate = snapshot_getter(current.date().isoformat(), now=current)
                if isinstance(candidate, dict):
                    calendar_snapshot = candidate
            except Exception:
                calendar_snapshot = {}
        calendar_timeline: dict[str, Any] = {}
        timeline_getter = getattr(self, "_agenda_calendar_timeline", None)
        if callable(timeline_getter):
            try:
                candidate = timeline_getter(
                    current.date().isoformat(),
                    now=current,
                    history_days=3,
                    horizon_days=14,
                )
                if isinstance(candidate, dict):
                    calendar_timeline = candidate
            except Exception:
                calendar_timeline = {}
        calendar_candidates: list[dict[str, Any]] = []
        candidates_getter = getattr(self, "_agenda_calendar_candidates_store", None)
        if callable(candidates_getter):
            try:
                raw_candidates = candidates_getter()
                if isinstance(raw_candidates, list):
                    calendar_candidates = [
                        item for item in raw_candidates
                        if isinstance(item, dict)
                        and str(item.get("lifecycle_state") or item.get("lifecycle") or "candidate") not in {"confirmed", "active", "completed", "cancelled", "expired"}
                    ][:8]
            except Exception:
                calendar_candidates = []
        all_calendar_events = [
            item for item in calendar_snapshot.get("effective_events", calendar_snapshot.get("events", []))
            if isinstance(item, dict) and str(item.get("status") or "") not in {"cancelled", "expired"}
        ]
        # New snapshots expose ``events`` as the complete adjusted list and
        # ``effective_events`` as the planning projection. Older snapshots may
        # only contain one list, so fall back gracefully.
        raw_calendar_events = calendar_snapshot.get("events")
        if isinstance(raw_calendar_events, list):
            all_calendar_events = [
                item for item in raw_calendar_events
                if isinstance(item, dict) and str(item.get("status") or "") not in {"cancelled", "expired"}
            ]
        calendar_events = [
            item for item in calendar_snapshot.get("effective_events", all_calendar_events)
            if isinstance(item, dict) and str(item.get("status") or "") not in {"cancelled", "expired"}
        ]
        calendar_constraints: list[str] = []
        calendar_conflict_lines: list[str] = []
        for event in calendar_events[:16]:
            title = _single_line(event.get("title"), 60)
            if not title:
                continue
            kind = str(event.get("kind") or event.get("type") or "event")
            kind_label = {
                "period": "长期区间",
                "recurrence": "周期规则",
                "event": "单次事件",
                "exception": "例外调整",
            }.get(kind, "日历事件")
            start_date = _single_line(
                event.get("occurrence_date") if kind != "period" else event.get("start_date"),
                24,
            ) or _single_line(event.get("date") or event.get("start_date"), 24)
            end_date = _single_line(event.get("end_date"), 24) if kind == "period" else ""
            date_text = start_date
            if end_date and end_date != start_date:
                date_text = f"{start_date} 至 {end_date}"
            start_at = _single_line(event.get("start_at"), 40)
            end_at = _single_line(event.get("end_at"), 40)
            clock_text = ""
            if (not event.get("all_day") or event.get("start_time") or event.get("end_time")) and start_at and "T" in start_at:
                clock_text = start_at.split("T", 1)[1][:5]
                if end_at and "T" in end_at:
                    clock_text += f"-{end_at.split('T', 1)[1][:5]}"
            if clock_text:
                date_text += f" {clock_text}"
            status_label = "已确认日历约束" if str(event.get("status") or "confirmed") in {"confirmed", "active"} else "待确认日历记录"
            calendar_constraints.append(f"- {title}｜{kind_label}｜{date_text or '今天'}｜{status_label}")
            joined = f"{title} {event.get('note', '')} {event.get('description', '')}"
            if any(token in joined for token in holiday_tokens):
                has_holiday_signal = True
        if calendar_constraints:
            calendar_constraints_block = "今天有效的日历约束（属于计划依据，不等于已经发生）：\n" + "\n".join(calendar_constraints)
        else:
            calendar_constraints_block = ""
        conflicts = calendar_snapshot.get("conflicts") if isinstance(calendar_snapshot.get("conflicts"), list) else []
        if conflicts:
            by_id = {
                str(item.get("source_calendar_id") or item.get("calendar_id") or ""): item
                for item in all_calendar_events
                if isinstance(item, dict)
            }
            for conflict in conflicts[:8]:
                winner_id = str(conflict.get("winner_id") or "")
                loser_id = str(conflict.get("loser_id") or "")
                winner = _single_line(by_id.get(winner_id, {}).get("title"), 50)
                loser = _single_line(by_id.get(loser_id, {}).get("title"), 50)
                if winner and loser:
                    state = "同优先级，需谨慎处理" if conflict.get("unresolved") else "按优先级采用前者"
                    suffix = "｜当天不生效" if not conflict.get("unresolved") else ""
                    calendar_conflict_lines.append(f"- {winner} 覆盖 {loser}｜{state}{suffix}")
        if has_holiday_signal:
            day_tone = "节假日/特殊日期"
        elif is_weekend:
            day_tone = "周末/休息日候选"
        else:
            day_tone = "普通工作日或学习日候选"
        rules = [
            f"日期：{current.strftime('%Y-%m-%d')}（{weekday}）",
            f"基础日期类型：{day_tone}",
        ]
        if special_lines:
            rules.append("今天相关的重要日期：\n" + "\n".join(special_lines))
        else:
            rules.append("今天相关的重要日期：无")
        if calendar_constraints_block:
            rules.append(calendar_constraints_block)
        if calendar_candidates:
            candidate_lines = []
            for item in calendar_candidates:
                title = _single_line(item.get("title"), 80)
                if not title:
                    continue
                date_text = _single_line(item.get("start_date") or item.get("date"), 20) or "近期"
                candidate_lines.append(f"- {title}｜{date_text}｜待确认")
            if candidate_lines:
                rules.append(
                    "近期对话待确认候选（仅供询问参考，不是事实）：\n"
                    + "\n".join(candidate_lines)
                    + "\n不得据此断言用户已经安排、正在执行或已经完成；如有必要，只能轻量询问确认。"
                )
        if calendar_conflict_lines:
            rules.append("日历重叠处理：\n" + "\n".join(calendar_conflict_lines))
        timeline_lines: list[str] = []
        current_phase = calendar_timeline.get("current_phase") if isinstance(calendar_timeline.get("current_phase"), list) else []
        if current_phase:
            phase_text = "、".join(
                f"{_single_line(item.get('title'), 48)}（{_single_line(item.get('start_date'), 16)} 至 {_single_line(item.get('end_date'), 16) or '待定'}）"
                for item in current_phase[:4]
                if isinstance(item, dict) and _single_line(item.get("title"), 48)
            )
            if phase_text:
                timeline_lines.append("当前生活阶段：" + phase_text)
        rhythms = calendar_timeline.get("rhythms") if isinstance(calendar_timeline.get("rhythms"), list) else []
        if rhythms:
            rhythm_text = "、".join(
                f"{_single_line(item.get('title'), 48)}（下次 {_single_line(item.get('next_occurrence'), 16) or '按周期推算'}）"
                for item in rhythms[:5]
                if isinstance(item, dict) and _single_line(item.get("title"), 48)
            )
            if rhythm_text:
                timeline_lines.append("稳定节律参考：" + rhythm_text)
        recent_changes = calendar_timeline.get("recent_changes") if isinstance(calendar_timeline.get("recent_changes"), list) else []
        if recent_changes:
            recent_text = "、".join(
                f"{_single_line(item.get('title'), 40)}（{_single_line(item.get('occurrence_date'), 16)}）"
                for item in recent_changes[:4]
                if isinstance(item, dict) and _single_line(item.get("title"), 40)
            )
            if recent_text:
                timeline_lines.append("最近变化/余波：" + recent_text)
        transitions = calendar_timeline.get("transitions") if isinstance(calendar_timeline.get("transitions"), list) else []
        if transitions:
            transition_text = "、".join(
                f"{_single_line(item.get('date'), 16)} {_single_line(item.get('title'), 40)}"
                for item in transitions[:5]
                if isinstance(item, dict) and _single_line(item.get("title"), 40)
            )
            if transition_text:
                timeline_lines.append("接下来可能发生的转换：" + transition_text)
        uncertainties = calendar_timeline.get("uncertainties") if isinstance(calendar_timeline.get("uncertainties"), list) else []
        if uncertainties:
            uncertainty_text = "、".join(
                _single_line(item.get("title") or item.get("reason") or "待确认变化", 44)
                for item in uncertainties[:4]
                if isinstance(item, dict)
            )
            if uncertainty_text:
                timeline_lines.append("仍不确定的部分：" + uncertainty_text)
        if timeline_lines:
            rules.append(
                "生活时间线（用于保持跨日连续，不等于执行事实）：\n"
                + "\n".join(f"- {line}" for line in timeline_lines)
                + "\n不要因为某一条当天计划就擅自结束或改写当前生活阶段；只有用户明确确认或日历明确记录了转换，才改变长期背景。稳定节律是默认倾向，临时事件可以改变当天，不必抹掉长期节律。存在待确认冲突时保留不确定性，用‘可能/先按目前记录’表达。"
            )
        rules.append(
            "日程判断：先看日期语境,再看人格设定。工作日可以有上课/上班；周末要更松,可以晚起、休息、出门、补一点自己的事；节假日/假期要明显区别于普通日,可以有庆祝、出行、宅家、已明确关系安排或假期拖延。"
        )
        rules.append(
            "如果人格、日程专用设定或重要日期备注里写了调休、补班、补课、考试、值班等例外,优先按这些例外来写。不要凭空塞入身份里没有的校园、职场或节日细节。"
        )
        if calendar_constraints or timeline_lines:
            rules.append(
                "日历使用边界：它提供生活阶段、节律和变化线索，不替代当前会话事实，也不自动删除日程。用户本轮明确说法优先；记录不确定时不要把推断写成确定事实。"
            )
        return "\n".join(rules)

    def _calendar_day_flags(self, now: datetime | None = None) -> dict[str, Any]:
        current = now or self._environment_now()
        is_weekend = current.weekday() >= 5
        builtin_holidays = {"01-01", "05-01", "10-01"}
        month_day = current.strftime("%m-%d")
        today_dates = [
            entry
            for entry in self._get_relevant_important_dates(now=current)
            if _safe_int(entry.get("_days_until"), 999) == 0
        ]
        holiday_tokens = (
            "节",
            "节日",
            "假",
            "假期",
            "放假",
            "休息",
            "旅行",
            "春节",
            "元旦",
            "清明",
            "端午",
            "中秋",
            "国庆",
            "劳动",
        )
        override_tokens = ("调休", "补班", "补课", "考试", "值班", "加班", "返校")
        has_holiday_signal = month_day in builtin_holidays
        has_override_signal = False
        has_calendar_context = False
        has_calendar_holiday_signal = False
        has_calendar_school_work = False
        calendar_snapshot = {}
        snapshot_getter = getattr(self, "_agenda_calendar_snapshot", None)
        if callable(snapshot_getter):
            try:
                candidate = snapshot_getter(current.date().isoformat(), now=current)
                if isinstance(candidate, dict):
                    calendar_snapshot = candidate
            except Exception:
                calendar_snapshot = {}
        calendar_events = calendar_snapshot.get("effective_events", calendar_snapshot.get("events", []))
        if isinstance(calendar_events, list):
            for item in calendar_events:
                if not isinstance(item, dict):
                    continue
                if str(item.get("status") or "confirmed") not in {"confirmed", "active"}:
                    continue
                has_calendar_context = True
                joined = _single_line(
                    f"{item.get('title', '')} {item.get('note', '')} {item.get('description', '')}",
                    180,
                )
                if any(token in joined for token in holiday_tokens):
                    has_calendar_holiday_signal = True
                if any(token in joined for token in ("上学", "上课", "放学", "学校", "上班", "通勤", "值班", "会议", "考试", "补课")):
                    has_calendar_school_work = True
                if any(token in joined for token in override_tokens):
                    has_override_signal = True
        for entry in today_dates:
            if not isinstance(entry, dict):
                continue
            joined = _single_line(
                f"{entry.get('title', '')} {entry.get('type', '')} {entry.get('note', '')}",
                160,
            )
            if any(token in joined for token in holiday_tokens):
                has_holiday_signal = True
            if any(token in joined for token in override_tokens):
                has_override_signal = True
        schedule_prompt = self._get_schedule_planning_prompt()
        if any(token in schedule_prompt for token in override_tokens):
            has_override_signal = True
        return {
            "is_weekend": is_weekend,
            "has_holiday_signal": has_holiday_signal or has_calendar_holiday_signal,
            "has_override_signal": has_override_signal,
            "has_calendar_context": has_calendar_context,
            "has_calendar_school_work": has_calendar_school_work,
            "calendar_snapshot": calendar_snapshot,
        }

    def _plan_conflicts_with_calendar(self, items: list[dict[str, str]], now: datetime | None = None) -> bool:
        """Report only explicit unresolved calendar conflicts.

        A plan can be a reasonable interpretation of a phase, a rhythm, or a
        user correction.  Keyword matching (for example, treating every
        ``上学`` row as invalid during a vacation) made the calendar a hidden
        hard filter and caused the companion to rewrite its own life.  The
        planner prompt now receives the timeline and can resolve ambiguity in
        prose; this hook is reserved for genuinely unresolved overlaps.
        """

        if not items:
            return False
        getter = getattr(self, "_agenda_calendar_timeline", None)
        if not callable(getter):
            return False
        try:
            timeline = getter(now=(now or self._environment_now()), history_days=0, horizon_days=1)
        except Exception:
            return False
        conflicts = timeline.get("conflicts") if isinstance(timeline, dict) else []
        return any(isinstance(item, dict) and item.get("unresolved") for item in (conflicts or []))

    def _is_micro_plan_activity(self, text: str) -> bool:
        normalized = _single_line(text, 160)
        if not normalized:
            return False
        length = len(normalized)
        instant_markers = (
            "看了一眼",
            "瞥了一眼",
            "拍了一下",
            "拍了下",
            "翻了个身",
            "揉了揉",
            "抬头看",
            "关掉闹钟",
            "叫了一声",
            "应了一声",
            "顺手点开",
        )
        if any(marker in normalized for marker in instant_markers):
            return length <= 30
        generic_short_markers = ("一下", "一眼", "一瞬", "顺手", "刚好", "忽然")
        continuity_markers = (
            "慢慢",
            "继续",
            "待着",
            "坐着",
            "趴着",
            "整理",
            "收拾",
            "吃饭",
            "洗漱",
            "发呆",
            "看剧",
            "听歌",
            "出门",
            "路上",
            "吹风",
            "睡前",
            "饭后",
            "午休",
            "收尾",
        )
        if any(marker in normalized for marker in generic_short_markers) and not any(
            marker in normalized for marker in continuity_markers
        ):
            return length <= 22
        return False

    def _plan_has_excess_micro_segments(self, items: list[dict[str, str]]) -> bool:
        if not items:
            return False
        micro_count = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            if self._is_micro_plan_activity(str(item.get("activity") or "")):
                micro_count += 1
        return micro_count >= max(2, len(items) // 4)

    def _is_abstract_plan_activity(self, text: str) -> bool:
        normalized = _single_line(text, 180)
        if not normalized:
            return False
        concrete_markers = (
            "起床", "赖床", "洗漱", "吃", "喝", "走", "坐", "趴", "靠", "收拾", "整理",
            "看", "听", "出门", "回家", "写", "刷", "逛", "吹风", "洗碗", "看剧", "躺",
            "翻", "换鞋", "背上", "拿着", "关灯", "开窗", "买", "收声", "聊天", "做饭",
        )
        abstract_markers = (
            "思绪", "心情", "气息", "余韵", "碎片", "温柔", "柔软", "飘忽", "微醺", "依恋",
            "恍惚", "生活感", "画面", "感觉", "梦里", "脑海里", "最后闪过", "随着光线",
        )
        if any(marker in normalized for marker in concrete_markers):
            abstract_count = sum(1 for marker in abstract_markers if marker in normalized)
            return abstract_count >= 3 and len(normalized) <= 22
        abstract_count = sum(1 for marker in abstract_markers if marker in normalized)
        return abstract_count >= 2

    def _plan_has_excess_abstract_segments(self, items: list[dict[str, str]]) -> bool:
        if not items:
            return False
        abstract_count = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            if self._is_abstract_plan_activity(str(item.get("activity") or "")):
                abstract_count += 1
        return abstract_count >= max(2, len(items) // 3)

    @staticmethod
    def _plan_activity_signature(text: str) -> str:
        normalized = _single_line(text, 180)
        if not normalized:
            return ""
        category_rules = (
            ("起床", ("起床", "醒来", "睡醒", "赖床", "闹钟", "被窝")),
            ("洗漱", ("洗漱", "刷牙", "洗脸", "梳头", "镜子", "卫生间")),
            ("早餐", ("早餐", "早饭", "面包", "牛奶", "豆浆", "粥")),
            ("正餐", ("午饭", "晚饭", "吃饭", "做饭", "干饭", "饭桌", "摆碗", "点外卖")),
            ("通勤出门", ("出门", "路上", "公交", "地铁", "校门", "换鞋", "背包", "打车")),
            ("校园课程", ("上课", "下课", "教室", "课间", "老师", "同桌", "黑板", "班会")),
            ("补课考试", ("补课", "考试", "测验", "卷子", "复习", "考场", "错题")),
            ("学习作业", ("作业", "自习", "刷题", "数学", "英语", "课本", "笔记", "书包")),
            ("工作事务", ("上班", "工位", "会议", "打卡", "下班", "同事", "项目", "文档")),
            ("家务整理", ("收拾", "整理", "扫地", "洗碗", "洗衣", "归位", "桌面", "房间")),
            ("休息摸鱼", ("午休", "休息", "摸鱼", "躺", "趴", "沙发", "发呆", "缓一会")),
            ("娱乐放松", ("看剧", "追番", "游戏", "刷短视频", "听歌", "小说", "漫画")),
            ("社交互动", ("聊天", "朋友", "家人", "消息", "电话", "群聊", "回复", "打开对话框")),
            ("购物外食", ("买", "便利店", "超市", "奶茶", "饮料", "小吃", "逛")),
            ("户外散步", ("散步", "走一段", "吹风", "公园", "楼下", "河边", "阳台", "开窗")),
            ("运动身体", ("运动", "跑步", "拉伸", "散操", "瑜伽", "出汗")),
            ("洗澡睡前", ("洗澡", "睡前", "关灯", "上床", "准备睡", "入睡", "枕头")),
        )
        hits: list[str] = []
        for label, tokens in category_rules:
            if any(token in normalized for token in tokens):
                hits.append(label)
            if len(hits) >= 2:
                break
        if hits:
            return "+".join(hits)
        compact = re.sub(r"[，。！？、,.!?；;：:\s]+", "", normalized)
        return compact[:8]

    def _plan_signature(self, items: list[dict[str, Any]]) -> list[str]:
        signatures: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            signature = self._plan_activity_signature(
                f"{item.get('activity', '')} {item.get('message_seed', '')}"
            )
            if signature:
                signatures.append(signature)
        return signatures
