from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path
from types import SimpleNamespace

from astrbot_plugin_private_companion.conversation_injection_plan import (
    PLACEMENT_DYNAMIC_SYSTEM,
    get_conversation_injection_plan,
)
from astrbot_plugin_private_companion.conversation_prompt_section import prompt_section
from astrbot_plugin_private_companion.private_scope_isolation import (
    GROUP_SCOPE_MARKERS,
    sanitize_private_request_group_artifacts,
)


class _Part:
    def __init__(self, text: str):
        self.text = text


class PrivateGroupPromptIsolationTests(unittest.TestCase):
    def test_marker_allowlist_covers_every_owned_group_prompt_block(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = "\n".join(
            (root / name).read_text(encoding="utf-8")
            for name in (
                "main.py",
                "group_observation.py",
                "group_member_safety.py",
                "passive_state_pipeline.py",
                "private_image.py",
            )
        )
        discovered = set(
            re.findall(
                r"<!--\s*(private_companion_(?:[a-z0-9_]*group[a-z0-9_]*|member_safety_hidden_marker)_v1)\s*-->",
                source,
            )
        )

        self.assertTrue(discovered)
        self.assertLessEqual(discovered, set(GROUP_SCOPE_MARKERS))

    def test_private_scope_guard_runs_immediately_before_plan_finalize(self) -> None:
        # 方法已随域拆分分散在 main.py 与 main_*.py，跨宿主族聚合采集。
        from tests.module_source_index import iter_class_methods

        priorities = {}
        for method, _owner in iter_class_methods(
            Path(__file__).resolve().parents[1], "main", "PrivateCompanionPlugin"
        ):
            for decorator in method.decorator_list:
                if (
                    not isinstance(decorator, ast.Call)
                    or not isinstance(decorator.func, ast.Attribute)
                    or decorator.func.attr != "on_llm_request"
                ):
                    continue
                value = next(
                    (
                        keyword.value
                        for keyword in decorator.keywords
                        if keyword.arg == "priority"
                    ),
                    ast.Constant(value=0),
                )
                priorities[method.name] = (
                    -value.operand.value
                    if isinstance(value, ast.UnaryOp)
                    else value.value
                )
        self.assertEqual(
            -259000, priorities["enforce_private_request_scope_isolation"]
        )
        self.assertGreater(
            priorities["enforce_private_request_scope_isolation"],
            priorities["finalize_conversation_injection_plan"],
        )

    def test_private_request_removes_group_blocks_from_every_request_surface(self) -> None:
        group = (
            "<!-- private_companion_group_context_v1 -->\n"
            "群标记 G29-不应进入私聊"
        )
        group_image = (
            "<!-- private_companion_group_image_vision_v1 -->\n"
            "群图片摘要 IMG-G29"
        )
        safe = "<!-- private_companion_reply_style_v1 -->\n私聊风格"
        request = SimpleNamespace(
            system_prompt=f"基础人格\n{group}\n{safe}",
            prompt=f"当前私聊\n{group_image}",
            contexts=[
                {"role": "system", "content": group},
                {"role": "user", "content": "历史私聊"},
            ],
            extra_user_content_parts=[_Part(group), _Part("普通附件说明")],
            _private_companion_turn_prompt_fragments=[
                {
                    "marker": "<!-- private_companion_group_context_v1 -->",
                    "source": "group",
                },
                {
                    "marker": "<!-- private_companion_reply_style_v1 -->",
                    "source": "style",
                },
            ],
        )
        event = SimpleNamespace(
            unified_msg_origin="default:FriendMessage:user-1",
            is_private_chat=lambda: True,
        )

        changed = sanitize_private_request_group_artifacts(event, request)

        surface = "\n".join(
            [request.system_prompt, request.prompt]
            + [str(item.get("content") or "") for item in request.contexts]
            + [part.text for part in request.extra_user_content_parts]
        )
        self.assertNotIn("G29-不应进入私聊", surface)
        self.assertNotIn("IMG-G29", surface)
        self.assertIn("私聊风格", request.system_prompt)
        self.assertIn("历史私聊", surface)
        self.assertEqual(1, len(request._private_companion_turn_prompt_fragments))
        self.assertEqual(5, changed)

    def test_plan_removal_preserves_private_blocks_and_rejects_frozen_changes(self) -> None:
        request = SimpleNamespace(
            system_prompt="base",
            prompt="hello",
            contexts=[],
            extra_user_content_parts=[],
        )
        plan = get_conversation_injection_plan(request)
        plan.add(
            section=prompt_section(
                key="group.context",
                title="群聊上下文",
                source="group",
                content="group-only",
            ),
            marker="<!-- private_companion_group_context_v1 -->",
            placement=PLACEMENT_DYNAMIC_SYSTEM,
            metadata={
                "delivery_group_marker": "<!-- private_companion_group_context_v1 -->"
            },
        )
        plan.add(
            section=prompt_section(
                key="group.recent_atrelay",
                title="群聊转述上下文",
                source="group",
                content="same-group-secondary",
            ),
            marker="",
            placement=PLACEMENT_DYNAMIC_SYSTEM,
            metadata={
                "delivery_group_marker": "<!-- private_companion_group_context_v1 -->"
            },
        )
        plan.add(
            section=prompt_section(
                key="private.style",
                title="私聊风格",
                source="style",
                content="private-style",
            ),
            marker="<!-- private_companion_reply_style_v1 -->",
            placement=PLACEMENT_DYNAMIC_SYSTEM,
        )
        plan.render_into(request)

        removed = plan.remove_markers(
            f"<!-- {marker} -->" for marker in GROUP_SCOPE_MARKERS
        )
        plan.render_into(request)

        self.assertEqual(2, removed)
        self.assertNotIn("group-only", request.system_prompt)
        self.assertNotIn("same-group-secondary", request.system_prompt)
        self.assertIn("private-style", request.system_prompt)
        plan.freeze()
        with self.assertRaisesRegex(RuntimeError, "frozen"):
            plan.remove_markers(["<!-- private_companion_reply_style_v1 -->"])

    def test_group_request_does_not_remove_current_group_context(self) -> None:
        group = "<!-- private_companion_group_context_v1 -->\n群标记 G29-保留"
        request = SimpleNamespace(
            system_prompt=group,
            prompt="群聊查询",
            contexts=[],
            extra_user_content_parts=[],
        )
        event = SimpleNamespace(
            unified_msg_origin="default:GroupMessage:group-1",
            is_private_chat=lambda: False,
        )

        changed = sanitize_private_request_group_artifacts(event, request)

        self.assertIn("G29-保留", request.system_prompt)
        self.assertEqual(0, changed)


if __name__ == "__main__":
    unittest.main()
