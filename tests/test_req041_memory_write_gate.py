from __future__ import annotations

import ast
import copy
from pathlib import Path
import types
import unittest
from typing import Any

from authoritative_private_memory import (
    AuthoritativePrivateMemoryError,
    AuthoritativePrivateMemoryStore,
    apply_private_memory_content,
    private_memory_content,
)
from unified_person_registry import UnifiedPersonRegistry
from tests.module_source_index import user_memory_mixin_tree, user_memory_source_text, file_family_source_text


ROOT = Path(__file__).resolve().parents[1]


def _load_methods(*names: str) -> dict[str, Any]:
    tree = user_memory_mixin_tree(ROOT)
    owner = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "UserMemoryMixin"
    )
    methods = [
        copy.deepcopy(node) for node in owner.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    for method in methods:
        method.decorator_list = []
    module = ast.Module(body=methods, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace: dict[str, Any] = {
        "Any": Any,
        "AuthoritativePrivateMemoryError": AuthoritativePrivateMemoryError,
        "AuthoritativePrivateMemoryStore": AuthoritativePrivateMemoryStore,
        "apply_private_memory_content": apply_private_memory_content,
        "private_memory_content": private_memory_content,
        "_single_line": lambda value, limit=240: " ".join(str(value or "").split())[:limit],
        "logger": types.SimpleNamespace(warning=lambda *_args, **_kwargs: None),
    }
    exec(compile(module, str(ROOT / "user_memory.py"), "exec"), namespace)
    return {name: namespace[name] for name in names}


METHODS = _load_methods(
    "_req041_private_memory_write_allowed",
    "_req041_private_memory_managed",
    "_req041_private_memory_unique_legacy_source",
    "_req041_prepare_authoritative_private_memory",
    "_req041_commit_authoritative_private_memory",
)


class _Host:
    _req041_private_memory_write_allowed = METHODS["_req041_private_memory_write_allowed"]
    _req041_private_memory_managed = METHODS["_req041_private_memory_managed"]
    _req041_private_memory_unique_legacy_source = METHODS["_req041_private_memory_unique_legacy_source"]
    _req041_prepare_authoritative_private_memory = METHODS["_req041_prepare_authoritative_private_memory"]
    _req041_commit_authoritative_private_memory = METHODS["_req041_commit_authoritative_private_memory"]


class ScopedMemoryWriteGateTests(unittest.TestCase):
    def test_unmanaged_legacy_install_remains_compatible(self) -> None:
        host = _Host()
        host.req041_scoped_projection_sync = None
        host.req041_migration_status = {"required": False}
        self.assertTrue(host._req041_private_memory_write_allowed({"user_id": "u1"}))
        self.assertFalse(host._req041_private_memory_managed())

    def test_fresh_or_migrating_runtime_without_memory_bridge_fails_closed(self) -> None:
        for status in (
            {"required": True},
            {"required": False, "scoped_required": True},
        ):
            with self.subTest(status=status):
                host = _Host()
                host.req041_scoped_projection_sync = None
                host.req041_migration_status = status
                self.assertFalse(host._req041_private_memory_write_allowed({"user_id": "u1"}))
                self.assertTrue(host._req041_private_memory_managed())

    def test_scoped_runtime_requires_a_formal_private_namespace(self) -> None:
        host = _Host()
        host.req041_scoped_projection_sync = object()
        host.req041_migration_status = {"scoped_required": True}
        calls: list[tuple[str, str]] = []

        def resolver(_user, *, kind, purpose):
            calls.append((kind, purpose))
            return object() if _user.get("formal") else None

        host._req041_scoped_context_for_user = resolver
        self.assertFalse(host._req041_private_memory_write_allowed({"formal": False}))
        self.assertTrue(host._req041_private_memory_write_allowed({"formal": True}))
        self.assertEqual([("private", "memory_write"), ("private", "memory_write")], calls)

    def test_missing_or_faulting_resolver_fails_closed(self) -> None:
        host = _Host()
        host.req041_scoped_projection_sync = object()
        host.req041_migration_status = {"scoped_required": True}
        self.assertFalse(host._req041_private_memory_write_allowed({"formal": True}))
        host._req041_scoped_context_for_user = lambda *_args, **_kwargs: 1 / 0
        self.assertFalse(host._req041_private_memory_write_allowed({"formal": True}))

    def test_private_pipeline_guards_all_durable_memory_mutations(self) -> None:
        source = file_family_source_text(ROOT, "message_pipeline.py")
        gate = source.index("private_memory_write_allowed = self._req041_private_memory_write_allowed(user)")
        guarded = source.index("if private_memory_write_allowed:", gate)
        for call in (
            "self._update_companion_memory_from_message",
            "self._update_open_loops_from_message",
            "self._update_action_preferences_from_message",
            "self._update_user_behavior_habits_from_message",
        ):
            self.assertGreater(source.index(call, guarded), guarded)
        self.assertIn("_req041_prepare_authoritative_private_memory(user)", source)
        self.assertIn("_req041_commit_authoritative_private_memory(", source)

        page_source = file_family_source_text(ROOT, "page_api_users_groups.py")
        self.assertIn('"_req041_prepare_authoritative_private_memory"', page_source)
        self.assertIn('"_req041_commit_authoritative_private_memory"', page_source)
        command_source = file_family_source_text(ROOT, "main.py")
        command_branch = command_source.index('elif action in {"话头删除"')
        self.assertGreater(
            command_source.index("_req041_prepare_authoritative_private_memory", command_branch),
            command_branch,
        )
        self.assertGreater(
            command_source.index("_req041_commit_authoritative_private_memory", command_branch),
            command_branch,
        )
        self.assertGreater(
            command_source.index(
                'save_sections.add("_req041_private_memory")', command_branch
            ),
            command_branch,
        )

    def test_two_explicit_identities_converge_on_one_authoritative_private_memory(self) -> None:
        data: dict = {}
        registry = UnifiedPersonRegistry(data)
        primary = registry.create_or_link(
            {
                "companion_instance_id": "astrbot_plugin_private_companion",
                "bot_account_id": "onebot:bot-1",
                "adapter_instance_id": "onebot:default",
                "subject_namespace": "onebot:user",
                "platform_subject_id": "10001",
            },
            operation_id="create-primary",
        )
        linked = registry.link_identity(
            primary["person_id"],
            {
                "companion_instance_id": "astrbot_plugin_private_companion",
                "bot_account_id": "onebot:bot-1",
                "adapter_instance_id": "onebot:second",
                "subject_namespace": "onebot:user",
                "platform_subject_id": "20002",
            },
            operation_id="link-secondary",
        )
        self.assertTrue(linked["ok"])
        data["users"] = {
            "10001": {
                "user_id": "10001", "identity_subject_id": "10001",
                "unified_person_id": primary["person_id"],
                "companion_memory": {"items": [{"text": "ambiguous-a"}]},
            },
            "20002": {
                "user_id": "20002", "identity_subject_id": "20002",
                "unified_person_id": primary["person_id"],
                "companion_memory": {"items": [{"text": "ambiguous-b"}]},
            },
        }
        host = _Host()
        host.data = data
        host.req041_scoped_projection_sync = object()
        host.req041_migration_status = {"scoped_required": True}
        host._active_unified_person_registry = lambda: UnifiedPersonRegistry(data)
        host._req041_scoped_context_for_user = lambda *_args, **_kwargs: object()

        first_user = data["users"]["10001"]
        first_revision = host._req041_prepare_authoritative_private_memory(first_user)
        self.assertEqual(1, first_revision)
        self.assertNotIn("companion_memory", first_user)
        first_user["companion_memory"] = {"items": [{"text": "canonical"}]}
        self.assertTrue(host._req041_commit_authoritative_private_memory(
            first_user,
            expected_revision=first_revision,
            operation_id="first-formal-message",
        ))

        second_user = data["users"]["20002"]
        second_revision = host._req041_prepare_authoritative_private_memory(second_user)
        self.assertEqual(2, second_revision)
        self.assertEqual("canonical", second_user["companion_memory"]["items"][0]["text"])
        second_user["companion_memory"]["items"][0]["text"] = "stale-overwrite"
        self.assertFalse(host._req041_commit_authoritative_private_memory(
            second_user,
            expected_revision=1,
            operation_id="stale-second-message",
        ))
        self.assertEqual("canonical", second_user["companion_memory"]["items"][0]["text"])


if __name__ == "__main__":
    unittest.main()
