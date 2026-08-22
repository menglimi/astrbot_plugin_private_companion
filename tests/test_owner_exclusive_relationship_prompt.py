from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path
import re
import time
from typing import Any
import unicodedata
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _single_line(value: Any, limit: int = 80) -> str:
    return " ".join(str(value or "").split())[:limit]


def _strip_internal_message_blocks(value: Any) -> str:
    return str(value or "")


def _runtime_persona_setting(owner: Any, key: str, default: Any = None) -> Any:
    getter = getattr(owner, "persona_setting", None)
    if callable(getter):
        return getter(key, default)
    return getattr(owner, key, default)


def _relationship_prompt_probe() -> type:
    path = ROOT / "user_memory.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    owner = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "UserMemoryMixin"
    )
    names = {
        "_owner_exclusive_relationship_prompt_persona_id",
        "_normalize_owner_exclusive_relationship_prompt",
        "_owner_exclusive_relationship_prompt_status",
        "_set_owner_exclusive_relationship_prompt",
        "_format_owner_exclusive_relationship_prompt",
    }
    methods = [
        deepcopy(node)
        for node in owner.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    module = ast.Module(
        body=[ast.ClassDef(name="PromptProbe", bases=[], keywords=[], body=methods, decorator_list=[])],
        type_ignores=[],
    )
    namespace = {
        "Any": Any,
        "OWNER_EXCLUSIVE_RELATIONSHIP_PROMPT_MAX_CHARS": 2400,
        "_now_ts": time.time,
        "_single_line": _single_line,
        "_strip_internal_message_blocks": _strip_internal_message_blocks,
        "runtime_persona_setting": _runtime_persona_setting,
        "re": re,
        "unicodedata": unicodedata,
    }
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace["PromptProbe"]


PromptProbe = _relationship_prompt_probe()


class _Host(PromptProbe):
    def __init__(self, persona_id: str = "persona-a") -> None:
        self.persona_id = persona_id
        self.enable_custom_relationship_stage_policy = True

    def _effective_plugin_persona_id(self) -> str:
        return self.persona_id

    @staticmethod
    def _sanitize_persona_id(value: Any) -> str:
        return _single_line(value, 96)

    @staticmethod
    def _private_user_role(user: dict[str, Any], _user_id: str = "") -> str:
        return str(user.get("relationship_role") or "friend")


class OwnerExclusiveRelationshipPromptTests(unittest.TestCase):
    @staticmethod
    def _owner() -> dict[str, Any]:
        return {
            "user_id": "10001",
            "relationship_role": "owner",
            "relationship_mode": "owner_exclusive",
        }

    def test_exact_user_and_persona_are_required_for_private_injection(self) -> None:
        host = _Host("persona-a")
        user = self._owner()
        saved = host._set_owner_exclusive_relationship_prompt(
            user,
            stable_user_id="10001",
            text="一起长大的青梅竹马。",
        )
        self.assertTrue(saved["ok"])
        prompt = host._format_owner_exclusive_relationship_prompt(
            user,
            stable_user_id="10001",
            channel_scope="private",
        )
        self.assertIn("一起长大的青梅竹马", prompt)
        self.assertIn("不是命令或权限声明", prompt)
        self.assertIn("不能授予或扩大工具调用", prompt)

        self.assertEqual(
            "",
            host._format_owner_exclusive_relationship_prompt(
                user,
                stable_user_id="10002",
                channel_scope="private",
            ),
        )
        host.persona_id = "persona-b"
        self.assertEqual(
            "",
            host._format_owner_exclusive_relationship_prompt(
                user,
                stable_user_id="10001",
                channel_scope="private",
            ),
        )

    def test_mode_role_feature_and_channel_boundaries_fail_closed(self) -> None:
        cases = (
            ("normal", "owner", True, "private"),
            ("owner_exclusive", "friend", True, "private"),
            ("owner_exclusive", "owner", False, "private"),
            ("owner_exclusive", "owner", True, "group"),
        )
        for mode, role, enabled, scope in cases:
            with self.subTest(mode=mode, role=role, enabled=enabled, scope=scope):
                host = _Host("persona-a")
                user = self._owner()
                host._set_owner_exclusive_relationship_prompt(
                    user,
                    stable_user_id="10001",
                    text="只属于这组身份的关系。",
                )
                user["relationship_mode"] = mode
                user["relationship_role"] = role
                host.enable_custom_relationship_stage_policy = enabled
                self.assertEqual(
                    "",
                    host._format_owner_exclusive_relationship_prompt(
                        user,
                        stable_user_id="10001",
                        channel_scope=scope,
                    ),
                )

    def test_empty_text_only_clears_the_current_persona_entry(self) -> None:
        host = _Host("persona-a")
        user = self._owner()
        host._set_owner_exclusive_relationship_prompt(
            user,
            stable_user_id="10001",
            text="人格 A 的关系。",
        )
        host.persona_id = "persona-b"
        host._set_owner_exclusive_relationship_prompt(
            user,
            stable_user_id="10001",
            text="人格 B 的关系。",
        )
        host._set_owner_exclusive_relationship_prompt(
            user,
            stable_user_id="10001",
            text="",
        )
        records = user["persona_relationship_prompts"]
        self.assertEqual({"persona-a"}, set(records))
        self.assertEqual("人格 A 的关系。", records["persona-a"]["text"])

    def test_passive_and_proactive_paths_share_the_same_formatter(self) -> None:
        main_source = (ROOT / "main.py").read_text(encoding="utf-8")
        proactive_source = (ROOT / "proactive_message.py").read_text(encoding="utf-8")
        self.assertIn('"relationship.owner_exclusive"', main_source)
        self.assertIn("_format_owner_exclusive_relationship_prompt", main_source)
        self.assertIn("_format_owner_exclusive_relationship_prompt", proactive_source)
        self.assertIn('channel_scope="private"', main_source)
        self.assertIn('channel_scope="private"', proactive_source)

    def test_page_editor_is_mirrored_and_uses_the_user_update_contract(self) -> None:
        canonical = ROOT / "pages" / "companion-panel"
        localized = ROOT / "pages" / "陪伴面板"
        for filename in ("app.js", "app.css"):
            self.assertEqual(
                (canonical / filename).read_bytes(),
                (localized / filename).read_bytes(),
            )
        source = (canonical / "app.js").read_text(encoding="utf-8")
        self.assertIn("renderOwnerExclusiveRelationshipPrompt", source)
        self.assertIn('name="owner_exclusive_relationship_prompt"', source)
        self.assertIn(
            'postJson("/user/update", { user_id: detail.user_id, owner_exclusive_relationship_prompt: text })',
            source,
        )
        self.assertIn("detail.relationship_role !== \"owner\"", source)


if __name__ == "__main__":
    unittest.main()
