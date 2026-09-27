# -*- coding: utf-8 -*-
"""photo_reference_catalog 拆分件 part03（机械搬移，行为不变）。

函数 / 常量 / 类逐字搬自 photo_reference_catalog.py，仅调整模块级依赖的导入来源。
"""
import uuid
from typing import Any, Collection, Iterable, Literal, cast

try:  # package import
    from .photo_reference_catalog_part01 import (
        CATALOG_VERSION,
        CatalogLoadResult,
        CatalogValidationError,
        MAX_LIBRARY_REFERENCES,
        PhotoReference,
        _CATEGORY_PRESETS,
        _clean_text,
        _normalize_outfit_category,
    )
except ImportError:  # direct test/import from the plugin directory
    from photo_reference_catalog_part01 import (
        CATALOG_VERSION,
        CatalogLoadResult,
        CatalogValidationError,
        MAX_LIBRARY_REFERENCES,
        PhotoReference,
        _CATEGORY_PRESETS,
        _clean_text,
        _normalize_outfit_category,
    )
try:  # package import
    from .photo_reference_catalog_part02 import (
        _canonical_catalog_is_strictly_persistable,
        _infer_outfit_category,
        _infer_reference_roles,
        _infer_scene_categories,
        _infer_time_categories,
        _legacy_library_items,
        _legacy_source,
        _load_canonical_references,
        _migrate_library_reference,
        _raw_catalog_has_content,
        validate_and_serialize,
    )
except ImportError:  # direct test/import from the plugin directory
    from photo_reference_catalog_part02 import (
        _canonical_catalog_is_strictly_persistable,
        _infer_outfit_category,
        _infer_reference_roles,
        _infer_scene_categories,
        _infer_time_categories,
        _legacy_library_items,
        _legacy_source,
        _load_canonical_references,
        _migrate_library_reference,
        _raw_catalog_has_content,
        validate_and_serialize,
    )


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
    # Canonical v1 entries remain read-compatible with v2.  Do not rewrite them
    # during startup: migration is deliberately lazy and is performed by the
    # guided editor/save path.
    if version >= 1:
        if (
            not user_cleared
            and canonical_references == ()
            and _canonical_catalog_is_strictly_persistable(raw_catalog, presets)
        ):
            legacy_references = _migrate_legacy_catalog(
                legacy_persona,
                legacy_library,
                presets,
                warnings,
            )
            if legacy_references:
                warnings.append("规范参考图目录异常为空，已从残留旧配置恢复并等待重新保存")
                return CatalogLoadResult(legacy_references, True, tuple(warnings))
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
