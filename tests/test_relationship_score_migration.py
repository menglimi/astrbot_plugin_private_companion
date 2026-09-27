# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import copy
from pathlib import Path
from typing import Any

import pytest

from relationship_ledger import (
    RELATIONSHIP_SCORE_SCHEMA_VERSION,
    legacy_relationship_score_to_v2,
    migrate_legacy_relationship_score,
    migrate_relationship_score_schema,
)
from tests.module_source_index import class_body_defs_for_file


ROOT = Path(__file__).resolve().parents[1]
NOW = 1_800_000_000.0


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _single_line(value: Any, limit: int = 160) -> str:
    return " ".join(str(value or "").split())[:limit]


class _Logger:
    @staticmethod
    def info(*_args: Any, **_kwargs: Any) -> None:
        return None


def _class_method(method_name: str, namespace: dict[str, Any]) -> Any:
    # core_store.py 拆分后方法散落在 core_store_*.py 的域 mixin 里，改为按宿主族
    # 聚合扫描（module_source_index），断言语义不变。
    path = ROOT / "core_store.py"
    owner_body = class_body_defs_for_file(ROOT, "core_store.py", "CoreStoreMixin")
    method = next(
        node
        for node in owner_body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name
    )
    module = ast.Module(body=[copy.deepcopy(method)], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(path), "exec"), namespace)
    return namespace[method_name]


DEFAULT_USER = {
    "relationship_score": 0,
    "relationship_ledger": [],
    "relationship_last_effective_at": 0,
    "relationship_decay_settled_day": "",
    "nickname": "",
    "style": "",
    "enabled": True,
}

CANONICAL_USER_ID = _class_method("_canonical_private_user_id", {"Any": Any})
MERGE_USER_RECORDS = _class_method(
    "_merge_user_record_values",
    {
        "Any": Any,
        "deepcopy": copy.deepcopy,
        "_now_ts": lambda: NOW,
        "_safe_float": _safe_float,
        "_safe_int": _safe_int,
        "migrate_legacy_relationship_score": migrate_legacy_relationship_score,
    },
)
MERGE_ALIAS_RECORDS = _class_method(
    "_merge_private_user_alias_records",
    {
        "_DEFAULT_USER_TEMPLATE": DEFAULT_USER,
        "_now_ts": lambda: NOW,
        "_single_line": _single_line,
        "deepcopy": copy.deepcopy,
        "logger": _Logger(),
        "migrate_legacy_relationship_score": migrate_legacy_relationship_score,
    },
)


class _StoreHost:
    _canonical_private_user_id = CANONICAL_USER_ID
    _merge_user_record_values = MERGE_USER_RECORDS
    _merge_private_user_alias_records = MERGE_ALIAS_RECORDS

    @staticmethod
    def _normalize_private_identity_id(value: Any, limit: int = 128) -> str:
        text = _single_line(value, max(limit, 512))
        if ":FriendMessage:" in text:
            session_id = _single_line(text.rsplit(":FriendMessage:", 1)[-1], limit)
            return session_id if session_id and ":" not in session_id else ""
        return _single_line(text, limit) if ":" not in text else ""

    def __init__(self, users: dict[str, Any], aliases: dict[str, str] | None = None) -> None:
        self.data = {"users": copy.deepcopy(users)}
        self.private_user_aliases = dict(aliases or {})


@pytest.mark.parametrize(
    ("legacy", "expected"),
    [
        (0, 0),
        (3, 200),
        (16, 600),
        (55, 900),
        (120, 1200),
        (121, 1200),
        (-3, -30),
        (-16, -160),
        (-55, -550),
        (-120, -1200),
        (-500, -1200),
    ],
)
def test_legacy_score_anchor_mapping_preserves_existing_stage_scale(legacy: int, expected: int) -> None:
    assert legacy_relationship_score_to_v2(legacy) == expected


def test_piecewise_interpolation_is_monotonic_between_anchors() -> None:
    values = [legacy_relationship_score_to_v2(score) for score in range(-120, 121)]
    assert values == sorted(values)
    assert 200 < legacy_relationship_score_to_v2(10) < 600
    assert legacy_relationship_score_to_v2(-10) == -100


def test_existing_record_migration_is_zero_delta_and_idempotent() -> None:
    user = {
        "user_id": "legacy-user",
        "relationship_score": 55,
        "relationship_last_effective_at": 10.0,
        "relationship_ledger": [{"reason_code": "existing", "delta": 1}],
    }

    first = migrate_relationship_score_schema(user, now=NOW)
    snapshot = copy.deepcopy(user)
    second = migrate_relationship_score_schema(user, now=NOW + 100)

    assert first["changed"] is True
    assert first["delta"] == 0
    assert second["changed"] is False
    assert user == snapshot
    assert user["relationship_score"] == 900
    assert user["relationship_score_schema_version"] == RELATIONSHIP_SCORE_SCHEMA_VERSION
    assert user["relationship_score_migration"]["record_id"] == "legacy-user"
    audit = user["relationship_ledger"][-1]
    assert audit["reason_code"] == "relationship_score_schema_migration"
    assert audit["delta"] == 0
    assert audit["score_before"] == 55
    assert audit["score_after"] == 900


def test_current_schema_repairs_old_nonzero_migration_delta_once() -> None:
    user = {
        "relationship_score": 900,
        "relationship_score_schema_version": RELATIONSHIP_SCORE_SCHEMA_VERSION,
        "relationship_ledger": [
            {
                "reason_code": "relationship_score_schema_migration",
                "delta": 845,
                "score_before": 55,
                "score_after": 900,
            }
        ],
    }

    first = migrate_relationship_score_schema(user, now=NOW)
    second = migrate_relationship_score_schema(user, now=NOW + 1)

    assert first["changed"] is True
    assert first["code"] == "relationship_score_migration_audit_repaired"
    assert first["delta"] == 0
    assert user["relationship_ledger"][0]["delta"] == 0
    assert second["changed"] is False


def test_new_record_only_initializes_schema_without_translation_or_audit() -> None:
    user = {"relationship_score": 55, "relationship_ledger": []}

    result = migrate_relationship_score_schema(user, created=True, now=NOW, record_id="new-user")

    assert result["code"] == "relationship_score_schema_initialized"
    assert user["relationship_score"] == 55
    assert user["relationship_score_schema_version"] == RELATIONSHIP_SCORE_SCHEMA_VERSION
    assert user["relationship_ledger"] == []
    assert "relationship_score_migration" not in user
    assert "relationship_last_effective_at" not in user


def test_startup_migrates_every_record_even_without_alias_config() -> None:
    host = _StoreHost({"legacy": {"relationship_score": 120}})

    assert host._merge_private_user_alias_records() is True
    assert host.data["users"]["legacy"]["relationship_score"] == 1200
    assert host.data["users"]["legacy"]["relationship_ledger"][-1]["delta"] == 0
    assert host._merge_private_user_alias_records() is False


def test_alias_records_are_migrated_before_scores_are_added() -> None:
    host = _StoreHost(
        {
            "canonical": {"user_id": "canonical", "relationship_score": 3, "req041_relationship_source_revision": 5},
            "alias": {"user_id": "alias", "relationship_score": 16, "req041_relationship_source_revision": 3},
        },
        {"alias": "canonical"},
    )

    assert host._merge_private_user_alias_records() is True

    assert set(host.data["users"]) == {"canonical"}
    merged = host.data["users"]["canonical"]
    assert merged["relationship_score"] == 800
    assert merged["relationship_score_schema_version"] == RELATIONSHIP_SCORE_SCHEMA_VERSION
    assert merged["req041_relationship_source_revision"] == 5
    assert {item["record_id"] for item in merged["relationship_score_migration_history"]} == {
        "alias",
        "canonical",
    }
    audits = [
        item
        for item in merged["relationship_ledger"]
        if item.get("reason_code") == "relationship_score_schema_migration"
    ]
    assert {item["record_id"] for item in audits} == {"alias", "canonical"}
    assert all(item["delta"] == 0 for item in audits)
    assert host._merge_private_user_alias_records() is False
    assert len(merged["relationship_score_migration_history"]) == 2


def test_startup_folds_legacy_full_umo_user_key_into_stable_identity() -> None:
    full_umo = "default_official:FriendMessage:openid-owner"
    host = _StoreHost(
        {
            "openid-owner": {"user_id": "openid-owner", "relationship_score": 0},
            full_umo: {
                "user_id": full_umo,
                "relationship_score": 0,
                "umo": full_umo,
                "bound_delivery_umo": full_umo,
                "manual_enabled": True,
            },
        }
    )

    assert host._merge_private_user_alias_records() is True
    assert set(host.data["users"]) == {"openid-owner"}
    merged = host.data["users"]["openid-owner"]
    assert merged["user_id"] == "openid-owner"
    assert merged["umo"] == full_umo
    assert merged["bound_delivery_umo"] == full_umo
    assert merged["manual_enabled"] is True
    assert full_umo not in merged.get("alias_user_ids", [])
    assert host._merge_private_user_alias_records() is False


def test_removing_alias_mapping_restores_the_pre_merge_alias_record() -> None:
    host = _StoreHost(
        {
            "canonical": {"user_id": "canonical", "relationship_score": 3},
            "alias": {"user_id": "alias", "relationship_score": 16},
        },
        {"alias": "canonical"},
    )

    assert host._merge_private_user_alias_records() is True
    assert "alias" not in host.data["users"]
    assert host.data["private_user_alias_merge_backups"]["alias"]["source"]["relationship_score"] == 600

    host.private_user_aliases = {}
    assert host._merge_private_user_alias_records() is True
    assert host.data["users"]["alias"]["relationship_score"] == 600
    assert "alias" not in host.data["users"]["canonical"].get("alias_user_ids", [])
    assert host.data["private_user_alias_merge_backups"] == {}
    assert host._merge_private_user_alias_records() is False


def test_clearing_legacy_alias_mapping_recreates_a_split_identity() -> None:
    host = _StoreHost(
        {
            "canonical": {
                "user_id": "canonical",
                "alias_user_ids": ["old-alias"],
            },
        },
        {},
    )

    assert host._merge_private_user_alias_records() is True
    assert host.data["users"]["old-alias"]["user_id"] == "old-alias"
    assert host.data["users"]["canonical"]["alias_user_ids"] == []
