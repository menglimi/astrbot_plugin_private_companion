from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Collection, Iterable, Literal, cast


CATALOG_VERSION = 1
MAX_LIBRARY_REFERENCES = 24
PhotoReferenceKind = Literal["persona", "library", "daily_outfit"]

_TRUE_BOOLEAN_VALUES = {"1", "true", "yes", "on", "是", "开启", "锁定"}
_FALSE_BOOLEAN_VALUES = {"0", "false", "no", "off", "否", "关闭", "不锁定"}

__all__ = [
    "CATALOG_VERSION",
    "MAX_LIBRARY_REFERENCES",
    "PhotoReferenceKind",
    "PhotoReference",
    "CatalogLoadResult",
    "CatalogValidationError",
    "load_catalog",
    "validate_and_serialize",
    "add_reference",
    "delete_reference",
    "build_daily_outfit_reference",
    "project_reference_candidate",
]

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
    ("sleepwear", r"睡衣|睡裙|睡袍|睡眠服|nightgown|nightdress|pajama|pyjama|sleepwear|loungewear|bedtime outfit"),
    ("swimwear", r"泳装|泳衣|比基尼|swimsuit|swimwear|bikini"),
    ("sportswear", r"运动服|健身服|瑜伽服|球衣|sportswear|activewear|gym wear|jersey"),
    ("formalwear", r"礼服|晚礼服|正装|燕尾服|西装|tuxedo|formalwear|formal attire|evening gown|\bsuit\b"),
    ("homewear", r"居家服|家居服|家常服|宅家服|homewear"),
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
    "loungewear": "sleepwear",
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
    }


def validate_and_serialize(
    references: Iterable[PhotoReference | dict[str, Any]],
    *,
    preset_names: Iterable[str] = (),
) -> list[dict[str, Any]]:
    presets = {_clean_text(item, 80) for item in preset_names if _clean_text(item, 80)}
    errors: dict[str, list[str]] = {}
    normalized: list[tuple[int, PhotoReference]] = []
    for index, raw in enumerate(references):
        reference = _strict_reference(raw, index, presets, errors)
        if reference is not None and reference.kind != "daily_outfit":
            normalized.append((index, reference))

    persona_index: int | None = None
    library_count = 0
    source_indexes: dict[str, int] = {}
    id_indexes: dict[str, int] = {}
    for index, reference in normalized:
        prefix = f"items.{index}"
        if reference.kind == "persona":
            if persona_index is not None:
                _append_error(errors, f"{prefix}.kind", "持久化目录最多只能有一个 persona")
            else:
                persona_index = index
        elif reference.kind == "library":
            library_count += 1
            if library_count > MAX_LIBRARY_REFERENCES:
                _append_error(errors, f"{prefix}.kind", f"参考图库最多保存 {MAX_LIBRARY_REFERENCES} 张")
        if reference.source in source_indexes:
            _append_error(
                errors,
                f"{prefix}.source",
                f"图片来源与第 {source_indexes[reference.source] + 1} 条重复",
            )
        else:
            source_indexes[reference.source] = index
        if reference.id in id_indexes:
            _append_error(
                errors,
                f"{prefix}.id",
                f"稳定 ID 与第 {id_indexes[reference.id] + 1} 条重复",
            )
        else:
            id_indexes[reference.id] = index
    if errors:
        raise CatalogValidationError(errors)
    return [_serialize_reference(reference) for _, reference in normalized]


def add_reference(
    references: Iterable[PhotoReference],
    *,
    kind: str,
    source: Any,
    note: Any = "",
    reference_roles: Any = None,
    outfit_category: Any = None,
    outfit_lock_default: Any = None,
    scene_categories: Any = None,
    time_categories: Any = None,
    preferred_preset: Any = None,
    metadata_source: Any = "",
    preset_names: Iterable[str] = (),
) -> tuple[PhotoReference, ...]:
    normalized_kind = _clean_text(kind, 40).lower()
    if normalized_kind not in {"persona", "library"}:
        raise CatalogValidationError({"item.kind": ["持久化条目只能是 persona 或 library"]})
    clean_note = _clean_text(note, 500)
    inferred_category = _infer_outfit_category(clean_note)
    category = outfit_category if outfit_category is not None else inferred_category
    roles = reference_roles
    if roles is None:
        roles = _infer_reference_roles(clean_note, outfit_category=_normalize_outfit_category(category))
    scenes = scene_categories if scene_categories is not None else _infer_scene_categories(clean_note)
    times = time_categories if time_categories is not None else _infer_time_categories(clean_note)
    lock_default = outfit_lock_default
    if lock_default is None:
        lock_default = bool(inferred_category and normalized_kind == "library")
    presets = {_clean_text(item, 80) for item in preset_names if _clean_text(item, 80)}
    preset = preferred_preset
    if preset is None:
        inferred_preset = _CATEGORY_PRESETS.get(inferred_category, "")
        preset = inferred_preset if inferred_preset in presets else ""
    inferred_metadata = (
        reference_roles is None
        and outfit_category is None
        and outfit_lock_default is None
        and scene_categories is None
        and time_categories is None
        and preferred_preset is None
    )
    raw_reference = {
        "id": "persona" if normalized_kind == "persona" else f"library_{uuid.uuid4().hex}",
        "kind": normalized_kind,
        "source": source,
        "note": clean_note,
        "reference_roles": roles,
        "outfit_category": category,
        "outfit_lock_default": lock_default,
        "scene_categories": scenes,
        "time_categories": times,
        "preferred_preset": preset,
        "metadata_source": _clean_text(metadata_source, 30) or ("inferred_note" if inferred_metadata else "configured"),
    }
    serialized = validate_and_serialize([*references, raw_reference], preset_names=presets)
    return load_catalog(serialized, catalog_version=CATALOG_VERSION, preset_names=presets).references


def delete_reference(
    references: Iterable[PhotoReference],
    reference_id: Any,
) -> tuple[PhotoReference, ...]:
    clean_id = _clean_text(reference_id, 80)
    existing = tuple(references)
    remaining = tuple(reference for reference in existing if reference.id != clean_id)
    if not clean_id or len(remaining) == len(existing):
        raise KeyError(clean_id)
    return remaining


def build_daily_outfit_reference(
    source: Any,
    *,
    note: Any = "今天生成的穿搭参考图；优先保持当天服装连续性，但不要覆盖用户明确提出的新服装",
    scene_categories: Any = ("school", "office", "outdoor"),
    time_categories: Any = (),
    preferred_preset: Any = "日常穿搭",
    preset_names: Iterable[str] = (),
) -> PhotoReference:
    presets = {_clean_text(item, 80) for item in preset_names if _clean_text(item, 80)}
    errors: dict[str, list[str]] = {}
    reference = _strict_reference(
        {
            "id": "daily_outfit",
            "kind": "daily_outfit",
            "source": source,
            "note": note,
            "reference_roles": ["identity", "outfit"],
            "outfit_category": "daily_outfit",
            "outfit_lock_default": True,
            "scene_categories": scene_categories,
            "time_categories": time_categories,
            "preferred_preset": preferred_preset,
            "metadata_source": "runtime",
        },
        0,
        presets,
        errors,
    )
    if reference is None:
        raise CatalogValidationError(errors)
    return reference


def project_reference_candidate(
    reference: PhotoReference,
    *,
    resolved_source: Any = "",
) -> dict[str, Any]:
    path = _clean_text(resolved_source, 1000) or reference.source
    return {
        "id": reference.id,
        "path": path,
        "source": reference.source,
        "kind": reference.kind,
        "note": reference.note,
        "reference_roles": list(reference.reference_roles),
        "outfit_category": reference.outfit_category,
        "outfit_lock_default": reference.outfit_lock_default,
        "scene_categories": list(reference.scene_categories),
        "time_categories": list(reference.time_categories),
        "preferred_preset": reference.preferred_preset,
        "metadata_source": reference.metadata_source,
    }


def _infer_outfit_category(text: Any) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    matches: list[tuple[int, int, str]] = []
    for category, pattern in _OUTFIT_PATTERNS:
        for match in re.finditer(pattern, normalized, flags=re.I):
            matches.append((match.start(), match.end(), category))
    return min(matches)[2] if matches else ""


def _infer_scene_categories(text: Any) -> tuple[str, ...]:
    normalized = re.sub(r"\s+", "", str(text or "")).lower()
    return tuple(category for category, tokens in _SCENE_TOKENS if any(token in normalized for token in tokens))


def _infer_time_categories(text: Any) -> tuple[str, ...]:
    normalized = re.sub(r"\s+", "", str(text or "")).lower()
    return tuple(category for category, tokens in _TIME_TOKENS if any(token in normalized for token in tokens))


def _infer_reference_roles(
    text: Any,
    *,
    outfit_category: str = "",
) -> tuple[str, ...]:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    if re.search(r"仅(?:用于)?(?:人设|身份|脸|发型)|只(?:参考|用于)(?:人设|身份|脸|发型)|identity only", normalized, flags=re.I):
        return ("identity",)

    roles = ["identity"]
    patterns = (
        ("outfit", r"服装|穿搭|衣服|衣着|outfit|wardrobe|clothing"),
        ("pose", r"姿势|动作|体态|pose|posture"),
        ("scene", r"场景|背景|环境|scene|background"),
        ("style", r"画风|风格|美术风格|style"),
        ("continuity", r"连续性|续拍|承接|保持一致|continuity|consistent"),
        ("source", r"原图|源图|改图|重绘|source image|original image"),
    )
    for role, pattern in patterns:
        if (role == "outfit" and outfit_category) or re.search(pattern, normalized, flags=re.I):
            roles.append(role)
    return tuple(roles)


def _stable_library_id(source: str) -> str:
    digest = hashlib.sha256(source.strip().encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"library_{digest}"


def _legacy_source(value: Any) -> str:
    source = _clean_text(value, 1000)
    while len(source) >= 2 and source[0] == source[-1] and source[0] in {"'", '"'}:
        source = source[1:-1].strip()
    return source


def _legacy_item_parts(raw_item: Any) -> tuple[str, str, dict[str, Any]]:
    if isinstance(raw_item, dict):
        metadata = dict(raw_item)
        return (
            _legacy_source(metadata.get("source") or metadata.get("path") or metadata.get("url")),
            _clean_text(metadata.get("note") or metadata.get("description"), 500),
            metadata,
        )

    text = str(raw_item or "").strip()
    if text.startswith("{") and text.endswith("}"):
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            return _legacy_item_parts(parsed)

    parts = re.split(r"\s*(?:\|\||｜｜)\s*", text, maxsplit=2)
    source = _legacy_source(parts[0] if parts else "")
    note = _clean_text(parts[1] if len(parts) > 1 else "", 500)
    metadata: dict[str, Any] = {}
    if len(parts) > 2:
        try:
            parsed_metadata = json.loads(parts[2])
        except (TypeError, ValueError):
            note = _clean_text(f"{note} || {parts[2]}", 500)
        else:
            if isinstance(parsed_metadata, dict):
                metadata = parsed_metadata
    return source, note, metadata


def _migrate_library_reference(
    raw_item: Any,
    preset_names: Collection[str],
    warnings: list[str],
) -> PhotoReference | None:
    source, note, metadata = _legacy_item_parts(raw_item)
    if not source:
        return None
    roles = _normalize_roles(
        metadata.get("reference_roles", metadata.get("reference_role")),
        warnings=warnings,
    )
    raw_category = metadata.get("outfit_category") or metadata.get("wardrobe_category")
    category = _normalize_outfit_category(raw_category) if raw_category else _infer_outfit_category(note)
    if not roles:
        roles = _infer_reference_roles(note, outfit_category=category)
    raw_scenes = metadata.get("scene_categories") or metadata.get("scene_tags")
    scenes = _normalize_scene_categories(raw_scenes) if raw_scenes else _infer_scene_categories(note)
    raw_times = metadata.get("time_categories") or metadata.get("time_tags")
    times = _normalize_time_categories(raw_times) if raw_times else _infer_time_categories(note)
    lock_default = _migration_bool(
        metadata.get("outfit_lock_default"),
        default=bool(category and "outfit" in roles),
        warnings=warnings,
        label="outfit_lock_default",
    )
    if lock_default and "outfit" not in roles:
        roles = (*roles, "outfit")
    preferred_preset = _clean_text(metadata.get("preferred_preset") or metadata.get("preset"), 80)
    if not preferred_preset:
        inferred_preset = _CATEGORY_PRESETS.get(category, "")
        preferred_preset = inferred_preset if inferred_preset in preset_names else ""
    elif preferred_preset not in preset_names:
        warnings.append(f"忽略不存在的首选预设：{preferred_preset}")
        preferred_preset = ""
    return PhotoReference(
        id=_stable_library_id(source),
        kind="library",
        source=source,
        note=note or "通用人物参考图；没有更具体的服装或场景匹配时使用",
        reference_roles=roles,
        outfit_category=category,
        outfit_lock_default=lock_default,
        scene_categories=scenes,
        preferred_preset=preferred_preset,
        metadata_source="migration",
        time_categories=times,
    )


def _catalog_items(raw_catalog: Any, warnings: list[str]) -> list[Any] | None:
    if isinstance(raw_catalog, list):
        return raw_catalog
    if isinstance(raw_catalog, str) and raw_catalog.strip():
        try:
            parsed = json.loads(raw_catalog)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, list):
            warnings.append("规范参考图目录以 JSON 字符串保存，已按数组兼容加载")
            return parsed
    return None


def _tolerant_catalog_reference(
    raw_item: Any,
    index: int,
    preset_names: Collection[str],
    warnings: list[str],
) -> PhotoReference | None:
    if isinstance(raw_item, PhotoReference):
        item = raw_item.__dict__
    elif isinstance(raw_item, dict):
        item = dict(raw_item)
    else:
        warnings.append(f"目录条目 {index + 1} 无效：条目必须是对象")
        return None

    note = _clean_text(item.get("note"), 500)
    category = _normalize_outfit_category(item.get("outfit_category"))
    raw_roles = item.get("reference_roles")
    role_warnings: list[str] = []
    roles = _normalize_roles(raw_roles, warnings=role_warnings)
    if role_warnings:
        warnings.extend(f"目录条目 {index + 1}：{warning}" for warning in role_warnings)
    if not roles and raw_roles in (None, "", [], (), set()):
        roles = _infer_reference_roles(note, outfit_category=category)

    preferred_preset = _clean_text(item.get("preferred_preset"), 80)
    if preferred_preset and preferred_preset not in preset_names:
        warnings.append(
            f"目录条目 {index + 1} 的首选预设不存在，已仅在运行时清空：{preferred_preset}"
        )
        preferred_preset = ""

    normalized = {
        **item,
        "reference_roles": roles,
        "outfit_category": category,
        "outfit_lock_default": _migration_bool(
            item.get("outfit_lock_default"),
            default=False,
            warnings=warnings,
            label=f"items.{index}.outfit_lock_default",
        ),
        "scene_categories": _normalize_scene_categories(item.get("scene_categories")),
        "time_categories": _normalize_time_categories(item.get("time_categories")),
        "preferred_preset": preferred_preset,
    }
    item_errors: dict[str, list[str]] = {}
    reference = _strict_reference(normalized, index, preset_names, item_errors)
    if reference is None:
        warnings.extend(
            f"目录条目 {index + 1} 无效：{message}"
            for messages in item_errors.values()
            for message in messages
        )
    return reference


def _load_canonical_references(
    raw_catalog: Any,
    preset_names: Collection[str],
    warnings: list[str],
) -> tuple[PhotoReference, ...] | None:
    raw_items = _catalog_items(raw_catalog, warnings)
    if raw_items is None:
        return None

    references: list[PhotoReference] = []
    seen_ids: set[str] = set()
    seen_sources: set[str] = set()
    persona_seen = False
    library_count = 0
    library_limit_reported = False
    for index, raw_item in enumerate(raw_items):
        reference = _tolerant_catalog_reference(raw_item, index, preset_names, warnings)
        if reference is None:
            continue
        if reference.kind == "daily_outfit":
            warnings.append(f"目录条目 {index + 1} 是运行时今日穿搭引用，已忽略")
            continue
        if reference.kind == "persona" and persona_seen:
            warnings.append(f"目录条目 {index + 1} 是重复 persona，已忽略")
            continue
        if reference.kind == "library" and library_count >= MAX_LIBRARY_REFERENCES:
            if not library_limit_reported:
                warnings.append(f"参考图库超过 {MAX_LIBRARY_REFERENCES} 张，超出条目已忽略")
                library_limit_reported = True
            continue
        if reference.id in seen_ids:
            warnings.append(f"目录条目 {index + 1} 的稳定 ID 重复，已忽略：{reference.id}")
            continue
        if reference.source in seen_sources:
            warnings.append(f"目录条目 {index + 1} 的图片来源重复，已忽略：{reference.source}")
            continue
        references.append(reference)
        seen_ids.add(reference.id)
        seen_sources.add(reference.source)
        persona_seen = persona_seen or reference.kind == "persona"
        if reference.kind == "library":
            library_count += 1
    return tuple(references)


def _legacy_library_items(legacy_library: Any, warnings: list[str]) -> list[Any]:
    if isinstance(legacy_library, list):
        return legacy_library
    if isinstance(legacy_library, (tuple, set)):
        return list(legacy_library)
    if isinstance(legacy_library, dict):
        return [legacy_library]
    raw_text = str(legacy_library or "").strip()
    if not raw_text:
        return []
    if raw_text.startswith("[") and raw_text.endswith("]"):
        try:
            parsed = json.loads(raw_text)
        except (TypeError, ValueError, json.JSONDecodeError):
            warnings.append("旧参考图库 JSON 数组解析失败，已回退为逐行迁移")
        else:
            if isinstance(parsed, list):
                return parsed
    return raw_text.splitlines()


def _raw_catalog_has_content(raw_catalog: Any) -> bool:
    if isinstance(raw_catalog, str):
        return bool(raw_catalog.strip())
    if isinstance(raw_catalog, (list, tuple, set, dict)):
        return bool(raw_catalog)
    return raw_catalog is not None


def _canonical_catalog_is_strictly_persistable(
    raw_catalog: Any,
    preset_names: Collection[str],
) -> bool:
    raw_items = _catalog_items(raw_catalog, [])
    if raw_items is None:
        return False
    try:
        serialized = validate_and_serialize(raw_items, preset_names=preset_names)
    except CatalogValidationError:
        return False
    return len(serialized) == len(raw_items)


def _migrate_legacy_catalog(
    legacy_persona: Any,
    legacy_library: Any,
    preset_names: Collection[str],
    warnings: list[str],
) -> tuple[PhotoReference, ...]:
    references: list[PhotoReference] = []
    persona_source = _legacy_source(legacy_persona)
    if persona_source:
        references.append(
            PhotoReference(
                id="persona",
                kind="persona",
                source=persona_source,
                note="默认人设参考图",
                reference_roles=("identity",),
                outfit_category="",
                outfit_lock_default=False,
                scene_categories=(),
                preferred_preset="",
                metadata_source="migration",
            )
        )

    raw_items = _legacy_library_items(legacy_library, warnings)
    seen_sources = {persona_source} if persona_source else set()
    for raw_item in raw_items:
        reference = _migrate_library_reference(raw_item, preset_names, warnings)
        if reference is None or reference.source in seen_sources:
            continue
        seen_sources.add(reference.source)
        references.append(reference)
        if sum(item.kind == "library" for item in references) >= MAX_LIBRARY_REFERENCES:
            break
    return tuple(references)


def load_catalog(
    raw_catalog: Any,
    *,
    catalog_version: Any,
    legacy_persona: Any = "",
    legacy_library: Any = None,
    user_cleared: bool = False,
    preset_names: Iterable[str] = (),
) -> CatalogLoadResult:
    presets = {_clean_text(item, 80) for item in preset_names if _clean_text(item, 80)}
    try:
        version = int(catalog_version or 0)
    except (TypeError, ValueError):
        version = 0
    warnings: list[str] = []
    canonical_references = _load_canonical_references(raw_catalog, presets, warnings)
    if version >= CATALOG_VERSION:
        if canonical_references is None:
            warnings.append("规范参考图目录不是数组，已按空目录加载")
            canonical_references = ()
        read_only = not _canonical_catalog_is_strictly_persistable(raw_catalog, presets)
        if read_only:
            warnings.append("规范参考图目录未通过完整校验，当前进程将以只读模式使用")
        return CatalogLoadResult(canonical_references, False, tuple(warnings), read_only)

    if _raw_catalog_has_content(raw_catalog):
        if canonical_references is not None and _canonical_catalog_is_strictly_persistable(raw_catalog, presets):
            warnings.append("检测到未标版本的规范参考图目录，已优先保留并等待补写版本号")
            return CatalogLoadResult(canonical_references, True, tuple(warnings))

        warnings.append("未标版本的规范参考图目录校验失败，已只读加载且不会覆盖原配置")
        if canonical_references:
            return CatalogLoadResult(canonical_references, False, tuple(warnings), True)
        warnings.append("规范目录没有可用条目，当前进程回退到旧配置的只读内存投影")
        legacy_references = _migrate_legacy_catalog(legacy_persona, legacy_library, presets, warnings)
        return CatalogLoadResult(legacy_references, False, tuple(warnings), True)

    legacy_references = _migrate_legacy_catalog(legacy_persona, legacy_library, presets, warnings)
    return CatalogLoadResult(legacy_references, True, tuple(warnings))
