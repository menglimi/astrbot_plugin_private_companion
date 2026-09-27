# -*- coding: utf-8 -*-
"""photo_reference_catalog 拆分件 part02（机械搬移，行为不变）。

函数 / 常量 / 类逐字搬自 photo_reference_catalog.py，仅调整模块级依赖的导入来源。
"""
import hashlib
import json
import re
from dataclasses import dataclass, replace
from typing import Any, Collection, Iterable, Literal, cast

try:  # package import
    from .photo_reference_catalog_part01 import (
        CatalogValidationError,
        MAX_LIBRARY_REFERENCES,
        PhotoReference,
        _CATEGORY_PRESETS,
        _OUTFIT_PATTERNS,
        _SCENE_TOKENS,
        _TIME_TOKENS,
        _append_error,
        _clean_text,
        _migration_bool,
        _normalize_outfit_category,
        _normalize_roles,
        _normalize_scene_categories,
        _normalize_time_categories,
        _serialize_reference,
        _strict_reference,
    )
except ImportError:  # direct test/import from the plugin directory
    from photo_reference_catalog_part01 import (
        CatalogValidationError,
        MAX_LIBRARY_REFERENCES,
        PhotoReference,
        _CATEGORY_PRESETS,
        _OUTFIT_PATTERNS,
        _SCENE_TOKENS,
        _TIME_TOKENS,
        _append_error,
        _clean_text,
        _migration_bool,
        _normalize_outfit_category,
        _normalize_roles,
        _normalize_scene_categories,
        _normalize_time_categories,
        _serialize_reference,
        _strict_reference,
    )


def _merge_persona_source_duplicates(
    normalized: list[tuple[int, PhotoReference]],
) -> list[tuple[int, PhotoReference]]:
    persona_row = next(
        ((index, reference) for index, reference in normalized if reference.kind == "persona"),
        None,
    )
    if persona_row is None:
        return normalized
    persona_index, persona = persona_row
    duplicates = [
        reference
        for _, reference in normalized
        if reference.kind == "library" and reference.source == persona.source
    ]
    if not duplicates:
        return normalized

    roles = list(persona.reference_roles)
    scenes = list(persona.scene_categories)
    notes = [persona.note] if persona.note else []
    outfit_category = persona.outfit_category
    preferred_preset = persona.preferred_preset
    outfit_lock_default = persona.outfit_lock_default
    for duplicate in duplicates:
        for role in duplicate.reference_roles:
            if role not in roles:
                roles.append(role)
        for scene in duplicate.scene_categories:
            if scene not in scenes:
                scenes.append(scene)
        if duplicate.note and duplicate.note not in notes:
            notes.append(duplicate.note)
        outfit_category = outfit_category or duplicate.outfit_category
        preferred_preset = preferred_preset or duplicate.preferred_preset
        outfit_lock_default = outfit_lock_default or duplicate.outfit_lock_default

    merged_persona = replace(
        persona,
        note="；".join(notes),
        reference_roles=tuple(roles),
        outfit_category=outfit_category,
        outfit_lock_default=outfit_lock_default,
        scene_categories=tuple(scenes),
        preferred_preset=preferred_preset,
    )
    return [
        (index, merged_persona if index == persona_index and reference.kind == "persona" else reference)
        for index, reference in normalized
        if not (reference.kind == "library" and reference.source == persona.source)
    ]


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
    normalized = _merge_persona_source_duplicates(normalized)

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
        "editor_intent": reference.editor_intent,
        "excluded_scene_categories": list(reference.excluded_scene_categories),
        "excluded_time_categories": list(reference.excluded_time_categories),
        "selection_eligibility": reference.selection_eligibility,
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
