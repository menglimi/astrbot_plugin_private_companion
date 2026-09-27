# -*- coding: utf-8 -*-
#
# 以下 import 仅为 re-export：本模块的全部实现已拆到 dreaming_shared.py 与 dreaming_partNN.py，
# 对外 `from .dreaming import X` 的可用名字与拆分前完全一致。
from __future__ import annotations

from .dreaming_part01 import (
    _clean_dream_fragment_text,
    _compact_diary_text,
    _diary_age_label,
    _diary_food_motif_tokens,
    _diary_keyword_overlap,
    _diary_reads_like_status_broadcast,
    _diary_text_similarity,
    _dream_fragment_is_useful,
    _recent_diary_avoid_context,
    _recent_diary_duplicate_hit,
    _render_dreaming_prompt,
    _repair_duplicate_daily_diary,
    _soften_repeated_diary_food_motifs,
    dream_fragment_effective_weight,
    extract_weighted_dream_fragments,
    fallback_dream_fragments_for_diary,
    merge_dream_fragment_pool,
    normalize_dream_fragment_item,
    normalize_dream_fragment_pool,
    recent_diary_context,
    recent_diary_tags,
    weighted_unique_fragment_sample,
)
from .dreaming_part02 import (
    _daily_diary_creativity_instruction,
    _daily_diary_evidence_ledger,
    _daily_diary_form_instruction,
    _daily_diary_length_instruction,
    _daily_diary_memory_external_event_issue,
    _daily_diary_quality_issues,
    _diary_entry_is_today,
    build_dream_memory_fragments,
    dream_theme_specs,
    fallback_diary_payload,
)
from .dreaming_shared import (
    Any,
    PromptRenderMode,
    PromptSection,
    SequenceMatcher,
    _ABSTRACT_DREAM_FRAGMENT_MARKERS,
    _DIARY_CONCRETE_ACTION_MARKERS,
    _DIARY_DUPLICATE_KEYWORDS,
    _DIARY_STATUS_BROADCAST_MARKERS,
    _now_ts,
    _safe_float,
    _safe_int,
    _single_line,
    _today_key,
    datetime,
    prompt_section,
    random,
    re,
    render_prompt_sections,
    runtime_persona_setting,
)


async def generate_enhanced_dream_pick(plugin, weather: dict[str, Any] | None = None) -> tuple[str, str, int, int] | None:
    fragments = plugin._build_dream_memory_fragments()
    if not fragments:
        persona_hint = _single_line(plugin._get_default_persona_prompt(), 80)
        weather_hint = _single_line(plugin._weather_summary_text(weather or plugin.data.get("daily_weather", {})), 60)
        can_do = plugin.data.get("can_do", [])
        activity_hint = ""
        if isinstance(can_do, list) and can_do:
            activity_hint = _single_line(random.choice(can_do), 60)
        fragments = [
            item
            for item in (persona_hint, weather_hint, activity_hint, "醒来后只剩一点断续的画面")
            if item and item != "暂无天气信息"
        ]
    dream_themes = plugin._dream_theme_specs()
    primary_name, primary_hint = random.choice(dream_themes)
    theme_name = primary_name
    theme_hint = primary_hint
    if runtime_persona_setting(plugin, "enable_mixed_dream_themes", True) and len(dream_themes) >= 2 and random.random() < 0.35:
        alt_name, alt_hint = random.choice([item for item in dream_themes if item[0] != primary_name])
        theme_name = f"{primary_name}+{alt_name}"
        theme_hint = f"主调偏{primary_name}，但中途会混进一点{alt_name}的质感。{primary_hint} 同时，{alt_hint}"
    persona = plugin._get_default_persona_prompt()
    worldview_adaptation = ""
    formatter = getattr(plugin, "_format_worldview_adaptation_prompt", None)
    if callable(formatter):
        worldview_adaptation = formatter()
    weather_text = plugin._weather_summary_text(weather or plugin.data.get("daily_weather", {}))
    input_block = render_prompt_sections(
        [
            prompt_section(
                key="background.dream.generate.input",
                title="本次输入",
                source="dreaming",
                content="",
                children=(
                    prompt_section(
                        key="background.dream.generate.persona",
                        title="人格参考",
                        source="dreaming",
                        content=persona,
                    ),
                ),
            )
        ],
        mode=PromptRenderMode.LABELED_BLOCK,
    )
    theme_block = render_prompt_sections(
        [
            prompt_section(
                key="background.dream.generate.theme",
                title="梦境主题",
                source="dreaming",
                content=f"{theme_name}：{theme_hint}",
            )
        ],
        mode=PromptRenderMode.LABELED_BLOCK,
    )
    fragments_block = render_prompt_sections(
        [
            prompt_section(
                key="background.dream.generate.fragments",
                title="碎片记忆",
                source="dreaming",
                content="\n".join(f"- {item}" for item in fragments),
            )
        ],
        mode=PromptRenderMode.LABELED_BLOCK,
    )
    weather_block = render_prompt_sections(
        [
            prompt_section(
                key="background.dream.generate.weather",
                title="天气",
                source="dreaming",
                content=weather_text,
            )
        ],
        mode=PromptRenderMode.LABELED_BLOCK,
    )
    prompt = prompt_section(
        key="background.dream.generate",
        title="梦境生成",
        source="dreaming",
        content=f"""
你现在是 Private Companion 的梦境生成器。请根据本次输入的记忆碎片,写一个拟人化 Bot 今早残留的完整梦境。
这个梦可以跳接、荒诞、前后不完全合逻辑,但读起来必须摸得到一条“梦里的情绪线”：她在找什么、躲什么、靠近什么、误认了什么,或为什么醒来后还残留那种感觉。不要只把碎片随机拼贴。

要求：
1. 梦境内容要像把记忆碎片在梦里重新变形,允许断裂和跳场,但必须有“发生了什么”和“为什么醒来后还记得”。
2. 尽量保留一点真实生活残影,不要纯奇幻大场面；如果出现奇幻,也要让它从生活物件、聊天残留或身体感受里长出来。
3. 不要写成日程、日记、设定说明或心理分析。梦里可以有人、物、地点变化,但不要解释得太清楚。
4. 如果主题偏温柔,energy_delta 可以略微为正；如果主题偏压迫/追赶/恐怖,可以略微为负。
5. 如果主题涉及暧昧或春梦,保持含蓄,只写心跳、靠近、错觉感,不要露骨。
6. 如果碎片很少,也要用已有的人格、天气、最近日记补出一个完整梦,不能输出“没有梦”“记不清”“什么都没有”。
7. 梦境不是现实复盘,但要有现实残留：物件、颜色、声音、气味、身体感受、半句话、聊天余味都可以以变形方式出现。
8. 不要让梦境像宏大奇幻设定简介。即使有不现实元素,也要从房间、桌面、手机、路口、雨声、光线、衣物、食物、课本、屏幕等具体生活物里长出来。
9. 梦境可以有突兀转场,但每个转场前后都要能被读者想象到画面。
10. 输出必须是 JSON,不要 Markdown,不要解释,不要在 JSON 外补充任何内容。
11. factors 必须是可感知的小碎片,例如物件、颜色、声音、气味、触感、半句话；不要输出“情绪很好”“今天很累”“日程残留”这类抽象标签。
12. content 至少包含三个连续梦内节点：起始画面、变形/转场、醒前一瞬。可以不讲现实逻辑,但要讲梦内因果。
13. 不要把“资料室、发光、迷路、追逐、水光、草稿纸”等词当作固定模板反复使用；只有输入碎片里真的有相近材料时才用。

只输出 JSON：
{{
  "dream_type": "梦境类型,例如温柔日常/奇幻/追逐/悬疑/荒诞/怀旧/混合类型",
  "factors": ["梦境因子或碎片,3到8个,可以是物件/颜色/声音/气味/半句话/动作"],
  "content": "180到600字的梦境内容,写成完整一段梦；要有起始画面、变形/转场、醒前一瞬和清楚的梦内情绪线",
  "afterglow": "醒来后的梦境余韵,20到120字,说明身体或情绪残留",
  "label": "20到50字的短标签,概括这个梦留在身上的感觉",
  "mood": "平稳/恍惚/柔和/低落/敏感/轻快 之一",
  "energy_delta": -12到6之间的整数",
  "duration_hours": 3到8之间的整数
}}

{input_block}

{worldview_adaptation}

{theme_block}

{fragments_block}

{weather_block}

""".strip(),
    )
    raw_text = await plugin._llm_call(
        _render_dreaming_prompt(prompt),
        max_tokens=1050,
        task="dream",
        provider_id=plugin._task_provider(
            runtime_persona_setting(plugin, "DREAM_PROVIDER_ID", ""),
            runtime_persona_setting(plugin, "DIARY_PROVIDER_ID", ""),
            runtime_persona_setting(plugin, "MAI_STYLE_PROVIDER_ID", ""),
        ),
    )
    payload = plugin._extract_json_payload(raw_text or "")
    if not isinstance(payload, dict):
        return None
    content = _single_line(payload.get("content"), 900)
    factors_raw = payload.get("factors")
    factors = []
    if isinstance(factors_raw, list):
        factors = [_single_line(item, 30) for item in factors_raw[:8] if _single_line(item, 30)]
    label = _single_line(payload.get("label"), 80)
    if not label and content:
        label = _single_line(content, 80)
    if not label:
        return None
    mood = _single_line(payload.get("mood"), 12) or "恍惚"
    energy_delta = _safe_int(payload.get("energy_delta"), -6, -12, 6)
    duration_hours = _safe_int(payload.get("duration_hours"), 5, 3, 8)
    if not content:
        content = f"梦里只剩下一段很断续的画面：{label}"
    plugin._last_generated_dream_payload = {
        "dream_type": _single_line(payload.get("dream_type"), 40) or theme_name,
        "factors": factors or fragments[:8],
        "content": content,
        "afterglow": _single_line(payload.get("afterglow"), 180) or label,
        "label": label,
        "mood": mood,
        "energy_delta": energy_delta,
        "duration_hours": duration_hours,
        "raw": raw_text or "",
    }
    return label, mood, energy_delta, duration_hours

async def _rewrite_daily_diary_once(
    plugin,
    payload: Any,
    issues: list[str],
    evidence_text: str,
    continuity_memory_context: str,
    form_instruction: str,
    min_chars: int,
    max_chars: int,
) -> dict[str, Any]:
    current = payload if isinstance(payload, dict) else {}
    prompt = prompt_section(
        key="background.diary.rewrite",
        title="私人日记修订",
        source="dreaming",
        content=f"""
请修订下面这篇私人日记，只修一次。保留原稿的第一人称质感、情绪浓度和细节，只纠正没有依据的外部事件与模板化补景。

问题：{'；'.join(issues)}
写作方式：{form_instruction}
篇幅：{min_chars}-{max_chars} 个中文字符左右；材料不足可以更短。

今日经历账本：
{evidence_text}

连续性记忆参考：
{continuity_memory_context or '（没有检索到足够相关的连续性记忆）'}

原稿：
摘要：{_single_line(current.get('summary'), 180)}
正文：{_single_line(current.get('body'), 800)}

修订边界：
1. 只有“已确认发生”可作为今天真实发生的外部事件；运行推演、原计划和旧记忆不能改写成今天已经完成的行动、对话或见闻。
2. 心理活动、身体感受、情绪变化、注意力与回想属于第一人称内心描写，不要求在经历账本中另有同名事件。只要没有借此虚构外部事实，就应保留原稿写法与细腻程度，不要压平成事实摘要。
3. 连续性记忆可以支撑关系熟悉感、情绪余味、共同历史和未完成心事，但只能自然承接，不能把旧日情节搬到今天重演。
4. 只删除无中生有的外部场景、人物互动、对话和完成结果；不要补桌面、窗光、茶水、便签等无来源场景。材料少时允许写短，但不要用“记录很少”替换原稿已有的真实感受。
只输出 JSON：{{"summary":"15-55字题眼","body":"日记正文","tags":["1-4个正文标签"]}}
""".strip(),
    )
    try:
        raw = await plugin._llm_call(
            _render_dreaming_prompt(prompt),
            max_tokens=520,
            task="diary_rewrite",
            provider_id=plugin._task_provider(
                runtime_persona_setting(plugin, "DIARY_PROVIDER_ID", ""),
                runtime_persona_setting(plugin, "MAI_STYLE_PROVIDER_ID", ""),
            ),
        )
        parsed = plugin._extract_json_payload(raw or "")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}

async def _extract_daily_diary_derivatives(plugin, payload: dict[str, Any]) -> dict[str, Any]:
    body = _single_line(payload.get("body"), 700)
    if not body:
        return {}
    share_enabled = bool(runtime_persona_setting(plugin, "daily_diary_generate_share_seed", True))
    prompt = prompt_section(
        key="background.diary.derivatives",
        title="私人日记结构提取",
        source="dreaming",
        content=f"""
请从这篇已经写好的私人日记中提取后台结构，不要改写日记正文，也不要新增事件。

日记：
{body}

只输出 JSON：
{{
  "share_seed": "{('从正文真实出现的细节延伸出一句自然分享；不适合分享则留空' if share_enabled else '必须留空')}",
  "dream_fragments": [{{"text": "正文里真实出现的物件/声音/动作/颜色/半句话", "weight": 0.6}}],
  "continuity_thread": {{"motif": "值得跨日延续的具体线索，没有则留空", "status": "出现/变化/淡出", "next_hint": "以后只在自然有依据时承接"}},
  "long_term_events": [{{"title": "正文里确实未完成且可能跨日的事项", "status": "当前状态", "next_hint": "下一步"}}]
}}

要求：dream_fragments 0–6 个，只提取正文确实存在的碎片，不足时留空；long_term_events 0–2 个。不要生成主动计划、今日事件或不存在的后续剧情。
""".strip(),
    )
    try:
        raw = await plugin._llm_call(
            _render_dreaming_prompt(prompt),
            max_tokens=320,
            task="diary_derivatives",
            provider_id=plugin._task_provider(
                runtime_persona_setting(plugin, "DIARY_PROVIDER_ID", ""),
                runtime_persona_setting(plugin, "MAI_STYLE_PROVIDER_ID", ""),
            ),
        )
        parsed = plugin._extract_json_payload(raw or "")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}

async def generate_daily_diary(plugin) -> dict[str, Any]:
    today = _today_key()
    state = plugin.data.get("daily_state", {})
    persona = plugin._get_default_persona_prompt()
    schedule_persona = _single_line(runtime_persona_setting(plugin, "schedule_persona_prompt", ""), 1200)
    schedule_worldview = _single_line(runtime_persona_setting(plugin, "schedule_worldview_prompt", ""), 1200)
    calendar_context = plugin._format_calendar_context_for_prompt()
    recent_diary_avoid_context = _recent_diary_avoid_context(plugin)
    evidence_text, evidence = _daily_diary_evidence_ledger(plugin)
    diary_form, form_instruction = _daily_diary_form_instruction(plugin, evidence)
    min_chars, max_chars = _daily_diary_length_instruction(plugin)
    creativity_instruction = _daily_diary_creativity_instruction(plugin)
    custom_direction = _single_line(runtime_persona_setting(plugin, "daily_diary_custom_direction", ""), 500)
    continuity_memory_context = ""
    memory_composer = getattr(plugin, "_memory_companion_compose_feature_context", None)
    if callable(memory_composer):
        try:
            continuity_memory_context = await memory_composer(
                kind="daily_diary",
                query=(
                    "每日日记连续性：Bot 自我时间线、今天与主要用户的明确聊天和共同经历、"
                    "最近主动消息、已确认的阅读创作搜索生图与公开动态、情绪余味、"
                    "未完成心事、稳定偏好和关系边界、近期日记连续性线索；"
                    "区分今日事实与旧日参考，不把旧事件改写成今天发生"
                ),
                top_k=6,
                max_chars=1200,
            )
        except Exception:
            continuity_memory_context = ""
    worldview_adaptation = ""
    formatter = getattr(plugin, "_format_worldview_adaptation_prompt", None)
    if callable(formatter):
        worldview_adaptation = formatter()
    input_block = render_prompt_sections(
        [
            prompt_section(
                key="background.diary.generate.input",
                title="本次输入",
                source="dreaming",
                content=f"日期：{today}",
            )
        ],
        mode=PromptRenderMode.LABELED_BLOCK,
    )
    persona_block = render_prompt_sections(
        [
            prompt_section(
                key="background.diary.generate.persona",
                title="AstrBot 默认人格",
                source="dreaming",
                content=persona,
            )
        ],
        mode=PromptRenderMode.LABELED_BLOCK,
    )
    identity_block = render_prompt_sections(
        [
            prompt_section(
                key="background.diary.generate.identity",
                title="生活身份补充",
                source="dreaming",
                content=schedule_persona or "（无）",
            )
        ],
        mode=PromptRenderMode.LABELED_BLOCK,
    )
    worldview_block = render_prompt_sections(
        [
            prompt_section(
                key="background.diary.generate.worldview",
                title="生活/世界观补充",
                source="dreaming",
                content=schedule_worldview or "（无）",
            )
        ],
        mode=PromptRenderMode.LABELED_BLOCK,
    )
    evidence_block = render_prompt_sections(
        [
            prompt_section(
                key="background.diary.generate.evidence",
                title="今日经历账本",
                source="dreaming",
                content=evidence_text,
            )
        ],
        mode=PromptRenderMode.LABELED_BLOCK,
    )
    continuity_block = render_prompt_sections(
        [
            prompt_section(
                key="background.diary.generate.continuity",
                title="连续性记忆参考",
                source="dreaming",
                content=(
                    "以下内容只帮助保持关系、情绪和未完成线索的连贯，不是今天新发生的事件，也不是必须写入正文的清单：\n"
                    f"{continuity_memory_context or '（没有检索到足够相关的连续性记忆）'}"
                ),
            )
        ],
        mode=PromptRenderMode.LABELED_BLOCK,
    )
    prompt = prompt_section(
        key="background.diary.generate",
        title="私人日记生成",
        source="dreaming",
        content=f"""
请以当前人格的第一人称，写一篇今天的私人日记。只写日记，不安排主动消息、梦境素材或后续剧情。

写作方式：{form_instruction}
篇幅：{min_chars}–{max_chars} 个中文字符左右。
事实边界：{creativity_instruction}
{f'用户指定方向：{custom_direction}' if custom_direction else ''}

规则：
1. “已确认发生”可以写成经历；“运行推演、原计划、状态底色”只能影响语气或成为未确认的念头，绝不能写成已经发生。
2. 材料少就写短，允许今天没有戏剧性；禁止用桌面、窗光、凉茶、旧便签等通用小物件自行补场景。
3. 不固定“场景→发现→余韵”的三段式，不总结人生，不把普通小事拔高成道理。
4. 保持当前人格的词汇、观察角度和关系边界。不要写系统、模型、状态数值、日程字段或后台功能。
5. 最近日记和连续性记忆只用于承接关系熟悉感、情绪余味、共同历史、稳定偏好与未完成心事；旧日材料不能单独证明今天发生了同一件事，没有新变化时让旧线索自然淡出。
6. 心理活动、身体感受、情绪变化、注意力和回想属于第一人称体验表达，不要求在经历账本中另有同名事件；可以自然细写，但不能借内心描写虚构外部人物、对话、场景或完成结果。

只输出 JSON：
{{
  "summary": "正文中最具体的一幕或题眼，15–55字",
  "body": "私人日记正文",
  "tags": ["正文确实体现的1–4个短标签"]
}}

{input_block}

{persona_block}

{identity_block}

{worldview_block}

{worldview_adaptation}

日期语境：
{calendar_context}

{evidence_block}

{continuity_block}

最近日记：
{plugin._recent_diary_context()}

近期需要避免复用的具体素材：
{recent_diary_avoid_context}

近期重要日期：
{plugin._format_important_dates_for_prompt()}
""".strip(),
    )
    try:
        raw_text = await plugin._llm_call(
            _render_dreaming_prompt(prompt),
            max_tokens=620,
            task="diary",
            provider_id=plugin._task_provider(
                runtime_persona_setting(plugin, "DIARY_PROVIDER_ID", ""),
                runtime_persona_setting(plugin, "MAI_STYLE_PROVIDER_ID", ""),
            ),
        )
    except Exception:
        raw_text = ""
    payload = plugin._extract_json_payload(raw_text or "")
    used_fallback = False
    quality_issues = _daily_diary_quality_issues(
        plugin,
        payload,
        evidence,
        min_chars,
        max_chars,
        continuity_memory_context,
    )
    if quality_issues and isinstance(payload, dict):
        payload = await _rewrite_daily_diary_once(
            plugin,
            payload,
            quality_issues,
            evidence_text,
            continuity_memory_context,
            form_instruction,
            min_chars,
            max_chars,
        )
        quality_issues = _daily_diary_quality_issues(
            plugin,
            payload,
            evidence,
            min_chars,
            max_chars,
            continuity_memory_context,
        )
    if quality_issues:
        payload = plugin._fallback_diary_payload(evidence=evidence)
        used_fallback = True
    polisher = getattr(plugin, "_polish_diary_payload", None)
    if callable(polisher):
        payload = polisher(payload)
    tags = payload.get("tags", [])
    if not isinstance(tags, list):
        tags = []
    derivatives = {} if used_fallback else await _extract_daily_diary_derivatives(plugin, payload)
    if not isinstance(derivatives, dict):
        derivatives = {}
    share_seed = _single_line(derivatives.get("share_seed"), 120) if runtime_persona_setting(plugin, "daily_diary_generate_share_seed", True) else ""
    continuity_thread = derivatives.get("continuity_thread") if isinstance(derivatives.get("continuity_thread"), dict) else {}
    derivative_payload = {
        "dream_fragments": derivatives.get("dream_fragments", []),
        "long_term_events": derivatives.get("long_term_events", []),
        "today_events": [],
        "proactive_events": [],
    }
    return {
        "date": today,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "summary": _single_line(payload.get("summary"), 160),
        "body": _single_line(payload.get("body"), 500),
        "share_seed": share_seed,
        "tags": [_single_line(tag, 20) for tag in tags[:6] if _single_line(tag, 20)],
        "diary_form": diary_form,
        "evidence": evidence,
        "continuity_thread": {
            "motif": _single_line(continuity_thread.get("motif"), 80),
            "status": _single_line(continuity_thread.get("status"), 20),
            "next_hint": _single_line(continuity_thread.get("next_hint"), 100),
        },
        "dream_fragments": plugin._extract_weighted_dream_fragments(derivative_payload),
        "story_plan": plugin._normalize_story_plan(derivative_payload),
        "raw": raw_text or "",
    }
