from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from typing import Any


DECISION_VERSION = 1

_SELFIE_WORKFLOWS = {"selfie", "portrait", "自拍", "人像"}
_EDIT_WORKFLOWS = {"edit", "改图", "修图", "重绘", "p图"}
_DAILY_OUTFIT_PATTERN = re.compile(
    r"(?:今日穿搭|当天基础穿搭|当天穿搭|日常穿搭|today'?s outfit|daily outfit)\s*[：:]",
    flags=re.I,
)
_OUTFIT_PATTERNS = (
    ("cosplay", r"(?<![a-z0-9])cos(?:play)?(?![a-z0-9])|角色扮演|扮成|女仆装|巫女服|魔法少女|表演服"),
    (
        "school_uniform",
        r"(?<![a-z0-9])jk\s*(?:制服|校服)"
        r"|(?:换(?:成|上|装)?|改穿|穿(?:着|上)?|身着|仍穿(?:着)?)\s*(?:一套|一身)?\s*(?<![a-z0-9])jk(?![a-z0-9])"
        r"|校服|学院制服|学生制服|school[\s_-]*uniform",
    ),
    ("sleepwear", r"睡衣|睡裙|睡袍|睡眠服|nightgown|nightdress|pajama|pyjama|sleepwear|bedtime outfit"),
    ("swimwear", r"泳装|泳衣|比基尼|swimsuit|swimwear|bikini"),
    ("sportswear", r"运动服|健身服|瑜伽服|球衣|sportswear|activewear|gym wear|jersey"),
    ("formalwear", r"礼服|晚礼服|正装|燕尾服|西装|tuxedo|formalwear|formal attire|evening gown|\bsuit\b"),
    ("homewear", r"居家服|家居服|家常服|宅家服|homewear|loungewear"),
    ("daily_outfit", r"今日穿搭|当天基础穿搭|当天穿搭|日常穿搭|today'?s outfit|daily outfit"),
)
_CATEGORY_PRESETS = {
    "sleepwear": "居家睡衣",
    "homewear": "居家服",
    "cosplay": "COS自拍",
    "school_uniform": "校服人像",
    "formalwear": "礼服人像",
    "swimwear": "泳装人像",
    "sportswear": "运动服人像",
    "daily_outfit": "日常穿搭",
    "custom_outfit": "日常穿搭",
}
_PRESET_CATEGORIES = {
    "COS自拍": "cosplay",
    "日常穿搭": "daily_outfit",
    "居家睡衣": "sleepwear",
    "居家服": "homewear",
    "校服人像": "school_uniform",
    "礼服人像": "formalwear",
    "泳装人像": "swimwear",
    "运动服人像": "sportswear",
}
_CATEGORY_LABELS = {
    "sleepwear": "sleepwear",
    "homewear": "comfortable homewear",
    "cosplay": "the explicitly requested cosplay costume",
    "school_uniform": "school uniform",
    "formalwear": "formalwear",
    "swimwear": "swimwear",
    "sportswear": "sportswear",
    "daily_outfit": "today's daily outfit",
    "reference_outfit": "the complete outfit shown in the selected reference",
    "custom_outfit": "the outfit described in the current request",
}

__all__ = [
    "PhotoWardrobeIntent",
    "PhotoWardrobeDecision",
    "analyze_photo_wardrobe",
    "merge_photo_wardrobe_continuity",
    "resolve_photo_wardrobe_decision",
]


def _clean_text(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit].rstrip() if limit > 0 else text


def _outfit_category_matches(value: Any) -> list[tuple[str, int, int, str]]:
    text = _clean_text(value, 10000).lower()
    matches: list[tuple[str, int, int, str]] = []
    for category, pattern in _OUTFIT_PATTERNS:
        for match in re.finditer(pattern, text, flags=re.I):
            resolved_category = category
            if category == "homewear" and match.group(0).lower() == "loungewear":
                context = text[max(0, match.start() - 40) : match.end() + 40]
                if "bedtime" in context:
                    resolved_category = "sleepwear"
            matches.append((resolved_category, match.start(), match.end(), match.group(0)))
    matches.sort(key=lambda item: (item[1], item[2]))
    return matches


def _preset_category(preset_name: Any) -> str:
    name = _clean_text(preset_name, 80)
    if not name:
        return ""
    matches = _outfit_category_matches(name)
    return _PRESET_CATEGORIES.get(name) or (matches[0][0] if matches else "")


def _negative_clause_content(clause: str) -> tuple[bool, str]:
    text = _clean_text(clause, 4000).strip(" ,.;；。，")
    if not text:
        return False, ""
    text = re.sub(
        r"^(?:user\s+request|requested\s+final\s+image|用户要求|画面要求)\s*[：:]\s*",
        "",
        text,
        flags=re.I,
    ).strip()
    prefix = re.compile(
        r"^(?:请)?(?:不要|别(?:再)?(?:穿|用|选)?|不想穿|不穿|不用|不是|无需|无须|避免|禁止|不许|不得|排除|拒绝|去掉|脱下|取消)\s*"
        r"|^(?:do\s+not|don't|not|avoid|without|no|exclude|skip|remove)\s+",
        flags=re.I,
    )
    match = prefix.match(text)
    if match:
        return True, text[match.end():].strip(" ,.;；。，")
    postfix = re.compile(
        r"\s*(?:不要(?:了)?|别穿|不穿|不用|算了|就算了|除外|排除|取消|not|no)\s*$",
        flags=re.I,
    )
    match = postfix.search(text)
    if match:
        return True, text[:match.start()].strip(" ,.;；。，")
    return False, text


def _semantic_prompt_parts(prompt_text: str) -> tuple[str, str]:
    prompt = str(prompt_text or "").strip()
    positive_match = re.search(
        r"positive\s+prompt\s*:\s*(.*?)(?=negative\s+prompt\s*:|$)",
        prompt,
        flags=re.I | re.S,
    )
    if positive_match:
        positive_raw = positive_match.group(1).strip()
        negative_match = re.search(r"negative\s+prompt\s*:\s*(.*)$", prompt, flags=re.I | re.S)
        negative_raw = negative_match.group(1).strip() if negative_match else ""
    else:
        positive_raw = prompt
        negative_raw = ""

    positive_parts: list[str] = []
    negative_parts: list[str] = []

    def add_clause(raw_clause: str) -> None:
        clause = _clean_text(raw_clause, 4000).strip(" ,.;；。，")
        if not clause:
            return
        is_negative, content = _negative_clause_content(clause)
        if is_negative:
            transition = re.search(
                r"(?:但|而|不过|可是)?(?:改穿|换成|换上|换为|改为|要穿|穿上|而要)"
                r"|\b(?:but|instead|and)\s+(?:wear|change\s+into|switch\s+to|put\s+on)\b",
                content,
                flags=re.I,
            )
            if transition and transition.start() > 0:
                excluded = content[:transition.start()].strip(" ,.;；。，")
                requested = content[transition.start():].strip(" ,.;；。，")
                if excluded:
                    negative_parts.append(excluded)
                if requested:
                    positive_parts.append(requested)
                return
            if content:
                negative_parts.append(content)
            return
        if content:
            positive_parts.append(content)

    for clause in re.split(r"(?:\r?\n+|[。；;，,]+|(?<=[.!?])\s+)", positive_raw):
        add_clause(clause)
    for clause in re.split(r"(?:\r?\n+|[。；;，,]+|(?<=[.!?])\s+)", negative_raw):
        cleaned = _clean_text(clause, 4000).strip(" ,.;；。，")
        if not cleaned:
            continue
        _, content = _negative_clause_content(cleaned)
        if content:
            negative_parts.append(content)
    return ", ".join(dict.fromkeys(positive_parts)), ", ".join(dict.fromkeys(negative_parts))


def _current_user_request_parts(prompt_text: str) -> tuple[str, str]:
    raw = str(prompt_text or "")
    positive_match = re.search(
        r"positive\s+prompt\s*:\s*(.*?)(?=negative\s+prompt\s*:|$)",
        raw,
        flags=re.I | re.S,
    )
    if positive_match:
        positive_raw = positive_match.group(1)
        negative_match = re.search(r"negative\s+prompt\s*:\s*(.*)$", raw, flags=re.I | re.S)
        negative_raw = negative_match.group(1) if negative_match else ""
    else:
        positive_raw = raw
        negative_raw = ""

    marker = re.search(
        r"(?:\buser\s+request|\brequested\s+final\s+image|【最终画面需求】)\s*[：:]\s*",
        positive_raw,
        flags=re.I,
    )
    if marker:
        positive_raw = positive_raw[marker.end():]
        positive_raw = re.split(
            r",\s*(?:visible face|preserve unchanged subjects|clear main subject)\b",
            positive_raw,
            maxsplit=1,
            flags=re.I,
        )[0]
    exclusion_marker = re.search(
        r"(?:explicit\s+(?:wardrobe\s+)?exclusions?|明确排除的服装)\s*[：:]\s*(.*)$",
        positive_raw,
        flags=re.I | re.S,
    )
    if exclusion_marker:
        negative_raw = f"{negative_raw}, {exclusion_marker.group(1)}".strip(" ,")
        positive_raw = positive_raw[:exclusion_marker.start()]

    positive_text, embedded_negative = _semantic_prompt_parts(positive_raw)
    _, explicit_negative = _semantic_prompt_parts(
        f"Positive prompt: requested image. Negative prompt: {negative_raw}" if negative_raw else ""
    )
    negative_text = ", ".join(
        part for part in (embedded_negative, explicit_negative) if str(part or "").strip()
    )
    return (
        _clean_text(positive_text.strip(" \t\r\n,.;；。\"'"), 1800),
        _clean_text(negative_text.strip(" \t\r\n,.;；。\"'"), 1200),
    )


def _contains_specific_outfit_text(value: Any) -> bool:
    return bool(
        re.search(
            r"连衣裙|裙子|短裙|长裙|吊带|衬衫|外套|夹克|西装|制服|汉服|旗袍|和服|洛丽塔|"
            r"裤(?:子)?|毛衣|卫衣|T恤|背心|上衣|套装|风衣|铠甲|盔甲|甲胄|袜(?:子)?|鞋(?:子)?|"
            r"\b(?:dress|skirt|shirt|blouse|coat|jacket|suit|uniform|hoodie|sweater|pants|trousers|shorts|top|armor|armour)\b|"
            r"\btrench\s+coat\b",
            str(value or ""),
            flags=re.I,
        )
    )


@dataclass(frozen=True, slots=True)
class PhotoWardrobeIntent:
    target_category: str = ""
    target_text: str = ""
    custom_outfit: bool = False
    change_requested: bool = False
    excluded_categories: tuple[str, ...] = ()
    exclusion_text: str = ""
    positive_text: str = ""


@dataclass(frozen=True, slots=True)
class PhotoWardrobeDecision:
    decision_version: int = DECISION_VERSION
    rule_id: str = "none"
    mode: str = "none"
    source: str = "none"
    category: str = ""
    lock_outfit: bool = False
    remove_daily_outfit_context: bool = False
    preset_name: str = ""
    selected_presets: tuple[str, ...] = ()
    suggested_preset: str = ""
    preset_source: str = "none"
    suggestion_status: str = "not_provided"
    reference_image_path: str = ""
    reference_id: str = ""
    reference_kind: str = ""
    reference_roles: tuple[str, ...] = ()
    effective_reference_roles: tuple[str, ...] = ()
    positive_instruction: str = ""
    negative_instruction: str = ""
    reason: str = ""
    excluded_categories: tuple[str, ...] = ()
    excluded_outfit_text: str = ""
    requested_outfit_text: str = ""
    base_prompt: str = ""
    scene_context: str = ""
    adjustments: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        preset_name = _clean_text(self.preset_name, 80)
        selected_presets = tuple(
            _clean_text(value, 80)
            for value in (self.selected_presets or ())
            if _clean_text(value, 80)
        )
        if preset_name and not selected_presets:
            selected_presets = (preset_name,)
        elif selected_presets and not preset_name:
            preset_name = selected_presets[0]
        object.__setattr__(self, "preset_name", preset_name)
        object.__setattr__(self, "selected_presets", selected_presets)
        if self.decision_version != DECISION_VERSION:
            raise ValueError(f"unsupported wardrobe decision version: {self.decision_version}")
        if not _clean_text(self.rule_id, 80):
            raise ValueError("rule_id must not be empty")
        if self.lock_outfit and not _clean_text(self.category, 80):
            raise ValueError("locked wardrobe decision requires a category")
        if not set(self.effective_reference_roles).issubset(self.reference_roles):
            raise ValueError("effective reference roles must be a subset of reference roles")
        if len(self.selected_presets) > 1:
            raise ValueError("at most one selected preset is allowed")
        if len(set(self.selected_presets)) != len(self.selected_presets):
            raise ValueError("selected presets must be unique")
        final_preset = self.selected_presets[0] if self.selected_presets else ""
        if self.preset_name != final_preset:
            raise ValueError("preset_name must match the single selected preset")
        non_daily_category = bool(self.category and self.category != "daily_outfit")
        if (self.remove_daily_outfit_context or non_daily_category) and _DAILY_OUTFIT_PATTERN.search(
            self.scene_context
        ):
            raise ValueError("conflicting daily outfit context was not removed")
        if (self.remove_daily_outfit_context or non_daily_category) and re.search(
            r"keep today's outfit and character appearance consistent",
            self.base_prompt,
            flags=re.I,
        ):
            raise ValueError("generated daily outfit continuity was not removed")

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision_version": self.decision_version,
            "rule_id": self.rule_id,
            "mode": self.mode,
            "source": self.source,
            "category": self.category,
            "lock_outfit": self.lock_outfit,
            "remove_daily_outfit_context": self.remove_daily_outfit_context,
            "preset_name": self.preset_name,
            "selected_presets": list(self.selected_presets),
            "suggested_preset": self.suggested_preset,
            "preset_source": self.preset_source,
            "suggestion_status": self.suggestion_status,
            "reference_image_path": self.reference_image_path,
            "reference_id": self.reference_id,
            "reference_kind": self.reference_kind,
            "reference_roles": list(self.reference_roles),
            "effective_reference_roles": list(self.effective_reference_roles),
            "positive_instruction": self.positive_instruction,
            "negative_instruction": self.negative_instruction,
            "reason": self.reason,
            "excluded_categories": list(self.excluded_categories),
            "excluded_outfit_text": self.excluded_outfit_text,
            "requested_outfit_text": self.requested_outfit_text,
            "base_prompt": self.base_prompt,
            "scene_context": self.scene_context,
            "adjustments": list(self.adjustments),
        }


def analyze_photo_wardrobe(prompt_text: str) -> PhotoWardrobeIntent:
    positive_text, negative_text = _current_user_request_parts(prompt_text)
    positive_matches = _outfit_category_matches(positive_text)
    negative_matches = _outfit_category_matches(negative_text)
    target_category = positive_matches[-1][0] if positive_matches else ""
    excluded_categories = tuple(
        dict.fromkeys(category for category, *_ in negative_matches if category != target_category)
    )
    change_requested = bool(
        re.search(
            r"换(?:装|衣|成|上|为|一套|一身|件)|改穿|改成|穿上|脱下.+(?:换|穿)|"
            r"\b(?:change\s+into|switch\s+to|put\s+on|change\s+(?:the\s+)?outfit|wear\s+instead)\b",
            positive_text,
            flags=re.I,
        )
    )
    custom_outfit = bool(
        not target_category
        and (
            change_requested
            or _contains_specific_outfit_text(positive_text)
            or re.search(
                r"(?:穿|换|改).{0,12}(?:衣服|服装|衣着|穿搭|一套|一身|一件)"
                r"|\b(?:wear|wearing|change|switch).{0,24}(?:clothes|clothing|outfit|wardrobe)\b",
                positive_text,
                flags=re.I,
            )
        )
    )
    wardrobe_negative_parts = [
        part.strip()
        for part in re.split(r"[,，;；。]+", negative_text)
        if part.strip()
        and (
            _outfit_category_matches(part)
            or _contains_specific_outfit_text(part)
            or re.search(r"衣服|服装|衣着|穿搭|clothes|clothing|outfit|wardrobe", part, flags=re.I)
        )
    ]
    return PhotoWardrobeIntent(
        target_category=target_category or ("custom_outfit" if custom_outfit else ""),
        target_text=_clean_text(positive_text, 360) if target_category or custom_outfit else "",
        custom_outfit=custom_outfit,
        change_requested=change_requested,
        excluded_categories=excluded_categories,
        exclusion_text=_clean_text(", ".join(dict.fromkeys(wardrobe_negative_parts)), 360),
        positive_text=_clean_text(positive_text, 1800),
    )


def merge_photo_wardrobe_continuity(
    intent: PhotoWardrobeIntent,
    continuity_request: str,
) -> PhotoWardrobeIntent:
    """Fill an otherwise empty outfit intent from an established dialogue outfit."""
    if intent.target_category or intent.excluded_categories:
        return intent
    continuity = analyze_photo_wardrobe(continuity_request)
    if not continuity.target_category:
        return intent
    return replace(
        continuity,
        excluded_categories=intent.excluded_categories,
        exclusion_text=intent.exclusion_text,
    )


def _scene_without_daily_outfit_details(scene_context: str) -> str:
    text = _clean_text(scene_context, 2400)
    outfit_label = r"(?:今日穿搭|当天基础穿搭|当天穿搭|日常穿搭|today'?s outfit|daily outfit)"
    if not text or not re.search(rf"{outfit_label}\s*[：:]", text, flags=re.I):
        return text
    cleaned = re.sub(
        rf"(^|[；;,，])\s*{outfit_label}\s*[：:].*?(?=[；;,，]\s*(?:视觉话题|时间|状态|当前日程|日程|情绪|可分享碎片|当前位置|地点|位置|当前场景|场景|天气背景|天气|背景|最近自拍|发型|发色|瞳色|表情|风格)[：:]|$)",
        lambda match: match.group(1),
        text,
        flags=re.S | re.I,
    )
    cleaned = re.sub(r"[；;,，]{2,}", "；", cleaned).strip("；;,， ")
    return _clean_text(cleaned, 2400)


def _daily_outfit_categories(scene_context: str) -> set[str]:
    text = _clean_text(scene_context, 2400)
    match = re.search(
        r"(?:今日穿搭|当天基础穿搭|当天穿搭|日常穿搭|today'?s outfit|daily outfit)\s*[：:]\s*(.*?)"
        r"(?=[；;,，]\s*(?:视觉话题|时间|状态|当前日程|日程|情绪|可分享碎片|当前位置|地点|位置|当前场景|场景|天气背景|天气|背景|最近自拍|发型|发色|瞳色|表情|风格)\s*[：:]|$)",
        text,
        flags=re.I | re.S,
    )
    if not match:
        return set()
    return {category for category, *_ in _outfit_category_matches(match.group(1))}


def _location_categories(value: str) -> set[str]:
    text = _clean_text(value, 2400).lower()
    categories: set[str] = set()
    patterns = {
        "home": r"家里|家中|居家|\bat home\b|\bhome\b",
        "bedroom": r"卧室|床边|\bbedroom\b",
        "living_room": r"客厅|\bliving room\b",
        "dorm": r"宿舍|\bdorm(?:itory)?\b",
        "apartment": r"公寓|\bapartment\b",
        "school": r"学校|\bschool\b",
        "campus": r"校园|\bcampus\b",
        "classroom": r"教室|\bclassroom\b",
        "workplace": r"办公室|公司|工位|工作地点|工作场所|\boffice\b|\bworkplace\b",
        "park": r"公园|\bpark\b",
        "street": r"街边|街头|街道|\bstreet\b",
        "mall": r"商场|\bmall\b",
        "restaurant": r"餐厅|咖啡馆|咖啡店|\brestaurant\b|\bcafe\b",
        "library": r"图书馆|\blibrary\b",
        "gym": r"健身房|\bgym\b",
        "pool": r"泳池|游泳池|\bpool\b",
        "beach": r"海边|沙滩|\bbeach\b",
        "transit": r"车站|机场|\bstation\b|\bairport\b",
        "outdoor": r"户外|室外|外出|\boutdoors?\b",
    }
    for category, pattern in patterns.items():
        if re.search(pattern, text, flags=re.I):
            categories.add(category)
    if categories & {"bedroom", "living_room", "dorm", "apartment"}:
        categories.add("home")
    if categories & {"campus", "classroom"}:
        categories.add("school")
    if categories & {"park", "street", "beach"}:
        categories.add("outdoor")
    return categories


def _ambient_location_categories(scene_context: str) -> set[str]:
    text = _clean_text(scene_context, 2400)
    labels = (
        "视觉话题|时间|状态|当前日程|日程|情绪|可分享碎片|当前位置|地点|位置|"
        "当前场景|场景|天气背景|天气|背景|最近自拍|今日穿搭|当天基础穿搭|当天穿搭|日常穿搭|"
        "发型|发色|瞳色|表情|风格"
    )
    parts = re.split(rf"[；;,，]\s*(?=(?:{labels})\s*[：:])", text, flags=re.I)
    categories: set[str] = set()
    for part in parts:
        if re.match(
            r"(?:当前日程|日程|当前位置|地点|位置|当前场景|场景)\s*[：:]",
            part.strip("；;,， "),
            flags=re.I,
        ):
            categories.update(_location_categories(part))
    return categories


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


def resolve_photo_wardrobe_decision(
    *,
    workflow_kind: str,
    prompt_text: str,
    reference: Mapping[str, Any] | None,
    scene_context: str = "",
    suggested_scene_preset: str = "",
    workflow_default_scene_preset: str = "",
    intent: PhotoWardrobeIntent | None = None,
    base_prompt: str = "",
    available_presets: Collection[str] = (),
) -> PhotoWardrobeDecision:
    resolved_intent = intent or analyze_photo_wardrobe(prompt_text)

    normalized_kind = _clean_text(workflow_kind, 40).lower()
    reference_data = dict(reference or {})
    reference_path = _clean_text(reference_data.get("path"), 1000)
    reference_id = _clean_text(reference_data.get("id"), 60)
    reference_kind = _clean_text(reference_data.get("kind"), 40)
    roles = tuple(str(role) for role in (reference_data.get("reference_roles") or ()))
    effective_roles = roles
    adjustments: list[str] = []
    reference_category = _clean_text(reference_data.get("outfit_category"), 40).lower()
    reference_locks = bool(reference_data.get("outfit_lock_default")) and "outfit" in roles
    suggested_preset = _clean_text(suggested_scene_preset, 80)
    suggested_category = _preset_category(suggested_preset)
    available = {
        _clean_text(name, 80)
        for name in available_presets
        if _clean_text(name, 80)
    }
    excluded_categories = set(resolved_intent.excluded_categories)
    workflow_default_preset = _clean_text(workflow_default_scene_preset, 80)
    if (
        workflow_default_preset not in available
        or _preset_category(workflow_default_preset) in excluded_categories
    ):
        workflow_default_preset = ""
    requested_locations = _location_categories(resolved_intent.positive_text)
    ambient_locations = _ambient_location_categories(scene_context)
    if normalized_kind in _SELFIE_WORKFLOWS and _location_categories_conflict(
        requested_locations,
        ambient_locations,
    ):
        cleaned_scene = _scene_without_ambient_location_fields(scene_context)
        if cleaned_scene != _clean_text(scene_context, 2400):
            scene_context = cleaned_scene
            adjustments.append("ambient_location_context_removed")

    if normalized_kind not in _SELFIE_WORKFLOWS:
        explicit_prompt_preset = _explicit_prompt_preset(workflow_kind, resolved_intent)
        suggestion_conflicts_with_user = bool(
            suggested_category
            and suggested_category in excluded_categories
        )
        if normalized_kind in _EDIT_WORKFLOWS:
            selected = ()
            preset_source = "none"
            suggestion_status = "rejected_workflow" if suggested_preset else "not_provided"
        elif explicit_prompt_preset and explicit_prompt_preset in available:
            selected = (explicit_prompt_preset,)
            preset_source = "user_prompt"
            suggestion_status = (
                "not_provided"
                if not suggested_preset
                else (
                    "rejected_user_conflict"
                    if suggestion_conflicts_with_user
                    else ("accepted" if suggested_preset in selected else "shadowed_by_user")
                )
            )
        elif suggested_preset and suggested_preset in available and not suggestion_conflicts_with_user:
            selected = (suggested_preset,)
            preset_source = "tool_suggestion"
            suggestion_status = "accepted"
        elif workflow_default_preset:
            selected = (workflow_default_preset,)
            preset_source = "workflow_default"
            suggestion_status = (
                "rejected_user_conflict"
                if suggestion_conflicts_with_user
                else ("rejected_unknown" if suggested_preset else "not_provided")
            )
        elif normalized_kind == "sticker" and "表情包场景" in available:
            selected = ("表情包场景",)
            preset_source = "workflow_default"
            suggestion_status = (
                "rejected_user_conflict"
                if suggestion_conflicts_with_user
                else ("rejected_unknown" if suggested_preset else "not_provided")
            )
        else:
            selected = tuple(
                name
                for name in _automatic_presets(
                    workflow_kind,
                    resolved_intent,
                    resolved_intent.excluded_categories,
                )
                if name in available
            )[:1]
            preset_source = "workflow_default" if selected else "none"
            suggestion_status = (
                "rejected_user_conflict"
                if suggestion_conflicts_with_user
                else ("rejected_unknown" if suggested_preset else "not_provided")
            )
        preset_name = selected[0] if selected else ""
        return PhotoWardrobeDecision(
            rule_id="non_selfie_source_edit" if normalized_kind in _EDIT_WORKFLOWS and reference_path else "non_selfie",
            mode="source_edit" if normalized_kind in _EDIT_WORKFLOWS and reference_path else "none",
            source="explicit_reference" if reference_path else "none",
            preset_name=preset_name,
            selected_presets=selected,
            suggested_preset=suggested_preset,
            preset_source=preset_source,
            suggestion_status=suggestion_status,
            reference_image_path=reference_path,
            reference_id=reference_id,
            reference_kind=reference_kind,
            reference_roles=roles,
            effective_reference_roles=roles,
            reason="non-selfie workflow keeps its own image-edit contract",
            excluded_categories=resolved_intent.excluded_categories,
            excluded_outfit_text=resolved_intent.exclusion_text,
            base_prompt=str(base_prompt or prompt_text or "").strip(),
            scene_context=_clean_text(scene_context, 2400),
            adjustments=tuple(adjustments),
        )

    explicit_category = resolved_intent.target_category
    if explicit_category:
        if (
            explicit_category == "custom_outfit"
            or not reference_category
            or (reference_category and reference_category != explicit_category)
        ) and "outfit" in effective_roles:
            effective_roles = tuple(role for role in effective_roles if role != "outfit")
            adjustments.append("reference_outfit_role_removed")
        remove_daily = explicit_category != "daily_outfit"
        cleaned_scene = _clean_text(scene_context, 2400)
        cleaned_prompt = str(base_prompt or prompt_text or "").strip()
        if remove_daily and _DAILY_OUTFIT_PATTERN.search(cleaned_scene):
            updated_scene = _scene_without_daily_outfit_details(cleaned_scene)
            if updated_scene != cleaned_scene:
                cleaned_scene = updated_scene
                adjustments.append("daily_outfit_context_removed")
        if remove_daily:
            updated_prompt = _prompt_without_generated_daily_outfit_continuity(cleaned_prompt)
            if updated_prompt != cleaned_prompt:
                cleaned_prompt = updated_prompt
                adjustments.append("generated_daily_outfit_continuity_removed")
        explicit_prompt_preset = _explicit_prompt_preset(workflow_kind, resolved_intent)
        preset_name = (
            explicit_prompt_preset
            if explicit_prompt_preset in available
            else _CATEGORY_PRESETS.get(explicit_category, "")
        )
        selected = _selected_presets(
            workflow_kind=workflow_kind,
            intent=resolved_intent,
            preset_name=preset_name,
            available_presets=available_presets,
            excluded_categories=resolved_intent.excluded_categories,
        )
        preset_name = selected[0] if selected else ""
        exclusion_instruction = (
            f"Respect the current request's explicit wardrobe exclusions: {resolved_intent.exclusion_text}."
            if resolved_intent.exclusion_text
            else ""
        )
        return PhotoWardrobeDecision(
            rule_id="explicit_prompt",
            mode="explicit_prompt",
            source="user_prompt",
            category=explicit_category,
            lock_outfit=True,
            remove_daily_outfit_context=remove_daily,
            preset_name=preset_name,
            selected_presets=selected,
            suggested_preset=suggested_preset,
            preset_source=(
                "user_prompt"
                if explicit_prompt_preset in selected
                else ("wardrobe_category" if selected else "none")
            ),
            suggestion_status=(
                "not_provided"
                if not suggested_preset
                else (
                    "rejected_unknown"
                    if suggested_preset not in available
                    else (
                        "accepted"
                        if suggested_preset in selected
                        else (
                            "rejected_user_conflict"
                            if suggested_category and suggested_category != explicit_category
                            else "shadowed_by_user"
                        )
                    )
                )
            ),
            reference_image_path=reference_path,
            reference_id=reference_id,
            reference_kind=reference_kind,
            reference_roles=roles,
            effective_reference_roles=effective_roles,
            positive_instruction=(
                "An explicit clothing request in this prompt has highest priority. "
                f"Render one coherent {_outfit_label(explicit_category)} outfit exactly as requested; "
                "use any incompatible selected reference only for identity and compatible visual details."
            ),
            negative_instruction=" ".join(
                part
                for part in (
                    (
                        "Do not restore clothing from today's outfit, schedule context, an older photo, or an incompatible reference."
                        if remove_daily
                        else "Do not replace today's requested outfit with an unrelated costume or wardrobe."
                    ),
                    exclusion_instruction,
                )
                if part
            ),
            reason=(
                "explicit custom or generic clothing change in the current image prompt"
                if explicit_category == "custom_outfit"
                else "explicit clothing request in the current image prompt"
            ),
            excluded_categories=resolved_intent.excluded_categories,
            excluded_outfit_text=resolved_intent.exclusion_text,
            requested_outfit_text=resolved_intent.target_text,
            base_prompt=cleaned_prompt,
            scene_context=cleaned_scene,
            adjustments=tuple(adjustments),
        )

    excluded_daily_context_removed = bool(
        excluded_categories & _daily_outfit_categories(scene_context)
    )
    if excluded_daily_context_removed:
        cleaned_scene = _scene_without_daily_outfit_details(scene_context)
        if cleaned_scene != _clean_text(scene_context, 2400):
            scene_context = cleaned_scene
            adjustments.append("daily_outfit_context_removed")
        cleaned_prompt = _prompt_without_generated_daily_outfit_continuity(
            str(base_prompt or prompt_text or "").strip()
        )
        if cleaned_prompt != str(base_prompt or prompt_text or "").strip():
            adjustments.append("generated_daily_outfit_continuity_removed")
        base_prompt = cleaned_prompt
        if reference_kind == "daily_outfit" and "outfit" in effective_roles:
            effective_roles = tuple(role for role in effective_roles if role != "outfit")
            adjustments.append("reference_outfit_role_removed")
            reference_locks = False

    if excluded_categories and reference_locks and not reference_category:
        effective_roles = tuple(role for role in effective_roles if role != "outfit")
        adjustments.append("reference_outfit_role_removed")
        reference_locks = False

    if reference_category and reference_category in excluded_categories:
        if "outfit" in effective_roles:
            effective_roles = tuple(role for role in effective_roles if role != "outfit")
            adjustments.append("reference_outfit_role_removed")
        explicit_prompt_preset = _explicit_prompt_preset(workflow_kind, resolved_intent)
        preferred_preset = _validated_reference_preferred_preset(
            reference_data.get("preferred_preset"),
            available_presets=available,
            excluded_categories=excluded_categories,
            outfit_category=reference_category,
            adjustments=adjustments,
        )
        suggestion_compatible = bool(
            suggested_preset
            and suggested_preset in available
            and suggested_category not in excluded_categories
        )
        if explicit_prompt_preset and explicit_prompt_preset in available:
            preset_name = explicit_prompt_preset
            preset_source = "user_prompt"
        elif preferred_preset:
            preset_name = preferred_preset
            preset_source = "reference_preferred"
        elif suggestion_compatible:
            preset_name = suggested_preset
            preset_source = "tool_suggestion"
        elif workflow_default_preset:
            preset_name = workflow_default_preset
            preset_source = "workflow_default"
        else:
            preset_name = ""
            preset_source = "none"
        selected = _selected_presets(
            workflow_kind=workflow_kind,
            intent=resolved_intent,
            preset_name=preset_name,
            available_presets=available_presets,
            excluded_categories=resolved_intent.excluded_categories,
        )
        preset_name = selected[0] if selected else ""
        if preset_name and preset_source == "none":
            preset_source = "workflow_default"
        selected_category = _preset_category(preset_name)
        remove_daily = bool(
            reference_category == "daily_outfit"
            or excluded_daily_context_removed
            or (selected_category and selected_category != "daily_outfit")
        )
        cleaned_scene = _clean_text(scene_context, 2400)
        cleaned_prompt = str(base_prompt or prompt_text or "").strip()
        if remove_daily and _DAILY_OUTFIT_PATTERN.search(cleaned_scene):
            updated_scene = _scene_without_daily_outfit_details(cleaned_scene)
            if updated_scene != cleaned_scene:
                cleaned_scene = updated_scene
                adjustments.append("daily_outfit_context_removed")
        if remove_daily:
            updated_prompt = _prompt_without_generated_daily_outfit_continuity(cleaned_prompt)
            if updated_prompt != cleaned_prompt:
                cleaned_prompt = updated_prompt
                adjustments.append("generated_daily_outfit_continuity_removed")
        exclusion_instruction = (
            f"Respect the current request's explicit wardrobe exclusions: {resolved_intent.exclusion_text}."
            if resolved_intent.exclusion_text
            else ""
        )
        return PhotoWardrobeDecision(
            rule_id="explicit_exclusion",
            mode="explicit_exclusion",
            source="user_prompt",
            category=selected_category,
            lock_outfit=bool(selected_category),
            remove_daily_outfit_context=remove_daily,
            preset_name=preset_name,
            selected_presets=selected,
            suggested_preset=suggested_preset,
            preset_source=preset_source,
            suggestion_status=(
                "rejected_user_conflict"
                if suggested_preset and suggested_category in excluded_categories
                else (
                    "rejected_unknown"
                    if suggested_preset and suggested_preset not in available
                    else (
                        "accepted"
                        if suggested_preset and suggested_preset in selected
                        else (
                            "shadowed_by_user"
                            if suggested_preset and explicit_prompt_preset in selected
                            else (
                                "shadowed_by_reference"
                                if suggested_preset and preferred_preset in selected
                                else ("rejected_user_conflict" if suggested_preset else "not_provided")
                            )
                        )
                    )
                )
            ),
            reference_image_path=reference_path,
            reference_id=reference_id,
            reference_kind=reference_kind,
            reference_roles=roles,
            effective_reference_roles=effective_roles,
            positive_instruction=(
                "Use the selected reference for identity and other compatible responsibilities only; "
                "its outfit is explicitly excluded by the current request."
                + (
                    f" Render one coherent {_outfit_label(selected_category)} outfit from the selected preset."
                    if selected_category
                    else ""
                )
            ),
            negative_instruction=exclusion_instruction,
            reason="selected reference outfit is explicitly excluded by the current request",
            excluded_categories=resolved_intent.excluded_categories,
            excluded_outfit_text=resolved_intent.exclusion_text,
            base_prompt=cleaned_prompt,
            scene_context=cleaned_scene,
            adjustments=tuple(adjustments),
        )

    if reference_kind == "daily_outfit" and not excluded_daily_context_removed:
        explicit_prompt_preset = _explicit_prompt_preset(workflow_kind, resolved_intent)
        preferred_preset = _validated_reference_preferred_preset(
            reference_data.get("preferred_preset"),
            available_presets=available,
            excluded_categories=excluded_categories,
            outfit_category="daily_outfit",
            adjustments=adjustments,
        )
        suggestion_compatible = bool(
            suggested_preset
            and suggested_preset in available
            and suggested_category not in excluded_categories
            and (not suggested_category or suggested_category == "daily_outfit")
        )
        if explicit_prompt_preset and explicit_prompt_preset in available:
            preset_name = explicit_prompt_preset
        elif preferred_preset:
            preset_name = preferred_preset
        elif suggestion_compatible:
            preset_name = suggested_preset
        else:
            preset_name = _CATEGORY_PRESETS["daily_outfit"]
        selected = _selected_presets(
            workflow_kind=workflow_kind,
            intent=resolved_intent,
            preset_name=preset_name,
            available_presets=available_presets,
            excluded_categories=resolved_intent.excluded_categories,
        )
        preset_name = selected[0] if selected else ""
        exclusion_instruction = (
            f"Respect the current request's explicit wardrobe exclusions: {resolved_intent.exclusion_text}."
            if resolved_intent.exclusion_text
            else ""
        )
        return PhotoWardrobeDecision(
            rule_id="daily_outfit_reference",
            mode="daily_outfit",
            source="selected_reference",
            category="daily_outfit",
            lock_outfit=True,
            preset_name=preset_name,
            selected_presets=selected,
            suggested_preset=suggested_preset,
            preset_source=(
                "user_prompt"
                if explicit_prompt_preset in selected
                else (
                    "reference_preferred"
                    if preferred_preset in selected
                    else (
                        "tool_suggestion"
                        if suggested_preset in selected
                        else ("wardrobe_category" if selected else "none")
                    )
                )
            ),
            suggestion_status=(
                "accepted"
                if suggested_preset and suggested_preset in selected
                else (
                    "rejected_unknown"
                    if suggested_preset and suggested_preset not in available
                    else (
                        "shadowed_by_user"
                        if suggested_preset and explicit_prompt_preset in selected
                        else (
                            "rejected_reference_conflict"
                            if suggested_preset and suggested_category and suggested_category != "daily_outfit"
                            else ("shadowed_by_reference" if suggested_preset else "not_provided")
                        )
                    )
                )
            ),
            reference_image_path=reference_path,
            reference_id=reference_id,
            reference_kind=reference_kind,
            reference_roles=roles,
            effective_reference_roles=roles,
            positive_instruction=(
                "Use the selected reference as the authoritative source for today's complete outfit and identity continuity. "
                "Preserve its coherent clothing layers, accessories, silhouette, and main color palette."
            ),
            negative_instruction=" ".join(
                part
                for part in (
                    "Do not invent an alternative outfit or mix several wardrobe variants.",
                    exclusion_instruction,
                )
                if part
            ),
            reason="selected reference is today's outfit reference",
            excluded_categories=resolved_intent.excluded_categories,
            excluded_outfit_text=resolved_intent.exclusion_text,
            base_prompt=str(base_prompt or prompt_text or "").strip(),
            scene_context=_clean_text(scene_context, 2400),
            adjustments=tuple(adjustments),
        )

    if reference_kind == "recent_sent_photo" and reference_locks:
        category = reference_category or "reference_outfit"
        remove_daily = category != "daily_outfit" or excluded_daily_context_removed
        cleaned_scene = _clean_text(scene_context, 2400)
        cleaned_prompt = str(base_prompt or prompt_text or "").strip()
        if remove_daily and _DAILY_OUTFIT_PATTERN.search(cleaned_scene):
            updated_scene = _scene_without_daily_outfit_details(cleaned_scene)
            if updated_scene != cleaned_scene:
                cleaned_scene = updated_scene
                adjustments.append("daily_outfit_context_removed")
        if remove_daily:
            updated_prompt = _prompt_without_generated_daily_outfit_continuity(cleaned_prompt)
            if updated_prompt != cleaned_prompt:
                cleaned_prompt = updated_prompt
                adjustments.append("generated_daily_outfit_continuity_removed")
        available = {
            _clean_text(name, 80)
            for name in available_presets
            if _clean_text(name, 80)
        }
        explicit_prompt_preset = _explicit_prompt_preset(workflow_kind, resolved_intent)
        preferred_preset = _validated_reference_preferred_preset(
            reference_data.get("preferred_preset"),
            available_presets=available,
            excluded_categories=excluded_categories,
            outfit_category=category,
            adjustments=adjustments,
        )
        suggestion_compatible = bool(
            suggested_preset
            and suggested_preset in available
            and suggested_category not in set(resolved_intent.excluded_categories)
            and (
                not suggested_category
                or (category != "reference_outfit" and suggested_category == category)
            )
        )
        if explicit_prompt_preset and explicit_prompt_preset in available:
            preset_name = explicit_prompt_preset
        elif preferred_preset and preferred_preset in available:
            preset_name = preferred_preset
        elif suggestion_compatible:
            preset_name = suggested_preset
        else:
            preset_name = _CATEGORY_PRESETS.get(category, "")
        selected = _selected_presets(
            workflow_kind=workflow_kind,
            intent=resolved_intent,
            preset_name=preset_name,
            available_presets=available_presets,
            excluded_categories=resolved_intent.excluded_categories,
        )
        preset_name = selected[0] if selected else ""
        exclusion_instruction = (
            f"Respect the current request's explicit wardrobe exclusions: {resolved_intent.exclusion_text}."
            if resolved_intent.exclusion_text
            else ""
        )
        return PhotoWardrobeDecision(
            rule_id="recent_photo_continuity",
            mode="continuity",
            source="selected_reference",
            category=category,
            lock_outfit=True,
            remove_daily_outfit_context=remove_daily,
            preset_name=preset_name,
            selected_presets=selected,
            suggested_preset=suggested_preset,
            preset_source=(
                "user_prompt"
                if explicit_prompt_preset in selected
                else (
                    "reference_preferred"
                    if preferred_preset in selected
                    else (
                        "tool_suggestion"
                        if suggested_preset in selected
                        else ("wardrobe_category" if selected else "none")
                    )
                )
            ),
            suggestion_status=(
                "accepted"
                if suggested_preset and suggested_preset in selected
                else (
                    "rejected_unknown"
                    if suggested_preset and suggested_preset not in available
                    else (
                        "rejected_reference_conflict"
                        if (
                            suggested_preset
                            and suggested_category
                            and (category == "reference_outfit" or suggested_category != category)
                        )
                        else (
                            "shadowed_by_user"
                            if suggested_preset and explicit_prompt_preset in selected
                            else ("shadowed_by_reference" if suggested_preset else "not_provided")
                        )
                    )
                )
            ),
            reference_image_path=reference_path,
            reference_id=reference_id,
            reference_kind=reference_kind,
            reference_roles=roles,
            effective_reference_roles=roles,
            positive_instruction=(
                "The last image sent in this conversation is authoritative for identity, complete outfit, room or location, "
                "lighting, and time unless the current request changes them. Use the schedule only for missing, non-conflicting details."
            ),
            negative_instruction=" ".join(
                part
                for part in (
                    "Do not relocate the scene, redesign the outfit, or replace continuity details merely because the schedule has advanced.",
                    exclusion_instruction,
                )
                if part
            ),
            reason="selected reference is the last image sent in this conversation",
            excluded_categories=resolved_intent.excluded_categories,
            excluded_outfit_text=resolved_intent.exclusion_text,
            base_prompt=cleaned_prompt,
            scene_context=cleaned_scene,
            adjustments=tuple(adjustments),
        )

    if reference_locks:
        category = reference_category or "reference_outfit"
        remove_daily = category != "daily_outfit" or excluded_daily_context_removed
        cleaned_scene = _clean_text(scene_context, 2400)
        cleaned_prompt = str(base_prompt or prompt_text or "").strip()
        if remove_daily and _DAILY_OUTFIT_PATTERN.search(cleaned_scene):
            updated_scene = _scene_without_daily_outfit_details(cleaned_scene)
            if updated_scene != cleaned_scene:
                cleaned_scene = updated_scene
                adjustments.append("daily_outfit_context_removed")
        if remove_daily:
            updated_prompt = _prompt_without_generated_daily_outfit_continuity(cleaned_prompt)
            if updated_prompt != cleaned_prompt:
                cleaned_prompt = updated_prompt
                adjustments.append("generated_daily_outfit_continuity_removed")
        available = {
            _clean_text(name, 80)
            for name in available_presets
            if _clean_text(name, 80)
        }
        explicit_prompt_preset = _explicit_prompt_preset(workflow_kind, resolved_intent)
        preferred_preset = _validated_reference_preferred_preset(
            reference_data.get("preferred_preset"),
            available_presets=available,
            excluded_categories=excluded_categories,
            outfit_category=category,
            adjustments=adjustments,
        )
        suggestion_compatible = bool(
            suggested_preset
            and suggested_preset in available
            and suggested_category not in set(resolved_intent.excluded_categories)
            and (
                not suggested_category
                or (category != "reference_outfit" and suggested_category == category)
            )
        )
        if explicit_prompt_preset and explicit_prompt_preset in available:
            preset_name = explicit_prompt_preset
        elif preferred_preset and preferred_preset in available:
            preset_name = preferred_preset
        elif suggestion_compatible:
            preset_name = suggested_preset
        else:
            preset_name = _CATEGORY_PRESETS.get(category, "")
        selected = _selected_presets(
            workflow_kind=workflow_kind,
            intent=resolved_intent,
            preset_name=preset_name,
            available_presets=available_presets,
            excluded_categories=resolved_intent.excluded_categories,
        )
        preset_name = selected[0] if selected else ""
        exclusion_instruction = (
            f"Respect the current request's explicit wardrobe exclusions: {resolved_intent.exclusion_text}."
            if resolved_intent.exclusion_text
            else ""
        )
        return PhotoWardrobeDecision(
            rule_id="locked_reference_outfit",
            mode="reference_outfit",
            source="selected_reference",
            category=category,
            lock_outfit=True,
            remove_daily_outfit_context=remove_daily,
            preset_name=preset_name,
            selected_presets=selected,
            suggested_preset=suggested_preset,
            preset_source=(
                "user_prompt"
                if explicit_prompt_preset in selected
                else (
                    "reference_preferred"
                    if preferred_preset in selected
                    else (
                        "tool_suggestion"
                        if suggested_preset in selected
                        else ("wardrobe_category" if selected else "none")
                    )
                )
            ),
            suggestion_status=(
                "accepted"
                if suggested_preset and suggested_preset in selected
                else (
                    "rejected_unknown"
                    if suggested_preset and suggested_preset not in available
                    else (
                        "rejected_reference_conflict"
                        if (
                            suggested_preset
                            and suggested_category
                            and (category == "reference_outfit" or suggested_category != category)
                        )
                        else (
                            "shadowed_by_user"
                            if suggested_preset and explicit_prompt_preset in selected
                            else ("shadowed_by_reference" if suggested_preset else "not_provided")
                        )
                    )
                )
            ),
            reference_image_path=reference_path,
            reference_id=reference_id,
            reference_kind=reference_kind,
            reference_roles=roles,
            effective_reference_roles=roles,
            positive_instruction=(
                "Use the selected reference image as the authoritative source for identity and the complete visible outfit. "
                f"Preserve {_outfit_label(category)}, including its garment category, layers, silhouette, material impression, "
                "trim details, accessories, and main color palette. The schedule context controls only location, activity, mood, lighting, and time."
            ),
            negative_instruction=" ".join(
                part
                for part in (
                    (
                        "Do not replace the selected-reference outfit with today's daytime outfit, school or commuter layers, a coat, blazer, shirt, vest, tie, or another wardrobe unless the user explicitly requests it."
                        if category in {"sleepwear", "homewear"}
                        else "Do not restore a different outfit from schedule context or today's outfit."
                    ),
                    exclusion_instruction,
                )
                if part
            ),
            reason="selected reference is an outfit-bearing reference with outfit_lock_default=true",
            excluded_categories=resolved_intent.excluded_categories,
            excluded_outfit_text=resolved_intent.exclusion_text,
            base_prompt=cleaned_prompt,
            scene_context=cleaned_scene,
            adjustments=tuple(adjustments),
        )

    identity_reference_preferred = _validated_reference_preferred_preset(
        reference_data.get("preferred_preset"),
        available_presets=available,
        excluded_categories=excluded_categories,
        adjustments=adjustments,
    )
    if (
        suggested_preset
        and suggested_preset in available
        and suggested_category
        and suggested_category not in set(resolved_intent.excluded_categories)
        and not (identity_reference_preferred and identity_reference_preferred in available)
    ):
        remove_daily = suggested_category != "daily_outfit" or excluded_daily_context_removed
        cleaned_scene = _clean_text(scene_context, 2400)
        cleaned_prompt = str(base_prompt or prompt_text or "").strip()
        if remove_daily and _DAILY_OUTFIT_PATTERN.search(cleaned_scene):
            updated_scene = _scene_without_daily_outfit_details(cleaned_scene)
            if updated_scene != cleaned_scene:
                cleaned_scene = updated_scene
                adjustments.append("daily_outfit_context_removed")
        if remove_daily:
            updated_prompt = _prompt_without_generated_daily_outfit_continuity(cleaned_prompt)
            if updated_prompt != cleaned_prompt:
                cleaned_prompt = updated_prompt
                adjustments.append("generated_daily_outfit_continuity_removed")
        return PhotoWardrobeDecision(
            rule_id="suggested_scene_preset",
            mode="suggested_preset",
            source="tool_suggestion",
            category=suggested_category,
            lock_outfit=True,
            remove_daily_outfit_context=remove_daily,
            preset_name=suggested_preset,
            selected_presets=(suggested_preset,),
            suggested_preset=suggested_preset,
            preset_source="tool_suggestion",
            suggestion_status="accepted",
            reference_image_path=reference_path,
            reference_id=reference_id,
            reference_kind=reference_kind,
            reference_roles=roles,
            effective_reference_roles=effective_roles,
            positive_instruction=(
                f"Use the compatible suggested scene preset '{suggested_preset}' as the wardrobe source because the user "
                "and selected reference do not provide a stronger outfit requirement. Render one coherent outfit."
            ),
            negative_instruction="Do not restore a conflicting outfit from schedule context or today's outfit.",
            reason="compatible tool suggestion fills an otherwise unspecified wardrobe",
            excluded_categories=resolved_intent.excluded_categories,
            excluded_outfit_text=resolved_intent.exclusion_text,
            base_prompt=cleaned_prompt,
            scene_context=cleaned_scene,
            adjustments=tuple(adjustments),
        )

    if _DAILY_OUTFIT_PATTERN.search(str(scene_context or "")):
        explicit_prompt_preset = _explicit_prompt_preset(workflow_kind, resolved_intent)
        suggestion_allowed = bool(
            suggested_preset
            and suggested_preset in available
            and not suggested_category
        )
        if explicit_prompt_preset and explicit_prompt_preset in available:
            selected = (explicit_prompt_preset,)
            preset_source = "user_prompt"
        elif identity_reference_preferred and identity_reference_preferred in available:
            selected = (identity_reference_preferred,)
            preset_source = "reference_preferred"
        elif suggestion_allowed:
            selected = (suggested_preset,)
            preset_source = "tool_suggestion"
        elif "日常穿搭" in available:
            selected = ("日常穿搭",)
            preset_source = "wardrobe_category"
        else:
            selected = ()
            preset_source = "none"
        if not suggested_preset:
            suggestion_status = "not_provided"
        elif suggested_preset not in available:
            suggestion_status = "rejected_unknown"
        elif suggested_preset in selected:
            suggestion_status = "accepted"
        elif explicit_prompt_preset in selected:
            suggestion_status = "shadowed_by_user"
        elif identity_reference_preferred in selected:
            suggestion_status = "shadowed_by_reference"
        else:
            suggestion_status = "rejected_user_conflict"
        preset_name = selected[0] if selected else ""
        return PhotoWardrobeDecision(
            rule_id="daily_outfit_context",
            mode="daily_outfit_context",
            source="daily_outfit",
            category="daily_outfit",
            lock_outfit=False,
            preset_name=preset_name,
            selected_presets=selected,
            suggested_preset=suggested_preset,
            preset_source=preset_source,
            suggestion_status=suggestion_status,
            reference_image_path=reference_path,
            reference_id=reference_id,
            reference_kind=reference_kind,
            reference_roles=roles,
            effective_reference_roles=effective_roles,
            positive_instruction=(
                "The selected reference, if present, controls identity only. Since the user did not request a clothing change, "
                "today's outfit context may provide wardrobe continuity."
            ),
            negative_instruction="Do not copy incidental clothing from an identity-only reference over today's outfit.",
            reason="identity-only reference with available daily outfit context",
            excluded_categories=resolved_intent.excluded_categories,
            excluded_outfit_text=resolved_intent.exclusion_text,
            base_prompt=str(base_prompt or prompt_text or "").strip(),
            scene_context=_clean_text(scene_context, 2400),
            adjustments=tuple(adjustments),
        )

    explicit_prompt_preset = _explicit_prompt_preset(workflow_kind, resolved_intent)
    suggestion_allowed = bool(
        suggested_preset
        and suggested_preset in available
        and suggested_category not in set(resolved_intent.excluded_categories)
    )
    if explicit_prompt_preset and explicit_prompt_preset in available:
        selected = (explicit_prompt_preset,)
        preset_source = "user_prompt"
    elif identity_reference_preferred and identity_reference_preferred in available:
        selected = (identity_reference_preferred,)
        preset_source = "reference_preferred"
    elif suggestion_allowed:
        selected = (suggested_preset,)
        preset_source = "tool_suggestion"
    else:
        selected = (
            (workflow_default_preset,)
            if workflow_default_preset
            else tuple(
                name
                for name in _automatic_presets(
                    workflow_kind,
                    resolved_intent,
                    resolved_intent.excluded_categories,
                )
                if name in available
            )[:1]
        )
        preset_source = (
            "user_prompt"
            if selected
            and not workflow_default_preset
            and selected[0] not in {"角色自拍", "可拍画面"}
            else ("workflow_default" if selected else "none")
        )
    preset_name = selected[0] if selected else ""
    if not suggested_preset:
        suggestion_status = "not_provided"
    elif suggested_preset not in available:
        suggestion_status = "rejected_unknown"
    elif suggested_category in set(resolved_intent.excluded_categories):
        suggestion_status = "rejected_user_conflict"
    elif suggested_preset in selected:
        suggestion_status = "accepted"
    elif explicit_prompt_preset in selected:
        suggestion_status = "shadowed_by_user"
    elif identity_reference_preferred in selected:
        suggestion_status = "shadowed_by_reference"
    else:
        suggestion_status = "rejected_user_conflict"
    return PhotoWardrobeDecision(
        rule_id="identity_only" if reference_path else "no_wardrobe_source",
        mode="identity_only" if reference_path else "none",
        source="selected_reference" if reference_path else "none",
        remove_daily_outfit_context=excluded_daily_context_removed,
        preset_name=preset_name,
        selected_presets=selected,
        suggested_preset=suggested_preset,
        preset_source=preset_source,
        suggestion_status=suggestion_status,
        reference_image_path=reference_path,
        reference_id=reference_id,
        reference_kind=reference_kind,
        reference_roles=roles,
        effective_reference_roles=effective_roles,
        positive_instruction=(
            "Use the selected reference only for character identity and appearance traits; its incidental clothing is not an outfit lock."
            if reference_path
            else ""
        ),
        reason="selected reference is identity-only" if reference_path else "no wardrobe source selected",
        excluded_categories=resolved_intent.excluded_categories,
        excluded_outfit_text=resolved_intent.exclusion_text,
        base_prompt=str(base_prompt or prompt_text or "").strip(),
        scene_context=_clean_text(scene_context, 2400),
        adjustments=tuple(adjustments),
    )
