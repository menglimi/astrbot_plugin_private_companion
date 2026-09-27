# -*- coding: utf-8 -*-
"""photo_wardrobe_decision 拆分件 part02（机械搬移，行为不变）。

函数 / 常量 / 类逐字搬自 photo_wardrobe_decision.py，仅调整模块级依赖的导入来源。
"""
import re
from collections.abc import Collection, Mapping
from typing import Any

try:  # package import
    from .photo_wardrobe_decision_part01 import (
        PhotoWardrobeIntent,
        _CATEGORY_LABELS,
        _CATEGORY_PRESETS,
        _EDIT_WORKFLOWS,
        _SELFIE_WORKFLOWS,
        _clean_text,
        _preset_category,
        _scene_without_daily_outfit_details,
    )
except ImportError:  # direct test/import from the plugin directory
    from photo_wardrobe_decision_part01 import (
        PhotoWardrobeIntent,
        _CATEGORY_LABELS,
        _CATEGORY_PRESETS,
        _EDIT_WORKFLOWS,
        _SELFIE_WORKFLOWS,
        _clean_text,
        _preset_category,
        _scene_without_daily_outfit_details,
    )


def _location_categories_conflict(requested: set[str], ambient: set[str]) -> bool:
    if not requested or not ambient:
        return False
    generic = {"home", "school", "outdoor"}
    requested_specific = requested - generic
    ambient_specific = ambient - generic
    if requested_specific and ambient_specific:
        return requested_specific.isdisjoint(ambient_specific)
    return requested.isdisjoint(ambient)


def _scene_without_ambient_location_fields(scene_context: str) -> str:
    text = _clean_text(scene_context, 2400)
    if not text:
        return ""
    labels = (
        "视觉话题|时间|状态|当前日程|日程|情绪|可分享碎片|当前位置|地点|位置|"
        "当前场景|场景|天气背景|天气|背景|最近自拍|今日穿搭|当天基础穿搭|当天穿搭|日常穿搭|"
        "发型|发色|瞳色|表情|风格"
    )
    parts = re.split(rf"[；;,，]\s*(?=(?:{labels})\s*[：:])", text, flags=re.I)
    kept = [
        part.strip("；;,， ")
        for part in parts
        if part.strip("；;,， ")
        and not re.match(
            r"(?:当前日程|日程|当前位置|地点|位置|当前场景|场景)\s*[：:]",
            part.strip("；;,， "),
            flags=re.I,
        )
    ]
    return _clean_text("；".join(kept), 2400)


def _prompt_without_generated_daily_outfit_continuity(prompt_text: str) -> str:
    text = str(prompt_text or "")
    replacements = (
        (
            r"keep today's outfit and character appearance consistent with the reference image",
            "keep character identity and stable appearance consistent with the selected reference image",
        ),
        (
            r"keep today's outfit and character appearance consistent with available visual continuity",
            "keep character identity and stable appearance consistent with available visual continuity",
        ),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.I)
    visual_memory_pattern = re.compile(
        r"(visual continuity reference:\s*)(.*?)"
        r"(?=,\s*(?:additional generation preference:|keep character identity|the user's explicit clothing)|\.\s*Negative prompt:|$)",
        flags=re.I | re.S,
    )

    def clean_visual_memory(match: re.Match[str]) -> str:
        cleaned = _scene_without_daily_outfit_details(match.group(2))
        return f"{match.group(1)}{cleaned}" if cleaned else ""

    text = visual_memory_pattern.sub(clean_visual_memory, text)
    return re.sub(r",\s*,+", ",", text)


def _outfit_label(category: str) -> str:
    return _CATEGORY_LABELS.get(_clean_text(category, 80).lower(), "the requested outfit")


def _explicit_mirror_request(text: str) -> bool:
    raw = _clean_text(text, 1200)
    if not raw:
        return False
    detection_text = re.split(r"negative prompt\s*:", raw.lower(), maxsplit=1, flags=re.I)[0]
    positive_scan = re.sub(
        r"(?:不要|避免|别|不许|禁止).{0,18}(?:镜前|对镜|镜中|镜子|全身镜|穿衣镜|试衣镜)",
        " ",
        detection_text,
        flags=re.I,
    )
    positive_scan = re.sub(
        r"(?:no|not|avoid|without)\s+(?:a\s+)?(?:mirror|mirror\s+selfie|full[-\s]?length\s+mirror|"
        r"full[-\s]?body\s+mirror|mirror\s+shot|mirror\s+photo|mirror\s+portrait)[^,.;；。]*",
        " ",
        positive_scan,
        flags=re.I,
    )
    positive_scan = re.sub(r"\bnon[-\s]?mirror\b", " ", positive_scan, flags=re.I)
    positive_scan = re.sub(r"unless[^,.;；。]*mirror[^,.;；。]*", " ", positive_scan, flags=re.I)
    return bool(
        re.search(
            r"镜前|对镜|镜中|镜子|全身镜|穿衣镜|试衣镜|\bmirror\b|looking\s+in\s+the\s+mirror|in\s+front\s+of\s+(?:a\s+)?mirror",
            positive_scan,
            flags=re.I,
        )
    )


def _automatic_presets(
    workflow_kind: str,
    intent: PhotoWardrobeIntent,
    excluded_categories: Collection[str],
) -> tuple[str, ...]:
    kind = _clean_text(workflow_kind, 40).lower()
    if kind in _EDIT_WORKFLOWS:
        return ()
    excluded = set(excluded_categories) | set(intent.excluded_categories)
    target_preset = _CATEGORY_PRESETS.get(intent.target_category, "")
    if target_preset and intent.target_category not in excluded:
        return (target_preset,)
    text = intent.positive_text.lower()
    if kind in _SELFIE_WORKFLOWS:
        if any(token in text for token in ("表情包", "贴纸", "sticker", "meme")):
            return ("表情包场景",)
        if re.search(r"(?<![a-z0-9])cos(?:play)?(?![a-z0-9])|角色扮演|扮成|神灯|女仆|巫女|魔法少女", text, flags=re.I):
            return ("COS自拍",)
        if _explicit_mirror_request(text):
            return ("镜前穿搭",)
        if any(token in text for token in ("穿搭", "衣服", "外套", "校服", "裙", "outfit", "clothes", "jacket", "uniform", "skirt")):
            return ("日常穿搭",)
        if any(token in text for token in ("头像", "特写", "大头", "avatar", "close-up", "closeup", "profile picture")):
            return ("头像特写",)
        return ("角色自拍",)
    if any(token in text for token in ("表情包", "贴纸", "sticker", "meme")):
        return ("表情包场景",)
    if any(token in text for token in ("房间", "桌", "书", "杯", "床", "窗边", "室内", "room", "desk", "book", "cup", "bed", "window", "indoor")):
        return ("房间日常",)
    return ("可拍画面",)


def _explicit_prompt_preset(workflow_kind: str, intent: PhotoWardrobeIntent) -> str:
    kind = _clean_text(workflow_kind, 40).lower()
    text = intent.positive_text.lower()
    if any(token in text for token in ("表情包", "贴纸", "sticker", "meme")):
        return "表情包场景"
    if kind in _SELFIE_WORKFLOWS:
        if _explicit_mirror_request(text):
            return "镜前穿搭"
        if any(token in text for token in ("头像", "特写", "大头", "avatar", "close-up", "closeup", "profile picture")):
            return "头像特写"
        return ""
    if any(token in text for token in ("房间", "桌", "书", "杯", "床", "窗边", "室内", "room", "desk", "book", "cup", "bed", "window", "indoor")):
        return "房间日常"
    return ""


def _selected_presets(
    *,
    workflow_kind: str,
    intent: PhotoWardrobeIntent,
    preset_name: str,
    available_presets: Collection[str],
    excluded_categories: Collection[str],
) -> tuple[str, ...]:
    available = {_clean_text(name, 80) for name in available_presets if _clean_text(name, 80)}
    if preset_name and preset_name in available:
        return (preset_name,)
    return tuple(
        name
        for name in _automatic_presets(workflow_kind, intent, excluded_categories)
        if name in available
    )[:1]


def _validated_reference_preferred_preset(
    value: Any,
    *,
    available_presets: set[str],
    excluded_categories: set[str],
    outfit_category: str = "",
    adjustments: list[str],
) -> str:
    preferred_preset = _clean_text(value, 60)
    if not preferred_preset:
        return ""
    if preferred_preset not in available_presets:
        adjustments.append("reference_preferred_preset_unknown")
        return ""

    preferred_category = _preset_category(preferred_preset)
    if preferred_category and preferred_category in excluded_categories:
        adjustments.append("reference_preferred_preset_user_conflict")
        return ""
    if (
        outfit_category
        and outfit_category != "reference_outfit"
        and preferred_category
        and preferred_category != outfit_category
    ):
        adjustments.append("reference_preferred_preset_conflict")
        return ""
    return preferred_preset
