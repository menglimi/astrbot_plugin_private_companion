# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace

from quart import Quart

from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi
from astrbot_plugin_private_companion.token_budget import TokenBudgetMixin


class _Config(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.save_count = 0

    def save_config(self):
        self.save_count += 1


class _FailingConfig(_Config):
    def save_config(self):
        self.save_count += 1
        raise OSError("disk unavailable")


class _FalseSaveConfig(_Config):
    def save_config(self):
        self.save_count += 1
        return False


class _DelayedFirstSaveConfig(_Config):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.first_save_started = asyncio.Event()
        self.release_first_save = asyncio.Event()

    async def save_config(self):
        self.save_count += 1
        if self.save_count == 1:
            self.first_save_started.set()
            await self.release_first_save.wait()
            raise OSError("first save failed")


class _PagePlugin:
    def __init__(self):
        self.config = _Config({"task_prompt_overrides": '{"voice":"旧指令"}'})


class TaskPromptPageApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app = Quart(__name__)
        self.plugin = _PagePlugin()
        self.api = PrivateCompanionPageApi(self.plugin)

    async def test_catalog_reads_persisted_overrides_and_exposes_complete_builtin_prompts(self) -> None:
        async with self.app.test_request_context("/task-prompts"):
            result = await self.api.get_task_prompts()
        self.assertTrue(result["success"])
        payload = result["data"]
        self.assertEqual("task_prompt_overrides", payload["config_key"])
        self.assertGreaterEqual(len(payload["tasks"]), 80)
        voice = next(item for item in payload["tasks"] if item["task_key"] == "voice")
        self.assertEqual("旧指令", voice["custom_prompt"])
        self.assertTrue(voice["customized"])
        builtin_prompt = str(voice["builtin_prompt"])
        self.assertTrue(builtin_prompt)
        for section in ("【任务身份】", "【任务目标】", "【动态输入】", "【执行规则】", "【输出契约】", "【禁止事项】"):
            self.assertIn(section, builtin_prompt)
        self.assertIn("{{task_input}}", builtin_prompt)
        self.assertIn("{{runtime_metadata}}", builtin_prompt)
        self.assertNotIn("模板预览", builtin_prompt)
        self.assertNotIn("稳定规则预览", builtin_prompt)
        self.assertIs(voice["builtin_prompt_dynamic"], True)
        self.assertIn("forward_message", {item["task_key"] for item in payload["tasks"]})
        self.assertNotIn("astrbot_reply", {item["task_key"] for item in payload["tasks"]})
        for task_key, task_marker in (
            ("screen_narration", "屏幕观察结果"),
            ("forward_message", "合并转发"),
        ):
            task = next(item for item in payload["tasks"] if item["task_key"] == task_key)
            self.assertIn(task_marker, task["builtin_prompt"])
            self.assertIs(task["builtin_prompt_dynamic"], True)

    async def test_single_batch_and_reset_updates_are_atomic_and_persisted(self) -> None:
        async with self.app.test_request_context(
            "/task-prompts/update", method="POST", json={"task_key": "screen_narration", "prompt": "先说明工具是否成功"}
        ):
            saved = await self.api.update_task_prompts()
        self.assertTrue(saved["success"])
        self.assertTrue(saved["data"]["config_saved"])
        self.assertEqual("先说明工具是否成功", self.plugin.task_prompt_overrides["screen_narration"])
        self.assertIsInstance(self.plugin.config["task_prompt_overrides"], str)
        self.assertIn('"screen_narration":"先说明工具是否成功"', self.plugin.config["task_prompt_overrides"])
        self.assertEqual(1, self.plugin.config.save_count)

        async with self.app.test_request_context(
            "/task-prompts/update",
            method="POST",
            json={"overrides": {"forward_message": "保留引用边界", "voice": "新的语音约束"}},
        ):
            batch = await self.api.update_task_prompts()
        self.assertTrue(batch["success"])
        self.assertEqual("保留引用边界", self.plugin.task_prompt_overrides["forward_message"])
        self.assertEqual("新的语音约束", self.plugin.task_prompt_overrides["voice"])

        async with self.app.test_request_context(
            "/task-prompts/update", method="POST", json={"task_key": "voice", "reset": True}
        ):
            reset = await self.api.update_task_prompts()
        self.assertTrue(reset["success"])
        self.assertNotIn("voice", self.plugin.task_prompt_overrides)
        voice = next(item for item in reset["data"]["tasks"] if item["task_key"] == "voice")
        self.assertFalse(voice["customized"])

    async def test_main_conversation_prompt_keys_are_rejected(self) -> None:
        before = dict(self.plugin.config)
        async with self.app.test_request_context(
            "/task-prompts/update", method="POST", json={"task_key": "astrbot_reply", "prompt": "不得修改主对话"}
        ):
            result = await self.api.update_task_prompts()
        self.assertFalse(result["success"])
        self.assertEqual(400, result.http_status)
        self.assertEqual(before, self.plugin.config)

    async def test_failed_config_save_rolls_back_config_and_runtime_state(self) -> None:
        self.plugin.config = _FailingConfig({"task_prompt_overrides": '{"voice":"旧指令"}'})
        self.api = PrivateCompanionPageApi(self.plugin)

        async with self.app.test_request_context(
            "/task-prompts/update",
            method="POST",
            json={"task_key": "screen_narration", "prompt": "无法持久化的指令"},
        ):
            result = await self.api.update_task_prompts()

        self.assertFalse(result["success"])
        self.assertEqual(500, result.http_status)
        self.assertEqual('{"voice":"旧指令"}', self.plugin.config["task_prompt_overrides"])
        self.assertFalse(hasattr(self.plugin, "task_prompt_overrides"))
        self.assertEqual(1, self.plugin.config.save_count)

    async def test_false_config_save_result_rolls_back_config_and_runtime_state(self) -> None:
        self.plugin.config = _FalseSaveConfig({"task_prompt_overrides": '{"voice":"旧指令"}'})
        self.api = PrivateCompanionPageApi(self.plugin)

        async with self.app.test_request_context(
            "/task-prompts/update",
            method="POST",
            json={"task_key": "screen_narration", "prompt": "返回 False 的保存指令"},
        ):
            result = await self.api.update_task_prompts()

        self.assertFalse(result["success"])
        self.assertEqual(500, result.http_status)
        self.assertEqual('{"voice":"旧指令"}', self.plugin.config["task_prompt_overrides"])
        self.assertFalse(hasattr(self.plugin, "task_prompt_overrides"))
        self.assertEqual(1, self.plugin.config.save_count)

    async def test_concurrent_failed_save_cannot_rollback_a_later_successful_update(self) -> None:
        config = _DelayedFirstSaveConfig({"task_prompt_overrides": '{"voice":"旧指令"}'})
        self.plugin.config = config
        self.api = PrivateCompanionPageApi(self.plugin)

        async def update(task_key: str, prompt: str):
            async with self.app.test_request_context(
                "/task-prompts/update",
                method="POST",
                json={"task_key": task_key, "prompt": prompt},
            ):
                return await self.api.update_task_prompts()

        failed_task = asyncio.create_task(update("screen_narration", "首个请求会失败"))
        await asyncio.wait_for(config.first_save_started.wait(), timeout=1)
        successful_task = asyncio.create_task(update("forward_message", "第二个请求必须保留"))
        await asyncio.sleep(0)

        self.assertEqual(1, config.save_count)
        self.assertFalse(successful_task.done())
        config.release_first_save.set()
        failed, successful = await asyncio.gather(failed_task, successful_task)

        self.assertFalse(failed["success"])
        self.assertEqual(500, failed.http_status)
        self.assertTrue(successful["success"])
        self.assertEqual(2, config.save_count)
        self.assertEqual(
            {"voice": "旧指令", "forward_message": "第二个请求必须保留"},
            self.plugin.task_prompt_overrides,
        )
        self.assertEqual(self.plugin.task_prompt_overrides, json.loads(config["task_prompt_overrides"]))


class _CallContext:
    def __init__(self) -> None:
        self.kwargs: list[dict] = []

    async def llm_generate(self, **kwargs):
        self.kwargs.append(dict(kwargs))
        return SimpleNamespace(role="assistant", completion_text="ok")


class _CallHarness(TokenBudgetMixin):
    def __init__(self) -> None:
        self.context = _CallContext()
        self.config = {"task_prompt_overrides": {"voice": "只输出适合朗读的正文"}}
        self.task_prompt_overrides = {"voice": "只输出适合朗读的正文"}
        self.llm_provider_id = "primary"
        self.provider_config_mode = "precision"
        self.usage: list[dict] = []

    def _is_llm_budget_exempt_task(self, _task):
        return False

    def _daily_token_soft_limit_should_defer(self, _task):
        return False

    def _llm_daily_budget_remaining(self):
        return 100000

    def _record_llm_usage(self, **kwargs):
        self.usage.append(kwargs)

    def _model_fallback_provider_for_call(self, **_kwargs):
        return "", ""

    def _model_token_limit_route_for_call(self, **_kwargs):
        return False, None, 0

    def _model_timeout_seconds_for_call(self, **_kwargs):
        return None


class TaskPromptCallTests(unittest.IsolatedAsyncioTestCase):
    async def test_regular_call_adds_only_task_system_prompt(self) -> None:
        harness = _CallHarness()
        result = await harness._llm_call(
            "待朗读内容", provider_id="primary", task="voice", system_prompt="原有任务规则"
        )
        self.assertEqual("ok", result)
        self.assertEqual("待朗读内容", harness.context.kwargs[0]["prompt"])
        system = harness.context.kwargs[0]["system_prompt"]
        self.assertIn("原有任务规则", system)
        self.assertIn("只输出适合朗读的正文", system)
        self.assertNotIn("astrbot_reply", system)
        self.assertIn("只输出适合朗读的正文", harness.usage[0]["prompt"])

    async def test_tool_call_and_flatten_helper_share_the_same_boundary(self) -> None:
        harness = _CallHarness()
        result = await harness._llm_tool_call(
            "工具输入", tools={"name": "demo"}, provider_id="primary", task="voice"
        )
        self.assertEqual("ok", result.completion_text)
        self.assertIn("只输出适合朗读的正文", harness.context.kwargs[0]["system_prompt"])
        flattened, system = harness._apply_task_prompt_override_for_call(
            "voice", "工具输入", None, flatten_system_prompt=True
        )
        self.assertIsNone(system)
        self.assertIn("工具输入", flattened)
        self.assertIn("只输出适合朗读的正文", flattened)


if __name__ == "__main__":
    unittest.main()
