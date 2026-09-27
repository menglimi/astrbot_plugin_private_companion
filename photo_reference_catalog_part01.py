# -*- coding: utf-8 -*-
"""photo_reference_catalog 拆分件 part01（机械搬移，行为不变）。

函数 / 常量 / 类逐字搬自 photo_reference_catalog.py，仅调整模块级依赖的导入来源。
"""
import re
from dataclasses import dataclass, replace
from typing import Any, Collection, Iterable, Literal, cast


CATALOG_VERSION = 2

MAX_LIBRARY_REFERENCES = 24

PhotoReferenceKind = Literal["persona", "library", "daily_outfit"]


_TRUE_BOOLEAN_VALUES = {"1", "true", "yes", "on", "是", "开启", "锁定"}

_FALSE_BOOLEAN_VALUES = {"0", "false", "no", "off", "否", "关闭", "不锁定"}


_ROLE_ALIASES = {
    "identity": "identity",
    "persona": "identity",
    "face": "identity",
    "人设": "identity",
    "身份": "identity",
    "人物": "identity",
    "脸": "identity",
    "outfit": "outfit",
    "wardrobe": "outfit",
    "clothing": "outfit",
    "服装": "outfit",
    "穿搭": "outfit",
    "pose": "pose",
    "姿势": "pose",
    "scene": "scene",
    "background": "scene",
    "场景": "scene",
    "背景": "scene",
    "style": "style",
    "画风": "style",
    "风格": "style",
    "continuity": "continuity",
    "连续性": "continuity",
    "source": "source",
    "原图": "source",
}


_OUTFIT_PATTERNS = (
    ("cosplay", r"(?<![a-z0-9])cos(?:play)?(?![a-z0-9])|角色扮演|扮成|女仆装|巫女服|魔法少女|表演服"),
    ("school_uniform", r"校服|学院制服|学生制服|school[\s_-]*uniform"),
    ("sleepwear", r"睡衣|睡裙|睡袍|睡眠服|nightgown|nightdress|pajama|pyjama|sleepwear|bedtime outfit"),
    ("swimwear", r"泳装|泳衣|比基尼|swimsuit|swimwear|bikini"),
    ("sportswear", r"运动服|健身服|瑜伽服|球衣|sportswear|activewear|gym wear|jersey"),
    ("formalwear", r"礼服|晚礼服|正装|燕尾服|西装|tuxedo|formalwear|formal attire|evening gown|\bsuit\b"),
    ("homewear", r"居家服|家居服|家常服|宅家服|homewear|loungewear"),
    ("daily_outfit", r"今日穿搭|当天穿搭|日常穿搭|today'?s outfit|daily outfit"),
)


_SCENE_TOKENS = (
    ("home", ("在家", "家里", "居家", "宅家", "home")),
    ("bedroom", ("卧室", "床边", "睡前", "刚起床", "bedroom", "bedtime")),
    ("school", ("上学", "校园", "教室", "校门", "school", "campus")),
    ("office", ("上班", "公司", "办公室", "office", "workplace")),
    ("outdoor", ("外出", "通勤", "逛街", "街头", "旅行", "outdoor", "commute")),
    ("formal_event", ("宴会", "舞会", "典礼", "正式场合", "banquet", "ceremony")),
    ("sport", ("运动", "健身", "跑步", "瑜伽", "球场", "gym", "sport")),
    ("beach", ("海边", "沙滩", "泳池", "beach", "pool")),
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
}


_OUTFIT_ALIASES = {
    "cosplay": "cosplay",
    "cos": "cosplay",
    "角色扮演": "cosplay",
    "school_uniform": "school_uniform",
    "school uniform": "school_uniform",
    "校服": "school_uniform",
    "sleepwear": "sleepwear",
    "pajama": "sleepwear",
    "pyjama": "sleepwear",
    "loungewear": "homewear",
    "睡衣": "sleepwear",
    "swimwear": "swimwear",
    "swimsuit": "swimwear",
    "泳装": "swimwear",
    "sportswear": "sportswear",
    "activewear": "sportswear",
    "运动服": "sportswear",
    "formalwear": "formalwear",
    "formal": "formalwear",
    "正装": "formalwear",
    "礼服": "formalwear",
    "homewear": "homewear",
    "居家服": "homewear",
    "daily_outfit": "daily_outfit",
    "daily outfit": "daily_outfit",
    "日常穿搭": "daily_outfit",
    "今日穿搭": "daily_outfit",
    "custom_outfit": "custom_outfit",
    "自定义穿搭": "custom_outfit",
}


_SCENE_ALIASES = {
    "home": "home",
    "家": "home",
    "居家": "home",
    "bedroom": "bedroom",
    "卧室": "bedroom",
    "school": "school",
    "校园": "school",
    "office": "office",
    "办公室": "office",
    "outdoor": "outdoor",
    "户外": "outdoor",
    "formal_event": "formal_event",
    "正式场合": "formal_event",
    "sport": "sport",
    "sports": "sport",
    "运动": "sport",
    "beach": "beach",
    "海边": "beach",
    "沙滩": "beach",
}


_TIME_ALIASES = {
    "morning": "morning",
    "早晨": "morning",
    "早上": "morning",
    "daytime": "daytime",
    "day": "daytime",
    "白天": "daytime",
    "afternoon": "afternoon",
    "下午": "afternoon",
    "evening": "evening",
    "傍晚": "evening",
    "黄昏": "evening",
    "night": "night",
    "夜晚": "night",
    "晚上": "night",
    "bedtime": "bedtime",
    "睡前": "bedtime",
}


_TIME_TOKENS = (
    ("morning", ("清晨", "早晨", "早上", "晨间", "morning", "sunrise")),
    ("daytime", ("白天", "日间", "daytime", "daylight")),
    ("afternoon", ("下午", "午后", "afternoon")),
    ("evening", ("傍晚", "黄昏", "日落", "evening", "sunset")),
    ("night", ("夜晚", "晚上", "深夜", "夜景", "night")),
    ("bedtime", ("睡前", "临睡", "bedtime")),
)


@dataclass(frozen=True)
class PhotoReference:
    id: str
    kind: PhotoReferenceKind
    source: str
    note: str
    reference_roles: tuple[str, ...]
    outfit_category: str
    outfit_lock_default: bool
    scene_categories: tuple[str, ...]
    preferred_preset: str
    metadata_source: str
    time_categories: tuple[str, ...] = ()
    editor_intent: dict[str, Any] | None = None
    excluded_scene_categories: tuple[str, ...] = ()
    excluded_time_categories: tuple[str, ...] = ()
    selection_eligibility: str = "matching_only"


@dataclass(frozen=True)
class CatalogLoadResult:
    references: tuple[PhotoReference, ...]
    needs_persist: bool
    warnings: tuple[str, ...] = ()
    read_only: bool = False


class CatalogValidationError(ValueError):
    def __init__(self, errors: dict[str, list[str]]) -> None:
        self.errors = errors
        super().__init__("；".join(message for messages in errors.values() for message in messages))


def _clean_text(value: Any, limit: int) -> str:
    return re.sub(r"[\r\n\t]+", " ", str(value or "")).strip()[:limit]


def _as_values(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [part for part in re.split(r"[,，、/|\s]+", str(value or "")) if part]


def _normalize_roles(value: Any, *, warnings: list[str] | None = None) -> tuple[str, ...]:
    roles: list[str] = []
    for raw in _as_values(value):
        raw_role = _clean_text(raw, 40)
        role = _ROLE_ALIASES.get(raw_role.lower(), "")
        if role and role not in roles:
            roles.append(role)
        elif raw_role and not role and warnings is not None:
            warnings.append(f"忽略未知参考职责：{raw_role}")
    return tuple(roles)


def _normalize_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in _TRUE_BOOLEAN_VALUES


def _migration_bool(
    value: Any,
    *,
    default: bool,
    warnings: list[str],
    label: str,
) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    if text in _TRUE_BOOLEAN_VALUES:
        return True
    if text in _FALSE_BOOLEAN_VALUES:
        return False
    warnings.append(f"忽略无效布尔元数据 {label}={_clean_text(value, 40)}，已使用推断默认值")
    return default


def _strict_bool(value: Any, field: str, errors: dict[str, list[str]]) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    _append_error(errors, field, "必须是布尔值 true 或 false")
    return False


def _custom_value(value: str) -> str:
    clean = _clean_text(value, 80)
    if not clean:
        return ""
    if clean.lower().startswith("custom:"):
        suffix = clean.split(":", 1)[1].strip()
        return f"custom:{suffix}" if suffix else ""
    return f"custom:{clean}"


def _normalize_outfit_category(value: Any) -> str:
    clean = _clean_text(value, 80)
    if not clean:
        return ""
    return _OUTFIT_ALIASES.get(clean.lower(), _custom_value(clean))


def _normalize_scene_categories(value: Any) -> tuple[str, ...]:
    scenes: list[str] = []
    for raw in _as_values(value):
        clean = _clean_text(raw, 80)
        if not clean:
            continue
        scene = _SCENE_ALIASES.get(clean.lower(), _custom_value(clean))
        if scene and scene not in scenes:
            scenes.append(scene)
    return tuple(scenes)


def _normalize_time_categories(value: Any) -> tuple[str, ...]:
    categories: list[str] = []
    for raw in _as_values(value):
        clean = _clean_text(raw, 80)
        if not clean:
            continue
        category = _TIME_ALIASES.get(clean.lower(), _custom_value(clean))
        if category and category not in categories:
            categories.append(category)
    return tuple(categories)


def _append_error(errors: dict[str, list[str]], field: str, message: str) -> None:
    errors.setdefault(field, []).append(message)


def _strict_roles(value: Any, field: str, errors: dict[str, list[str]]) -> tuple[str, ...]:
    roles: list[str] = []
    for raw in _as_values(value):
        clean = _clean_text(raw, 40)
        role = _ROLE_ALIASES.get(clean.lower(), "")
        if not role:
            _append_error(errors, field, f"未知参考职责：{clean}")
        elif role not in roles:
            roles.append(role)
    return tuple(roles)


def _strict_custom_value(
    value: Any,
    aliases: dict[str, str],
    field: str,
    label: str,
    errors: dict[str, list[str]],
) -> str:
    clean = _clean_text(value, 80)
    if not clean:
        return ""
    canonical = aliases.get(clean.lower())
    if canonical:
        return canonical
    if clean.lower().startswith("custom:") and clean.split(":", 1)[1].strip():
        return _custom_value(clean)
    _append_error(errors, field, f"未知{label}必须使用 custom:<名称>：{clean}")
    return ""


def _strict_scenes(value: Any, field: str, errors: dict[str, list[str]]) -> tuple[str, ...]:
    scenes: list[str] = []
    for raw in _as_values(value):
        scene = _strict_custom_value(raw, _SCENE_ALIASES, field, "场景", errors)
        if scene and scene not in scenes:
            scenes.append(scene)
    return tuple(scenes)


def _strict_times(value: Any, field: str, errors: dict[str, list[str]]) -> tuple[str, ...]:
    categories: list[str] = []
    for raw in _as_values(value):
        category = _strict_custom_value(raw, _TIME_ALIASES, field, "时间类别", errors)
        if category and category not in categories:
            categories.append(category)
    return tuple(categories)


def _strict_reference(
    raw: Any,
    index: int,
    preset_names: Collection[str],
    errors: dict[str, list[str]],
) -> PhotoReference | None:
    prefix = f"items.{index}"
    if isinstance(raw, PhotoReference):
        item = raw.__dict__
    elif isinstance(raw, dict):
        item = dict(raw)
    else:
        _append_error(errors, prefix, "目录条目必须是对象")
        return None
    kind = _clean_text(item.get("kind"), 40).lower()
    if kind not in {"persona", "library", "daily_outfit"}:
        _append_error(errors, f"{prefix}.kind", "类型必须是 persona、library 或 daily_outfit")
    reference_id = _clean_text(item.get("id"), 80)
    if not reference_id:
        _append_error(errors, f"{prefix}.id", "缺少稳定 ID")
    source = _clean_text(item.get("source"), 1000)
    if not source:
        _append_error(errors, f"{prefix}.source", "图片路径或 URL 不能为空")
    note = _clean_text(item.get("note"), 500)
    roles = _strict_roles(item.get("reference_roles"), f"{prefix}.reference_roles", errors)
    category = _strict_custom_value(
        item.get("outfit_category"),
        _OUTFIT_ALIASES,
        f"{prefix}.outfit_category",
        "服装类别",
        errors,
    )
    lock_default = _strict_bool(
        item.get("outfit_lock_default"),
        f"{prefix}.outfit_lock_default",
        errors,
    )
    if lock_default and "outfit" not in roles:
        roles = (*roles, "outfit")
    scenes = _strict_scenes(item.get("scene_categories"), f"{prefix}.scene_categories", errors)
    times = _strict_times(item.get("time_categories"), f"{prefix}.time_categories", errors)
    excluded_scenes = _strict_scenes(
        item.get("excluded_scene_categories"),
        f"{prefix}.excluded_scene_categories",
        errors,
    )
    excluded_times = _strict_times(
        item.get("excluded_time_categories"),
        f"{prefix}.excluded_time_categories",
        errors,
    )
    eligibility = _clean_text(item.get("selection_eligibility"), 40).lower() or "matching_only"
    if eligibility not in {"matching_only", "fallback_identity_only", "fallback_allowed", "disabled"}:
        _append_error(errors, f"{prefix}.selection_eligibility", "必须是 matching_only、fallback_identity_only、fallback_allowed 或 disabled")
        eligibility = "matching_only"
    raw_intent = item.get("editor_intent")
    editor_intent = dict(raw_intent) if isinstance(raw_intent, dict) else None
    preferred_preset = _clean_text(item.get("preferred_preset"), 80)
    if preferred_preset and preferred_preset not in preset_names:
        _append_error(errors, f"{prefix}.preferred_preset", f"场景预设不存在：{preferred_preset}")
    metadata_source = _clean_text(item.get("metadata_source"), 30) or "configured"
    if any(field.startswith(f"{prefix}.") for field in errors):
        return None
    return PhotoReference(
        id="persona" if kind == "persona" else reference_id,
        kind=cast(PhotoReferenceKind, kind),
        source=source,
        note=note,
        reference_roles=roles,
        outfit_category=category,
        outfit_lock_default=lock_default,
        scene_categories=scenes,
        preferred_preset=preferred_preset,
        metadata_source=metadata_source,
        time_categories=times,
        editor_intent=editor_intent,
        excluded_scene_categories=excluded_scenes,
        excluded_time_categories=excluded_times,
        selection_eligibility=eligibility,
    )


def _serialize_reference(reference: PhotoReference) -> dict[str, Any]:
    return {
        "id": reference.id,
        "kind": reference.kind,
        "source": reference.source,
        "note": reference.note,
        "reference_roles": list(reference.reference_roles),
        "outfit_category": reference.outfit_category,
        "outfit_lock_default": reference.outfit_lock_default,
        "scene_categories": list(reference.scene_categories),
        "time_categories": list(reference.time_categories),
        "preferred_preset": reference.preferred_preset,
        "metadata_source": reference.metadata_source,
        "editor_intent": reference.editor_intent,
        "excluded_scene_categories": list(reference.excluded_scene_categories),
        "excluded_time_categories": list(reference.excluded_time_categories),
        "selection_eligibility": reference.selection_eligibility,
    }
