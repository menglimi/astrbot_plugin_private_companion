"""Comprehensive unit & adversarial security tests for companion_v2.model_gateway.pricing_expr."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest

from companion_v2.model_gateway.pricing_expr import (
    MAX_AST_DEPTH,
    MAX_EVAL_STEPS,
    PricingExprSyntaxError,
    TieredExprEvaluationError,
    compile_expr,
    eval_compiled_expr,
    evaluate_tiered_expr_rule,
    expr_to_python,
    strip_v1_prefix,
    validate_tiered_expr_rule,
)


class PricingExprStaticSecurityTests(unittest.TestCase):
    def test_module_contains_zero_eval_exec_or_compile_calls(self):
        source_path = Path(__file__).resolve().parents[1] / "companion_v2" / "model_gateway" / "pricing_expr.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        forbidden = {"eval", "exec", "compile", "__import__"}
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in forbidden:
                    found.append(node.func.id)
                elif (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in forbidden
                    and not (isinstance(node.func.value, ast.Name) and node.func.value.id == "re" and node.func.attr == "compile")
                ):
                    found.append(node.func.attr)
        self.assertEqual(found, [], f"Forbidden dynamic execution calls found: {found}")

    def test_v1_prefix_strip_and_preservation(self):
        self.assertEqual(strip_v1_prefix("v1: p * 2 + c * 10 "), "p * 2 + c * 10")
        compiled = compile_expr("v1:len > 200000 ? p * 4 + c * 18 : p * 2 + c * 12")
        self.assertEqual(compiled.original, "v1:len > 200000 ? p * 4 + c * 18 : p * 2 + c * 12")
        self.assertFalse(compiled.stripped.startswith("v1:"))
        self.assertEqual(compiled.used_variables, frozenset({"len", "p", "c"}))

    def test_rejects_dunder_attribute_subscript_and_builtins_attacks(self):
        attack_vectors = [
            "__class__",
            "().__class__.__mro__",
            "p.__class__",
            "__import__('os')",
            "__builtins__",
            "open('/etc/passwd')",
            "eval('1+1')",
            "exec('x=1')",
            "_secret + 1",
            "[1, 2][0]",
            "2 ** 1000000",
            "p ** 2",
            "len('abc')",
            "round(1.5, ndigits=1)",
            "(lambda x: x)(1)",
            "[x for x in [1, 2]]",
            "unknown_var + 1",
        ]
        for expr in attack_vectors:
            with self.subTest(expr=expr):
                with self.assertRaises(PricingExprSyntaxError):
                    compile_expr(expr)

    def test_rejects_unclosed_string_quotes(self):
        for expr in ['tier == "fast', "tier == 'priority", 'tier == "escaped \\"']:
            with self.subTest(expr=expr):
                with self.assertRaises(PricingExprSyntaxError):
                    compile_expr(expr)

    def test_non_ascii_identifiers_rejected_cleanly_while_unicode_string_literals_allowed(self):
        # Non-ASCII chars outside quotes must raise PricingExprSyntaxError (NEVER AssertionError!)
        for bad in ["p * 单价", "价格", "α + 1", "123单价", "p + 中"]:
            with self.subTest(expr=bad):
                with self.assertRaises(PricingExprSyntaxError):
                    compile_expr(bad)
                res = validate_tiered_expr_rule({"expr": bad})
                self.assertFalse(res["valid"])
                self.assertTrue(res["error"])

        # Unicode/Chinese string literals inside quotes remain valid!
        rule = {"mode": "tiered_expr", "expr": 'model == "测试模型" ? p * 3 + c * 9 : p * 1 + c * 2'}
        cost_match, _ = evaluate_tiered_expr_rule(rule, other_input_tokens=1000, output_tokens=100, model="测试模型")
        self.assertEqual(cost_match, 3900.0)
        cost_other, _ = evaluate_tiered_expr_rule(rule, other_input_tokens=1000, output_tokens=100, model="other")
        self.assertEqual(cost_other, 1200.0)

    def test_rejects_deep_recursion_and_nesting_above_depth_cap(self):
        deep_parens = "(" * (MAX_AST_DEPTH + 10) + "p" + ")" * (MAX_AST_DEPTH + 10)
        with self.assertRaises(PricingExprSyntaxError):
            compile_expr(deep_parens)

        deep_chain = " + ".join(["1"] * (MAX_AST_DEPTH + 10))
        with self.assertRaises(PricingExprSyntaxError):
            compile_expr(deep_chain)

        deep_ternary = " ? 1 : ".join(["p > 0"] * (MAX_AST_DEPTH + 5)) + " ? 1 : 0"
        with self.assertRaises(PricingExprSyntaxError):
            compile_expr(deep_ternary)

        deep_bang = "!" * (MAX_AST_DEPTH + 5) + "true"
        with self.assertRaises(PricingExprSyntaxError):
            compile_expr(deep_bang)

    def test_rejects_str_mul_int_bool_arithmetic_and_magnitude_cap(self):
        # Compile-time magnitude literal > 1e18 rejected
        with self.assertRaises(PricingExprSyntaxError):
            compile_expr("1e19")
        with self.assertRaises(PricingExprSyntaxError):
            compile_expr("99999999999999999999")

        # Runtime str * int, int * str, list * int rejected
        for expr in ['"a" * 99999999', '99999999 * "a"', '[1] * 99999999', "true * 5", "true + 1"]:
            with self.subTest(expr=expr):
                compiled = compile_expr(expr)
                with self.assertRaises(TieredExprEvaluationError):
                    eval_compiled_expr(compiled, {"p": 1, "c": 1})

        # Runtime overflow above 1e18 rejected before ceil/floor
        compiled_mul = compile_expr("1e10 * 1e10")
        with self.assertRaises(TieredExprEvaluationError):
            eval_compiled_expr(compiled_mul, {})

        compiled_ceil = compile_expr("ceil(1e18 + 1e18)")
        with self.assertRaises(TieredExprEvaluationError):
            eval_compiled_expr(compiled_ceil, {})

    def test_enforces_runtime_step_budget(self):
        # Build a compact, shallow expression (< 4096 chars, < 1000 AST nodes, depth <= 6)
        # that evaluates > MAX_EVAL_STEPS (512) AST nodes at runtime.
        chunks = [
            "max(" + ",".join(f"p+{i}" for i in range(base, base + 20)) + ")"
            for base in range(0, 180, 20)
        ]
        expr = "max(" + ",".join(chunks) + ")"
        compiled = compile_expr(expr)
        with self.assertRaises(TieredExprEvaluationError):
            eval_compiled_expr(compiled, {"p": 10})
        self.assertEqual(MAX_EVAL_STEPS, 512)


class PricingExprEvaluationTests(unittest.TestCase):
    def test_opt_in_variable_normalization_p_absorbs_unreferenced_cache_vars(self):
        # Expression only references p and c -> p absorbs cr, cc, cc1h!
        rule_simple = {"mode": "tiered_expr", "expr": "v1: p * 2.0 + c * 10.0"}
        microusd, matched = evaluate_tiered_expr_rule(
            rule_simple,
            other_input_tokens=10_000,
            cached_input_tokens=50_000,
            cache_creation_tokens=20_000,
            cache_creation_1h_tokens=5_000,
            output_tokens=1_000,
        )
        # p = 10k + 50k + 20k + 5k = 85k -> 85k * 2 + 1k * 10 = 180_000 microUSD
        self.assertAlmostEqual(microusd, 180_000.0, places=4)
        self.assertEqual(matched, "expr")

        # Expression references cr and cc -> p absorbs only unreferenced cc1h
        rule_cache = {"mode": "tiered_expr", "expr": "p * 3.0 + cr * 0.3 + cc * 3.75 + c * 15.0"}
        microusd_c, _ = evaluate_tiered_expr_rule(
            rule_cache,
            other_input_tokens=10_000,
            cached_input_tokens=50_000,
            cache_creation_tokens=20_000,
            cache_creation_1h_tokens=5_000,
            output_tokens=1_000,
        )
        # p = 10k + 5k = 15k -> 15k*3 + 50k*0.3 + 20k*3.75 + 1k*15 = 45000 + 15000 + 75000 + 15000 = 150000
        self.assertAlmostEqual(microusd_c, 150_000.0, places=4)

        # Expression references len (> 200k triggers high tier) while p absorbs cr/cc
        rule_len = {"mode": "tiered_expr", "expr": "v1:len > 200000 ? p * 4 + c * 18 : p * 2 + c * 12"}
        short_cost, _ = evaluate_tiered_expr_rule(
            rule_len, other_input_tokens=50_000, cached_input_tokens=50_000, output_tokens=1_000
        )
        self.assertAlmostEqual(short_cost, 100_000 * 2 + 1_000 * 12, places=4)
        long_cost, _ = evaluate_tiered_expr_rule(
            rule_len, other_input_tokens=120_000, cached_input_tokens=100_000, output_tokens=1_000
        )
        # len = 220k > 200k -> p=220k * 4 + 1k * 18 = 898_000
        self.assertAlmostEqual(long_cost, 898_000.0, places=4)

    def test_tiers_matching_operators_builtins_and_cluster_multiplier(self):
        rule = {
            "mode": "tiered_expr",
            "tiers": [
                {
                    "name": "priority-long",
                    "condition": 'tier in ["fast", "priority"] && len > 200000',
                    "expr": "ceil(p * 6.0 + cr * 0.6 + c * 30.0)",
                },
                {
                    "name": "not-batch",
                    "condition": '!(tier == "batch") && len > 200000',
                    "expr": "p * 4.0 + cr * 0.4 + c * 18.0",
                },
            ],
            "default_expr": "p * 2.0 + cr * 0.2 + c * 12.0",
            "cluster_multiplier": 0.5,
        }
        cost_fast, tier_fast = evaluate_tiered_expr_rule(
            rule, other_input_tokens=150_000, cached_input_tokens=100_000, output_tokens=2_000, service_tier="priority"
        )
        self.assertEqual(tier_fast, "priority-long")
        expected = (150_000 * 6.0 + 100_000 * 0.6 + 2_000 * 30.0) * 0.5
        self.assertAlmostEqual(cost_fast, expected, places=4)

        cost_norm, tier_norm = evaluate_tiered_expr_rule(
            rule, other_input_tokens=150_000, cached_input_tokens=100_000, output_tokens=2_000, service_tier="standard"
        )
        self.assertEqual(tier_norm, "not-batch")
        self.assertAlmostEqual(cost_norm, (150_000 * 4.0 + 100_000 * 0.4 + 2_000 * 18.0) * 0.5, places=4)

        cost_def, tier_def = evaluate_tiered_expr_rule(
            rule, other_input_tokens=10_000, cached_input_tokens=5_000, output_tokens=1_000, service_tier="batch"
        )
        self.assertEqual(tier_def, "default")
        self.assertAlmostEqual(cost_def, (10_000 * 2.0 + 5_000 * 0.2 + 1_000 * 12.0) * 0.5, places=4)

    def test_short_circuit_ternary_and_never_silent_zero_on_error(self):
        # Short-circuit prevents division by zero when p == 0
        rule_safe = {"mode": "tiered_expr", "expr": "p > 0 ? (c / p) * 1000 : 0"}
        cost, _ = evaluate_tiered_expr_rule(rule_safe, other_input_tokens=0, output_tokens=100)
        self.assertEqual(cost, 0.0)

        # Division by zero raises TieredExprEvaluationError (never silent 0!)
        with self.assertRaises(TieredExprEvaluationError):
            evaluate_tiered_expr_rule({"mode": "tiered_expr", "expr": "p / 0"}, other_input_tokens=100)

        # Negative result raises TieredExprEvaluationError
        with self.assertRaises(TieredExprEvaluationError):
            evaluate_tiered_expr_rule({"mode": "tiered_expr", "expr": "p * -1"}, other_input_tokens=100)

        # Unmatched tier with no default raises TieredExprEvaluationError
        with self.assertRaises(TieredExprEvaluationError):
            evaluate_tiered_expr_rule(
                {"mode": "tiered_expr", "tiers": [{"name": "only-huge", "condition": "len > 999999", "expr": "p * 2"}]},
                other_input_tokens=100,
            )

    def test_validate_tiered_expr_rule_returns_usd_and_cny_previews(self):
        res = validate_tiered_expr_rule(
            {"expr": "v1:len > 200000 ? p * 4 + c * 16 : p * 2 + c * 8"},
            usd_cny_rate=7.25,
            sample={"p": 100_000, "c": 10_000},
        )
        self.assertTrue(res["valid"])
        self.assertEqual(res["used_variables"], ["c", "len", "p"])
        self.assertEqual(len(res["samples"]), 4)
        custom = res["samples"][0]
        # 100k * 2 + 10k * 8 = 280_000 microUSD = $0.28 -> CNY 2.03
        self.assertEqual(custom["cost_microusd"], 280_000)
        self.assertAlmostEqual(custom["cost_usd"], 0.28, places=6)
        self.assertAlmostEqual(custom["cost_cny"], 0.28 * 7.25, places=6)

        bad = validate_tiered_expr_rule({"expr": "p / 0"})
        self.assertFalse(bad["valid"])
        self.assertTrue(bad["error"])


if __name__ == "__main__":
    unittest.main()
