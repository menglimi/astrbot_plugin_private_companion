from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import unittest

from group_cycle_boundary import build_group_cycle_boundary, cycle_phase_from_label


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
            self.assertEqual("", result["prompt"])

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
            self.assertNotIn(label, result["prompt"])

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
        self.assertIn("not feeling great", result["prompt"])
        self.assertNotIn(secret, result["prompt"])

    def test_menstrual_highly_private_request_has_fixed_boundary_not_affected_by_relationship_inputs(self) -> None:
        result = build_group_cycle_boundary(
            enabled=True,
            group_allowed=True,
            cycle_label="处于月经期",
            inbound_text="我们做爱吧",
        )

        self.assertTrue(result["private_boundary"])
        self.assertIn("Fixed boundary", result["prompt"])
        self.assertIn("Affinity, intimacy, pressure", result["prompt"])
        non_menstrual = build_group_cycle_boundary(
            enabled=True,
            group_allowed=True,
            cycle_label="处于卵泡期",
            inbound_text="我们做爱吧",
        )
        self.assertFalse(non_menstrual["private_boundary"])


def _load_hook() -> Any:
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PrivateCompanionPlugin")
    hook = next(node for node in owner.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "append_group_cycle_privacy_boundary")
    namespace: dict[str, Any] = {
        "Any": Any,
        "AstrMessageEvent": Any,
        "ProviderRequest": Any,
        # The production hook is tested separately; this loader only executes
        # the selected method body and therefore needs a no-op decorator stub.
        "_multi_persona_event_context": lambda target: target,
        "filter": SimpleNamespace(on_llm_request=lambda **_kwargs: lambda target: target),
        "build_group_cycle_boundary": build_group_cycle_boundary,
        "runtime_persona_setting": lambda host, key, default=None: getattr(host, key, default),
    }
    module = ast.Module(body=[copy.deepcopy(hook)], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(ROOT / "main.py"), "exec"), namespace)
    return namespace["append_group_cycle_privacy_boundary"]


GROUP_CYCLE_HOOK = _load_hook()


class _GroupCycleHost:
    def __init__(self, *, enabled: bool, group_allowed: bool) -> None:
        self.enable_group_cycle_awareness = enabled
        self.group_allowed = group_allowed
        self.data = {"daily_state": {"body_cycle": "处于月经期"}}
        self.fragments: list[tuple[Any, ...]] = []

    def _extract_group_id_from_event(self, _event: object) -> str:
        return "group-a"

    def _group_enabled_for_event(self, _group_id: str) -> bool:
        return self.group_allowed

    def _append_turn_prompt_fragment_by_position(self, *args: Any, **kwargs: Any) -> bool:
        self.fragments.append(args)
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
        prompt = host.fragments[0][2]
        self.assertEqual("<!-- private_companion_group_cycle_boundary_v1 -->", marker)
        self.assertIn("Group cycle privacy boundary", prompt)
        self.assertNotIn("月经会不舒服吗", prompt)
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
        source = ast.get_source_segment((ROOT / "main.py").read_text(encoding="utf-8"), next(
            node for node in ast.walk(ast.parse((ROOT / "main.py").read_text(encoding="utf-8")))
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "append_group_cycle_privacy_boundary"
        )) or ""
        self.assertNotIn("_ensure_daily_state", source)
        self.assertNotIn("_schedule_data_save", source)


if __name__ == "__main__":
    unittest.main()
