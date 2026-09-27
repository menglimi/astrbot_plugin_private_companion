# -*- coding: utf-8 -*-
"""rest_reply。

由 tools/split_main_domain.py 从 main.py 机械抽取（8 个方法 / 257 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import random
import re
import time
from .conversation_prompt_section import PromptRenderMode, prompt_section, render_prompt_sections
from .helpers import _safe_float, _safe_int, _single_line
from .persona_config import runtime_persona_setting
from astrbot.api.event import AstrMessageEvent
from datetime import datetime
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)

class PrivateCompanionPluginRestReplyMixin:
    """rest_reply（从 PrivateCompanionPlugin 拆出）。"""

    def _rest_reply_window_active(self) -> bool:
        raw = str(runtime_persona_setting(self, 'rest_reply_active_windows', "") or "").strip()
        if not raw:
            return True
        raw = re.sub(r"\s+", "", raw)
        now_minutes = self._environment_now_minutes()
        for part in re.split(r"[,;；，、]+", raw):
            window = part.strip()
            if not window:
                continue
            start, end = self._parse_window_minutes(window)
            if start is None or end is None:
                continue
            for candidate in (now_minutes, now_minutes + 24 * 60):
                if start <= candidate < end:
                    return True
        return False

    def _rest_reply_sleep_context(self) -> tuple[bool, dict[str, Any], dict[str, Any] | None, str]:
        try:
            current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
            runtime = self._refresh_sleep_runtime_state(current_item)
        except Exception:
            current_item = None
            runtime = self._sleep_runtime_state()
        phase = str((runtime or {}).get("phase") or "")
        window_active = self._rest_reply_window_active()
        sleep_delay_active = False
        try:
            sleep_delay_active = bool(self._sleep_delay_override_state(runtime if isinstance(runtime, dict) else None))
        except Exception:
            sleep_delay_active = False
        sleepy_item = window_active and not sleep_delay_active and self._is_sleepy_plan_item(current_item) if isinstance(current_item, dict) else False
        sleeping = window_active and (phase in {"falling_asleep", "light_sleep", "sleeping_again"} or sleepy_item)
        if phase == "woken":
            sleeping = False
        if phase == "staying_up" or sleep_delay_active:
            sleeping = False
        if phase in {"natural_wake", "awake"} and not sleepy_item:
            sleeping = False
        schedule_text = self._format_plan_item_for_prompt(current_item) if isinstance(current_item, dict) else ""
        return sleeping, runtime if isinstance(runtime, dict) else {}, current_item, _single_line(schedule_text, 220)

    @staticmethod
    def _rest_reply_boundary_score(text: str) -> tuple[int, str]:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return 0, "empty"
        no_reply_boundary = r"(?:了|啦|吧|我|这(?:个|条|句|段)(?:消息|话|话题|内容|问题)?|这(?:条)?消息|本条消息|消息|哈|噢|哦|$|[，。！？,.!?])"
        if re.search(
            r"(?:不用|不必|无需|别|不要|先别|暂时别|今晚别|今天别)(?:再)?(?:回(?:复)?|理我|搭理我|接话|说话|出声)"
            + no_reply_boundary,
            compact,
        ):
            return -100, "user_asks_no_reply"
        proactive_only_quiet = bool(
            re.search(r"(?:别|不要|先别|暂时别|今晚别|今天别).{0,10}主动.{0,8}(?:打扰|吵|发消息|找我|回(?:复)?|理我|搭理我|接话|说话)", compact)
        )
        if re.search(r"你.{0,6}(没睡|没在睡|不是在睡|不是睡|不在睡|还没睡|醒着|清醒|没休息|没在休息|不是在休息|不是休息|不在休息)|别装睡|别装休息|装睡|装睡觉|明明醒着|明明没睡|明明没休息|又没睡|又没休息", compact):
            return 100, "user_corrects_not_resting"
        if re.search(r"快醒|醒醒|醒一醒|醒来|醒过来|别睡了?|别睡啦|别睡嘛|先别睡|别睡别睡|起床|起来|快起|回我一下|快回|马上回", compact):
            return 100, "explicit_wakeup_request"
        quiet_pattern = (
            r"(?:不用|不必|无需|别|不要|先别|暂时别|今晚别|今天别)(?:再)?(?:回(?:复)?|理我|搭理我|接话|说话|出声)"
            + no_reply_boundary
            + r"|(?:别|不要|先别|暂时别|今晚别|今天别).{0,10}(?:打扰|吵我|叫我|主动|发消息|找我)"
            r"|(?:安静点|闭嘴|别说话|不要说话|别醒|继续睡)"
        )
        if not proactive_only_quiet and re.search(quiet_pattern, compact):
            return -100, "user_asks_quiet"
        if re.search(r"(?:晚安|好梦|早点睡|早点休息|睡个好觉)", compact):
            return 100, "goodnight_ack"
        if proactive_only_quiet:
            return 0, "user_asks_no_proactive"
        if re.search(r"救命|出事|急|紧急|重要|不舒服|难受|害怕|崩溃|报警|医院|摔|痛", compact):
            return 100, "urgent_or_explicit_wakeup"
        if re.search(r"醒了吗|睡了吗|在吗|能不能回|可以回吗|想你|陪我|听我说|还睡吗|还在睡吗", compact):
            return 72, "soft_wakeup_request"
        return 0, "normal"

    async def _rest_reply_llm_score(
        self,
        *,
        text: str,
        schedule_text: str,
        runtime: dict[str, Any],
        is_private_chat: bool,
    ) -> tuple[int, str]:
        section = prompt_section(
            key="background.rest_wakeup_judge",
            title="休息中唤醒判断",
            source="main",
            template=(
                "你是一个睡眠/休息中是否需要醒来回复的判定器。请只输出 JSON。\n\n"
                "背景：\n"
                "- Bot 当前日程处于睡眠、午休或休息段。\n"
                "- 当前睡眠阶段：{sleep_phase}。\n"
                "- 当前日程：{schedule}。\n"
                "- 会话类型：{conversation_type}。\n\n"
                "判断原则：\n"
                "- 只有用户明显需要回应、明确叫醒、情绪/安全/紧急需要支持，或继续不回复会显得很不合适时，才建议醒来。\n"
                "- 普通闲聊、表情、无明确对象的群聊、轻微玩笑、可等到醒来再说的内容，应保持睡眠不回复。\n"
                "- 如果用户明确说不要打扰、别回、继续睡，必须不回复。\n\n"
                "用户消息：\n{message}\n\n"
                "只输出 JSON：\n"
                '{{"score": 0-100, "should_reply": true/false, "reason": "一句话原因"}}'
            ),
            variables={
                "sleep_phase": _single_line(runtime.get("label") or runtime.get("phase"), 40) or "未知",
                "schedule": schedule_text or "未知",
                "conversation_type": "私聊" if is_private_chat else "群聊",
                "message": _single_line(text, 800),
            },
        )
        prompt = render_prompt_sections([section], mode=PromptRenderMode.BODY_ONLY)
        raw = await self._llm_call(
            prompt,
            max_tokens=180,
            provider_id=self._task_provider(
                runtime_persona_setting(self, "rest_wakeup_provider_id", ""),
                runtime_persona_setting(self, "response_review_provider_id", ""),
                runtime_persona_setting(self, "llm_provider_id", ""),
            ),
            task="rest_wakeup_judge",
        )
        payload = self._extract_json_payload(raw or "")
        if not isinstance(payload, dict):
            return 0, "llm_invalid"
        try:
            score = max(0, min(100, int(float(payload.get("score", 0)))))
        except (TypeError, ValueError):
            score = 0
        should_reply = bool(payload.get("should_reply"))
        reason = _single_line(payload.get("reason"), 80) or "llm"
        if should_reply and score < runtime_persona_setting(self, 'rest_reply_llm_threshold', 65):
            score = runtime_persona_setting(self, 'rest_reply_llm_threshold', 65)
        return score, reason

    async def _should_reply_during_rest(self, event: AstrMessageEvent, *, is_private_chat: bool) -> tuple[bool, str]:
        if not runtime_persona_setting(self, 'enable_rest_reply_simulation', False):
            return True, "disabled"
        sleeping, runtime, _current_item, schedule_text = self._rest_reply_sleep_context()
        if not sleeping:
            return True, "not_sleeping"
        text = _single_line(getattr(event, "message_str", ""), 800)
        boundary_score, boundary_reason = self._rest_reply_boundary_score(text)
        if boundary_score < 0:
            return False, boundary_reason
        if boundary_score >= max(1, runtime_persona_setting(self, 'rest_reply_llm_threshold', 65)):
            try:
                self._mark_sleep_woken_by_user(text)
            except Exception:
                pass
            return True, boundary_reason
        mode = runtime_persona_setting(self, 'rest_reply_mode', "probability")
        if mode == "llm":
            score, reason = await self._rest_reply_llm_score(
                text=text,
                schedule_text=schedule_text,
                runtime=runtime,
                is_private_chat=is_private_chat,
            )
            allowed = score >= runtime_persona_setting(self, 'rest_reply_llm_threshold', 65)
            if allowed:
                try:
                    self._mark_sleep_woken_by_user(text)
                except Exception:
                    pass
            return allowed, f"llm:{score}/{runtime_persona_setting(self, 'rest_reply_llm_threshold', 65)}:{reason}"
        probability = max(0.0, min(1.0, float(runtime_persona_setting(self, 'rest_reply_probability', 0.0) or 0.0)))
        hit = random.random() <= probability
        if hit:
            try:
                self._mark_sleep_woken_by_user(text)
            except Exception:
                pass
        return hit, f"probability:{probability:.2f}"

    def _rest_backlog_user_for_event(self, event: AstrMessageEvent) -> tuple[str, dict[str, Any] | None]:
        try:
            if not bool(getattr(event, "is_private_chat", lambda: False)()):
                return "", None
        except Exception:
            return "", None
        try:
            resolver = getattr(self, "_private_user_id_for_event", None)
            user_id = (
                resolver(event)
                if callable(resolver)
                else self._canonical_private_user_id(str(event.get_sender_id()))
            )
        except Exception:
            return "", None
        users = self.data.get("users", {})
        user = users.get(user_id) if isinstance(users, dict) else None
        if not isinstance(user, dict):
            return user_id, None
        if not self._private_passive_profile_available(user_id, user):
            return user_id, None
        return user_id, user

    def _record_rest_reply_backlog(self, event: AstrMessageEvent, reason: str) -> None:
        if not bool(runtime_persona_setting(self, 'enable_rest_backlog_reply', True)):
            return
        user_id, user = self._rest_backlog_user_for_event(event)
        if not isinstance(user, dict):
            return
        text = _single_line(getattr(event, "message_str", ""), 240)
        if not text:
            text = "发来了一条非文本消息"
        backlog = user.get("rest_reply_backlog")
        if not isinstance(backlog, list):
            backlog = []
        now = time.time()
        backlog.append(
            {
                "ts": now,
                "text": text,
                "reason": _single_line(reason, 80),
            }
        )
        max_items = max(1, _safe_int(runtime_persona_setting(self, 'rest_backlog_max_messages', 4), 4, 1))
        user["rest_reply_backlog"] = backlog[-max_items:]
        user["rest_reply_backlog_updated_at"] = now
        self._schedule_data_save(sections={"users"})
        logger.info(
            "已记录休息中未回复私聊: user=%s count=%s reason=%s text=%s",
            user_id,
            len(user["rest_reply_backlog"]),
            _single_line(reason, 80),
            _single_line(text, 80),
        )

    def _take_rest_reply_backlog_prompt(self, user: dict[str, Any]) -> str:
        if not bool(runtime_persona_setting(self, 'enable_rest_backlog_reply', True)):
            return ""
        backlog = user.get("rest_reply_backlog")
        if not isinstance(backlog, list) or not backlog:
            return ""
        max_items = max(1, _safe_int(runtime_persona_setting(self, 'rest_backlog_max_messages', 4), 4, 1))
        items = [item for item in backlog[-max_items:] if isinstance(item, dict)]
        if not items:
            user["rest_reply_backlog"] = []
            user["rest_reply_backlog_updated_at"] = 0
            self._schedule_data_save(sections={"users"})
            return ""
        lines: list[str] = []
        for idx, item in enumerate(items, 1):
            ts = _safe_float(item.get("ts"), 0)
            if ts > 0:
                try:
                    when = self._environment_fromtimestamp(ts).strftime("%H:%M")
                except Exception:
                    when = datetime.fromtimestamp(ts).strftime("%H:%M")
            else:
                when = "刚才"
            text = _single_line(item.get("text"), 180) or "发来了一条消息"
            lines.append(f"{idx}. {when}｜{text}")
        user["rest_reply_backlog"] = []
        user["rest_reply_backlog_updated_at"] = 0
        self._schedule_data_save(sections={"users"})
        if not lines:
            return ""
        return "休息时有几条私聊没来得及回，醒来后补看到：\n" + "\n".join(lines)
