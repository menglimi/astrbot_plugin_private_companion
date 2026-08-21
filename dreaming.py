# -*- coding: utf-8 -*-

from __future__ import annotations

import random
import re
from difflib import SequenceMatcher
from datetime import datetime
from typing import Any

from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .persona_config import runtime_persona_setting


_ABSTRACT_DREAM_FRAGMENT_MARKERS = (
    "状态", "情绪", "心情", "感觉", "余韵", "碎片", "生活感", "日程", "计划", "总结",
    "今天", "明天", "用户", "主动", "消息", "回复", "关系", "陪伴", "模型", "生成",
)

_DIARY_STATUS_BROADCAST_MARKERS = (
    "今天偏", "当前天气", "状态确认", "今天状态", "能量", "适合推进",
    "平稳推进", "没有什么特别重的话想说", "醒来后慢慢把自己拢回",
    "梦里的雾还没散", "等晚一点遇到合适的小事再讲", "今日状态",
)

_DIARY_CONCRETE_ACTION_MARKERS = (
    "放", "拿", "翻", "写", "擦", "收", "整理", "拉开", "关上", "停", "等",
    "看", "听", "闻", "走", "坐", "喝", "热", "晾", "找", "碰", "摸", "回",
)


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


_DIARY_DUPLICATE_KEYWORDS = (
    "梦", "梦里", "梦见", "学校", "教室", "窗台", "窗边", "窗", "猫", "橘猫", "星图",
    "发夹", "书包", "餐桌", "糖", "软糖", "花", "雨", "伞", "走廊", "床", "枕头",
)


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
            if cleaned and "无明确" not in candidate and _dream_fragment_is_useful(cleaned):
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
    prompt = f"""
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

【本次输入】
【人格参考】
{persona}

{worldview_adaptation}

【梦境主题】
{theme_name}：{theme_hint}

【碎片记忆】
{chr(10).join(f"- {item}" for item in fragments)}

【天气】
{weather_text}

""".strip()
    raw_text = await plugin._llm_call(
        prompt,
        max_tokens=1050,
        task="dream",
        provider_id=plugin._task_provider(
            getattr(plugin, "dream_provider_id", ""),
            getattr(plugin, "diary_provider_id", ""),
            getattr(plugin, "mai_style_provider_id", ""),
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
        ("_self_timeline_from_private_reading", "实际阅读记录"),
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
    prompt = f"""
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
""".strip()
    try:
        raw = await plugin._llm_call(
            prompt,
            max_tokens=520,
            task="diary_rewrite",
            provider_id=plugin._task_provider(
                getattr(plugin, "diary_provider_id", ""),
                getattr(plugin, "mai_style_provider_id", ""),
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
    prompt = f"""
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
""".strip()
    try:
        raw = await plugin._llm_call(
            prompt,
            max_tokens=320,
            task="diary_derivatives",
            provider_id=plugin._task_provider(getattr(plugin, "diary_provider_id", ""), getattr(plugin, "mai_style_provider_id", "")),
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
    prompt = f"""
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

【本次输入】
日期：{today}

【AstrBot 默认人格】
{persona}

【生活身份补充】
{schedule_persona or "（无）"}

【生活/世界观补充】
{schedule_worldview or "（无）"}

{worldview_adaptation}

日期语境：
{calendar_context}

【今日经历账本】
{evidence_text}

【连续性记忆参考】
以下内容只帮助保持关系、情绪和未完成线索的连贯，不是今天新发生的事件，也不是必须写入正文的清单：
{continuity_memory_context or '（没有检索到足够相关的连续性记忆）'}

最近日记：
{plugin._recent_diary_context()}

近期需要避免复用的具体素材：
{recent_diary_avoid_context}

近期重要日期：
{plugin._format_important_dates_for_prompt()}
""".strip()
    try:
        raw_text = await plugin._llm_call(
            prompt,
            max_tokens=620,
            task="diary",
            provider_id=plugin._task_provider(
                getattr(plugin, "diary_provider_id", ""),
                getattr(plugin, "mai_style_provider_id", ""),
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
