# -*- coding: utf-8 -*-
"""ProactiveEngineEventPart02Mixin。

由 tools/split_mixin_domain.py 从 proactive_engine_event.py 机械抽取（13 个方法 + 0 个模块级名字 + 0 个类级赋值 / 482 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveEngineEventMixin）。
"""
from __future__ import annotations
from .proactive_engine_event_shared import Any
from .proactive_engine_event_shared import _engine_host
from .proactive_engine_event_shared import _safe_float
from .proactive_engine_event_shared import _safe_int
from .proactive_engine_event_shared import _single_line
from .proactive_engine_event_shared import _today_key
from .proactive_engine_event_shared import datetime
from .proactive_engine_event_shared import runtime_persona_setting
from .proactive_engine_event_shared import timedelta



class ProactiveEngineEventPart02Mixin:
    """ProactiveEngineEventPart02Mixin（从 ProactiveEngineEventMixin 拆出）。"""


    def _pick_pending_followup_event(
        self, user: dict[str, Any], now: float | None = None
    ) -> dict[str, Any] | None:
        now = now or _engine_host._now_ts()
        if self._private_user_role(user) == "friend":
            return None
        if self._in_llm_timer_silence_window(user, now=now):
            return None
        opener_event = self._build_suspended_opener_followup_event(user, now=now)
        if isinstance(opener_event, dict):
            return opener_event
        raw = user.get("pending_followup_event")
        if not isinstance(raw, dict):
            return None
        if raw.get("_meal_care_followup"):
            context = self._meal_care_active_context(user, now=now)
            blocked_by_newer_food_prompt = bool(
                context
                and self._meal_care_followup_blocked_by_newer_food_prompt(user, context, now=now)
            )
            if (
                not context
                or _safe_int(context.get("followup_count"), 0, 0, 1) >= 1
                or blocked_by_newer_food_prompt
            ):
                if blocked_by_newer_food_prompt:
                    context.update(
                        {
                            "active": False,
                            "stage": "closed_newer_food_prompt",
                            "closed_at": now,
                            "followup_due_at": 0,
                        }
                    )
                    user["meal_check_context"] = context
                user["pending_followup_event"] = {}
                return None
        raw = dict(raw)
        raw["reason"] = self._normalize_legacy_proactive_text(raw.get("reason"), limit=40) or _single_line(raw.get("reason"), 40) or "check_in"
        followup_date = str(raw.get("date") or "")
        if followup_date and followup_date != _today_key():
            return None
        scheduled = _safe_float(raw.get("_scheduled_ts"), 0)
        if scheduled <= 0:
            return None
        if scheduled <= now:
            return raw
        return raw

    def _build_suspended_opener_followup_event(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        raw = user.get("suspended_proactive")
        if not isinstance(raw, dict) or not raw.get("active"):
            return None
        if not raw.get("complaint_enabled") or raw.get("complaint_sent"):
            return None
        if max(_safe_float(user.get("awaiting_reply_since"), 0), _safe_float(user.get("last_sent"), 0)) <= 0:
            return None
        due_at = _safe_float(raw.get("complaint_after_ts"), 0)
        if due_at <= 0:
            return None
        now = now or _engine_host._now_ts()
        if now < due_at:
            return None
        name = _single_line(
            user.get("nickname") or runtime_persona_setting(self, "default_nickname", "你"),
            24,
        )
        return {
            "date": _today_key(),
            "window": self._window_from_delay_minutes(4, width_minutes=18),
            "reason": self._normalize_legacy_proactive_text(raw.get("complaint_reason"), limit=40) or "check_in",
            "action": "message",
            "why": "之前只叫了用户一声，因此把话说完",
            "topic": _single_line(raw.get("complaint_topic"), 80) or "刚才那句后面",
            "motive": _single_line(raw.get("complaint_motive"), 100) or f"刚才只喊了{name}一声，想补完话",
            "scene": "先前那句之后又过了一阵",
            "tone": _single_line(raw.get("complaint_tone"), 30) or "耐心等待",
            "impulse": "想把刚才没说完的话补上",
            "_scheduled_ts": due_at,
            "_opener_followup": True,
            "_cancel_on_inbound": True,
        }

    def _build_followup_event_from_chain(
        self,
        chain: list[dict[str, Any]] | None,
        *,
        origin_reason: str,
        origin_action: str,
        now_ts: float | None = None,
    ) -> dict[str, Any] | None:
        steps = [dict(step) for step in (chain or []) if isinstance(step, dict)]
        if not steps:
            return None
        current = None
        remaining: list[dict[str, Any]] = []
        consumed_name_only = False
        for step in steps:
            kind = str(step.get("kind") or "")
            if kind == "name_only_opener" and not consumed_name_only:
                consumed_name_only = True
                continue
            if current is None and kind in {"if_no_reply", "if_still_no_reply"}:
                current = step
                continue
            remaining.append(step)
        if not isinstance(current, dict):
            return None
        now_ts = now_ts or _engine_host._now_ts()
        after_minutes = _safe_int(current.get("after_minutes"), 18, 0, 240)
        origin_reason = self._normalize_legacy_proactive_text(origin_reason, limit=40)
        follow_reason = self._normalize_legacy_proactive_text(current.get("reason"), limit=40) or origin_reason or "check_in"
        if origin_reason == "morning_greeting" or follow_reason == "morning_greeting":
            after_minutes = max(after_minutes, 75)
        topic = _single_line(current.get("topic"), 80) or "刚才那条主动后面"
        motive = self._normalize_internal_motive_text(
            _single_line(current.get("motive"), 100) or "刚才那句话信息不够完整,所以想补充一句"
        )
        tone = _single_line(current.get("tone"), 30)
        return {
            "date": _today_key(),
            "window": self._window_from_delay_minutes(after_minutes, width_minutes=18),
            "reason": follow_reason,
            "action": "message",
            "why": "刚才那句话还有个具体点没说完,如果用户还没接住,就把那一点补上。",
            "topic": topic,
            "motive": motive,
            "scene": "前一条主动消息发出去后又过了一阵",
            "tone": "克制一点,把重点补上" if (origin_reason == "morning_greeting" or follow_reason == "morning_greeting") else (tone or "有点认真,顺手补上"),
            "impulse": "早上那句还差个重点,想补完整" if (origin_reason == "morning_greeting" or follow_reason == "morning_greeting") else "刚才那句话还有个点没落到实处,想补完整",
            "_scheduled_ts": now_ts + after_minutes * 60,
            "_origin_action": origin_action,
            "_origin_reason": origin_reason,
            "_cancel_on_inbound": True,
            "_chain_followup": True,
            "chain": remaining,
        }

    def _pick_daily_greeting_event(
        self, user: dict[str, Any], now: float | None = None
    ) -> dict[str, Any] | None:
        if not runtime_persona_setting(self, "enable_daily_greetings", True):
            return None
        self._reset_daily_counter_if_needed(user)
        sent = user.get("greetings_sent", [])
        if not isinstance(sent, list):
            sent = []
            user["greetings_sent"] = sent
        suppressed = user.get("greetings_suppressed_by_inbound", [])
        if not isinstance(suppressed, list):
            suppressed = []
            user["greetings_suppressed_by_inbound"] = suppressed
        now_dt = self._environment_fromtimestamp(now or _engine_host._now_ts())
        minute = now_dt.hour * 60 + now_dt.minute
        morning_start, morning_end = self._morning_greeting_window()
        anchors = [
            (
                "morning_greeting",
                f"{self._minutes_to_hhmm(morning_start)}-{self._minutes_to_hhmm(morning_end)}",
                "刚睡醒，想打个招呼",
                "刚醒",
            ),
            ("noon_greeting", "12:05-13:35", "中午有些犯困，想打个招呼", "午饭后那会儿"),
            ("evening_greeting", "20:10-21:20", "晚上闲下来时，想打个招呼", "天暗下来那会儿"),
        ]
        today = now_dt.date()
        candidates = []
        for reason, window, why, topic in anchors:
            if self._greeting_was_sent_today(user, reason) or reason in suppressed:
                continue
            start, end = self._parse_window_minutes(window)
            if start is None or end is None:
                continue
            if self._private_user_role(user) == "friend":
                bucket = self._proactive_daypart_bucket_for_minute(start)
                if _safe_int(self._today_proactive_daypart_counts(user).get(bucket), 0, 0) >= 1:
                    continue
            if self._recent_activity_satisfies_greeting(user, reason, now=now_dt.timestamp()):
                if reason not in suppressed:
                    suppressed.append(reason)
                continue
            if minute >= end:
                continue
            start_dt = datetime.combine(today, datetime.min.time(), tzinfo=now_dt.tzinfo) + timedelta(minutes=start)
            end_dt = datetime.combine(today, datetime.min.time(), tzinfo=now_dt.tzinfo) + timedelta(minutes=end)
            earliest = max(now_dt + timedelta(minutes=1), start_dt)
            if earliest >= end_dt:
                continue
            if reason == "morning_greeting":
                early_window_end = min(
                    end_dt.timestamp(),
                    (earliest + timedelta(minutes=18)).timestamp(),
                )
                scheduled = _engine_host.random.uniform(
                    earliest.timestamp(),
                    max(earliest.timestamp() + 60, early_window_end),
                )
            elif reason == "evening_greeting":
                tighten_end = min(end_dt.timestamp(), (earliest + timedelta(minutes=48)).timestamp())
                scheduled = _engine_host.random.uniform(earliest.timestamp(), max(earliest.timestamp() + 60, tighten_end))
            else:
                scheduled = _engine_host.random.uniform(earliest.timestamp(), end_dt.timestamp())
            if self._friend_proactive_scheduled_too_early(user, scheduled):
                continue
            candidates.append(
                (
                    scheduled,
                    {
                        "window": window,
                        "reason": reason,
                        "action": "message",
                        "_daily_greeting": True,
                        "conversation_posture": "closing" if reason == "evening_greeting" else "",
                        "why": why,
                        "topic": topic,
                        "_scheduled_ts": scheduled,
                    },
                )
            )
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    def _pick_insomnia_night_event(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        check_now = _engine_host._now_ts() if now is None else now
        if not self._can_send_insomnia_night_message(user, now=check_now):
            return None
        night_key = self._insomnia_night_key(check_now)
        planned_context = user.get("insomnia_night_context") if isinstance(user.get("insomnia_night_context"), dict) else {}
        if (
            self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40) == "insomnia_night"
            and _single_line(planned_context.get("night_key"), 20) == night_key
            and _safe_float(user.get("next_proactive_at"), 0) > 0
        ):
            return None
        current = self._environment_fromtimestamp(check_now)
        end = (
            current.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            if current.hour >= 23
            else current.replace(hour=6, minute=0, second=0, microsecond=0)
        )
        remaining_seconds = int(end.timestamp() - check_now)
        if remaining_seconds <= 30:
            return None
        max_delay = max(20, min(22 * 60, remaining_seconds - 10))
        min_delay = min(4 * 60, max_delay)
        scheduled = check_now + _engine_host.random.randint(min_delay, max_delay)
        return {
            "window": "23:00-24:00" if current.hour >= 23 else "00:00-06:00",
            "reason": "insomnia_night",
            "action": "message",
            "why": "Bot 还醒着，夜里只想给用户留一句不要求回应的话",
            "topic": "夜里还醒着",
            "motive": "夜里一直没睡着，想短短和对方说一句",
            "conversation_posture": "closing",
            "_scheduled_ts": scheduled,
            "_proactive_source": "night_care",
            "context_key": "insomnia_night_context",
            "context": {"night_key": night_key},
        }

    def _pick_special_day_greeting_event(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        if self._private_user_role(user) != "owner" or bool(user.get("special_day_greeting_opt_out")):
            return None
        check_now = _engine_host._now_ts() if now is None else now
        current = self._environment_fromtimestamp(check_now)
        # A user's birthday owns the midnight ritual when it overlaps a
        # calendar holiday; avoid sending two competing greetings in one slot.
        if self._birthday_profile_matches_on_date(user, current) or self._birthday_profile_matches_on_date(
            user, current + timedelta(days=1)
        ):
            return None
        observance = self._special_day_observance(current)
        tomorrow = current + timedelta(days=1)
        tomorrow_observance = self._special_day_observance(tomorrow)
        current_minute = current.hour * 60 + current.minute
        if observance is None and not (tomorrow_observance and current_minute >= 21 * 60 + 30):
            return None
        target = observance or tomorrow_observance
        assert target is not None
        receipt_key = f"{target['key']}:{target['year']}"
        receipts = user.get("special_day_greeting_receipts")
        if isinstance(receipts, dict) and receipt_key in receipts:
            return None
        if observance:
            if current.hour == 0 and current.minute < 15:
                midnight_end = current.replace(hour=0, minute=15, second=0, microsecond=0).timestamp()
                remaining = int(midnight_end - check_now)
                if remaining > 10:
                    scheduled = check_now + _engine_host.random.randint(5, min(120, remaining - 5))
                    midnight = True
                else:
                    scheduled = current.replace(hour=8, minute=30, second=0, microsecond=0).timestamp() + _engine_host.random.randint(0, 35) * 60
                    midnight = False
            elif current.hour < 21:
                scheduled = check_now + _engine_host.random.randint(8, 28) * 60
                midnight = False
            else:
                return None
            date_text = current.date().isoformat()
        else:
            scheduled = datetime.combine(tomorrow.date(), datetime.min.time(), tzinfo=current.tzinfo).timestamp() + _engine_host.random.randint(1, 7) * 60
            midnight = True
            date_text = tomorrow.date().isoformat()
        return {
            "window": "00:00-00:15" if midnight else "08:30-21:30",
            "date": date_text,
            "reason": "special_day_greeting",
            "action": "message",
            "why": f"{target['title']}刚开始，想第一时间送一句有关系感的问候",
            "topic": f"{target['title']}的问候",
            "motive": f"今天是{target['title']}，想在特别的时间点先和对方说一句",
            "_scheduled_ts": scheduled,
            "_midnight_ritual": midnight,
            "_proactive_source": "special_day_ritual",
            "context_key": "planned_special_day_context",
            "context": {
                "observance_key": target["key"],
                "observance_title": target["title"],
                "observance_year": target["year"],
                "receipt_key": receipt_key,
                "delivery_timing": "midnight" if midnight else "daytime_fallback",
            },
        }

    def _pick_story_plan_event(
        self,
        now: float | None = None,
        *,
        user: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        plan = self.data.get("daily_story_plan", {})
        if not isinstance(plan, dict) or not self._is_plan_date_active(plan.get("date")):
            return None
        events = plan.get("proactive_events", [])
        if not isinstance(events, list):
            return None
        now = now or _engine_host._now_ts()
        future_events = []
        for event in events:
            if not isinstance(event, dict):
                continue
            if _single_line(event.get("lifecycle_status"), 20).lower() in {
                "cancelled", "canceled", "取消", "已取消", "expired", "skipped", "completed",
            }:
                continue
            if self._unverified_social_relay_plan_reason(
                event,
                source="event",
                has_trigger=bool(_single_line(event.get("trigger_message_id"), 120)),
            ):
                continue
            reason = str(event.get("reason") or "check_in")
            prepared, _invalid_reason = self._prepare_proactive_candidate_window(
                event,
                reason=reason,
                source="story",
                now=now,
            )
            if not isinstance(prepared, dict):
                continue
            event_ts = _safe_float(
                prepared.get("scheduled_ts"),
                self._timestamp_from_story_event(event, reason),
            )
            if event_ts > now or (
                event_ts > 0
                and now - event_ts
                <= runtime_persona_setting(self, "max_proactive_plan_lag_minutes", 180) * 60
            ):
                future_events.append((event_ts, event))
        if not future_events:
            return None
        future_events.sort(key=lambda item: item[0])
        shortlist = future_events[:6]
        weighted: list[tuple[dict[str, Any], float]] = []
        daypart_counts = self._today_proactive_daypart_counts(user or {})
        friend_user = isinstance(user, dict) and self._private_user_role(user) == "friend"
        for index, (_, event) in enumerate(shortlist):
            event_ts = self._timestamp_from_story_event(event, str(event.get("reason") or "check_in"))
            if friend_user and user is not None and self._friend_proactive_scheduled_too_early(user, event_ts):
                continue
            priority_tuple = self._event_priority(event)
            priority_score = float(-priority_tuple[0])
            weight = 1.0 + priority_score * 0.08 + max(0.0, 0.45 - index * 0.06)
            bucket = self._proactive_daypart_bucket_for_event(event)
            sent_in_bucket = _safe_int(daypart_counts.get(bucket), 0, 0) if bucket else 0
            if friend_user and bucket and sent_in_bucket >= 1:
                continue
            if bucket == "late_night" and sent_in_bucket >= 1 and not self._is_sticky_greeting_event(event):
                continue
            if bucket and sent_in_bucket >= 2 and not self._is_sticky_greeting_event(event):
                continue
            if sent_in_bucket > 0:
                weight *= max(0.22, 0.56 ** sent_in_bucket)
            if bucket == "late_night":
                weight *= 0.72
            weighted.append((event, weight))
        if not weighted and shortlist:
            for _, event in shortlist:
                if self._is_sticky_greeting_event(event):
                    weighted.append((event, 1.0))
                    break
        if not weighted:
            return None
        return self._weighted_choice(weighted)

    def _today_proactive_daypart_counts(self, user: dict[str, Any]) -> dict[str, int]:
        if not isinstance(user, dict):
            return {}
        self._reset_daily_counter_if_needed(user)
        raw = user.get("proactive_daypart_counts")
        if not isinstance(raw, dict):
            raw = {}
            user["proactive_daypart_counts"] = raw
        counts: dict[str, int] = {}
        for key, value in raw.items():
            text_key = str(key or "")
            if text_key:
                counts[text_key] = _safe_int(value, 0, 0)
        return counts

    def _proactive_daypart_bucket_for_event(self, event: dict[str, Any]) -> str:
        reason = str(event.get("reason") or "check_in")
        event_ts = self._timestamp_from_story_event(event, reason)
        if event_ts <= 0:
            start, _ = self._parse_window_minutes(str(event.get("window") or ""))
            if start is None:
                return ""
            minute = start
        else:
            when = self._environment_fromtimestamp(event_ts)
            minute = when.hour * 60 + when.minute
        return self._proactive_daypart_bucket_for_minute(minute)

    def _proactive_daypart_bucket_for_timestamp(self, timestamp: float) -> str:
        if timestamp <= 0:
            return ""
        when = self._environment_fromtimestamp(timestamp)
        return self._proactive_daypart_bucket_for_minute(when.hour * 60 + when.minute)

    def _planned_event_exceeds_daypart_cap(self, user: dict[str, Any], reason: str, scheduled_at: float) -> bool:
        if reason in {"insomnia_night", "important_date_share"}:
            return False
        if bool(self._proactive_intensity_effect("ignore_soft_daily_target", False)):
            return False
        if self._friend_proactive_scheduled_too_early(user, scheduled_at):
            return True
        bucket = self._proactive_daypart_bucket_for_timestamp(scheduled_at)
        if not bucket:
            return False
        counts = self._today_proactive_daypart_counts(user)
        sent_in_bucket = _safe_int(counts.get(bucket), 0, 0)
        if bucket == "late_night":
            return sent_in_bucket >= 1
        return sent_in_bucket >= 2

    @staticmethod
    def _proactive_daypart_bucket_for_minute(minute: int) -> str:
        if minute < 11 * 60:
            return "morning"
        if minute < 14 * 60 + 30:
            return "noon"
        if minute < 18 * 60:
            return "afternoon"
        if minute < 21 * 60:
            return "evening"
        return "late_night"

    def _note_proactive_daypart_sent(self, user: dict[str, Any], sent_at: float | None = None) -> None:
        self._reset_daily_counter_if_needed(user)
        when = self._environment_fromtimestamp(sent_at or _engine_host._now_ts())
        bucket = self._proactive_daypart_bucket_for_minute(when.hour * 60 + when.minute)
        raw = user.setdefault("proactive_daypart_counts", {})
        if not isinstance(raw, dict):
            raw = {}
            user["proactive_daypart_counts"] = raw
        raw[bucket] = _safe_int(raw.get(bucket), 0, 0) + 1
