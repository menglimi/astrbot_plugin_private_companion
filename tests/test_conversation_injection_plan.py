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
    PLACEMENT_TOOL_CONTRACT,
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
        self.assertEqual(plan.manifest(include_content=True)[2]["content"], "new")
        self.assertNotIn("content", plan.manifest()[2])
        self.assertEqual(64, len(plan.manifest()[2]["sha256"]))
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

    def test_materialized_system_blocks_keep_legacy_wire_order_after_flush(self) -> None:
        request = SimpleNamespace(
            system_prompt="persona",
            prompt="hello",
            extra_user_content_parts=[],
        )
        plan = ConversationInjectionPlan()
        plan.materialize_system_block(
            request,
            key="guard.first",
            marker="<!-- first -->",
            content="first guard",
            priority=90,
            source="guard",
        )
        plan.materialize_system_block(
            request,
            key="guard.second",
            marker="<!-- second -->",
            content="second guard",
            priority=10,
            source="guard",
        )
        before_flush = request.system_prompt

        plan.render_into(request)
        plan.render_into(request)

        self.assertEqual(request.system_prompt, before_flush)
        self.assertEqual(
            request.system_prompt,
            "persona\n\n<!-- first -->\nfirst guard\n\n<!-- second -->\nsecond guard",
        )
        self.assertTrue(all(item["materialized"] for item in plan.manifest()))
        self.assertFalse(
            plan.materialize_system_block(
                request,
                key="guard.first",
                marker="<!-- first -->",
                content="duplicate",
            )
        )
        self.assertEqual(request.system_prompt, before_flush)

    def test_opaque_tool_contract_is_audited_without_entering_text_surfaces(self) -> None:
        request = SimpleNamespace(
            system_prompt="persona",
            prompt="hello",
            extra_user_content_parts=[],
        )
        plan = ConversationInjectionPlan()
        plan.add(
            key="tool.photo.prompt_format",
            marker="<!-- tool-contract -->",
            content="1.5::fixed nai syntax::",
            placement=PLACEMENT_TOOL_CONTRACT,
            materialized=True,
            opaque=True,
        )

        plan.render_into(request)

        self.assertEqual("persona", request.system_prompt)
        self.assertEqual("hello", request.prompt)
        self.assertTrue(plan.manifest()[0]["opaque"])
        self.assertNotIn("content", plan.manifest()[0])

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
        safe_children = plan.manifest()[0]["children"]
        self.assertEqual([item["key"] for item in safe_children], ["state"])
        self.assertNotIn("content", safe_children[0])
        self.assertEqual(
            "state block",
            plan.manifest(include_content=True)[0]["children"][0]["content"],
        )

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
        self.assertEqual(priorities["finalize_conversation_injection_plan"], -260000)
        self.assertGreater(
            priorities["intercept_native_astrbot_group_context"],
            priorities["finalize_conversation_injection_plan"],
        )

    def test_main_direct_system_writes_are_limited_to_registered_fallbacks(self) -> None:
        module = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        direct_writes: dict[str, int] = {}
        for node in ast.walk(module):
            if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not any(
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "req"
                and target.attr == "system_prompt"
                for target in targets
            ):
                continue
            owner = next(
                (
                    parent
                    for parent in ast.walk(module)
                    if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node in ast.walk(parent)
                ),
                None,
            )
            if owner is not None:
                direct_writes[owner.name] = direct_writes.get(owner.name, 0) + 1

        self.assertEqual(
            set(direct_writes),
            {
                "_append_environment_perception_to_request",
                "_append_reply_style_to_request",
                "_append_group_high_intensity_reply_guard_to_request",
                "_materialize_conversation_system_block",
                "_append_conditional_tool_instructions_to_request",
                "_append_group_active_period_boundary_to_request",
                "_append_private_active_period_boundary_to_request",
                "_append_group_persona_denoise_to_request",
                "_append_atrelay_target_summary_to_request",
                "_append_worldbook_mentions_to_request",
                "_append_rest_reply_backlog_to_request",
                "append_group_cycle_privacy_boundary",
            },
        )


if __name__ == "__main__":
    unittest.main()
