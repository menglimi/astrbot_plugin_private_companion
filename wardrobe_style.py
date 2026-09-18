# -*- coding: utf-8 -*-
"""风格画像：把大量参考穿搭压成一段可注入的短描述。

架构（role-outfit-phase2-architecture.md）里，214 套参考不该逐套进提示词，
而应压缩成一段"风格画像"。本模块先用**确定性关键词统计**实现，不依赖模型：
从参考的名称与描述里抽颜色 / 材质 / 廓形 / 风格 / 品类词，按频次取前几项。

接口保持"文本进、文本出"，将来换成模型归纳时调用方不用改。
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable, Mapping, Sequence

STYLE_DIMENSIONS = ("color", "material", "silhouette", "vibe", "category")

STYLE_DIMENSION_LABELS = {
    "color": "色系",
    "material": "材质",
    "silhouette": "廓形",
    "vibe": "风格",
    "category": "常见单品",
}

_COLOR_WORDS = (
    "低饱和", "米白", "米色", "杏色", "卡其", "酒红", "藏青", "墨绿", "浅蓝", "深蓝",
    "浅灰", "深灰", "浅粉", "粉色", "黑色", "白色", "灰色", "红色", "蓝色",
    "绿色", "黄色", "紫色", "棕色", "银色", "金色", "撞色", "同色系",
)
_MATERIAL_WORDS = (
    "雪纺", "薄纱", "蕾丝", "针织", "真丝", "缎面", "漆皮", "亮面", "亚光", "哑光",
    "皮革", "牛仔", "羊毛", "羊绒", "毛呢", "棉质", "亚麻", "网纱", "丝绒", "纱质",
    "棉", "麻", "绒", "纱", "皮质",
)
_SILHOUETTE_WORDS = (
    "oversize", "收腰", "高腰", "落肩", "宽松", "修身", "短款", "长款", "直筒", "百褶",
    "层叠", "多层次", "蓬松", "A字", "荷叶边", "喇叭", "泡泡袖", "连帽", "斗篷",
)
_VIBE_WORDS = (
    "洛丽塔", "暗黑", "甜酷", "甜美", "温柔", "冷淡", "学院", "复古", "和风", "通勤",
    "居家", "辣妹", "舞台感", "cosplay", "日常", "秋冬", "春夏", "少女", "朋克",
)
_CATEGORY_WORDS = (
    "连衣裙", "半身裙", "衬衫", "开衫", "长裤", "短裤", "外套", "大衣", "毛衣", "卫衣",
    "连体衣", "连裤袜", "过膝袜", "短袜", "高跟鞋", "厚底鞋", "玛丽珍鞋", "乐福鞋",
    "靴子", "木屐", "发饰", "头饰", "眼镜", "围巾", "帽子", "背包",
)

_WORD_TABLE: dict[str, tuple[str, ...]] = {
    "color": _COLOR_WORDS,
    "material": _MATERIAL_WORDS,
    "silhouette": _SILHOUETTE_WORDS,
    "vibe": _VIBE_WORDS,
    "category": _CATEGORY_WORDS,
}

# 一个词同时命中多个维度时按先后顺序归属，避免"纱"既算材质又算廓形
_DIMENSION_ORDER = ("material", "silhouette", "color", "vibe", "category")

_WHITESPACE = re.compile(r"\s+")
MAX_PROFILE_ITEMS = 6
MAX_PROFILE_CHARS = 160
MIN_REFERENCES_FOR_PROFILE = 3


def _clean(value: Any, limit: int = 400) -> str:
    text = _WHITESPACE.sub(" ", str(value or "")).strip()
    return text[:limit]


def collect_reference_texts(references: Iterable[Mapping[str, Any]] | None) -> list[str]:
    """Flatten reference records into plain texts for mining."""

    texts: list[str] = []
    for row in references or ():
        if not isinstance(row, Mapping):
            continue
        parts = [
            _clean(row.get("name"), 120),
            _clean(row.get("description") or row.get("style"), 400),
        ]
        for tag in row.get("tags") or ():
            parts.append(_clean(tag, 40))
        joined = " ".join(part for part in parts if part)
        if joined:
            texts.append(joined)
    return texts


def extract_style_profile(
    texts: Sequence[str] | None,
    *,
    top: int = MAX_PROFILE_ITEMS,
    min_count: int = 2,
) -> dict[str, Any]:
    """Mine a compact style profile from reference texts.

    返回 {维度: [词...], "evidence_count": n}；只保留出现次数 >= min_count
    的词，避免把一次性描述当成风格。
    """

    payload = [text for text in (texts or ()) if str(text or "").strip()]
    counters: dict[str, Counter[str]] = {dim: Counter() for dim in STYLE_DIMENSIONS}
    for text in payload:
        haystack = str(text).casefold()
        for dim in _DIMENSION_ORDER:
            for word in _WORD_TABLE[dim]:
                if word.casefold() in haystack:
                    counters[dim][word] += 1
    profile: dict[str, Any] = {}
    for dim in STYLE_DIMENSIONS:
        rows = [(word, count) for word, count in counters[dim].items() if count >= min_count]
        rows.sort(key=lambda item: (-item[1], item[0]))
        profile[dim] = [word for word, _ in rows[:top]]
    profile["evidence_count"] = len(payload)
    return profile


def render_style_profile(profile: Mapping[str, Any] | None, *, max_chars: int = MAX_PROFILE_CHARS) -> str:
    """Render a profile as one short injectable line (empty when too little evidence)."""

    if not isinstance(profile, Mapping):
        return ""
    evidence = int(profile.get("evidence_count") or 0)
    if evidence < MIN_REFERENCES_FOR_PROFILE:
        return ""
    segments: list[str] = []
    for dim in ("vibe", "color", "silhouette", "material", "category"):
        words = [str(word) for word in (profile.get(dim) or ()) if str(word).strip()]
        if not words:
            continue
        segments.append("/".join(words[:4]))
    if not segments:
        return ""
    body = "、".join(segments)
    text = f"参考风格（来自 {evidence} 套参考）：{body}"
    if len(text) > max_chars:
        text = text[: max(0, max_chars - 1)].rstrip("、/") + "…"
    return text


def build_style_profile(
    references: Iterable[Mapping[str, Any]] | None,
    *,
    top: int = MAX_PROFILE_ITEMS,
    min_count: int = 2,
) -> dict[str, Any]:
    """Convenience: references in, profile out."""

    return extract_style_profile(
        collect_reference_texts(references), top=top, min_count=min_count
    )


def render_reference_profile(
    references: Iterable[Mapping[str, Any]] | None,
    *,
    top: int = MAX_PROFILE_ITEMS,
    min_count: int = 2,
    max_chars: int = MAX_PROFILE_CHARS,
) -> str:
    """Convenience: references in, one-line profile out (or an empty string)."""

    return render_style_profile(
        build_style_profile(references, top=top, min_count=min_count), max_chars=max_chars
    )


__all__ = [
    "MIN_REFERENCES_FOR_PROFILE",
    "STYLE_DIMENSIONS",
    "STYLE_DIMENSION_LABELS",
    "build_style_profile",
    "collect_reference_texts",
    "extract_style_profile",
    "render_reference_profile",
    "render_style_profile",
]
