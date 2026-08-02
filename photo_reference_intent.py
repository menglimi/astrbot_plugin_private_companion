from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


REFERENCE_ROLES = (
    "identity",
    "outfit",
    "pose",
    "scene",
    "style",
    "continuity",
    "source",
)
CONTINUITY_MODES = ("continuation", "edit", "new_topic", "ambiguous")

__all__ = [
    "REFERENCE_ROLES",
    "CONTINUITY_MODES",
    "ReferenceIntent",
    "analyze_indexed_reference_roles",
    "analyze_reference_intent",
    "explicitly_excludes_reference_outfit",
]


@dataclass(frozen=True)
class ReferenceIntent:
    requested_roles: tuple[str, ...]
    excluded_roles: tuple[str, ...]
    continuity_mode: str
    confidence: float
    source: str


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


_REFERENCED_IMAGE_PATTERN = (
    r"(?:(?:(?:这|那)(?:张|个)?|该)?(?:图片|图)(?:中|里|里的|的)?)"
)
_ROLE_SPECIFIC_REFERENCE_NEGATIVES = (
    (
        "identity",
        rf"(?:不要|别|不再|无需|无须|不用)(?:再)?参考{_REFERENCED_IMAGE_PATTERN}(?:人物|人脸|脸|身份|发型)",
    ),
    (
        "outfit",
        rf"(?:不要|别|不再|无需|无须|不用)(?:再)?参考{_REFERENCED_IMAGE_PATTERN}(?:衣服|服装|穿搭|衣着|造型)",
    ),
    (
        "pose",
        rf"(?:不要|别|不再|无需|无须|不用)(?:再)?参考{_REFERENCED_IMAGE_PATTERN}(?:姿势|动作|姿态)",
    ),
    (
        "scene",
        rf"(?:不要|别|不再|无需|无须|不用)(?:再)?参考{_REFERENCED_IMAGE_PATTERN}(?:场景|背景|地点|环境)",
    ),
    (
        "style",
        rf"(?:不要|别|不再|无需|无须|不用)(?:再)?参考{_REFERENCED_IMAGE_PATTERN}(?:画风|风格|美术风格)",
    ),
)
_NEGATIVE_ROLE_PATTERNS = (
    (
        "identity",
        r"(?:不要|别|不再|无需|无须)(?:再)?(?:用|使用|参考|照着|沿用|保持)?(?:这个|这张|图中)?(?:人物|人脸|脸|身份|发型)",
    ),
    (
        "outfit",
        r"(?:不要|别|不再|无需|无须)(?:再)?(?:用|参考|照着|沿用|保持)?(?:这个|这张|这套|图中)?(?:衣服|服装|穿搭|衣着|造型)",
    ),
    (
        "pose",
        r"(?:不要|别|不再|无需|无须)(?:再)?(?:用|参考|照着|沿用|保持)?(?:这个|这张|图中)?(?:姿势|动作|姿态)",
    ),
    (
        "scene",
        r"(?:不要|别|不再|无需|无须)(?:再)?(?:用|参考|照着|沿用|保持)?(?:这个|这张|图中)?(?:场景|背景|地点|环境)",
    ),
    (
        "style",
        r"(?:不要|别|不再|无需|无须)(?:再)?(?:用|参考|照着|沿用|保持)?(?:这个|这张|图中)?(?:画风|风格|美术风格)",
    ),
)


def explicitly_excludes_reference_outfit(request_text: Any) -> bool:
    text = _text(request_text)
    outfit_patterns = (
        pattern
        for role, pattern in (
            *_ROLE_SPECIFIC_REFERENCE_NEGATIVES,
            *_NEGATIVE_ROLE_PATTERNS,
        )
        if role == "outfit"
    )
    if any(re.search(pattern, text) for pattern in outfit_patterns):
        return True
    return bool(
        re.search(
            r"explicit wardrobe exclusions?\s*:\s*[^\n]*(?:参考|沿用|照着)[^\n]*(?:图片|图)[^\n]*(?:衣服|服装|穿搭|衣着|造型)",
            text,
            flags=re.IGNORECASE,
        )
    )


def analyze_indexed_reference_roles(
    request_text: Any,
    *,
    image_count: int,
) -> tuple[tuple[str, ...], ...]:
    count = max(0, min(16, int(image_count or 0)))
    result: list[list[str]] = [[] for _ in range(count)]
    if not count:
        return ()
    text = _text(request_text)
    ordinal_pattern = re.compile(
        r"第\s*(?P<cn>[一二三四五六七八九十]|\d{1,2})\s*张"
        r"|\b(?P<en>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+(?:image|photo|picture)\b",
        flags=re.I,
    )
    chinese_numbers = {
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    english_numbers = {
        word: index
        for index, word in enumerate(
            ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth"),
            start=1,
        )
    }
    matches = list(ordinal_pattern.finditer(text))
    role_patterns = (
        ("identity", r"脸|人脸|长相|身份|人物|发型|face|identity|person"),
        ("outfit", r"衣服|服装|穿搭|衣着|造型|outfit|clothes|clothing|wardrobe"),
        ("pose", r"姿势|动作|姿态|pose|posture"),
        ("scene", r"场景|背景|地点|环境|scene|background"),
        ("style", r"画风|风格|style"),
        ("source", r"原图|底图|source"),
    )
    for offset, match in enumerate(matches):
        raw_index = match.group("cn")
        if raw_index:
            number = int(raw_index) if raw_index.isdigit() else chinese_numbers.get(raw_index, 0)
        else:
            number = english_numbers.get(str(match.group("en") or "").lower(), 0)
        if not 1 <= number <= count:
            continue
        end = matches[offset + 1].start() if offset + 1 < len(matches) else len(text)
        clause = text[match.end():end]
        for role, pattern in role_patterns:
            if re.search(pattern, clause, flags=re.I) and role not in result[number - 1]:
                result[number - 1].append(role)
    return tuple(tuple(roles) for roles in result)


def analyze_reference_intent(
    request_text: Any,
    *,
    has_explicit_reference: bool = False,
    workflow_kind: Any = "",
) -> ReferenceIntent:
    text = _text(request_text)
    all_reference_opt_out = re.search(
        r"(?:不要|不|别|无需|无须)(?:再)?(?:用|使用)?(?:任何)?参考(?:(?:图片|图)(?=$|[，,。；;])|(?=$|[，,。；;]))"
        r"|重新(?:开始|画|生成|来)(?:一张|画面)?"
        r"|全新(?:画面|图片|一张)|另起(?:主题|画面)"
        r"|(?:do not|don't|without|no)\s+(?:use\s+)?(?:any\s+)?reference"
        r"|start\s+(?:over|fresh)|brand[ -]?new\s+(?:image|scene)",
        text,
        flags=re.I,
    )
    positive_role_reference = re.search(
        r"(?:只|仅)?参考(?:这个人的|这人的|这套|这个|这张图(?:中|里|的)?|这张|图(?:中|里|的))?"
        r"(?:人物|人|角色|脸|长相|发型|身份|衣服|服装|穿搭|衣着|造型|姿势|动作|姿态|场景|背景|地点|环境|画风|风格)",
        text,
    )
    if all_reference_opt_out and not positive_role_reference:
        return ReferenceIntent((), REFERENCE_ROLES, "new_topic", 1.0, "rule")

    workflow = _text(workflow_kind)
    edit = workflow in {"edit", "改图", "修图", "重绘", "p图"} or bool(
        re.search(
            r"(?:把|将)?(?:这张|这幅|这个图|这图|原图|图片)(?:照片|图片)?(?:给我)?(?:改|修|重绘|变)(?:成|为|一下)?"
            r"|(?:edit|modify|transform|restyle)\s+(?:this|the)\s+(?:image|photo)",
            text,
            flags=re.I,
        )
    )
    if edit:
        excluded: list[str] = []
        if re.search(r"动漫|动画|写实|画风|风格|style|anime|realistic", text, flags=re.I):
            excluded.append("style")
        if re.search(r"(?:换|改)(?:成|上|为)?[^，。；;]*(?:衣|装|裙|裤|服|穿搭)", text):
            excluded.append("outfit")
        if re.search(r"(?:换|改)(?:成|到|为)?[^，。；;]*(?:地方|地点|场景|背景|环境)", text):
            excluded.append("scene")
        return ReferenceIntent(
            ("source",),
            tuple(role for role in REFERENCE_ROLES if role in excluded),
            "edit",
            0.99,
            "rule",
        )

    continuation = bool(
        re.search(
            r"接着(?:上一张|上张|刚才那张)?|继续(?:上一张|刚才的|拍)?|续拍|沿用上一张"
            r"|(?:上一张|上张|刚才那张)(?:继续|再|接着)"
            r"|continue\s+(?:from|with)|same\s+(?:shot|series)|previous\s+(?:image|photo)",
            text,
            flags=re.I,
        )
    )
    if continuation:
        requested = ["identity", "outfit", "scene", "continuity"]
        excluded: list[str] = []
        if re.search(r"(?:换|改)(?:个|一下|成|为)?[^，。；;]*(?:姿势|动作|姿态|构图|角度|表情|视线)", text):
            excluded.append("pose")
        if re.search(r"(?:换|改)(?:个|一下|成|到|为)?[^，。；;]*(?:衣|装|裙|裤|服|穿搭)", text):
            excluded.append("outfit")
        if re.search(r"(?:换|改)(?:个|一下|成|到|为)?[^，。；;]*(?:地方|地点|场景|背景|环境|时间|白天|夜晚|晚上)", text):
            excluded.append("scene")
        preserves_existing = bool(
            re.search(r"(?:保持|保留|沿用|仍然|还是|依旧|继续穿|同一|相同|原来)", text)
        )
        if not preserves_existing and re.search(
            r"睡衣|睡裙|睡袍|校服|制服|泳装|泳衣|比基尼|礼服|正装|运动服|健身服|居家服|家居服|"
            r"cos(?:play)?|nightgown|pajama|pyjama|sleepwear|swimsuit|swimwear|formalwear|sportswear|homewear|loungewear",
            text,
            flags=re.I,
        ):
            excluded.append("outfit")
        if not preserves_existing and re.search(
            r"海边|海滩|沙滩|泳池|办公室|办公桌|公司|学校|校园|教室|宿舍|卧室|客厅|厨房|咖啡店|餐厅|商场|街头|公园|车站|机场|"
            r"beach|seaside|pool|office|workplace|school|classroom|bedroom|living room|cafe|restaurant|mall|street|park|station|airport",
            text,
            flags=re.I,
        ):
            excluded.append("scene")
        excluded = list(dict.fromkeys(excluded))
        requested = [role for role in requested if role not in excluded]
        return ReferenceIntent(
            tuple(requested),
            tuple(role for role in REFERENCE_ROLES if role in excluded),
            "continuation",
            0.98,
            "rule",
        )

    if re.search(
        r"(?:换|改)(?:个|一下|成|到|为)?[^，。；;]*(?:地方|地点|场景|背景|环境|时间|白天|夜晚|晚上)",
        text,
    ):
        return ReferenceIntent(
            ("identity",),
            ("outfit", "pose", "scene", "continuity"),
            "ambiguous",
            0.86,
            "rule",
        )
    requested: list[str] = []
    excluded: list[str] = []

    for indexed_roles in analyze_indexed_reference_roles(text, image_count=16):
        requested.extend(role for role in indexed_roles if role not in requested)

    for role, pattern in _ROLE_SPECIFIC_REFERENCE_NEGATIVES:
        if re.search(pattern, text):
            excluded.append(role)

    for role, pattern in _NEGATIVE_ROLE_PATTERNS:
        if re.search(pattern, text):
            excluded.append(role)

    explicit_role_patterns = (
        ("identity", r"(?:只|仅)?参考(?:这个人的|这人的|这个|这张图(?:中|里|的)?|这张|图(?:中|里|的))?(?:人物|人|角色|脸|长相|发型|身份)"),
        (
            "outfit",
            r"(?:只|仅)?参考(?:这套|这个|这张图(?:中|里|的)?|这张|图(?:中|里|的))?(?:衣服|服装|穿搭|衣着|造型)"
            r"|(?:穿|照着穿|按照)(?:这个|这张|这套|图中)?(?:衣服|服装|穿搭|衣着|造型)",
        ),
        ("pose", r"(?:只|仅)?(?:参考|照|照着|按照)(?:这个|这张图(?:中|里|的)?|这张|图(?:中|里|的))?(?:姿势|动作|姿态)"),
        ("scene", r"(?:只|仅)?(?:参考|照|照着|按照)(?:这个|这张图(?:中|里|的)?|这张|图(?:中|里|的))?(?:场景|背景|地点|环境)"),
        ("style", r"(?:只|仅)?参考(?:这个|这张图(?:中|里|的)?|这张|图(?:中|里|的))?(?:画风|风格|美术风格)"),
    )
    for role, pattern in explicit_role_patterns:
        if re.search(pattern, text):
            requested.append(role)
    if re.search(r"按(?:照)?(?:这个|这张)?(?:人物|人|角色)", text) and "identity" not in requested:
        requested.append("identity")
    outfit_change = bool(
        re.search(
            r"(?:换|改)(?:成|上|为)?[^，。；;]*(?:衣|装|裙|裤|服|穿搭)"
            r"|\b(?:wear|wearing|change\s+into|switch\s+to|put\s+on)\b[^,.!;]*(?:outfit|clothes|clothing|dress|skirt|uniform|pajama|coat|jacket|suit|armor)",
            text,
            flags=re.I,
        )
    )
    if outfit_change:
        excluded.append("outfit")

    excluded = list(dict.fromkeys(excluded))
    requested = [role for role in dict.fromkeys(requested) if role not in excluded]
    if not requested and excluded and "identity" not in excluded and (
        has_explicit_reference or workflow in {"selfie", "portrait", "自拍", "人像"}
    ):
        requested.append("identity")

    if requested or excluded:
        return ReferenceIntent(
            requested_roles=tuple(requested),
            excluded_roles=tuple(excluded),
            continuity_mode="ambiguous",
            confidence=0.96,
            source="rule",
        )
    if has_explicit_reference:
        return ReferenceIntent(("identity",), (), "ambiguous", 0.55, "conservative")
    if workflow in {"selfie", "portrait", "自拍", "人像"}:
        return ReferenceIntent(("identity",), (), "ambiguous", 0.55, "workflow_default")
    return ReferenceIntent((), (), "ambiguous", 0.0, "none")
