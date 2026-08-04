"""Guided editing compiler for photo reference metadata.

The editor deals in plain-language answers; the selector consumes this module's
small, deterministic result objects.  Nothing in this module persists data.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from .photo_reference_catalog import PhotoReference

OUTFIT_BEHAVIORS = {
    "ignore": "ignore",
    "参考但不保持": "reference_without_lock",
    "可以参考，但不要求保持": "reference_without_lock",
    "通常保持，除非用户明确要求换装": "preserve_unless_explicit_change",
    "preserve": "preserve_unless_explicit_change",
    "preserve_unless_explicit_change": "preserve_unless_explicit_change",
}
_ROLE_LABELS = {
    "identity": "人物外貌",
    "outfit": "穿搭",
    "pose": "动作姿势",
    "scene": "场景背景",
    "style": "画面风格",
    "continuity": "连续性",
    "source": "原图",
}


@dataclass(frozen=True)
class MetadataField:
    field: str
    value: Any
    source: str
    label: str


@dataclass(frozen=True)
class CompileResult:
    metadata: dict[str, Any]
    behavior_summary: str
    fields: tuple[MetadataField, ...]
    differences: tuple[dict[str, Any], ...]
    missing: tuple[str, ...]
    conflicts: tuple[str, ...]
    recommended_trials: tuple[dict[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "metadata": self.metadata,
            "behavior_summary": self.behavior_summary,
            "fields": [asdict(field) for field in self.fields],
            "differences": list(self.differences),
            "missing": list(self.missing),
            "conflicts": list(self.conflicts),
            "recommended_trials": list(self.recommended_trials),
        }


@dataclass(frozen=True)
class ReferenceExplanation:
    reference_id: str
    behavior_summary: str
    fields: tuple[MetadataField, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference_id": self.reference_id,
            "behavior_summary": self.behavior_summary,
            "fields": [asdict(field) for field in self.fields],
            "warnings": list(self.warnings),
        }


def _values(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _behavior(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in OUTFIT_BEHAVIORS:
        return OUTFIT_BEHAVIORS[text]
    if "完全不" in text or "不参考" in text or text in {"ignore", "none"}:
        return "ignore"
    if "通常保持" in text or "明确要求换装" in text:
        return "preserve_unless_explicit_change"
    return "reference_without_lock"


def _roles(intent: Mapping[str, Any]) -> list[str]:
    preserve = intent.get("preserve", intent.get("reference_roles", ()))
    result: list[str] = []
    for role in _values(preserve):
        role = role.lower()
        if role in _ROLE_LABELS and role not in result:
            result.append(role)
    return result


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def compile_reference_metadata(
    intent: Mapping[str, Any] | None,
    available_presets: Iterable[str] = (),
    *,
    saved: Mapping[str, Any] | PhotoReference | None = None,
) -> CompileResult:
    """Compile editor answers without writing to the catalog."""
    answers = dict(intent or {})
    roles = _roles(answers)
    outfit_behavior = _behavior(answers.get("outfit_behavior")) if "outfit" in roles else "ignore"
    prefer = _as_mapping(answers.get("prefer"))
    avoid = _as_mapping(answers.get("avoid"))
    scenes = _values(prefer.get("scenes", answers.get("scene_categories")))
    times = _values(prefer.get("times", answers.get("time_categories")))
    excluded_scenes = _values(avoid.get("scenes", answers.get("excluded_scene_categories")))
    excluded_times = _values(avoid.get("times", answers.get("excluded_time_categories")))
    preset = str(answers.get("preferred_preset") or answers.get("preset") or "").strip()
    presets = {str(item).strip() for item in available_presets if str(item).strip()}
    conflicts: list[str] = []
    if preset and presets and preset not in presets:
        conflicts.append(f"首选预设不存在：{preset}")
        preset = ""
    overlap = set(scenes) & set(excluded_scenes)
    if overlap:
        conflicts.append("偏好和排除场景重复：" + ", ".join(sorted(overlap)))
        scenes = [item for item in scenes if item not in overlap]
    overlap_time = set(times) & set(excluded_times)
    if overlap_time:
        conflicts.append("偏好和排除时间重复：" + ", ".join(sorted(overlap_time)))
        times = [item for item in times if item not in overlap_time]
    if "outfit" in roles and not str(answers.get("outfit_category") or "").strip() and outfit_behavior != "ignore":
        conflicts.append("已选择穿搭职责，但没有填写服装类型")

    metadata: dict[str, Any] = {
        "editor_intent": {
            "version": 1,
            "preserve": roles,
            "outfit_behavior": outfit_behavior,
            "prefer": {"scenes": scenes, "times": times},
            "avoid": {"scenes": excluded_scenes, "times": excluded_times},
            "fallback": str(answers.get("fallback") or "matching_only"),
        },
        "reference_roles": roles,
        "outfit_category": (
            str(answers.get("outfit_category") or "").strip()
            if "outfit" in roles and outfit_behavior != "ignore"
            else ""
        ),
        "outfit_lock_default": outfit_behavior == "preserve_unless_explicit_change",
        "scene_categories": scenes,
        "excluded_scene_categories": excluded_scenes,
        "time_categories": times,
        "excluded_time_categories": excluded_times,
        "selection_eligibility": str(answers.get("selection_eligibility") or "matching_only"),
        "preferred_preset": preset,
        "metadata_source": "guided_editor",
    }
    fields = tuple(
        MetadataField(field, value, "回答：" + field, _ROLE_LABELS.get(field, field))
        for field, value in (
            ("reference_roles", roles),
            ("outfit_category", metadata["outfit_category"]),
            ("outfit_lock_default", metadata["outfit_lock_default"]),
            ("scene_categories", scenes),
            ("excluded_scene_categories", excluded_scenes),
            ("time_categories", times),
            ("excluded_time_categories", excluded_times),
            ("selection_eligibility", metadata["selection_eligibility"]),
            ("preferred_preset", preset),
        )
    )
    old = _mapping(saved)
    differences = tuple(
        {"field": key, "saved": old.get(key), "generated": value}
        for key, value in metadata.items()
        if key != "editor_intent" and key in old and old.get(key) != value
    )
    missing = tuple(
        field for field in ("reference_roles", "selection_eligibility") if not metadata.get(field)
    )
    summary = _summary(metadata)
    trials = (
        {"label": "通用自拍", "message": "现在给我拍一张自然的自拍吧"},
        {"label": "冲突换装", "message": "晚上了，在卧室穿着睡衣给我拍一张吧"},
    )
    return CompileResult(metadata, summary, fields, differences, missing, tuple(conflicts), trials)


def _mapping(saved: Mapping[str, Any] | PhotoReference | None) -> Mapping[str, Any]:
    if isinstance(saved, PhotoReference):
        return {
            "reference_roles": list(saved.reference_roles),
            "outfit_category": saved.outfit_category,
            "outfit_lock_default": saved.outfit_lock_default,
            "scene_categories": list(saved.scene_categories),
            "excluded_scene_categories": list(saved.excluded_scene_categories),
            "time_categories": list(saved.time_categories),
            "excluded_time_categories": list(saved.excluded_time_categories),
            "selection_eligibility": saved.selection_eligibility,
            "preferred_preset": saved.preferred_preset,
        }
    return saved if isinstance(saved, Mapping) else {}


def _summary(metadata: Mapping[str, Any]) -> str:
    roles = [str(item) for item in metadata.get("reference_roles", ())]
    labels = "、".join(_ROLE_LABELS.get(role, role) for role in roles) or "不承担特定职责"
    outfit = metadata.get("outfit_lock_default")
    suffix = "；用户明确换装时才改变穿搭" if outfit else "；穿搭仅作参考，不强制保持"
    return f"这张图用于参考：{labels}{suffix}。"


def explain_reference_metadata(reference: PhotoReference | Mapping[str, Any]) -> ReferenceExplanation:
    data = _mapping(reference if isinstance(reference, PhotoReference) else reference)
    result = compile_reference_metadata(data.get("editor_intent") or data, ())
    warnings: list[str] = []
    if not data.get("editor_intent"):
        warnings.append("该条目没有保存维护者原始回答，显示的是兼容字段推导结果")
    return ReferenceExplanation(str(data.get("id") or ""), result.behavior_summary, result.fields, tuple(warnings))


__all__ = [
    "CompileResult",
    "MetadataField",
    "ReferenceExplanation",
    "compile_reference_metadata",
    "explain_reference_metadata",
]
