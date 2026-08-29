# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace

from astrbot_plugin_private_companion.token_budget import (
    TokenBudgetMixin,
    _looks_like_upstream_llm_error_response,
)
from astrbot_plugin_private_companion.main import PrivateCompanionPlugin
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


class _FallbackContext:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[str] = []
        self.kwargs: list[dict] = []

    async def llm_generate(self, **kwargs):
        self.kwargs.append(dict(kwargs))
        provider_id = str(kwargs.get("chat_provider_id") or "")
        self.calls.append(provider_id)
        result = self.responses.get(provider_id)
        if isinstance(result, Exception):
            raise result
        if hasattr(result, "completion_text"):
            return result
        return SimpleNamespace(role="assistant", completion_text=str(result or ""))


class _FallbackHarness(TokenBudgetMixin):
    def __init__(self, responses: dict[str, object], *, mode: str = "precision") -> None:
        self.context = _FallbackContext(responses)
        self.provider_config_mode = mode
        self.llm_provider_id = "primary"
        self.model_timeout_overrides = {}
        self.model_token_limit_overrides = {}
        self.model_fallback_overrides = {}
        self.config = {}
        self.usage: list[dict] = []

    def _classify_llm_prompt(self, _prompt: str) -> str:
        return "other"

    def _is_llm_budget_exempt_task(self, _task: str) -> bool:
        return False

    def _daily_token_soft_limit_should_defer(self, _task: str) -> bool:
        return False

    def _llm_daily_budget_remaining(self) -> int:
        return 100000

    def _record_llm_usage(self, **kwargs) -> None:
        self.usage.append(kwargs)


class _UsageToolSet:
    @staticmethod
    def openai_schema() -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "pc_generate_photo",
                    "description": "Capture a photo generation decision.",
                    "parameters": {
                        "type": "object",
                        "properties": {"prompt": {"type": "string"}},
                        "required": ["prompt"],
                    },
                },
            }
        ]


class ModelFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_call_uses_the_budgeted_primary_provider_path(self) -> None:
        response = SimpleNamespace(
            role="assistant",
            completion_text="",
            tools_call_name=["pc_generate_photo"],
            tools_call_args=[{"kind": "selfie", "prompt": "portrait"}],
        )
        harness = _FallbackHarness({"primary": response})
        tools = _UsageToolSet()

        result = await harness._llm_tool_call(
            "take a photo",
            tools=tools,
            provider_id="primary",
            task="photo_reference_selection_trial",
            timeout_key="LLM_PROVIDER_ID",
        )

        self.assertIs(result, response)
        self.assertEqual(harness.context.calls, ["primary"])
        self.assertIs(harness.context.kwargs[0]["tools"], tools)
        self.assertTrue(harness.usage[0]["success"])
        self.assertIn("pc_generate_photo", harness.usage[0]["prompt"])
        self.assertIn("pc_generate_photo", harness.usage[0]["completion"])
        self.assertIn("portrait", harness.usage[0]["completion"])
        estimated = harness._extract_llm_usage(
            response,
            harness.usage[0]["prompt"],
            harness.usage[0]["completion"],
        )
        self.assertTrue(estimated["estimated"])
        self.assertGreater(
            estimated["prompt_tokens"],
            harness._estimate_token_count("take a photo"),
        )
        self.assertGreater(estimated["completion_tokens"], 0)

    async def test_tool_call_preserves_timeout_error_without_configured_timeout(self) -> None:
        harness = _FallbackHarness({"primary": asyncio.TimeoutError()})

        with self.assertRaisesRegex(TimeoutError, "模型任务 photo_reference_selection_trial 调用超时"):
            await harness._llm_tool_call(
                "take a photo",
                tools=_UsageToolSet(),
                provider_id="primary",
                task="photo_reference_selection_trial",
            )

        self.assertEqual(harness.context.calls, ["primary"])
        self.assertIn("调用超时", harness.usage[0]["error"])
        self.assertNotIn("NoneType", harness.usage[0]["error"])

    async def test_tool_call_stops_before_provider_when_daily_budget_is_exhausted(self) -> None:
        harness = _FallbackHarness({"primary": "unused"})
        skips: list[dict] = []
        harness._llm_daily_budget_remaining = lambda: 0
        harness._record_llm_budget_skip = lambda **kwargs: skips.append(kwargs)

        result = await harness._llm_tool_call(
            "take a photo",
            tools="trial-tools",
            provider_id="primary",
            task="photo_reference_selection_trial",
        )

        self.assertIsNone(result)
        self.assertEqual(harness.context.calls, [])
        self.assertEqual(skips[0]["provider_id"], "primary")

    async def test_tool_semantic_provider_error_uses_card_fallback(self) -> None:
        harness = _FallbackHarness(
            {
                "primary": "The prompt could not be submitted.",
                "backup": "tool fallback",
            }
        )
        harness.model_fallback_overrides = {"PHOTO_PROMPT_PROVIDER_ID": "backup"}

        result = await harness._llm_tool_call(
            "take a photo",
            tools=_UsageToolSet(),
            provider_id="primary",
            task="photo_reference_selection_trial",
            timeout_key="PHOTO_PROMPT_PROVIDER_ID",
        )

        self.assertEqual(result.completion_text, "tool fallback")
        self.assertEqual(harness.context.calls, ["primary", "backup"])
        self.assertEqual(harness.usage[0]["error"], "semantic_provider_error")

    async def test_primary_failure_uses_card_fallback_once(self) -> None:
        harness = _FallbackHarness({"primary": RuntimeError("primary down"), "backup": "ok"})
        harness.model_fallback_overrides = {"DAILY_PLAN_PROVIDER_ID": "backup"}
        result = await harness._llm_call(
            "plan",
            provider_id="primary",
            task="daily_plan",
            timeout_key="DAILY_PLAN_PROVIDER_ID",
        )
        self.assertEqual(result, "ok")
        self.assertEqual(harness.context.calls, ["primary", "backup"])
        self.assertFalse(harness.usage[0]["success"])
        self.assertTrue(harness.usage[1]["success"])

    async def test_empty_primary_response_uses_fallback(self) -> None:
        harness = _FallbackHarness({"primary": "", "backup": "fallback text"})
        harness.model_fallback_overrides = {"RESPONSE_REVIEW_PROVIDER_ID": "backup"}
        result = await harness._llm_call(
            "review",
            provider_id="primary",
            task="response_review",
            timeout_key="RESPONSE_REVIEW_PROVIDER_ID",
        )
        self.assertEqual(result, "fallback text")
        self.assertEqual(harness.context.calls, ["primary", "backup"])

    async def test_semantic_provider_error_uses_card_fallback(self) -> None:
        harness = _FallbackHarness(
            {
                "primary": "The prompt could not be submitted.",
                "backup": "人格化备用模型正文",
            }
        )
        harness.model_fallback_overrides = {"RESPONSE_REVIEW_PROVIDER_ID": "backup"}

        result = await harness._llm_call(
            "review",
            provider_id="primary",
            task="proactive_message_fallback",
        )

        self.assertEqual(result, "人格化备用模型正文")
        self.assertEqual(harness.context.calls, ["primary", "backup"])
        self.assertFalse(harness.usage[0]["success"])
        self.assertEqual(harness.usage[0]["error"], "semantic_provider_error")
        self.assertEqual(harness.usage[0]["completion"], "The prompt could not be submitted.")
        self.assertTrue(harness.usage[1]["success"])

    async def test_native_error_role_uses_card_fallback(self) -> None:
        harness = _FallbackHarness(
            {
                "primary": SimpleNamespace(
                    role="err",
                    completion_text="opaque upstream failure",
                ),
                "backup": "备用模型正常正文",
            }
        )
        harness.model_fallback_overrides = {"RESPONSE_REVIEW_PROVIDER_ID": "backup"}

        result = await harness._llm_call(
            "review",
            provider_id="primary",
            task="response_review",
            timeout_key="RESPONSE_REVIEW_PROVIDER_ID",
        )

        self.assertEqual(result, "备用模型正常正文")
        self.assertEqual(harness.context.calls, ["primary", "backup"])
        self.assertEqual(harness.usage[0]["error"], "provider_error_role")

    async def test_normal_technical_text_does_not_use_card_fallback(self) -> None:
        normal_messages = (
            "你刚才说的 tool schema 我看懂了，先歇一会儿吧。",
            "那个页面显示 status disabled，晚点我陪你再看。",
            "别再盯着 traceback 了，先喝口水。",
            "工具调用失败这种提示确实很烦，但先别折腾了。",
        )
        for normal_text in normal_messages:
            with self.subTest(normal_text=normal_text):
                self.assertFalse(_looks_like_upstream_llm_error_response(normal_text))
                harness = _FallbackHarness(
                    {"primary": normal_text, "backup": "不应调用"}
                )
                harness.model_fallback_overrides = {
                    "RESPONSE_REVIEW_PROVIDER_ID": "backup"
                }
                result = await harness._llm_call(
                    "review",
                    provider_id="primary",
                    task="response_review",
                    timeout_key="RESPONSE_REVIEW_PROVIDER_ID",
                )
                self.assertEqual(result, normal_text)
                self.assertEqual(harness.context.calls, ["primary"])

    async def test_same_primary_and_fallback_is_not_retried(self) -> None:
        harness = _FallbackHarness({"primary": RuntimeError("down")})
        harness.model_fallback_overrides = {"DAILY_PLAN_PROVIDER_ID": "primary"}
        result = await harness._llm_call(
            "plan",
            provider_id="primary",
            task="daily_plan",
            timeout_key="DAILY_PLAN_PROVIDER_ID",
        )
        self.assertIsNone(result)
        self.assertEqual(harness.context.calls, ["primary"])

    async def test_quick_mode_uses_quick_card_fallback(self) -> None:
        harness = _FallbackHarness({"primary": RuntimeError("down"), "quick-backup": "ok"}, mode="quick")
        harness.model_fallback_overrides = {"COMPLEX_REASONING_PROVIDER_ID": "quick-backup"}
        result = await harness._llm_call(
            "plan",
            provider_id="primary",
            task="daily_plan",
            timeout_key="DAILY_PLAN_PROVIDER_ID",
        )
        self.assertEqual(result, "ok")
        self.assertEqual(harness.context.calls, ["primary", "quick-backup"])

    async def test_estimated_token_limit_skips_primary_and_uses_fallback(self) -> None:
        harness = _FallbackHarness({"primary": "should not run", "backup": "short fallback"})
        harness.model_token_limit_overrides = {"DAILY_PLAN_PROVIDER_ID": 256}
        harness.model_fallback_overrides = {"DAILY_PLAN_PROVIDER_ID": "backup"}

        result = await harness._llm_call(
            "x" * 1200,
            max_tokens=100,
            provider_id="primary",
            task="daily_plan",
            timeout_key="DAILY_PLAN_PROVIDER_ID",
        )

        self.assertEqual(result, "short fallback")
        self.assertEqual(harness.context.calls, ["backup"])
        self.assertTrue(harness.usage[0]["success"])

    async def test_estimated_token_limit_keeps_primary_when_request_fits(self) -> None:
        harness = _FallbackHarness({"primary": "primary result", "backup": "should not run"})
        harness.model_token_limit_overrides = {"DAILY_PLAN_PROVIDER_ID": 256}
        harness.model_fallback_overrides = {"DAILY_PLAN_PROVIDER_ID": "backup"}

        result = await harness._llm_call(
            "x" * 100,
            max_tokens=100,
            provider_id="primary",
            task="daily_plan",
            timeout_key="DAILY_PLAN_PROVIDER_ID",
        )

        self.assertEqual(result, "primary result")
        self.assertEqual(harness.context.calls, ["primary"])

    async def test_estimated_token_limit_does_not_block_without_fallback(self) -> None:
        harness = _FallbackHarness({"primary": "primary result"})
        harness.model_token_limit_overrides = {"DAILY_PLAN_PROVIDER_ID": 256}

        result = await harness._llm_call(
            "x" * 1200,
            max_tokens=100,
            provider_id="primary",
            task="daily_plan",
            timeout_key="DAILY_PLAN_PROVIDER_ID",
        )

        self.assertEqual(result, "primary result")
        self.assertEqual(harness.context.calls, ["primary"])

    async def test_tool_estimated_token_limit_uses_fallback(self) -> None:
        primary = SimpleNamespace(role="assistant", completion_text="primary")
        backup = SimpleNamespace(role="assistant", completion_text="backup")
        harness = _FallbackHarness({"primary": primary, "backup": backup})
        harness.model_token_limit_overrides = {"RESPONSE_REVIEW_PROVIDER_ID": 256}
        harness.model_fallback_overrides = {"RESPONSE_REVIEW_PROVIDER_ID": "backup"}

        result = await harness._llm_tool_call(
            "x" * 1200,
            tools=_UsageToolSet(),
            max_tokens=100,
            provider_id="primary",
            task="response_review",
            timeout_key="RESPONSE_REVIEW_PROVIDER_ID",
        )

        self.assertIs(result, backup)
        self.assertEqual(harness.context.calls, ["backup"])

    def test_token_limit_config_is_normalized_and_serialized(self) -> None:
        normalized = _FallbackHarness._normalize_model_token_limit_overrides(
            '{"DAILY_PLAN_PROVIDER_ID":" 512 ","too_small":1,"unknown":900}'
        )
        self.assertEqual(normalized, {"DAILY_PLAN_PROVIDER_ID": 512})
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api.plugin = SimpleNamespace(
            _normalize_model_token_limit_overrides=_FallbackHarness._normalize_model_token_limit_overrides,
        )
        api._schema_key_index_cache = None
        self.assertIn("model_token_limit_overrides", api._allowed_setting_keys())
        saved = api._normalize_setting_value(
            "model_token_limit_overrides",
            {"DAILY_PLAN_PROVIDER_ID": 512},
        )
        self.assertEqual(json.loads(saved), {"DAILY_PLAN_PROVIDER_ID": 512})

    def test_fallback_config_is_normalized_and_ui_is_wired(self) -> None:
        normalized = _FallbackHarness._normalize_model_fallback_overrides(
            '{"DAILY_PLAN_PROVIDER_ID":" backup ","unknown":"ignored"}'
        )
        self.assertEqual(normalized, {"DAILY_PLAN_PROVIDER_ID": "backup"})
        root = Path(__file__).resolve().parents[1]
        provider_tree = (root / "pages" / "陪伴面板" / "js" / "panels" / "provider-tree.js").read_text(encoding="utf-8")
        app_js = (root / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
        self.assertIn("data-provider-fallback-select", provider_tree)
        self.assertIn("model_fallback_overrides: fallbackOverrides", app_js)

    def test_token_limit_config_is_wired_into_both_provider_panels(self) -> None:
        root = Path(__file__).resolve().parents[1]
        primary_tree = (root / "pages" / "companion-panel" / "js" / "panels" / "provider-tree.js").read_text(encoding="utf-8")
        localized_tree = (root / "pages" / "陪伴面板" / "js" / "panels" / "provider-tree.js").read_text(encoding="utf-8")
        primary_app = (root / "pages" / "companion-panel" / "app.js").read_text(encoding="utf-8")
        localized_app = (root / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
        primary_css = (root / "pages" / "companion-panel" / "app.css").read_text(encoding="utf-8")
        localized_css = (root / "pages" / "陪伴面板" / "app.css").read_text(encoding="utf-8")
        self.assertEqual(primary_tree, localized_tree)
        self.assertEqual(primary_app, localized_app)
        self.assertEqual(primary_css, localized_css)
        self.assertIn("data-provider-token-limit", primary_tree)
        self.assertIn("单次 Token 上限", primary_tree)
        self.assertIn(".provider-limit-grid", primary_css)
        self.assertIn(".provider-token-limit-control", primary_css)
        self.assertIn(".provider-limit-control b", primary_css)
        self.assertIn("model_token_limit_overrides: tokenLimitOverrides", primary_app)

    def test_ui_can_clear_a_saved_token_limit_before_save(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is unavailable")
        provider_tree_path = Path(__file__).resolve().parents[1] / "pages" / "陪伴面板" / "js" / "panels" / "provider-tree.js"
        script = f"""
global.window = {{}};
const fs = require("fs");
eval(fs.readFileSync({json.dumps(str(provider_tree_path), ensure_ascii=False)}, "utf8"));
const values = window.PrivateCompanionProviderTree.currentProviderTokenLimitValues({{
  state: {{
    overview: {{ settings: {{ model_token_limit_overrides: {{ DAILY_PLAN_PROVIDER_ID: 512 }} }} }},
    providerTokenLimitDraft: {{ DAILY_PLAN_PROVIDER_ID: "" }},
  }},
  document: {{ querySelectorAll: () => [] }},
}});
process.stdout.write(JSON.stringify(values));
"""
        result = subprocess.run(
            [node, "-e", script],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(json.loads(result.stdout), {})

    def test_ui_can_clear_a_saved_fallback_before_save(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is unavailable")
        provider_tree_path = Path(__file__).resolve().parents[1] / "pages" / "陪伴面板" / "js" / "panels" / "provider-tree.js"
        script = f"""
global.window = {{}};
const fs = require("fs");
eval(fs.readFileSync({json.dumps(str(provider_tree_path), ensure_ascii=False)}, "utf8"));
const values = window.PrivateCompanionProviderTree.currentProviderFallbackValues({{
  state: {{
    overview: {{ settings: {{ model_fallback_overrides: {{ DAILY_PLAN_PROVIDER_ID: "saved-backup" }} }} }},
    providerFallbackDraft: {{ DAILY_PLAN_PROVIDER_ID: "" }},
  }},
  document: {{ querySelectorAll: () => [] }},
}});
process.stdout.write(JSON.stringify(values));
"""
        result = subprocess.run(
            [node, "-e", script],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(json.loads(result.stdout), {})

    def test_manual_provider_selection_keeps_custom_inputs_mounted(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is unavailable")
        provider_tree_path = Path(__file__).resolve().parents[1] / "pages" / "陪伴面板" / "js" / "panels" / "provider-tree.js"
        script = f"""
global.window = {{}};
const fs = require("fs");
eval(fs.readFileSync({json.dumps(str(provider_tree_path), ensure_ascii=False)}, "utf8"));
function control(value, dataset) {{
  return {{ value, dataset, hidden: true, focused: false, listeners: {{}},
    focus() {{ this.focused = true; }},
    addEventListener(name, callback) {{ this.listeners[name] = callback; }} }};
}}
const primarySelect = control("", {{ providerSelect: "LLM_PROVIDER_ID" }});
const primaryInput = control("", {{ providerKey: "LLM_PROVIDER_ID" }});
const fallbackSelect = control("", {{ providerFallbackSelect: "LLM_PROVIDER_ID" }});
const fallbackInput = control("", {{ providerFallbackKey: "LLM_PROVIDER_ID" }});
const document = {{
  querySelectorAll(selector) {{
    if (selector === "[data-provider-select]") return [primarySelect];
    if (selector === "[data-provider-key]") return [primaryInput];
    if (selector === "[data-provider-fallback-select]") return [fallbackSelect];
    if (selector === "[data-provider-fallback-key]") return [fallbackInput];
    return [];
  }},
  querySelector(selector) {{
    if (selector === '[data-provider-key="LLM_PROVIDER_ID"]') return primaryInput;
    if (selector === '[data-provider-fallback-key="LLM_PROVIDER_ID"]') return fallbackInput;
    return null;
  }},
}};
const state = {{ providerDraft: {{}}, providerFallbackDraft: {{}} }};
window.PrivateCompanionProviderTree.bindProviderTests({{ document, state }});
primarySelect.value = "__custom__";
primarySelect.listeners.change();
fallbackSelect.value = "__custom__";
fallbackSelect.listeners.change();
process.stdout.write(JSON.stringify({{
  primaryVisible: !primaryInput.hidden,
  primaryFocused: primaryInput.focused,
  fallbackVisible: !fallbackInput.hidden,
  fallbackFocused: fallbackInput.focused,
}}));
"""
        result = subprocess.run(
            [node, "-e", script],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(
            json.loads(result.stdout),
            {
                "primaryVisible": True,
                "primaryFocused": True,
                "fallbackVisible": True,
                "fallbackFocused": True,
            },
        )

    def test_precision_ui_fallback_ignores_hidden_quick_provider_values(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is unavailable")
        provider_tree_path = Path(__file__).resolve().parents[1] / "pages" / "陪伴面板" / "js" / "panels" / "provider-tree.js"
        script = f"""
global.window = {{}};
const fs = require("fs");
eval(fs.readFileSync({json.dumps(str(provider_tree_path), ensure_ascii=False)}, "utf8"));
const values = {{
  FAST_RESPONSE_PROVIDER_ID: "old-fast",
  COMPLEX_REASONING_PROVIDER_ID: "old-complex",
  LLM_PROVIDER_ID: "new-main",
  MAI_STYLE_PROVIDER_ID: "new-style",
}};
const common = {{
  noFallbackProviderKeys: new Set(),
  optionalNoFallbackProviderKeys: new Set(),
  state: {{}},
}};
const precision = window.PrivateCompanionProviderTree.resolveProviderId(
  {{ ...common, currentProviderConfigMode: () => "precision" }},
  "HISTORY_SUMMARY_PROVIDER_ID",
  values,
);
const quick = window.PrivateCompanionProviderTree.resolveProviderId(
  {{ ...common, currentProviderConfigMode: () => "quick" }},
  "HISTORY_SUMMARY_PROVIDER_ID",
  values,
);
process.stdout.write(JSON.stringify({{ precision, quick }}));
"""
        result = subprocess.run(
            [node, "-e", script],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(
            json.loads(result.stdout),
            {"precision": "new-main", "quick": "old-complex"},
        )

    def test_precision_save_does_not_derive_hidden_quick_vision_from_narration(self) -> None:
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        values = {
            "FAST_RESPONSE_PROVIDER_ID": "old-fast",
            "COMPLEX_REASONING_PROVIDER_ID": "old-complex",
            "CREATIVE_MODEL_PROVIDER_ID": "old-creative",
            "PLUGIN_VISION_PROVIDER_ID": "old-vision",
            "LLM_PROVIDER_ID": "new-main",
            "MAI_STYLE_PROVIDER_ID": "new-style",
            "CREATIVE_PROVIDER_ID": "new-creative",
            "NARRATION_PROVIDER_ID": "deepseek/deepseek-v4-flash",
            "PRIVATE_READING_VISION_PROVIDER_ID": "new-reading-vision",
        }

        bundle = api._quick_bundle_from_precision(values)

        self.assertEqual(bundle["FAST_RESPONSE_PROVIDER_ID"], "new-style")
        self.assertEqual(bundle["COMPLEX_REASONING_PROVIDER_ID"], "new-main")
        self.assertEqual(bundle["CREATIVE_MODEL_PROVIDER_ID"], "new-creative")
        self.assertNotIn("PLUGIN_VISION_PROVIDER_ID", bundle)

    def test_precision_save_preserves_or_clears_vision_without_narration_fallback(self) -> None:
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        stored = {
            "FAST_RESPONSE_PROVIDER_ID": "old-fast",
            "COMPLEX_REASONING_PROVIDER_ID": "old-complex",
            "CREATIVE_MODEL_PROVIDER_ID": "old-creative",
            "PLUGIN_VISION_PROVIDER_ID": "stored-vision",
            "NARRATION_PROVIDER_ID": "deepseek/deepseek-v4-flash",
        }
        api._provider_settings = lambda: dict(stored)
        api._allowed_provider_keys = lambda: set(stored)

        missing = api._expand_provider_overwrite_bundle(
            "precision",
            {"NARRATION_PROVIDER_ID": "deepseek/deepseek-v4-flash"},
        )
        chosen = api._expand_provider_overwrite_bundle(
            "precision",
            {
                "NARRATION_PROVIDER_ID": "deepseek/deepseek-v4-flash",
                "PLUGIN_VISION_PROVIDER_ID": "chosen-vision",
            },
        )
        cleared = api._expand_provider_overwrite_bundle(
            "precision",
            {
                "NARRATION_PROVIDER_ID": "deepseek/deepseek-v4-flash",
                "PLUGIN_VISION_PROVIDER_ID": "",
            },
        )

        self.assertEqual(missing["PLUGIN_VISION_PROVIDER_ID"], "stored-vision")
        self.assertEqual(chosen["PLUGIN_VISION_PROVIDER_ID"], "chosen-vision")
        self.assertEqual(cleared["PLUGIN_VISION_PROVIDER_ID"], "")

    def test_provider_mode_round_trip_preserves_independent_vision_provider(self) -> None:
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        plugin.config = {
            "model_assignment_config": {
                "PLUGIN_VISION_PROVIDER_ID": "vision-provider",
                "PRIVATE_READING_VISION_PROVIDER_ID": "reading-vision-provider",
                "LLM_PROVIDER_ID": "precision-main",
                "MAI_STYLE_PROVIDER_ID": "deepseek/deepseek-v4-flash",
                "NARRATION_PROVIDER_ID": "deepseek/deepseek-v4-flash",
            },
            "PLUGIN_VISION_PROVIDER_ID": "legacy-stale-vision",
        }
        plugin.fast_response_provider_id = "deepseek/deepseek-v4-flash"
        plugin.complex_reasoning_provider_id = "quick-complex"
        plugin.creative_model_provider_id = "quick-creative"
        plugin.plugin_vision_provider_id = "vision-provider"
        plugin.private_reading_vision_provider_id = "reading-vision-provider"

        plugin.provider_config_mode = "quick"
        plugin._apply_quick_provider_defaults()
        self.assertEqual(plugin.plugin_vision_provider_id, "vision-provider")

        plugin.provider_config_mode = "precision"
        plugin._apply_quick_provider_defaults()
        self.assertEqual(plugin.narration_provider_id, "deepseek/deepseek-v4-flash")
        self.assertEqual(plugin.plugin_vision_provider_id, "vision-provider")

        plugin.provider_config_mode = "quick"
        plugin._apply_quick_provider_defaults()
        self.assertEqual(plugin.plugin_vision_provider_id, "vision-provider")
        self.assertNotEqual(plugin.plugin_vision_provider_id, plugin.fast_response_provider_id)

    def test_provider_mode_recovers_stale_runtime_vision_from_config(self) -> None:
        for mode in ("quick", "precision"):
            for stale_runtime in ("", "deepseek/deepseek-v4-flash"):
                with self.subTest(mode=mode, stale_runtime=stale_runtime):
                    plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
                    plugin.config = {
                        "model_assignment_config": {
                            "PLUGIN_VISION_PROVIDER_ID": "vision-provider",
                            "PRIVATE_READING_VISION_PROVIDER_ID": "reading-vision-provider",
                            "NARRATION_PROVIDER_ID": "deepseek/deepseek-v4-flash",
                        },
                        "PLUGIN_VISION_PROVIDER_ID": "legacy-stale-vision",
                    }
                    plugin.provider_config_mode = mode
                    plugin.fast_response_provider_id = "deepseek/deepseek-v4-flash"
                    plugin.complex_reasoning_provider_id = "quick-complex"
                    plugin.creative_model_provider_id = "quick-creative"
                    plugin.plugin_vision_provider_id = stale_runtime
                    plugin.private_reading_vision_provider_id = "reading-vision-provider"

                    plugin._apply_quick_provider_defaults()

                    self.assertEqual(plugin.plugin_vision_provider_id, "vision-provider")
                    self.assertNotEqual(plugin.plugin_vision_provider_id, plugin.fast_response_provider_id)

    def test_provider_mode_round_trip_respects_explicitly_cleared_vision_provider(self) -> None:
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        plugin.config = {
            "model_assignment_config": {
                "PLUGIN_VISION_PROVIDER_ID": "",
                "NARRATION_PROVIDER_ID": "deepseek/deepseek-v4-flash",
            }
        }
        plugin.fast_response_provider_id = "deepseek/deepseek-v4-flash"
        plugin.complex_reasoning_provider_id = "quick-complex"
        plugin.creative_model_provider_id = "quick-creative"
        plugin.plugin_vision_provider_id = "previous-vision"
        plugin.private_reading_vision_provider_id = ""

        plugin.provider_config_mode = "precision"
        plugin._apply_quick_provider_defaults()
        self.assertEqual(plugin.plugin_vision_provider_id, "")

        plugin.provider_config_mode = "quick"
        plugin._apply_quick_provider_defaults()
        self.assertEqual(plugin.plugin_vision_provider_id, "")

    def test_grouped_provider_clear_does_not_fall_back_to_stale_legacy_value(self) -> None:
        plugin = SimpleNamespace(
            config={
                "model_assignment_config": {"LLM_PROVIDER_ID": ""},
                "LLM_PROVIDER_ID": "stale-legacy-provider",
            }
        )
        api = PrivateCompanionPageApi(plugin)

        self.assertEqual(api._config_get("LLM_PROVIDER_ID"), "")

        api._schema_provider_keys = lambda public_only=True: set()
        plugin.llm_provider_id = "stale-runtime-provider"
        self.assertEqual(api._provider_settings()["LLM_PROVIDER_ID"], "")

    def test_precision_hot_refresh_updates_specialized_provider_assignments(self) -> None:
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        plugin.config = {
            "model_assignment_config": {
                "GROUP_MEMBER_SAFETY_PROVIDER_ID": "safety-new",
                "REACTION_EXPRESSION_EMBEDDING_PROVIDER_ID": "embedding-new",
                "DEEPSEEK_PEAK_REPLACEMENT_PROVIDER_ID": "peak-new",
                "SENSITIVE_REPLACEMENT_PROVIDER_ID": "sensitive-new",
            }
        }
        plugin.provider_config_mode = "precision"
        plugin.fast_response_provider_id = "fast"
        plugin.complex_reasoning_provider_id = "complex"
        plugin.creative_model_provider_id = "creative"
        plugin.plugin_vision_provider_id = "vision"
        plugin.private_reading_vision_provider_id = "reading"
        plugin.group_member_safety_provider_id = "safety-old"
        plugin.reaction_expression_embedding_provider_id = "embedding-old"
        plugin.deepseek_peak_replacement_provider_id = "peak-old"
        plugin.sensitive_replacement_provider_id = "sensitive-old"

        plugin._apply_quick_provider_defaults()

        self.assertEqual(plugin.group_member_safety_provider_id, "safety-new")
        self.assertEqual(plugin.reaction_expression_embedding_provider_id, "embedding-new")
        self.assertEqual(plugin.deepseek_peak_replacement_provider_id, "peak-new")
        self.assertEqual(plugin.sensitive_replacement_provider_id, "sensitive-new")

    def test_model_page_invalidates_closed_setup_guide_provider_draft(self) -> None:
        root = Path(__file__).resolve().parents[1]
        primary = (root / "pages" / "companion-panel" / "app.js").read_text(encoding="utf-8")
        localized = (root / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")

        self.assertEqual(primary, localized)
        self.assertIn("if (!state.setupGuideOpen) {", primary)
        self.assertIn("state.setupGuideDraft = null;", primary)
        self.assertIn("state.setupGuideProviderTests = {};", primary)
        self.assertIn("独立识图保持原配置", primary)
        self.assertIn(
            "if (visibleConfigKey(key) && providerAllowedInCurrentMode(key)) providers[key] = values[key] || \"\";",
            primary,
        )

    def test_page_api_accepts_and_serializes_fallback_map(self) -> None:
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api.plugin = SimpleNamespace(_normalize_model_fallback_overrides=_FallbackHarness._normalize_model_fallback_overrides)
        api._schema_key_index_cache = None
        self.assertIn("model_fallback_overrides", api._allowed_setting_keys())
        saved = api._normalize_setting_value(
            "model_fallback_overrides",
            {"DAILY_PLAN_PROVIDER_ID": "backup"},
        )
        self.assertEqual(json.loads(saved), {"DAILY_PLAN_PROVIDER_ID": "backup"})

    def test_reaction_library_analysis_exposes_token_limit_route(self) -> None:
        page_api = Path(__file__).resolve().parents[1] / "page_api.py"
        source = page_api.read_text(encoding="utf-8")
        self.assertIn('task="reaction_library_analysis"', source)
        self.assertIn('error="model_token_limit_exceeded"', source)
        self.assertIn("_model_token_limit_should_skip_primary", source)


if __name__ == "__main__":
    unittest.main()
