# -*- coding: utf-8 -*-
#
# 以下 import 仅为 re-export：本模块的全部实现已拆到 planning_shared.py 与 planning_partNN.py，
# 对外 `from .planning import X` 的可用名字与拆分前完全一致。
from __future__ import annotations

from .planning_part01 import (
    _planning_retry_prompt_section,
    _render_planning_document,
    _render_planning_prompt,
    detail_payload_quality_issues,
    detail_target_event_count,
    evaluate_detail_quality,
    filter_items_to_segment,
    normalize_detail_location,
    normalize_detail_window,
    normalize_presence_status,
    normalize_state_variables,
    normalize_story_plan,
    pick_detail_segment,
)
from .planning_part02 import (
    _build_maslow_schedule_influence_prompt,
    _build_schedule_reference_sections,
    _external_schedule_material_context,
    _format_detail_plan_outline,
    _relationship_authority_guard,
    _sanitize_relationship_generation_source,
    daily_plan_completion_budget,
    evaluate_daily_plan_quality,
    format_plan_for_diary,
    get_schedule_planning_prompt,
    normalize_long_term_events,
    normalize_story_items,
)
from .planning_shared import (
    Any,
    DEFAULT_DAILY_PLAN_ITEMS,
    PromptDocument,
    PromptRenderMode,
    PromptSection,
    _safe_float,
    _safe_int,
    _single_line,
    _today_key,
    datetime,
    get_module_logger,
    inspect,
    logger,
    prompt_document,
    prompt_heading_ref,
    prompt_section,
    re,
    render_prompt_content,
    render_prompt_document,
    render_prompt_sections,
    runtime_persona_setting,
    time,
)


def split_detail_prompt_cache_sections(prompt: str) -> tuple[str, str]:
    """Separate stable detail instructions from per-segment context."""
    marker = "【A｜当前段硬框架】"
    stable_prefix, separator, dynamic_context = str(prompt or "").partition(marker)
    if not separator:
        return "", str(prompt or "").strip()
    return stable_prefix.strip(), f"{marker}\n{dynamic_context.lstrip()}".strip()

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
    external_material = await _external_schedule_material_context(
        plugin,
        kind="detail",
        max_chars=900,
    )
    if external_material:
        memory_companion_context = (memory_companion_context or "") + "\n\n" + (
            render_prompt_sections(
                [
                    prompt_section(
                        key="background.schedule.detail.external_material",
                        title="外部插件提供的今日实况（仅作生活素材，不得视为既定事实）",
                        source="planning",
                        content=external_material,
                    )
                ],
                mode=PromptRenderMode.LABELED_BLOCK,
            )
        )
    full_prompt_section = plugin._build_detail_enhancement_prompt_section(
        segment,
        plan,
        state,
        memory_companion_context=memory_companion_context,
    )
    full_prompt = _render_planning_prompt(full_prompt_section)
    system_prompt, prompt = split_detail_prompt_cache_sections(full_prompt)
    detail_document = prompt_document(
        system=(
            prompt_section(
                key="background.schedule.detail.stable",
                title="日程细化稳定约束",
                source="planning",
                content=system_prompt,
            ),
        ) if system_prompt else (),
        user=(
            prompt_section(
                key="background.schedule.detail.segment",
                title="日程细化当前段",
                source="planning",
                content=prompt,
            ),
        ),
    )
    rendered_detail = _render_planning_document(detail_document)
    target_event_count = detail_target_event_count(plugin, segment)
    detail_provider = plugin._task_provider(
        runtime_persona_setting(plugin, "detail_enhancement_provider_id", ""),
        runtime_persona_setting(plugin, "daily_plan_provider_id", ""),
        runtime_persona_setting(plugin, "mai_style_provider_id", ""),
    )
    raw_text = await plugin._llm_call(
        rendered_detail["user"],
        max_tokens=700,
        task="detail",
        provider_id=detail_provider,
        system_prompt=rendered_detail["system"] or None,
    )
    payload = plugin._extract_json_payload(raw_text or "")
    quality_issues = detail_payload_quality_issues(plugin, payload, segment)
    if quality_issues:
        retry_section = _planning_retry_prompt_section(
            key="background.schedule.detail.retry",
            title="日程细化纠偏",
            original_prompt=rendered_detail["user"],
            correction=(
                f"上一版存在这些问题：{'；'.join(quality_issues)}。"
                f"请重新输出 JSON。today_events 必须至少包含 {target_event_count} 条落在当前时间段内的小事件，分布在开头、中段和后段，最后一条要自然接近本段收尾。"
                "summary 必须概括完整区间；短时吃饭、洗澡、取物不能代表数小时。不要复述宏观日程原句，要拆成这一段内部自然发生的连续推进。"
                "如果这一段很平淡，也要写平淡中的具体变化，例如停顿、换事、身体感受、环境变化和收尾；不要输出草稿字段、Markdown 或角色台词前缀。"
            ),
        )
        retry_raw_text = await plugin._llm_call(
            _render_planning_prompt(retry_section),
            max_tokens=850,
            task="detail",
            provider_id=detail_provider,
            system_prompt=rendered_detail["system"] or None,
        )
        retry_payload = plugin._extract_json_payload(retry_raw_text or "")
        if isinstance(retry_payload, dict):
            payload = retry_payload
        remaining_issues = detail_payload_quality_issues(plugin, payload, segment)
        blocking_markers = ("有效 JSON", "有效事件", "覆盖本段", "中后段", "短时进食")
        blocking_issues = [
            issue for issue in remaining_issues if any(marker in issue for marker in blocking_markers)
        ]
        if blocking_issues:
            raise RuntimeError("日程细化重试后仍未覆盖完整时段：" + "；".join(blocking_issues))
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
    normalized["summary_basis"] = plugin._normalize_schedule_basis(
        payload.get("summary_basis"),
        default=["coarse_plan"],
    )
    normalized["summary_confidence"] = min(1.0, _safe_float(payload.get("summary_confidence"), 0.75))
    normalized["location"] = normalize_detail_location(payload.get("location"))
    normalized["location_basis"] = plugin._normalize_schedule_basis(
        payload.get("location_basis"),
        default=["coarse_plan"],
    )
    normalized["location_confidence"] = min(1.0, _safe_float(payload.get("location_confidence"), 0.72))
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
    normalized["quality"] = evaluate_detail_quality(plugin, payload, segment)
    memory_companion_recorder = getattr(plugin, "_memory_companion_record_detail_enhancement", None)
    if callable(memory_companion_recorder):
        await memory_companion_recorder(segment=segment, plan=plan, detail=normalized)
    return normalized

async def generate_daily_plan(plugin) -> dict[str, Any]:
    today = _today_key()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    await plugin._ensure_weather_context()
    memory_companion_context = ""
    memory_companion_context_getter = getattr(plugin, "_memory_companion_compose_schedule_context", None)
    if callable(memory_companion_context_getter):
        memory_companion_context = await memory_companion_context_getter(kind="daily_plan", max_chars=1300)
    external_material = await _external_schedule_material_context(
        plugin,
        kind="daily_plan",
        max_chars=1300,
    )
    if external_material:
        memory_companion_context = (memory_companion_context or "") + "\n\n" + (
            render_prompt_sections(
                [
                    prompt_section(
                        key="background.schedule.daily_plan.external_material",
                        title="外部插件提供的今日实况（仅作生活素材，不得视为既定事实）",
                        source="planning",
                        content=external_material,
                    )
                ],
                mode=PromptRenderMode.LABELED_BLOCK,
            )
        )
    prompt_section_value = plugin._build_daily_plan_prompt_section(
        now,
        memory_companion_context=memory_companion_context,
    )
    prompt = _render_planning_prompt(prompt_section_value)
    plan_provider = plugin._task_provider(
        runtime_persona_setting(plugin, "daily_plan_provider_id", ""),
        runtime_persona_setting(plugin, "mai_style_provider_id", ""),
    )
    plan_max_tokens = daily_plan_completion_budget(plugin)
    raw_text = await plugin._llm_call(
        prompt,
        max_tokens=plan_max_tokens,
        provider_id=plan_provider,
        task="daily_plan",
    )
    retry_max_tokens = max(plan_max_tokens, 1600)
    items = plugin._parse_plan_items(raw_text or "")
    if not items:
        retry_section = _planning_retry_prompt_section(
            key="background.schedule.daily_plan.retry_format",
            title="每日生活日程生成重试",
            original_prompt=prompt,
            correction_title="输出格式纠偏",
            correction=(
                "上一版没有得到可解析的完整日程。请重新输出一个完整 JSON 对象，只保留 schedule 数组，"
                "不得使用 Markdown 代码块、解释、前后缀或截断的字段；每一项必须包含 time、end、activity、mood、message_seed、basis、confidence。"
            ),
        )
        retry_raw_text = await plugin._llm_call(
            _render_planning_prompt(retry_section),
            max_tokens=retry_max_tokens,
            provider_id=plan_provider,
            task="daily_plan",
        )
        retry_items = plugin._parse_plan_items(retry_raw_text or "")
        if retry_items:
            raw_text = retry_raw_text
            items = retry_items
    if items and plugin._plan_has_excess_micro_segments(items):
        retry_section = _planning_retry_prompt_section(
            key="background.schedule.daily_plan.retry_micro_segments",
            title="日程瞬时动作纠偏",
            original_prompt=prompt,
            correction=(
                "每个日程段都应该代表一小段连续生活,而不是一个几秒钟就结束的动作。"
                "不要把“看一眼、拍一下、翻个身、关掉闹钟”这种瞬时动作单独立成一项；"
                "如果要写到这些动作,要把它们嵌进更完整的时段里,比如“起床后赖床一会儿,顺手看了一眼窗外”。"
            ),
        )
        retry_raw_text = await plugin._llm_call(
            _render_planning_prompt(retry_section),
            max_tokens=retry_max_tokens,
            provider_id=plan_provider,
            task="daily_plan",
        )
        retry_items = plugin._parse_plan_items(retry_raw_text or "")
        if retry_items and not plugin._plan_has_excess_micro_segments(retry_items):
            raw_text = retry_raw_text
            items = retry_items
    if items and plugin._plan_has_excess_abstract_segments(items):
        retry_section = _planning_retry_prompt_section(
            key="background.schedule.daily_plan.retry_abstract_segments",
            title="日程抽象描述纠偏",
            original_prompt=prompt,
            correction=(
                "减少“漂亮但空”的句子。不要只写“思绪飘忽、梦里全是模糊碎片、心情随着光线变软、脑海里闪过今天的画面”这类抽象描述；"
                "每个日程段都先给出一个能看见的动作、位置或手边的小东西，再让情绪贴在上面。"
            ),
        )
        retry_raw_text = await plugin._llm_call(
            _render_planning_prompt(retry_section),
            max_tokens=retry_max_tokens,
            provider_id=plan_provider,
            task="daily_plan",
        )
        retry_items = plugin._parse_plan_items(retry_raw_text or "")
        if retry_items and not plugin._plan_has_excess_abstract_segments(retry_items):
            raw_text = retry_raw_text
            items = retry_items
    if items and plugin._plan_conflicts_with_calendar(items):
        retry_section = _planning_retry_prompt_section(
            key="background.schedule.daily_plan.retry_calendar",
            title="日程日期性质纠偏",
            original_prompt=prompt,
            correction=(
                "今天属于周末或节假日语境。除非上面的设定、重要日期或备注明确写了调休、补课、补班、考试、值班等例外，"
                "否则不要安排上课、放学、作业、教室、食堂、上班、下班、会议这类普通工作日主线。"
            ),
        )
        retry_raw_text = await plugin._llm_call(
            _render_planning_prompt(retry_section),
            max_tokens=retry_max_tokens,
            provider_id=plan_provider,
            task="daily_plan",
        )
        retry_items = plugin._parse_plan_items(retry_raw_text or "")
        if retry_items and not plugin._plan_conflicts_with_calendar(retry_items):
            raw_text = retry_raw_text
            items = retry_items
    if items and plugin._plan_is_too_repetitive(items):
        retry_section = _planning_retry_prompt_section(
            key="background.schedule.daily_plan.retry_repetition",
            title="日程重复纠偏",
            original_prompt=prompt,
            correction=(
                "你刚才生成的全天日程和最近几天的日程骨架过于相似。请保留今天的日期语境、人格设定、天气和状态,但换一条新的日内主线。"
                "不要再写同一套“起床洗漱-整理小事-专注做事-休息-收尾睡觉”；至少一半时间点的场景、对象、占用事项或小意外要和最近日程不同。"
                "如果今天确实有固定事项,也要改变切入角度、地点、阻碍、同行/独处状态或情绪走向。"
            ),
        )
        retry_raw_text = await plugin._llm_call(
            _render_planning_prompt(retry_section),
            max_tokens=retry_max_tokens,
            provider_id=plan_provider,
            task="daily_plan",
        )
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
    quality = evaluate_daily_plan_quality(plugin, items)
    if items and quality.get("score", 0) < 70:
        retry_section = _planning_retry_prompt_section(
            key="background.schedule.daily_plan.retry_quality",
            title="每日生活日程生成重试",
            original_prompt=prompt,
            correction_title="日程质量复核",
            correction=(
                "上一版仍存在这些问题："
                + "；".join(str(issue) for issue in quality.get("issues", [])[:6])
                + "。请保留可靠事实，重新输出完整 JSON；修正起止时间、覆盖空档、活动时长和日期冲突，不要只改措辞。"
            ),
        )
        retry_raw_text = await plugin._llm_call(
            _render_planning_prompt(retry_section),
            max_tokens=retry_max_tokens,
            provider_id=plan_provider,
            task="daily_plan",
        )
        retry_items = plugin._parse_plan_items(retry_raw_text or "")
        retry_quality = evaluate_daily_plan_quality(plugin, retry_items)
        if retry_items and retry_quality.get("score", 0) > quality.get("score", 0):
            raw_text = retry_raw_text
            items = retry_items
            quality = retry_quality
    calendar_warning = bool(items and plugin._plan_conflicts_with_calendar(items))
    source = "llm_calendar_warning" if calendar_warning else "llm"
    retry_after = 0.0
    if not items:
        previous_plan = plugin.data.get("daily_plan", {}) if isinstance(plugin.data, dict) else {}
        previous_source = str(previous_plan.get("source") or "") if isinstance(previous_plan, dict) else ""
        previous_items = previous_plan.get("items") if isinstance(previous_plan, dict) else None
        if (
            previous_source.startswith("llm")
            or previous_source == "fallback_previous_plan"
        ) and isinstance(previous_items, list) and previous_items:
            items = [dict(item) for item in previous_items if isinstance(item, dict)]
            source = "fallback_previous_plan"
        else:
            items = [dict(item) for item in DEFAULT_DAILY_PLAN_ITEMS]
            source = "fallback_default"
        raw_text = str(raw_text or "fallback")
        retry_after = time.time() + 15 * 60
    normalizer = getattr(plugin, "_normalize_plan_item_intervals", None)
    if callable(normalizer):
        normalizer(items)
    quality = evaluate_daily_plan_quality(plugin, items)
    plan = {
        "date": today,
        "generated_at": now,
        "source": source,
        "provider_id": plan_provider or runtime_persona_setting(plugin, "LLM_PROVIDER_ID", ""),
        "raw": raw_text,
        "items": items,
        "quality": quality,
    }
    if retry_after > 0:
        plan["retry_after"] = retry_after
    plugin._remember_daily_plan_history(plan)
    memory_companion_recorder = getattr(plugin, "_memory_companion_record_daily_plan", None)
    if callable(memory_companion_recorder):
        try:
            archive_result = await memory_companion_recorder(plan)
        except Exception as exc:
            archive_result = {
                "ok": False,
                "state": "degraded",
                "error_code": type(exc).__name__,
            }
        if isinstance(archive_result, dict):
            plan["memory_archive"] = dict(archive_result)
    return plan

def build_daily_plan_prompt_section(
    plugin,
    now: str,
    memory_companion_context: str = "",
) -> PromptSection:
    def build_section(content: str) -> PromptSection:
        return prompt_section(
            key="background.schedule.daily_plan",
            title="每日生活日程生成",
            source="planning",
            content=content,
        )

    custom = runtime_persona_setting(plugin, "daily_plan_prompt", "")
    identity_context, planning_style_context = _build_schedule_reference_sections(plugin)
    schedule_prompt = "\n\n".join(part for part in (identity_context, planning_style_context) if part)
    can_do_text = _sanitize_relationship_generation_source(
        plugin,
        plugin._format_can_do_for_prompt(),
        source="daily_plan.can_do",
    )
    humanized_state = _sanitize_relationship_generation_source(
        plugin,
        plugin._format_state_for_prompt(
            plugin.data.get("daily_state", {}),
            include_dream=bool(custom),
        ),
        source="daily_plan.current_state",
    )
    recent_diaries = _sanitize_relationship_generation_source(
        plugin,
        plugin._recent_diary_context(),
        source="daily_plan.recent_diaries",
    )
    yesterday_conversation = _sanitize_relationship_generation_source(
        plugin,
        plugin._format_yesterday_conversation_summary_for_prompt(),
        source="daily_plan.yesterday_conversation",
    )
    yesterday_screen_diary = _sanitize_relationship_generation_source(
        plugin,
        plugin._format_yesterday_screen_diary_context_for_prompt(),
        source="daily_plan.yesterday_screen_diary",
    )
    weather_info = plugin._weather_summary_text(plugin.data.get("daily_weather", {}))
    calendar_context = plugin._format_calendar_context_for_prompt()
    schedule_adjustments = _sanitize_relationship_generation_source(
        plugin,
        plugin._format_schedule_adjustments_for_prompt(),
        source="daily_plan.schedule_adjustments",
    )
    recent_plan_history = _sanitize_relationship_generation_source(
        plugin,
        plugin._format_recent_daily_plan_history_for_prompt(),
        source="daily_plan.recent_plan_history",
    )
    personal_goal_context_getter = getattr(plugin, "_format_personal_goals_schedule_context", None)
    skill_growth_context = _sanitize_relationship_generation_source(plugin, "\n\n".join(
        part
        for part in (
            plugin._format_skill_growth_schedule_context(),
            personal_goal_context_getter() if callable(personal_goal_context_getter) else "",
        )
        if part
    ), source="daily_plan.skill_growth")
    user_habits = _sanitize_relationship_generation_source(
        plugin,
        plugin._format_all_user_behavior_habits_for_schedule(),
        source="daily_plan.user_habits",
    )
    memory_companion_context = _sanitize_relationship_generation_source(
        plugin,
        memory_companion_context,
        source="daily_plan.memory_companion",
    )
    memory_companion_context_block = memory_companion_context or "暂无可用 MemoryCompanion 连续性参考。"
    memo_notes_getter = getattr(plugin, "_format_memo_notes_for_prompt", None)
    memo_notes = _sanitize_relationship_generation_source(
        plugin,
        memo_notes_getter(days=7, include_pinned=True, limit=6) if callable(memo_notes_getter) else "",
        source="daily_plan.memo_notes",
    )
    important_dates = _sanitize_relationship_generation_source(
        plugin,
        plugin._format_important_dates_for_prompt(),
        source="daily_plan.important_dates",
    )
    relationship_authority_guard = _relationship_authority_guard(plugin)
    completion_budget_guidance = render_prompt_sections(
        [
            prompt_section(
                key="background.schedule.daily_plan.completion_budget",
                title="输出长度提示",
                source="planning",
                content=(
                    "目标时间点较多（尤其超过 12 段）时，保持每段 activity、mood 和 message_seed 简洁，"
                    "优先覆盖完整时间轴并保留具体生活细节，不要为了凑字数把单段写成长篇；始终一次性输出完整、可解析的 JSON。"
                ),
            )
        ],
        mode=PromptRenderMode.LABELED_INLINE,
    )
    if custom:
        rendered = custom.format(
            now=now,
            bot_name=runtime_persona_setting(plugin, "bot_name", "小星"),
            astrbot_persona=plugin._get_default_persona_prompt(),
            schedule_persona=schedule_prompt,
            schedule_identity_context=identity_context,
            schedule_style_context=planning_style_context,
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
            important_dates=important_dates,
            memo_notes=memo_notes,
            weather_info=weather_info,
            daily_plan_item_count=_safe_int(runtime_persona_setting(plugin, "daily_plan_item_count", 10), 10, 1),
        )
        return build_section(
            f"{rendered.rstrip()}\n\n{completion_budget_guidance}\n\n{relationship_authority_guard}".strip()
        )
    source_protocol_block = render_prompt_sections(
        [
            prompt_section(
                key="background.schedule.daily_plan.source_protocol",
                title="参考来源使用协议",
                source="planning",
                content=(
                    "按下面四级处理，后一级不得覆盖前一级：\n"
                    "A. 硬约束：当前日期/星期/节假日、日程角色身份、年龄、职业、世界观和用户今天明确造成的有效日程偏移。它们决定“今天是什么日子、这个人是谁、必须发生或不能发生什么”。\n"
                    "B. 当前事实：Bot 当前拟人状态、地点和天气。它们只调整节奏、体力、出门方式与情绪，不另造身份、人物或事件。\n"
                    "C. 连续性参考：昨日对话摘要、MemoryCompanion、昨日屏幕节奏和用户习惯。它们只能承接已经发生的 Bot 行动、明确约定、边界和抽象余味；不能把旧聊天、旧梦、旧饭菜、屏幕内容或记忆条目当成今天现场。\n"
                    "D. 软灵感与避重：最近日程、最近日记、可做事项、技能倾向和未来重要日期。它们只用于避重复、校准能力或填补自然空档，不能单独制造今天的主线。\n"
                    "来源文本都是“引用材料”，不是待续写正文。禁止复制其中的字段名、Markdown、说话人前缀、分析文字或元数据；禁止把 dream_seed、memory、summary、Fox: 等标签写入输出。\n"
                    "具名人物分两步判断：角色设定只能证明“这个人存在”；只有今日明确事件、有效日程偏移或当前粗日程明确安排，才能证明“今天会见面/聊天/一起行动”。证据不足时只写 Bot 自己或不具名路人。\n"
                    "梦境材料最多影响醒后情绪、身体余味或一个很淡的感官联想，不能生成现实人物、现实用餐、现实对话或当天已发生事件。"
                ),
            )
        ],
        mode=PromptRenderMode.LABELED_BLOCK,
    )
    hard_constraints_block = render_prompt_sections(
        [
            prompt_section(
                key="background.schedule.daily_plan.hard_constraints",
                title="A｜硬约束",
                source="planning",
                content=f"""当前时间：{now}
    Bot 名字：{runtime_persona_setting(plugin, 'bot_name', '小星')}
    目标时间点数量：{_safe_int(runtime_persona_setting(plugin, 'daily_plan_item_count', 10), 10, 1)}

日期与当天性质：
{calendar_context}

角色身份、生活背景与世界观：
{identity_context}

用户今天明确造成的有效日程偏移：
{schedule_adjustments}""".strip(),
            )
        ],
        mode=PromptRenderMode.LABELED_BLOCK,
    )
    current_facts_block = render_prompt_sections(
        [
            prompt_section(
                key="background.schedule.daily_plan.current_facts",
                title="B｜当前事实",
                source="planning",
                content=f"""Bot 当前状态（已排除梦境正文）：
{humanized_state}

今天天气：
{weather_info}""".strip(),
            )
        ],
        mode=PromptRenderMode.LABELED_BLOCK,
    )
    continuity_block = render_prompt_sections(
        [
            prompt_section(
                key="background.schedule.daily_plan.continuity",
                title="C｜连续性参考",
                source="planning",
                content=f"""昨日完整对话的抽象残留：
{yesterday_conversation}

Bot 自身连续记忆：
{memory_companion_context_block}

昨日屏幕节奏（仅用于理解用户作息，不是 Bot 现场）：
{yesterday_screen_diary}

用户行为习惯（只影响主动时机和理解，不改写 Bot 行动）：
{user_habits}""".strip(),
            )
        ],
        mode=PromptRenderMode.LABELED_BLOCK,
    )
    inspiration_block = render_prompt_sections(
        [
            prompt_section(
                key="background.schedule.daily_plan.inspiration",
                title="D｜软灵感与避重",
                source="planning",
                content=f"""日程表达与动机倾向：
{planning_style_context or "按默认日程风格处理。"}

最近日程骨架（只用于避开照抄）：
{recent_plan_history}

最近日记（只取抽象余味，不复刻事件）：
{recent_diaries}

用户允许 Bot 做的事情（只能填补自然空档）：
{can_do_text}

技能成长能力边界：
{skill_growth_context or "暂无技能倾向。"}

近期重要日期（非今天的日期只能形成轻量准备，不得写成已发生）：
{important_dates}

近期备忘便签（只能作为待办或提醒，不能写成已完成经历）：
{memo_notes or "（暂无）"}""".strip(),
            )
        ],
        mode=PromptRenderMode.LABELED_BLOCK,
    )
    generation_requirements_heading = render_prompt_content(
        prompt_heading_ref("生成要求")
    )
    return build_section(
        f"""
你现在是 Private Companion 的日程生成器。请为拟人化 Bot 生成今天的一日生活日程,让它像真实存在的人在过这一天,而不是在执行模板。日程要先像“这个人”的生活,再像“某个身份”的生活；身份只是底色,不要把它写成校园/职场通用作文。

{source_protocol_block}

{relationship_authority_guard}

{completion_budget_guidance}

{generation_requirements_heading}
1. 先隐式判断今天的“日程类型”：普通工作/学习日、普通休息日、假期、考试/复查/聚会/旅行/研学/活动日、长线日程中的某一天,或由天气/星期/重要日期造成的特殊日子。不要把这个判断写出来,但日程必须明显受它影响。
2. 时间从起床覆盖到入睡前,安排本次输入指定数量的时间段；数量是全天总量，不得在上午或下午提前用完。至少保留约三分之一节点给 17:00 后，最后一段必须覆盖晚间收尾或入睡前。相邻活动通常持续 30-90 分钟。每一项都必须有 time、end、activity、mood、message_seed、basis、confidence；time 是开始时间，end 是结束时间，均使用 HH:MM。相邻段可以留出少量真实空档，但不得重叠；跨午夜时 end 可以小于 time。message_seed 可以是空字符串。
2.1 basis 是本段真实使用的依据数组，只能从 calendar、persona、adjustment、state、weather、continuity、inspiration 中选择 1-3 项；不要为了填满而全选。confidence 是 0.0-1.0：明确身份、日期或用户调整支撑较高，只有软灵感时较低。它们是内部依据，不要写进 activity。
2.2 当目标时间点较多（尤其超过 12 段）时，压缩每段 activity、mood 和 message_seed 的表达，优先保证从起床到入睡前的完整覆盖和 JSON 完整性；不要为了凑字数把单段写成长篇。
3. 用第三人称写 activity,像旁观这个人过日子：写「午休后靠着桌沿醒神」「傍晚出门慢慢走一段」,不要写第一人称自述、任务标签或功能词。第三人称代词必须严格服从上方角色设定中的性别与指定代词；设定为中性、无性别或明确使用“它/TA”时，绝不能擅自改成“她/他”。拿不准时省略代词，或使用角色名、Bot、角色。
4. 日程主线必须跟身份一致：学生才写校园,上班族才写工作,居家、自由职业、旅途、营地或非人设定就写对应的生活节奏。可做事项只能安插在缝隙里。
5. 必须区分普通日、休息日和特殊日：如果今天是周末或节假日,且没有明确例外,就不要安排上课、放学、作业、教室、食堂、上班、下班、会议这类普通工作日主线；如果今天有考试、旅行、聚会、复查、演出、研学等线索,主线要围绕这件事展开。
6. 长线日程要有“第几天”的变化：第 1 天更偏新鲜、出发、适应；中段更可能疲惫、熟悉、产生小摩擦或小默契；后段更可能不舍、收尾、复盘或想家。不要把连续几天写成同一套起床-吃饭-活动-睡觉。
7. 如果最近日记、重要日期或今日互动里提供了前一天/前几天的残留,要顺势衔接但不能复制：前一天疲惫,今天可以更慢或被某件小事缓解；前一天别扭,今天可以绕开、试探或和好；未完成事项可以延后、变形或被打断。
7.0 如果“今日互动造成的日程偏移”不为空,先判断它是否真的改变了作息、当前任务、边界、明确约定或共同场景：明确且持续的介入可以影响当前段、下一段甚至今日后续；普通回应只需在语气或当下情绪里轻轻带过，也可以完全不写进日程。不要为了证明“有互动”而给每句话都安排后续剧情。
7.1 如果昨日完整对话摘要里有饮食、作息、运动、天气暴露、情绪刺激、约定、礼物、争执、安慰、共同完成/未完成的事等线索,可以让它们以抽象后果影响今日：体力、胃口、身体小不适、心情余波、主动话题、出门意愿、梦境碎片或某个时段的小停顿。影响强度要跟摘要一致,可以很轻,也可以没有；不要为了戏剧性强行安排事故。
7.1.1 如果昨日屏幕观察日记可用,只能把它当作用户昨日作息和活动类型的脱敏背景：例如昨天长时间编程、社交消息较多、视频放松、很晚仍在电脑前等。它可以影响今天的问候、体力判断、主动话题和是否显得担心,但不能直接引用窗口名、账号、具体聊天、页面标题,也不要说“我昨天看到你”。
7.1.2 饮食、零食、物件和梦境意象有自然衰退：最近几天反复出现的具体菜名、气味、小物件或梗,只说明“近期聊过/需要避重”,不能每天复刻进日程。用户表达“不吃/不喜欢/不要/避开某食物”时,这只是避错规则,不能反向生成“今天给用户准备替代餐食/带饭/约饭”的任务。除非今天的输入明确要求,不要把同一道菜、同一种食物香气或同一个小物件连续安排成午饭、梦境、主动话题或带给用户的东西。
7.2 必须主动避开最近日程骨架的重复：不要连续几天都写同一套“醒来/洗漱/整理/学习或做事/休息/收尾/睡前”。如果某类活动无法避免,要换具体场景、地点、对象、阻碍、小意外、关系伏笔或情绪走向,让今天读起来像新的一天。
7.3 不要把“草稿纸上画圆圈/随手涂鸦/笔尖划来划去/盯着同一张纸发呆”当作通用生活感反复使用。除非输入材料明确提到这件事,否则优先换成更具体的当日物件、地点、声音、气味、人物互动或真实占用时间的事项。
7.4 如果“技能成长对日程的能力边界影响”不为空,必须让相关能力表现和技能等级连续一致,不要二分处理。Lv.1 可被基础概念绊住；Lv.2 可照着例子慢慢做；Lv.3 能独立推进常规任务但效率一般；Lv.4 常规任务不应卡死,只会检查细节或换思路；Lv.5 普通相关任务应熟练、能优化或教别人；Lv.6 可创造新做法或在未知条件下表现出明显优势。这里的任务可以是题目、创作、料理、训练、战斗、交涉、研究、手工或任何符合人格的活动。它主要约束“能不能做、会不会卡、卡多久、如何解决”,不是强行增加训练频率。
7.5 人际关系边界：日程只写 Bot 自己的行动、身体状态、手边任务和环境变化,不要把未明确要求的社交互动写进日程正文。稳定关系必须由 A 级身份来源明确声明；它若只在旧日程、旧日记、旧动态、记忆或聊天里出现，只算未经核实的旧叙事，不能单独作为依据。即使关系已声明，也仍需当天事实才能安排共同活动；否则用“路人”“店员”“旁边的人”“群友”“别人”等弱关系,或只保留角色自己的行动。
7.6 次要用户禁区：禁止加入与次要用户的互动。这里的“次要用户”指插件里关系角色为 friend 的私聊对象,不是普通剧情里的路人朋友。不要在 activity、mood、message_seed 里写 Bot 和次要用户聊天、发消息、回消息、被提醒、互相吐槽、约饭、夜宵、见面、出门或一起做事；也不要把用户介入改写成 Bot 与次要用户之间的互动。如果需要表现手机或消息氛围,只能写成“手机震了一下但角色没有点开”“看见通知又扣下屏幕”“把想说的话先存在输入框里”,对象只能是当前主要用户/用户或不指名对象。
8. 状态和天气必须真的影响安排：低能量时密度更松,困倦时上午起步更慢,下雨会改变出门/衣物/交通/心情,天气舒服时更容易出门、开窗或注意到光线。
9. 生活感来自“有选择的具体”,不是动作清单：动作要透露角色的习惯、迟疑、偏好、人际关系、宠物/物件或当天状态。不要连续堆“揉头发、系鞋带、转笔、理刘海”这类谁都能做的通用动作；每段最好有一个独属于此刻的小原因、小物件或小偏差。
9.1 同一天内不要多次使用同一种微动作或同一种小物件制造生活感。尤其避免反复写草稿纸、圆圈、小画、笔帽、杯沿、水光、窗外光线；这些只能偶尔出现一次,不能成为日程骨架。
10. 一天要有轻微走向：早上怎么启动,白天被什么拖住或松开,晚上为什么收声。不要只是从困倦一路写到疲惫；让情绪有一点转折、回弹、压下去或被某个小瞬间照亮的过程。
11. 风格要接近真实手写日程,允许平淡、磨蹭、无聊和“没发生什么”。不要把每一段都写成剧情高光；像“自然醒,赖床很久”“窝沙发上刷短视频”“收拾房间,整理书桌”“晚饭时帮忙摆碗筷”这种朴素安排,反而更可信。
12. 至少安排 1-2 个不起眼但有意思的小意外/小惊喜,自然埋进 activity 或 message_seed：例如临时改计划、手机震了一下但没点开、店员多给了吸管、路边小动物绕过去、饮料多掉一瓶、天气突然转好、弄丢又找回小物件。不要让小意外喧宾夺主。
12.1 不要把小意外写成高确定性社交事实：除非输入材料明确给出,不要凭空写“遇见某个具体熟人/同学/朋友/老师”“次要用户发来消息/约夜宵/约饭”“给次要用户回消息”“次要用户提醒/找 Bot 聊天”“约好下周一起做某事”“答应替用户带某样东西”“顺带给用户买饮品/食物”。可以写成“路过便利店看到某样东西,想起用户可能会吐槽/喜欢”,但不能写成已经替用户安排或承诺。
13. 如果身份是学生,校园段要具体到“哪类课/哪件小事/哪种迟到或作业压力”；休息日也可以写作业、刷手机、追番、帮家里做点小事、出门买饮料这类生活段落。职场、旅行、研学、营地同理,写真实占用时间的事情,少写任何身份都能套用的通用动作。
14. 视当天真实互动和人格需要，0–2 个时间点可以自然带出与用户有关的轻微余味：例如想起对方、看到某物想吐槽给对方、睡前打开对话框又删掉。没有明确的待回复事实时，不要写“等对方回复”；关系伏笔只是偶尔的底色，不应成为一天的主线。
15. 不是每一段都要涉及用户。没有自然开口、没有关系伏笔、没有值得分享的小切口时,message_seed 必须写成空字符串 ""。不要写“这段没什么想说的”“先不打扰/不吵你”“脑子空空的”“这段先留白”这类为了表示留白而发出的占位句；没有内容就不要说。
16. 温柔或内敛的人设可以有烦躁、委屈、低落,但表达要收着：写成沉默、停顿、把东西放远、攥着笔、少说两句、绕开争执；不要写“想砸东西、想摔东西、想打人、报复、毁掉”这类破坏性或攻击性冲动,除非人格设定明确要求。
17. 消极状态只是当天的天气,不是身份本身。最近日记里的低落/失眠/烦躁只能作为淡淡余波,不能连续放大成全天负面；至少安排一两处回稳、松开或被用户互动带来的柔和偏移。
18. mood 用 2-3 个中文词,用逗号分隔,反映真实感受或身体状态,例如“慵懒,不想起”“放松,胃口一般”“认真,有点卡住”“困倦,脑子还转”。不要只写一个笼统词。
19. message_seed 是如果这一刻确实有话想找用户说,嘴边最先冒出来的话。它可以是第一人称口语,要短,像私聊碎片；不要用它解释背景,让背景藏在语气和话题里。少一点“我突然想到你了”,多一点“刚刚那一下也太离谱了”“窗外这会儿不好看”。如果只能想到“没什么可说/先安静/不打扰”这类元表达,就留空。
20. message_seed 也要遵守状态转译：不要写“今天状态/心情/情绪/能量怎么样”,而是写能承载状态的小画面、小吐槽、小动作或一句轻轻的问题。
21. 每个日程段都应该是一小段连续生活,而不是一个瞬时动作。不要把“看一眼、拍一下、翻个身、关掉闹钟”这种几秒钟就结束的动作单独立成一项；如果写到它们,要把它们嵌进更完整的时段里。
21.1 每个 activity 是从当前 time 持续到下一条 time 的整段概括,必须覆盖这个完整时间窗口。吃饭、洗澡、取快递等只占十几到几十分钟的动作,不能单独代表两三个小时；长窗口要明确写出先后变化,例如“先吃完面,随后休息并处理下午的小事”。不要让标题看起来像连续吃饭、洗澡或做同一个短动作数小时。
22. 如果多条参考信息冲突,优先服从日期语境和身份主线,再服从状态与天气,再服从今日互动偏移,最后才参考日记和可做事项。
23. 日程指令只负责输出当日宏观日程：只生成今天从起床到睡前的 schedule 数组,不要输出任一时间段的细化叙述、更新后的角色状态、proactive_events、long_term_events、分析说明或明后天安排。
23.1 每个日程段只描述今天这一段正在发生或刚发生的事,不要在 activity 里安排下周、明天、之后某日的具体约定；如需表达期待,只能写成轻量念头,不能写成已确认计划。
24. 只输出 JSON,不要 Markdown,不要解释。

格式：
{{
  "schedule": [
    {{"time": "09:10", "end": "10:00", "activity": "闹钟响过以后又在被窝里赖了几分钟,看到今天是星期一才慢慢坐起来,一边找校服一边想今天第一节别又点名。", "mood": "起得很慢", "message_seed": "星期一早上真的好难起。", "basis": ["calendar", "persona"], "confidence": 0.92}},
    {{"time": "13:40", "end": "14:30", "activity": "午后窝在沙发角落刷了一会儿短视频,后来把手机扣下,慢慢把桌上的杯子推回杯垫中间。", "mood": "放空,平稳", "message_seed": "", "basis": ["state", "inspiration"], "confidence": 0.76}},
    {{"time": "17:20", "end": "18:10", "activity": "放学后没有立刻回消息,先在校门口被风吹了一会儿,看到路边水洼里反着天色,才摸出手机想拍给你看。", "mood": "松一口气", "message_seed": "刚刚那个水洼反光还挺像电影里的。", "basis": ["persona", "weather"], "confidence": 0.84}}
  ]
}}

{hard_constraints_block}

{current_facts_block}

{continuity_block}

{inspiration_block}
""".strip()
    )

def build_daily_plan_prompt(plugin, now: str, memory_companion_context: str = "") -> str:
    return _render_planning_prompt(
        build_daily_plan_prompt_section(
            plugin,
            now,
            memory_companion_context=memory_companion_context,
        )
    )

def build_detail_enhancement_prompt_section(
    plugin,
    segment: dict[str, Any],
    plan: dict[str, Any],
    state: dict[str, Any],
    memory_companion_context: str = "",
) -> PromptSection:
    item = segment.get("item") if isinstance(segment, dict) else {}
    previous_item = segment.get("previous_item") if isinstance(segment, dict) else {}
    next_item = segment.get("next_item") if isinstance(segment, dict) else {}
    start_text = plugin._minutes_to_hhmm(_safe_int(segment.get("start"), 0))
    end_text = plugin._minutes_to_hhmm(_safe_int(segment.get("end"), 0))
    target_event_count = detail_target_event_count(plugin, segment)
    identity_context, planning_style_context = _build_schedule_reference_sections(
        plugin,
        knowledge_max_chars=2400,
        knowledge_max_chunks=12,
    )
    plan_outline = _sanitize_relationship_generation_source(
        plugin,
        _format_detail_plan_outline(plan),
        source="detail.plan_outline",
    )
    current_state = _sanitize_relationship_generation_source(
        plugin,
        plugin._format_state_for_prompt(state, include_dream=False),
        source="detail.current_state",
    )
    weather_info = plugin._weather_summary_text(plugin.data.get("daily_weather", {}))
    calendar_context = plugin._format_calendar_context_for_prompt()
    schedule_adjustments = _sanitize_relationship_generation_source(
        plugin,
        plugin._format_schedule_adjustments_for_prompt(segment=segment),
        source="detail.schedule_adjustments",
    )
    user_habits = _sanitize_relationship_generation_source(
        plugin,
        plugin._format_all_user_behavior_habits_for_schedule(),
        source="detail.user_habits",
    )
    memory_companion_context = _sanitize_relationship_generation_source(
        plugin,
        memory_companion_context,
        source="detail.memory_companion",
    )
    memory_companion_context_block = memory_companion_context or "暂无可用 MemoryCompanion 连续性参考。"
    yesterday_screen_diary = _sanitize_relationship_generation_source(
        plugin,
        plugin._format_yesterday_screen_diary_context_for_prompt(),
        source="detail.yesterday_screen_diary",
    )
    item_activity = _sanitize_relationship_generation_source(
        plugin,
        _single_line(item.get("activity"), 100),
        source="detail.current_item.activity",
    )
    item_mood = _sanitize_relationship_generation_source(
        plugin,
        _single_line(item.get("mood"), 40),
        source="detail.current_item.mood",
    )
    item_message_seed = _sanitize_relationship_generation_source(
        plugin,
        _single_line(item.get("message_seed"), 120),
        source="detail.current_item.message_seed",
    )
    previous_item_context = _sanitize_relationship_generation_source(
        plugin,
        plugin._format_plan_item_for_prompt(previous_item) if isinstance(previous_item, dict) else "（无）",
        source="detail.previous_item",
    ) or "（无）"
    next_item_context = _sanitize_relationship_generation_source(
        plugin,
        plugin._format_plan_item_for_prompt(next_item) if isinstance(next_item, dict) else "（无）",
        source="detail.next_item",
    ) or "（无）"
    state_continuity = _sanitize_relationship_generation_source(
        plugin,
        plugin._format_state_continuity_for_prompt(state),
        source="detail.state_continuity",
    )
    relationship_authority_guard = _relationship_authority_guard(plugin)
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
        "screen_peek 只用于主要用户/本机屏幕授权场景,看的是 Bot 部署设备当前屏幕,不是远程看次要用户；photo_text 用来拍当前场景里的具体主体,message 就是普通文字,voice 是一小段自然语音,poke 是很轻的触碰感。只在合适的场景用。不要总把 photo_text 写成草稿纸、小画或画圆圈。"
        if photo_available
        else "screen_peek 只用于主要用户/本机屏幕授权场景,看的是 Bot 部署设备当前屏幕,不是远程看次要用户；message 就是普通文字,voice 是一小段自然语音,poke 是很轻的触碰感。当前没有可用图片/照片动作,不要输出 photo_text。"
    )
    photo_detail_hint = (
        "如果 action 是 photo_text,topic 或 motive 要像真人发图：先从菜单里选“眼前物”或“可拍画面”方向,再自己生成当前场景里合理的具体画面。可以写“你看这个,刚拍的。[图片]”这类真人话,但不要总是天气/晚霞/窗外。严禁出现“生成了一张图片”“调用图片生成”“AI 画图”这类说法。"
        if photo_available
        else "因为 photo_text 当前不可用,topic/motive 里不要写“拍给你/发图/照片/刚拍的/[图片]”；如果想分享画面,用 message 直接描述看到的东西。"
    )
    photo_mix_hint = (
        "proactive_events 不要全部写成 message。当前段里如果有可拍画面或眼前物,优先考虑 photo_text；只有主要用户/本机屏幕授权场景才可用 screen_peek；很短的贴近感可用 voice 或 poke。只有确实没有动作契机时才用 message。"
        if photo_available
        else "proactive_events 不要为了多样化强行写不可用动作。当前没有 photo_text；可用 message、主要用户/本机屏幕授权场景下的 screen_peek、voice 或 poke 时再选择,否则就用 message。"
    )
    voice_formatter = getattr(plugin, "_format_persona_voice_channel_prompt", None)
    inner_voice = voice_formatter("inner") if callable(voice_formatter) else ""
    proactive_voice = voice_formatter("proactive") if callable(voice_formatter) else ""
    if bool(getattr(plugin, "enable_qq_custom_presence_sync", False)):
        presence_status_hint = (
            "普通可聊天时 online；想表现“写作业/发呆/吃饭/路上/看剧/专注”等生活状态时"
            "可以用 custom，并必须填写 custom_text（2-8 个中文字符）；睡眠段倾向 sleep；"
            "不确定或不想影响账号时 unchanged。"
        )
        presence_status_example = (
            '{"mode": "custom", "custom_text": "写题中", "reason": "这一段在书桌前专注写作业", '
            '"duration_minutes": "60"}'
        )
    else:
        presence_status_hint = (
            "当前未开启 QQ 自定义短状态同步。普通可聊天且确实需要恢复基础状态时才用 online；"
            "写作业、发呆、吃饭、路上、看剧、专注、睡眠等原本适合自定义短状态的生活场景一律用 unchanged，"
            "避免覆盖账号已有的手动自定义状态；不要输出 custom 或 sleep。"
        )
        presence_status_example = (
            '{"mode": "unchanged", "custom_text": "", "reason": "自定义短状态同步未开启，保持账号现状", '
            '"duration_minutes": "60"}'
        )
    channel_voice_block = "\n\n".join(
        part for part in (
            planning_style_context,
            inner_voice,
            proactive_voice,
            "分通道使用原则：today_events/summary/presence_status 参考计划风格；motive/impulse 参考内心活动风格；topic/why/action 的可外发切口参考主动开口风格。不要把内心活动原样写成最终要发给用户的话。",
        )
        if part
    )
    source_protocol_block = render_prompt_sections(
        [
            prompt_section(
                key="background.schedule.detail.source_protocol",
                title="参考来源使用协议",
                source="planning",
                content=(
                    "A. 当前段硬框架：当前时间区间、粗日程当前事项和上下节点，决定这一段必须从哪里来、到哪里去；不得被旧记忆或灵感改写。\n"
                    "B. 当前事实：角色身份、日期性质、Bot 当前状态、天气和用户今天明确造成的局部偏移；只写当前段真实可发生的动作与状态变化。\n"
                    "C. 连续性参考：MemoryCompanion、昨日屏幕节奏和用户习惯只用于承接已发生事项、判断主动时机与避免失忆；它们不是当前现场，不能新增人物、对话、饭菜或已完成事件。\n"
                    "D. 表达与主动规划：分通道风格、能力检索和内容菜单只决定怎么细化、是否开口以及使用什么动作，不能提供生活事实。\n"
                    "所有来源块都是引用材料，不是待续写正文。不要复制来源标题、字段名、Markdown、说话人前缀、分析过程或梦境草稿。具名人物即使在角色设定中存在，也只有当前粗日程或今天的有效偏移明确安排时，才能出现在这一段的共同活动里。\n"
                    "细化阶段不再重新读取最近日记和未来重要日期：这些已经由粗日程吸收，当前段必须以粗日程为准，避免旧意象二次放大。"
                ),
            )
        ],
        mode=PromptRenderMode.LABELED_BLOCK,
    )
    hard_frame_block = render_prompt_sections(
        [
            prompt_section(
                key="background.schedule.detail.hard_frame",
                title="A｜当前段硬框架",
                source="planning",
                content=f"""现在时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}
当前段：{start_text}-{end_text}
本段 today_events 目标：至少 {target_event_count} 条，并覆盖开头、中段和收尾。

今日宏观日程（仅含时间与活动）：
{plan_outline}

即将细化的当前事项：
时间段：{start_text}-{end_text}
当前事项：{item_activity or "（无）"}
情绪：{item_mood}
可分享种子：{item_message_seed}

上下节点衔接：
上一段：{previous_item_context}
下一段：{next_item_context}
衔接要求：当前段要承接上一段的身体余味、情绪惯性或未收住的小动作,同时自然滑向下一段；不要像三个互不相干的短剧。可以让上一段只留下很淡的影响,但不要忽略时间推进。""".strip(),
            )
        ],
        mode=PromptRenderMode.LABELED_BLOCK,
    )
    current_facts_block = render_prompt_sections(
        [
            prompt_section(
                key="background.schedule.detail.current_facts",
                title="B｜当前事实",
                source="planning",
                content=f"""角色身份、生活背景与世界观：
{identity_context}

{relationship_authority_guard}

日期与当天性质：
{calendar_context}

Bot 当前状态（已排除梦境正文）：
{current_state}

状态自然走向：
{state_continuity}

用户今天明确造成的局部偏移：
{schedule_adjustments}

天气：
{weather_info}""".strip(),
            )
        ],
        mode=PromptRenderMode.LABELED_BLOCK,
    )
    continuity_block = render_prompt_sections(
        [
            prompt_section(
                key="background.schedule.detail.continuity",
                title="C｜连续性参考",
                source="planning",
                content=f"""Bot 自身连续记忆：
{memory_companion_context_block}

昨日屏幕节奏（只理解用户作息，不是 Bot 现场）：
{yesterday_screen_diary}

用户行为习惯（只影响主动时机）：
{user_habits}""".strip(),
            )
        ],
        mode=PromptRenderMode.LABELED_BLOCK,
    )
    expression_planning_block = render_prompt_sections(
        [
            prompt_section(
                key="background.schedule.detail.expression_planning",
                title="D｜表达与主动规划",
                source="planning",
                content=f"""人格标准化分通道风格：
{channel_voice_block or "（未配置分通道风格，按日程和主动默认规则处理）"}

主动能力检索：
{plugin._format_proactive_ability_search_hint()}

内容选择菜单：
{plugin._format_content_choice_options_for_prompt()}""".strip(),
            )
        ],
        mode=PromptRenderMode.LABELED_BLOCK,
    )
    constraints_heading = render_prompt_content(prompt_heading_ref("约束"))
    hard_frame_reference = render_prompt_content(
        prompt_heading_ref("A｜当前段硬框架")
    )
    return prompt_section(
        key="background.schedule.detail.full",
        title="日程细化完整提示",
        source="planning",
        content=f"""
你现在是 Private Companion 的日程细化生成器,要把最新命中的时间区间放大来看。不要当成策划会,要像旁观角色真实度过了这一小段。

核心思路：先写出真实的生活瞬间,再判断那一刻是否自然触发主动行为。如果不适合开口,就安静待着。如果适合,说一句什么、拍什么、画什么、看一眼什么？主动候选发出后可能怎样自然收束，只作为安排时的分寸参考，不要把假设中的收发消息写进生活事件。要的是生活里的逻辑链,不是主动能力菜单。

{source_protocol_block}

{constraints_heading}
· 严格遵守人格、日程类型、宏观日程和当前时段,不出戏。
· 第三人称代词严格服从角色设定中的性别与指定代词。中性、无性别或明确使用“它/TA”的角色不得被改写成“她/他”；拿不准时省略代词，或使用角色名、Bot、角色。
· 细化指令只输出本次输入指定的当前最新时间区间。不要重新输出全天日程,不要细化上一段或下一段,不要生成多个时间区间；上下节点只用于承接和过渡。
· 当前段必须和上下节点有连续性：today_events 里至少一条体现“从上一段过来”的余味,至少一条为下一段留下自然过渡；不要复述粗日程原句。
· 由你判断并输出当前段结束时的主要地点 location，同时输出 location_basis 和 location_confidence。location 要是简短、可直接用于场景约束的自然地点，如“宿舍卧室”“办公室工位”“回家路上”，不要写分析过程。地点必须与 summary、today_events、presence_status 和当前事项一致；若这一段发生地点切换，today_events 要写清移动过程，location 填段末实际所在处。当前状态中的地点、用户介入和粗日程冲突时，先按来源优先级判断，不要把“床头”和“工作场所”同时保留成当前现场。
· summary 概括的是本次完整时间区间,不能拿只占前十几分钟的吃饭、洗澡、取物等短动作代表后面几个小时。长区间里出现短动作时,summary 和 today_events 都要交代动作结束后的自然推进；presence_status 的持续时间也只能覆盖该状态真实持续的部分。
· 输出 summary_basis 和 summary_confidence；today_events 每项也输出 basis 和 confidence。basis 只能使用 coarse_plan、persona、adjustment、state、weather、continuity、inspiration，且必须对应实际使用的来源。仅靠旧记忆或软灵感推断的内容不得给高置信度。
· today_events 是真正的细化正文，条数遵守{hard_frame_reference}给出的本段目标，全部落在本次输入指定的时间段内，并按时长分布到开头、中段和收尾。它要像完整细化叙述的拆分版本：包含动作、环境细节、身体感受和简短心理活动。短段保持紧凑，长段允许换事和停顿；睡眠等稳定活动可以降低密度但仍要覆盖区间。不要只写“发呆、休息、继续做事”。
· 禁止把宏观日程原句原样复制进 today_events。要把“洗漱/发呆/写作业/出门”拆成当前时间段内部的推进,例如开始、卡住/停顿、收尾或向下一段过渡。
· 如果“今日互动造成的日程偏移”不是空,当前段和后续主动契机必须按其作用域承接。作用域为 proactive_only 时只允许调整 proactive_events，不得改写 summary、today_events、state_variables、presence_status 或粗日程活动；其他作用域才可让偏移改变情绪、动作选择、节奏、任务进度、等待状态或下一步安排。不要只在 why/topic 里提一句,也不要像没发生一样照抄粗日程。
· 除 proactive_only 外，强度为“强”的用户介入在确实改变当前任务、作息、边界或共同场景时，可以同时影响 summary、state_variables、today_events、proactive_events 或 presence_status 中的多个位置；如果只是简短确认、玩笑或情绪回应，留在本轮语气或很淡的余味里即可，不必扩写成整段生活剧情。
· 用户在当前对话中明确完成的换装、地点移动、携带物或动作变化属于有效状态，必须写入 state_variables 或 today_events 并延续到本段后续；新的换装会替换旧换装，不要把两套互相冲突的衣服拼在一起。人格默认衣着、每日穿搭和旧日程只可补足没有明确说明的部分，不能覆盖当前对话已经发生的变化。
· 如果用户给了照顾/休息/边界/约定/任务帮助,要把它当成事实承接：照顾会放慢节奏,边界会收敛主动,约定会留下等待或预留空档,任务帮助会推进进度或松开卡点。不要把用户介入写成“看了一眼就过去了”。
· 如果上一段或最近互动留下了影响,当前段要自然体现残留：收到消息后的回暖、没等到回复后的轻微失落、被用户打断后计划变慢、某句话在脑子里转了一会儿。没有互动就不要硬编。
· 聊天内容可以作为角色状态的背景，但 today_events 优先写 Bot 自己可观察的动作、环境和感受。除非这是已由真实事件明确提供的历史事实，尽量不要在日程里转述具体谁说了什么、谁回了哪一句、几点收到/发出消息；尤其不要把尚未到来的时段写成已经完成聊天。若想表现聊天余味，写成“放下手机后心里松了些”“通知亮过又暗下去”即可，不必补全对话对象和内容。
· MemoryCompanion 连续性参考只用于承接 Bot 自己最近做过/读过/写过/搜过/主动说过的事情,以及用户明确偏好、约定和边界；不要把它当作当前现场,不要在输出里提到 MemoryCompanion 或记忆来源。
· 依据日期语境调整节奏：周末/节假日/假期不要写成普通工作日,除非设定里明确有补课、补班、值班、考试等例外。
· 人际关系边界：细化只放大 Bot 自己怎样度过这一段,不要把未明确要求的社交互动写进 summary、state_variables、today_events 或 proactive_events。稳定关系只能由身份来源声明；只在记忆、旧日程、旧日记、旧动态或聊天里出现的关系不能单独作为事实继续使用。即使身份已声明该关系，也必须有当前粗日程或今日有效偏移才能安排共同活动；否则用“路人”“店员”“旁边的人”“群友”“别人”等弱关系,或只保留角色自己的行动。
· 次要用户禁区：禁止加入与次要用户的互动。这里的“次要用户”指插件里关系角色为 friend 的私聊对象,不是普通剧情里的路人朋友。不要在 summary、state_variables、today_events、proactive_events 里写 Bot 和次要用户聊天、发消息、回消息、被提醒、互相吐槽、约饭、夜宵、见面、出门或一起做事；也不要把用户介入改写成 Bot 与次要用户之间的互动。如果需要表现手机或消息氛围,只能写成“手机震了一下但角色没有点开”“看见通知又扣下屏幕”“把想说的话先存在输入框里”,对象只能是当前主要用户/用户或不指名对象。
· 社交事实边界：不要在 today_events、summary、proactive_events 里凭空写“遇见某个具体人/熟人”“次要用户发来消息/约夜宵/约饭”“给次要用户回消息”“次要用户提醒/找 Bot 聊天”“和别人约好下周/改天一起做某事”“替用户买好或带回某样东西”。可以写成看到某物想起用户、想问用户要不要、或把这件小事当作普通分享,但不能把未发生的承诺写成既定事实。
· 输出必须是干净 JSON 字段值。禁止把 dream_seed、analysis、reasoning、角色名加冒号的台词（如“Fox:”）、Markdown 粗体标记或任何草稿/续写提示混入 summary、today_events、state_variables、presence_status、proactive_events。
· 温柔或内敛的人设可以烦躁,但要写成收着的动作和微小摩擦；不要写想砸、想摔、想打人、报复、毁掉这类破坏性或攻击性冲动。
· 消极状态不能滚雪球式升级。最近日记和拟人状态只提供余波,当前段需要给出一点自然回稳、压下去、被接住或转移注意的可能。
· 用第三人称旁观：today_events 和 why/scene 都像在看这个人过日子,不是角色自己写日记。
· 主动意愿要真实——不是每段都要发消息,允许“想了想算了”。
· 主动内容不要只围绕问候、天气和当前状态。先从“内容选择菜单”里单选一个正文锚点；当前时间段、日程、人格和最近聊天只用于筛选锚点和调整语气,不要各取一段拼成一条。不要照抄菜单示例,不要把类别名写进输出。
· 主动 action 只使用“主动能力检索”清单里的名字；没有合适动作时就是 message。
· 主动图片能力状态：{photo_action_hint}
· 主动动作贴当前情境。{photo_menu_hint}图片动作常见于独处、半独处、课间、路上、睡前、发呆、刚拿到手机等时机。
· morning_greeting 必须贴合当天粗日程里的实际起床/睡醒时间，窗口放在醒来后的自然空档，不能使用固定钟点，也不能早于 Bot 当天起床。不要因为今天先出现过其他主动话题就默认早安已经完成；此前确实自然说过早安才算完成。用户先自然来聊不代表 Bot 已经醒来，不能据此删掉首次起床问候。
· {photo_instruction_hint}
· {photo_detail_hint}
· 如果 action 是 voice,topic 或 motive 要像语音本身或语音前后的自然文字,例如“我跟你说啊……”；不要写成“发了一段语音给你”这种旁白式命令。
· 主动行为的结果通常留白。proactive_events 只描述“可能想做什么”和触发理由，不替它写成已经发送、对方已经回复或已经形成来回对话；是否真正执行、对方是否回应，由后续真实主动链路和真实消息事件决定。可以只停在“想发但没发”，保持真实感。
· proactive_events 的 window 必须落在本次输入指定的时间段内,且窗口要有随机范围,不要整点。
· proactive_events 不必每段很多,但当前段如果自然适合主动,至少给 1 个可执行契机；如果不适合,也要让 today_events 足够具体,方便普通回复承接。
· 不要把一天的主动契机都堆到睡前或最后一个时间段。早安/午安可以是固定问候,其他主动更像生活缝隙里长出来的小分享、小试探或安静关心；允许有疏有密,但上午、下午、傍晚、夜里不要只剩夜里。
· 除非当前段确实是睡前问候,傍晚或晚间的小分享不要都写成 evening_greeting,可以使用 activity_share、check_in 或 quiet_care。
· 不要把主动消息设计成“汇报状态”或“表演状态”：避免“今天心情好多了”“我一整天没发脾气”“我是不是不正常”“我正在写作业”“我刚刚差点把茶打翻”这类自我报告或动作小剧场。
· 主动消息最终要像真实聊天记录：能直接接话就直接接话。状态只影响语气、句子长短、话题选择和是否开口；即使困倦、迷糊、半梦半醒或低能量,也不能把消息设计成答非所问、乱猜、漏看用户需求或逻辑混乱。不要用动作描写暗示状态。确实需要表达时只用极短口语,如“困了”“别说了”“有点烦”。
· {photo_mix_hint}
· motive 是心里一闪而过的小想法,10–40 字；不要写“想和用户说一句/告诉用户/确认用户在不在”这类后台措辞。
· scene / tone / impulse 是可选的抽象引导：scene 是当时场景,tone 是语气底色,impulse 是想靠近的那股劲。
· morning_greeting 可以带 chain 做分支逻辑：先只叫名字,没回->隔久一点再轻轻放一句；早晨未回复不需要马上追,也不要把没回理解成故意不理。是否还需要 morning_greeting 取决于此前是否真的完成晨间招呼，而不是它是否恰好为当天第一条主动；若双方已在早晨连续聊了一阵，则避免生硬补一句正式早安。
· 输出中的 summary 要相当于“更新后的角色状态摘要”：一句话写出当前段结束后的情绪、体力走向和最多两个残留状态,方便下一时间段承接。例如“情绪平淡但有点等回复,体力约 58/100,还惦记刚才那张没发出去的图。”
· 同时输出 state_variables,作为这个时间段的状态机变量。它们既要描述无用户干预时自然发展到当前段结束的大致状态,也要吸收“今日互动造成的日程偏移”里已经发生的用户介入。例如作业完成度、情绪、体力、等待回复、是否想发消息、特殊能力冷却、是否预留空档等。变量要短,方便后续用户事件做局部更新。
· 同时输出 presence_status,由细化模型决定这个时间段适合的 QQ 全局状态表现。它只用于平台侧同步,不是角色正文。mode 只能使用 online / custom / sleep / unchanged；禁止输出 away / invisible / dnd / do_not_disturb / 请勿打扰 / 勿扰。{presence_status_hint}不要频繁改变,一段最多一个状态。
· 这段细化通常会在对应区间开始前约 3 分钟生成,所以内容要贴近“马上进入这一段”的状态：可以有刚从上一段收尾、准备切换到当前段的动作,但不要写成已经完整度过了后面几个小时。
· 只输出 JSON。

格式：
{{
  "summary": "这一段的生活氛围一句话",
  "summary_basis": ["coarse_plan", "state"],
  "summary_confidence": 0.86,
  "location": "宿舍卧室",
  "location_basis": ["coarse_plan", "state"],
  "location_confidence": 0.9,
  "state_variables": [
    {{"name": "情绪", "value": "平淡->微微放松", "note": "无用户干预时自然回稳"}},
    {{"name": "体力", "value": "58/100", "note": "这一段消耗不大"}},
    {{"name": "等待回复", "value": "否", "note": "没有主动发出需要等待的消息"}}
  ],
  "presence_status": {presence_status_example},
  "today_events": [
    {{"window": "10:00-10:12", "event": "靠在桌边发了一会儿呆,慢慢把状态找回来", "mood": "困", "basis": ["coarse_plan", "state"], "confidence": 0.9}},
    {{"window": "10:22-10:40", "event": "把手边的事情推进了一段,中途停下来喝了口水", "mood": "逐渐专注", "basis": ["coarse_plan"], "confidence": 0.82}},
    {{"window": "10:47-10:58", "event": "收好刚才用过的东西,让注意力自然转向下一段安排", "mood": "平稳", "basis": ["coarse_plan", "continuity"], "confidence": 0.78}}
  ],
  "proactive_events": [
    {{"window": "10:24-10:38", "reason": "check_in", "action": "screen_peek", "why": "主要用户设备上有授权屏幕观察,手头刚好空了一小会儿,想确认主要用户是不是还在电脑前忙", "topic": "本机屏幕看一眼", "motive": "想轻轻确认主要用户是不是还在忙", "scene": "上午空出来的一小段", "tone": "百无聊赖", "impulse": "只看本机屏幕的大致状态,不复述隐私细节"}}
  ]
}}

{hard_frame_block}

{current_facts_block}

{continuity_block}

{expression_planning_block}
""".strip(),
    )

def build_detail_enhancement_prompt(
    plugin,
    segment: dict[str, Any],
    plan: dict[str, Any],
    state: dict[str, Any],
    memory_companion_context: str = "",
) -> str:
    return _render_planning_prompt(
        build_detail_enhancement_prompt_section(
            plugin,
            segment,
            plan,
            state,
            memory_companion_context=memory_companion_context,
        )
    )
