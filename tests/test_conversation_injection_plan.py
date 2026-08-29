# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import re
import unittest
from xml.etree import ElementTree as ET
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
from astrbot_plugin_private_companion.conversation_prompt_section import (
    PromptSection,
    prompt_section,
    render_prompt_sections,
    xml_element,
)
from astrbot_plugin_private_companion.main import PrivateCompanionPlugin
from astrbot_plugin_private_companion.prompt_surface import PromptSurface


ROOT = Path(__file__).resolve().parents[1]


class ConversationInjectionPlanTests(unittest.TestCase):
    def test_materialize_system_block_tolerates_plan_frozen_by_late_hook(self) -> None:
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        request = SimpleNamespace(
            system_prompt="persona",
            prompt="hello",
            extra_user_content_parts=[],
        )
        plan = get_conversation_injection_plan(request)
        assert plan is not None
        plan.freeze()

        added = plugin._materialize_conversation_system_block(
            request,
            key="tools.passive_reply_boundary",
            marker="<!-- frozen-boundary -->",
            content="boundary",
        )

        self.assertTrue(added)
        self.assertIn("<!-- frozen-boundary -->", request.system_prompt)
        self.assertFalse(
            plugin._materialize_conversation_system_block(
                request,
                key="tools.passive_reply_boundary",
                marker="<!-- frozen-boundary -->",
                content="boundary",
            )
        )

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
        plan.add(marker="<!-- later -->", title="稍后片段", content="later", priority=50, source="test")
        plan.add(marker="<!-- earlier -->", title="较早片段", content="earlier", priority=10, source="test")

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
            "<private_companion_context>"
            '<section title="较早片段">earlier</section>'
            '<section title="稍后片段">later</section>'
            "</private_companion_context>",
        )
        self.assertEqual(1, first_text.count("<private_companion_context>"))

    def test_system_placements_render_as_explicit_sections(self) -> None:
        request = SimpleNamespace(
            system_prompt="persona",
            prompt="hello",
            extra_user_content_parts=[],
        )
        plan = ConversationInjectionPlan()
        plan.add(
            key="dynamic",
            marker="<!-- dynamic -->",
            title="动态约束",
            content="dynamic text",
            priority=20,
            placement=PLACEMENT_DYNAMIC_SYSTEM,
            temporary=False,
        )
        plan.add(
            key="stable",
            marker="<!-- stable -->",
            title="稳定约束",
            content="stable text",
            priority=30,
            placement=PLACEMENT_STABLE_SYSTEM,
            temporary=False,
        )

        plan.render_into(request)
        first = request.system_prompt
        plan.render_into(request)

        self.assertEqual(request.system_prompt, first)
        self.assertIn('<section title="稳定约束">stable text</section>', first)
        self.assertIn('<section title="动态约束">dynamic text</section>', first)
        self.assertEqual(2, first.count("<private_companion_context>"))
        self.assertNotIn("<!-- stable -->", first)
        self.assertNotIn("<!-- dynamic -->", first)
        self.assertNotIn("conversation_plan", first)

    def test_structured_blocks_share_one_xml_root(self) -> None:
        request = SimpleNamespace(
            system_prompt="persona",
            prompt="hello",
            extra_user_content_parts=[],
        )
        plan = ConversationInjectionPlan()
        for key, title, body in (("one", "一", "第一条"), ("two", "二", "第二条")):
            plan.add(
                key=key,
                marker=f"<!-- {key} -->",
                title=title,
                content=render_prompt_sections([prompt_section(title, body)]),
                placement=PLACEMENT_DYNAMIC_SYSTEM,
                materialized=True,
                structured=True,
            )
        plan.render_into(request)
        self.assertEqual(1, request.system_prompt.count("<private_companion_context>"))
        self.assertIn('<section title="一">第一条</section><section title="二">第二条</section>', request.system_prompt)

    def test_middle_turn_text_cleanup_is_instance_safe(self) -> None:
        request = SimpleNamespace(
            system_prompt="persona",
            prompt="before\n\nmanaged\n\nafter",
            extra_user_content_parts=[],
            _private_companion_conversation_plan_turn_text="managed",
        )
        plan = ConversationInjectionPlan()
        plan.add(marker="<!-- fresh -->", title="新片段", content="fresh")
        plan.render_into(request, prefer_extra_user_content=False)
        self.assertNotIn("managed", request.prompt)
        self.assertIn("fresh", request.prompt)

    def test_system_rerender_removes_previous_owned_roots_from_middle(self) -> None:
        request = SimpleNamespace(
            system_prompt="persona",
            prompt="hello",
            extra_user_content_parts=[],
        )
        plan = ConversationInjectionPlan()
        for key, title, priority in (
            ("boundary", "当前会话回复边界", 10),
            ("guard", "群聊防注入", 31),
            ("group", "群聊上下文", 10_000),
        ):
            plan.materialize_system_block(
                request,
                key=key,
                marker=f"<!-- {key} -->",
                title=title,
                content=key,
                priority=priority,
                placement=PLACEMENT_DYNAMIC_SYSTEM,
            )
        plan.materialize_system_block(
            request,
            key="media",
            marker="<!-- media -->",
            title="内部历史标记",
            content="media",
            priority=30,
            placement=PLACEMENT_STABLE_SYSTEM,
        )

        previous = request.system_prompt
        plan.add(
            key="environment",
            marker="<!-- environment -->",
            title="环境感知",
            content="environment",
            priority=30,
            placement=PLACEMENT_DYNAMIC_SYSTEM,
            temporary=False,
            materialized=True,
        )
        request.system_prompt = f"{previous}\n\n<!-- environment -->\nenvironment"
        plan.render_into(request)

        roots = [
            ET.fromstring(item)
            for item in re.findall(
                r"<private_companion_context>.*?</private_companion_context>",
                request.system_prompt,
                flags=re.DOTALL,
            )
        ]
        self.assertEqual(2, len(roots))
        self.assertEqual(
            ["内部历史标记"],
            [item.attrib["title"] for item in roots[0].findall("./section")],
        )
        self.assertEqual(
            ["当前会话回复边界", "环境感知", "群聊防注入", "群聊上下文"],
            [item.attrib["title"] for item in roots[1].findall("./section")],
        )
        for title in ("当前会话回复边界", "群聊防注入", "群聊上下文"):
            self.assertEqual(1, request.system_prompt.count(f'title="{title}"'))

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
            title="第一条边界",
            content="first guard",
            priority=90,
            source="guard",
        )
        plan.materialize_system_block(
            request,
            key="guard.second",
            marker="<!-- second -->",
            title="第二条边界",
            content="second guard",
            priority=10,
            source="guard",
        )
        before_flush = request.system_prompt

        plan.render_into(request)
        plan.render_into(request)

        self.assertEqual(request.system_prompt, before_flush)
        self.assertIn('<section title="第一条边界">first guard</section>', request.system_prompt)
        self.assertIn('<section title="第二条边界">second guard</section>', request.system_prompt)
        self.assertEqual(1, request.system_prompt.count("<private_companion_context>"))
        self.assertNotIn("<!-- first -->", request.system_prompt)
        self.assertNotIn("<!-- second -->", request.system_prompt)
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

    def test_marker_dedup_and_opaque_interleave_leave_no_empty_sections(self) -> None:
        request = SimpleNamespace(
            system_prompt="persona",
            prompt="hello",
            extra_user_content_parts=[],
        )
        plan = ConversationInjectionPlan()
        plan.materialize_system_block(
            request,
            key="tools.passive_reply_boundary",
            marker="<!-- boundary -->",
            title="当前会话回复边界",
            content="boundary body",
            priority=10,
        )
        opaque_marker = "<!-- media-contract -->"
        opaque_body = "【内部历史标记】`<pc_history_media ... />`"
        request.system_prompt += f"\n\n{opaque_marker}\n{opaque_body}"
        plan.add(
            key="tools.media_contract",
            marker=opaque_marker,
            content=opaque_body,
            placement=PLACEMENT_TOOL_CONTRACT,
            materialized=True,
            opaque=True,
        )
        plan.add(
            marker="<!-- guard -->",
            title="群聊防注入",
            content="guard body",
            placement=PLACEMENT_DYNAMIC_SYSTEM,
            materialized=True,
        )
        duplicate = plan.materialize_system_block(
            request,
            key="group.injection_guard",
            marker="<!-- guard -->",
            title="群聊防注入",
            content="guard body",
            priority=31,
        )
        plan.render_into(request)

        self.assertFalse(duplicate)
        self.assertEqual(1, request.system_prompt.count('title="当前会话回复边界"'))
        self.assertEqual(1, request.system_prompt.count('title="群聊防注入"'))
        self.assertNotIn('<section title="当前会话回复边界"/>', request.system_prompt)
        self.assertNotIn('<section title="群聊防注入"/>', request.system_prompt)
        self.assertEqual(1, request.system_prompt.count(opaque_marker))
        self.assertEqual(1, request.system_prompt.count(opaque_body))
        self.assertEqual(3, len(plan.blocks()))

    def test_opaque_tool_contract_is_audited_without_entering_text_surfaces(self) -> None:
        request = SimpleNamespace(
            system_prompt="persona",
            prompt="hello",
            extra_user_content_parts=[],
        )
        plan = ConversationInjectionPlan()
        opaque_wire = "\n  1.5::fixed nai syntax::  \n"
        plan.add(
            key="tool.photo.prompt_format",
            marker="<!-- tool-contract -->",
            content=opaque_wire,
            placement=PLACEMENT_TOOL_CONTRACT,
            materialized=True,
            opaque=True,
        )

        plan.render_into(request)

        self.assertEqual("persona", request.system_prompt)
        self.assertEqual("hello", request.prompt)
        self.assertTrue(plan.manifest()[0]["opaque"])
        self.assertNotIn("content", plan.manifest()[0])
        self.assertEqual(
            opaque_wire,
            plan.manifest(include_content=True)[0]["content"],
        )

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
        surface.add("state", "state block", title="状态", priority=30, source="daily")
        surface.add("style", "style block", title="风格", priority=10, source="style")

        static, dynamic, static_children, dynamic_children = surface.render_partition_with_fragments(
            lambda fragment: fragment.normalized_key() == "style"
        )

        static_xml = ET.fromstring(static)
        dynamic_xml = ET.fromstring(dynamic)
        self.assertEqual(static_xml.find("./section").attrib["title"], "风格")
        self.assertEqual(static_xml.findtext("./section"), "style block")
        self.assertEqual(dynamic_xml.find("./section").attrib["title"], "状态")
        self.assertEqual(dynamic_xml.findtext("./section"), "state block")
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

    def test_structuring_uses_explicit_key_and_does_not_infer_title_from_content(self) -> None:
        request = SimpleNamespace(system_prompt="persona", prompt="hello", extra_user_content_parts=[])
        plan = ConversationInjectionPlan()
        plan.add(
            key="reply.style",
            marker="<!-- style -->",
            title="回复风格约束",
            content="正文中提到【旧标题】，但它不是结构边界。",
            placement=PLACEMENT_DYNAMIC_SYSTEM,
        )

        plan.render_into(request)
        payload = ET.fromstring(request.system_prompt.split("\n\n", 1)[1])

        self.assertEqual("回复风格约束", payload.find("./section").attrib["title"])
        self.assertEqual("正文中提到【旧标题】，但它不是结构边界。", payload.findtext("./section"))

    def test_xml_renderer_removes_xml_10_invalid_codepoints(self) -> None:
        surface = PromptSurface()
        surface.add(
            "reply.style",
            "可见\x00文本\ufffe与孤立代理项\ud800结束",
        )

        payload = ET.fromstring(surface.render())

        self.assertEqual(
            "可见文本与孤立代理项结束",
            payload.findtext("./section"),
        )

    def test_surface_accepts_prompt_section_and_mapping_without_folding_body(self) -> None:
        surface = PromptSurface()
        surface.add("one", PromptSection("第一段", "  第一行\n\n  第二行  "))
        surface.add("two", {"title": "第二段", "content": "正文"})

        rendered = surface.render()
        payload = ET.fromstring(rendered)

        self.assertNotIn(">\n<", rendered)
        self.assertEqual(1, rendered.count("<private_companion_context>"))
        self.assertEqual(["第一段", "第二段"], [item.attrib["title"] for item in payload.findall("./section")])
        self.assertEqual(
            "  第一行\n\n  第二行  ",
            payload.findall("./section")[0].text,
        )

    def test_xml_element_contract_renders_escaped_attributes_text_and_children(self) -> None:
        surface = PromptSurface()
        surface.add(
            "group.context",
            PromptSection(
                "群聊上下文",
                xml_element(
                    "history",
                    attrs={"date": "2026-08-25", "timezone": "Asia/Shanghai"},
                    children=[
                        xml_element(
                            "message",
                            attrs={
                                "time": "21:30",
                                "id": "m&1",
                                "name": "A<B",
                                "role": "user",
                            },
                            text="  原文\n不折叠  ",
                        ),
                        xml_element("status", attrs={"ready": True}),
                    ],
                ),
            ),
        )

        payload = ET.fromstring(surface.render())
        history = payload.find("./section/history")
        message = payload.find("./section/history/message")

        self.assertEqual("2026-08-25", history.attrib["date"])
        self.assertEqual("Asia/Shanghai", history.attrib["timezone"])
        self.assertEqual("m&1", message.attrib["id"])
        self.assertEqual("A<B", message.attrib["name"])
        self.assertEqual("  原文\n不折叠  ", message.text)
        self.assertIn('<status ready="true"/>', surface.render())
        with self.assertRaises(ValueError):
            xml_element("message bad", text="no")
        with self.assertRaises(TypeError):
            xml_element("message", attrs={"meta": {"nested": True}})

    def test_unknown_key_uses_generic_title_instead_of_leaking_internal_key(self) -> None:
        surface = PromptSurface()
        surface.add("internal.runtime.key", "body")

        payload = ET.fromstring(surface.render())

        self.assertEqual("提示词片段", payload.find("./section").attrib["title"])

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
