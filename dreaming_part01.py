# -*- coding: utf-8 -*-
"""dreaming_part01：从 dreaming.py 机械抽取的模块级函数。

由 tmp/split4/mod_split.py 生成（22 个函数 / 372 行）。函数体逐字节原样，仅位置变化。
对外经由宿主 dreaming.py re-export，接口不变。
"""
from __future__ import annotations

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
    _single_line,
    _today_key,
    datetime,
    random,
    re,
    render_prompt_sections,
)


def _render_dreaming_prompt(section: PromptSection) -> str:
    return render_prompt_sections([section], mode=PromptRenderMode.BODY_ONLY)

def _diary_reads_like_status_broadcast(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return True
    summary = _single_line(payload.get("summary"), 180)
    body = _single_line(payload.get("body"), 600)
    if not summary or not body:
        return True
    text = f"{summary} {body}"
    marker_hits = sum(1 for marker in _DIARY_STATUS_BROADCAST_MARKERS if marker in text)
    has_concrete_action = any(marker in body for marker in _DIARY_CONCRETE_ACTION_MARKERS)
    return marker_hits >= 2 or (marker_hits >= 1 and not has_concrete_action)

def _clean_dream_fragment_text(text: Any, limit: int = 28) -> str:
    raw = _single_line(text, 80)
    if not raw:
        return ""
    raw = raw.replace("，", ",").replace("。", ",").replace("；", ",").replace("、", ",")
    parts = [part.strip(" ,.!！？?：:（）()[]【】\"'“”") for part in raw.split(",") if part.strip()]
    if parts:
        parts = sorted(parts, key=lambda item: (len(item) > limit, len(item)))
        raw = parts[0]
    raw = _single_line(raw, limit).strip(" ,.!！？?：:（）()[]【】\"'“”")
    if len(raw) <= 1:
        return ""
    return raw

def _dream_fragment_is_useful(text: str) -> bool:
    cleaned = str(text or "").strip()
    if not cleaned or cleaned in {"没有记住梦", "平稳", "暂无天气信息", "无明确碎片"}:
        return False
    if len(cleaned) > 32:
        return False
    abstract_hits = sum(1 for marker in _ABSTRACT_DREAM_FRAGMENT_MARKERS if marker in cleaned)
    concrete_markers = (
        "光", "雨", "风", "水", "纸", "书", "门", "窗", "杯", "碗", "路", "影", "声", "味",
        "颜色", "蓝", "红", "白", "黑", "暖", "冷", "手", "衣", "鞋", "车", "灯", "雾", "床",
        "被子", "手机", "屏幕", "钥匙", "包装", "饮料", "猫", "楼梯", "走廊",
    )
    has_concrete = any(marker in cleaned for marker in concrete_markers)
    if abstract_hits >= 2 and not has_concrete:
        return False
    return True

def recent_diary_tags(plugin, /) -> set[str]:
    diaries = plugin.data.get("bot_diaries", [])
    tags: set[str] = set()
    if not isinstance(diaries, list):
        return tags
    for diary in diaries[-3:]:
        if not isinstance(diary, dict):
            continue
        raw_tags = diary.get("tags", [])
        if isinstance(raw_tags, list):
            tags.update(str(tag) for tag in raw_tags)
    return tags

def recent_diary_context(plugin, count: int = 3) -> str:
    diaries = plugin.data.get("bot_diaries", [])
    if not isinstance(diaries, list) or not diaries:
        return "（暂无）"
    recent = [diary for diary in diaries[-max(count * 2, count):] if isinstance(diary, dict)]
    repeated_food_tokens: set[str] = set()
    food_seen: dict[str, int] = {}
    for diary in recent:
        text = " ".join(
            _single_line(diary.get(key), 160)
            for key in ("summary", "share_seed", "body")
            if _single_line(diary.get(key), 160)
        )
        for token in _diary_food_motif_tokens(text):
            food_seen[token] = food_seen.get(token, 0) + 1
    repeated_food_tokens = {token for token, total in food_seen.items() if total >= 2}
    lines = []
    for diary in diaries[-count:]:
        if not isinstance(diary, dict):
            continue
        tags = diary.get("tags", [])
        tag_text = "、".join(str(tag) for tag in tags[:4]) if isinstance(tags, list) else ""
        summary = _single_line(diary.get("summary"), 120)
        if repeated_food_tokens:
            summary = _soften_repeated_diary_food_motifs(summary, repeated_food_tokens)
        if summary:
            date_text = _single_line(diary.get("date"), 16)
            age_text = _diary_age_label(plugin, date_text)
            suffix = f"（{age_text},只作余味和避重）" if age_text else "（只作余味和避重）"
            continuity = diary.get("continuity_thread") if isinstance(diary.get("continuity_thread"), dict) else {}
            motif = _single_line(continuity.get("motif"), 60)
            status = _single_line(continuity.get("status"), 16)
            thread_text = f"；线索={motif}（{status or '出现'}）" if motif else ""
            lines.append(f"- {date_text} {suffix}：{summary} {tag_text}{thread_text}".strip())
    return "\n".join(lines) if lines else "（暂无）"

def _diary_age_label(plugin, date_text: str) -> str:
    if not date_text:
        return ""
    try:
        diary_date = datetime.strptime(date_text[:10], "%Y-%m-%d").date()
        today = plugin._environment_now().date() if hasattr(plugin, "_environment_now") else datetime.now().date()
        days = max(0, (today - diary_date).days)
    except Exception:
        return ""
    if days <= 0:
        return "今天"
    if days == 1:
        return "昨天"
    return f"{days}天前"

def _diary_food_motif_tokens(text: Any) -> list[str]:
    cleaned = _single_line(text, 500)
    if not cleaned:
        return []
    food_tokens = (
        "糖醋排骨", "排骨", "螺蛳粉", "锅包肉", "烤肠", "豆花", "冰粉", "奶茶",
        "豆浆", "夜宵", "便当", "饭团", "甜口", "软糖",
    )
    return [token for token in food_tokens if token in cleaned]

def _soften_repeated_diary_food_motifs(text: str, repeated_tokens: set[str]) -> str:
    softened = _single_line(text, 140)
    if not softened:
        return ""
    changed = False
    for token in sorted(repeated_tokens, key=len, reverse=True):
        if token and token in softened:
            softened = softened.replace(token, "近期重复食物意象")
            changed = True
    if changed:
        softened += "（不要复刻具体菜名）"
    return _single_line(softened, 140)

def _compact_diary_text(text: Any, limit: int = 220) -> str:
    raw = _single_line(text, limit)
    if not raw:
        return ""
    chars: list[str] = []
    for char in raw:
        if "\u4e00" <= char <= "\u9fff" or char.isascii() and char.isalnum():
            chars.append(char.lower())
    return "".join(chars)

def _diary_keyword_overlap(left: Any, right: Any) -> int:
    a = _compact_diary_text(left, limit=520)
    b = _compact_diary_text(right, limit=520)
    if not a or not b:
        return 0
    return sum(1 for keyword in _DIARY_DUPLICATE_KEYWORDS if keyword in a and keyword in b)

def _diary_text_similarity(left: Any, right: Any) -> float:
    a = _compact_diary_text(left)
    b = _compact_diary_text(right)
    if len(a) < 8 or len(b) < 8:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()

def _recent_diary_avoid_context(plugin, count: int = 3) -> str:
    diaries = plugin.data.get("bot_diaries", [])
    if not isinstance(diaries, list) or not diaries:
        return "（暂无）"
    lines: list[str] = []
    for diary in diaries[-count:]:
        if not isinstance(diary, dict):
            continue
        date_text = _single_line(diary.get("date"), 16)
        summary = _single_line(diary.get("summary"), 70)
        share_seed = _single_line(diary.get("share_seed"), 90)
        body = _single_line(diary.get("body"), 120)
        fragments = []
        for item in diary.get("dream_fragments", []) if isinstance(diary.get("dream_fragments"), list) else []:
            if not isinstance(item, dict):
                continue
            text = _single_line(item.get("text"), 24)
            if text:
                fragments.append(text)
            if len(fragments) >= 4:
                break
        parts = []
        if summary:
            parts.append(f"摘要={summary}")
        if share_seed:
            parts.append(f"分享句={share_seed}")
        if body:
            parts.append(f"正文片段={body}")
        if fragments:
            parts.append(f"梦境碎片={','.join(fragments)}")
        if parts:
            lines.append(f"- {date_text or '近期'}：" + "；".join(parts))
    return "\n".join(lines) if lines else "（暂无）"

def _recent_diary_duplicate_hit(plugin, payload: dict[str, Any], count: int = 3) -> tuple[bool, str]:
    diaries = plugin.data.get("bot_diaries", [])
    if not isinstance(diaries, list) or not diaries:
        return False, ""
    current_share = _single_line(payload.get("share_seed"), 140)
    current_summary = _single_line(payload.get("summary"), 180)
    current_body = _single_line(payload.get("body"), 520)
    current_all = " ".join(part for part in (current_share, current_summary, current_body) if part)
    for diary in reversed(diaries[-count:]):
        if not isinstance(diary, dict):
            continue
        prior_share = _single_line(diary.get("share_seed"), 140)
        prior_summary = _single_line(diary.get("summary"), 180)
        prior_body = _single_line(diary.get("body"), 520)
        prior_all = " ".join(part for part in (prior_share, prior_summary, prior_body) if part)
        share_ratio = _diary_text_similarity(current_share, prior_share)
        all_ratio = _diary_text_similarity(current_all, prior_all)
        cross_ratio = max(
            _diary_text_similarity(current_share, prior_summary),
            _diary_text_similarity(current_share, prior_body),
            _diary_text_similarity(current_summary, prior_share),
        )
        keyword_overlap = max(
            _diary_keyword_overlap(current_share, prior_share),
            _diary_keyword_overlap(current_all, prior_all),
        )
        if share_ratio >= 0.58 or all_ratio >= 0.48 or cross_ratio >= 0.62 or keyword_overlap >= 4:
            return True, _single_line(diary.get("date"), 16) or "近期日记"
    return False, ""

def _repair_duplicate_daily_diary(plugin, payload: dict[str, Any], matched_date: str) -> dict[str, Any]:
    state = plugin.data.get("daily_state", {})
    mood = state.get("mood_bias", "平稳") if isinstance(state, dict) else "平稳"
    weather = _single_line(plugin._weather_summary_text(plugin.data.get("daily_weather", {})), 48)
    note = "今天脑子里还残留着前几天梦里的画面,像醒来后还留着的一点余温。"
    if weather and weather != "暂无天气信息":
        note += f"外面的{weather}让这种余韵更明显了一点。"
    note += f"整个人偏{mood},但已经不太想继续在旧梦里打转,就把注意力慢慢放回今天新的小事。"
    repaired = dict(payload)
    repaired["summary"] = "今天有一点梦境余韵,但更想把注意力放回新的小事上。"
    repaired["body"] = note
    repaired["share_seed"] = "今天梦里的余韵还在,不过我想等遇到新的小事再讲给你听"
    repaired["tags"] = payload.get("tags") if isinstance(payload.get("tags"), list) else ["平稳"]
    return repaired

def normalize_dream_fragment_item(plugin, raw: Any) -> dict[str, Any] | None:
    now_ts = _now_ts()
    if isinstance(raw, str):
        text = _clean_dream_fragment_text(raw)
        if not text or not _dream_fragment_is_useful(text):
            return None
        return {
            "text": text,
            "weight": 1.0,
            "created_ts": now_ts,
            "source": "legacy",
        }
    if not isinstance(raw, dict):
        return None
    text = _clean_dream_fragment_text(raw.get("text") or raw.get("keyword") or raw.get("label"))
    if not text or not _dream_fragment_is_useful(text):
        return None
    weight = float(_safe_float(raw.get("weight"), 1.0))
    created_ts = _safe_float(raw.get("created_ts"), now_ts)
    if created_ts <= 0:
        created_ts = now_ts
    return {
        "text": text,
        "weight": max(0.2, min(6.0, weight)),
        "created_ts": created_ts,
        "source": _single_line(raw.get("source"), 20) or "diary",
        "date": _single_line(raw.get("date"), 16) or _today_key(),
    }

def dream_fragment_effective_weight(plugin, fragment: dict[str, Any], now_ts: float | None = None) -> float:
    now_ts = now_ts or _now_ts()
    base_weight = max(0.2, min(6.0, _safe_float(fragment.get("weight"), 1.0)))
    created_ts = _safe_float(fragment.get("created_ts"), now_ts)
    age_hours = max(0.0, (now_ts - created_ts) / 3600.0)
    decay = pow(0.72, age_hours / 24.0)
    return base_weight * decay

def normalize_dream_fragment_pool(plugin, fragments: Any, *, now_ts: float | None = None) -> list[dict[str, Any]]:
    now_ts = now_ts or _now_ts()
    if not isinstance(fragments, list):
        return []
    deduped: dict[str, dict[str, Any]] = {}
    fuzzy_seen: set[str] = set()
    for raw in fragments:
        item = plugin._normalize_dream_fragment_item(raw)
        if not item:
            continue
        text = item["text"]
        fuzzy_key = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", text).lower()[:36]
        if not fuzzy_key or fuzzy_key in fuzzy_seen:
            continue
        item["effective_weight"] = plugin._dream_fragment_effective_weight(item, now_ts=now_ts)
        if item["effective_weight"] < 0.12:
            continue
        existing = deduped.get(text)
        if not existing or item["effective_weight"] > existing.get("effective_weight", 0):
            deduped[text] = item
            fuzzy_seen.add(fuzzy_key)
    ranked = sorted(
        deduped.values(),
        key=lambda item: (float(item.get("effective_weight", 0)), float(item.get("created_ts", 0))),
        reverse=True,
    )
    for item in ranked:
        item.pop("effective_weight", None)
    return ranked[:48]

def extract_weighted_dream_fragments(plugin, payload: Any) -> list[dict[str, Any]]:
    raw_items = []
    if isinstance(payload, dict):
        raw_items = payload.get("dream_fragments") or []
    if not isinstance(raw_items, list):
        raw_items = []
    items: list[dict[str, Any]] = []
    for raw in raw_items[:12]:
        if isinstance(raw, str):
            normalized = plugin._normalize_dream_fragment_item({"text": raw, "weight": 1.0, "source": "diary"})
        elif isinstance(raw, dict):
            normalized = plugin._normalize_dream_fragment_item(
                {
                    "text": raw.get("text") or raw.get("keyword") or raw.get("label"),
                    "weight": raw.get("weight", 1.0),
                    "source": raw.get("source") or "diary",
                    "date": _today_key(),
                    "created_ts": _now_ts(),
                }
            )
        else:
            normalized = None
        if normalized:
            items.append(normalized)
    return items[:8]

def fallback_dream_fragments_for_diary(plugin, state: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seed_candidates = [
        _single_line(state.get("dream"), 36),
        _single_line(state.get("mood_bias"), 20),
        _single_line(plugin._weather_summary_text(plugin.data.get("daily_weather", {})), 36),
    ]
    current_getter = getattr(plugin, "_agenda_current_context_item", None)
    legacy_getter = getattr(plugin, "_get_current_plan_item", None)
    try:
        current_item = (
            current_getter()
            if callable(current_getter)
            else legacy_getter(plugin.data.get("daily_plan", {}))
            if callable(legacy_getter)
            else None
        )
    except Exception:
        current_item = None
    if isinstance(current_item, dict):
        seed_candidates.extend(
            [
                _single_line(current_item.get("activity"), 36),
                _single_line(current_item.get("message_seed"), 30),
            ]
        )
    seen: set[str] = set()
    for index, text in enumerate(seed_candidates):
        text = _clean_dream_fragment_text(text)
        if not text or text in seen or not _dream_fragment_is_useful(text):
            continue
        seen.add(text)
        items.append(
            {
                "text": text,
                "weight": max(1.0, 2.4 - index * 0.4),
                "created_ts": _now_ts(),
                "source": "fallback_diary",
                "date": _today_key(),
            }
        )
    return items[:5]

def merge_dream_fragment_pool(plugin, new_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    existing = plugin._normalize_dream_fragment_pool(plugin.data.get("dream_fragments", []))
    merged = existing + [item for item in new_items if isinstance(item, dict)]
    return plugin._normalize_dream_fragment_pool(merged)

def weighted_unique_fragment_sample(plugin, fragments: list[dict[str, Any]], *, count: int) -> list[str]:
    if not fragments or count <= 0:
        return []
    remaining = [dict(item) for item in fragments if isinstance(item, dict)]
    picked: list[str] = []
    while remaining and len(picked) < count:
        weights = [max(0.01, plugin._dream_fragment_effective_weight(item)) for item in remaining]
        total = sum(weights)
        if total <= 0:
            break
        chosen = random.choices(remaining, weights=weights, k=1)[0]
        text = _single_line(chosen.get("text"), 40)
        if text and text not in picked:
            picked.append(text)
        remaining = [item for item in remaining if _single_line(item.get("text"), 40) != text]
    return picked
