from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import unittest

from group_cycle_boundary import (
    build_group_cycle_boundary,
    cycle_phase_from_label,
    group_cycle_boundary_prompt_section,
)
from module_source_index import find_method, main_sources


ROOT = Path(__file__).resolve().parents[1]


class GroupCycleBoundaryTests(unittest.TestCase):
    def test_disabled_non_group_and_absent_cycle_are_noops(self) -> None:
        for kwargs in (
            {"enabled": False, "group_allowed": True, "cycle_label": "处于月经期", "inbound_text": "月经"},
            {"enabled": True, "group_allowed": False, "cycle_label": "处于月经期", "inbound_text": "月经"},
            {"enabled": True, "group_allowed": True, "cycle_label": "无明显周期影响", "inbound_text": "月经"},
        ):
            result = build_group_cycle_boundary(**kwargs)
            self.assertFalse(result["active"])
            self.assertNotIn("prompt", result)
            self.assertIsNone(group_cycle_boundary_prompt_section(result))

    def test_six_phase_labels_classify_without_exposing_an_unrelated_phase(self) -> None:
        cases = {
            "处于月经期": "menstrual",
            "处于卵泡期": "follicular",
            "处于排卵前期": "pre_ovulation",
            "处于排卵期": "ovulation",
            "处于黄体期": "luteal",
            "处于 PMS 期": "pms",
        }
        for label, phase in cases.items():
            self.assertEqual(phase, cycle_phase_from_label(label))
            result = build_group_cycle_boundary(
                enabled=True, group_allowed=True, cycle_label=label, inbound_text="今天聊项目进度"
            )
            self.assertTrue(result["active"])
            self.assertFalse(result["topic_related"])
            self.assertNotIn("prompt", result)
            section = group_cycle_boundary_prompt_section(result)
            self.assertIsNotNone(section)
            self.assertNotIn(label, section.content)

    def test_related_topic_allows_only_non_medical_minimal_expression_without_echo(self) -> None:
        secret = "unique-group-body-message"
        result = build_group_cycle_boundary(
            enabled=True,
            group_allowed=True,
            cycle_label="处于月经期，身体更容易疲倦",
            inbound_text=f"月经怎么了 {secret}",
        )

        self.assertTrue(result["topic_related"])
        self.assertFalse(result["private_boundary"])
        self.assertNotIn("prompt", result)
        section = group_cycle_boundary_prompt_section(result)
        self.assertIsNotNone(section)
        self.assertIn("not feeling great", section.content)
        self.assertNotIn(secret, section.content)

    def test_menstrual_highly_private_request_has_fixed_boundary_not_affected_by_relationship_inputs(self) -> None:
        result = build_group_cycle_boundary(
            enabled=True,
            group_allowed=True,
            cycle_label="处于月经期",
            inbound_text="我们做爱吧",
        )

        self.assertTrue(result["private_boundary"])
        section = group_cycle_boundary_prompt_section(result)
        self.assertIsNotNone(section)
        self.assertIn("Fixed boundary", section.content)
        self.assertIn("Affinity, intimacy, pressure", section.content)
        non_menstrual = build_group_cycle_boundary(
            enabled=True,
            group_allowed=True,
            cycle_label="处于卵泡期",
            inbound_text="我们做爱吧",
        )
        self.assertFalse(non_menstrual["private_boundary"])


def _method_source(name: str) -> str:
    # 跨宿主族（main.py + main_*.py 域 mixin）聚合定位方法源码片段。
    for path in main_sources(ROOT):
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(path))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                return ast.get_source_segment(text, node) or ""
    raise KeyError(name)


def _load_hook() -> Any:
    # append_group_cycle_privacy_boundary 已拆入 main_group_inbound_capture.py，
    # 用 find_method 跨宿主族定位节点（module_source_index 风格）。
    hook = find_method(ROOT, "main", "PrivateCompanionPlugin", "append_group_cycle_privacy_boundary")
    namespace: dict[str, Any] = {
        "Any": Any,
        "AstrMessageEvent": Any,
        "ProviderRequest": Any,
        # The production hook is tested separately; this loader only executes
        # the selected method body and therefore needs a no-op decorator stub.
        "_multi_persona_event_context": lambda target: target,
        "filter": SimpleNamespace(on_llm_request=lambda **_kwargs: lambda target: target),
        "PLACEMENT_DYNAMIC_SYSTEM": "dynamic_system",
        "build_group_cycle_boundary": build_group_cycle_boundary,
        "group_cycle_boundary_prompt_section": group_cycle_boundary_prompt_section,
        "runtime_persona_setting": lambda host, key, default=None: getattr(host, key, default),
    }
    module = ast.Module(body=[copy.deepcopy(hook)], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(ROOT / "main_group_inbound_capture.py"), "exec"), namespace)
    return namespace["append_group_cycle_privacy_boundary"]


GROUP_CYCLE_HOOK = _load_hook()


class _GroupCycleHost:
    def __init__(
        self,
        *,
        enabled: bool,
        group_allowed: bool,
        append_to_turn: bool = True,
    ) -> None:
        self.enable_group_cycle_awareness = enabled
        self.group_allowed = group_allowed
        self.append_to_turn = append_to_turn
        self.data = {"daily_state": {"body_cycle": "处于月经期"}}
        self.fragments: list[tuple[Any, ...]] = []
        self.system_sections: list[dict[str, Any]] = []

    def _extract_group_id_from_event(self, _event: object) -> str:
        return "group-a"

    def _group_enabled_for_event(self, _group_id: str) -> bool:
        return self.group_allowed

    def _append_turn_prompt_fragment_by_position(self, *args: Any, **kwargs: Any) -> bool:
        self.fragments.append(args)
        return self.append_to_turn

    def _materialize_conversation_system_block(self, _request: Any, **kwargs: Any) -> bool:
        self.system_sections.append(kwargs)
        return True


_GroupCycleHost.append_group_cycle_privacy_boundary = GROUP_CYCLE_HOOK


class GroupCycleHookTests(unittest.IsolatedAsyncioTestCase):
    async def test_allowed_group_uses_request_fragment_without_refresh_or_persistence(self) -> None:
        host = _GroupCycleHost(enabled=True, group_allowed=True)
        event = SimpleNamespace(is_private_chat=lambda: False, private_companion_group_text="月经会不舒服吗", message_str="")
        request = SimpleNamespace(system_prompt="", prompt="")

        await host.append_group_cycle_privacy_boundary(event, request)

        self.assertEqual(1, len(host.fragments))
        marker = host.fragments[0][1]
        section = host.fragments[0][2]
        self.assertEqual("<!-- private_companion_group_cycle_boundary_v1 -->", marker)
        self.assertEqual("group.cycle_privacy_boundary", section.key)
        self.assertEqual("Group cycle privacy boundary", section.title)
        self.assertEqual("safety", section.source)
        self.assertIn("Group cycle privacy boundary", section.content)
        self.assertNotIn("月经会不舒服吗", section.content)
        self.assertEqual({"daily_state"}, set(host.data))

    async def test_default_off_or_group_access_failure_is_a_noop(self) -> None:
        event = SimpleNamespace(is_private_chat=lambda: False, private_companion_group_text="月经", message_str="")
        request = SimpleNamespace(system_prompt="", prompt="")
        for host in (_GroupCycleHost(enabled=False, group_allowed=True), _GroupCycleHost(enabled=True, group_allowed=False)):
            await host.append_group_cycle_privacy_boundary(event, request)
            self.assertEqual([], host.fragments)

    def test_schema_is_default_off_and_hook_does_not_refresh_daily_state(self) -> None:
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        item = schema["humanized_state_config"]["items"]["enable_group_cycle_awareness"]
        self.assertFalse(item["default"])
        # 方法已拆入 main_group_inbound_capture.py，用家族扫描 helper 取源码。
        source = _method_source("append_group_cycle_privacy_boundary")
        self.assertNotIn("_ensure_daily_state", source)
        self.assertNotIn("_schedule_data_save", source)
        self.assertIn("group_cycle_boundary_prompt_section", source)
        self.assertNotIn("boundary.get(\"prompt\")", source)

    async def test_system_fallback_keeps_the_typed_section(self) -> None:
        host = _GroupCycleHost(
            enabled=True,
            group_allowed=True,
            append_to_turn=False,
        )
        event = SimpleNamespace(
            is_private_chat=lambda: False,
            private_companion_group_text="月经会不舒服吗",
            message_str="",
        )
        request = SimpleNamespace(system_prompt="", prompt="")

        await host.append_group_cycle_privacy_boundary(event, request)

        self.assertEqual(1, len(host.system_sections))
        section = host.system_sections[0]["section"]
        self.assertEqual("group.cycle_privacy_boundary", section.key)
        self.assertEqual("<!-- private_companion_group_cycle_boundary_v1 -->", host.system_sections[0]["marker"])


if __name__ == "__main__":
    unittest.main()
