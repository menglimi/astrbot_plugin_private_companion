# -*- coding: utf-8 -*-
"""DailyStateTimerPart01Mixin。

由 tools/split_mixin_domain.py 从 daily_state_timer.py 机械抽取（21 个方法 + 0 个模块级名字 + 0 个类级赋值 / 475 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateTimerMixin）。
"""
from __future__ import annotations

from .daily_state_timer_shared import _now_ts, logger
from .daily_state_timer_shared import Any
from .daily_state_timer_shared import AstrMessageEvent
from .daily_state_timer_shared import PromptSection
from .daily_state_timer_shared import SUPPORTED_TIMER_FORMATS
from .daily_state_timer_shared import TIMER_TAG_PATTERN
from .daily_state_timer_shared import _safe_float
from .daily_state_timer_shared import _safe_int
from .daily_state_timer_shared import _single_line
from .daily_state_timer_shared import datetime
from .daily_state_timer_shared import json
from .daily_state_timer_shared import prompt_section
from .daily_state_timer_shared import re
from .daily_state_timer_shared import runtime_persona_setting



class DailyStateTimerPart01Mixin:
    """DailyStateTimerPart01Mixin（从 DailyStateTimerMixin 拆出）。"""


    def _format_timer_scheduling_prompt_section(
        self,
        user: dict[str, Any] | None = None,
    ) -> PromptSection:
        def build_section(content: str = "") -> PromptSection:
            return prompt_section(
                key="timer.scheduling",
                title="临时预约与动作回访",
                source="daily_state",
                content=content,
            )

        if not self.enable_llm_timer_scheduling:
            return build_section()
        current_user = user if isinstance(user, dict) else {}
        role = self._private_user_role(current_user) if isinstance(user, dict) else "owner"
        followup_policy = self._activity_followup_quota_policy(current_user)
        tier = _safe_int(followup_policy.get("tier"), 3, 0, 5)
        tier_label = _single_line(followup_policy.get("tier_label"), 30) or f"L{tier}"
        max_intensity = _safe_int(followup_policy.get("max_intensity"), 1, 1, 3)
        completion_buffer = _safe_int(followup_policy.get("completion_buffer_minutes"), 0, 0, 30)
        role_note = (
            "当前是主要用户；强度 3 仍须人格资料明确支持监督、黏人或查岗倾向。"
            if role == "owner"
            else "当前是次要用户；动作回访强度必须为 1，只做普通朋友式轻问候。"
        )
        timing_note = (
            f"预约时间至少应落在预计完成后约 {completion_buffer} 分钟，给用户留出自然收尾空间。"
            if completion_buffer > 0
            else "预约时间可落在预计完成附近，但不能早于预计完成时间。"
        )
        current_time = self._environment_fromtimestamp(_now_ts()).strftime("%Y-%m-%d %H:%M:%S")
        reality_consented = getattr(self, "_reality_touch_audio_consented", lambda _: False)(current_user)
        enabled_getter = getattr(self, "_reality_companion_enabled", None)
        reality_ready = bool(callable(enabled_getter) and enabled_getter() and reality_consented)
        reality_touch_rule = (
            "用户已经具备现实触及音频授权。只有用户明确要求‘用现实触及/本机音响/电脑扬声器提醒’时，"
            "不要调用 `future_task`，必须只输出：\n"
            '<timer>{"time":"YYYY-MM-DD HH:MM:SS","delivery":"reality_touch","reason":"custom_reminder","topic":"要提醒的具体事项"}</timer>\n'
            "这种标签仍会注册为 AstrBot 官方一次性 Cron，由官方任务到点调用现实触及；普通提醒不得擅自改成现实触及。"
            if reality_ready
            else "当前用户没有可用的现实触及音频授权。即使用户提到音响，也不得承诺本机播放；普通提醒仍使用官方 `future_task`。"
        )
        body = f"""
当前本地时间：{current_time}。所有 time 都必须据此换算为未来的绝对时间。
一、明确约定：用户明确要求稍后提醒/叫醒/回头说，或双方形成明确临时约定时，若本轮提供 AstrBot 官方 `future_task` 工具，优先调用该工具；只有没有官方工具可用时，才在回复末尾写：
<timer>{{"time":"YYYY-MM-DD HH:MM:SS","topic":"约定内容"}}</timer>

同一约定只能选择 `future_task` 或 `<timer>` 其中一种，绝对不能同时创建。用户明确说“便签/便笺/备忘/待办/帮我记一下/记下来”时，应使用 `pc_manage_memo`；带提醒时间的便签由便签自身提醒，不得再调用 `future_task` 或输出 `<timer>`。

现实触及交付：{reality_touch_rule}

二、动作回访：用户明确说自己暂时离开去做一个有自然结束点的具体动作（如洗澡、吃饭、拿快递、短时出门办事），即使没有主动要求提醒，也可以形成一个“忙完后想问一句”的主动念头。生成念头的同一轮必须估计合理耗时并直接预约下一次主动消息：
<timer>{{"time":"YYYY-MM-DD HH:MM:SS","reason":"activity_followup","activity":"洗澡","estimated_minutes":30,"topic":"洗完澡后问问回来了没有","motive":"记得用户刚去洗澡，估计差不多结束后想自然问一句","followup_intensity":1,"style":"轻松自然"}}</timer>

估时应结合动作和用户给出的线索：洗澡通常 20-40 分钟，吃饭通常 30-60 分钟，短途办事通常 45-120 分钟；用户给了时长或返回时间时以用户信息为准。睡觉、上班、上学、长时间学习、旅行等没有可靠结束点的动作，不得擅自估时回访，除非用户给了明确时长或要求联系。用户只说“我在忙/没空/晚点聊”是在表达边界，不是可估时动作：不要创建回访，安静等待用户回来。
当前主动配额为 L{tier}（{tier_label}）：{followup_policy.get("generation_rule")} 最大回访强度为 {max_intensity}/3；{timing_note}
强度 1 是轻轻问一句；2 可以更直接、更有存在感；3 仅限主要用户且当前人格和关系明确支持的轻度监督感。无论强度都只发一次，不得命令、指责、施压、连续追发或假装看见用户现实状态。{role_note}

改时间直接写新时间；取消普通约定时写：<timer>{{"action":"cancel"}}</timer>；取消现实触及提醒时必须保留交付类型，写：<timer>{{"action":"cancel","delivery":"reality_touch","topic":"要取消的提醒事项"}}</timer>。
        除上述动作回访外，时间和约定不明确就不要写。标签不应出现在可见回复中，只会被转写为 AstrBot 官方一次性定时计划。"""
        body = body.lstrip("\n")
        return build_section(body)

    def _extract_timer_directives(self, text: str) -> tuple[str, list[dict[str, Any]]]:
        raw_text = str(text or "")
        payloads: list[dict[str, Any]] = []
        for match in TIMER_TAG_PATTERN.finditer(raw_text):
            payload = self._parse_timer_directive(match.group(1))
            if payload:
                payloads.append(payload)
        cleaned = TIMER_TAG_PATTERN.sub("", raw_text)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned, payloads

    def _llm_response_has_official_timer_tool(self, resp: Any) -> bool:
        names = getattr(resp, "tools_call_name", None)
        if isinstance(names, str) and names.strip() == "future_task":
            return True
        if isinstance(names, (list, tuple, set)) and any(str(name).strip() == "future_task" for name in names):
            return True
        raw_completion = getattr(resp, "raw_completion", None)
        candidates = [
            getattr(raw_completion, "model_extra", None),
            getattr(raw_completion, "additional_kwargs", None),
            getattr(resp, "metadata", None),
            getattr(resp, "extra_content", None),
        ]
        for candidate in candidates:
            if not candidate:
                continue
            try:
                text = json.dumps(candidate, ensure_ascii=False)
            except Exception:
                text = str(candidate)
            if "future_task" in text:
                return True
        return False

    def _text_mentions_official_timer_created(self, text: str) -> bool:
        cleaned = _single_line(text, 500)
        if not cleaned:
            return False
        lower = cleaned.lower()
        has_explicit_job_id = bool(
            re.search(r"(?:job[_\s-]?id|任务\s*id|future task|cron job)\s*[:：#]?\s*[A-Za-z0-9_-]{6,}", cleaned, re.I)
        )
        if not has_explicit_job_id:
            return False
        if "future_task" in lower or "future task" in lower or "cron job" in lower or "cronjob" in lower:
            if any(token in lower for token in ("scheduled", "created", "job_id", "task")):
                return True
        official_markers = ("官方定时", "定时计划", "定时任务", "预约任务", "任务ID", "任务 id")
        success_markers = ("已创建", "已添加", "已登记", "已安排", "创建成功", "登记成功", "安排好了")
        return any(marker in cleaned for marker in official_markers) and any(marker in cleaned for marker in success_markers)

    def _should_skip_timer_capture_for_official_task(self, resp: Any, text: str) -> bool:
        return self._llm_response_has_official_timer_tool(resp) or self._text_mentions_official_timer_created(text)

    @staticmethod
    def _record_future_task_result(
        event: AstrMessageEvent,
        tool: Any,
        tool_args: Any,
        tool_result: Any,
    ) -> bool:
        if _single_line(getattr(tool, "name", ""), 80) != "future_task":
            return False
        action = _single_line((tool_args or {}).get("action") if isinstance(tool_args, dict) else "", 20).lower()
        success_prefixes = {
            "create": "Scheduled future task ",
            "edit": "Updated future task ",
            "delete": "Deleted cron job ",
        }
        expected_prefix = success_prefixes.get(action)
        if not expected_prefix and action != "list":
            return False
        try:
            setattr(event, "private_companion_future_task_result_observed", True)
            setattr(event, "private_companion_future_task_action", action)
        except Exception:
            return False
        if action == "list":
            return False
        if tool_result is None or bool(getattr(tool_result, "isError", False)):
            return False
        content = getattr(tool_result, "content", None)
        if not isinstance(content, list):
            return False
        result_text = "\n".join(
            str(getattr(item, "text", "") or "")
            for item in content
            if getattr(item, "text", None) is not None
        ).strip()
        if not result_text.startswith(expected_prefix):
            return False
        try:
            setattr(event, "private_companion_future_task_succeeded", True)
        except Exception:
            return False
        return True

    async def _schedule_llm_timer_after_response_dedup(
        self,
        event: AstrMessageEvent,
        resp: Any,
        user_id: str,
        payload: dict[str, Any],
        *,
        source_text: str,
        visible_text: str,
        trigger_message_id: str = "",
        trigger_umo: str = "",
    ) -> str:
        if bool(getattr(event, "private_companion_memo_reminder_saved", False)):
            logger.info(
                "跳过对话临时预约转写: 本轮已保存带提醒的便签 session=%s",
                _single_line(trigger_umo, 120) or "unknown",
            )
            return "memo_reminder"
        if bool(getattr(event, "private_companion_future_task_succeeded", False)):
            logger.info(
                "跳过对话临时预约转写: 本轮 AstrBot future_task 已执行成功 session=%s",
                _single_line(trigger_umo, 120) or "unknown",
            )
            return "official_task"
        future_task_result_observed = bool(
            getattr(event, "private_companion_future_task_result_observed", False)
        )
        if not future_task_result_observed and self._should_skip_timer_capture_for_official_task(resp, visible_text):
            logger.info(
                "跳过对话临时预约转写: 本轮疑似已由 AstrBot 官方定时计划处理 session=%s",
                _single_line(trigger_umo, 120) or "unknown",
            )
            return "official_task"
        if _single_line(payload.get("delivery"), 32).lower() == "reality_touch":
            scheduler = getattr(self, "_schedule_reality_touch_official_reminder", None)
            if not callable(scheduler):
                logger.warning("当前实例不支持现实触及官方提醒")
                return "reality_touch_unavailable"
            scheduled = await scheduler(
                user_id,
                payload,
                source_text=source_text,
                trigger_umo=trigger_umo,
            )
            return "reality_touch_official" if scheduled else "reality_touch_unavailable"
        await self._schedule_llm_timer(
            user_id,
            payload,
            source_text=source_text,
            source_origin="llm_response",
            trigger_message_id=trigger_message_id,
            trigger_umo=trigger_umo,
        )
        return "scheduled"

    def _parse_timer_directive(self, raw: str) -> dict[str, Any] | None:
        content = str(raw or "").strip()
        if not content:
            return None
        payload: dict[str, Any]
        if content.startswith("{") and content.endswith("}"):
            try:
                loaded = json.loads(content)
            except Exception:
                return None
            if not isinstance(loaded, dict):
                return None
            payload = {str(key): value for key, value in loaded.items()}
        else:
            payload = {"time": content}

        action_text = str(payload.get("action") or payload.get("operation") or "").strip().lower()
        cancel_requested = bool(payload.get("cancel")) or action_text in {"cancel", "delete", "remove", "取消", "删除", "撤销"}
        if cancel_requested:
            return {
                "cancel": True,
                "action": "cancel",
                "topic": _single_line(payload.get("topic") or payload.get("reason"), 60),
                "delivery": _single_line(payload.get("delivery"), 32).lower(),
                "reminder_id": _single_line(payload.get("reminder_id") or payload.get("id"), 40),
            }

        time_text = ""
        for key in ("time", "timer", "at", "datetime", "date"):
            candidate = payload.get(key)
            if candidate:
                time_text = str(candidate).strip()
                break
        if not time_text:
            return None
        scheduled_ts = self._parse_timer_timestamp(time_text)
        if scheduled_ts <= 0:
            return None
        parsed: dict[str, Any] = {"scheduled_ts": scheduled_ts, "raw_time": time_text}
        for key in ("reason", "topic", "motive", "action", "style", "activity"):
            value = payload.get(key)
            if value is not None:
                parsed[key] = _single_line(value, 140 if key == "motive" else 60)
        delivery = _single_line(payload.get("delivery"), 32).lower()
        if delivery == "reality_touch":
            parsed["delivery"] = delivery
            parsed["delivery_mode"] = _single_line(payload.get("delivery_mode"), 32).lower()
            parsed["playback_volume"] = _safe_int(payload.get("playback_volume"), -1, -1, 100)
            parsed["fade_in_ms"] = _safe_int(payload.get("fade_in_ms"), -1, -1, 5000)
        if _single_line(parsed.get("reason"), 40) == "activity_followup":
            parsed["estimated_minutes"] = _safe_int(payload.get("estimated_minutes"), 0, 0, 720)
            parsed["followup_intensity"] = self._normalize_activity_followup_intensity(
                payload.get("followup_intensity")
            )
        chain = self._normalize_chain_steps(payload.get("chain"))
        if chain:
            parsed["chain"] = chain
        return parsed

    def _normalize_chain_steps(self, raw_chain: Any) -> list[dict[str, Any]]:
        if not isinstance(raw_chain, list):
            return []
        normalized_chain: list[dict[str, Any]] = []
        for step in raw_chain[:4]:
            if not isinstance(step, dict):
                continue
            kind = _single_line(step.get("kind"), 32)
            if not kind:
                continue
            normalized_chain.append(
                {
                    "kind": kind,
                    "after_minutes": _safe_int(step.get("after_minutes"), 0, 0, 240),
                    "reason": _single_line(step.get("reason"), 40),
                    "topic": _single_line(step.get("topic"), 80),
                    "motive": _single_line(step.get("motive"), 100),
                    "tone": _single_line(step.get("tone"), 30),
                }
            )
        return normalized_chain

    @staticmethod
    def _normalize_activity_followup_intensity(value: Any) -> int:
        text = str(value or "").strip().lower()
        aliases = {
            "soft": 1,
            "gentle": 1,
            "轻": 1,
            "轻柔": 1,
            "normal": 2,
            "direct": 2,
            "标准": 2,
            "直接": 2,
            "firm": 3,
            "strong": 3,
            "强": 3,
            "强势": 3,
        }
        if text in aliases:
            return aliases[text]
        return _safe_int(value, 1, 1, 3)

    def _activity_followup_intensity_for_user(self, value: Any, user: dict[str, Any]) -> int:
        intensity = self._normalize_activity_followup_intensity(value)
        policy = self._activity_followup_quota_policy(user)
        return min(intensity, _safe_int(policy.get("max_intensity"), 1, 1, 3))

    def _activity_followup_quota_policy(self, user: dict[str, Any] | None) -> dict[str, Any]:
        current_user = user if isinstance(user, dict) else {}
        quota_policy: dict[str, Any] = {}
        policy_getter = getattr(self, "_proactive_quota_policy", None)
        if callable(policy_getter):
            try:
                result = policy_getter(current_user)
                if isinstance(result, dict):
                    quota_policy = result
            except Exception:
                quota_policy = {}

        tier = _safe_int(quota_policy.get("tier"), 3, 0, 5)
        tier_label = _single_line(quota_policy.get("label"), 30) or {
            0: "已关闭",
            1: "克制",
            2: "轻陪伴",
            3: "稳定陪伴",
            4: "亲密陪伴",
            5: "持续在线",
        }.get(tier, "稳定陪伴")
        tier_rules = {
            0: (1, 15, "主动消息已关闭，不应自行创建动作回访。"),
            1: (1, 15, "只在动作非常具体、短时且有明确自然终点时才创建；宁可不追问。"),
            2: (1, 8, "仅对明确的短时动作创建轻量回访，不把普通离开都变成追问。"),
            3: (2, 3, "可对明确短时动作自然回访，语气应随关系而变化。"),
            4: (2, 0, "可更积极承接明确短时动作，但仍只形成一次自然回访。"),
            5: (3, 0, "明确短时动作可优先承接为回访，但不能把每次离开都解释成需要查岗。"),
        }
        max_intensity, buffer_minutes, generation_rule = tier_rules[tier]
        role = self._private_user_role(current_user)
        ignored = _safe_int(current_user.get("ignored_streak"), 0, 0)
        if role != "owner" or ignored > 0:
            max_intensity = 1
        if ignored > 0:
            buffer_minutes = max(buffer_minutes, 10)

        if max_intensity >= 3:
            persona_text = " ".join(
                (
                    str(runtime_persona_setting(self, "schedule_persona_prompt", "") or ""),
                    str(runtime_persona_setting(self, "persona_proactive_voice_prompt", "") or ""),
                    str(current_user.get("style") or ""),
                )
            )
            strong_markers = ("查岗", "监督", "管着", "管束", "强势", "严格", "占有", "黏人", "粘人")
            if not any(marker in persona_text for marker in strong_markers):
                max_intensity = 2

        return {
            "tier": tier,
            "tier_label": tier_label,
            "max_intensity": max_intensity,
            "completion_buffer_minutes": buffer_minutes,
            "generation_rule": generation_rule,
        }

    def _parse_timer_timestamp(self, time_text: str) -> float:
        normalized = str(time_text or "").strip()
        if not normalized:
            return 0.0
        for fmt in SUPPORTED_TIMER_FORMATS:
            try:
                return datetime.strptime(normalized, fmt).timestamp()
            except ValueError:
                continue
        return 0.0

    def _infer_timer_reason(self, scheduled_ts: float, source_text: str = "") -> str:
        dt = self._environment_fromtimestamp(scheduled_ts)
        minute = dt.hour * 60 + dt.minute
        lowered = str(source_text or "")
        if 8 * 60 <= minute <= 10 * 60 + 30:
            return "morning_greeting"
        if 12 * 60 <= minute <= 13 * 60 + 50:
            return "noon_greeting"
        if 21 * 60 <= minute <= 23 * 60 + 10:
            return "evening_greeting"
        if any(token in lowered for token in ("照片", "风景", "云", "雨", "光", "晚霞", "猫")):
            return "activity_share"
        if any(token in lowered for token in ("记下来", "那句话", "写下", "日记")):
            return "diary_share"
        return "check_in"

    def _timer_default_topic(self, reason: str, user: dict[str, Any], source_text: str = "") -> str:
        source = _single_line(source_text, 48)
        if source:
            return source
        return self._choose_proactive_topic(reason, user)

    def _timer_default_motive(
        self,
        reason: str,
        user: dict[str, Any],
        *,
        source_text: str = "",
        topic: str = "",
    ) -> str:
        if topic:
            return self._normalize_internal_motive_text(f"关于“{topic}”还有一点后续内容,适合稍后补充")
        if source_text:
            return self._normalize_internal_motive_text("刚才的话题还有一点后续内容,适合稍后补充")
        return self._choose_proactive_motive(reason, user, action="message")

    def _timer_source_implies_user_unavailable(self, source_text: str, payload: dict[str, Any] | None = None) -> bool:
        text = f"{source_text or ''} {_single_line((payload or {}).get('topic'), 80)} {_single_line((payload or {}).get('motive'), 120)}"
        if not text.strip():
            return False
        rest_tokens = (
            "睡觉",
            "睡会",
            "睡一会",
            "午睡",
            "补觉",
            "休息",
            "躺会",
            "躺一会",
            "眯一会",
            "小憩",
            "闭眼",
            "一起睡",
            "一起休息",
        )
        wake_tokens = (
            "叫我",
            "叫醒",
            "喊我",
            "喊醒",
            "起床",
            "醒来",
            "准时",
            "到点",
            "提醒我",
        )
        return any(token in text for token in rest_tokens) and any(token in text for token in wake_tokens)

    def _get_active_llm_timer(self, user: dict[str, Any]) -> dict[str, Any] | None:
        raw = user.get("llm_timer_event")
        if not isinstance(raw, dict) or not raw:
            return None
        if _single_line(raw.get("backend"), 40) != "astrbot_cron":
            return None
        status = _single_line(raw.get("status"), 40)
        if status not in {"pending", "registering", "replacing", "scheduled"}:
            return None
        scheduled_ts = _safe_float(raw.get("scheduled_ts"), 0)
        if scheduled_ts <= 0:
            return None
        return raw

    def _due_internal_llm_timer_id(self, user: dict[str, Any], *, now: float | None = None) -> str:
        event = self._get_active_llm_timer(user)
        if not isinstance(event, dict) or not self._llm_timer_can_use_internal_scheduler(event):
            return ""
        check_now = _now_ts() if now is None else now
        if check_now < _safe_float(event.get("scheduled_ts"), 0):
            return ""
        return _single_line(event.get("id"), 40)

    def _has_due_llm_timer(self, user: dict[str, Any], now: float | None = None) -> bool:
        event = self._get_active_llm_timer(user)
        if not isinstance(event, dict):
            return False
        if not self._llm_timer_can_use_internal_scheduler(event):
            return False
        now = now or _now_ts()
        return now >= _safe_float(event.get("scheduled_ts"), 0)

    def _llm_timer_can_use_internal_scheduler(self, event: dict[str, Any] | None) -> bool:
        """LLM timer is now a compatibility layer; execution belongs to AstrBot cron."""
        return False
