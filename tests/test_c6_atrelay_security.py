from __future__ import annotations

import ast
from pathlib import Path
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]


class _Logger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None


def _single_line(value, limit=240):
    text = " ".join(str(value or "").split())
    return text[:limit]


def _load_security_helpers():
    source = (ROOT / "atrelay.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {
        "_normalize_atrelay_group_target_id",
        "_atrelay_tool_authorization",
        "_atrelay_known_group_ids",
        "_atrelay_target_group_allowed",
    }
    nodes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AtRelayMixin"]
    methods = [node for node in nodes[0].body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    module = ast.Module(
        body=[ast.parse("from __future__ import annotations").body[0], *methods],
        type_ignores=[],
    )
    namespace = {"_single_line": _single_line, "logger": _Logger()}
    exec(compile(ast.fix_missing_locations(module), str(ROOT / "atrelay.py"), "exec"), namespace)
    return namespace


class _Harness:
    def __init__(
        self,
        *,
        owner=True,
        whitelist=None,
        blacklist=None,
        data=None,
        current_group="",
        group_aliases=None,
    ):
        self._owner = owner
        self._whitelist = set(whitelist or ())
        self._blacklist = set(blacklist or ())
        self.data = data or {}
        self.current_group = current_group
        self.group_aliases = dict(group_aliases or {})

    def _permission_identity_id(self, value):
        return str(value or "")

    def _is_private_companion_owner_user_id(self, value):
        return self._owner and value == "owner"

    def _configured_group_ids(self):
        return self._whitelist

    def _configured_group_blacklist_ids(self):
        return self._blacklist

    def _extract_group_id_from_event(self, event):
        return self.current_group

    def _normalize_group_identity_id(self, value):
        if isinstance(value, (dict, list, tuple, set)):
            return ""
        text = _single_line(value, 512)
        marker = ":groupmessage:"
        marker_offset = text.lower().find(marker)
        if marker_offset >= 0:
            text = _single_line(text[marker_offset + len(marker):], 160)
            if ":" in text:
                return ""
        return self.group_aliases.get(text, text)


class AtRelaySecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helpers = _load_security_helpers()
        cls.source = (ROOT / "atrelay.py").read_text(encoding="utf-8") + (ROOT / "llm_tool_actions.py").read_text(encoding="utf-8")

    def _bind(self, harness, name):
        method = self.helpers[name]
        return types.MethodType(method, harness)

    def _bind_known_groups(self, harness):
        harness._normalize_atrelay_group_target_id = self._bind(
            harness,
            "_normalize_atrelay_group_target_id",
        )
        harness._atrelay_known_group_ids = self._bind(
            harness,
            "_atrelay_known_group_ids",
        )
        return harness._atrelay_known_group_ids

    def test_non_owner_is_rejected_and_owner_is_allowed(self):
        event = types.SimpleNamespace(unified_msg_origin="aiocqhttp:FriendMessage:owner")
        event.get_sender_id = lambda: "guest"
        denied, _ = self._bind(_Harness(owner=False), "_atrelay_tool_authorization")(event)
        self.assertFalse(denied)
        event.get_sender_id = lambda: "owner"
        allowed, _ = self._bind(_Harness(owner=True), "_atrelay_tool_authorization")(event)
        self.assertTrue(allowed)

    def test_blacklist_takes_precedence_over_whitelist(self):
        harness = _Harness(whitelist={"100"}, blacklist={"100"})
        self._bind_known_groups(harness)
        result = self._bind(harness, "_atrelay_target_group_allowed")("100", None)
        self.assertTrue(result)
        self.assertIn("发送失败", result)

    def test_normalized_blacklist_takes_precedence_over_configured_group(self):
        group_umo = "official:GroupMessage:configured-openid"
        harness = _Harness(whitelist={group_umo}, blacklist={group_umo})
        self._bind_known_groups(harness)

        result = self._bind(harness, "_atrelay_target_group_allowed")(
            "configured-openid",
            None,
        )

        self.assertIn("发送失败", result)

    def test_unknown_group_is_rejected_when_allow_set_exists(self):
        harness = _Harness(whitelist={"100"})
        self._bind_known_groups(harness)
        result = self._bind(harness, "_atrelay_target_group_allowed")("999", None)
        self.assertIn("发送失败", result)

    def test_empty_group_set_keeps_owner_checked_compatibility_pass(self):
        harness = _Harness()
        self._bind_known_groups(harness)
        result = self._bind(harness, "_atrelay_target_group_allowed")("999", None)
        self.assertEqual(result, "")

    def test_known_and_current_groups_extend_allow_set(self):
        harness = _Harness(data={"groups": {"200": {"name": "observed"}}}, current_group="300")
        self._bind_known_groups(harness)
        checker = self._bind(harness, "_atrelay_target_group_allowed")
        self.assertEqual(checker("200", None), "")
        self.assertEqual(checker("300", types.SimpleNamespace()), "")

    def test_configured_group_is_known_when_store_is_empty(self):
        harness = _Harness(
            whitelist={"official:GroupMessage:configured-openid"},
        )
        resolver = self._bind_known_groups(harness)

        self.assertEqual({"configured-openid"}, resolver())

    def test_known_groups_normalize_aliases_and_group_origins_from_all_sources(self):
        harness = _Harness(
            whitelist={"configured-alias"},
            group_aliases={
                "configured-alias": "configured-id",
                "stored-alias": "stored-id",
                "worldbook-alias": "worldbook-id",
            },
            data={
                "groups": {
                    "stored-alias": {
                        "group_id": "official:GroupMessage:stored-origin",
                    }
                },
                "worldbook_group_profiles": {
                    "worldbook-alias": {
                        "group_id": "official:GroupMessage:worldbook-origin",
                    }
                },
            },
        )
        resolver = self._bind_known_groups(harness)

        self.assertEqual(
            {
                "configured-id",
                "stored-id",
                "stored-origin",
                "worldbook-id",
                "worldbook-origin",
            },
            resolver(),
        )

    def test_known_group_resolver_ignores_broken_config_and_dirty_records(self):
        harness = _Harness(
            data={
                "groups": [],
                "worldbook_group_profiles": {
                    "valid": None,
                    "invalid": {"group_id": ["not", "an", "id"]},
                },
            }
        )
        harness._configured_group_ids = lambda: (_ for _ in ()).throw(
            RuntimeError("broken config")
        )
        resolver = self._bind_known_groups(harness)

        self.assertEqual({"valid", "invalid"}, resolver())

    def test_known_group_resolver_has_single_definition(self):
        tree = ast.parse((ROOT / "atrelay.py").read_text(encoding="utf-8"))
        owner = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "AtRelayMixin"
        )

        self.assertEqual(
            1,
            sum(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "_atrelay_known_group_ids"
                for node in owner.body
            ),
        )

    def test_audit_log_contract_truncates_body_and_keeps_safe_metadata(self):
        tree = ast.parse(self.source)
        note_nodes = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_note_atrelay_send"
        ]
        self.assertEqual(len(note_nodes), 1)
        note_source = ast.get_source_segment(self.source, note_nodes[0]) or ""
        self.assertIn("_normalize_atrelay_text(text, limit=300)", note_source)
        append_dicts = [
            node.args[0]
            for node in ast.walk(note_nodes[0])
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and node.args
            and isinstance(node.args[0], ast.Dict)
        ]
        self.assertTrue(append_dicts)
        keys = {key.value for key in append_dicts[0].keys if isinstance(key, ast.Constant)}
        self.assertNotIn("text", keys)
        self.assertIn("signature", keys)

    def test_authorization_and_group_guard_precede_rewrite_or_send(self):
        tree = ast.parse(self.source)
        wanted = {
            "_pc_relay_message_impl",
            "_pc_send_to_group_impl",
            "_pc_send_to_private_user_impl",
            "_pc_schedule_group_relay_impl",
        }
        functions = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted
        }
        for name, node in functions.items():
            calls = []
            for child in ast.walk(node):
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                    calls.append((child.func.attr, child.lineno))
            auth_line = next(line for method, line in calls if method == "_atrelay_tool_authorization")
            first_sensitive_line = min(
                line
                for method, line in calls
                if method in {"_rewrite_atrelay_message_with_llm", "_send_atrelay_chain_to_target", "_resolve_atrelay_target_user"}
            )
            self.assertLess(auth_line, first_sensitive_line, name)
            if "group" in name or name == "_pc_relay_message_impl":
                guard_line = next(line for method, line in calls if method == "_atrelay_target_group_allowed")
                self.assertLess(guard_line, first_sensitive_line, name)


if __name__ == "__main__":
    unittest.main()
