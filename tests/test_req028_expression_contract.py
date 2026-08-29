from __future__ import annotations

from copy import deepcopy
import ast
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion_interaction_expression import (  # noqa: E402
    EXPRESSION_CONTRACT_VERSION,
    ExpressionBand,
    ExpressionDecision,
    ExpressionInput,
    allowed_expression_bands,
    build_expression_decision,
    current_interaction_projection,
    expression_decision_prompt,
    resolve_expression_decision,
)


class Req028ExpressionContractTests(unittest.TestCase):
    def test_all_seven_bands_are_supported_for_owner_exclusive(self) -> None:
        for band in ExpressionBand:
            with self.subTest(band=band.value):
                decision = build_expression_decision(
                    {
                        "relationship_role": "owner",
                        "relationship_mode": "owner_exclusive",
                        "current_interaction": band.value,
                        "administrator_override": band.value,
                    }
                )
                self.assertEqual(band.value, decision.expression_band)
                self.assertIsNone(decision.blocker)

    def test_owner_only_bands_are_downgraded_for_regular_users(self) -> None:
        for band in ("close", "affectionate"):
            with self.subTest(band=band):
                decision = build_expression_decision(
                    {
                        "relationship_role": "friend",
                        "relationship_mode": "owner_exclusive",
                        "current_interaction": band,
                    }
                )
                self.assertEqual("warm", decision.expression_band)
                self.assertIn("owner_role_required", decision.reason_codes)

    def test_owner_role_can_use_owner_only_interaction_without_exclusive_mode(self) -> None:
        decision = build_expression_decision(
            {"relationship_role": "owner", "relationship_mode": "normal", "current_interaction": "affectionate"}
        )
        self.assertEqual("affectionate", decision.expression_band)
        self.assertNotIn("owner_role_required", decision.reason_codes)

    def test_p4_and_contact_boundaries_precede_administrator_override(self) -> None:
        blocked = build_expression_decision(
            {
                "relationship_role": "owner",
                "relationship_mode": "owner_exclusive",
                "administrator_override": "affectionate",
                "safety_constraints": {"contact_boundary": True, "p4_blocked": True},
            }
        )
        self.assertEqual("p4_safety", blocked.blocker)
        self.assertEqual("p4_blocked", blocked.safety_mode)
        self.assertEqual("avoidant", blocked.expression_band)
        self.assertEqual(0, blocked.proactive_budget)

        boundary = build_expression_decision(
            {
                "relationship_role": "owner",
                "relationship_mode": "owner_exclusive",
                "administrator_override": "affectionate",
                "safety_constraints": {"contact_boundary": {"active": True}},
            }
        )
        self.assertEqual("contact_boundary", boundary.blocker)
        self.assertEqual("avoidant", boundary.expression_band)

        passive_reply = build_expression_decision(
            {
                "relationship_role": "owner",
                "relationship_mode": "owner_exclusive",
                "administrator_override": "affectionate",
                "proactive_candidate": {"eligible": True, "daily_allowance": 5},
                "safety_constraints": {
                    "contact_boundary": {"active": True},
                    "passive_reengagement": True,
                },
            }
        )
        self.assertIsNone(passive_reply.blocker)
        self.assertEqual("contact_boundary_passive", passive_reply.safety_mode)
        self.assertEqual("avoidant", passive_reply.expression_band)
        self.assertEqual(0, passive_reply.proactive_budget)
        self.assertFalse(passive_reply.followup)
        self.assertIn("contact_boundary_passive_reengagement", passive_reply.reason_codes)

        capped = build_expression_decision(
            {
                "relationship_role": "owner",
                "relationship_mode": "owner_exclusive",
                "current_interaction": "affectionate",
                "safety_constraints": {"p4_warmth_cap": "neutral"},
            }
        )
        self.assertEqual("relaxed", capped.expression_band)
        self.assertIn("p4_warmth_cap_applied", capped.reason_codes)

    def test_is_deterministic_and_has_no_input_mutation_or_persistence_effect(self) -> None:
        payload = {
            "relationship_score": 700,
            "relationship_role": "owner",
            "relationship_mode": "owner_exclusive",
            "current_interaction": {"band": "close"},
            "bot_state": {"energy": 88},
            "schedule": {"mode": "free"},
            "message_intent": {"kind": "question"},
            "proactive_candidate": {"eligible": True, "daily_allowance": 3},
        }
        before = deepcopy(payload)
        first = build_expression_decision(payload)
        second = resolve_expression_decision(payload)
        self.assertEqual(first, second)
        self.assertEqual(before, payload)
        self.assertIsInstance(first, ExpressionDecision)
        self.assertEqual(EXPRESSION_CONTRACT_VERSION, first.contract)
        self.assertEqual(3, first.proactive_budget)
        self.assertEqual("allowed", first.initiative)
        self.assertEqual("close", first.expression_band)

    def test_invalid_or_maliciously_shaped_input_fails_safe_without_echoing_it(self) -> None:
        marker = "PRIVATE_CONTEXT_DO_NOT_ECHO"
        decision = build_expression_decision(
            {
                "relationship_score": object(),
                "relationship_role": ["owner"],
                "relationship_mode": {"unexpected": "owner_exclusive"},
                "current_interaction": {"band": marker},
                "bot_state": "not-a-mapping",
                "schedule": [marker],
                "message_intent": marker,
                "proactive_candidate": {"eligible": "yes", "daily_allowance": 99},
                "safety_constraints": marker,
                "administrator_override": {"band": marker},
            }
        )
        self.assertEqual("relaxed", decision.expression_band)
        self.assertEqual(0, decision.proactive_budget)
        self.assertNotIn(marker, repr(decision))
        self.assertEqual("passive_only", decision.initiative)

    def test_typed_input_is_accepted_and_decision_projection_is_detached(self) -> None:
        decision = build_expression_decision(
            ExpressionInput(current_interaction="warm", message_intent={"followup_allowed": False})
        )
        projection = decision.to_dict()
        projection["reason_codes"] = ("changed_only_in_projection",)
        self.assertEqual("warm", decision.expression_band)
        self.assertFalse(decision.followup)
        self.assertIn("intent_followup_suppressed", decision.reason_codes)
        self.assertNotIn("changed_only_in_projection", decision.reason_codes)

    def test_long_term_relationship_remains_the_baseline_when_interaction_is_relaxed(self) -> None:
        decision = build_expression_decision(
            {
                "relationship_score": 900,
                "relationship_role": "friend",
                "current_interaction": "relaxed",
                "relationship_baseline": {
                    "tone": "亲密、柔软、不过度黏人",
                    "address_level": "使用双方已确认的昵称",
                    "proactive_care_limit": 2,
                    "soft_behaviors": {"allow_followup": True},
                },
                "proactive_candidate": {"eligible": True, "daily_allowance": 5},
            }
        )
        self.assertEqual("warm", decision.expression_band)
        self.assertEqual("亲密、柔软、不过度黏人", decision.tone)
        self.assertEqual("使用双方已确认的昵称", decision.address_style)
        self.assertEqual(5, decision.proactive_budget)
        self.assertEqual(2, decision.proactive_target)
        self.assertIn("relationship_baseline_retained", decision.reason_codes)
        self.assertIn("relationship_proactive_soft_target", decision.reason_codes)

    def test_owner_exclusive_relationship_is_fixed_baseline_but_manual_interaction_can_adjust_it(self) -> None:
        baseline = build_expression_decision(
            {
                "relationship_role": "owner",
                "relationship_mode": "owner_exclusive",
                "current_interaction": "relaxed",
            }
        )
        adjusted = build_expression_decision(
            {
                "relationship_role": "owner",
                "relationship_mode": "owner_exclusive",
                "current_interaction": "relaxed",
                "administrator_override": "relaxed",
            }
        )
        self.assertEqual("close", baseline.expression_band)
        self.assertEqual("relaxed", adjusted.expression_band)
        self.assertIn("administrator_override_applied", adjusted.reason_codes)

    def test_bot_state_and_schedule_change_the_single_expression_decision(self) -> None:
        baseline = build_expression_decision(
            {
                "current_interaction": "warm",
                "bot_state": {"energy": 80, "mood": "轻快"},
                "proactive_candidate": {"eligible": True, "daily_allowance": 3},
            }
        )
        constrained = build_expression_decision(
            {
                "current_interaction": "warm",
                "bot_state": {"energy": 18, "mood": "疲惫低落"},
                "schedule": {"label": "忙碌会议"},
                "proactive_candidate": {"eligible": True, "daily_allowance": 3},
            }
        )
        self.assertGreater(baseline.warmth, constrained.warmth)
        self.assertTrue(baseline.followup)
        self.assertFalse(constrained.followup)
        self.assertEqual("brief", constrained.response_length)
        self.assertEqual(0, constrained.proactive_budget)
        self.assertIn("low_energy_expression_cap", constrained.reason_codes)
        self.assertIn("down_mood_expression_cap", constrained.reason_codes)
        self.assertIn("schedule_proactive_suppressed", constrained.reason_codes)

    def test_page_projection_gates_owner_only_bands_and_expires_manual_override(self) -> None:
        friend = current_interaction_projection(
            {"expression_band": "affectionate", "source": "manual", "updated_at": 10},
            relationship_role="friend",
        )
        owner = current_interaction_projection(
            {"expression_band": "affectionate", "source": "manual", "expires_at": 20},
            relationship_role="owner",
            relationship_mode="owner_exclusive",
            now=30,
        )
        self.assertEqual("warm", friend["expression_band"])
        self.assertEqual(5, len(allowed_expression_bands("friend", "normal")))
        self.assertEqual(7, len(allowed_expression_bands("owner", "normal")))
        self.assertEqual("relaxed", owner["expression_band"])
        self.assertFalse(owner["manual_override"])

    def test_prompt_is_bounded_and_uses_single_decision(self) -> None:
        decision = build_expression_decision({"current_interaction": "lively"})
        prompt = expression_decision_prompt(decision)
        self.assertIn("活泼", prompt)
        self.assertNotIn("relationship_score", prompt)

    def test_legacy_relationship_fields_are_compatibility_only_for_expression(self) -> None:
        source = (ROOT / "user_memory.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "UserMemoryMixin")
        profile_method = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == "_relationship_profile")
        constants = {node.value for node in ast.walk(profile_method) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        self.assertNotIn("persona_relationship", constants)
        default_source = (ROOT / "constants.py").read_text(encoding="utf-8")
        self.assertNotIn('"persona_relationship": {}', default_source)
        self.assertNotIn('"relationship_state": {}', default_source)
        self.assertIn("current_interaction_projection", ast.unparse(profile_method))

        expression_context = next(
            node
            for node in owner.body
            if isinstance(node, ast.FunctionDef) and node.name == "_expression_companion_context"
        )
        context_constants = {
            node.value
            for node in ast.walk(expression_context)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        self.assertNotIn("relationship_state", context_constants)
        self.assertNotIn("persona_relationship", context_constants)

    def test_proactive_contact_boundary_is_independent_of_legacy_state_toggle(self) -> None:
        source = (ROOT / "proactive.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ProactiveMixin")
        method = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == "_current_relationship_gate_mode")
        constants = {node.value for node in ast.walk(method) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        self.assertNotIn("enable_relationship_state_machine", constants)
        module = ast.Module(
            body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), deepcopy(method)],
            type_ignores=[],
        )
        namespace: dict[str, object] = {
            "_single_line": lambda value, _limit=0: value.strip() if isinstance(value, str) else "",
        }
        exec(compile(ast.fix_missing_locations(module), str(ROOT / "proactive.py"), "exec"), namespace)
        gate = namespace["_current_relationship_gate_mode"]
        host = type(
            "Host",
            (),
            {
                "enable_relationship_state_machine": False,
                "_relationship_proactive_temperature": lambda *_args, **_kwargs: {
                    "expression_decision": {"blocker": "contact_boundary"}
                },
            },
        )()
        self.assertEqual("backoff", gate(host, {"contact_preference": {"no_contact": True}}))

    def test_proactive_chat_bridge_preflight_consumes_expression_budget_and_boundary(self) -> None:
        source = (ROOT / "proactive_message.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ProactiveMessageMixin")
        method = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == "_proactive_chat_bridge_preflight_block_reason")
        constants = {node.value for node in ast.walk(method) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        self.assertIn("_build_expression_decision_for_user", constants)
        self.assertIn("expression_proactive_budget_exhausted", constants)
        self.assertIn("expression_contact_boundary", constants)
        module = ast.Module(
            body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), deepcopy(method)],
            type_ignores=[],
        )

        def safe_int(value: object, default: int = 0, *_args: object) -> int:
            try:
                return int(value) if not isinstance(value, bool) else default
            except (TypeError, ValueError):
                return default

        namespace = {
            "_safe_float": lambda value, default=0.0: float(value) if isinstance(value, (int, float)) else default,
            "_safe_int": safe_int,
            "_single_line": lambda value, _limit=0: value.strip() if isinstance(value, str) else "",
        }
        exec(compile(ast.fix_missing_locations(module), str(ROOT / "proactive_message.py"), "exec"), namespace)
        preflight = namespace["_proactive_chat_bridge_preflight_block_reason"]

        class Host:
            def _is_quiet_time(self) -> bool:
                return False

            def _can_send_insomnia_night_message(self, _user: object) -> bool:
                return False

            def _current_relationship_gate_mode(self, _user: object, *, now: float) -> str:
                return ""

            def _current_emotion_gate_mode(self, _user: object, *, now: float) -> str:
                return ""

            def _effective_user_daily_limit(self, _user: object) -> int:
                return 3

            def _proactive_daily_limit_is_unlimited(self, _limit: int) -> bool:
                return False

            def _reset_daily_counter_if_needed(self, _user: object) -> None:
                return None

            def _build_expression_decision_for_user(self, _user: object, **_kwargs: object) -> dict[str, object]:
                return {"proactive_budget": 3, "blocker": None}

        host = Host()
        self.assertEqual("expression_proactive_budget_exhausted", preflight(host, {"sent_today": 3}, now=100.0))
        host._current_relationship_gate_mode = lambda _user, *, now: "backoff"  # type: ignore[method-assign]
        self.assertEqual("expression_contact_boundary", preflight(host, {"sent_today": 0}, now=100.0))

    def test_page_dto_marks_legacy_relationship_state_read_only(self) -> None:
        page_source = (ROOT / "page_api.py").read_text(encoding="utf-8")
        users_source = (ROOT / "page_api_users_groups.py").read_text(encoding="utf-8")

        self.assertIn('"current_interaction": interaction', page_source)
        self.assertIn('"expression_decision": expression', page_source)
        self.assertIn('detail["current_interaction"] = relationship_panel["current_interaction"]', users_source)
        self.assertIn('detail["expression_decision"] = relationship_panel["expression_decision"]', users_source)

        for source in (page_source, users_source):
            tree = ast.parse(source)
            for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
                if not isinstance(call.func, ast.Name) or call.func.id != "current_interaction_projection":
                    continue
                self.assertTrue(call.args)
                first_arg = call.args[0]
                self.assertFalse(
                    isinstance(first_arg, ast.BoolOp) and isinstance(first_arg.op, ast.Or),
                    "current_interaction must not fall back to legacy relationship_state",
                )

    def test_runtime_expression_projections_never_fall_back_to_legacy_state(self) -> None:
        runtime_files = (
            "proactive.py",
            "proactive_engine.py",
            "proactive_message.py",
            "reading_archive.py",
            "main.py",
            "user_memory.py",
        )
        for filename in runtime_files:
            with self.subTest(filename=filename):
                tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
                for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
                    if not isinstance(call.func, ast.Name) or call.func.id != "current_interaction_projection":
                        continue
                    self.assertTrue(call.args)
                    self.assertFalse(
                        isinstance(call.args[0], ast.BoolOp) and isinstance(call.args[0].op, ast.Or),
                        "current_interaction must not fall back to relationship_state",
                    )

    def test_expression_context_has_no_legacy_state_consumer_or_sync_adapter(self) -> None:
        source = (ROOT / "user_memory.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "UserMemoryMixin")
        methods = {
            node.name: node
            for node in owner.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        context = methods["_expression_companion_context"]
        context_constants = {
            node.value for node in ast.walk(context) if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        self.assertNotIn("relationship_state", context_constants)
        self.assertNotIn("_sync_current_interaction_from_legacy", methods)


if __name__ == "__main__":
    unittest.main()
