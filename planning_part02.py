# -*- coding: utf-8 -*-
"""planning_part02：从 planning.py 机械抽取的模块级函数。

由 tmp/split4/mod_split.py 生成（12 个函数 / 431 行）。函数体逐字节原样，仅位置变化。
对外经由宿主 planning.py re-export，接口不变。
"""
from __future__ import annotations

from .planning_part01 import normalize_detail_window
from .planning_shared import (
    Any,
    PromptRenderMode,
    _safe_int,
    _single_line,
    inspect,
    logger,
    prompt_section,
    re,
    render_prompt_sections,
    runtime_persona_setting,
)


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
        lifecycle = _single_line(raw.get("lifecycle_status"), 20).lower()
        if lifecycle not in {"changed", "cancelled"}:
            lifecycle = "planned"
        item = {
            "window": window,
            text_key: text_value,
            "mood": _single_line(raw.get("mood"), 30),
            "lifecycle_status": lifecycle,
            # Detail output is a temporary scene proposal.  It must never be
            # mistaken for a current or historical Bot fact.
            "status": "planned",
            "source_kind": "planned",
            "evidence_kind": "none",
            "commitment_level": "tentative",
            "content_granularity": "scene",
            "materialization_state": "candidate",
            "fact_eligibility": "none",
            "subject_actor_id": "bot_self",
            "actor_type": "bot",
            "basis": plugin._normalize_schedule_basis(raw.get("basis"), default=["coarse_plan"]),
            "confidence": min(1.0, max(0.0, float(raw.get("confidence") or 0.72)))
            if str(raw.get("confidence") or "").strip().replace(".", "", 1).isdigit()
            else 0.72,
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
            window = f"{item.get('time', '')}-{item.get('end', '')}" if item.get("end") else item.get("time", "")
            lines.append(f"- {window} {item.get('activity', '')}")
    return "\n".join(lines) if lines else "（暂无）"

def evaluate_daily_plan_quality(plugin, items: Any) -> dict[str, Any]:
    if not isinstance(items, list) or not items:
        return {"score": 0, "level": "poor", "issues": ["没有可用日程段"]}
    issues: list[str] = []
    deductions = 0
    parsed: list[tuple[int, int, dict[str, Any]]] = []
    day_offset = 0
    previous_raw_start: int | None = None
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        raw_start = plugin._parse_hhmm_to_minutes(item.get("time"))
        raw_end = plugin._parse_hhmm_to_minutes(item.get("end"))
        if raw_start is None or raw_end is None:
            deductions += 18
            issues.append(f"第 {index + 1} 段缺少有效起止时间")
            continue
        if previous_raw_start is not None and raw_start < previous_raw_start:
            day_offset += 24 * 60
        start = raw_start + day_offset
        end = raw_end + day_offset
        if raw_end <= raw_start:
            end += 24 * 60
        previous_raw_start = raw_start
        duration = end - start
        parsed.append((start, end, item))
        if duration < 15:
            deductions += 14
            issues.append(f"{item.get('time')}-{item.get('end')} 时长过短")
        if duration > 6 * 60 and not plugin._is_sleepy_plan_item(item):
            deductions += 12
            issues.append(f"{item.get('time')}-{item.get('end')} 非睡眠活动持续过长")
        meal_checker = getattr(plugin, "_schedule_text_is_single_meal_action", None)
        if duration > 120 and callable(meal_checker) and meal_checker(item.get("activity")):
            deductions += 22
            issues.append(f"{item.get('time')}-{item.get('end')} 用短时进食动作概括长时段")
    for (start, end, _), (next_start, _, _) in zip(parsed, parsed[1:]):
        if end > next_start:
            deductions += 20
            issues.append("相邻日程存在时间重叠")
        elif next_start - end > 180:
            deductions += 8
            issues.append("相邻日程之间存在超过三小时的未说明空档")
    if len(parsed) < 5:
        deductions += 12
        issues.append("全天有效日程段过少")
    if parsed:
        last_start, last_end, _ = parsed[-1]
        # 从日程里最后一段睡眠推导该人格的"晚间"：早睡/夜型人格的晚间是
        # 睡前最后三小时，而不是硬编码的 17:00 之后，否则合法作息被误罚。
        sleepy_starts = [start for (start, _e, item) in parsed if plugin._is_sleepy_plan_item(item)]
        bedtime = max(sleepy_starts) if sleepy_starts and max(sleepy_starts) >= 17 * 60 else None
        if bedtime is not None:
            evening_threshold = max(12 * 60, bedtime - 3 * 60)
            if last_start < evening_threshold or last_end < evening_threshold + 2 * 60:
                deductions += 24
                issues.append("日程在睡前活跃段前结束，没有覆盖就寝前的生活")
        else:
            evening_threshold = 17 * 60
            if last_start < evening_threshold or last_end < 20 * 60:
                deductions += 24
                issues.append("日程在傍晚前结束，没有覆盖晚间生活")
        evening_count = sum(1 for start, _, _ in parsed if start >= evening_threshold)
        expected_evening = max(2, (len(parsed) + 2) // 3)
        if evening_count < expected_evening:
            deductions += 16
            issues.append(f"晚间节点不足：{evening_count} 段，至少需要 {expected_evening} 段")
    if plugin._plan_has_excess_micro_segments(items):
        deductions += 12
        issues.append("瞬时动作占比过高")
    if plugin._plan_has_excess_abstract_segments(items):
        deductions += 12
        issues.append("抽象描述占比过高")
    if plugin._plan_conflicts_with_calendar(items):
        deductions += 30
        issues.append("日程与日期性质冲突")
    if plugin._plan_is_too_repetitive(items):
        deductions += 10
        issues.append("与最近日程骨架过于重复")
    score = max(0, 100 - deductions)
    level = "good" if score >= 85 else "fair" if score >= 70 else "poor"
    return {"score": score, "level": level, "issues": list(dict.fromkeys(issues))[:8]}

def daily_plan_completion_budget(plugin, *, retry: bool = False) -> int:
    """Scale the completion budget with the configured number of daily segments."""
    item_count = _safe_int(runtime_persona_setting(plugin, "daily_plan_item_count", 10), 10, 5, 24)
    # Keep the existing 1,500-token default while giving the 24-segment setting
    # enough room for complete JSON instead of relying on a provider-side cutoff.
    budget = max(1500, min(5000, 300 + item_count * 120))
    if retry:
        budget = max(budget, 1600)
    return budget

def _build_schedule_reference_sections(
    plugin,
    *,
    knowledge_max_chars: int = 3600,
    knowledge_max_chunks: int = 20,
) -> tuple[str, str]:
    persona = plugin._get_default_persona_prompt()
    schedule_persona = runtime_persona_setting(plugin, "schedule_persona_prompt", "")
    worldview = runtime_persona_setting(plugin, "schedule_worldview_prompt", "")
    identity_parts = []
    if schedule_persona:
        identity_parts.append(
            render_prompt_sections(
                [
                    prompt_section(
                        key="background.schedule.reference.persona",
                        title="日程专用角色设定",
                        source="planning",
                        content=schedule_persona,
                    )
                ],
                mode=PromptRenderMode.LABELED_BLOCK,
            )
        )
    if worldview:
        identity_parts.append(
            render_prompt_sections(
                [
                    prompt_section(
                        key="background.schedule.reference.worldview",
                        title="日程专用世界观/生活背景",
                        source="planning",
                        content=worldview,
                    )
                ],
                mode=PromptRenderMode.LABELED_BLOCK,
            )
        )
    knowledge_formatter = getattr(plugin, "_format_roleplay_knowledge_context", None)
    if callable(knowledge_formatter):
        knowledge_context = knowledge_formatter(
            purpose="schedule",
            max_chars=max(800, int(knowledge_max_chars or 3600)),
            max_chunks=max(4, int(knowledge_max_chunks or 20)),
        )
        if knowledge_context:
            identity_parts.append(knowledge_context)
    if not identity_parts:
        identity_parts.append(
            render_prompt_sections(
                [
                    prompt_section(
                        key="background.schedule.reference.persona_fallback",
                        title="AstrBot 默认人格（身份回退）",
                        source="planning",
                        content=persona,
                    )
                ],
                mode=PromptRenderMode.LABELED_BLOCK,
            )
        )
    else:
        identity_parts.append(
            render_prompt_sections(
                [
                    prompt_section(
                        key="background.schedule.reference.persona_supplement",
                        title="AstrBot 默认人格（仅作缺项补充）",
                        source="planning",
                        content=(
                            persona
                            + "\n只补充日程专用设定没有覆盖的性格与表达习惯；身份、年龄、职业、居住方式和世界观冲突时以上面的日程专用内容为准。"
                        ),
                    )
                ],
                mode=PromptRenderMode.LABELED_BLOCK,
            )
        )
    behavior_parts = []
    worldview_adaptation = ""
    formatter = getattr(plugin, "_format_worldview_adaptation_prompt", None)
    if callable(formatter):
        worldview_adaptation = formatter()
    if worldview_adaptation:
        behavior_parts.append(worldview_adaptation)
    voice_formatter = getattr(plugin, "_format_persona_voice_channel_prompt", None)
    if callable(voice_formatter):
        planning_voice = voice_formatter("planning")
        if planning_voice:
            behavior_parts.append(planning_voice)
    maslow_schedule_hint = _build_maslow_schedule_influence_prompt(plugin)
    if maslow_schedule_hint:
        behavior_parts.append(maslow_schedule_hint)
    return "\n\n".join(identity_parts), "\n\n".join(behavior_parts)

def _sanitize_relationship_generation_source(
    plugin,
    value: Any,
    *,
    source: str,
    max_chars: int = 0,
) -> str:
    sanitizer = getattr(plugin, "_sanitize_generation_relationship_context", None)
    if callable(sanitizer):
        try:
            try:
                cleaned = sanitizer(value, source=source, max_chars=max_chars)
            except TypeError:
                cleaned = sanitizer(value, source=source)
            cleaned_text = str(cleaned or "").strip()
            return cleaned_text[:max_chars] if max_chars > 0 else cleaned_text
        except Exception:
            pass
    cleaned_text = str(value or "").strip()
    return cleaned_text[:max_chars] if max_chars > 0 else cleaned_text

async def _external_schedule_material_context(
    plugin,
    *,
    kind: str,
    max_chars: int,
) -> str:
    """Read optional external life material without making it a hard fact."""
    getter = None
    getter_name = ""
    for candidate in ("_external_schedule_material_context", "_m7a_daily_material_context"):
        candidate_getter = getattr(plugin, candidate, None)
        if callable(candidate_getter):
            getter = candidate_getter
            getter_name = candidate
            break
    if getter is None:
        return ""
    try:
        try:
            value = getter(kind=kind, max_chars=max_chars)
        except TypeError:
            value = getter(kind=kind)
        if inspect.isawaitable(value):
            value = await value
        return _sanitize_relationship_generation_source(
            plugin,
            value,
            source=f"external_schedule.{kind}",
            max_chars=max_chars,
        )
    except Exception as exc:
        logger.debug(
            "外部日程素材提供者不可用: kind=%s getter=%s error=%s",
            kind,
            getter_name or "unknown",
            _single_line(exc, 160),
        )
        return ""

def _relationship_authority_guard(plugin) -> str:
    formatter = getattr(plugin, "_format_generation_relationship_authority_guard", None)
    if callable(formatter):
        try:
            guard = str(formatter() or "").strip()
            if guard:
                return guard
        except Exception:
            pass
    return render_prompt_sections(
        [
            prompt_section(
                key="background.schedule.relationship_authority",
                title="关系事实权限",
                source="planning",
                content=(
                    "只有当前人格与世界观可以建立 Bot 的稳定关系。记忆、历史日程、旧动态和其他连续性材料"
                    "只能延续人格已声明的关系，不能新增家人、亲友、同学、同事或伴侣。"
                ),
            )
        ],
        mode=PromptRenderMode.LABELED_BLOCK,
    )

def get_schedule_planning_prompt(plugin) -> str:
    identity_context, behavior_context = _build_schedule_reference_sections(plugin)
    return "\n\n".join(part for part in (identity_context, behavior_context) if part)

def _format_detail_plan_outline(plan: dict[str, Any], *, limit: int = 18) -> str:
    items = plan.get("items") if isinstance(plan, dict) else None
    if not isinstance(items, list):
        return "（暂无宏观日程）"
    lines = []
    for item in items[: max(1, limit)]:
        if not isinstance(item, dict):
            continue
        if _single_line(item.get("lifecycle_status"), 20).lower() in {"cancelled", "canceled", "取消", "已取消"}:
            continue
        time_text = _single_line(item.get("time"), 8)
        end_text = _single_line(item.get("end"), 8)
        activity = _single_line(item.get("activity"), 120)
        if time_text and activity:
            lines.append(f"- {time_text}{f'-{end_text}' if end_text else ''} {activity}")
    return "\n".join(lines) if lines else "（暂无宏观日程）"

def _build_maslow_schedule_influence_prompt(plugin) -> str:
    if not bool(runtime_persona_setting(plugin, "enable_maslow_motivation_experiment", False)):
        return ""
    if not bool(runtime_persona_setting(plugin, "enable_maslow_schedule_influence", False)):
        return ""
    strength = _safe_int(runtime_persona_setting(plugin, "maslow_motivation_strength", 35), 35, 0, 100)
    if strength <= 0:
        return ""
    influence = "轻微"
    if strength >= 70:
        influence = "明显"
    elif strength >= 40:
        influence = "适中"
    return render_prompt_sections(
        [
            prompt_section(
                key="background.schedule.maslow_influence",
                title="实验性功能：需求强化（日程影响）",
                source="planning",
                content=(
                    f"已启用需求强化功能对日程的{influence}影响,强度 {strength}/100。"
                    "它只作为隐式倾向,不要在 activity、mood 或 message_seed 里写“需求层级/马斯洛/状态层/归属层”等术语。\n"
                    "- 状态层：当拟人状态显示疲惫、困、饿、不舒服或恢复中时,日程应更轻、更慢,优先安排休息、进食、整理和低负担活动。\n"
                    "- 安全层：当最近有边界、忙碌、未回复或关系收敛线索时,减少追问、约定和高压社交,让日程转向自我消化或低打扰等待。\n"
                    "- 归属层：当存在自然续话、共同话题、关系伏笔或温和想念时,可以在少量 message_seed 里留下轻量开口,但不能每段都围绕用户。\n"
                    "- 尊重层：当有考试、生日、纪念日、项目、成果或挫败线索时,日程可以多一点准备、鼓励、复盘或认真收束。\n"
                    "- 成长层：当角色最近有创作、学习、阅读、搜索、看视频或技能成长线索时,可把空档偏向探索和推进,但不能覆盖真实日期和身份主线。\n"
                    "- 意义层：只有人格/世界观/近期材料真的支持时,才加入很轻的远望、信念或存在感余味；不要把普通一天写成哲学独白。"
                ),
            )
        ],
        mode=PromptRenderMode.LABELED_BLOCK,
    )
