# -*- coding: utf-8 -*-
"""ProactiveEngineEventPart03Mixin。

由 tools/split_mixin_domain.py 从 proactive_engine_event.py 机械抽取（9 个方法 + 0 个模块级名字 + 0 个类级赋值 / 293 行）。
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
from .proactive_engine_event_shared import re
from .proactive_engine_event_shared import runtime_persona_setting
from .proactive_engine_event_shared import timedelta



class ProactiveEngineEventPart03Mixin:
    """ProactiveEngineEventPart03Mixin（从 ProactiveEngineEventMixin 拆出）。"""


    def _maybe_make_followup_event(self, user: dict[str, Any], reason: str, action: str) -> dict[str, Any] | None:
        daily_limit = self._effective_user_daily_limit(user)
        if (
            not self._proactive_daily_limit_is_unlimited(daily_limit)
            and _safe_int(user.get("sent_today"), 0) >= max(0, daily_limit - 1)
        ):
            return None
        if action not in {"photo_text", "poke", "voice", "screen_peek"} and "+" not in action:
            return None
        chance = 0.12
        if "voice" in action:
            chance += 0.06
        if "photo_text" in action:
            chance += 0.05
        if "poke" in action:
            chance += 0.03
        if _engine_host.random.random() > chance:
            return None
        delay_minutes = _engine_host.random.randint(22, 95)
        follow_reason = "check_in" if action in {"poke", "screen_peek"} else "diary_share"
        topic = {
            "photo_text": "对发送的图片进行补充说明",
            "poke": "刚才戳完之后进行补充说明",
            "voice": "发完语音后的互动",
            "screen_peek": "偷看用户屏幕后的互动",
        }.get(action.split("+")[0], "刚刚那条主动后面")
        motive = {
            "photo_text": "刚才发完图以后，想和{name}聊聊",
            "poke": "刚才戳完以后，想和{name}聊聊",
            "voice": "刚才发完语音消息以后，想和{name}聊聊",
            "screen_peek": "刚才看过屏幕后，想问问{name}现在还忙不忙",
        }.get(action.split("+")[0], "刚才那条主动后面，还有一句话想补上")
        display_name = _single_line(
            user.get("nickname") or runtime_persona_setting(self, "default_nickname", "你"),
            24,
        )
        if display_name:
            motive = motive.replace("{name}", display_name)
        return {
            "date": _today_key(),
            "window": self._window_from_delay_minutes(delay_minutes, width_minutes=26),
            "reason": follow_reason,
            "action": "message",
            "why": "上一条主动消息之后进行自然的接话",
            "topic": topic,
            "motive": motive,
            "scene": "上一条主动消息发出去之后的互动",
            "tone": "自然",
            "impulse": "想接着刚才的话继续聊聊",
            "_scheduled_ts": _engine_host._now_ts() + delay_minutes * 60,
            "_origin_action": action,
            "_origin_reason": reason,
            "_cancel_on_inbound": True,
        }

    def _bot_currently_bored_for_unanswered_peek(self, user: dict[str, Any]) -> bool:
        text_parts = [
            user.get("last_proactive_reason"),
            user.get("last_proactive_action"),
            user.get("last_proactive_motive"),
            user.get("planned_proactive_reason"),
            user.get("planned_proactive_motive"),
        ]
        current_item = self._proactive_current_agenda_item()
        if isinstance(current_item, dict):
            text_parts.extend(
                [
                    current_item.get("activity"),
                    current_item.get("mood"),
                    current_item.get("message_seed"),
                ]
            )
        snapshot = self._current_story_plan_snapshot()
        if isinstance(snapshot, dict):
            text_parts.extend(snapshot.values())
        text = " ".join(_single_line(part, 80) for part in text_parts if part)
        bored_tokens = (
            "无聊", "发呆", "摸鱼", "闲", "空", "没事", "百无聊赖", "松下来",
            "喘口气", "空档", "空隙", "刷视频", "短视频", "休息",
        )
        if any(token in text for token in bored_tokens):
            return True
        reason = self._normalize_legacy_proactive_text(user.get("last_proactive_reason") or user.get("planned_proactive_reason"), limit=40)
        return reason in {"check_in", "quiet_care", "background_schedule"} and _safe_int(user.get("ignored_streak"), 0) >= 1

    def _maybe_make_unanswered_screen_peek_event(
        self,
        user: dict[str, Any],
        reason: str,
        action: str,
    ) -> dict[str, Any] | None:
        if not runtime_persona_setting(self, "enable_unanswered_screen_peek_followup", True):
            return None
        if "screen_peek" in str(action or ""):
            return None
        if not self._screen_glance_available(user, ignore_daily_limit=True):
            return None
        now = _engine_host._now_ts()
        cooldown = max(
            30,
            runtime_persona_setting(self, "unanswered_screen_peek_cooldown_minutes", 180),
        ) * 60
        last_at = _safe_float(user.get("last_unanswered_screen_peek_at"), 0)
        if last_at > 0 and now - last_at < cooldown:
            return None
        if not self._bot_currently_bored_for_unanswered_peek(user):
            return None
        delay_minutes = max(
            10,
            runtime_persona_setting(self, "unanswered_screen_peek_after_minutes", 45),
        )
        return {
            "date": _today_key(),
            "window": self._window_from_delay_minutes(delay_minutes, width_minutes=18),
            "reason": "check_in",
            "action": "screen_peek",
            "why": "上一条之后那边一直安静，想看一眼是不是还在忙。",
            "topic": "看看那边是不是还在忙",
            "motive": "那边一直安静着",
            "scene": "上一条主动消息之后的安静空档",
            "tone": "好奇",
            "impulse": "想看一眼那边是不是还在忙",
            "_scheduled_ts": now + delay_minutes * 60,
            "_cancel_on_inbound": True,
            "_unanswered_screen_peek": True,
            "_free_screen_peek": True,
            "_origin_action": action,
            "_origin_reason": reason,
        }

    def _timestamp_from_story_event(self, event: dict[str, Any], reason: str) -> float:
        scheduled_ts = _safe_float(event.get("_scheduled_ts"), 0)
        if scheduled_ts > 0:
            return scheduled_ts
        window = str(event.get("window") or "").strip()
        match = re.fullmatch(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", window)
        now_dt = self._environment_now()
        today = now_dt.date()
        if match:
            sh, sm, eh, em = [int(part) for part in match.groups()]
            start = datetime.combine(today, datetime.min.time(), tzinfo=now_dt.tzinfo).replace(hour=sh % 24, minute=sm)
            end = datetime.combine(today, datetime.min.time(), tzinfo=now_dt.tzinfo).replace(hour=eh % 24, minute=em)
            if end <= start:
                end = end + timedelta(days=1)
            if now_dt >= end:
                return 0
            earliest = max(start.timestamp(), (now_dt + timedelta(seconds=45)).timestamp())
            latest = end.timestamp()
            if earliest >= latest:
                return 0
            scheduled = _engine_host.random.uniform(earliest, latest)
            event["_scheduled_ts"] = scheduled
            return scheduled
        scheduled = self._move_timestamp_into_reason_window(_engine_host._now_ts() + _engine_host.random.uniform(2 * 3600, 10 * 3600), reason)
        event["_scheduled_ts"] = scheduled
        return scheduled

    def _reschedule_greeting_within_window(
        self,
        user: dict[str, Any],
        reason: str,
        *,
        now: float | None = None,
    ) -> bool:
        if not self._is_sticky_greeting_reason(reason):
            return False
        now_dt = self._environment_fromtimestamp(now or _engine_host._now_ts())
        windows = self._reason_windows(reason)
        if not windows:
            return False
        today = now_dt.date()
        for start, end in windows:
            start_dt = datetime.combine(today, datetime.min.time(), tzinfo=now_dt.tzinfo) + timedelta(minutes=start)
            end_dt = datetime.combine(today, datetime.min.time(), tzinfo=now_dt.tzinfo) + timedelta(minutes=end)
            if now_dt >= end_dt:
                continue
            earliest = max(now_dt + timedelta(minutes=_engine_host.random.randint(6, 14)), start_dt)
            latest = end_dt - timedelta(minutes=3)
            if earliest >= latest:
                continue
            user["next_proactive_at"] = _engine_host.random.uniform(earliest.timestamp(), latest.timestamp())
            return True
        return False

    def _pick_life_thought_topic(self, reason: str = "") -> str:
        terms = self._worldview_terms()
        if reason == "group_share":
            return f"{terms['group_chat']}里那段片段"
        if reason == "bili_video_share":
            return f"刚看到的{terms['video']}"
        if reason == "news_share":
            return "刚看到的一条新闻"
        if reason == "creative_share":
            return "刚写到的小说片段"
        current_item = self._proactive_current_agenda_item()
        activity = _single_line((current_item or {}).get("activity"), 36)
        if activity:
            return f"{activity}里自然冒出来的小内容"
        if reason == "diary_share":
            return "今天记录里想给你看看的一小段"
        return "当前时段里自然冒出来的小内容"

    def _should_use_name_only_opener(
        self,
        user: dict[str, Any],
        *,
        reason: str,
        action: str,
        motive: str,
    ) -> bool:
        if self._private_user_role(user) == "friend":
            return False
        if action != "message":
            return False
        if str(user.get("planned_followup_kind") or "") == "suspended_opener":
            return False
        chain = user.get("planned_event_chain")
        if isinstance(chain, list) and chain:
            first = chain[0] if isinstance(chain[0], dict) else {}
            if str(first.get("kind") or "") == "name_only_opener":
                return True
        if reason not in {"check_in", "quiet_care", "state_share", "evening_greeting", "insomnia_night"}:
            return False
        if _safe_float(user.get("awaiting_reply_since"), 0) > 0:
            return False
        profile = self._persona_action_profile()
        chance = 0.09
        if reason in {"quiet_care", "evening_greeting", "insomnia_night"}:
            chance += 0.05
        if profile.get("clingy"):
            chance += 0.06
        if profile.get("observant"):
            chance += 0.03
        if profile.get("playful"):
            chance += 0.02
        if any(token in motive for token in ("来找你", "确认一下用户状态", "想和用户说一句", "放心不下", "想看你在不在")):
            chance += 0.05
        if self._is_vague_seek_user_motive(reason, action, motive):
            chance *= 0.45
        return _engine_host.random.random() < min(0.32, chance)

    def _build_name_only_opener(self, name: str) -> str:
        clean_name = _single_line(name, 24) or runtime_persona_setting(
            self, "default_nickname", "你"
        )
        return f"{clean_name}……"

    def _build_suspended_proactive_payload(
        self,
        *,
        opener_text: str,
        reason: str,
        action: str,
        motive: str,
        action_summary: str,
        chain: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        profile = self._persona_action_profile()
        delay_minutes = _engine_host.random.randint(26, 95)
        complaint_chance = 0.18
        if profile.get("clingy"):
            complaint_chance += 0.16
        if profile.get("playful"):
            complaint_chance += 0.08
        if reason in {"quiet_care", "insomnia_night", "evening_greeting"}:
            complaint_chance += 0.08
        if reason == "morning_greeting":
            delay_minutes = _engine_host.random.randint(80, 150)
            complaint_chance = min(complaint_chance, 0.08)
        chain = list(chain or [])
        no_reply_step = None
        still_no_reply_step = None
        for step in chain:
            if not isinstance(step, dict):
                continue
            kind = str(step.get("kind") or "")
            if kind == "if_no_reply" and no_reply_step is None:
                no_reply_step = step
            elif kind == "if_still_no_reply" and still_no_reply_step is None:
                still_no_reply_step = step
        complaint_after_minutes = _safe_int((no_reply_step or {}).get("after_minutes"), delay_minutes, 0, 240)
        if reason == "morning_greeting":
            complaint_after_minutes = max(complaint_after_minutes, 75)
        return {
            "active": True,
            "resume_ready": False,
            "created_at": _engine_host._now_ts(),
            "opener_text": _single_line(opener_text, 60),
            "reason": reason,
            "action": action,
            "motive": self._normalize_internal_motive_text(motive),
            "summary": _single_line(action_summary, 60),
            "complaint_enabled": bool(no_reply_step) or _engine_host.random.random() < min(0.55, complaint_chance),
            "complaint_sent": False,
            "complaint_after_ts": _engine_host._now_ts() + complaint_after_minutes * 60,
            "complaint_reason": _single_line((no_reply_step or {}).get("reason"), 40),
            "complaint_topic": _single_line((no_reply_step or {}).get("topic"), 80),
            "complaint_motive": self._normalize_internal_motive_text(_single_line((no_reply_step or {}).get("motive"), 100)),
            "complaint_tone": "克制一点,把重点补上" if reason == "morning_greeting" else _single_line((no_reply_step or {}).get("tone"), 30),
            "second_followup": still_no_reply_step if isinstance(still_no_reply_step, dict) else {},
        }
