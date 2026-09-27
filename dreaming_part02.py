# -*- coding: utf-8 -*-
"""dreaming_part02：从 dreaming.py 机械抽取的模块级函数。

由 tmp/split4/mod_split.py 生成（10 个函数 / 386 行）。函数体逐字节原样，仅位置变化。
对外经由宿主 dreaming.py re-export，接口不变。
"""
from __future__ import annotations

from .dreaming_part01 import (
    _clean_dream_fragment_text,
    _compact_diary_text,
    _diary_reads_like_status_broadcast,
    _dream_fragment_is_useful,
    _recent_diary_duplicate_hit,
)
from .dreaming_shared import (
    Any,
    _safe_int,
    _single_line,
    _today_key,
    datetime,
    random,
    re,
    runtime_persona_setting,
)


def build_dream_memory_fragments(plugin, count: int = 8) -> list[str]:
    fragment_pool = plugin._normalize_dream_fragment_pool(plugin.data.get("dream_fragments", []))
    picked = plugin._weighted_unique_fragment_sample(fragment_pool, count=min(count, 6))
    if len(picked) >= count:
        return picked[:count]
    fragments: list[str] = []
    diaries = plugin.data.get("bot_diaries", [])
    if isinstance(diaries, list):
        for diary in diaries[-4:]:
            if not isinstance(diary, dict):
                continue
            for candidate in (
                _single_line(diary.get("share_seed"), 80),
                _single_line(diary.get("summary"), 80),
            ):
                if candidate:
                    cleaned = _clean_dream_fragment_text(candidate)
                    if cleaned and _dream_fragment_is_useful(cleaned):
                        fragments.append(cleaned)
    # A raw daily plan is an intent/projection input.  It must not become
    # dream or long-term memory material merely because its clock window has
    # elapsed.  Only evidence-backed historical entries are eligible here.
    disclosure = getattr(plugin, "_agenda_disclosure_view", None)
    if callable(disclosure):
        try:
            view = disclosure("history_fact", max_entries=12)
            entries = view.get("entries", []) if isinstance(view, dict) else getattr(view, "entries", [])
        except Exception:
            entries = []
        if isinstance(entries, list):
            for item in entries:
                if not isinstance(item, dict):
                    continue
                for candidate in (
                    _single_line(item.get("title") or item.get("activity"), 80),
                    _single_line(item.get("summary"), 80),
                ):
                    cleaned = _clean_dream_fragment_text(candidate)
                    if cleaned and _dream_fragment_is_useful(cleaned):
                        fragments.append(cleaned)
    can_do = plugin.data.get("can_do", [])
    if isinstance(can_do, list):
        for item in can_do[:4]:
            candidate = _single_line(item, 60)
            cleaned = _clean_dream_fragment_text(candidate)
            if cleaned and _dream_fragment_is_useful(cleaned):
                fragments.append(cleaned)
    for entry in plugin._get_relevant_important_dates()[:3]:
        if not isinstance(entry, dict):
            continue
        joined = _single_line(
            f"{entry.get('title', '')} {entry.get('note', '')}",
            80,
        )
        cleaned = _clean_dream_fragment_text(joined)
        if cleaned and _dream_fragment_is_useful(cleaned):
            fragments.append(cleaned)
    yesterday = plugin.data.get("yesterday_conversation_summary", {})
    if isinstance(yesterday, dict) and yesterday.get("date") == _today_key():
        for candidate in (
            _single_line(yesterday.get("dream_reference"), 100),
            _single_line(yesterday.get("summary"), 100),
        ):
            cleaned = _clean_dream_fragment_text(candidate)
            if cleaned and "无明确" not in candidate and "暂无" not in candidate and _dream_fragment_is_useful(cleaned):
                fragments.append(cleaned)
        residues = yesterday.get("residues", [])
        if isinstance(residues, list):
            for item in residues[:4]:
                if not isinstance(item, dict):
                    continue
                content = _single_line(item.get("content"), 80)
                cleaned = _clean_dream_fragment_text(content)
                if cleaned and _dream_fragment_is_useful(cleaned):
                    fragments.append(cleaned)
    weather = _single_line(plugin._weather_summary_text(plugin.data.get("daily_weather", {})), 60)
    weather_fragment = _clean_dream_fragment_text(weather)
    if weather_fragment and _dream_fragment_is_useful(weather_fragment):
        fragments.append(weather_fragment)
    deduped: list[str] = []
    seen: set[str] = set()
    for fragment in fragments:
        if not fragment or fragment in seen:
            continue
        seen.add(fragment)
        deduped.append(fragment)
    random.shuffle(deduped)
    for fragment in deduped:
        if fragment not in picked:
            picked.append(fragment)
        if len(picked) >= count:
            break
    return picked[:count]

def dream_theme_specs(plugin) -> list[tuple[str, str]]:
    default_specs = {
        "温柔日常": "梦像从白天的普通片段里慢慢渗出来，柔软、安静、带一点生活气。",
        "奇幻": "现实里的东西轻轻偏离常理，带一点不合逻辑的发光感或变形感。",
        "恐怖": "不是血腥惊吓，而是熟悉场景里多出一点说不清的不安和压迫。",
        "追逐": "一直在赶什么、找什么、错过什么，节奏偏紧，醒来会残留一点慌。",
        "悬疑": "细节像有答案却总差一点，梦里会反复回头、确认、怀疑。",
        "荒诞": "东西会莫名其妙地接到一起，逻辑松掉，带一点好笑又奇怪的偏移。",
        "怀旧": "梦会把旧场景、旧物件、旧关系轻轻翻出来，但不一定讲得明白。",
        "暧昧春梦": "梦里会有一点亲密、靠近、心跳变快的错觉，但保持含蓄，不写露骨内容。",
    }
    raw = str(runtime_persona_setting(plugin, "dream_theme_candidates", "温柔日常,奇幻,恐怖,追逐,悬疑,荒诞,怀旧,暧昧春梦") or "").strip()
    names = [name.strip() for name in raw.split(",") if name.strip()]
    if not names:
        names = list(default_specs.keys())
    specs: list[tuple[str, str]] = []
    for name in names:
        if name == "暧昧春梦" and not runtime_persona_setting(plugin, "enable_intimate_dream_theme", False):
            continue
        specs.append((name, default_specs.get(name, f"梦整体偏{name}，但仍然要从具体生活碎片出发,保留一条能读懂的梦内情绪线。")))
    if not specs:
        specs.append(("温柔日常", default_specs["温柔日常"]))
    return specs

def _diary_entry_is_today(plugin, value: Any) -> bool:
    today = _today_key()
    if isinstance(value, (int, float)):
        try:
            stamp = float(value)
            if stamp > 100_000_000_000:
                stamp /= 1000.0
            return datetime.fromtimestamp(stamp).strftime("%Y-%m-%d") == today
        except Exception:
            return False
    return str(value or "").strip().startswith(today)

def _daily_diary_evidence_ledger(plugin) -> tuple[str, list[dict[str, str]]]:
    data = plugin.data if isinstance(getattr(plugin, "data", None), dict) else {}
    evidence: list[dict[str, str]] = []

    def add(level: str, source: str, text: Any) -> None:
        cleaned = _single_line(text, 180)
        if not cleaned or any(item["text"] == cleaned for item in evidence):
            return
        evidence.append({"level": level, "source": source, "text": cleaned})

    adjustments = data.get("schedule_adjustments") if isinstance(data.get("schedule_adjustments"), list) else []
    for item in adjustments[-12:]:
        if not isinstance(item, dict):
            continue
        stamp = item.get("created_at") or item.get("updated_at") or item.get("ts") or item.get("date")
        if not stamp or not _diary_entry_is_today(plugin, stamp):
            continue
        text = item.get("summary") or item.get("reason") or item.get("adjustment") or item.get("text")
        add("planned", "用户调整后的计划", text)

    audits = data.get("proactive_audit_log") if isinstance(data.get("proactive_audit_log"), list) else []
    for item in audits[-30:]:
        if not isinstance(item, dict) or str(item.get("status") or "").lower() not in {"sent", "success", "completed"}:
            continue
        stamp = item.get("updated_at") or item.get("created_at") or item.get("ts") or item.get("sent_at")
        if not stamp or not _diary_entry_is_today(plugin, stamp):
            continue
        text = item.get("final_text_preview") or item.get("text_preview") or item.get("topic") or item.get("note")
        add("confirmed", "已执行主动", text)

    for state_key, label in (("web_exploration", "主动搜索"), ("news_integration", "新闻阅读")):
        source_state = data.get(state_key) if isinstance(data.get(state_key), dict) else {}
        stamp = source_state.get("last_explore_at") or source_state.get("last_read_at") or source_state.get("updated_at")
        digest = source_state.get("last_digest") if isinstance(source_state.get("last_digest"), dict) else {}
        if stamp and _diary_entry_is_today(plugin, stamp):
            add("confirmed", label, digest.get("topic") or digest.get("headline") or digest.get("note"))

    for method_name, label in (
        ("_self_timeline_from_creative", "实际创作记录"),
        ("_self_timeline_from_photo_generation", "实际生图记录"),
        ("_self_timeline_from_qzone_publish", "实际空间发布"),
    ):
        collector = getattr(plugin, method_name, None)
        if not callable(collector):
            continue
        try:
            entries = collector(data)
        except Exception:
            continue
        for entry in entries[-6:] if isinstance(entries, list) else []:
            if not isinstance(entry, dict) or not _diary_entry_is_today(plugin, entry.get("ts") or entry.get("date")):
                continue
            summary = _single_line(entry.get("summary"), 100)
            detail = _single_line(entry.get("detail"), 140)
            if "tid:" in detail:
                detail = detail.split("tid:", 1)[0].rstrip("；; ")
            add("confirmed", label, "；".join(part for part in (summary, detail) if part))

    goals = data.get("personal_goals") if isinstance(data.get("personal_goals"), list) else []
    for goal in goals[-8:]:
        if not isinstance(goal, dict):
            continue
        title = _single_line(goal.get("title") or goal.get("name"), 60)
        logs = goal.get("recent_logs") if isinstance(goal.get("recent_logs"), list) else []
        for log in logs[-3:]:
            if not isinstance(log, dict) or not _diary_entry_is_today(plugin, log.get("ts")):
                continue
            evidence_text = _single_line(log.get("evidence"), 100)
            progress = _safe_int(log.get("progress"), -1, -1, 100)
            suffix = f"，进度 {progress}%" if progress >= 0 else ""
            add("simulated", "个人目标运行记录", f"{title or '个人目标'}：{evidence_text}{suffix}")

    enhanced = data.get("detail_enhanced_segments") if isinstance(data.get("detail_enhanced_segments"), dict) else {}
    enhanced_day = str(data.get("detail_enhanced_day") or "")[:10]
    for segment_key, snapshot in list(enhanced.items())[-8:]:
        if not isinstance(snapshot, dict) or snapshot.get("status") != "done":
            continue
        if enhanced_day != _today_key() and not str(segment_key).startswith(_today_key()):
            continue
        add("simulated", "运行细化", snapshot.get("summary"))
        for item in (snapshot.get("today_events") if isinstance(snapshot.get("today_events"), list) else [])[:2]:
            if isinstance(item, dict):
                add("simulated", "运行细化", item.get("event"))

    plan = data.get("daily_plan") if isinstance(data.get("daily_plan"), dict) else {}
    if str(plan.get("date") or "")[:10] != _today_key():
        plan = {}
    now = plugin._environment_now() if hasattr(plugin, "_environment_now") else datetime.now()
    now_minutes = now.hour * 60 + now.minute
    for item in plan.get("items", []) if isinstance(plan.get("items"), list) else []:
        if not isinstance(item, dict):
            continue
        raw_end = str(item.get("end") or item.get("time") or "")
        match = re.match(r"^(\d{1,2}):(\d{2})", raw_end)
        if match and int(match.group(1)) * 60 + int(match.group(2)) <= now_minutes:
            add("planned", "已过去的计划段", item.get("activity"))

    if not evidence:
        state = data.get("daily_state") if isinstance(data.get("daily_state"), dict) else {}
        add("state", "当前状态", state.get("mood_bias") or state.get("summary") or "只留下了很少的具体记录")

    level_labels = {
        "confirmed": "已确认发生",
        "simulated": "运行推演，不能直接声称真实发生",
        "planned": "原计划，不能直接声称已经完成",
        "state": "状态底色，不是事件",
    }
    lines = [f"- [{level_labels.get(item['level'], item['level'])}] {item['source']}：{item['text']}" for item in evidence[:16]]
    return "\n".join(lines), evidence[:16]

def _daily_diary_form_instruction(plugin, evidence: list[dict[str, str]]) -> tuple[str, str]:
    configured = str(runtime_persona_setting(plugin, "daily_diary_form", "auto") or "auto").strip().lower()
    forms = {
        "scene": "场景短记：围绕一个确有依据的场景写清当时的动作和注意力变化。",
        "fragments": "碎片手记：允许两到四个短段或断句，不强求完整起承转合，但彼此要有同一天的气息。",
        "inner_voice": "心绪自述：从一个真实触发点写内心反应，不写空泛情绪总结。",
        "observation": "观察记录：抓住一个具体对象、声音、文字或细节，少解释，多保留当时的目光。",
    }
    if configured not in forms:
        choices = ["scene", "fragments", "inner_voice", "observation"]
        confirmed = sum(1 for item in evidence if item.get("level") == "confirmed")
        seed = sum(ord(char) for char in _today_key()) + confirmed
        configured = choices[seed % len(choices)]
    return configured, forms[configured]

def _daily_diary_length_instruction(plugin) -> tuple[int, int]:
    mode = str(runtime_persona_setting(plugin, "daily_diary_length", "standard") or "standard").strip().lower()
    return {"short": (60, 130), "long": (180, 360)}.get(mode, (110, 240))

def _daily_diary_creativity_instruction(plugin) -> str:
    mode = str(runtime_persona_setting(plugin, "daily_diary_creativity", "balanced") or "balanced").strip().lower()
    if mode == "strict":
        return "严格写实：只写已确认发生的事实；材料不足就写短，不补场景。"
    if mode == "expressive":
        return "表达可以更有个人色彩和节奏，但只能放大感受与观察，不能虚构人物、事件或完成结果。"
    return "写实为主：允许对已确认事实做轻微感官化表达，不得把计划或运行推演写成真实经历。"

def _daily_diary_memory_external_event_issue(
    body: str,
    evidence: list[dict[str, str]],
    continuity_memory_context: str,
) -> bool:
    """Flag an explicit memory-backed interaction claim that lacks today's support."""
    memory_text = _compact_diary_text(continuity_memory_context, 1200)
    if not memory_text:
        return False
    compact_body = _compact_diary_text(body, 800)
    if not compact_body:
        return False
    external_claim = re.search(
        r"(?:今天|刚刚|今天上午|今天下午|今天晚上).{0,10}(?:和|跟|与|同).{0,24}"
        r"(?:聊|谈|说|讨论|提到|联系|发消息|通话)",
        compact_body,
    )
    if not external_claim:
        return False

    def ngrams(value: str) -> set[str]:
        normalized = re.sub(r"[^0-9A-Za-z_\u3400-\u9fff]", "", value)
        return {normalized[index : index + 2] for index in range(max(0, len(normalized) - 1))}

    body_ngrams = ngrams(compact_body)
    memory_overlap = len(body_ngrams & ngrams(memory_text))
    if memory_overlap < 2:
        return False
    confirmed_text = "；".join(
        _compact_diary_text(item.get("text"), 180)
        for item in evidence
        if item.get("level") == "confirmed"
    )
    confirmed_overlap = len(body_ngrams & ngrams(confirmed_text))
    return confirmed_overlap < 2

def _daily_diary_quality_issues(
    plugin,
    payload: Any,
    evidence: list[dict[str, str]],
    min_chars: int,
    max_chars: int,
    continuity_memory_context: str = "",
) -> list[str]:
    if not isinstance(payload, dict):
        return ["没有返回 JSON 对象"]
    summary = _single_line(payload.get("summary"), 180)
    body = _single_line(payload.get("body"), 800)
    issues: list[str] = []
    if not summary or not body:
        issues.append("摘要或正文为空")
        return issues
    body_len = len(re.sub(r"\s+", "", body))
    if body_len < max(28, min_chars // 2):
        issues.append("正文过短且没有形成有效记录")
    if body_len > max_chars + 120:
        issues.append("正文明显超出所选篇幅")
    internal_markers = ("系统", "模型", "提示词", "JSON", "运行推演", "状态数值", "主动消息", "日程字段", "evidence")
    if any(marker in f"{summary} {body}" for marker in internal_markers):
        issues.append("正文泄露后台术语")
    if _diary_reads_like_status_broadcast(payload):
        issues.append("正文像状态播报而不是私人日记")
    unsupported = [item for item in evidence if item.get("level") in {"planned", "simulated"}]
    completion_markers = ("完成了", "做完了", "已经做", "去了", "看完了", "写完了", "收拾好了", "结束了")
    if unsupported and any(marker in body for marker in completion_markers):
        compact_body = _compact_diary_text(body, 800)
        for item in unsupported:
            compact_evidence = _compact_diary_text(item.get("text"), 180)
            trigrams = {compact_evidence[index : index + 3] for index in range(max(0, len(compact_evidence) - 2))}
            if any(token in compact_body for token in trigrams):
                issues.append("把未确认计划或推演写成了已完成经历")
                break
    if _daily_diary_memory_external_event_issue(body, evidence, continuity_memory_context):
        issues.append("把连续性记忆中的外部互动写成了今天已经发生")
    compact_summary = _compact_diary_text(summary, 180)
    compact_body = _compact_diary_text(body, 800)
    summary_bigrams = {compact_summary[index : index + 2] for index in range(max(0, len(compact_summary) - 1))}
    if len(compact_summary) >= 6 and not any(token in compact_body for token in summary_bigrams):
        issues.append("摘要没有落在正文内容上")
    duplicate_hit, _ = _recent_diary_duplicate_hit(plugin, payload)
    if duplicate_hit:
        issues.append("与近期日记过于相似")
    return issues

def fallback_diary_payload(plugin, evidence: list[dict[str, str]] | None = None) -> dict[str, Any]:
    state = plugin.data.get("daily_state", {})
    energy = state.get("energy", 70) if isinstance(state, dict) else 70
    tags = ["平稳"]
    if _safe_int(energy, 70) < 45:
        tags.append("低能量")
    for key, tag in (("sleep", "失眠"), ("health", "生病"), ("dream", "好梦")):
        value = str(state.get(key, "")) if isinstance(state, dict) else ""
        if tag == "好梦" and "梦见" in value:
            tags.append(tag)
        elif tag != "好梦" and ("失眠" in value or "低烧" in value or "头重" in value):
            tags.append(tag)
    conditions = state.get("conditions", []) if isinstance(state, dict) else []
    if isinstance(conditions, list):
        phases = {str(cond.get("phase") or "") for cond in conditions if isinstance(cond, dict)}
        kinds = {str(cond.get("kind") or "") for cond in conditions if isinstance(cond, dict)}
        if "afterglow" in phases or {"recovery_afterglow", "sleep_afterglow", "soft_afterglow"} & kinds:
            tags.append("回弹")
        if "tail" in phases or {"health_tail", "sleep_tail"} & kinds:
            tags.append("恢复期")
    usable = [item for item in (evidence or []) if isinstance(item, dict) and _single_line(item.get("text"), 180)]
    confirmed = next((item for item in usable if item.get("level") == "confirmed"), None)
    uncertain = next((item for item in usable if item.get("level") in {"planned", "simulated"}), None)
    if confirmed:
        fact = _single_line(confirmed.get("text"), 150)
        summary = fact
        body = f"今天能确定留下来的记录是：{fact}。除此之外没有足够具体的细节，就先记到这里。"
    elif uncertain:
        fact = _single_line(uncertain.get("text"), 150)
        summary = "今天只留下一条尚未确认的线索"
        body = f"今天原本记着：{fact}。后来是否照计划发生，我这里没有足够记录，所以不把它写成已经做过的事。"
    else:
        summary = "今天留下的具体记录不多"
        body = "今天没有留下足够具体、可以确认的经历。与其补出一个看似自然的小场景，不如先如实记到这里。"
    return {
        "summary": summary,
        "body": body,
        "share_seed": "",
        "tags": tags,
        "today_events": [],
        "proactive_events": [],
        "dream_fragments": [],
        "long_term_events": [],
    }
