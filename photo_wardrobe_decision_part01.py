# -*- coding: utf-8 -*-
"""photo_wardrobe_decision 拆分件 part01（机械搬移，行为不变）。

函数 / 常量 / 类逐字搬自 photo_wardrobe_decision.py，仅调整模块级依赖的导入来源。
"""
import re
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
