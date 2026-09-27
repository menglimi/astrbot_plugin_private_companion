# -*- coding: utf-8 -*-
"""planning_part01：从 planning.py 机械抽取的模块级函数。

由 tmp/split4/mod_split.py 生成（13 个函数 / 329 行）。函数体逐字节原样，仅位置变化。
对外经由宿主 planning.py re-export，接口不变。
"""
from __future__ import annotations

from .planning_shared import (
    Any,
    PromptDocument,
    PromptRenderMode,
    PromptSection,
    _safe_float,
    _safe_int,
    _single_line,
    _today_key,
    prompt_section,
    re,
    render_prompt_document,
    render_prompt_sections,
    runtime_persona_setting,
)


def _render_planning_prompt(section: PromptSection) -> str:
    return render_prompt_sections([section], mode=PromptRenderMode.BODY_ONLY)

def _render_planning_document(document: PromptDocument) -> dict[str, str]:
    return render_prompt_document(document, mode=PromptRenderMode.BODY_ONLY)

def _planning_retry_prompt_section(
    *,
    key: str,
    title: str,
    original_prompt: str,
    correction: str,
    correction_title: str = "额外纠偏",
) -> PromptSection:
    return prompt_section(
        key=key,
        title=title,
        source="planning",
        content=original_prompt,
        children=(
            prompt_section(
                key=f"{key}.correction",
                title=correction_title,
                source="planning",
                content=correction,
            ),
        ),
    )

def pick_detail_segment(plugin, plan: dict[str, Any], enhanced: dict[str, Any]) -> dict[str, Any] | None:
    parsed_segments = plugin._collect_detail_segments(plan, enhanced)
    if not parsed_segments:
        return None
    now_minutes = plugin._effective_plan_now_minutes(str(plan.get("date") or ""))
    if now_minutes is None:
        return parsed_segments[0] if parsed_segments else None
    lead = _safe_int(runtime_persona_setting(plugin, "detail_enhancement_lead_minutes", 3), 3, 0)
    for segment in parsed_segments:
        start = _safe_int(segment.get("start"), 0)
        next_start = _safe_int(segment.get("end"), plugin._segment_end_minutes(start, segment.get("item")))
        in_lead = start - lead <= now_minutes <= start
        in_segment = start <= now_minutes < next_start
        if in_lead or in_segment:
            return segment
    return None

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

def detail_target_event_count(plugin, segment: dict[str, Any]) -> int:
    start = _safe_int(segment.get("start"), 0)
    end = _safe_int(segment.get("end"), plugin._segment_end_minutes(start, segment.get("item")))
    if end <= start:
        end += 24 * 60
    duration = max(1, end - start)
    if plugin._is_sleepy_plan_item(segment.get("item")):
        return 2 if duration <= 60 else 3
    if duration <= 30:
        return 2
    if duration <= 60:
        return 3
    if duration <= 120:
        return 4
    return 5

def detail_payload_quality_issues(plugin, payload: Any, segment: dict[str, Any]) -> list[str]:
    if not isinstance(payload, dict):
        return ["返回内容不是有效 JSON 对象"]
    raw_events = plugin._normalize_story_items(payload.get("today_events"), "event")
    events = filter_items_to_segment(plugin, raw_events, segment)
    issues: list[str] = []
    target_count = detail_target_event_count(plugin, segment)
    if len(events) < target_count:
        issues.append(f"当前段内只有 {len(events)} 条有效事件，目标至少 {target_count} 条")

    start = _safe_int(segment.get("start"), 0)
    end = _safe_int(segment.get("end"), plugin._segment_end_minutes(start, segment.get("item")))
    if end <= start:
        end += 24 * 60
    duration = max(1, end - start)
    event_bounds: list[tuple[int, int]] = []
    for event in events:
        item_start, item_end = plugin._parse_window_minutes(str(event.get("window") or ""))
        if item_start is None or item_end is None:
            continue
        if item_end < item_start:
            item_end += 24 * 60
        if item_start < start and end > 24 * 60:
            item_start += 24 * 60
            item_end += 24 * 60
        event_bounds.append((item_start, item_end))
    if duration >= 60 and event_bounds:
        first_start = min(bound[0] for bound in event_bounds)
        last_end = max(bound[1] for bound in event_bounds)
        if first_start > start + min(30, max(10, duration // 4)):
            issues.append("事件没有覆盖本段开头")
        if last_end < start + int(duration * 0.72):
            issues.append("事件只集中在本段前部，没有覆盖中后段")

    summary = _single_line(payload.get("summary"), 180)
    meal_checker = getattr(plugin, "_schedule_text_is_single_meal_action", None)
    if duration > 120 and callable(meal_checker) and meal_checker(summary):
        issues.append("summary 用短时进食动作概括了整个长时段")
    artifact_cleaner = getattr(plugin, "_sanitize_schedule_model_artifacts", None)
    if summary and callable(artifact_cleaner) and artifact_cleaner(summary, limit=180) != summary:
        issues.append("summary 混入草稿字段、Markdown 或角色台词")
    return issues

def evaluate_detail_quality(plugin, payload: Any, segment: dict[str, Any]) -> dict[str, Any]:
    issues = detail_payload_quality_issues(plugin, payload, segment)
    deductions = 0
    for issue in issues:
        if "有效 JSON" in issue:
            deductions += 60
        elif "有效事件" in issue:
            deductions += 28
        elif "中后段" in issue or "覆盖本段" in issue:
            deductions += 22
        elif "短时进食" in issue:
            deductions += 24
        else:
            deductions += 10
    score = max(0, 100 - deductions)
    return {
        "score": score,
        "level": "good" if score >= 85 else "fair" if score >= 70 else "poor",
        "issues": issues[:8],
    }

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

def normalize_detail_location(raw: Any) -> str:
    if isinstance(raw, dict):
        raw = (
            raw.get("name")
            or raw.get("text")
            or raw.get("location")
            or raw.get("place")
            or raw.get("地点")
        )
    text = _single_line(raw, 80)
    text = re.sub(r"^(?:当前位置|地点|位置|场景)\s*[:：]\s*", "", text).strip()
    return _single_line(text, 60)

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
        if action == "screen_peek" and not runtime_persona_setting(plugin, "allow_screen_peek_action", False):
            action = "message"
        photo_planning_available = getattr(plugin, "_photo_text_planning_available", lambda *_args, **_kwargs: False)
        if action == "photo_text" and not bool(photo_planning_available()):
            action = "message"
        if action == "voice" and not runtime_persona_setting(plugin, "allow_voice_action", False):
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
        "location": normalize_detail_location(payload.get("location")),
        "location_basis": plugin._normalize_schedule_basis(payload.get("location_basis"), default=["coarse_plan"]),
        "location_confidence": min(1.0, _safe_float(payload.get("location_confidence"), 0.72)),
        "state_variables": state_variables,
        "presence_status": normalize_presence_status(payload.get("presence_status")),
        "today_events": today_events[:8],
        "proactive_events": normalized_proactive,
        "long_term_events": long_term_events[:3],
    }

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
