from __future__ import annotations

import ast
import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import json
from pathlib import Path
import time
import unittest
from types import SimpleNamespace

from expression_scope_ownership import (
    ExpressionScopeError,
    bind_expression_item,
    bind_expression_profile,
    validate_expression_scope_binding,
)


ROOT = Path(__file__).resolve().parents[1]

from tests.module_source_index import find_method


class _Request:
    payload = {}

    async def get_json(self, silent=True):
        del silent
        return deepcopy(self.payload)


class _AsyncLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


def _method(name: str, globals_map: dict):
    # 表达域方法已拆到 page_api_expression.py 等域 mixin，
    # 故在「宿主类 + 各域 mixin 类」里定位，保持原有执行语义。
    method = find_method(ROOT, "page_api", "PrivateCompanionPageApi", name)
    if method is None:
        raise KeyError(name)
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias("annotations")], level=0), method],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    namespace = dict(globals_map)
    exec(compile(module, str(ROOT / "page_api.py"), "exec"), namespace)
    return namespace[name]


@dataclass(frozen=True)
class Context:
    kind: str = "private"
    persona_id: str = "default"
    identity_id: str = "person-a"
    group_id: str = ""
    assurance: str = "verified"
    profile_status: str = "active"
    policy_version: str = "req041-v1"
    migration_epoch: str = "epoch-a"

    def errors(self):
        return []


class Harness:
    max_learned_expression_items = 60

    def __init__(self):
        self.plugin = self
        self.data = {}
        self.req041_scoped_projection_sync = object()
        self.resolved_context = Context()
        self._data_lock = _AsyncLock()
        self.saved = 0

    @staticmethod
    def _single_line(value, limit=100):
        return " ".join(str(value or "").split())[:limit]

    @staticmethod
    def _int(value):
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _expression_bind_profile_scope(self, profile, context, *, bump_revision):
        result = bind_expression_profile(profile, context, bump_revision=bump_revision)
        states = {
            "pending_rules": ("pending", ""),
            "learned_rules": ("approved", "legacy_migration"),
            "rejected_rules": ("rejected", "administrator"),
            "revoked_rules": ("revoked", "administrator"),
        }
        for key, (state, default_actor) in states.items():
            rows = []
            for item in result.get(key, []) if isinstance(result.get(key), list) else []:
                existing = item.get("scope_binding") if isinstance(item.get("scope_binding"), dict) else {}
                rows.append(bind_expression_item(
                    item, context, approval_state=state,
                    approved_by=str(existing.get("approved_by") or default_actor),
                ))
            result[key] = rows
        return result

    @staticmethod
    def _normalize_group_identity_id(value):
        return str(value or "").strip().lower()

    def _req041_scoped_context_for_user(self, _owner, **_kwargs):
        return self.resolved_context

    def _req041_scoped_group_context(self, _group_id, **_kwargs):
        return Context(kind="group_shared", identity_id="", group_id="group-a")

    def _req041_persona_global_context(self, **_kwargs):
        return Context(kind="persona_global", identity_id="", group_id="")

    @staticmethod
    def _expression_rule_definition_is_valid(rule):
        return bool(rule.get("kind") in {"style", "grammar"} and rule.get("situation") and rule.get("pattern") and rule.get("instruction"))

    def _save_data_sync(self, **_kwargs):
        self.saved += 1

    @staticmethod
    def _expression_library_summary(_snapshot):
        return {"rule_count": 0}

    @staticmethod
    def _ok(value):
        return {"ok": True, "data": value}

    @staticmethod
    def _error(message):
        return {"ok": False, "error": message}

    def _exception_error(self, message):
        return self._error(message)


GLOBALS = {
    "Any": object,
    "deepcopy": deepcopy,
    "datetime": datetime,
    "time": time,
    "hashlib": hashlib,
    "hmac": hmac,
    "json": json,
    "request": _Request(),
    "logger": SimpleNamespace(error=lambda *_args, **_kwargs: None),
    "ExpressionScopeError": ExpressionScopeError,
    "bind_expression_item": bind_expression_item,
    "bind_expression_profile": bind_expression_profile,
    "validate_expression_scope_binding": validate_expression_scope_binding,
}
for _name in (
    "_expression_admin_scope_context",
    "_expression_prepare_admin_profile",
    "_expression_validate_admin_revision",
    "_expression_item_content",
    "_expression_finalize_admin_profile",
    "_expression_share_value_list",
    "_expression_share_rule",
    "_expression_promotion_confirmation",
    "_expression_global_promotion_state",
    "_apply_expression_profile_action",
    "update_expression_library",
):
    setattr(Harness, _name, _method(_name, GLOBALS))


def _rule(rule_id: str, family: str, context: Context) -> dict:
    return bind_expression_item(
        {
            "id": rule_id, "family_id": family, "kind": "style",
            "situation": "问候", "pattern": "好呀____", "instruction": "自然改写",
            "evidence_count": 2,
        },
        context, approval_state="pending",
    )


class ExpressionAdminScopeTests(unittest.TestCase):
    def setUp(self):
        self.harness = Harness()
        self.context = Context()

    def owner(self):
        profile = bind_expression_profile({
            "pending_rules": [
                _rule("style-a", "family-a", self.context),
                bind_expression_item(
                    {
                        "id": "grammar-a", "family_id": "family-a", "kind": "grammar",
                        "situation": "问候", "pattern": "省略主语短句", "instruction": "使用短句",
                        "evidence_count": 2,
                    },
                    self.context, approval_state="pending",
                ),
            ],
        }, self.context)
        return {"expression_profile": profile}

    @staticmethod
    def revisions(owner):
        profile = owner["expression_profile"]
        return {
            "expected_scope_revision": profile["scope_revision"],
            "expected_item_revisions": {
                item["id"]: item["scope_binding"]["revision"]
                for item in profile["pending_rules"]
            },
        }

    def test_approval_binds_administrator_and_advances_both_revisions(self):
        owner = self.owner()
        payload = {**self.revisions(owner), "expression_action": "approve_rule_group", "rule_family_id": "family-a"}
        prepared = self.harness._expression_prepare_admin_profile(owner, self.context)
        self.harness._expression_validate_admin_revision(prepared, payload)
        before = deepcopy(prepared)
        message = self.harness._apply_expression_profile_action(owner, payload)
        self.harness._expression_finalize_admin_profile(owner, before, self.context)
        profile = owner["expression_profile"]
        self.assertIn("已通过规则组", message)
        self.assertEqual(2, profile["scope_revision"])
        self.assertEqual([], profile["pending_rules"])
        self.assertEqual({"style-a", "grammar-a"}, {item["id"] for item in profile["learned_rules"]})
        self.assertTrue(all(item["scope_binding"]["approval_state"] == "approved" for item in profile["learned_rules"]))
        self.assertTrue(all(item["scope_binding"]["approved_by"] == "administrator" for item in profile["learned_rules"]))
        self.assertTrue(all(item["scope_binding"]["revision"] == 2 for item in profile["learned_rules"]))

    def test_stale_profile_revision_is_rejected_without_mutation(self):
        owner = self.owner()
        payload = {**self.revisions(owner), "expression_action": "approve_rule_group", "rule_family_id": "family-a"}
        before = deepcopy(owner)
        owner["expression_profile"] = bind_expression_profile(
            owner["expression_profile"], self.context, bump_revision=True,
        )
        with self.assertRaisesRegex(ValueError, "已被其他操作更新"):
            self.harness._expression_validate_admin_revision(owner["expression_profile"], payload)
        self.assertEqual(before["expression_profile"]["pending_rules"], owner["expression_profile"]["pending_rules"])

    def test_stale_or_incomplete_item_revision_is_rejected(self):
        owner = self.owner()
        payload = {**self.revisions(owner), "rule_family_id": "family-a"}
        payload["expected_item_revisions"] = {"style-a": 1}
        with self.assertRaisesRegex(ValueError, "表达项已被其他操作更新"):
            self.harness._expression_validate_admin_revision(owner["expression_profile"], payload)

    def test_reject_and_delete_keep_bounded_audit_state(self):
        owner = self.owner()
        payload = {**self.revisions(owner), "expression_action": "reject_rule_group", "rule_family_id": "family-a"}
        before = deepcopy(owner["expression_profile"])
        self.harness._apply_expression_profile_action(owner, payload)
        self.harness._expression_finalize_admin_profile(owner, before, self.context)
        rejected = owner["expression_profile"]["rejected_rules"]
        self.assertEqual(2, len(rejected))
        self.assertTrue(all(item["scope_binding"]["approval_state"] == "rejected" for item in rejected))
        self.assertTrue(all(item["scope_binding"]["revision"] == 2 for item in rejected))

        approved = bind_expression_item(
            {**_rule("enabled", "enabled-family", self.context), "review_status": "approved"},
            self.context, approval_state="approved", approved_by="administrator", bump_revision=True,
        )
        profile = owner["expression_profile"]
        profile["learned_rules"] = [approved]
        before = deepcopy(profile)
        self.harness._apply_expression_profile_action(owner, {
            "expression_action": "delete_rule_group", "rule_family_id": "enabled-family",
        })
        self.harness._expression_finalize_admin_profile(owner, before, self.context)
        revoked = owner["expression_profile"]["revoked_rules"]
        self.assertEqual("revoked", revoked[0]["scope_binding"]["approval_state"])
        self.assertEqual(3, revoked[0]["scope_binding"]["revision"])

    def test_cross_namespace_preparation_fails_closed(self):
        owner = self.owner()
        with self.assertRaisesRegex(ValueError, "作用域校验失败"):
            self.harness._expression_prepare_admin_profile(
                owner,
                Context(identity_id="person-b"),
            )

    def test_raw_group_locator_cannot_override_container_identity(self):
        with self.assertRaisesRegex(ValueError, "群来源标识"):
            self.harness._expression_admin_scope_context(
                "group", "group-a", {"group_id": "group-b"},
            )

    def test_unresolved_private_admin_target_fails_closed(self):
        self.harness.resolved_context = None
        with self.assertRaisesRegex(ValueError, "没有可写的正式身份作用域"):
            self.harness._expression_admin_scope_context(
                "private", "raw-user", {"user_id": "raw-user"},
            )

    def test_explicit_global_promotion_rebinds_sanitized_rules_to_persona(self):
        owner = self.owner()
        pending = owner["expression_profile"].pop("pending_rules")
        learned = []
        for item in pending:
            learned.append(bind_expression_item(
                item, self.context, approval_state="approved",
                approved_by="administrator", bump_revision=True,
            ))
        owner["expression_profile"]["learned_rules"] = learned
        self.harness.data = {"users": {"user-a": owner}}
        revisions = {
            "expected_scope_revision": owner["expression_profile"]["scope_revision"],
            "expected_item_revisions": {
                item["id"]: item["scope_binding"]["revision"] for item in learned
            },
        }
        state = self.harness._expression_global_promotion_state(
            source_type="private", source_id="user-a", family_id="family-a",
            operation_id="promote-a", payload=revisions,
        )
        self.assertEqual(2, len(state["rules"]))
        self.assertTrue(all(
            item["scope_binding"]["owner_type"] == "persona"
            and item["scope_binding"]["approved_by"] == "administrator"
            for item in state["rules"]
        ))
        encoded = json.dumps(state["rules"], ensure_ascii=False)
        self.assertNotIn("person-a", encoded)
        self.assertNotIn("user-a", encoded)
        self.assertNotIn("evidence_examples", encoded)

    def test_promotion_preview_changes_when_target_revision_changes(self):
        owner = self.owner()
        pending = owner["expression_profile"].pop("pending_rules")
        learned = [
            bind_expression_item(
                item, self.context, approval_state="approved",
                approved_by="administrator", bump_revision=True,
            ) for item in pending
        ]
        owner["expression_profile"]["learned_rules"] = learned
        self.harness.data = {"users": {"user-a": owner}}
        revisions = {
            "expected_scope_revision": owner["expression_profile"]["scope_revision"],
            "expected_item_revisions": {
                item["id"]: item["scope_binding"]["revision"] for item in learned
            },
        }
        first = self.harness._expression_global_promotion_state(
            source_type="private", source_id="user-a", family_id="family-a",
            operation_id="promote-a", payload=revisions,
        )
        global_context = self.harness._req041_persona_global_context()
        self.harness.data["_req041_persona_expression_profile"] = bind_expression_profile(
            first["target_profile"], global_context, bump_revision=True,
        )
        second = self.harness._expression_global_promotion_state(
            source_type="private", source_id="user-a", family_id="family-a",
            operation_id="promote-a", payload=revisions,
        )
        self.assertNotEqual(first["confirmation_token"], second["confirmation_token"])

    def test_promotion_endpoint_requires_preview_then_persists_global_profile(self):
        owner = self.owner()
        pending = owner["expression_profile"].pop("pending_rules")
        learned = [
            bind_expression_item(
                item, self.context, approval_state="approved",
                approved_by="administrator", bump_revision=True,
            ) for item in pending
        ]
        owner["expression_profile"]["learned_rules"] = learned
        self.harness.data = {"users": {"user-a": owner}}
        base = {
            "source_type": "private",
            "source_id": "user-a",
            "expression_action": "promote_rule_group",
            "rule_family_id": "family-a",
            "operation_id": "promote-endpoint-a",
            "expected_scope_revision": owner["expression_profile"]["scope_revision"],
            "expected_item_revisions": {
                item["id"]: item["scope_binding"]["revision"] for item in learned
            },
        }
        GLOBALS["request"].payload = {**base, "dry_run": True}
        preview = asyncio.run(self.harness.update_expression_library())
        self.assertTrue(preview["ok"])
        promotion = preview["data"]["promotion"]
        self.assertEqual("persona_global_promotion_preview", promotion["code"])
        self.assertNotIn("_req041_persona_expression_profile", self.harness.data)

        GLOBALS["request"].payload = {
            **base, "dry_run": False,
            "confirmation_token": promotion["confirmation_token"],
        }
        applied = asyncio.run(self.harness.update_expression_library())
        self.assertTrue(applied["ok"])
        self.assertEqual("persona_global_promoted", applied["data"]["promotion"]["code"])
        profile = self.harness.data["_req041_persona_expression_profile"]
        self.assertEqual(2, len(profile["learned_rules"]))
        self.assertEqual(1, self.harness.saved)
        replayed = asyncio.run(self.harness.update_expression_library())
        self.assertTrue(replayed["ok"])
        self.assertEqual(
            "persona_global_promotion_replayed",
            replayed["data"]["promotion"]["code"],
        )
        self.assertEqual(1, self.harness.saved)

        family_id = profile["learned_rules"][0]["family_id"]
        GLOBALS["request"].payload = {
            "source_type": "persona", "source_id": "current-persona",
            "expression_action": "delete_rule_group", "rule_family_id": family_id,
            "expected_scope_revision": profile["scope_revision"],
            "expected_item_revisions": {
                item["id"]: item["scope_binding"]["revision"]
                for item in profile["learned_rules"]
            },
        }
        revoked = asyncio.run(self.harness.update_expression_library())
        self.assertTrue(revoked["ok"])
        global_profile = self.harness.data["_req041_persona_expression_profile"]
        self.assertEqual([], global_profile["learned_rules"])
        self.assertEqual(2, len(global_profile["revoked_rules"]))
        self.assertEqual(2, self.harness.saved)

    def test_promotion_endpoint_rejects_forged_confirmation_without_write(self):
        owner = self.owner()
        pending = owner["expression_profile"].pop("pending_rules")
        learned = [
            bind_expression_item(
                item, self.context, approval_state="approved",
                approved_by="administrator", bump_revision=True,
            ) for item in pending
        ]
        owner["expression_profile"]["learned_rules"] = learned
        self.harness.data = {"users": {"user-a": owner}}
        GLOBALS["request"].payload = {
            "source_type": "private", "source_id": "user-a",
            "expression_action": "promote_rule_group", "rule_family_id": "family-a",
            "operation_id": "forged-promotion", "dry_run": False,
            "confirmation_token": "0" * 64,
            "expected_scope_revision": owner["expression_profile"]["scope_revision"],
            "expected_item_revisions": {
                item["id"]: item["scope_binding"]["revision"] for item in learned
            },
        }
        rejected = asyncio.run(self.harness.update_expression_library())
        self.assertFalse(rejected["ok"])
        self.assertNotIn("_req041_persona_expression_profile", self.harness.data)
        self.assertEqual(0, self.harness.saved)


if __name__ == "__main__":
    unittest.main()
