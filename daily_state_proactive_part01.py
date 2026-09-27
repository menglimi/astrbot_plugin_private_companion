# -*- coding: utf-8 -*-
"""DailyStateProactivePart01Mixin。

由 tools/split_mixin_domain.py 从 daily_state_proactive.py 机械抽取（7 个方法 + 0 个模块级名字 + 0 个类级赋值 / 477 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateProactiveMixin）。
"""
from __future__ import annotations

from .daily_state_proactive_shared import _now_ts, _today_key
from .daily_state_proactive_shared import Any
from .daily_state_proactive_shared import _safe_float
from .daily_state_proactive_shared import _safe_int
from .daily_state_proactive_shared import _single_line
from .daily_state_proactive_shared import random



class DailyStateProactivePart01Mixin:
    """DailyStateProactivePart01Mixin（从 DailyStateProactiveMixin 拆出）。"""


    def _reschedule_users_for_new_detail_events(self, segment: dict[str, Any]) -> None:
        users = self.data.get("users", {})
        if not isinstance(users, dict):
            return
        now = _now_ts()
        start = _safe_int(segment.get("start"), 0) * 60
        end = _safe_int(segment.get("end"), 0) * 60
        for user in users.values():
            if not isinstance(user, dict) or not user.get("umo"):
                continue
            next_at = _safe_float(user.get("next_proactive_at"), 0)
            if next_at <= 0:
                self._schedule_next_proactive(user, now=now)
                continue
            dt = self._environment_fromtimestamp(next_at)
            seconds_today = dt.hour * 3600 + dt.minute * 60 + dt.second
            if not (start <= seconds_today <= end):
                self._schedule_next_proactive(user, now=now)

    async def _apply_detail_presence_status(
        self,
        segment: dict[str, Any],
        detail: dict[str, Any] | None = None,
    ) -> None:
        if not self.enable_qq_presence_sync:
            return
        status = (detail or {}).get("presence_status") if isinstance(detail, dict) else None
        if not isinstance(status, dict):
            key = str((segment or {}).get("key") or "")
            enhanced = self.data.get("detail_enhanced_segments", {})
            snapshot = enhanced.get(key) if isinstance(enhanced, dict) else None
            status = snapshot.get("presence_status") if isinstance(snapshot, dict) else None
        # An omitted/unchanged status still matters when moving away from a
        # status that this plugin applied for the previous detail segment.
        # Treat it as a transition request, while leaving unrelated manual
        # QQ status changes untouched.
        if not isinstance(status, dict):
            status = {"mode": "unchanged"}
        key = str((segment or {}).get("key") or "")
        state = self.data.setdefault("qq_presence_state", {})
        if not isinstance(state, dict):
            state = {}
            self.data["qq_presence_state"] = state
        mode = str(status.get("mode") or status.get("status") or "unchanged").strip().lower()
        if mode in {"away", "invisible", "dnd", "do_not_disturb", "离开", "隐身", "请勿打扰", "勿扰"}:
            mode = "online"
        custom_text = _single_line(
            status.get("custom_text")
            or status.get("wording")
            or status.get("text")
            or status.get("label")
            or status.get("自定义状态")
            or status.get("文案"),
            28,
        )
        custom_sync_enabled = bool(getattr(self, "enable_qq_custom_presence_sync", False))
        custom_note = ""
        if mode in {"busy", "忙碌"}:
            if custom_sync_enabled:
                mode = "custom"
                custom_text = custom_text or "专注中"
            else:
                mode = "busy"
                custom_text = ""
                custom_note = "自定义短状态未开启，已改用标准忙碌"
        if mode in {"sleep", "睡觉", "睡眠"}:
            if custom_sync_enabled:
                mode = "custom"
                custom_text = custom_text or "休息中"
            else:
                return
        if mode in {"custom", "自定义", "自定义状态"} and not custom_sync_enabled:
            # A disabled custom-status feature must not clear a status managed
            # manually or by another QQ client.  Treat this plan as unchanged.
            return
        if mode in {"custom", "自定义", "自定义状态"} and not custom_text:
            return
        same_presence = (
            str(state.get("mode") or "") == mode
            and str(state.get("custom_text") or "") == custom_text
        )
        elapsed = _now_ts() - _safe_float(state.get("updated_at"), 0)
        previous_detail_key = str(state.get("detail_key") or "")
        detail_changed = bool(key and previous_detail_key and previous_detail_key != key)
        same_plan = (
            str(state.get("date") or "") == _today_key()
            and str(state.get("plan_date") or "") == str(self.data.get("detail_enhanced_day") or "")
            and previous_detail_key == key
        )
        if same_presence and not detail_changed and (
            (same_plan and bool(state.get("ok", False)) and elapsed < 10 * 60)
            or (not bool(state.get("ok", False)) and elapsed < 60 * 60)
        ):
            return
        if mode in {"", "unchanged", "keep", "保持", "不变"}:
            # Only reset a status with an explicit plugin ownership marker.
            # This avoids turning a user's manually selected QQ status into
            # online merely because the next schedule segment is quiet.
            if not detail_changed or not bool(state.get("managed_by_plugin", bool(previous_detail_key))):
                return
            if str(state.get("mode") or "") == "online" and not str(state.get("custom_text") or ""):
                state["detail_key"] = key
                state["date"] = _today_key()
                state["plan_date"] = str(self.data.get("detail_enhanced_day") or "")
                self._save_daily_state_sections({"qq_presence_state"})
                return
            ok, note = await self._set_qq_online_presence("online")
            state["detail_key"] = key
            state["date"] = _today_key()
            state["plan_date"] = str(self.data.get("detail_enhanced_day") or "")
            state["mode"] = "online"
            state["custom_text"] = ""
            state["reason"] = "当前日程段未要求自定义状态"
            state["updated_at"] = _now_ts()
            state["ok"] = bool(ok)
            state["note"] = _single_line(note, 120)
            state["managed_by_plugin"] = True
            self._save_daily_state_sections({"qq_presence_state"})
            return
        if mode in {"custom", "自定义", "自定义状态"}:
            ok, note = await self._set_qq_custom_presence(custom_text)
            mode = "custom"
            if not ok:
                note = f"{note}；未追加在线状态，保持账号原状态"
        else:
            ok, note = await self._set_qq_online_presence(mode)
        if custom_note:
            note = f"{note}；{custom_note}" if note else custom_note
        state["detail_key"] = key
        state["date"] = _today_key()
        state["plan_date"] = str(self.data.get("detail_enhanced_day") or "")
        state["mode"] = mode
        state["custom_text"] = custom_text
        state["reason"] = _single_line(status.get("reason"), 80)
        state["updated_at"] = _now_ts()
        state["ok"] = bool(ok)
        state["note"] = _single_line(note, 120)
        state["managed_by_plugin"] = True
        self._save_daily_state_sections({"qq_presence_state"})

    async def _ensure_current_detail_presence_status(self) -> None:
        plan = self.data.get("daily_plan", {})
        if not isinstance(plan, dict) or str(plan.get("date") or "") != _today_key():
            return
        enhanced = self.data.get("detail_enhanced_segments", {})
        if not isinstance(enhanced, dict):
            return
        segment = self._current_detail_segment_for_update()
        if not segment:
            return
        snapshot = enhanced.get(str(segment.get("key") or ""))
        if not isinstance(snapshot, dict) or snapshot.get("status") != "done":
            if self._refresh_daily_state_location_from_plan(plan=plan, segment=segment):
                self._save_daily_state_sections({"daily_state"})
            if self.enable_qq_presence_sync:
                # Clear a previous plugin-managed segment status while the
                # new segment is still being generated. The completed detail
                # will apply its own status when it becomes available.
                await self._apply_detail_presence_status(segment, {})
            return
        if self._refresh_daily_state_location_from_plan(plan=plan, detail=snapshot, segment=segment):
            self._save_daily_state_sections({"daily_state"})
        if not self.enable_qq_presence_sync:
            return
        await self._apply_detail_presence_status(segment, snapshot)

    def _balance_proactive_events_for_day(
        self,
        events: list[dict[str, Any]],
        *,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        prepared: list[dict[str, Any]] = []
        for raw in events:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            if str(item.get("reason") or "") == "state_share":
                item["reason"] = "quiet_care"
            for key, fallback in (
                ("topic", "短短说一句"),
                ("why", "生活里刚好空出一点缝隙"),
                ("motive", "刚好停了一下，想短短说一句"),
                ("impulse", "想短短说一句"),
            ):
                item[key] = _single_line(item.get(key), 100) or fallback
            if str(item.get("action") or "message") == "message":
                item["action"] = self._preferred_action_for_story_event(item)
            prepared.append(item)
        if not prepared:
            return []
        ordered = sorted(prepared, key=self._story_plan_item_sort_key)
        buckets = ["morning", "noon", "afternoon", "evening", "late_night"]
        by_bucket: dict[str, list[dict[str, Any]]] = {bucket: [] for bucket in buckets}
        for item in ordered:
            bucket = self._proactive_daypart_bucket_for_event(item)
            if bucket in by_bucket:
                by_bucket[bucket].append(item)
        selected: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()

        def add(item: dict[str, Any]) -> None:
            if len(selected) >= limit:
                return
            identity = self._story_plan_item_identity("proactive_events", item)
            if identity in seen:
                return
            seen.add(identity)
            selected.append(item)

        for bucket in buckets:
            if by_bucket[bucket]:
                add(by_bucket[bucket][0])
        for bucket in buckets:
            cap = 1 if bucket == "late_night" else 2
            count = sum(1 for item in selected if self._proactive_daypart_bucket_for_event(item) == bucket)
            for item in by_bucket[bucket][1:]:
                if count >= cap:
                    break
                add(item)
                count += 1
        remaining = sorted(
            ordered,
            key=lambda item: (
                0 if str(item.get("action") or "message") != "message" else 1,
                self._event_priority(item),
                self._story_plan_item_sort_key(item),
            ),
        )
        for item in remaining:
            if len(selected) >= limit:
                break
            add(item)
        return sorted(selected, key=self._story_plan_item_sort_key)

    def _preferred_action_for_story_event(self, event: dict[str, Any]) -> str:
        reason = str(event.get("reason") or "check_in")
        text = " ".join(
            _single_line(event.get(key), 80)
            for key in ("topic", "why", "scene", "motive", "impulse")
        )
        if self._photo_text_available() and (
            reason in {"activity_share", "diary_share", "background_schedule", "noon_greeting", "evening_greeting"}
            or any(token in text for token in self._visual_share_tokens())
        ):
            return "photo_text"
        if self._screen_glance_available() and reason in {"check_in", "quiet_care", "background_schedule"}:
            return "screen_peek"
        if self._voice_available() and reason in {"quiet_care", "diary_share", "insomnia_night", "evening_greeting"}:
            return "voice"
        if self._poke_available() and reason in {"check_in", "quiet_care", "morning_greeting", "evening_greeting"}:
            return "poke"
        return "message"

    def _generate_morning_linked_proactive_events(self) -> list[dict[str, Any]]:
        state = self.data.get("daily_state", {})
        if not isinstance(state, dict):
            return []
        sleep_text = str(state.get("sleep") or "")
        conditions = state.get("conditions", [])
        if not isinstance(conditions, list):
            conditions = []

        morning_start, morning_end = 8 * 60 + 20, 9 * 60 + 50
        window_getter = getattr(self, "_morning_greeting_window", None)
        if callable(window_getter):
            try:
                candidate_start, candidate_end = window_getter()
                if 0 <= candidate_start < candidate_end <= 24 * 60:
                    morning_start, morning_end = candidate_start, candidate_end
            except Exception:
                pass

        def morning_window(*, delay_minutes: int, span_minutes: int) -> str:
            latest_start = max(morning_start, morning_end - 8)
            start = min(morning_start + max(0, delay_minutes), latest_start)
            end = min(morning_end, max(start + 8, start + max(8, span_minutes)))
            return f"{start // 60:02d}:{start % 60:02d}-{end // 60:02d}:{end % 60:02d}"

        events: list[dict[str, Any]] = []
        if any(token in sleep_text for token in ("赖床", "闹钟", "起得有点迟", "还没完全开机", "懵懵", "有点懵")):
            events.append(
                {
                    "window": morning_window(delay_minutes=8, span_minutes=30),
                    "reason": "morning_greeting",
                    "action": "message",
                    "why": "迷迷糊糊醒来，虽然还想再睡，但先轻轻说声早安",
                    "topic": "赖床间隙的早安",
                    "motive": "迷迷糊糊醒来，虽然还想再睡，但先轻轻说声早安",
                    "scene": "睡意依旧，不想起床",
                    "tone": "迷糊",
                    "impulse": "虽然打算继续睡，但想轻轻说声早安",
                    "chain": [
                        {"kind": "name_only_opener"},
                        {"kind": "if_no_reply", "after_minutes": 80, "reason": "check_in", "topic": "赖床醒来", "motive": "回笼觉结束，看看用户是先醒了还是依旧在睡", "tone": "耐心等待"},
                        {"kind": "if_still_no_reply", "after_minutes": 140, "reason": "morning_greeting", "topic": "催用户起床", "motive": "用户依旧没有回应你的消息，该催用户起床了", "tone": "调侃"},
                    ],
                    "mood": "迷糊",
                }
            )
        elif any(token in sleep_text for token in ("睡得很浅", "半夜醒", "一晚上都在做梦", "失眠")):
            events.append(
                {
                    "window": morning_window(delay_minutes=6, span_minutes=28),
                    "reason": "morning_greeting",
                    "action": "message",
                    "why": "醒来还带着一点睡意时,迷迷糊糊先发一声早安。",
                    "topic": "没完全醒的早安",
                    "motive": "人还没完全清醒,但还是先想打个招呼",
                    "scene": "人还带着睡意的时候",
                    "tone": "迟钝",
                    "impulse": "想轻轻说声早安",
                    "chain": [
                        {"kind": "name_only_opener"},
                        {"kind": "if_no_reply", "after_minutes": 90, "reason": "check_in", "topic": "早安余韵", "motive": "已经清醒过来，但刚刚和用户说的早安还没得到回应,猜测用户还在休息", "tone": "耐心等待"},
                        {"kind": "if_still_no_reply", "after_minutes": 150, "reason": "morning_greeting", "topic": "催用户起床", "motive": "用户依旧没有回应你的消息，该催用户起床了", "tone": "调侃"},
                    ],
                    "mood": "迟钝",
                }
            )
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        if energy >= 62 and random.random() < 0.45:
            events.append(
                {
                    "window": morning_window(delay_minutes=3, span_minutes=24),
                    "reason": "morning_greeting",
                    "action": "message",
                    "why": "睡得很好,习惯性地想去打个招呼。",
                    "topic": "早安",
                    "motive": "昨晚睡得很好，刚醒来就去和用户打个招呼",
                    "scene": "刚从床上爬起来的时候",
                    "tone": "清爽",
                    "impulse": "想轻轻说早安",
                    "chain": [
                        {"kind": "name_only_opener"},
                        {"kind": "if_no_reply", "after_minutes": 85, "reason": "check_in", "topic": "早安余韵", "motive": "刚刚和用户说了早安但没得到回应,猜测用户还在休息", "tone": "耐心等待"},
                        {"kind": "if_still_no_reply", "after_minutes": 145, "reason": "morning_greeting", "topic": "催用户起床", "motive": "用户依旧没有回应你的消息，该催用户起床了", "tone": "调侃"},
                    ],
                    "mood": "清爽",
                }
            )
        for cond in conditions:
            if not isinstance(cond, dict):
                continue
            title = str(cond.get("title") or "")
            label = str(cond.get("label") or "")
            if "睡眠延续" in title and random.random() < 0.55:
                events.append(
                    {
                        "window": morning_window(delay_minutes=10, span_minutes=30),
                        "reason": "morning_greeting",
                        "action": "message",
                        "why": "睡意延续到白天,有种半梦半醒的感觉",
                        "topic": "刚醒来后脑子晕乎乎的",
                        "motive": "依旧带着睡意的早安问候",
                        "scene": "依旧带着睡意",
                        "tone": "半梦半醒",
                        "impulse": "醒来迷迷糊糊的，想轻轻说早安",
                        "mood": _single_line(cond.get("mood"), 20) or "迟钝",
                    }
                )
                break
            if any(token in label for token in ("赖床", "闹钟", "起得有点迟")):
                events.append(
                    {
                        "window": morning_window(delay_minutes=6, span_minutes=32),
                        "reason": "morning_greeting",
                        "action": "message",
                        "why": "早晨发生了一点生活小插曲，和用户抱怨一句或打个招呼。",
                        "topic": "早晨的生活小插曲",
                        "motive": "早上折腾了一下,想来找你吐个小槽",
                        "scene": "被早晨的小事故折腾了一下之后",
                        "tone": "迷糊又有点乱",
                        "impulse": "想顺手分享早上的生活小插曲",
                        "mood": "迷糊",
                    }
                )
                break
        return events[:2]

    def _generate_daypart_linked_proactive_events(self) -> list[dict[str, Any]]:
        state = self.data.get("daily_state", {})
        if not isinstance(state, dict):
            return []
        weather = self._weather_summary_text(self.data.get("daily_weather", {}))
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        sleep_text = str(state.get("sleep") or "")
        events: list[dict[str, Any]] = []
        if 36 <= energy <= 68 and random.random() < 0.58:
            events.append(
                {
                    "window": "12:10-13:30",
                    "reason": "noon_greeting",
                    "action": "message",
                    "why": "中午有些犯困，想短短打声招呼。",
                    "topic": "午后犯困",
                    "motive": "中午这会儿有点犯困，想短短说句话",
                    "scene": "午后犯困的时候",
                    "tone": "懒洋洋",
                    "impulse": "想趁午后休息时短短说一句",
                    "mood": "懒洋洋",
                }
            )
        if any(token in weather for token in ("晚霞", "晴", "阳光", "多云")) and random.random() < 0.52:
            events.append(
                {
                    "window": "17:20-19:10",
                    "reason": "activity_share",
                    "action": "photo_text" if self._photo_text_available() else "message",
                    "why": "傍晚天色好看时，想拍一张路上的画面给你看。",
                    "topic": "傍晚路上",
                    "motive": "傍晚路上的天色很好看，想拍给你看看",
                    "scene": "傍晚走在路上时",
                    "tone": "松弛",
                    "impulse": "想顺手分享傍晚路上的画面",
                    "mood": "松弛",
                }
            )
        if 45 <= energy <= 82 and random.random() < 0.46:
            events.append(
                {
                    "window": "15:20-17:10",
                    "reason": "check_in",
                    "action": "message",
                    "why": "下午短暂休息时，想轻轻问一句用户那边怎么样。",
                    "topic": "下午短暂休息",
                    "motive": "下午节奏缓下来一点，想看看用户是不是也能休息一下",
                    "scene": "下午短暂休息的时候",
                    "tone": "平静",
                    "impulse": "好奇用户在做什么",
                    "mood": "微松",
                }
            )
        if random.random() < 0.48:
            topic = self._pick_life_thought_topic("activity_share")
            action = "photo_text" if self._photo_text_available() and random.random() < 0.16 else "message"
            events.append(
                {
                    "window": "14:40-18:40" if 12 <= self._environment_now().hour < 18 else "19:20-21:40",
                    "reason": "activity_share",
                    "action": action,
                    "why": "日常里突然冒出一个小想法，想短短说一句。",
                    "topic": topic,
                    "motive": f"刚刚想到“{topic}”，想顺手分享一下",
                    "scene": "闲下来的时候",
                    "tone": "自然",
                    "impulse": "想把刚冒出来的小想法顺口提一下",
                    "mood": "微妙",
                }
            )
        if any(token in sleep_text for token in ("失眠", "睡得很浅", "半夜醒", "一晚上都在做梦")) and random.random() < 0.5:
            events.append(
                {
                    "window": "22:10-23:25",
                    "reason": "quiet_care",
                    "action": "message",
                    "why": "睡前还没完全困下来，想随便聊两句",
                    "topic": "睡前还没困下来",
                    "motive": "明明快该睡了，但还是想找用户说说话",
                    "scene": "准备睡觉但还没困下来的时候",
                    "tone": "平静",
                    "impulse": "想在睡前和用户聊天",
                    "mood": "安静",
                    "conversation_posture": "closing",
                }
            )
        if energy < 42 and random.random() < 0.42:
            events.append(
                {
                    "window": "19:40-21:10",
                    "reason": "quiet_care",
                    "action": "message",
                    "why": "累了一天之后，想在睡前和用户聊聊天",
                    "topic": "一天快结束时",
                    "motive": "今天快结束了，睡前想聊两句",
                    "scene": "一天快结束的时候",
                    "tone": "疲惫",
                    "impulse": "想在睡前和用户聊天",
                    "mood": "疲惫",
                    "conversation_posture": "closing",
                }
            )
        return events[:3]
