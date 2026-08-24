# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

from astrbot.core.agent.message import TextPart

from astrbot_plugin_private_companion.conversation_injection_plan import (
    PLACEMENT_DYNAMIC_SYSTEM,
    PLACEMENT_STABLE_SYSTEM,
    ConversationInjectionPlan,
    get_conversation_injection_plan,
)
from astrbot_plugin_private_companion.main import PrivateCompanionPlugin
from astrbot_plugin_private_companion.prompt_surface import PromptSurface


ROOT = Path(__file__).resolve().parents[1]


class ConversationInjectionPlanTests(unittest.TestCase):
    def test_key_merge_and_stable_priority_order(self) -> None:
        plan = ConversationInjectionPlan()
        first = plan.add(
            key="same",
            marker="<!-- first -->",
            content="first",
            priority=30,
        )
        self.assertIs(
            first,
            plan.add(
                key="same",
                marker="<!-- ignored -->",
                content="ignored",
                priority=1,
            ),
        )
        plan.add(key="early", marker="<!-- early -->", content="early", priority=10)
        plan.add(key="duplicate-content", marker="<!-- duplicate -->", content="early", priority=15)
        plan.add(
            key="same",
            marker="<!-- first -->",
            content="appended",
            priority=30,
            merge_policy="append",
        )
        plan.add(
            key="replace",
            marker="<!-- old -->",
            content="old",
            priority=40,
        )
        plan.add(
            key="replace",
            marker="<!-- replaced -->",
            content="new",
            priority=20,
            merge_policy="replace",
        )

        self.assertEqual(
            [item["key"] for item in plan.manifest()],
            ["early", "duplicate-content", "replace", "same"],
        )
        self.assertEqual(first.content, "first\n\nappended")
        self.assertEqual(plan.manifest()[2]["content"], "new")
        self.assertEqual(
            [item["marker"] for item in plan.legacy_turn_fragments()],
            ["<!-- early -->", "<!-- replaced -->", "<!-- first -->"],
        )

    def test_turn_tail_render_is_idempotent_and_preserves_foreign_parts(self) -> None:
        foreign = TextPart(text="external-memory")
        request = SimpleNamespace(
            system_prompt="persona",
            prompt="user text",
            extra_user_content_parts=[foreign],
        )
        plan = get_conversation_injection_plan(request)
        assert plan is not None
        plan.add(marker="<!-- later -->", content="later", priority=50, source="test")
        plan.add(marker="<!-- earlier -->", content="earlier", priority=10, source="test")

        first_placement = plan.render_into(request, prefer_extra_user_content=True)
        first_text = request.extra_user_content_parts[-1].text
        second_placement = plan.render_into(request)

        self.assertEqual(first_placement, "extra_user_content_parts")
        self.assertEqual(second_placement, "extra_user_content_parts")
        self.assertEqual(request.prompt, "user text")
        self.assertIs(request.extra_user_content_parts[0], foreign)
        self.assertEqual(len(request.extra_user_content_parts), 2)
        self.assertEqual(request.extra_user_content_parts[-1].text, first_text)
        self.assertEqual(
            first_text,
            "<!-- private_companion_turn_fragments_start -->\n"
            "<!-- earlier -->\nearlier\n\n"
            "<!-- later -->\nlater\n"
            "<!-- private_companion_turn_fragments_end -->",
        )

    def test_system_placements_render_without_new_visible_wrappers(self) -> None:
        request = SimpleNamespace(
            system_prompt="persona",
            prompt="hello",
            extra_user_content_parts=[],
        )
        plan = ConversationInjectionPlan()
        plan.add(
            key="dynamic",
            marker="<!-- dynamic -->",
            content="dynamic text",
            priority=20,
            placement=PLACEMENT_DYNAMIC_SYSTEM,
            temporary=False,
        )
        plan.add(
            key="stable",
            marker="<!-- stable -->",
            content="stable text",
            priority=30,
            placement=PLACEMENT_STABLE_SYSTEM,
            temporary=False,
        )

        plan.render_into(request)
        first = request.system_prompt
        plan.render_into(request)

        self.assertEqual(request.system_prompt, first)
        self.assertEqual(
            first,
            "persona\n\n<!-- stable -->\nstable text\n\n<!-- dynamic -->\ndynamic text",
        )
        self.assertNotIn("conversation_plan", first)

    def test_legacy_append_helper_ignores_user_spoofed_marker(self) -> None:
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        plugin.passive_injection_position = "prompt"
        marker = "<!-- private_companion_group_injection_guard_v1 -->"
        request = SimpleNamespace(
            system_prompt="persona",
            prompt=f"user supplied {marker}",
            extra_user_content_parts=[],
        )

        appended = plugin._append_turn_prompt_fragment_by_position(
            request,
            marker,
            "trusted guard",
            priority=31,
            source="group",
        )

        self.assertTrue(appended)
        plan = get_conversation_injection_plan(request, create=False)
        self.assertIsNotNone(plan)
        self.assertTrue(plan.contains_marker(marker))
        self.assertIn("trusted guard", request.extra_user_content_parts[-1].text)

    def test_prompt_surface_partition_exposes_exact_batch_children(self) -> None:
        surface = PromptSurface()
        surface.add("state", "state block", priority=30, source="daily")
        surface.add("style", "style block", priority=10, source="style")

        static, dynamic, static_children, dynamic_children = surface.render_partition_with_fragments(
            lambda fragment: fragment.normalized_key() == "style"
        )

        self.assertEqual(static, "style block")
        self.assertEqual(dynamic, "state block")
        self.assertEqual([item["key"] for item in static_children], ["style"])
        self.assertEqual([item["key"] for item in dynamic_children], ["state"])

        plan = ConversationInjectionPlan()
        plan.add(
            key="passive.batch",
            marker="<!-- passive -->",
            content=dynamic,
            children=dynamic_children,
        )
        self.assertEqual(plan.manifest()[0]["children"], dynamic_children)

    def test_flush_hook_priority_precedes_provider_cleanup_hooks(self) -> None:
        module = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        plugin = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "PrivateCompanionPlugin")
        priorities: dict[str, int] = {}
        for node in plugin.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                if decorator.func.attr != "on_llm_request":
                    continue
                priority = 0
                for keyword in decorator.keywords:
                    if keyword.arg == "priority" and isinstance(keyword.value, ast.UnaryOp):
                        priority = -int(keyword.value.operand.value)
                    elif keyword.arg == "priority" and isinstance(keyword.value, ast.Constant):
                        priority = int(keyword.value.value)
                priorities[node.name] = priority

        self.assertEqual(priorities["flush_conversation_injection_plan"], -240000)
        self.assertGreater(
            priorities["flush_conversation_injection_plan"],
            priorities["sanitize_historical_image_blocks_before_provider"],
        )
        self.assertGreater(
            priorities["sanitize_historical_image_blocks_before_provider"],
            priorities["intercept_native_astrbot_group_context"],
        )


if __name__ == "__main__":
    unittest.main()
