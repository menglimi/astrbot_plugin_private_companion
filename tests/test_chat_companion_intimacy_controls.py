from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path
import re
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from relationship_policy import relationship_stage_for_score


def _class_method(path: Path, class_name: str, method_name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for src in [path, *(p for p in sorted(ROOT.glob("page_api_*.py")) if p != path)]:
        try:
            tree = ast.parse(src.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for owner in tree.body:
            if not isinstance(owner, ast.ClassDef):
                continue
            try:
                node = deepcopy(next(node for node in owner.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name))
            except StopIteration:
                continue
            # 标记来源文件：拆分后方法可能已搬离宿主到 page_api_*.py，
            # get_source_segment 必须按实际所在文件取源码（先例同 test_relationship_system_pr）。
            node._src_path = src  # type: ignore[attr-defined]
            return node
    raise AssertionError(f"method not found in page_api family: {method_name}")


def _compile_static_method(path: Path, class_name: str, method_name: str) -> object:
    method = _class_method(path, class_name, method_name)
    module = ast.Module(
        body=[
            ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
            ast.ClassDef(name="Probe", bases=[], keywords=[], body=[method], decorator_list=[]),
        ],
        type_ignores=[],
    )
    namespace = {"re": re}
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return getattr(namespace["Probe"], method_name)


class ChatCompanionIntimacyControlTests(unittest.TestCase):
    def test_projection_is_bounded_and_has_the_public_contract(self) -> None:
        projection_node = _class_method(
            ROOT / "page_api.py",
            "PrivateCompanionPageApi",
            "_relationship_intimacy_projection",
        )
        # 拆分后方法可能已搬离宿主，按实际所在文件取源码（先例：test_relationship_system_pr）
        projection_source = ast.get_source_segment(
            projection_node._src_path.read_text(encoding="utf-8"),
            projection_node,
        ) or ""
        self.assertIn("relationship_stage_for_score", projection_source)
        cases = {
            -1200: ("deeply_distant", "极度疏离"),
            -801: ("deeply_distant", "极度疏离"),
            -800: ("strongly_distant", "强烈疏离"),
            -400: ("distant", "疏离"),
            0: ("acquaintance", "初识"),
            200: ("familiar", "熟悉"),
            600: ("close", "亲近"),
            900: ("intimate", "亲密"),
            1200: ("deeply_bonded", "深度联结"),
        }
        for value, (phase_key, phase_label) in cases.items():
            with self.subTest(value=value):
                result = relationship_stage_for_score(value)
                self.assertTrue(
                    {"value", "band", "phase", "min", "max", "trend"}.issubset(result)
                )
                self.assertEqual(value, result["value"])
                self.assertEqual(phase_label, result["band"])
                self.assertEqual(phase_key, result["phase"]["key"])
                self.assertEqual(phase_label, result["phase"]["label"])
                self.assertEqual(-1200, result["min"])
                self.assertEqual(1200, result["max"])
                self.assertEqual("unknown", result["trend"])
        self.assertEqual(-1200, relationship_stage_for_score(-99999)["value"])
        self.assertEqual(1200, relationship_stage_for_score(99999)["value"])

    def test_manual_input_accepts_only_range_limited_integers(self) -> None:
        parser = _compile_static_method(
            ROOT / "page_api_users_groups.py",
            "PrivateCompanionPageApiUsersGroupsMixin",
            "_relationship_score_input",
        )
        for raw, expected in ((-1200, -1200), (0, 0), (1200, 1200), (" +42 ", 42)):
            with self.subTest(raw=raw):
                self.assertEqual(expected, parser(raw))
        for raw in (True, False, 12.0, "", "  ", "1.0", "1e2", "1201", "-1201"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    parser(raw)

    def test_update_contract_rejects_duplicate_fields_and_writes_legacy_score_inside_lock(self) -> None:
        path = ROOT / "page_api_users_groups.py"
        update_user = _class_method(path, "PrivateCompanionPageApiUsersGroupsMixin", "update_user")
        constants = {node.value for node in ast.walk(update_user) if isinstance(node, ast.Constant) and type(node.value) is str}
        self.assertTrue({"companion_intimacy", "relationship_score"}.issubset(constants))
        self.assertTrue({"person_id", "candidate_id", "attestation", "ledger", "affinity", "confinement"}.isdisjoint(constants))

        locks = [
            node for node in ast.walk(update_user)
            if isinstance(node, ast.AsyncWith)
            and any(
                isinstance(item.context_expr, ast.Attribute)
                and item.context_expr.attr == "_data_lock"
                for item in node.items
            )
        ]
        self.assertEqual(1, len(locks))
        writes = [
            node for node in ast.walk(update_user)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and target.value.id == "user"
                and isinstance(target.slice, ast.Constant)
                and target.slice.value == "relationship_score"
                for target in node.targets
            )
        ]
        self.assertEqual(1, len(writes))
        self.assertGreater(writes[0].lineno, locks[0].lineno)
        self.assertLessEqual(writes[0].end_lineno or writes[0].lineno, locks[0].end_lineno or locks[0].lineno)

    def test_summary_reuses_the_safe_relationship_panel_projection(self) -> None:
        path = ROOT / "page_api.py"
        summary = _class_method(path, "PrivateCompanionPageApi", "_user_summary")
        panel = _class_method(path, "PrivateCompanionPageApi", "_relationship_panel")
        summary_calls = [
            node for node in ast.walk(summary)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_relationship_panel"
        ]
        panel_calls = [
            node for node in ast.walk(panel)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_relationship_intimacy_projection"
        ]
        self.assertEqual(1, len(summary_calls))
        self.assertEqual(1, len(panel_calls))
        for function in (summary, panel):
            constants = {node.value for node in ast.walk(function) if isinstance(node, ast.Constant) and type(node.value) is str}
            self.assertIn("relationship_intimacy", constants)
            self.assertTrue(
                {"person_id", "candidate_id", "attestation", "confinement"}.isdisjoint(constants),
                function.name,
            )

    def test_frontend_uses_number_input_and_the_dedicated_update_field(self) -> None:
        source = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
        editor_start = source.index("function renderRelationshipStatus")
        panel_start = source.index("async function renderUserDetail", editor_start)
        editor = source[editor_start:panel_start]
        bind_start = source.index("function bindUserActions")
        bind_end = source.index("function renderGroups", bind_start)
        bindings = source[bind_start:bind_end]
        self.assertIn('type="number"', editor)
        self.assertIn('name="companion_intimacy"', editor)
        self.assertIn("亲密度与互动表达", editor)
        self.assertIn("精确调整亲密度", editor)
        self.assertNotIn('type="range"', editor)
        self.assertIn('postJson("/user/update", { user_id: detail.user_id, companion_intimacy: value })', bindings)
        self.assertIn("Number.isInteger(value)", bindings)
        self.assertIn("value < -1200 || value > 1200", bindings)

    def test_p4_page_status_is_fixed_and_cannot_resolve_a_user(self) -> None:
        projection = _compile_static_method(
            ROOT / "page_api.py",
            "PrivateCompanionPageApi",
            "_p4_page_status_projection",
        )
        status = projection()
        self.assertEqual(
            {
                "schema_version",
                "scope",
                "reply_gate",
                "warmth",
                "confinement",
                "manual_review",
                "action_available",
            },
            set(status),
        )
        self.assertEqual("chat_event_only", status["scope"])
        self.assertFalse(status["action_available"])
        self.assertEqual("not_migrated", status["manual_review"])

        get_user = _class_method(ROOT / "page_api_users_groups.py", "PrivateCompanionPageApiUsersGroupsMixin", "get_user")
        calls = [
            node for node in ast.walk(get_user)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_p4_page_status_projection"
        ]
        self.assertEqual(1, len(calls))
        self.assertEqual([], calls[0].args)
        constants = {node.value for node in ast.walk(get_user) if isinstance(node, ast.Constant) and type(node.value) is str}
        self.assertTrue({"person_id", "ledger", "candidate_id", "attestation"}.isdisjoint(constants))

        source = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
        start = source.index("function renderUserP4RuntimeStatus")
        end = source.index("function renderPrivateDeliveryRoute", start)
        panel = source[start:end]
        self.assertIn("页面不读取个人黑屋状态", panel)
        self.assertIn("页面不能审核", panel)
        for forbidden in ("approve", "reject", "release", "candidate_id", "attestation", "ledger"):
            self.assertNotIn(forbidden, panel)


if __name__ == "__main__":
    unittest.main()
