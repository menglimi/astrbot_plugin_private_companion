from __future__ import annotations

import ast
import json
from collections import Counter
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


REQUIRED_MODULES = {
    "atrelay.py",
    "command_handlers.py",
    "creative.py",
    "extension_api_relationship.py",
    "group_member_safety.py",
    "group_observation.py",
    "group_wakeup.py",
    "llm_tool_actions.py",
    "news_exploration.py",
    "page_api.py",
    "page_api_qzone.py",
    "page_api_users_groups.py",
    "private_image.py",
    "reading_archive.py",
    "proactive_engine.py",
    "qzone_integration.py",
    "qzone_media.py",
    "qzone_recent_parser.py",
    "qzone_selection.py",
    "tts_enhancement.py",
}

REQUIRED_PAGE_ASSETS = {
    "pages/陪伴面板/index.html",
    "pages/陪伴面板/app.js",
    "pages/陪伴面板/app.css",
    "pages/陪伴面板/js/panels/qzone-panel.js",
    "pages/陪伴面板/js/features/daily-outfit.js",
}

EXPECTED_QZONE_ROUTES = [
    ("/qzone/status", "get_qzone_status", "GET"),
    ("/qzone/health", "get_qzone_status", "GET"),
    ("/qzone/summary", "get_qzone_status", "GET"),
    ("/qzone/state", "get_qzone_status", "GET"),
    ("/qzone/feed", "get_qzone_feed", "GET"),
    ("/qzone/feeds", "get_qzone_feed", "GET"),
    ("/qzone/list", "get_qzone_feed", "GET"),
    ("/qzone/detail", "get_qzone_detail", "GET"),
    ("/qzone/post", "get_qzone_detail", "GET"),
    ("/qzone/item", "get_qzone_detail", "GET"),
    ("/qzone/refresh_cookies", "refresh_qzone_cookies", "POST"),
    ("/qzone/refresh-cookies", "refresh_qzone_cookies", "POST"),
    ("/qzone/cookies/refresh", "refresh_qzone_cookies", "POST"),
    ("/qzone/cookie/refresh", "refresh_qzone_cookies", "POST"),
    ("/qzone/refresh", "refresh_qzone_cookies", "POST"),
    ("/qzone/publish", "publish_qzone_post", "POST"),
    ("/qzone/post/publish", "publish_qzone_post", "POST"),
    ("/qzone/post", "publish_qzone_post", "POST"),
    ("/qzone/like", "like_qzone_post", "POST"),
    ("/qzone/post/like", "like_qzone_post", "POST"),
    ("/qzone/comment", "comment_qzone_post", "POST"),
    ("/qzone/post/comment", "comment_qzone_post", "POST"),
    ("/qzone/delete", "delete_qzone_post", "POST"),
    ("/qzone/post/delete", "delete_qzone_post", "POST"),
]

EXPECTED_LLM_TOOLS = {
    "pc_qzone_view_feed",
    "pc_qzone_publish_feed",
    "pc_generate_photo",
    "pc_find_reaction_image",
    "pc_manage_memo",
    "pc_manage_schedule",
    "pc_view_creative_work",
    "pc_get_group_id_by_name",
    "pc_get_user_id_by_name",
    "pc_query_relation_person",
    "pc_get_specified_group_members",
    "pc_query_interaction",
    "pc_relay_message",
    "pc_send_to_group",
    "pc_send_to_private_user",
    "pc_send_to_groups",
    "pc_send_to_private_users",
    "pc_schedule_group_relay",
}

# The production code does not expose a literal ``optional_tasks`` object.
# These are the stable config switches that keep its optional C6 capabilities
# routable without importing AstrBot or instantiating the plugin.
OPTIONAL_TASKS = {
    "bilibili": "enable_bilibili_integration",
    "news": "enable_news_integration",
    "web_exploration": "enable_web_exploration",
    "qzone": "enable_qzone_integration",
    "group_companion": "enable_group_companion",
    "group_wakeup": "enable_group_wakeup_enhancement",
    "group_safety": "enable_group_member_safety",
    "atrelay": "enable_atrelay_tools",
    "outfit": "enable_daily_outfit_photo",
    "photo": "enable_natural_language_photo_generation",
    "tts": "enable_tts_enhancement",
}


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _source(*relative_paths: str) -> str:
    return "\n".join((ROOT / relative_path).read_text(encoding="utf-8") for relative_path in relative_paths)


def _literal(node: ast.AST) -> object:
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        return None


def _qzone_routes() -> list[tuple[str, str, str]]:
    tree = _parse(ROOT / "page_api.py")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.List):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "routes" for target in node.targets):
            continue
        routes: list[tuple[str, str, str]] = []
        for item in node.value.elts:
            if not isinstance(item, ast.Tuple) or len(item.elts) < 3:
                continue
            path, handler, methods = item.elts[:3]
            path_value = _literal(path)
            handler_name = handler.attr if isinstance(handler, ast.Attribute) else None
            method_values = _literal(methods)
            if isinstance(path_value, str) and path_value.startswith("/qzone/") and handler_name and isinstance(method_values, list):
                routes.extend((path_value, handler_name, str(method)) for method in method_values)
        return routes
    return []


def _llm_tool_names() -> set[str]:
    names: set[str] = set()
    for path in ROOT.glob("*.py"):
        tree = _parse(path)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                if decorator.func.attr != "llm_tool":
                    continue
                for keyword in decorator.keywords:
                    if keyword.arg == "name" and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                        names.add(keyword.value.value)
    return names


def _command_mappings() -> dict[str, set[str]]:
    tree = _parse(ROOT / "main.py")
    mappings: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            if decorator.func.attr != "command" or not decorator.args:
                continue
            command = _literal(decorator.args[0])
            aliases = next((keyword.value for keyword in decorator.keywords if keyword.arg == "alias"), None)
            alias_values = _literal(aliases) if aliases is not None else set()
            mappings[str(command)] = {str(value) for value in alias_values} if isinstance(alias_values, set) else set()
    return mappings


class C6CapabilityMatrixTests(unittest.TestCase):
    def test_required_capability_modules_and_page_assets_exist(self) -> None:
        missing_modules = sorted(name for name in REQUIRED_MODULES if not (ROOT / name).is_file())
        missing_assets = sorted(name for name in REQUIRED_PAGE_ASSETS if not (ROOT / name).is_file())
        self.assertEqual(missing_modules, [], f"missing chat-side capability modules: {missing_modules}")
        self.assertEqual(missing_assets, [], f"missing companion page assets: {missing_assets}")

        main_source = (ROOT / "main.py").read_text(encoding="utf-8")
        directly_routed_modules = REQUIRED_MODULES - {
            "page_api.py",
            "page_api_qzone.py",
            "page_api_users_groups.py",
            "qzone_media.py",
            "qzone_recent_parser.py",
            "qzone_selection.py",
        }
        for module_name in directly_routed_modules:
            self.assertIn(f".{module_name[:-3]}", main_source, f"main.py no longer routes {module_name}")

    def test_qzone_route_matrix_keeps_24_entries_and_handlers(self) -> None:
        actual = _qzone_routes()
        self.assertEqual(len(actual), 24, "C6 expects 24 /qzone/* route entries")
        self.assertEqual(Counter(actual), Counter(EXPECTED_QZONE_ROUTES))
        self.assertEqual({handler for _, handler, _ in actual}, {
            "get_qzone_status",
            "get_qzone_feed",
            "get_qzone_detail",
            "refresh_qzone_cookies",
            "publish_qzone_post",
            "like_qzone_post",
            "comment_qzone_post",
            "delete_qzone_post",
        })

    def test_original_llm_tool_matrix_is_present(self) -> None:
        actual = _llm_tool_names()
        self.assertGreaterEqual(len(actual), 18)
        self.assertTrue(EXPECTED_LLM_TOOLS <= actual, sorted(EXPECTED_LLM_TOOLS - actual))

    def test_optional_tasks_are_declared_in_schema_and_used_by_chat_side(self) -> None:
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        self.assertIsInstance(schema, dict)
        schema_keys = set(schema)
        source = _source("main.py", "page_api.py", "plugin_bootstrap.py")
        for task, config_key in OPTIONAL_TASKS.items():
            self.assertIn(config_key, schema_keys, f"optional C6 task {task} lost schema key {config_key}")
            self.assertRegex(source, rf"\b{re.escape(config_key)}\b", f"optional C6 task {task} is not routed")
        self.assertIn("enabled_proactive_actions", schema_keys)
        self.assertIn("enabled_proactive_actions", source)

    def test_command_mapping_and_reason_window_hooks_remain(self) -> None:
        mappings = _command_mappings()
        self.assertIn("陪伴", mappings)
        self.assertIn("陪伴群", mappings)
        self.assertTrue({"私聊陪伴", "主动陪伴"} <= mappings["陪伴"])
        self.assertTrue({"群陪伴", "群聊陪伴"} <= mappings["陪伴群"])

        tree = _parse(ROOT / "proactive_engine.py")
        reason_window_methods = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_reason_windows"
        }
        self.assertEqual(reason_window_methods, {"_reason_windows"})
        source = (ROOT / "proactive_engine.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("_reason_windows("), 4)

    def test_c6_domain_entrypoints_are_still_reachable(self) -> None:
        source = _source(
            "main.py",
            "command_handlers.py",
            "extension_api_relationship.py",
            "news_exploration.py",
            "page_api.py",
            "private_image.py",
            "reading_archive.py",
            "group_member_safety.py",
            "group_wakeup.py",
            "atrelay.py",
            "tts_enhancement.py",
        )
        markers = {
            "QQ space": ("qzone", "QZONE_COOKIE"),
            "news": ("news", "NEWS_PROVIDER_ID"),
            "Bilibili": ("bilibili", "enable_bilibili_integration"),
            "web exploration": ("web_exploration", "WEB_EXPLORATION_PROVIDER_ID"),
            "group safety": ("group_member_safety", "enable_group_member_safety"),
            "group wakeup": ("group_wakeup", "enable_group_wakeup_enhancement"),
            "outfit": ("daily_outfit", "enable_daily_outfit_photo"),
            "image generation": ("pc_generate_photo", "photo_generation_backend"),
            "TTS": ("tts", "enable_tts_enhancement"),
            "history import": ("历史对话导入", "HISTORY_SUMMARY_PROVIDER_ID"),
            "AtRelay": ("atrelay", "pc_relay_message"),
            "page API": ("register_routes", "/qzone/status"),
        }
        for capability, required_markers in markers.items():
            self.assertTrue(all(marker in source for marker in required_markers), capability)


if __name__ == "__main__":
    unittest.main()
