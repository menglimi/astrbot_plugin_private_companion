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
    exact_text,
    prompt_section,
    render_prompt_sections,
    xml_element,
)
from astrbot_plugin_private_companion.main import PrivateCompanionPlugin
from astrbot_plugin_private_companion.prompt_surface import PromptSurface

from tests.module_source_index import class_body_defs


ROOT = Path(__file__).resolve().parents[1]


def _section(
    key: str,
    content: str,
    *,
    title: str = "测试片段",
    source: str = "test",
):
    return prompt_section(
        key=key,
        title=title,
        source=source,
        content=content,
    )


class ConversationInjectionPlanTests(unittest.TestCase):
    def test_materialize_system_block_rejects_late_write_after_plan_freeze(self) -> None:
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        request = SimpleNamespace(
            system_prompt="persona",
            prompt="hello",
            extra_user_content_parts=[],
        )
        plan = get_conversation_injection_plan(request)
        assert plan is not None
        plan.freeze()
        section = prompt_section(
            key="tools.passive_reply_boundary",
            title="当前会话回复边界",
            source="tools",
            content="boundary",
        )

        with self.assertRaises(RuntimeError):
            plugin._materialize_conversation_system_block(
                request,
                section=section,
                marker="<!-- frozen-boundary -->",
            )
        self.assertEqual("persona", request.system_prompt)

    def test_main_helpers_require_authored_sections_and_preserve_exact_text(self) -> None:
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        plugin.passive_injection_position = "prompt"
        request = SimpleNamespace(
            system_prompt="persona",
            prompt="hello",
            extra_user_content_parts=[],
        )

        with self.assertRaises(TypeError):
            plugin._append_turn_prompt_fragment_by_position(
                request,
                "<!-- raw-turn -->",
                "raw text",
            )
        with self.assertRaises(TypeError):
            plugin._materialize_conversation_system_block(
                request,
                section="raw text",
            )

        wire = "1.5::fixed <nai> syntax\nsecond line"
        section = prompt_section(
            key="tools.exact_protocol",
            title="精确协议",
            source="tools",
            content=exact_text(wire),
        )
        self.assertTrue(
            plugin._materialize_conversation_system_block(
                request,
                section=section,
                marker="<!-- exact-protocol -->",
            )
        )
        self.assertEqual(f"persona\n\n{wire}", request.system_prompt)
        plan = get_conversation_injection_plan(request, create=False)
        self.assertIsNotNone(plan)
        self.assertTrue(plan.contains_marker("<!-- exact-protocol -->"))

    def test_exact_text_keeps_boundary_whitespace_in_system_and_turn_surfaces(self) -> None:
        system_wire = "  exact system contract  \n"
        system_request = SimpleNamespace(
            system_prompt="persona",
            prompt="hello",
            extra_user_content_parts=[],
        )
        system_plan = ConversationInjectionPlan()
        system_plan.add(
            section=prompt_section(
                key="contract.system",
                title="系统精确协议",
                source="test",
                content=exact_text(system_wire),
            ),
            marker="<!-- exact-system -->",
            placement=PLACEMENT_DYNAMIC_SYSTEM,
        )

        system_plan.render_into(system_request)

        self.assertEqual(f"persona\n\n{system_wire}", system_request.system_prompt)

        turn_wire = "  exact turn contract  \n"
        turn_request = SimpleNamespace(
            system_prompt="persona",
            prompt="hello",
            extra_user_content_parts=[],
        )
        turn_plan = ConversationInjectionPlan()
        turn_plan.add(
            section=prompt_section(
                key="contract.turn",
                title="本轮精确协议",
                source="test",
                content=exact_text(turn_wire),
            ),
            marker="<!-- exact-turn -->",
            placement="turn_tail",
        )

        turn_plan.render_into(turn_request, prefer_extra_user_content=False)

        self.assertEqual(f"hello\n\n{turn_wire}", turn_request.prompt)

    def test_key_merge_and_stable_priority_order(self) -> None:
        plan = ConversationInjectionPlan()
        first = plan.add(
            section=_section("same", "first"),
            marker="<!-- first -->",
            priority=30,
        )
        self.assertIs(
            first,
            plan.add(
                section=_section("same", "ignored"),
                marker="<!-- ignored -->",
                priority=1,
            ),
        )
        plan.add(section=_section("early", "early"), marker="<!-- early -->", priority=10)
        plan.add(
            section=_section("duplicate-content", "early"),
            marker="<!-- duplicate -->",
            priority=15,
        )
        plan.add(
            section=_section("same", "appended"),
            marker="<!-- first -->",
            priority=30,
            merge_policy="append",
        )
        plan.add(
            section=_section("replace", "old"),
            marker="<!-- old -->",
            priority=40,
        )
        plan.add(
            section=_section("replace", "new"),
            marker="<!-- replaced -->",
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
            [item["marker"] for item in plan.turn_fragments()],
            ["<!-- early -->", "<!-- duplicate -->", "<!-- replaced -->", "<!-- first -->"],
        )
        self.assertEqual(1, len(first.conflicts))
        self.assertEqual("same", first.conflicts[0]["key"])

    def test_identical_key_is_idempotent_and_strict_conflict_raises(self) -> None:
        section = _section("same", "body")
        plan = ConversationInjectionPlan()
        first = plan.add(section=section, marker="<!-- same -->")

        self.assertIs(first, plan.add(section=section, marker="<!-- same -->"))
        self.assertEqual([], plan.manifest()[0]["conflicts"])

        strict = ConversationInjectionPlan(strict_conflicts=True)
        strict.add(section=section, marker="<!-- same -->")
        with self.assertRaisesRegex(ValueError, "same"):
            strict.add(section=_section("same", "different"), marker="<!-- other -->")

    def test_surface_records_conflict_and_strict_mode_rejects_it(self) -> None:
        surface = PromptSurface()
        surface.add(_section("same", "first"))
        surface.add(_section("same", "second"))

        self.assertEqual("first", surface.sections()[0].content)
        self.assertEqual("same", surface.conflicts()[0]["key"])

        strict = PromptSurface(strict_conflicts=True)
        strict.add(_section("same", "first"))
        with self.assertRaisesRegex(ValueError, "same"):
            strict.add(_section("same", "second"))

    def test_child_only_turn_section_is_visible_and_keeps_turn_placement(self) -> None:
        request = SimpleNamespace(
            system_prompt="persona",
            prompt="hello",
            extra_user_content_parts=[],
        )
        plan = ConversationInjectionPlan()
        parent = prompt_section(
            key="reply.guidance",
            title="回复指导",
            source="main",
            content="",
            children=(
                prompt_section(
                    key="reply.style",
                    title="回复风格约束",
                    source="main",
                    content="保持自然简洁。",
                ),
            ),
        )

        plan.add(
            section=parent,
            marker="<!-- reply-style -->",
            placement="turn_tail",
        )
        placement = plan.render_into(request, prefer_extra_user_content=True)

        self.assertEqual("extra_user_content_parts", placement)
        self.assertEqual("persona", request.system_prompt)
        self.assertEqual("hello", request.prompt)
        self.assertEqual(1, len(request.extra_user_content_parts))
        rendered = request.extra_user_content_parts[0].text
        self.assertIn('<section title="回复风格约束">保持自然简洁。</section>', rendered)
        item = plan.manifest(include_content=True)[0]
        self.assertEqual("【回复风格约束】\n保持自然简洁。", item["content"])
        self.assertEqual(len(item["content"]), item["chars"])
        self.assertEqual("reply.style", item["children"][0]["key"])

    def test_main_multi_section_placement_keeps_authored_sections_as_siblings(self) -> None:
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        plugin.passive_injection_position = "prompt"
        request = SimpleNamespace(
            system_prompt="persona",
            prompt="hello",
            extra_user_content_parts=[],
        )
        sections = (
            _section("reply.style", "保持自然。", title="回复风格约束"),
            _section("reply.accuracy", "准确解释。", title="技术解释准确性"),
        )

        placement, visible, authored = plugin._place_conversation_prompt_sections(
            request,
            "<!-- reply-style -->",
            sections,
            priority=12,
        )

        self.assertEqual("extra_user_content_parts", placement)
        self.assertEqual(sections, authored)
        self.assertEqual(render_prompt_sections(sections), visible)
        payload = ET.fromstring(request.extra_user_content_parts[0].text)
        self.assertEqual(
            ["回复风格约束", "技术解释准确性"],
            [item.attrib["title"] for item in payload.findall("./section")],
        )
        self.assertEqual([], payload.findall("./section/section"))
        plan = get_conversation_injection_plan(request, create=False)
        self.assertIsNotNone(plan)
        self.assertEqual(
            ["reply.style", "reply.accuracy"],
            [item["key"] for item in plan.manifest()],
        )
        self.assertEqual(
            ["<!-- reply-style -->", "<!-- reply-style -->"],
            [
                item["metadata"]["delivery_group_marker"]
                for item in plan.manifest()
            ],
        )
        self.assertEqual(2, plan.remove_markers(["<!-- reply-style -->"]))
        plan.render_into(request, prefer_extra_user_content=True)
        self.assertEqual([], request.extra_user_content_parts)

    def test_main_multi_section_placement_keeps_system_sections_as_siblings(self) -> None:
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        plugin.passive_injection_position = "system_prompt"
        request = SimpleNamespace(
            system_prompt="persona",
            prompt="hello",
            extra_user_content_parts=[],
        )
        sections = (
            _section("guard.general", "通用边界。", title="能力边界"),
            _section("guard.platform", "平台边界。", title="平台能力边界"),
        )

        placement, _, _ = plugin._place_conversation_prompt_sections(
            request,
            "<!-- capability -->",
            sections,
            priority=30,
        )

        self.assertEqual("system_prompt", placement)
        self.assertEqual([], request.extra_user_content_parts)
        payload = ET.fromstring(request.system_prompt.split("\n\n", 1)[1])
        self.assertEqual(
            ["能力边界", "平台能力边界"],
            [item.attrib["title"] for item in payload.findall("./section")],
        )
        self.assertEqual([], payload.findall("./section/section"))

    def test_append_merge_preserves_authored_child_sections(self) -> None:
        plan = ConversationInjectionPlan()
        plan.add(
            section=prompt_section(
                key="reply.guidance",
                title="回复指导",
                source="test",
                content="",
                children=(
                    prompt_section(
                        key="reply.first",
                        title="第一项",
                        source="test",
                        content="A",
                    ),
                ),
            ),
            marker="<!-- reply-batch -->",
        )
        plan.add(
            section=prompt_section(
                key="reply.guidance",
                title="回复指导",
                source="test",
                content="",
                children=(
                    prompt_section(
                        key="reply.second",
                        title="第二项",
                        source="test",
                        content="B",
                    ),
                ),
            ),
            marker="<!-- reply-batch -->",
            merge_policy="append",
        )

        rendered = plan.turn_fragments()[0]["content"]
        payload = ET.fromstring(rendered)
        self.assertEqual(
            ["第一项", "第二项"],
            [item.attrib["title"] for item in payload.findall("./section/section")],
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
        plan.add(
            section=_section("later", "later", title="稍后片段"),
            marker="<!-- later -->",
            priority=50,
        )
        plan.add(
            section=_section("earlier", "earlier", title="较早片段"),
            marker="<!-- earlier -->",
            priority=10,
        )

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
            section=_section("dynamic", "dynamic text", title="动态约束"),
            marker="<!-- dynamic -->",
            priority=20,
            placement=PLACEMENT_DYNAMIC_SYSTEM,
        )
        plan.add(
            section=_section("stable", "stable text", title="稳定约束"),
            marker="<!-- stable -->",
            priority=30,
            placement=PLACEMENT_STABLE_SYSTEM,
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

    def test_authored_blocks_share_one_xml_root(self) -> None:
        request = SimpleNamespace(
            system_prompt="persona",
            prompt="hello",
            extra_user_content_parts=[],
        )
        plan = ConversationInjectionPlan()
        for key, title, body in (("one", "一", "第一条"), ("two", "二", "第二条")):
            plan.add(
                section=_section(key, body, title=title),
                marker=f"<!-- {key} -->",
                placement=PLACEMENT_DYNAMIC_SYSTEM,
                materialized=True,
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
        plan.add(
            section=_section("fresh", "fresh", title="新片段"),
            marker="<!-- fresh -->",
        )
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
                section=_section(key, key, title=title),
                marker=f"<!-- {key} -->",
                priority=priority,
                placement=PLACEMENT_DYNAMIC_SYSTEM,
            )
        plan.materialize_system_block(
            request,
            section=_section("media", "media", title="内部历史标记"),
            marker="<!-- media -->",
            priority=30,
            placement=PLACEMENT_STABLE_SYSTEM,
        )

        previous = request.system_prompt
        plan.add(
            section=_section("environment", "environment", title="环境感知"),
            marker="<!-- environment -->",
            priority=30,
            placement=PLACEMENT_DYNAMIC_SYSTEM,
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
            section=_section("guard.first", "first guard", title="第一条边界", source="guard"),
            marker="<!-- first -->",
            priority=90,
        )
        plan.materialize_system_block(
            request,
            section=_section("guard.second", "second guard", title="第二条边界", source="guard"),
            marker="<!-- second -->",
            priority=10,
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
                section=_section("guard.first", "duplicate", title="第一条边界", source="guard"),
                marker="<!-- first -->",
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
            section=_section(
                "tools.passive_reply_boundary",
                "boundary body",
                title="当前会话回复边界",
            ),
            marker="<!-- boundary -->",
            priority=10,
        )
        opaque_marker = "<!-- media-contract -->"
        opaque_body = "【内部历史标记】`<pc_history_media ... />`"
        request.system_prompt += f"\n\n{opaque_marker}\n{opaque_body}"
        plan.add(
            section=prompt_section(
                key="tools.media_contract",
                title="内部历史标记",
                source="test",
                content=exact_text(opaque_body),
            ),
            marker=opaque_marker,
            placement=PLACEMENT_TOOL_CONTRACT,
            materialized=True,
        )
        plan.add(
            section=_section("group.injection_guard", "guard body", title="群聊防注入"),
            marker="<!-- guard -->",
            placement=PLACEMENT_DYNAMIC_SYSTEM,
            materialized=True,
        )
        duplicate = plan.materialize_system_block(
            request,
            section=_section("group.injection_guard", "guard body", title="群聊防注入"),
            marker="<!-- guard -->",
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
            section=prompt_section(
                key="tool.photo.prompt_format",
                title="提示词表达方式",
                source="photo_tool",
                content=exact_text(opaque_wire),
            ),
            marker="<!-- tool-contract -->",
            placement=PLACEMENT_TOOL_CONTRACT,
            materialized=True,
        )

        plan.render_into(request)

        self.assertEqual("persona", request.system_prompt)
        self.assertEqual("hello", request.prompt)
        self.assertNotIn("content", plan.manifest()[0])
        self.assertEqual(
            opaque_wire,
            plan.manifest(include_content=True)[0]["content"],
        )

    def test_section_append_helper_ignores_user_spoofed_marker(self) -> None:
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        plugin.passive_injection_position = "prompt"
        marker = "<!-- private_companion_group_injection_guard_v1 -->"
        request = SimpleNamespace(
            system_prompt="persona",
            prompt=f"user supplied {marker}",
            extra_user_content_parts=[],
        )

        section = prompt_section(
            key="group.injection_guard",
            title="群聊防注入",
            source="group",
            content="trusted guard",
        )
        appended = plugin._append_turn_prompt_fragment_by_position(
            request,
            marker,
            section,
            priority=31,
        )

        self.assertTrue(appended)
        plan = get_conversation_injection_plan(request, create=False)
        self.assertIsNotNone(plan)
        self.assertTrue(plan.contains_marker(marker))
        self.assertIn("trusted guard", request.extra_user_content_parts[-1].text)

    def test_prompt_surface_partition_exposes_exact_batch_children(self) -> None:
        surface = PromptSurface()
        surface.add(
            prompt_section(
                key="state",
                title="状态",
                source="daily",
                content="state block",
            ),
            priority=30,
        )
        surface.add(
            prompt_section(
                key="style",
                title="风格",
                source="style",
                content="style block",
            ),
            priority=10,
        )

        static_sections, dynamic_sections = surface.partition_sections(
            lambda fragment: fragment.normalized_key() == "style"
        )

        static_xml = ET.fromstring(render_prompt_sections(static_sections))
        dynamic_xml = ET.fromstring(render_prompt_sections(dynamic_sections))
        self.assertEqual(static_xml.find("./section").attrib["title"], "风格")
        self.assertEqual(static_xml.findtext("./section"), "style block")
        self.assertEqual(dynamic_xml.find("./section").attrib["title"], "状态")
        self.assertEqual(dynamic_xml.findtext("./section"), "state block")
        plan = ConversationInjectionPlan()
        plan.add(
            section=prompt_section(
                key="context.passive_state",
                title="被动状态上下文",
                source="test",
                content="",
                children=dynamic_sections,
            ),
            marker="<!-- passive -->",
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
            section=_section(
                "reply.style",
                "正文中提到【旧标题】，但它不是结构边界。",
                title="回复风格约束",
            ),
            marker="<!-- style -->",
            placement=PLACEMENT_DYNAMIC_SYSTEM,
        )

        plan.render_into(request)
        payload = ET.fromstring(request.system_prompt.split("\n\n", 1)[1])

        self.assertEqual("回复风格约束", payload.find("./section").attrib["title"])
        self.assertEqual("正文中提到【旧标题】，但它不是结构边界。", payload.findtext("./section"))

    def test_xml_renderer_removes_xml_10_invalid_codepoints(self) -> None:
        surface = PromptSurface()
        surface.add(
            prompt_section(
                key="reply.style",
                title="回复风格",
                source="test",
                content="可见\x00文本\ufffe与孤立代理项\ud800结束",
            )
        )

        payload = ET.fromstring(render_prompt_sections(surface.sections()))

        self.assertEqual(
            "可见文本与孤立代理项结束",
            payload.findtext("./section"),
        )

    def test_surface_accepts_only_authored_sections_without_folding_body(self) -> None:
        surface = PromptSurface()
        surface.add(
            prompt_section(
                key="one",
                title="第一段",
                source="test",
                content="  第一行\n\n  第二行  ",
            )
        )
        surface.add(
            prompt_section(
                key="two",
                title="第二段",
                source="test",
                content="正文",
            )
        )

        rendered = render_prompt_sections(surface.sections())
        payload = ET.fromstring(rendered)

        self.assertNotIn(">\n<", rendered)
        self.assertEqual(1, rendered.count("<private_companion_context>"))
        self.assertEqual(["第一段", "第二段"], [item.attrib["title"] for item in payload.findall("./section")])
        self.assertEqual(
            "  第一行\n\n  第二行  ",
            payload.findall("./section")[0].text,
        )
        with self.assertRaises(TypeError):
            surface.add({"title": "旧映射", "content": "正文"})  # type: ignore[arg-type]

    def test_xml_element_contract_renders_escaped_attributes_text_and_children(self) -> None:
        surface = PromptSurface()
        surface.add(
            prompt_section(
                key="group.context",
                title="群聊上下文",
                source="test",
                content=xml_element(
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

        payload = ET.fromstring(render_prompt_sections(surface.sections()))
        history = payload.find("./section/history")
        message = payload.find("./section/history/message")

        self.assertEqual("2026-08-25", history.attrib["date"])
        self.assertEqual("Asia/Shanghai", history.attrib["timezone"])
        self.assertEqual("m&1", message.attrib["id"])
        self.assertEqual("A<B", message.attrib["name"])
        self.assertEqual("  原文\n不折叠  ", message.text)
        self.assertIn('<status ready="true"/>', render_prompt_sections(surface.sections()))
        with self.assertRaises(ValueError):
            xml_element("message bad", text="no")
        with self.assertRaises(TypeError):
            xml_element("message", attrs={"meta": {"nested": True}})

    def test_surface_rejects_untyped_content(self) -> None:
        surface = PromptSurface()
        with self.assertRaises(TypeError):
            surface.add("body")  # type: ignore[arg-type]

    def test_flush_hook_priority_precedes_provider_cleanup_hooks(self) -> None:
        # 巨型模块拆分后，on_llm_request 钩子方法体可能位于 main.py 或 main_*.py
        # 域 mixin 类里，故聚合「宿主类 + 各域 mixin 类」的类体，语义不变。
        priorities: dict[str, int] = {}
        for node in class_body_defs(ROOT, "main", "PrivateCompanionPlugin"):
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

        self.assertEqual(set(), set(direct_writes))


if __name__ == "__main__":
    unittest.main()
