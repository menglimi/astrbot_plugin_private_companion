# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock
from types import SimpleNamespace

from astrbot_plugin_private_companion.daily_state import DailyStateMixin
from astrbot_plugin_private_companion.main import PrivateCompanionPlugin
from astrbot_plugin_private_companion.model_routing import (
    CURRENT_MODEL_REPLACEMENT_SOURCES,
    build_rules,
    contains_sensitive_refusal,
    find_route,
    normalize_scope,
    scope_allows,
)


class _PluginRouteHarness(DailyStateMixin):
    model_replacement_scope = "plugin"

    def __init__(self, scope: str = "plugin") -> None:
        self.model_replacement_scope = scope
        self.model_replacement_rules, _ = build_rules(
            [
                {
                    "name": "代码",
                    "provider_id": "coding-provider",
                    "keywords": ["写代码", "报错"],
                    "priority": 10,
                }
            ]
        )
        self.context = SimpleNamespace(
            get_provider_by_id=lambda provider_id: object() if provider_id == "coding-provider" else None
        )

    def _apply_deepseek_peak_replacement(self, provider_id: str, **_kwargs: object) -> str:
        return provider_id


class ModelReplacementStrategyTests(unittest.TestCase):
    def test_keyword_rules_keep_priority_and_match_sources(self) -> None:
        rules, warnings = build_rules(
            [
                {"name": "低", "provider_id": "low", "keywords": ["代码"], "priority": 1},
                {"name": "高", "provider_id": "high", "keywords": ["代码"], "priority": 20},
            ]
        )

        match = find_route(rules, [("wake_message", "帮我写代码")])

        self.assertEqual([], warnings)
        self.assertIsNotNone(match)
        self.assertEqual("high", match.rule.provider_id)

    def test_scope_controls_plugin_and_conversation_independently(self) -> None:
        self.assertEqual("plugin", normalize_scope("插件调用"))
        self.assertTrue(scope_allows("plugin", "plugin"))
        self.assertFalse(scope_allows("plugin", "conversation"))
        self.assertTrue(scope_allows("all", "conversation"))

        plugin_only = _PluginRouteHarness("plugin")
        token = CURRENT_MODEL_REPLACEMENT_SOURCES.set((("wake_message", "请帮我写代码"),))
        try:
            self.assertEqual("coding-provider", plugin_only._task_provider("default-provider"))
        finally:
            CURRENT_MODEL_REPLACEMENT_SOURCES.reset(token)
        conversation_only = _PluginRouteHarness("conversation")
        self.assertEqual("default-provider", conversation_only._task_provider("default-provider"))

    def test_strict_task_provider_bypasses_keyword_and_peak_replacements(self) -> None:
        harness = _PluginRouteHarness("plugin")
        harness._apply_deepseek_peak_replacement = (
            lambda _provider_id, **_kwargs: "peak-provider"
        )
        token = CURRENT_MODEL_REPLACEMENT_SOURCES.set(
            (("wake_message", "请帮我写代码"),)
        )
        try:
            self.assertEqual(
                "peak-provider",
                harness._task_provider("default-provider"),
            )
            self.assertEqual(
                "default-provider",
                harness._task_provider(
                    "default-provider",
                    allow_replacement=False,
                ),
            )
        finally:
            CURRENT_MODEL_REPLACEMENT_SOURCES.reset(token)

    def test_sensitive_refusal_matches_compact_variants(self) -> None:
        self.assertEqual(
            "很抱歉，我无法",
            contains_sensitive_refusal("很抱歉，我 无法继续回答这个问题。"),
        )
        self.assertEqual(
            "露骨性行为",
            contains_sensitive_refusal("这涉及露骨性行为，因此不能继续。"),
        )
        self.assertEqual(
            "没办法提交这个请求",
            contains_sensitive_refusal("抱歉，没办法提交这个请求。"),
        )
        self.assertEqual(
            "The prompt could not be submitted",
            contains_sensitive_refusal(
                "The prompt could not be submitted. The prompt contains sensitive words."
            ),
        )
        self.assertEqual(
            "露骨性行为",
            contains_sensitive_refusal("自定义词表仍会保留内置拒答检测：露骨性行为", "自定义词"),
        )
        self.assertEqual("", contains_sensitive_refusal("当然可以，我来帮你处理。"))

    def test_conversation_sensitive_response_is_replaced_before_send(self) -> None:
        async def run() -> None:
            plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
            plugin.enabled = True
            plugin.enable_sensitive_model_replacement = True
            plugin.model_replacement_scope = "conversation"
            plugin.sensitive_replacement_provider_id = "safe-provider"
            plugin.sensitive_replacement_keywords = "很抱歉，我无法；我无法满足"
            fallback = SimpleNamespace(
                completion_text="我可以换个角度帮你处理这件事。",
                result_chain=None,
                role="assistant",
            )
            plugin.context = SimpleNamespace(
                get_provider_by_id=lambda provider_id: object() if provider_id == "safe-provider" else None,
                llm_generate=AsyncMock(return_value=fallback),
            )
            request = SimpleNamespace(
                prompt="请继续",
                contexts=[{"role": "user", "content": "请继续"}],
                system_prompt="你是助手",
                image_urls=[],
                audio_urls=[],
            )

            class Event:
                unified_msg_origin = "default:FriendMessage:1"

                def __init__(self) -> None:
                    self.extras = {"provider_request": request}

                def get_extra(self, key, default=None):
                    return self.extras.get(key, default)

            from astrbot.core.provider.entities import LLMResponse

            event = Event()
            response = LLMResponse("assistant", "很抱歉，我无法继续回答这个问题。")
            response.provider_id = "strict-provider"
            await plugin.replace_sensitive_conversation_response(event, response)

            self.assertEqual("我可以换个角度帮你处理这件事。", response.completion_text)
            plugin.context.llm_generate.assert_awaited_once()
            self.assertEqual("safe-provider", plugin.context.llm_generate.await_args.kwargs["chat_provider_id"])

        asyncio.run(run())

    def test_relationship_stage_route_pairs_provider_and_model_before_request(self) -> None:
        async def run() -> None:
            plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
            plugin.enabled = True
            plugin.model_replacement_scope = "plugin"
            plugin.enable_relationship_stage_provider_routing = True
            plugin.relationship_stage_provider_routes = {
                "close": "test-lab-real-gemini",
                "intimate": "test-lab-missing-provider-fixture",
            }
            plugin.relationship_stage_policy = None
            plugin.data = {
                "users": {
                    "actor-a": {
                        "relationship_score": 650,
                        "relationship_mode": "normal",
                    }
                }
            }
            plugin._safe_event_is_private = lambda _event: True
            plugin._safe_event_sender_id = lambda event: event.get_sender_id()
            plugin._private_user_id_for_event = lambda event: event.get_sender_id()
            plugin._lab_fixture_relationship_view = lambda _event, user: dict(user)
            plugin.context = SimpleNamespace(
                get_provider_by_id=lambda provider_id: (
                    SimpleNamespace(get_model=lambda: "gemini-3.5-flash-low")
                    if provider_id == "test-lab-real-gemini"
                    else None
                )
            )

            class Event:
                unified_msg_origin = "test-lab:FriendMessage:actor-a"

                def __init__(self) -> None:
                    self.extras = {"selected_provider": "test-lab-real-deepseek"}

                @staticmethod
                def get_sender_id() -> str:
                    return "actor-a"

                def get_extra(self, key, default=None):
                    return self.extras.get(key, default)

                def set_extra(self, key, value) -> None:
                    self.extras[key] = value

            event = Event()
            await plugin.route_model_replacement_before_agent(event)

            self.assertEqual(
                "test-lab-real-gemini",
                event.extras["selected_provider"],
            )
            self.assertEqual(
                "gemini-3.5-flash-low",
                event.extras["selected_model"],
            )
            self.assertEqual(
                {
                    "stage_key": "close",
                    "provider_id": "test-lab-real-gemini",
                },
                event.extras[
                    "private_companion_relationship_stage_provider_route"
                ],
            )

            request = SimpleNamespace(
                provider_id="test-lab-real-deepseek",
                model="deepseek-v4-flash",
            )
            await plugin.enforce_model_replacement_request(event, request)

            self.assertEqual("test-lab-real-gemini", request.provider_id)
            self.assertEqual("gemini-3.5-flash-low", request.model)

            plugin.data["users"]["actor-a"]["relationship_score"] = 950
            fallback_event = Event()
            plugin._prepare_model_replacement_sources = AsyncMock(return_value=[])
            plugin._model_replacement_rules_for_event = lambda: []
            plugin._default_chat_provider_id = (
                lambda _umo: "test-lab-real-deepseek"
            )
            await plugin.route_model_replacement_before_agent(fallback_event)
            fallback_request = SimpleNamespace(
                provider_id="test-lab-real-deepseek",
                model="deepseek-v4-flash",
            )
            await plugin.enforce_model_replacement_request(
                fallback_event,
                fallback_request,
            )

            self.assertEqual(
                "test-lab-real-deepseek", fallback_request.provider_id
            )
            self.assertEqual("deepseek-v4-flash", fallback_request.model)
            self.assertNotIn(
                "private_companion_relationship_stage_provider_route",
                fallback_event.extras,
            )

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
