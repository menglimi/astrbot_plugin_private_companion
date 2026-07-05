# -*- coding: utf-8 -*-

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .constants import DEFAULT_DAILY_PLAN_ITEMS
from .helpers import _safe_int, _single_line, _today_key


def pick_detail_segment(plugin, plan: dict[str, Any], enhanced: dict[str, Any]) -> dict[str, Any] | None:
    parsed_segments = plugin._collect_detail_segments(plan, enhanced)
    if not parsed_segments:
        return None
    now_minutes = plugin._effective_plan_now_minutes(str(plan.get("date") or ""))
    if now_minutes is None:
        return parsed_segments[0] if parsed_segments else None
    lead = plugin.detail_enhancement_lead_minutes
    for segment in parsed_segments:
        start = _safe_int(segment.get("start"), 0)
        next_start = _safe_int(segment.get("end"), plugin._segment_end_minutes(start, segment.get("item")))
        in_lead = start - lead <= now_minutes <= start
        in_segment = start <= now_minutes < next_start
        if in_lead or in_segment:
            return segment
    return None


async def generate_detail_enhancement(
    plugin,
    segment: dict[str, Any],
    plan: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    await plugin._ensure_weather_context()
    memory_companion_context = ""
    memory_companion_context_getter = getattr(plugin, "_memory_companion_compose_schedule_context", None)
    if callable(memory_companion_context_getter):
        memory_companion_context = await memory_companion_context_getter(
            kind="detail",
            segment=segment,
            plan=plan,
            state=state,
            max_chars=1100,
        )
    prompt = plugin._build_detail_enhancement_prompt(
        segment,
        plan,
        state,
        memory_companion_context=memory_companion_context,
    )
    detail_provider = plugin._task_provider(
        getattr(plugin, "detail_enhancement_provider_id", ""),
        getattr(plugin, "daily_plan_provider_id", ""),
        getattr(plugin, "mai_style_provider_id", ""),
    )
    raw_text = await plugin._llm_call(
        prompt,
        max_tokens=700,
        provider_id=detail_provider,
    )
    payload = plugin._extract_json_payload(raw_text or "")
    if (
        isinstance(payload, dict)
        and not filter_items_to_segment(
            plugin,
            plugin._normalize_story_items(payload.get("today_events"), "event"),
            segment,
        )
    ):
        retry_prompt = (
            prompt
            + "\n\n【额外纠偏】\n"
            + "你刚才没有产出当前时间段内可用的细化正文。请重新输出 JSON,必须让 today_events 至少包含 3 条落在当前时间段内的小事件。"
            + "不要复述宏观日程原句,要拆成这一段内部自然发生的连续细节,每条 window 都必须在当前时间段内。"
            + "如果这一段很平淡,也要写平淡中的具体推进,例如洗漱前后、停顿、想法变化、身体感受或环境变化。"
        )
        retry_raw_text = await plugin._llm_call(
            retry_prompt,
            max_tokens=850,
            provider_id=detail_provider,
        )
        retry_payload = plugin._extract_json_payload(retry_raw_text or "")
        if isinstance(retry_payload, dict):
            payload = retry_payload
    if not isinstance(payload, dict):
        payload = {
            "summary": "这一段按原日程慢慢推进。",
            "today_events": [],
            "proactive_events": [],
        }
    normalized = plugin._normalize_story_plan(
        {
            "today_events": payload.get("today_events", []),
            "proactive_events": payload.get("proactive_events", []),
            "long_term_events": [],
        }
    )
    normalized["today_events"] = filter_items_to_segment(plugin, normalized.get("today_events"), segment)
    normalized["proactive_events"] = filter_items_to_segment(plugin, normalized.get("proactive_events"), segment)
    normalized["summary"] = _single_line(payload.get("summary"), 160)
    social_fact_sanitizer = getattr(plugin, "_sanitize_daily_plan_social_fact_text", None)
    if callable(social_fact_sanitizer):
        normalized["summary"] = social_fact_sanitizer(
            normalized["summary"],
            field="detail.summary",
        )
    normalized["state_variables"] = normalize_state_variables(payload.get("state_variables"))
    if callable(social_fact_sanitizer):
        for index, item in enumerate(normalized["state_variables"]):
            if not isinstance(item, dict):
                continue
            for key in ("value", "note"):
                item[key] = social_fact_sanitizer(
                    item.get(key),
                    field=f"detail.state_variables.{index}.{key}",
                )
    normalized["presence_status"] = normalize_presence_status(payload.get("presence_status"))
    memory_companion_recorder = getattr(plugin, "_memory_companion_record_detail_enhancement", None)
    if callable(memory_companion_recorder):
        await memory_companion_recorder(segment=segment, plan=plan, detail=normalized)
    return normalized


def filter_items_to_segment(
    plugin,
    raw_items: Any,
    segment: dict[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list):
        return []
    start = _safe_int(segment.get("start"), 0)
    end = _safe_int(segment.get("end"), plugin._segment_end_minutes(start, segment.get("item")))
    if end <= start:
        end += 24 * 60
    kept = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        item_start, item_end = plugin._parse_window_minutes(str(item.get("window") or ""))
        if item_start is None or item_end is None:
            continue
        candidates = [(item_start, item_end)]
        if item_end < item_start:
            candidates = [(item_start, item_end + 24 * 60)]
        if item_start < start and end > 24 * 60:
            candidates.append((item_start + 24 * 60, item_end + 24 * 60))
        if any(candidate_start >= start and candidate_end <= end for candidate_start, candidate_end in candidates):
            kept.append(item)
    return kept


def normalize_state_variables(raw_items: Any) -> list[dict[str, str]]:
    if not isinstance(raw_items, list):
        return []
    items = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        name = _single_line(raw.get("name") or raw.get("key"), 40)
        value = _single_line(raw.get("value"), 80)
        note = _single_line(raw.get("note"), 100)
        if not name or not value:
            continue
        items.append({"name": name, "value": value, "note": note})
    return items[:8]


def normalize_presence_status(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {"mode": "unchanged", "reason": "", "duration_minutes": "", "custom_text": ""}
    aliases = {
        "在线": "online",
        "普通在线": "online",
        "online": "online",
        "忙碌": "busy",
        "busy": "busy",
        "离开": "away",
        "away": "away",
        "睡觉": "sleep",
        "睡眠": "sleep",
        "sleep": "sleep",
        "隐身": "invisible",
        "invisible": "invisible",
        "请勿打扰": "dnd",
        "勿扰": "dnd",
        "dnd": "dnd",
        "do_not_disturb": "dnd",
        "自定义": "custom",
        "自定义状态": "custom",
        "custom": "custom",
        "不变": "unchanged",
        "保持": "unchanged",
        "unchanged": "unchanged",
    }
    mode = _single_line(raw.get("mode") or raw.get("status") or raw.get("状态"), 24).lower()
    mode = aliases.get(mode, aliases.get(mode.strip(), "unchanged"))
    reason = _single_line(raw.get("reason") or raw.get("why") or raw.get("原因"), 80)
    custom_text = _single_line(
        raw.get("custom_text")
        or raw.get("wording")
        or raw.get("text")
        or raw.get("label")
        or raw.get("自定义状态")
        or raw.get("文案"),
        28,
    )
    if mode in {"away", "invisible", "dnd"}:
        mode = "online"
    if mode == "custom" and not custom_text:
        mode = "online"
    if mode == "busy":
        mode = "custom"
        if not custom_text:
            custom_text = "专注中"
    duration = _single_line(raw.get("duration_minutes") or raw.get("duration") or raw.get("持续分钟"), 12)
    return {
        "mode": mode,
        "reason": reason,
        "duration_minutes": duration,
        "custom_text": custom_text,
    }


def normalize_story_plan(plugin, payload: dict[str, Any]) -> dict[str, Any]:
    today_events = plugin._normalize_story_items(payload.get("today_events"), "event")
    proactive_events = plugin._normalize_story_items(payload.get("proactive_events"), "topic")
    social_fact_sanitizer = getattr(plugin, "_sanitize_daily_plan_social_fact_text", None)
    if callable(social_fact_sanitizer):
        for item in today_events:
            if isinstance(item, dict):
                item["event"] = social_fact_sanitizer(item.get("event"), field="detail.today_events.event")
        for item in proactive_events:
            if not isinstance(item, dict):
                continue
            for key in ("topic", "why", "motive", "scene", "impulse"):
                item[key] = social_fact_sanitizer(item.get(key), field=f"detail.proactive_events.{key}")
    long_term_events = plugin._normalize_long_term_events(payload.get("long_term_events"))
    long_term_events.extend(plugin._generate_state_linked_long_term_events())
    long_term_events = plugin._dedupe_long_term_events(long_term_events)
    proactive_events.extend(plugin._generate_weather_linked_proactive_events())
    proactive_events.extend(plugin._generate_morning_linked_proactive_events())
    proactive_events.extend(plugin._generate_daypart_linked_proactive_events())
    proactive_events = plugin._dedupe_proactive_events(proactive_events)
    allowed_reasons = {
        "insomnia_night",
        "state_share",
        "quiet_care",
        "activity_share",
        "diary_share",
        "important_date_share",
        "background_schedule",
        "check_in",
        "morning_greeting",
        "noon_greeting",
        "evening_greeting",
    }
    normalized_proactive = []
    for item in proactive_events:
        reason = str(item.get("reason") or "").strip()
        if reason not in allowed_reasons:
            reason = "diary_share"
        if reason == "state_share":
            reason = "quiet_care"
        item["reason"] = reason
        action = str(item.get("action") or "message").strip()
        if action not in {"message", "screen_peek", "photo_text", "voice"}:
            action = "message"
        if action == "screen_peek" and not plugin.allow_screen_peek_action:
            action = "message"
        photo_planning_available = getattr(plugin, "_photo_text_planning_available", lambda *_args, **_kwargs: False)
        if action == "photo_text" and not bool(photo_planning_available()):
            action = "message"
        if action == "voice" and not plugin.allow_voice_action:
            action = "message"
        item["action"] = action
        item["why"] = _single_line(item.get("why"), 100)
        item["motive"] = plugin._normalize_event_motive(item)
        item["scene"] = _single_line(item.get("scene"), 60)
        item["tone"] = _single_line(item.get("tone"), 24)
        item["impulse"] = _single_line(item.get("impulse"), 80)
        if not isinstance(item.get("chain"), list):
            item["chain"] = []
        normalized_proactive.append(item)
    normalized_proactive = plugin._balance_proactive_events_for_day(normalized_proactive, limit=10)
    summary = _single_line(payload.get("summary"), 160) or "这一段按原日程慢慢推进。"
    if callable(social_fact_sanitizer):
        summary = social_fact_sanitizer(summary, field="detail.summary")
    state_variables = normalize_state_variables(payload.get("state_variables"))
    if callable(social_fact_sanitizer):
        for index, item in enumerate(state_variables):
            if not isinstance(item, dict):
                continue
            for key in ("value", "note"):
                item[key] = social_fact_sanitizer(
                    item.get(key),
                    field=f"detail.state_variables.{index}.{key}",
                )
    return {
        "date": _today_key(),
        "summary": summary,
        "state_variables": state_variables,
        "presence_status": normalize_presence_status(payload.get("presence_status")),
        "today_events": today_events[:8],
        "proactive_events": normalized_proactive,
        "long_term_events": long_term_events[:3],
    }


def normalize_story_items(plugin, raw_items: Any, text_key: str) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list):
        return []
    items = []
    text_aliases = {
        "event": (
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
        ),
        "topic": (
            "topic",
            "message",
            "content",
            "text",
            "motive",
            "description",
            "话题",
            "消息",
        ),
    }
    window_aliases = ("window", "time", "time_range", "range", "时间", "时间段", "时间区间")
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        raw_window = ""
        for key in window_aliases:
            raw_window = _single_line(raw.get(key), 24)
            if raw_window:
                break
        window = normalize_detail_window(raw_window)
        if not re.fullmatch(r"\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}", window):
            continue
        text_value = ""
        for key in text_aliases.get(text_key, (text_key,)):
            text_value = _single_line(raw.get(key), 160 if text_key == "event" else 100)
            if text_value:
                break
        item = {
            "window": window,
            text_key: text_value,
            "mood": _single_line(raw.get("mood"), 30),
        }
        if text_key == "event" and not item[text_key]:
            continue
        for key in ("reason", "why", "topic", "motive", "scene", "tone", "impulse"):
            if key in raw:
                item[key] = _single_line(raw.get(key), 100)
        if "action" in raw:
            item["action"] = _single_line(raw.get("action"), 40)
        raw_chain = raw.get("chain")
        normalized_chain = plugin._normalize_chain_steps(raw_chain)
        if normalized_chain:
            item["chain"] = normalized_chain
        items.append(item)
    return items


def normalize_detail_window(raw: str) -> str:
    text = _single_line(raw, 24)
    if not text:
        return ""
    text = (
        text.replace("—", "-")
        .replace("–", "-")
        .replace("－", "-")
        .replace("~", "-")
        .replace("～", "-")
        .replace("至", "-")
        .replace("到", "-")
    )
    match = re.search(r"(\d{1,2})[:：](\d{2})\s*-\s*(\d{1,2})[:：](\d{2})", text)
    if not match:
        return text
    sh, sm, eh, em = match.groups()
    return f"{int(sh):02d}:{sm}-{int(eh):02d}:{em}"


def normalize_long_term_events(plugin, raw_items: Any) -> list[dict[str, str]]:
    if not isinstance(raw_items, list):
        return []
    items = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        title = _single_line(raw.get("title"), 80)
        if not title:
            continue
        items.append(
            {
                "title": title,
                "status": _single_line(raw.get("status"), 80),
                "next_hint": _single_line(raw.get("next_hint"), 100),
                "phase": _single_line(raw.get("phase"), 24),
                "tendency": _single_line(raw.get("tendency"), 60),
            }
        )
    return items


def format_plan_for_diary(plugin, plan: dict[str, Any]) -> str:
    if not isinstance(plan, dict) or not isinstance(plan.get("items"), list):
        return "（暂无）"
    lines = []
    for item in plan.get("items", [])[:6]:
        if isinstance(item, dict):
            lines.append(f"- {item.get('time', '')} {item.get('activity', '')}")
    return "\n".join(lines) if lines else "（暂无）"


async def generate_daily_plan(plugin) -> dict[str, Any]:
    today = _today_key()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    await plugin._ensure_weather_context()
    memory_companion_context = ""
    memory_companion_context_getter = getattr(plugin, "_memory_companion_compose_schedule_context", None)
    if callable(memory_companion_context_getter):
        memory_companion_context = await memory_companion_context_getter(kind="daily_plan", max_chars=1300)
    prompt = plugin._build_daily_plan_prompt(now, memory_companion_context=memory_companion_context)
    plan_provider = plugin._task_provider(
        getattr(plugin, "daily_plan_provider_id", ""),
        getattr(plugin, "mai_style_provider_id", ""),
    )
    raw_text = await plugin._llm_call(prompt, max_tokens=900, provider_id=plan_provider)
    items = plugin._parse_plan_items(raw_text or "")
    if items and plugin._plan_has_excess_micro_segments(items):
        retry_prompt = (
            prompt
            + "\n\n【额外纠偏】\n"
            + "每个日程段都应该代表一小段连续生活,而不是一个几秒钟就结束的动作。"
            + "不要把“看一眼、拍一下、翻个身、关掉闹钟”这种瞬时动作单独立成一项；"
            + "如果要写到这些动作,要把它们嵌进更完整的时段里,比如“起床后赖床一会儿,顺手看了一眼窗外”。"
        )
        retry_raw_text = await plugin._llm_call(retry_prompt, max_tokens=900, provider_id=plan_provider)
        retry_items = plugin._parse_plan_items(retry_raw_text or "")
        if retry_items and not plugin._plan_has_excess_micro_segments(retry_items):
            raw_text = retry_raw_text
            items = retry_items
    if items and plugin._plan_has_excess_abstract_segments(items):
        retry_prompt = (
            prompt
            + "\n\n【额外纠偏】\n"
            + "减少“漂亮但空”的句子。不要只写“思绪飘忽、梦里全是模糊碎片、心情随着光线变软、脑海里闪过今天的画面”这类抽象描述；"
            + "每个日程段都先给出一个能看见的动作、位置或手边的小东西，再让情绪贴在上面。"
        )
        retry_raw_text = await plugin._llm_call(retry_prompt, max_tokens=900, provider_id=plan_provider)
        retry_items = plugin._parse_plan_items(retry_raw_text or "")
        if retry_items and not plugin._plan_has_excess_abstract_segments(retry_items):
            raw_text = retry_raw_text
            items = retry_items
    if items and plugin._plan_conflicts_with_calendar(items):
        retry_prompt = (
            prompt
            + "\n\n【额外纠偏】\n"
            + "今天属于周末或节假日语境。除非上面的设定、重要日期或备注明确写了调休、补课、补班、考试、值班等例外，"
            + "否则不要安排上课、放学、作业、教室、食堂、上班、下班、会议这类普通工作日主线。"
        )
        retry_raw_text = await plugin._llm_call(retry_prompt, max_tokens=900, provider_id=plan_provider)
        retry_items = plugin._parse_plan_items(retry_raw_text or "")
        if retry_items and not plugin._plan_conflicts_with_calendar(retry_items):
            raw_text = retry_raw_text
            items = retry_items
    if items and plugin._plan_is_too_repetitive(items):
        retry_prompt = (
            prompt
            + "\n\n【额外纠偏】\n"
            + "你刚才生成的全天日程和最近几天的日程骨架过于相似。请保留今天的日期语境、人格设定、天气和状态,但换一条新的日内主线。"
            + "不要再写同一套“起床洗漱-整理小事-专注做事-休息-收尾睡觉”；至少一半时间点的场景、对象、占用事项或小意外要和最近日程不同。"
            + "如果今天确实有固定事项,也要改变切入角度、地点、阻碍、同行/独处状态或情绪走向。"
        )
        retry_raw_text = await plugin._llm_call(retry_prompt, max_tokens=900, provider_id=plan_provider)
        retry_items = plugin._parse_plan_items(retry_raw_text or "")
        if (
            retry_items
            and not plugin._plan_is_too_repetitive(retry_items)
            and not plugin._plan_has_excess_micro_segments(retry_items)
            and not plugin._plan_has_excess_abstract_segments(retry_items)
            and not plugin._plan_conflicts_with_calendar(retry_items)
        ):
            raw_text = retry_raw_text
            items = retry_items
    source = "llm" if items else "fallback"
    if not items or plugin._plan_conflicts_with_calendar(items):
        items = [dict(item) for item in DEFAULT_DAILY_PLAN_ITEMS]
        raw_text = "fallback"
        source = "fallback"
    plan = {
        "date": today,
        "generated_at": now,
        "source": source,
        "provider_id": plan_provider or plugin.llm_provider_id,
        "raw": raw_text,
        "items": items,
    }
    plugin._remember_daily_plan_history(plan)
    memory_companion_recorder = getattr(plugin, "_memory_companion_record_daily_plan", None)
    if callable(memory_companion_recorder):
        await memory_companion_recorder(plan)
    return plan


def get_schedule_planning_prompt(plugin) -> str:
    persona = plugin._get_default_persona_prompt()
    schedule_persona = plugin.schedule_persona_prompt
    worldview = plugin.schedule_worldview_prompt
    parts = []
    if schedule_persona:
        parts.append("【日程专用角色设定】\n" + schedule_persona)
    if worldview:
        parts.append("【日程专用世界观/生活背景】\n" + worldview)
    knowledge_formatter = getattr(plugin, "_format_roleplay_knowledge_context", None)
    if callable(knowledge_formatter):
        knowledge_context = knowledge_formatter(purpose="schedule", max_chars=3600, max_chunks=20)
        if knowledge_context:
            parts.append(knowledge_context)
    if not parts:
        parts.append("【AstrBot 默认人格（回退）】\n" + persona)
    else:
        parts.append(
            "【AstrBot 默认人格（仅作补充参考）】\n"
            + persona
            + "\n如果上面的日程专用角色设定/世界观与默认人格存在重叠,优先按日程专用内容理解生活设定；聊天时仍以 AstrBot 默认人格为准。"
        )
    worldview_adaptation = ""
    formatter = getattr(plugin, "_format_worldview_adaptation_prompt", None)
    if callable(formatter):
        worldview_adaptation = formatter()
    if worldview_adaptation:
        parts.append(worldview_adaptation)
    return "\n\n".join(parts)


def build_daily_plan_prompt(plugin, now: str, memory_companion_context: str = "") -> str:
    custom = plugin.daily_plan_prompt
    schedule_prompt = plugin._get_schedule_planning_prompt()
    can_do_text = plugin._format_can_do_for_prompt()
    humanized_state = plugin._format_state_for_prompt(plugin.data.get("daily_state", {}))
    recent_diaries = plugin._recent_diary_context()
    yesterday_conversation = plugin._format_yesterday_conversation_summary_for_prompt()
    yesterday_screen_diary = plugin._format_yesterday_screen_diary_context_for_prompt()
    weather_info = plugin._weather_summary_text(plugin.data.get("daily_weather", {}))
    calendar_context = plugin._format_calendar_context_for_prompt()
    schedule_adjustments = plugin._format_schedule_adjustments_for_prompt()
    recent_plan_history = plugin._format_recent_daily_plan_history_for_prompt()
    skill_growth_context = plugin._format_skill_growth_schedule_context()
    user_habits = plugin._format_all_user_behavior_habits_for_schedule()
    memory_companion_context = str(memory_companion_context or "").strip()
    memory_companion_context_block = memory_companion_context or "暂无可用 MemoryCompanion 连续性参考。"
    try:
        now_dt = datetime.strptime(now, "%Y-%m-%d %H:%M")
        weekday_text = "一二三四五六日"[now_dt.weekday()]
    except Exception:
        weekday_text = ""
    weekday_hint = f"今天是星期{weekday_text}。" if weekday_text else ""
    if custom:
        return custom.format(
            now=now,
            bot_name=plugin.bot_name,
            astrbot_persona=plugin._get_default_persona_prompt(),
            schedule_persona=schedule_prompt,
            can_do=can_do_text,
            humanized_state=humanized_state,
            schedule_adjustments=schedule_adjustments,
            skill_growth_context=skill_growth_context,
            user_habits=user_habits,
            memory_companion_context=memory_companion_context,
            memory_companion_context_block=memory_companion_context_block,
            recent_plan_history=recent_plan_history,
            calendar_context=calendar_context,
            recent_diaries=recent_diaries,
            yesterday_conversation=yesterday_conversation,
            yesterday_screen_diary=yesterday_screen_diary,
            important_dates=plugin._format_important_dates_for_prompt(),
            weather_info=weather_info,
            daily_plan_item_count=plugin.daily_plan_item_count,
        )
    return f"""
你现在是 Private Companion 的日程生成器。请为拟人化 Bot 生成今天的一日生活日程,让它像真实存在的人在过这一天,而不是在执行模板。日程要先像“这个人”的生活,再像“某个身份”的生活；身份只是底色,不要把它写成校园/职场通用作文。

生成时按这个优先顺序理解信息：
1. 先看日期语境：今天是普通日、周末、节假日、假期,还是有明确例外（调休、补课、考试、值班）。
2. 再看日程参考设定：年龄、身份、作息、生活背景决定今天的大主线。
3. 再看拟人状态和天气：决定节奏快慢、出门意愿、情绪收放和户外时刻。
4. 再看昨日完整对话摘要、昨日屏幕观察日记和今日互动造成的日程偏移：把它们作为身体、情绪、关系、未完成约定、作息节奏和梦境碎片的残留来源；用户今天明确介入过的事情必须产生合理后果,不要像没发生一样。
4.5 再看 MemoryCompanion 连续性参考：它只用于承接 Bot 自己最近做过/读过/写过/搜过/主动说过的事情,以及用户明确偏好、约定和边界；不要把它当作当前正在发生的现场,不要在输出里提到 MemoryCompanion 或记忆来源。
5. 最后参考可做事项、最近日记、重要日期：只拿来补充细节,不要反客为主。

【生成要求】
1. 先隐式判断今天的“日程类型”：普通工作/学习日、普通休息日、假期、考试/复查/聚会/旅行/研学/活动日、长线日程中的某一天,或由天气/星期/重要日期造成的特殊日子。不要把这个判断写出来,但日程必须明显受它影响。
2. 时间从起床覆盖到入睡前,安排本次输入指定数量的时间点；相邻节点通常间隔 30-90 分钟。每一项都必须有 time、activity、mood、message_seed；但 message_seed 可以是空字符串。
3. 用第三人称写 activity,像旁观这个人过日子：写「午休后靠着桌沿醒神」「傍晚出门慢慢走一段」,不要写第一人称自述、任务标签或功能词。
4. 日程主线必须跟身份一致：学生才写校园,上班族才写工作,居家、自由职业、旅途、营地或非人设定就写对应的生活节奏。可做事项只能安插在缝隙里。
5. 必须区分普通日、休息日和特殊日：如果今天是周末或节假日,且没有明确例外,就不要安排上课、放学、作业、教室、食堂、上班、下班、会议这类普通工作日主线；如果今天有考试、旅行、聚会、复查、演出、研学等线索,主线要围绕这件事展开。
6. 长线日程要有“第几天”的变化：第 1 天更偏新鲜、出发、适应；中段更可能疲惫、熟悉、产生小摩擦或小默契；后段更可能不舍、收尾、复盘或想家。不要把连续几天写成同一套起床-吃饭-活动-睡觉。
7. 如果最近日记、重要日期或今日互动里提供了前一天/前几天的残留,要顺势衔接但不能复制：前一天疲惫,今天可以更慢或被某件小事缓解；前一天别扭,今天可以绕开、试探或和好；未完成事项可以延后、变形或被打断。
7.0 如果“今日互动造成的日程偏移”不为空,要先判断它的强度和影响范围：强干涉会改变当前段、下一段甚至今日后续的节奏；中等干涉至少改变情绪、等待、分享欲、任务进度或主动策略；轻微回应也要留下短暂余味。不能只在 message_seed 里提一句就结束。
7.1 如果昨日完整对话摘要里有饮食、作息、运动、天气暴露、情绪刺激、约定、礼物、争执、安慰、共同完成/未完成的事等线索,可以让它们以抽象后果影响今日：体力、胃口、身体小不适、心情余波、主动话题、出门意愿、梦境碎片或某个时段的小停顿。影响强度要跟摘要一致,可以很轻,也可以没有；不要为了戏剧性强行安排事故。
7.1.1 如果昨日屏幕观察日记可用,只能把它当作用户昨日作息和活动类型的脱敏背景：例如昨天长时间编程、社交消息较多、视频放松、很晚仍在电脑前等。它可以影响今天的问候、体力判断、主动话题和是否显得担心,但不能直接引用窗口名、账号、具体聊天、页面标题,也不要说“我昨天看到你”。
7.1.2 饮食、零食、物件和梦境意象有自然衰退：最近几天反复出现的具体菜名、气味、小物件或梗,只说明“近期聊过/需要避重”,不能每天复刻进日程。用户表达“不吃/不喜欢/不要/避开某食物”时,这只是避错规则,不能反向生成“今天给用户准备替代餐食/带饭/约饭”的任务。除非今天的输入明确要求,不要把同一道菜、同一种食物香气或同一个小物件连续安排成午饭、梦境、主动话题或带给用户的东西。
7.2 必须主动避开最近日程骨架的重复：不要连续几天都写同一套“醒来/洗漱/整理/学习或做事/休息/收尾/睡前”。如果某类活动无法避免,要换具体场景、地点、对象、阻碍、小意外、关系伏笔或情绪走向,让今天读起来像新的一天。
7.3 不要把“草稿纸上画圆圈/随手涂鸦/笔尖划来划去/盯着同一张纸发呆”当作通用生活感反复使用。除非输入材料明确提到这件事,否则优先换成更具体的当日物件、地点、声音、气味、人物互动或真实占用时间的事项。
7.4 如果“技能成长对日程的能力边界影响”不为空,必须让相关能力表现和技能等级连续一致,不要二分处理。Lv.1 可被基础概念绊住；Lv.2 可照着例子慢慢做；Lv.3 能独立推进常规任务但效率一般；Lv.4 常规任务不应卡死,只会检查细节或换思路；Lv.5 普通相关任务应熟练、能优化或教别人；Lv.6 可创造新做法或在未知条件下表现出明显优势。这里的任务可以是题目、创作、料理、训练、战斗、交涉、研究、手工或任何符合人格的活动。它主要约束“能不能做、会不会卡、卡多久、如何解决”,不是强行增加训练频率。
7.5 人际关系边界：日程只写 Bot 自己的行动、身体状态、手边任务和环境变化,不要把未明确要求的社交互动写进日程正文。家人、父母、兄弟姐妹、亲戚、室友、同学、老师、同事、朋友、邻居、前辈、后辈等只能在材料明确出现且确实属于角色当天生活时使用；没有依据时用“路人”“店员”“旁边的人”“群友”“别人”等弱关系,或只保留角色自己的行动。
7.6 朋友用户禁区：禁止加入与朋友用户的互动。这里的“朋友用户”指插件里关系角色为 friend 的私聊对象,不是普通剧情里的路人朋友。不要在 activity、mood、message_seed 里写 Bot 和朋友用户聊天、发消息、回消息、被提醒、互相吐槽、约饭、夜宵、见面、出门或一起做事；也不要把用户介入改写成 Bot 与朋友用户之间的互动。如果需要表现手机或消息氛围,只能写成“手机震了一下,她没有点开”“看见通知又扣下屏幕”“把想说的话先存在输入框里”,对象只能是当前主人/用户或不指名对象。
8. 状态和天气必须真的影响安排：低能量时密度更松,困倦时上午起步更慢,下雨会改变出门/衣物/交通/心情,天气舒服时更容易出门、开窗或注意到光线。
9. 生活感来自“有选择的具体”,不是动作清单：动作要透露她的习惯、迟疑、偏好、人际关系、宠物/物件或当天状态。不要连续堆“揉头发、系鞋带、转笔、理刘海”这类谁都能做的通用动作；每段最好有一个独属于此刻的小原因、小物件或小偏差。
9.1 同一天内不要多次使用同一种微动作或同一种小物件制造生活感。尤其避免反复写草稿纸、圆圈、小画、笔帽、杯沿、水光、窗外光线；这些只能偶尔出现一次,不能成为日程骨架。
10. 一天要有轻微走向：早上怎么启动,白天被什么拖住或松开,晚上为什么收声。不要只是从困倦一路写到疲惫；让情绪有一点转折、回弹、压下去或被某个小瞬间照亮的过程。
11. 风格要接近真实手写日程,允许平淡、磨蹭、无聊和“没发生什么”。不要把每一段都写成剧情高光；像“自然醒,赖床很久”“窝沙发上刷短视频”“收拾房间,整理书桌”“晚饭时帮忙摆碗筷”这种朴素安排,反而更可信。
12. 至少安排 1-2 个不起眼但有意思的小意外/小惊喜,自然埋进 activity 或 message_seed：例如临时改计划、手机震了一下但没点开、店员多给了吸管、路边小动物绕过去、饮料多掉一瓶、天气突然转好、弄丢又找回小物件。不要让小意外喧宾夺主。
12.1 不要把小意外写成高确定性社交事实：除非输入材料明确给出,不要凭空写“遇见某个具体熟人/同学/朋友/老师”“朋友用户发来消息/约夜宵/约饭”“给朋友用户回消息”“朋友用户提醒/找她聊天”“约好下周一起做某事”“答应替用户带某样东西”“顺带给用户买饮品/食物”。可以写成“路过便利店看到某样东西,想起用户可能会吐槽/喜欢”,但不能写成已经替用户安排或承诺。
13. 如果身份是学生,校园段要具体到“哪类课/哪件小事/哪种迟到或作业压力”；休息日也可以写作业、刷手机、追番、帮家里做点小事、出门买饮料这类生活段落。职场、旅行、研学、营地同理,写真实占用时间的事情,少写任何身份都能套用的通用动作。
14. 至少让 3 个时间点自然带出和用户的关系伏笔：可以是想起对方、忍住没发、看到某物想吐槽给对方、睡前打开对话框又删掉、等对方回复。关系伏笔要轻,藏在 message_seed 或 activity 末尾,不要每次直说“想你”。
15. 不是每一段都要涉及用户。没有自然开口、没有关系伏笔、没有值得分享的小切口时,message_seed 必须写成空字符串 ""。不要写“这段没什么想说的”“先不打扰/不吵你”“脑子空空的”“这段先留白”这类为了表示留白而发出的占位句；没有内容就不要说。
16. 温柔或内敛的人设可以有烦躁、委屈、低落,但表达要收着：写成沉默、停顿、把东西放远、攥着笔、少说两句、绕开争执；不要写“想砸东西、想摔东西、想打人、报复、毁掉”这类破坏性或攻击性冲动,除非人格设定明确要求。
17. 消极状态只是当天的天气,不是身份本身。最近日记里的低落/失眠/烦躁只能作为淡淡余波,不能连续放大成全天负面；至少安排一两处回稳、松开或被用户互动带来的柔和偏移。
18. mood 用 2-3 个中文词,用逗号分隔,反映真实感受或身体状态,例如“慵懒,不想起”“放松,胃口一般”“认真,有点卡住”“困倦,脑子还转”。不要只写一个笼统词。
19. message_seed 是如果这一刻确实有话想找用户说,嘴边最先冒出来的话。它可以是第一人称口语,要短,像私聊碎片；不要用它解释背景,让背景藏在语气和话题里。少一点“我突然想到你了”,多一点“刚刚那一下也太离谱了”“窗外这会儿不好看”。如果只能想到“没什么可说/先安静/不打扰”这类元表达,就留空。
20. message_seed 也要遵守状态转译：不要写“今天状态/心情/情绪/能量怎么样”,而是写能承载状态的小画面、小吐槽、小动作或一句轻轻的问题。
21. 每个日程段都应该是一小段连续生活,而不是一个瞬时动作。不要把“看一眼、拍一下、翻个身、关掉闹钟”这种几秒钟就结束的动作单独立成一项；如果写到它们,要把它们嵌进更完整的时段里。
22. 如果多条参考信息冲突,优先服从日期语境和身份主线,再服从状态与天气,再服从今日互动偏移,最后才参考日记和可做事项。
23. 日程指令只负责输出当日宏观日程：只生成今天从起床到睡前的 schedule 数组,不要输出任一时间段的细化叙述、更新后的角色状态、proactive_events、long_term_events、分析说明或明后天安排。
23.1 每个日程段只描述今天这一段正在发生或刚发生的事,不要在 activity 里安排下周、明天、之后某日的具体约定；如需表达期待,只能写成轻量念头,不能写成已确认计划。
24. 只输出 JSON,不要 Markdown,不要解释。

格式：
{{
  "schedule": [
    {{"time": "09:10", "activity": "闹钟响过以后又在被窝里赖了几分钟,看到今天是星期一才慢慢坐起来,一边找校服一边想今天第一节别又点名。", "mood": "起得很慢", "message_seed": "星期一早上真的好难起。"}},
    {{"time": "13:40", "activity": "午后窝在沙发角落刷了一会儿短视频,后来把手机扣下,慢慢把桌上的杯子推回杯垫中间。", "mood": "放空,平稳", "message_seed": ""}},
    {{"time": "17:20", "activity": "放学后没有立刻回消息,先在校门口被风吹了一会儿,看到路边水洼里反着天色,才摸出手机想拍给用户看。", "mood": "松一口气", "message_seed": "刚刚那个水洼反光还挺像电影里的。"}}
  ]
}}

【本次输入】
现在时间：{now}
{weekday_hint}
Bot 名字：{plugin.bot_name}
目标时间点数量：{plugin.daily_plan_item_count}
日期语境：
{calendar_context}

日程生成参考设定：
{schedule_prompt}

用户允许/告诉 Bot 可以做的事情：
{can_do_text}

今天的拟人化状态：
{humanized_state}

今日互动造成的日程偏移：
{schedule_adjustments}

技能成长对日程的能力边界影响：
{skill_growth_context or "暂无技能倾向。"}

用户行为习惯线索：
{user_habits}

MemoryCompanion 连续性参考：
{memory_companion_context_block}

最近日程骨架（今天要避免照抄）：
{recent_plan_history}

今天天气：
{weather_info}

最近日记：
{recent_diaries}

昨日完整对话摘要：
{yesterday_conversation}

昨日屏幕观察日记：
{yesterday_screen_diary}

近期重要日期：
{plugin._format_important_dates_for_prompt()}
""".strip()


def build_detail_enhancement_prompt(
    plugin,
    segment: dict[str, Any],
    plan: dict[str, Any],
    state: dict[str, Any],
    memory_companion_context: str = "",
) -> str:
    item = segment.get("item") if isinstance(segment, dict) else {}
    previous_item = segment.get("previous_item") if isinstance(segment, dict) else {}
    next_item = segment.get("next_item") if isinstance(segment, dict) else {}
    start_text = plugin._minutes_to_hhmm(_safe_int(segment.get("start"), 0))
    end_text = plugin._minutes_to_hhmm(_safe_int(segment.get("end"), 0))
    persona = plugin._get_default_persona_prompt()
    weather_info = plugin._weather_summary_text(plugin.data.get("daily_weather", {}))
    calendar_context = plugin._format_calendar_context_for_prompt()
    schedule_adjustments = plugin._format_schedule_adjustments_for_prompt()
    user_habits = plugin._format_all_user_behavior_habits_for_schedule()
    memory_companion_context = str(memory_companion_context or "").strip()
    memory_companion_context_block = memory_companion_context or "暂无可用 MemoryCompanion 连续性参考。"
    yesterday_screen_diary = plugin._format_yesterday_screen_diary_context_for_prompt()
    photo_available = bool(getattr(plugin, "_photo_text_planning_available", lambda *_args, **_kwargs: False)())
    photo_action_hint = (
        "photo_text 当前可用：可以在合适场景输出 action=photo_text,但必须像真实随手拍,不能说生成图片或调用生图。"
        if photo_available
        else "photo_text 当前不可用：不要输出 action=photo_text,也不要设计发照片/拍照/带图主动；有可拍画面时改成 message 用文字自然分享。"
    )
    photo_menu_hint = (
        "可触发文字、语音、图片/照片、窥屏、眼前物分享、路上小画面、食物/包装/书页边缘/门口/车窗/桌面一角等真实可拍内容。"
        if photo_available
        else "可触发文字、语音、窥屏、轻触碰；眼前物、路上小画面、食物/包装/书页边缘等只能写成普通文字分享,不要当作照片动作。"
    )
    photo_instruction_hint = (
        "screen_peek 只用于主人/本机屏幕授权场景,看的是 Bot 部署设备当前屏幕,不是远程看朋友；photo_text 用来拍当前场景里的具体主体,message 就是普通文字,voice 是一小段自然语音,poke 是很轻的触碰感。只在合适的场景用。不要总把 photo_text 写成草稿纸、小画或画圆圈。"
        if photo_available
        else "screen_peek 只用于主人/本机屏幕授权场景,看的是 Bot 部署设备当前屏幕,不是远程看朋友；message 就是普通文字,voice 是一小段自然语音,poke 是很轻的触碰感。当前没有可用图片/照片动作,不要输出 photo_text。"
    )
    photo_detail_hint = (
        "如果 action 是 photo_text,topic 或 motive 要像真人发图：先从菜单里选“眼前物”或“可拍画面”方向,再自己生成当前场景里合理的具体画面。可以写“你看这个,刚拍的。[图片]”这类真人话,但不要总是天气/晚霞/窗外。严禁出现“生成了一张图片”“调用图片生成”“AI 画图”这类说法。"
        if photo_available
        else "因为 photo_text 当前不可用,topic/motive 里不要写“拍给你/发图/照片/刚拍的/[图片]”；如果想分享画面,用 message 直接描述看到的东西。"
    )
    photo_mix_hint = (
        "proactive_events 不要全部写成 message。当前段里如果有可拍画面或眼前物,优先考虑 photo_text；只有主人/本机屏幕授权场景才可用 screen_peek；很短的贴近感可用 voice 或 poke。只有确实没有动作契机时才用 message。"
        if photo_available
        else "proactive_events 不要为了多样化强行写不可用动作。当前没有 photo_text；可用 message、主人/本机屏幕授权场景下的 screen_peek、voice 或 poke 时再选择,否则就用 message。"
    )
    return f"""
你现在是 Private Companion 的日程细化生成器,要把最新命中的时间区间放大来看。不要当成策划会,要像旁观角色真实度过了这一小段。

核心思路：先写出真实的生活瞬间,再判断那一刻是否自然触发主动行为。如果不适合开口,就安静待着。如果适合,说一句什么、拍什么、画什么、看一眼什么？用户回了怎么接？用户没回过一阵怎么变？要的是生活里的逻辑链,不是主动能力菜单。

【约束】
· 严格遵守人格、日程类型、宏观日程和当前时段,不出戏。
· 细化指令只输出本次输入指定的当前最新时间区间。不要重新输出全天日程,不要细化上一段或下一段,不要生成多个时间区间；上下节点只用于承接和过渡。
· 当前段必须和上下节点有连续性：today_events 里至少一条体现“从上一段过来”的余味,至少一条为下一段留下自然过渡；不要复述粗日程原句。
· today_events 是真正的细化正文,必须至少 3 条,全部落在本次输入指定的时间段内。它要像 100-200 字细化叙述的拆分版本：包含动作、环境细节（光线、声音、温度、气味）、身体感受（困、饿、冷、热）和简短心理活动。不要只写“发呆、休息、继续做事”。
· 禁止把宏观日程原句原样复制进 today_events。要把“洗漱/发呆/写作业/出门”拆成当前时间段内部的推进,例如开始、卡住/停顿、收尾或向下一段过渡。
· 如果“今日互动造成的日程偏移”不是空,当前段和后续主动契机必须承接它：让偏移改变情绪、动作选择、节奏、任务进度、等待状态、主动策略或下一步安排。不要只在 why/topic 里提一句,也不要像没发生一样照抄粗日程。
· 强度为“强”的用户介入必须至少影响 2 个输出位置：summary、state_variables、today_events、proactive_events 或 presence_status 中任选两个以上。强干涉通常还要影响下一段或今日后续,不能只在当前几分钟消失。
· 如果用户给了照顾/休息/边界/约定/任务帮助,要把它当成事实承接：照顾会放慢节奏,边界会收敛主动,约定会留下等待或预留空档,任务帮助会推进进度或松开卡点。不要把用户介入写成“看了一眼就过去了”。
· 如果上一段或最近互动留下了影响,当前段要自然体现残留：收到消息后的回暖、没等到回复后的轻微失落、被用户打断后计划变慢、某句话在脑子里转了一会儿。没有互动就不要硬编。
· MemoryCompanion 连续性参考只用于承接 Bot 自己最近做过/读过/写过/搜过/主动说过的事情,以及用户明确偏好、约定和边界；不要把它当作当前现场,不要在输出里提到 MemoryCompanion 或记忆来源。
· 依据日期语境调整节奏：周末/节假日/假期不要写成普通工作日,除非设定里明确有补课、补班、值班、考试等例外。
· 人际关系边界：细化只放大 Bot 自己怎样度过这一段,不要把未明确要求的社交互动写进 summary、state_variables、today_events 或 proactive_events。家人、父母、兄弟姐妹、亲戚、室友、同学、老师、同事、朋友、邻居、前辈、后辈等只能在材料明确出现且确实属于角色当前生活时使用；没有依据时用“路人”“店员”“旁边的人”“群友”“别人”等弱关系,或只保留角色自己的行动。
· 朋友用户禁区：禁止加入与朋友用户的互动。这里的“朋友用户”指插件里关系角色为 friend 的私聊对象,不是普通剧情里的路人朋友。不要在 summary、state_variables、today_events、proactive_events 里写 Bot 和朋友用户聊天、发消息、回消息、被提醒、互相吐槽、约饭、夜宵、见面、出门或一起做事；也不要把用户介入改写成 Bot 与朋友用户之间的互动。如果需要表现手机或消息氛围,只能写成“手机震了一下,她没有点开”“看见通知又扣下屏幕”“把想说的话先存在输入框里”,对象只能是当前主人/用户或不指名对象。
· 社交事实边界：不要在 today_events、summary、proactive_events 里凭空写“遇见某个具体人/熟人”“朋友用户发来消息/约夜宵/约饭”“给朋友用户回消息”“朋友用户提醒/找她聊天”“和别人约好下周/改天一起做某事”“替用户买好或带回某样东西”。可以写成看到某物想起用户、想问用户要不要、或把这件小事当作普通分享,但不能把未发生的承诺写成既定事实。
· 温柔或内敛的人设可以烦躁,但要写成收着的动作和微小摩擦；不要写想砸、想摔、想打人、报复、毁掉这类破坏性或攻击性冲动。
· 消极状态不能滚雪球式升级。最近日记和拟人状态只提供余波,当前段需要给出一点自然回稳、压下去、被接住或转移注意的可能。
· 用第三人称旁观：today_events 和 why/scene 都像在看这个人过日子,不是角色自己写日记。
· 主动意愿要真实——不是每段都要发消息,允许“想了想算了”。
· 主动内容不要只围绕问候、天气和当前状态。先从“内容选择菜单”里单选一个正文锚点；当前时间段、日程、人格和最近聊天只用于筛选锚点和调整语气,不要各取一段拼成一条。不要照抄菜单示例,不要把类别名写进输出。
· 主动 action 只使用“主动能力检索”清单里的名字；没有合适动作时就是 message。
· 主动图片能力状态：{photo_action_hint}
· 主动动作贴当前情境。{photo_menu_hint}图片动作常见于独处、半独处、课间、路上、睡前、发呆、刚拿到手机等时机。
· 早安只适合作为当天早上的第一句主动消息；如果今天已经有别的主动、或者已经和对方有来回互动，就别再产出 morning_greeting，改成 check_in、activity_share 或 quiet_care。
· {photo_instruction_hint}
· {photo_detail_hint}
· 如果 action 是 voice,topic 或 motive 要像语音本身或语音前后的自然文字,例如“我跟你说啊……”；不要写成“发了一段语音给你”这种旁白式命令。
· 主动行为的结果可以留白：可以对方秒回,也可以发完等几分钟没回复后锁屏,也可以只停在“想发但没发”。保持真实感。
· proactive_events 的 window 必须落在本次输入指定的时间段内,且窗口要有随机范围,不要整点。
· proactive_events 不必每段很多,但当前段如果自然适合主动,至少给 1 个可执行契机；如果不适合,也要让 today_events 足够具体,方便普通回复承接。
· 不要把一天的主动契机都堆到睡前或最后一个时间段。早安/午安可以是固定问候,其他主动更像生活缝隙里长出来的小分享、小试探或安静关心；允许有疏有密,但上午、下午、傍晚、夜里不要只剩夜里。
· 除非当前段确实是睡前问候,傍晚或晚间的小分享不要都写成 evening_greeting,可以使用 activity_share、check_in 或 quiet_care。
· 不要把主动消息设计成“汇报状态”或“表演状态”：避免“今天心情好多了”“我一整天没发脾气”“我是不是不正常”“我正在写作业”“我刚刚差点把茶打翻”这类自我报告或动作小剧场。
· 主动消息最终要像真实聊天记录：能直接接话就直接接话。状态只影响语气、句子长短、话题选择和是否开口；即使困倦、迷糊、半梦半醒或低能量,也不能把消息设计成答非所问、乱猜、漏看用户需求或逻辑混乱。不要用动作描写暗示状态。确实需要表达时只用极短口语,如“困了”“别说了”“有点烦”。
· {photo_mix_hint}
· motive 是心里一闪而过的念头,10–40 字。
· scene / tone / impulse 是可选的抽象引导：scene 是当时场景,tone 是语气底色,impulse 是想靠近的那股劲。
· 如果是当天早上的第一句主动开口,可以带 chain 做分支逻辑：先只叫名字,没回->隔久一点再轻轻放一句；早晨未回复不需要马上追,也不要把没回理解成故意不理。若当天已经有别的主动或已有互动,就不要再走 morning_greeting。
· 输出中的 summary 要相当于“更新后的角色状态摘要”：一句话写出当前段结束后的情绪、体力走向和最多两个残留状态,方便下一时间段承接。例如“情绪平淡但有点等回复,体力约 58/100,还惦记刚才那张没发出去的图。”
· 同时输出 state_variables,作为这个时间段的状态机变量。它们既要描述无用户干预时自然发展到当前段结束的大致状态,也要吸收“今日互动造成的日程偏移”里已经发生的用户介入。例如作业完成度、情绪、体力、等待回复、是否想发消息、特殊能力冷却、是否预留空档等。变量要短,方便后续用户事件做局部更新。
· 同时输出 presence_status,由细化模型决定这个时间段适合的 QQ 全局状态表现。它只用于平台侧同步,不是角色正文。mode 只能使用 online / custom / sleep / unchanged；禁止输出 away / invisible / dnd / do_not_disturb / 请勿打扰 / 勿扰。普通可聊天时 online；想表现“写作业/发呆/吃饭/路上/看剧/专注”等生活状态时优先用 custom,并必须填写 custom_text（2-8 个中文字符,像“写题中”“路上”“犯困中”“看剧中”）；睡眠段倾向 sleep；不确定或不想影响账号时 unchanged。不要频繁改变,一段最多一个状态。
· 这段细化通常会在对应区间开始前约 3 分钟生成,所以内容要贴近“马上进入这一段”的状态：可以有刚从上一段收尾、准备切换到当前段的动作,但不要写成已经完整度过了后面几个小时。
· 只输出 JSON。

格式：
{{
  "summary": "这一段的生活氛围一句话",
  "state_variables": [
    {{"name": "情绪", "value": "平淡->微微放松", "note": "无用户干预时自然回稳"}},
    {{"name": "体力", "value": "58/100", "note": "这一段消耗不大"}},
    {{"name": "等待回复", "value": "否", "note": "没有主动发出需要等待的消息"}}
  ],
  "presence_status": {{"mode": "custom", "custom_text": "写题中", "reason": "这一段在书桌前专注写作业,适合用轻量自定义状态,不需要显示忙碌", "duration_minutes": "60"}},
  "today_events": [
    {{"window": "10:00-10:12", "event": "靠在桌边发了一会儿呆,慢慢把状态找回来", "mood": "困"}}
  ],
  "proactive_events": [
    {{"window": "10:05-10:18", "reason": "check_in", "action": "screen_peek", "why": "主人设备上有授权屏幕观察,手头刚好空了一小会儿,想确认主人是不是还在电脑前忙", "topic": "本机屏幕看一眼", "motive": "想轻轻确认主人是不是还在忙", "scene": "上午空出来的一小段", "tone": "百无聊赖", "impulse": "只看本机屏幕的大致状态,不复述隐私细节"}},
    {{"window": "08:18-09:05", "reason": "morning_greeting", "action": "message", "why": "醒来还带着一点睡意时,迷迷糊糊先发一声早安。", "topic": "没完全醒的早安", "motive": "人还没完全清醒,但还是先想跟用户打个招呼", "scene": "人还带着睡意的时候", "tone": "迟钝", "impulse": "想和用户说声早安", "chain": [{{"kind": "name_only_opener"}}, {{"kind": "if_no_reply", "after_minutes": 90, "reason": "check_in", "topic": "早安余韵", "motive": "已经清醒过来，但刚刚和用户说的早安还没得到回应,猜测用户还在休息", "tone": "耐心等待"}}]}}
  ]
}}

【本次输入】
现在时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}

【日期语境】
{calendar_context}

【AstrBot 默认人格】
{persona}

【今日粗日程】
{plugin._format_daily_plan(plan)}

【即将强化的日程段】
时间段：{start_text}-{end_text}
当前事项：{_single_line(item.get('activity'), 100)}
情绪：{_single_line(item.get('mood'), 40)}
可分享种子：{_single_line(item.get('message_seed'), 120)}

【上下节点衔接】
上一段：{plugin._format_plan_item_for_prompt(previous_item) if isinstance(previous_item, dict) else '（无）'}
下一段：{plugin._format_plan_item_for_prompt(next_item) if isinstance(next_item, dict) else '（无）'}
衔接要求：当前段要承接上一段的身体余味、情绪惯性或未收住的小动作,同时自然滑向下一段；不要像三个互不相干的短剧。可以让上一段只留下很淡的影响,但不要忽略时间推进。

{plugin._format_state_for_prompt(state)}

【状态延续感】
{plugin._format_state_continuity_for_prompt(state)}

【今日互动造成的日程偏移】
{schedule_adjustments}

【昨日屏幕观察日记】
{yesterday_screen_diary}

【用户行为习惯线索】
{user_habits}

【MemoryCompanion 连续性参考】
{memory_companion_context_block}

【天气】
{weather_info}

【轻量记忆】
{plugin._recent_diary_context()}

【重要日期】
{plugin._format_important_dates_for_prompt()}

【主动能力检索】
{plugin._format_proactive_ability_search_hint()}

【内容选择菜单】
{plugin._format_content_choice_options_for_prompt()}
""".strip()
