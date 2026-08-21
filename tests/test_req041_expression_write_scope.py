from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import unittest

from expression_scope_ownership import bind_expression_item, bind_expression_profile
from persona_config import runtime_persona_setting


ROOT = Path(__file__).resolve().parents[1]


def _method(name: str, globals_map: dict):
    tree = ast.parse((ROOT / "user_memory.py").read_text(encoding="utf-8"))
    class_node = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "UserMemoryMixin")
    method = next(node for node in class_node.body if isinstance(node, ast.FunctionDef) and node.name == name)
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias("annotations")], level=0), method],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    namespace = dict(globals_map)
    exec(compile(module, str(ROOT / "user_memory.py"), "exec"), namespace)
    return namespace[name]


def _single_line(value, limit=100):
    return " ".join(str(value or "").split())[:limit]


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _safe_int(value, default=0, minimum=0, maximum=None):
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = int(default)
    result = max(minimum, result)
    return min(maximum, result) if maximum is not None else result


@dataclass(frozen=True)
class Context:
    kind: str
    persona_id: str = "persona-a"
    identity_id: str = ""
    group_id: str = ""
    assurance: str = "verified"
    profile_status: str = "active"
    policy_version: str = "req041-v1"
    migration_epoch: str = "epoch-a"

    def errors(self):
        return []

    def cache_scope(self):
        return f"{self.persona_id}:{self.kind}:{self.identity_id}:{self.group_id}:{self.migration_epoch}"


GLOBALS = {
    "Any": object,
    "deepcopy": deepcopy,
    "datetime": datetime,
    "_single_line": _single_line,
    "_strip_internal_message_blocks": lambda value: str(value or ""),
    "_safe_float": _safe_float,
    "_safe_int": _safe_int,
    "_now_ts": lambda: 1000.0,
    "bind_expression_item": bind_expression_item,
    "bind_expression_profile": bind_expression_profile,
    "runtime_persona_setting": runtime_persona_setting,
}


class Harness:
    enable_expression_learning = True
    max_learned_expression_items = 60
    expression_learning_mode = "balanced"

    def __init__(self, *, managed=True, private_context=None, group_contexts=None):
        self.req041_scoped_projection_sync = object() if managed else None
        self.private_context = private_context
        self.group_contexts = group_contexts or {}

    @staticmethod
    def _expression_sample_max_chars():
        return 120

    @staticmethod
    def _should_skip_expression_sample(_text):
        return False

    @staticmethod
    def _expression_manual_review_enabled():
        return False

    @staticmethod
    def _expression_sample_from_text(cleaned, now):
        return {"id": "sample-a", "text": cleaned, "ts": now, "length": len(cleaned)}

    @staticmethod
    def _refresh_expression_profile_legacy_summary(_profile):
        return None

    @staticmethod
    def _normalize_group_expression_profile(_profile, **_kwargs):
        return False

    def _req041_scoped_context_for_user(self, _owner, **_kwargs):
        return _owner.get("_context", self.private_context)

    def _req041_scoped_group_context(self, group_id, **_kwargs):
        return self.group_contexts.get(group_id)

    @staticmethod
    def _expression_visible_signals_in_reply(_response, _rule):
        return []

    @staticmethod
    def _classify_expression_rule_feedback(_text, **_kwargs):
        return "negative"

    @staticmethod
    def _refresh_expression_voice_profile():
        return None


for _name in (
    "_expression_formal_scope_for_owner",
    "_expression_bind_profile_scope",
    "_update_expression_profile_from_message",
    "_update_group_expression_profile_from_message",
    "_record_expression_rule_injection",
    "_apply_expression_rule_feedback",
):
    setattr(Harness, _name, _method(_name, GLOBALS))


class ExpressionWriteScopeTests(unittest.TestCase):
    def test_formal_private_write_is_bound_and_revisioned(self):
        context = Context(kind="private", identity_id="person-a")
        harness = Harness(private_context=context)
        owner = {"user_id": "raw-user", "expression_profile": {"samples": []}}
        harness._update_expression_profile_from_message(owner, "好呀")
        profile = owner["expression_profile"]
        binding = profile["samples"][0]["scope_binding"]
        self.assertEqual(1, profile["scope_revision"])
        self.assertEqual("approved", binding["approval_state"])
        self.assertEqual("automatic_policy", binding["approved_by"])
        self.assertNotIn("raw-user", str(binding))

    def test_managed_pending_identity_does_not_learn(self):
        harness = Harness(private_context=None)
        owner = {"user_id": "pending-user"}
        harness._update_expression_profile_from_message(owner, "好呀")
        self.assertNotIn("expression_profile", owner)

    def test_group_and_persona_namespaces_are_distinct(self):
        contexts = {
            "group-a": Context(kind="group_shared", group_id="group-a"),
            "group-b": Context(kind="group_shared", group_id="group-b"),
        }
        harness = Harness(group_contexts=contexts)
        groups = [
            {"group_id": "group-a", "expression_profile": {"samples": []}},
            {"group_id": "group-b", "expression_profile": {"samples": []}},
        ]
        for group in groups:
            harness._update_group_expression_profile_from_message(group, "好呀")
        namespaces = {
            group["expression_profile"]["samples"][0]["scope_binding"]["source_namespace"]
            for group in groups
        }
        self.assertEqual(2, len(namespaces))

        other_persona = Harness(private_context=Context(
            kind="private", identity_id="person-a", persona_id="persona-b",
        ))
        owner = {"expression_profile": {"samples": []}}
        other_persona._update_expression_profile_from_message(owner, "好呀")
        self.assertNotIn(
            owner["expression_profile"]["samples"][0]["scope_binding"]["source_namespace"],
            namespaces,
        )

    def test_unmanaged_legacy_instance_keeps_official_compatibility(self):
        harness = Harness(managed=False)
        owner = {"expression_profile": {"samples": []}}
        harness._update_expression_profile_from_message(owner, "好呀")
        self.assertEqual("sample-a", owner["expression_profile"]["samples"][0]["id"])
        self.assertNotIn("scope_ownership", owner["expression_profile"])

    def test_injection_feedback_cannot_update_a_different_private_namespace(self):
        context_a = Context(kind="private", identity_id="person-a")
        context_b = Context(kind="private", identity_id="person-b")
        harness = Harness(private_context=context_a)
        rule_b = bind_expression_item(
            {"id": "rule-b", "evidence_count": 2}, context_b,
            approval_state="approved", approved_by="administrator",
        )
        user_a = {
            "_context": context_a,
            "expression_profile": bind_expression_profile({"learned_rules": []}, context_a),
        }
        user_b = {
            "_context": context_b,
            "expression_profile": bind_expression_profile({"learned_rules": [rule_b]}, context_b),
        }
        harness.data = {"users": {"a": user_a, "b": user_b}, "groups": {}}
        selected = [{
            "id": "bundle-b", "evidence_count": 2,
            "source_refs": [{"source_kind": "private", "source_id": "b", "rule_id": "rule-b"}],
        }]
        harness._record_expression_rule_injection(
            user_a, {}, "reply", semantic_rules=selected, context={"channel": "private"},
        )
        self.assertNotIn("use_count", user_b["expression_profile"]["learned_rules"][0])
        self.assertEqual(1, user_b["expression_profile"]["scope_revision"])

    def test_injection_feedback_advances_current_rule_and_profile_revisions(self):
        context = Context(kind="private", identity_id="person-a")
        harness = Harness(private_context=context)
        rule = bind_expression_item(
            {"id": "rule-a", "evidence_count": 2}, context,
            approval_state="approved", approved_by="administrator",
        )
        user = {
            "_context": context,
            "expression_profile": bind_expression_profile({"learned_rules": [rule]}, context),
        }
        harness.data = {"users": {"a": user}, "groups": {}}
        selected = [{
            "id": "bundle-a", "evidence_count": 2,
            "source_refs": [{"source_kind": "private", "source_id": "a", "rule_id": "rule-a"}],
        }]
        harness._record_expression_rule_injection(
            user, {}, "reply", semantic_rules=selected, context={"channel": "private"},
        )
        stored = user["expression_profile"]["learned_rules"][0]
        self.assertEqual(1, stored["use_count"])
        self.assertEqual(2, stored["scope_binding"]["revision"])
        self.assertEqual(2, user["expression_profile"]["scope_revision"])

    def test_negative_feedback_demotes_rule_with_atomic_scope_transition(self):
        context = Context(kind="private", identity_id="person-a")
        harness = Harness(private_context=context)
        rule = bind_expression_item(
            {
                "id": "rule-a", "evidence_count": 2,
                "negative_feedback": 1, "positive_feedback": 0,
            },
            context, approval_state="approved", approved_by="administrator",
        )
        user = {
            "_context": context,
            "expression_profile": bind_expression_profile({
                "learned_rules": [rule],
                "pending_semantic_feedback": {
                    "ts": 1000.0, "channel": "private", "seen_after": 0,
                    "rules": [{
                        "source_refs": [{
                            "source_kind": "private", "source_id": "a", "rule_id": "rule-a",
                        }],
                    }],
                },
            }, context),
        }
        harness.data = {"users": {"a": user}, "groups": {}}
        result = harness._apply_expression_rule_feedback(user, "别这么说", channel="private")
        self.assertEqual(1, result["demoted_rules"])
        self.assertEqual([], user["expression_profile"]["learned_rules"])
        demoted = user["expression_profile"]["pending_rules"][0]
        self.assertEqual("pending", demoted["scope_binding"]["approval_state"])
        self.assertEqual(2, demoted["scope_binding"]["revision"])
        self.assertEqual(2, user["expression_profile"]["scope_revision"])


if __name__ == "__main__":
    unittest.main()
