from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE_API = ROOT / "page_api.py"
USERS_GROUPS_API = ROOT / "page_api_users_groups.py"
SAVE_METHODS = {"_save_data_sync", "_save_data_now_sync", "_schedule_data_save"}


def _function(path: Path, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    )


def _direct_save_calls(node: ast.AST) -> list[ast.Call]:
    return [
        item
        for item in ast.walk(node)
        if isinstance(item, ast.Call)
        and isinstance(item.func, ast.Attribute)
        and item.func.attr in SAVE_METHODS
    ]


def _literal_sections(call: ast.Call) -> set[str] | None:
    keyword = next((item for item in call.keywords if item.arg == "sections"), None)
    if keyword is None or not isinstance(keyword.value, (ast.Set, ast.List, ast.Tuple)):
        return None
    if not all(
        isinstance(item, ast.Constant) and type(item.value) is str
        for item in keyword.value.elts
    ):
        return None
    return {str(item.value) for item in keyword.value.elts}


def _single_literal_save_sections(path: Path, function_name: str) -> set[str]:
    calls = _direct_save_calls(_function(path, function_name))
    literal = [sections for call in calls if (sections := _literal_sections(call)) is not None]
    if len(literal) != 1:
        raise AssertionError(
            f"expected one literal save in {path.name}:{function_name}, got {literal}"
        )
    return literal[0]


class PageApiIncrementalPersistenceTests(unittest.TestCase):
    def test_page_read_endpoints_do_not_repair_or_persist_live_state(self) -> None:
        overview = ast.unparse(_function(PAGE_API, "get_overview"))
        expression_library = ast.unparse(_function(PAGE_API, "get_expression_library"))

        for source in (overview, expression_library):
            self.assertNotIn("_save_data_sync", source)
            self.assertNotIn("_schedule_data_save", source)
        self.assertNotIn("_refresh_sleep_runtime_state", overview)
        self.assertNotIn("_expression_voice_profile", overview)

    def test_indirect_page_savers_declare_sections(self) -> None:
        failures: list[str] = []
        for path in (PAGE_API, USERS_GROUPS_API):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for function in (
                item
                for item in ast.walk(tree)
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            ):
                aliases = {
                    target.id
                    for assignment in ast.walk(function)
                    if isinstance(assignment, ast.Assign)
                    and isinstance(assignment.value, ast.Call)
                    and isinstance(assignment.value.func, ast.Name)
                    and assignment.value.func.id == "getattr"
                    and len(assignment.value.args) >= 2
                    and isinstance(assignment.value.args[1], ast.Constant)
                    and assignment.value.args[1].value in SAVE_METHODS
                    for target in assignment.targets
                    if isinstance(target, ast.Name)
                }
                for call in ast.walk(function):
                    if not (
                        isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Name)
                        and call.func.id in aliases
                    ):
                        continue
                    if not any(
                        keyword.arg in {"sections", "deleted_sections", "full_scope"}
                        for keyword in call.keywords
                    ):
                        failures.append(f"{path.name}:{function.name}:{call.lineno}")
        self.assertEqual([], failures)

    def test_fixed_page_mutations_use_their_owned_sections(self) -> None:
        expected = {
            "bulk_update_food_menu": {"food_menu"},
            "update_external_ability": {"external_proactive_abilities"},
            "update_creative_project": {"creative_projects"},
            "reanalyze_creative_project": {"creative_projects"},
            "delete_creative_project": {"creative_projects"},
        }
        for function_name, sections in expected.items():
            with self.subTest(function=function_name):
                self.assertEqual(
                    sections,
                    _single_literal_save_sections(PAGE_API, function_name),
                )

    def test_user_and_group_mutations_do_not_mark_unrelated_identity_sections(self) -> None:
        expected = {
            "list_users": {"users"},
            "link_unified_identity": {"unified_person"},
            "unlink_unified_identity": {"unified_person"},
            "_refresh_group_names_from_platform": {"groups"},
            "update_group_member_safety": {"groups"},
            "update_group_slang": {"groups"},
        }
        for function_name, sections in expected.items():
            with self.subTest(function=function_name):
                self.assertEqual(
                    sections,
                    _single_literal_save_sections(USERS_GROUPS_API, function_name),
                )

    def test_dynamic_user_update_tracks_secondary_sections(self) -> None:
        source = ast.unparse(_function(USERS_GROUPS_API, "update_user"))

        self.assertIn("save_sections = {'users'}", source)
        self.assertIn("save_sections.add('expression_voice_profile')", source)
        self.assertIn("save_sections.add('unified_person')", source)
        self.assertIn("save_sections.add('_req041_private_memory')", source)
        self.assertIn("_save_data_sync(sections=save_sections)", source)

    def test_migration_import_saves_the_normalized_data_sections(self) -> None:
        entry_source = ast.unparse(
            _function(PAGE_API, "_apply_migration_normalized")
        )
        commit_source = ast.unparse(
            _function(PAGE_API, "_commit_migration_normalized")
        )

        self.assertIn("validator(set(data_payload), (), None)", entry_source)
        self.assertLess(
            entry_source.index("validator(set(data_payload), (), None)"),
            entry_source.index("before = await self._build_migration_package"),
        )
        self.assertIn(
            "_save_data_sync(sections=set(data_payload))",
            commit_source,
        )
        self.assertLess(
            commit_source.index("if not config_saved"),
            commit_source.index("_save_data_sync(sections=set(data_payload))"),
        )

    def test_setup_and_schedule_mutations_declare_their_owned_sections(self) -> None:
        expected = {
            "apply_setup_guide": {
                "worldbook_member_profiles",
                "worldbook_deleted_member_ids",
                "setup_guide_completed_at",
                "setup_guide_completed_version",
            },
            "_setup_guide_generate_daily_plan_fast": {
                "daily_plan",
                "daily_state",
                "detail_enhanced_day",
                "detail_enhanced_segments",
                "daily_story_plan",
            },
            "regenerate_daily_detail_segment": {
                "daily_plan",
                "daily_state",
                "detail_enhanced_day",
                "detail_enhanced_segments",
                "detail_enhanced_history",
                "daily_story_plan",
                "daily_story_plan_history",
            },
        }
        for function_name, sections in expected.items():
            with self.subTest(function=function_name):
                source = ast.unparse(_function(PAGE_API, function_name))
                self.assertNotIn("data_payload", source)
                for section in sections:
                    self.assertIn(repr(section), source)

    def test_expression_management_saves_users_groups_persona_and_voice_sections(self) -> None:
        preview = ast.unparse(_function(PAGE_API, "preview_expression_library_import"))
        apply_import = ast.unparse(_function(PAGE_API, "apply_expression_library_import"))
        update = ast.unparse(_function(PAGE_API, "update_expression_library"))

        self.assertIn("source_type == 'group'", preview)
        self.assertIn("source_type == 'group'", apply_import)
        for section in (
            "expression_voice_profile",
            "_req041_persona_expression_profile",
            "_req041_expression_promotion_operations",
            "groups",
            "users",
        ):
            self.assertIn(repr(section), update)


if __name__ == "__main__":
    unittest.main()
