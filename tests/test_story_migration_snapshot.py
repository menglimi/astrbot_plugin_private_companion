from __future__ import annotations

import copy
import hashlib
import json

import pytest

import story_migration_contract as story_contract
from story_migration_contract import (
    MAX_CHUNKS,
    MAX_MANUAL_EDITS,
    MAX_MEMORY_ENTRIES,
    MAX_PROJECTS,
    MAX_QUALITY_REVIEWS,
    STORY_MIGRATION_OWNER_ID,
    STORY_MIGRATION_SNAPSHOT_VERSION,
    StoryMigrationSnapshotError,
    build_story_migration_snapshot,
    canonical_story_snapshot_payload,
)


def _project(project_id: str = "work-1") -> dict:
    return {
        "id": project_id,
        "title": "雨停以后",
        "work_type": "短篇小说",
        "premise": "雨夜里没有寄出的信。",
        "tone": "克制",
        "point_of_view": "第三人称有限视角",
        "point_of_view_policy_version": 2,
        "point_of_view_note": "旧项目已调整",
        "source": "life",
        "source_text": "窗边的雨声",
        "target_chars": 2400,
        "current_chars": 8,
        "status": "drafting",
        "draft_chunks": [
            {
                "at": 100.0,
                "text": "雨停以后。",
                "chars": 6,
                "manually_edited": True,
            }
        ],
        "disclosed_milestones": ["first_excerpt"],
        "story_bible": {
            "mainline_direction": "找到收信人",
            "active_themes": ["雨"],
            "resolved_threads": [],
            "unresolved_threads": ["信去了哪里"],
            "important_facts": ["信封没有邮戳"],
            "next_direction": "去旧邮局",
            "story_time": "初秋雨停后的清晨",
            "recent_keywords": ["雨", "信"],
            "recent_outlines": ["- 去旧邮局"],
            "last_updated_chunk": 1,
        },
        "creative_memory_pool": [
            {
                "id": "memory-1",
                "type": "fact",
                "content": "信封没有邮戳",
                "keywords": ["信封"],
                "importance": 5,
                "created_at": 100.0,
                "project_id": project_id,
            }
        ],
        "outline": ["去旧邮局"],
        "characters": [
            {
                "id": "character-1",
                "name": "岚",
                "role": "主角",
                "description": "修复旧信件",
                "appearance": "灰色围巾",
                "personality": "谨慎",
                "background": "旧邮局附近长大",
                "relationships": [
                    {
                        "name": "闻",
                        "relationship": "旧友",
                        "status": "失联",
                    }
                ],
                "must_keep_traits": ["不轻易承诺"],
                "status": "alive",
                "created_at": 20.0,
                "updated_at": 30.0,
            }
        ],
        "revision_notes": [
            "保留克制语气",
            {
                "id": "revision-1",
                "type": "note",
                "title": "时间线",
                "content": "清晨之后再进入午后",
                "created_at": 40.0,
            },
        ],
        "quality_reviews": [
            {
                "id": "review-1",
                "chunk_index": 0,
                "passed": True,
                "persona_score": 8,
                "progress_score": 9,
                "repetition_score": 8,
                "style_score": 9,
                "issues": [],
                "suggestions": ["保留动作收束"],
                "rewrite_focus": "",
                "scores": {
                    "persona": 8,
                    "progress": 9,
                    "repetition": 8,
                    "style": 9,
                },
                "created_at": 50.0,
                "provider_id": "must-not-cross-boundary",
            }
        ],
        "manual_edits": [
            {
                "id": "edit-1",
                "type": "chunk_text",
                "title": "修改第一段",
                "content": "雨停以后。",
                "chunk_index": 0,
                "created_at": 60.0,
            }
        ],
        "last_manual_edit_at": 60.0,
        "last_manual_edit_summary": "修改第一段",
        "next_hint": "去旧邮局",
        "created_at": 10.0,
        "last_advanced_at": 100.0,
        "next_advance_at": 200.0,
        "last_share_at": 0,
        "share_count": 0,
        "advance_failure_count": 0,
        "legacy_fallback_chunks_removed": 2,
        "cover_path": "/private/cover.png",
        "cover_generation_prompt": "secret cover prompt",
        "cover_generation_reference_image": "/private/reference.png",
        "cover_generation_backend": "private-backend",
        "writing_provider_id": "provider-secret",
        "review_provider_id": "review-provider-secret",
        "last_advance_error": "private runtime exception",
    }


def test_snapshot_is_deterministic_detached_path_free_and_owner_scoped() -> None:
    projects = [_project("work-b"), _project("work-a")]
    baseline = copy.deepcopy(projects)

    first = build_story_migration_snapshot(projects)
    second = build_story_migration_snapshot(projects)

    assert first == second
    assert projects == baseline
    assert set(first) == {
        "version",
        "snapshot_id",
        "snapshot_sha256",
        "owner_id",
        "projects",
    }
    assert first["version"] == STORY_MIGRATION_SNAPSHOT_VERSION
    assert first["owner_id"] == STORY_MIGRATION_OWNER_ID
    assert [item["id"] for item in first["projects"]] == ["work-a", "work-b"]
    assert [item["source_order"] for item in first["projects"]] == [1, 0]
    assert all(item["owner_id"] == STORY_MIGRATION_OWNER_ID for item in first["projects"])
    assert first["projects"][0]["story_bible"]["story_time"] == "初秋雨停后的清晨"
    assert first["projects"][0]["characters"][0]["must_keep_traits"] == ["不轻易承诺"]

    encoded = json.dumps(first, ensure_ascii=False, sort_keys=True)
    for forbidden_value in (
        "/private/cover.png",
        "secret cover prompt",
        "/private/reference.png",
        "private-backend",
        "provider-secret",
        "review-provider-secret",
        "must-not-cross-boundary",
        "private runtime exception",
    ):
        assert forbidden_value not in encoded
    for forbidden_key in ("cover_path", "provider_id", "writing_provider_id"):
        assert forbidden_key not in encoded

    canonical = canonical_story_snapshot_payload(first)
    assert first["snapshot_sha256"] == hashlib.sha256(canonical).hexdigest()
    assert first["snapshot_id"] == f"storysnap_{first['snapshot_sha256']}"

    projects[0]["title"] = "changed source"
    first["projects"][0]["title"] = "changed result"
    assert second["projects"][0]["title"] == "雨停以后"


def test_existing_exact_owner_is_preserved_without_mutation() -> None:
    project = _project()
    project["owner_id"] = STORY_MIGRATION_OWNER_ID
    snapshot = build_story_migration_snapshot([project])
    assert snapshot["projects"][0]["owner_id"] == STORY_MIGRATION_OWNER_ID
    assert project["owner_id"] == STORY_MIGRATION_OWNER_ID


def test_empty_legacy_owner_variants_have_identical_projection_and_hash() -> None:
    variants = []
    for owner in (None, ""):
        project = _project()
        project["owner_id"] = owner
        before = copy.deepcopy(project)
        variants.append(build_story_migration_snapshot([project]))
        assert project == before
    variants.append(build_story_migration_snapshot([_project()]))

    assert variants[0] == variants[1] == variants[2]
    assert variants[0]["projects"][0]["owner_id"] == STORY_MIGRATION_OWNER_ID


@pytest.mark.parametrize(
    ("collection", "record_index"),
    [
        ("draft_chunks", 0),
        ("characters", 0),
        ("revision_notes", 1),
        ("quality_reviews", 0),
        ("manual_edits", 0),
        ("creative_memory_pool", 0),
    ],
)
def test_present_optional_record_ids_are_strict_identifiers(
    collection: str,
    record_index: int,
) -> None:
    project = _project()
    project[collection][record_index]["id"] = "bad\nidentifier"

    with pytest.raises(StoryMigrationSnapshotError) as captured:
        build_story_migration_snapshot([project])

    assert captured.value.code == "story_snapshot_invalid"


@pytest.mark.parametrize(
    "collection",
    [
        "draft_chunks",
        "characters",
        "revision_notes",
        "quality_reviews",
        "manual_edits",
        "creative_memory_pool",
    ],
)
def test_each_project_collection_rejects_duplicate_present_ids(collection: str) -> None:
    project = _project()
    project[collection] = [{"id": "same"}, {"id": "same"}]

    with pytest.raises(StoryMigrationSnapshotError) as captured:
        build_story_migration_snapshot([project])

    assert captured.value.code == "story_snapshot_collection_id_ambiguous"
    assert str(captured.value) == captured.value.code


def test_memory_project_empty_variants_project_outer_id_with_same_hash() -> None:
    snapshots = []
    for marker in ("missing", None, ""):
        project = _project()
        memory = project["creative_memory_pool"][0]
        if marker == "missing":
            memory.pop("project_id")
        else:
            memory["project_id"] = marker
        snapshots.append(build_story_migration_snapshot([project]))

    assert snapshots[0] == snapshots[1] == snapshots[2]
    memory = snapshots[0]["projects"][0]["creative_memory_pool"][0]
    assert memory["project_id"] == "work-1"


@pytest.mark.parametrize(
    ("project_id", "code"),
    [
        ("other-project", "story_snapshot_memory_project_conflict"),
        (" padded ", "story_snapshot_invalid"),
        ([], "story_snapshot_invalid"),
    ],
)
def test_memory_project_conflict_or_malformed_value_is_body_free(
    project_id,
    code: str,
) -> None:
    project = _project()
    project["creative_memory_pool"][0]["project_id"] = project_id

    with pytest.raises(StoryMigrationSnapshotError) as captured:
        build_story_migration_snapshot([project])

    assert captured.value.code == code
    assert str(captured.value) == code


def test_valid_local_only_values_are_validated_then_excluded_from_hash() -> None:
    with_local = _project()
    without_local = copy.deepcopy(with_local)
    local_fields = {
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
    with_local.update(
        cover_generated_at=10.0,
        cover_generation_attempted_at=11.0,
        cover_generation_attempts=2,
        cover_generation_error="retry later",
        cover_generation_next_retry_at=12.0,
        cover_generation_person_policy="no_people",
        cover_generation_status="ready",
        cover_generation_style="watercolor",
    )
    for field in local_fields:
        without_local.pop(field, None)
    without_local["quality_reviews"][0].pop("provider_id", None)

    assert build_story_migration_snapshot([with_local]) == build_story_migration_snapshot(
        [without_local]
    )


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("cover_path", {}),
        ("cover_generated_at", float("nan")),
        ("cover_generation_attempted_at", []),
        ("cover_generation_attempts", True),
        ("cover_generation_backend", {"secret": "value"}),
        ("cover_generation_error", "bad\ud800"),
        ("cover_generation_next_retry_at", float("inf")),
        ("cover_generation_person_policy", []),
        ("cover_generation_prompt", {}),
        ("cover_generation_reference_image", []),
        ("cover_generation_status", {}),
        ("cover_generation_style", []),
        ("writing_provider_id", {}),
        ("review_provider_id", []),
        ("last_advance_error", {}),
    ],
)
def test_invalid_local_only_values_are_rejected_before_stripping(field: str, invalid) -> None:
    project = _project()
    project[field] = invalid

    with pytest.raises(StoryMigrationSnapshotError) as captured:
        build_story_migration_snapshot([project])

    assert captured.value.code == "story_snapshot_invalid"
    assert str(captured.value) == captured.value.code


@pytest.mark.parametrize("invalid", [{}, "bad\ud800", "x" * 241])
def test_invalid_review_provider_id_is_rejected_before_stripping(invalid) -> None:
    project = _project()
    project["quality_reviews"][0]["provider_id"] = invalid

    with pytest.raises(StoryMigrationSnapshotError) as captured:
        build_story_migration_snapshot([project])

    assert captured.value.code in {"story_snapshot_invalid", "story_snapshot_too_large"}


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda projects: projects.append(_project("overflow")), "story_snapshot_too_large"),
        (lambda projects: projects[0].update(owner_id="other-owner"), "story_snapshot_owner_conflict"),
        (lambda projects: projects[0].update(owner_id=" padded "), "story_snapshot_invalid"),
        (lambda projects: projects[0].update(owner_id=[]), "story_snapshot_invalid"),
        (lambda projects: projects[0].update(unknown_business_field="value"), "story_snapshot_unknown_field"),
        (lambda projects: projects[0].update(id=" padded "), "story_snapshot_invalid"),
        (lambda projects: projects[0].update(id="bad\x00id"), "story_snapshot_invalid"),
        (lambda projects: projects[0].update(id="bad\nidentifier"), "story_snapshot_invalid"),
        (lambda projects: projects[0].update(title="bad\ud800"), "story_snapshot_invalid"),
        (lambda projects: projects[0].update(created_at=float("nan")), "story_snapshot_invalid"),
        (
            lambda projects: projects[0].update(created_at=10**10_000),
            "story_snapshot_too_large",
        ),
        (lambda projects: projects[0].update(current_chars=True), "story_snapshot_invalid"),
        (lambda projects: projects[0].update(draft_chunks={}), "story_snapshot_invalid"),
        (
            lambda projects: projects[0]["draft_chunks"][0].update(path="/private/chunk"),
            "story_snapshot_unknown_field",
        ),
        (
            lambda projects: projects[0]["characters"][0].update(token="secret"),
            "story_snapshot_unknown_field",
        ),
        (
            lambda projects: projects[0]["quality_reviews"][0]["scores"].update(extra=7),
            "story_snapshot_unknown_field",
        ),
    ],
)
def test_hostile_values_fail_closed_without_echoing_payload(mutation, code: str) -> None:
    projects = [_project(f"work-{index}") for index in range(MAX_PROJECTS)]
    mutation(projects)
    secret = "DO-NOT-ECHO-SECRET"
    projects[0]["cover_generation_error"] = secret

    with pytest.raises(StoryMigrationSnapshotError) as captured:
        build_story_migration_snapshot(projects)

    assert captured.value.code == code
    assert str(captured.value) == code
    assert secret not in str(captured.value)


def test_duplicate_project_ids_are_rejected() -> None:
    projects = [_project("same"), _project("same")]
    with pytest.raises(StoryMigrationSnapshotError) as captured:
        build_story_migration_snapshot(projects)
    assert captured.value.code == "story_snapshot_project_id_ambiguous"


@pytest.mark.parametrize(
    ("field", "maximum", "factory"),
    [
        ("draft_chunks", MAX_CHUNKS, lambda index: {"text": f"chunk-{index}"}),
        (
            "creative_memory_pool",
            MAX_MEMORY_ENTRIES,
            lambda index: {"id": f"memory-{index}", "content": "memory"},
        ),
        (
            "manual_edits",
            MAX_MANUAL_EDITS,
            lambda index: {"id": f"edit-{index}", "content": "edit"},
        ),
        (
            "quality_reviews",
            MAX_QUALITY_REVIEWS,
            lambda index: {"id": f"review-{index}", "passed": True},
        ),
    ],
)
def test_each_published_collection_limit_rejects_plus_one(field, maximum, factory) -> None:
    project = _project()
    project[field] = [factory(index) for index in range(maximum + 1)]
    with pytest.raises(StoryMigrationSnapshotError) as captured:
        build_story_migration_snapshot([project])
    assert captured.value.code == "story_snapshot_too_large"


def test_project_limit_accepts_exact_boundary_and_rejects_plus_one() -> None:
    exact = [_project(f"work-{index:02d}") for index in range(MAX_PROJECTS)]
    assert len(build_story_migration_snapshot(exact)["projects"]) == MAX_PROJECTS

    with pytest.raises(StoryMigrationSnapshotError) as captured:
        build_story_migration_snapshot(exact + [_project("overflow")])
    assert captured.value.code == "story_snapshot_too_large"


def test_reordering_changes_hash_because_source_order_is_preserved() -> None:
    first = build_story_migration_snapshot([_project("a"), _project("b")])
    second = build_story_migration_snapshot([_project("b"), _project("a")])
    assert first["snapshot_sha256"] != second["snapshot_sha256"]
    assert [item["id"] for item in first["projects"]] == ["a", "b"]
    assert [item["id"] for item in second["projects"]] == ["a", "b"]
    assert [item["source_order"] for item in first["projects"]] == [0, 1]
    assert [item["source_order"] for item in second["projects"]] == [1, 0]


def test_canonical_projection_can_be_revalidated_without_changing_identity() -> None:
    snapshot = build_story_migration_snapshot([_project("b"), _project("a")])

    rebuilt = build_story_migration_snapshot(snapshot["projects"])

    assert rebuilt == snapshot


def test_wire_source_order_must_be_an_exact_permutation() -> None:
    projects = [_project("a"), _project("b")]
    projects[0]["source_order"] = 0
    projects[1]["source_order"] = 0

    with pytest.raises(StoryMigrationSnapshotError) as captured:
        build_story_migration_snapshot(projects)

    assert captured.value.code == "story_snapshot_source_order_ambiguous"


def test_final_envelope_not_only_hashed_payload_obeys_byte_wall(monkeypatch) -> None:
    project = _project()
    baseline = build_story_migration_snapshot([project])
    canonical_size = len(canonical_story_snapshot_payload(baseline))
    envelope_size = len(
        json.dumps(
            baseline,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    assert envelope_size > canonical_size

    monkeypatch.setattr(story_contract, "MAX_SNAPSHOT_BYTES", canonical_size)
    with pytest.raises(StoryMigrationSnapshotError) as captured:
        build_story_migration_snapshot([project])
    assert captured.value.code == "story_snapshot_too_large"
    assert str(captured.value) == captured.value.code
