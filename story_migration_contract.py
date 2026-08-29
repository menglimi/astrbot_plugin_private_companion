from __future__ import annotations

import hashlib
import json
import math
from typing import Any


STORY_MIGRATION_API_FAMILY = "companion.story-migration"
STORY_MIGRATION_API_VERSION = "companion.story-migration-api.v1"
STORY_MIGRATION_SNAPSHOT_VERSION = "companion.story-migration-snapshot.v1"
STORY_MIGRATION_OWNER_ID = "astrbot_plugin_private_companion"

MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
MAX_PROJECTS = 20
MAX_CHUNKS = 40
MAX_MEMORY_ENTRIES = 50
MAX_MANUAL_EDITS = 10
MAX_QUALITY_REVIEWS = 20


class StoryMigrationSnapshotError(ValueError):
    """A stable, body-free rejection at the Story migration boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _reject(code: str = "story_snapshot_invalid") -> None:
    raise StoryMigrationSnapshotError(code)


def _text(
    value: Any,
    limit: int,
    *,
    required: bool = False,
) -> str:
    if type(value) is not str or "\x00" in value:
        _reject()
    if required and (not value or value != value.strip()):
        _reject()
    if len(value) > limit:
        _reject("story_snapshot_too_large")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        _reject()
    return value


def _identifier(value: Any, limit: int) -> str:
    normalized = _text(value, limit, required=True)
    if any(ord(character) < 32 or 127 <= ord(character) < 160 for character in normalized):
        _reject()
    return normalized


def _integer(value: Any, minimum: int, maximum: int) -> int:
    if type(value) is not int:
        _reject()
    if value < minimum or value > maximum:
        _reject("story_snapshot_too_large")
    return value


def _number(value: Any, minimum: float, maximum: float) -> int | float:
    if type(value) not in (int, float):
        _reject()
    try:
        numeric = float(value)
    except OverflowError:
        _reject("story_snapshot_too_large")
    if not math.isfinite(numeric):
        _reject()
    if numeric < minimum or numeric > maximum:
        _reject("story_snapshot_too_large")
    return value


def _boolean(value: Any) -> bool:
    if type(value) is not bool:
        _reject()
    return value


def _list(value: Any, maximum: int, *, dictionaries: bool = False) -> list[Any]:
    if type(value) is not list:
        _reject()
    if len(value) > maximum:
        _reject("story_snapshot_too_large")
    if dictionaries and any(type(item) is not dict for item in value):
        _reject()
    return value


def _string_list(value: Any, maximum: int, item_limit: int) -> list[str]:
    return [_text(item, item_limit) for item in _list(value, maximum)]


def _known_keys(value: dict[str, Any], allowed: set[str], ignored: set[str] | None = None) -> None:
    if any(type(key) is not str for key in value):
        _reject()
    if set(value) - allowed - (ignored or set()):
        _reject("story_snapshot_unknown_field")


def _optional_text_fields(
    value: dict[str, Any],
    limits: dict[str, int],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field, limit in limits.items():
        if field in value:
            result[field] = _text(value[field], limit)
    return result


def _optional_integer_fields(
    value: dict[str, Any],
    limits: dict[str, tuple[int, int]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field, (minimum, maximum) in limits.items():
        if field in value:
            result[field] = _integer(value[field], minimum, maximum)
    return result


def _optional_number_fields(
    value: dict[str, Any],
    limits: dict[str, tuple[float, float]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field, (minimum, maximum) in limits.items():
        if field in value:
            result[field] = _number(value[field], minimum, maximum)
    return result


def _strict_optional_id(value: dict[str, Any], result: dict[str, Any]) -> None:
    if "id" in value:
        result["id"] = _identifier(value["id"], 80)


def _reject_duplicate_ids(records: list[Any]) -> None:
    seen: set[str] = set()
    for record in records:
        if type(record) is not dict or "id" not in record:
            continue
        record_id = record["id"]
        if record_id in seen:
            _reject("story_snapshot_collection_id_ambiguous")
        seen.add(record_id)


def _chunk(value: dict[str, Any]) -> dict[str, Any]:
    text_fields = {"id": 80, "text": 8_000, "title": 160}
    integer_fields = {"chars": (0, 8_000), "index": (0, 9_999)}
    number_fields = {
        "at": (0.0, 10_000_000_000.0),
        "created_at": (0.0, 10_000_000_000.0),
        "created_ts": (0.0, 10_000_000_000.0),
    }
    boolean_fields = {"manually_edited"}
    allowed = set(text_fields) | set(integer_fields) | set(number_fields) | boolean_fields
    _known_keys(value, allowed)
    result = _optional_text_fields(value, text_fields)
    _strict_optional_id(value, result)
    result.update(_optional_integer_fields(value, integer_fields))
    result.update(_optional_number_fields(value, number_fields))
    for field in boolean_fields:
        if field in value:
            result[field] = _boolean(value[field])
    return result


def _story_bible(value: Any) -> dict[str, Any]:
    if type(value) is not dict:
        _reject()
    text_fields = {
        "mainline_direction": 500,
        "next_direction": 500,
        "story_time": 160,
    }
    list_fields = {
        "active_themes": (6, 80),
        "resolved_threads": (20, 160),
        "unresolved_threads": (12, 160),
        "important_facts": (12, 240),
        "recent_keywords": (20, 80),
        "recent_outlines": (6, 800),
    }
    integer_fields = {"last_updated_chunk": (0, 9_999)}
    _known_keys(value, set(text_fields) | set(list_fields) | set(integer_fields))
    result = _optional_text_fields(value, text_fields)
    result.update(_optional_integer_fields(value, integer_fields))
    for field, (maximum, item_limit) in list_fields.items():
        if field in value:
            result[field] = _string_list(value[field], maximum, item_limit)
    return result


def _relationship(value: Any) -> str | dict[str, str]:
    if type(value) is str:
        return _text(value, 500)
    if type(value) is not dict:
        _reject()
    fields = {
        "name": 80,
        "target": 80,
        "relationship": 160,
        "description": 500,
        "status": 40,
        "notes": 500,
    }
    _known_keys(value, set(fields))
    return _optional_text_fields(value, fields)


def _character(value: dict[str, Any]) -> dict[str, Any]:
    text_fields = {
        "id": 80,
        "name": 80,
        "role": 120,
        "description": 1_000,
        "appearance": 1_000,
        "personality": 1_000,
        "background": 1_000,
        "motivation": 500,
        "relationship": 500,
        "notes": 1_000,
        "status": 40,
    }
    list_fields = {
        "must_keep_traits": (20, 120),
        "traits": (20, 120),
        "tags": (20, 120),
    }
    number_fields = {
        "created_at": (0.0, 10_000_000_000.0),
        "updated_at": (0.0, 10_000_000_000.0),
    }
    allowed = set(text_fields) | set(list_fields) | set(number_fields) | {"relationships"}
    _known_keys(value, allowed)
    result = _optional_text_fields(value, text_fields)
    _strict_optional_id(value, result)
    result.update(_optional_number_fields(value, number_fields))
    for field, (maximum, item_limit) in list_fields.items():
        if field in value:
            result[field] = _string_list(value[field], maximum, item_limit)
    if "relationships" in value:
        result["relationships"] = [
            _relationship(item) for item in _list(value["relationships"], 20)
        ]
    return result


def _revision(value: Any) -> str | dict[str, Any]:
    if type(value) is str:
        return _text(value, 2_000)
    if type(value) is not dict:
        _reject()
    text_fields = {
        "id": 80,
        "type": 40,
        "title": 160,
        "content": 5_000,
    }
    integer_fields = {"chunk_index": (-1, 9_999)}
    number_fields = {"created_at": (0.0, 10_000_000_000.0)}
    _known_keys(value, set(text_fields) | set(integer_fields) | set(number_fields))
    result = _optional_text_fields(value, text_fields)
    _strict_optional_id(value, result)
    result.update(_optional_integer_fields(value, integer_fields))
    result.update(_optional_number_fields(value, number_fields))
    return result


def _quality_review(value: dict[str, Any]) -> dict[str, Any]:
    text_fields = {
        "id": 80,
        "rewrite_focus": 1_000,
        "summary": 1_000,
    }
    integer_fields = {"chunk_index": (-1, 9_999)}
    number_fields = {
        "persona_score": (0.0, 10.0),
        "progress_score": (0.0, 10.0),
        "repetition_score": (0.0, 10.0),
        "style_score": (0.0, 10.0),
        "continuity_score": (0.0, 10.0),
        "coherence_score": (0.0, 10.0),
        "overall": (0.0, 10.0),
        "created_at": (0.0, 10_000_000_000.0),
    }
    allowed = (
        set(text_fields)
        | set(integer_fields)
        | set(number_fields)
        | {"passed", "issues", "suggestions", "scores"}
    )
    _known_keys(value, allowed, {"provider_id"})
    if "provider_id" in value:
        _text(value["provider_id"], 240)
    result = _optional_text_fields(value, text_fields)
    _strict_optional_id(value, result)
    result.update(_optional_integer_fields(value, integer_fields))
    result.update(_optional_number_fields(value, number_fields))
    if "passed" in value:
        result["passed"] = _boolean(value["passed"])
    for field in ("issues", "suggestions"):
        if field in value:
            result[field] = _string_list(value[field], 20, 500)
    if "scores" in value:
        scores = value["scores"]
        if type(scores) is not dict:
            _reject()
        score_fields = {"persona", "progress", "repetition", "style", "continuity", "coherence"}
        _known_keys(scores, score_fields)
        result["scores"] = {
            field: _number(scores[field], 0.0, 10.0)
            for field in sorted(scores)
        }
    return result


def _manual_edit(value: dict[str, Any]) -> dict[str, Any]:
    text_fields = {
        "id": 80,
        "type": 40,
        "title": 160,
        "content": 5_000,
    }
    integer_fields = {"chunk_index": (-1, 9_999)}
    number_fields = {"created_at": (0.0, 10_000_000_000.0)}
    _known_keys(value, set(text_fields) | set(integer_fields) | set(number_fields))
    result = _optional_text_fields(value, text_fields)
    _strict_optional_id(value, result)
    result.update(_optional_integer_fields(value, integer_fields))
    result.update(_optional_number_fields(value, number_fields))
    return result


def _memory_entry(value: dict[str, Any], project_id: str) -> dict[str, Any]:
    text_fields = {
        "id": 80,
        "type": 40,
        "kind": 40,
        "content": 1_000,
        "project_id": 80,
    }
    integer_fields = {"importance": (0, 5)}
    number_fields = {"created_at": (0.0, 10_000_000_000.0)}
    _known_keys(value, set(text_fields) | set(integer_fields) | set(number_fields) | {"keywords"})
    result = _optional_text_fields(
        value,
        {field: limit for field, limit in text_fields.items() if field != "project_id"},
    )
    _strict_optional_id(value, result)
    raw_project_id = value.get("project_id")
    if raw_project_id is None or (
        type(raw_project_id) is str and raw_project_id == ""
    ):
        result["project_id"] = project_id
    else:
        memory_project_id = _identifier(raw_project_id, 80)
        if memory_project_id != project_id:
            _reject("story_snapshot_memory_project_conflict")
        result["project_id"] = project_id
    result.update(_optional_integer_fields(value, integer_fields))
    result.update(_optional_number_fields(value, number_fields))
    if "keywords" in value:
        result["keywords"] = _string_list(value["keywords"], 20, 120)
    return result


_PROJECT_TEXT_FIELDS = {
    "id": 80,
    "owner_id": 120,
    "title": 160,
    "work_type": 80,
    "premise": 2_000,
    "tone": 240,
    "point_of_view": 120,
    "point_of_view_note": 500,
    "source": 120,
    "source_text": 8_000,
    "status": 40,
    "next_hint": 2_000,
    "last_manual_edit_summary": 1_000,
}
_PROJECT_INTEGER_FIELDS = {
    "source_order": (0, MAX_PROJECTS - 1),
    "point_of_view_policy_version": (0, 100),
    "target_chars": (0, 2_000_000),
    "current_chars": (0, 2_000_000),
    "share_count": (0, 1_000_000),
    "advance_failure_count": (0, 1_000_000),
    "legacy_fallback_chunks_removed": (0, 1_000_000),
}
_PROJECT_NUMBER_FIELDS = {
    "created_at": (0.0, 10_000_000_000.0),
    "last_advanced_at": (0.0, 10_000_000_000.0),
    "next_advance_at": (0.0, 10_000_000_000.0),
    "last_share_at": (0.0, 10_000_000_000.0),
    "last_manual_edit_at": (0.0, 10_000_000_000.0),
    "last_advance_failed_at": (0.0, 10_000_000_000.0),
    "last_creative_burst_at": (0.0, 10_000_000_000.0),
}
_PROJECT_CONTAINER_FIELDS = {
    "draft_chunks",
    "disclosed_milestones",
    "story_bible",
    "creative_memory_pool",
    "outline",
    "characters",
    "revision_notes",
    "quality_reviews",
    "manual_edits",
}
_PROJECT_LOCAL_ONLY_FIELDS = {
    "cover_path",
    "cover_generated_at",
    "cover_generation_attempted_at",
    "cover_generation_attempts",
    "cover_generation_backend",
    "cover_generation_error",
    "cover_generation_next_retry_at",
    "cover_generation_person_policy",
    "cover_generation_prompt",
    "cover_generation_reference_image",
    "cover_generation_status",
    "cover_generation_style",
    "writing_provider_id",
    "review_provider_id",
    "last_advance_error",
}

_PROJECT_LOCAL_TEXT_FIELDS = {
    "cover_path": 1_000,
    "cover_generation_backend": 80,
    "cover_generation_error": 220,
    "cover_generation_person_policy": 40,
    "cover_generation_prompt": 1_800,
    "cover_generation_reference_image": 1_000,
    "cover_generation_status": 40,
    "cover_generation_style": 40,
    "writing_provider_id": 240,
    "review_provider_id": 240,
    "last_advance_error": 180,
}
_PROJECT_LOCAL_INTEGER_FIELDS = {
    "cover_generation_attempts": (0, 1_000_000),
}
_PROJECT_LOCAL_NUMBER_FIELDS = {
    "cover_generated_at": (0.0, 10_000_000_000.0),
    "cover_generation_attempted_at": (0.0, 10_000_000_000.0),
    "cover_generation_next_retry_at": (0.0, 10_000_000_000.0),
}


def _validate_project_local_fields(value: dict[str, Any]) -> None:
    _optional_text_fields(value, _PROJECT_LOCAL_TEXT_FIELDS)
    _optional_integer_fields(value, _PROJECT_LOCAL_INTEGER_FIELDS)
    _optional_number_fields(value, _PROJECT_LOCAL_NUMBER_FIELDS)


def _project(value: dict[str, Any], source_order: int, owner_id: str) -> dict[str, Any]:
    allowed = (
        set(_PROJECT_TEXT_FIELDS)
        | set(_PROJECT_INTEGER_FIELDS)
        | set(_PROJECT_NUMBER_FIELDS)
        | _PROJECT_CONTAINER_FIELDS
    )
    _known_keys(value, allowed, _PROJECT_LOCAL_ONLY_FIELDS)
    _validate_project_local_fields(value)
    project_id = _identifier(value.get("id"), 80)
    raw_owner = value.get("owner_id")
    if raw_owner is None or (type(raw_owner) is str and raw_owner == ""):
        projected_owner = owner_id
    else:
        projected_owner = _identifier(raw_owner, 120)
    if projected_owner != owner_id:
        _reject("story_snapshot_owner_conflict")
    projected_source_order = (
        _integer(value["source_order"], 0, MAX_PROJECTS - 1)
        if "source_order" in value
        else source_order
    )
    result: dict[str, Any] = {
        "id": project_id,
        "owner_id": owner_id,
        "source_order": projected_source_order,
    }
    for field, limit in _PROJECT_TEXT_FIELDS.items():
        if field not in {"id", "owner_id"} and field in value:
            result[field] = _text(value[field], limit)
    result.update(_optional_integer_fields(value, _PROJECT_INTEGER_FIELDS))
    result.update(_optional_number_fields(value, _PROJECT_NUMBER_FIELDS))
    result["draft_chunks"] = [
        _chunk(item)
        for item in _list(value.get("draft_chunks", []), MAX_CHUNKS, dictionaries=True)
    ]
    _reject_duplicate_ids(result["draft_chunks"])
    result["disclosed_milestones"] = _string_list(
        value.get("disclosed_milestones", []), 64, 80
    )
    result["story_bible"] = _story_bible(value.get("story_bible", {}))
    result["creative_memory_pool"] = [
        _memory_entry(item, project_id)
        for item in _list(
            value.get("creative_memory_pool", []),
            MAX_MEMORY_ENTRIES,
            dictionaries=True,
        )
    ]
    _reject_duplicate_ids(result["creative_memory_pool"])
    result["outline"] = _string_list(value.get("outline", []), 30, 500)
    result["characters"] = [
        _character(item)
        for item in _list(value.get("characters", []), 20, dictionaries=True)
    ]
    _reject_duplicate_ids(result["characters"])
    result["revision_notes"] = [
        _revision(item) for item in _list(value.get("revision_notes", []), 20)
    ]
    _reject_duplicate_ids(result["revision_notes"])
    result["quality_reviews"] = [
        _quality_review(item)
        for item in _list(
            value.get("quality_reviews", []),
            MAX_QUALITY_REVIEWS,
            dictionaries=True,
        )
    ]
    _reject_duplicate_ids(result["quality_reviews"])
    result["manual_edits"] = [
        _manual_edit(item)
        for item in _list(
            value.get("manual_edits", []),
            MAX_MANUAL_EDITS,
            dictionaries=True,
        )
    ]
    _reject_duplicate_ids(result["manual_edits"])
    return result


def canonical_story_snapshot_payload(snapshot: dict[str, Any]) -> bytes:
    """Serialize the three hashed fields of a validated snapshot envelope."""

    if type(snapshot) is not dict:
        _reject()
    try:
        return json.dumps(
            {
                "version": snapshot["version"],
                "owner_id": snapshot["owner_id"],
                "projects": snapshot["projects"],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (KeyError, TypeError, ValueError, OverflowError, UnicodeEncodeError):
        _reject()


def build_story_migration_snapshot(
    projects: Any,
    *,
    owner_id: str = STORY_MIGRATION_OWNER_ID,
) -> dict[str, Any]:
    """Build a deterministic, path-free copy without mutating the legacy shelf."""

    owner_id = _identifier(owner_id, 120)
    raw_projects = _list(projects, MAX_PROJECTS, dictionaries=True)
    projected = [
        _project(project, source_order, owner_id)
        for source_order, project in enumerate(raw_projects)
    ]
    project_ids = [project["id"] for project in projected]
    if len(project_ids) != len(set(project_ids)):
        _reject("story_snapshot_project_id_ambiguous")
    source_orders = [project["source_order"] for project in projected]
    if sorted(source_orders) != list(range(len(projected))):
        _reject("story_snapshot_source_order_ambiguous")
    projected.sort(key=lambda project: project["id"])
    snapshot: dict[str, Any] = {
        "version": STORY_MIGRATION_SNAPSHOT_VERSION,
        "owner_id": owner_id,
        "projects": projected,
    }
    canonical = canonical_story_snapshot_payload(snapshot)
    if len(canonical) > MAX_SNAPSHOT_BYTES:
        _reject("story_snapshot_too_large")
    digest = hashlib.sha256(canonical).hexdigest()
    snapshot["snapshot_id"] = f"storysnap_{digest}"
    snapshot["snapshot_sha256"] = digest
    try:
        encoded = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, UnicodeEncodeError):
        _reject()
    if len(encoded) > MAX_SNAPSHOT_BYTES:
        _reject("story_snapshot_too_large")
    try:
        return json.loads(encoded)
    except (TypeError, ValueError, OverflowError, UnicodeDecodeError):
        _reject()


__all__ = [
    "MAX_CHUNKS",
    "MAX_MANUAL_EDITS",
    "MAX_MEMORY_ENTRIES",
    "MAX_PROJECTS",
    "MAX_QUALITY_REVIEWS",
    "MAX_SNAPSHOT_BYTES",
    "STORY_MIGRATION_API_FAMILY",
    "STORY_MIGRATION_API_VERSION",
    "STORY_MIGRATION_OWNER_ID",
    "STORY_MIGRATION_SNAPSHOT_VERSION",
    "StoryMigrationSnapshotError",
    "build_story_migration_snapshot",
    "canonical_story_snapshot_payload",
]
