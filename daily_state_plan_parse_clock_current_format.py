# -*- coding: utf-8 -*-
"""DailyStatePlanParseClockCurrentFormatMixin。

由 tools/split_mixin_domain.py 从 daily_state_plan.py 机械抽取（7 个方法 + 0 个模块级名字 + 0 个类级赋值 / 321 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStatePlanMixin）。
"""
from __future__ import annotations

from .daily_state_plan_shared import _today_key
from .daily_state_plan_shared import Any
from .daily_state_plan_shared import _memory_archive_warning
from .daily_state_plan_shared import _safe_float
from .daily_state_plan_shared import _safe_int
from .daily_state_plan_shared import _single_line
from .daily_state_plan_shared import normalize_plan_item
from .daily_state_plan_shared import re
from .daily_state_plan_shared import runtime_persona_setting



class DailyStatePlanParseClockCurrentFormatMixin:
    """DailyStatePlanParseClockCurrentFormatMixin（从 DailyStatePlanMixin 拆出）。"""


    def _parse_plan_items(self, raw_text: str) -> list[dict[str, str]]:
        payload = self._extract_json_payload(raw_text)
        if payload is None:
            return []
        if isinstance(payload, dict):
            raw_items = (
                payload.get("schedule")
                or payload.get("items")
                or payload.get("tasks")
                or payload.get("events")
                or payload.get("plan")
                or []
            )
        elif isinstance(payload, list):
            raw_items = payload
        else:
            raw_items = []

        items: list[dict[str, str]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            raw_time = item.get("time") or item.get("start") or item.get("start_time") or item.get("begin_time") or item.get("开始时间")
            item_time, range_end = self._normalize_plan_clock_range(raw_time)
            if self._parse_hhmm_to_minutes(item_time) is None:
                continue
            raw_activity = _single_line(
                item.get("activity")
                or item.get("title")
                or item.get("task")
                or item.get("event")
                or item.get("内容"),
                120,
            )
            activity = self._align_plan_text_with_skill_bounds(
                self._sanitize_daily_plan_social_fact_text(
                    self._soften_destructive_daily_plan_text(raw_activity),
                    field="activity",
                )
            )
            if not activity:
                continue
            mood = self._align_plan_text_with_skill_bounds(
                self._soften_destructive_daily_plan_text(_single_line(item.get("mood"), 30))
            )
            raw_message_seed = _single_line(item.get("message_seed"), 140)
            message_seed = self._align_plan_text_with_skill_bounds(
                self._sanitize_empty_daily_plan_message_seed(
                    self._sanitize_daily_plan_social_fact_text(
                        self._soften_destructive_daily_plan_text(
                            self._deemphasize_state_report_preamble(
                                raw_message_seed,
                                reason="background_schedule",
                            )
                        ),
                        field="message_seed",
                    )
                )
            )
            items.append(
                {
                    "time": item_time,
                    "end": self._normalize_plan_clock(
                        item.get("end")
                        or item.get("end_time")
                        or item.get("finish_time")
                        or item.get("until")
                        or item.get("结束时间")
                        or range_end
                    ),
                    "activity": activity,
                    "mood": mood,
                    "message_seed": message_seed,
                    "basis": self._normalize_schedule_basis(item.get("basis"), default=["inspiration"]),
                    "confidence": min(1.0, _safe_float(item.get("confidence"), 0.7)),
                }
            )
        items = sorted(items, key=lambda item: self._parse_hhmm_to_minutes(item["time"]) or 0)
        items = items[: _safe_int(runtime_persona_setting(self, "daily_plan_item_count", 10), 10, 1)]
        self._normalize_plan_item_intervals(items)
        # Pass every generated item through the C3 write gate.  LLM fields such
        # as status, source_refs, authority and evidence are never trusted;
        # canonical axes are retained so downstream views cannot silently lose
        # the distinction between a plan and an observation.
        today = _today_key()
        for index, item in enumerate(items):
            try:
                canonical = normalize_plan_item(
                    {**item, "title": item.get("activity"), "date": today, "subject_actor_id": "bot_self", "actor_type": "bot"},
                    plan_id=f"{today}:{index}",
                    now=self._environment_now(),
                )
            except Exception:
                continue
            item.update(canonical)
            item["activity"] = _single_line(item.get("activity") or item.get("title"), 120)
            item["date"] = today
        return items

    def _normalize_plan_clock_range(self, value: Any) -> tuple[str, str]:
        """Normalize common model time forms and extract an optional range end."""

        text = _single_line(value, 32).strip()
        if not text:
            return "", ""
        matches = re.findall(r"(?<!\d)(\d{1,2})\s*[:：点时]\s*(\d{1,2})?", text)
        clocks: list[str] = []
        for hour_text, minute_text in matches[:2]:
            hour = int(hour_text)
            minute = int(minute_text or 0)
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                clocks.append(f"{hour:02d}:{minute:02d}")
        if not clocks:
            return "", ""
        return clocks[0], clocks[1] if len(clocks) > 1 else ""

    def _normalize_plan_clock(self, value: Any) -> str:
        start, _end = self._normalize_plan_clock_range(value)
        return start

    @staticmethod
    def _soften_destructive_daily_plan_text(text: str) -> str:
        softened = _single_line(text, 160)
        if not softened:
            return ""
        replacements = [
            (r"想[^，。,；;]{0,18}(砸|摔|打人|揍人|报复|毁掉|弄坏)[^，。,；;]{0,18}", "烦得想先躲开一会儿"),
            (r"(把|将)[^，。,；;]{0,14}(砸|摔|扔)[^，。,；;]{0,14}(地上|墙上|门上|出去|烂|碎)[^，。,；;]{0,8}", "把手边的东西往里推了推"),
            (r"(砸|摔)(东西|门|墙|书|杯子|手机|笔)[^，。,；;]{0,8}", "把东西先放远一点"),
            (r"(骂人|想骂|吼人|想吼)[^，。,；;]{0,10}", "把话咽回去"),
        ]
        for pattern, replacement in replacements:
            softened = re.sub(pattern, replacement, softened)
        softened = re.sub(r"(烦躁|暴躁|恼火)到?有点?攻击性", "烦躁得有点想躲开", softened)
        softened = softened.replace("想砸东西的烦躁", "有点烦,但努力收着")
        softened = softened.replace("想摔东西的烦躁", "有点烦,但努力收着")
        return _single_line(softened, 160)

    def _get_current_plan_item(self, plan: dict[str, Any]) -> dict[str, str] | None:
        if not self._is_plan_date_active(plan.get("date")):
            return None
        items = plan.get("items")
        if not isinstance(items, list):
            return None
        current_minutes = self._effective_plan_now_minutes(str(plan.get("date") or ""))
        if current_minutes is None:
            return None
        selected = None
        selected_start: int | None = None
        starts = self._normalized_plan_item_starts(items)
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            if self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) == "cancelled":
                continue
            item_minutes = starts[index] if index < len(starts) else None
            if item_minutes is None:
                continue
            next_start = next((value for value in starts[index + 1 :] if value is not None), None)
            item_end = self._plan_item_end_minutes(item_minutes, item, next_start=next_start)
            if item_minutes <= current_minutes < item_end:
                selected = item
                selected_start = item_minutes
                break
        if isinstance(selected, dict):
            # A clock window is not execution evidence.  Keep confirmed
            # schedule commitments available through the future/schedule
            # policy, but expose current plan text only when a compatible
            # observation or a short-lived resolver commit exists.
            policy_allows_current = True
            policy_getter = getattr(self, "_agenda_disclosure_view", None)
            if callable(policy_getter):
                policy_allows_current = False
                try:
                    view = policy_getter("current_fact", now=self._environment_now(), max_entries=128)
                    values = view.get("entries", []) if isinstance(view, dict) else getattr(view, "entries", [])
                    selected_key = _single_line(selected.get("plan_id"), 120)
                    selected_pair = (
                        _single_line(selected.get("time"), 12),
                        _single_line(selected.get("activity") or selected.get("title"), 120),
                    )
                    for value in values if isinstance(values, list) else []:
                        if not isinstance(value, dict):
                            continue
                        value_key = _single_line(value.get("plan_id") or value.get("entry_id"), 120)
                        value_pair = (
                            _single_line(value.get("time"), 12),
                            _single_line(value.get("title") or value.get("activity"), 120),
                        )
                        if (selected_key and selected_key == value_key) or (selected_pair == value_pair and all(selected_pair)):
                            policy_allows_current = True
                            break
                except Exception:
                    policy_allows_current = False
            evidence_kind = _single_line(selected.get("evidence_kind"), 48).lower()
            fact_eligibility = _single_line(selected.get("fact_eligibility"), 48).lower()
            status = _single_line(selected.get("status"), 32).lower()
            # 睡眠/休息段是 Bot 的内部状态模拟，不是需要外部执行证据的日程动作：
            # 计划里的“睡觉”只表达“Bot 此刻该休息”的内部状态，不主张任何已发生
            # 的外部事实。若不在此豁免，上游 C3 证据认证门槛会让普通计划项
            # （evidence_kind/fact_eligibility 均为 none）在这里返回 None，
            # 睡眠状态机（_refresh_sleep_runtime_state）拿不到当前睡眠项，
            # 睡眠相位就会永远停在 awake。
            if not self._is_sleepy_plan_item(selected) and (
                not policy_allows_current
                or not (
                    evidence_kind in {"interaction", "tool_action", "external_record"}
                    and fact_eligibility in {"current_observed", "history_observed", ""}
                    and status in {"active", "completed", "partially_completed", ""}
                )
            ):
                runtime_getter = getattr(self, "_agenda_runtime_scene", None)
                if callable(runtime_getter):
                    try:
                        runtime = runtime_getter(now=self._environment_now())
                    except Exception:
                        runtime = None
                    if isinstance(runtime, dict):
                        return {
                            "time": self._minutes_to_hhmm(current_minutes),
                            "end": _single_line(runtime.get("valid_until"), 40),
                            "activity": _single_line(runtime.get("state"), 120),
                            "mood": "当前状态",
                            "message_seed": "",
                            "subject_actor_id": "bot_self",
                            "evidence_kind": "self_state_commit",
                            "fact_eligibility": "current_internal",
                            "materialization_state": "active",
                            "status": "active",
                        }
                return None
            plan_date = str(plan.get("date") or "").strip()
            if (
                plan_date
                and plan_date != _today_key()
                and current_minutes >= 24 * 60
                and selected_start is not None
                and self._is_sleepy_plan_item(selected)
            ):
                elapsed = max(0, current_minutes - selected_start)
                carried = dict(selected)
                carried["time"] = self._minutes_to_hhmm(current_minutes)
                runtime = {}
                state = self.data.get("daily_state", {})
                if isinstance(state, dict) and isinstance(state.get("sleep_runtime"), dict):
                    runtime = state.get("sleep_runtime", {})
                phase = str(runtime.get("phase") or "")
                if phase == "woken":
                    carried["activity"] = "夜里被消息轻轻叫醒，还半梦半醒地留着一点睡意。"
                    carried["mood"] = "刚醒，迷糊"
                    carried["message_seed"] = "像刚从睡里被叫醒；如果用户不继续聊，会慢慢把手机放下睡回去。"
                elif phase == "sleeping_again":
                    carried["activity"] = "刚才被叫醒过一下，现在又慢慢睡回去了。"
                    carried["mood"] = "重新睡着，安静"
                    carried["message_seed"] = "睡意重新接上了；再被唤起时会有一点断续的迷糊感。"
                elif elapsed >= 45:
                    carried["activity"] = "夜里还在睡着，睡意早已沉下去，睡眠正在安静延续。"
                    carried["mood"] = "睡着，安静"
                    carried["message_seed"] = "还在睡着。如果这时候被叫醒，会有点迷糊；没人继续打扰就会继续睡下去。"
                else:
                    carried["activity"] = "刚从前一晚的睡前片段进入休息，正在慢慢安静下来。"
                    carried["mood"] = _single_line(selected.get("mood"), 40) or "安静"
                    carried["message_seed"] = "正在收声准备睡，语气会更轻。"
                return carried
            return selected
        return None

    def _get_clock_plan_item_for_display(self, plan: dict[str, Any]) -> dict[str, Any] | None:
        """Pick the scheduled row covering now for UI only, without claiming it happened."""

        if not isinstance(plan, dict) or not self._is_plan_date_active(plan.get("date")):
            return None
        items = plan.get("items")
        if not isinstance(items, list):
            return None
        now_minutes = self._effective_plan_now_minutes(str(plan.get("date") or ""))
        if now_minutes is None:
            return None
        starts = self._normalized_plan_item_starts(items)
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            if self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) == "cancelled":
                continue
            start = starts[index] if index < len(starts) else None
            if start is None:
                continue
            next_start = next((value for value in starts[index + 1 :] if value is not None), None)
            end = self._plan_item_end_minutes(start, item, next_start=next_start)
            if start <= now_minutes < end:
                return item
        return None

    def _format_daily_plan(self, plan: dict[str, Any]) -> str:
        if not plan or not plan.get("items"):
            return "今天还没有日程。"
        source = "模型生成" if plan.get("source") == "llm" else "备用日程"
        lines = [
            f"{runtime_persona_setting(self, 'bot_name', '小星')} 今天的日程（{plan.get('date', _today_key())},{source}）："
        ]
        state = self.data.get("daily_state", {})
        if isinstance(state, dict) and state.get("date") == plan.get("date"):
            lines.append(
                f"状态：能量 {state.get('energy', 70)}/100｜情绪偏{state.get('mood_bias', '平稳')}｜{state.get('sleep', '睡眠平稳')}"
            )
        status_labels = {
            "planned": "计划中",
            "active": "进行中",
            "completed": "已完成",
            "changed": "已变更",
            "cancelled": "已取消",
            "deferred": "已顺延",
            "unknown": "未核实",
            "overridden": "已被新安排覆盖",
        }
        for index, item in enumerate(plan.get("items", [])):
            if not isinstance(item, dict):
                continue
            mood = f"｜{item.get('mood')}" if item.get("mood") else ""
            window = f"{item.get('time')}-{item.get('end')}" if item.get("end") else str(item.get("time") or "")
            lifecycle = self._plan_item_display_status(plan, item, index)
            status = status_labels.get(lifecycle, "计划中")
            lines.append(f"{window}｜{status} {item.get('activity')}{mood}")
        archive_warning = _memory_archive_warning(plan)
        if archive_warning:
            lines.append(archive_warning)
        return "\n".join(lines)
