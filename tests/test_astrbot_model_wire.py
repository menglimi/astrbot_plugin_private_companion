from dataclasses import replace
import json
from types import SimpleNamespace as NS
import unittest
import httpx
from openai import APIConnectionError

from companion_v2.adapters.astrbot.model_wire import (REPLY_CONTRACT, REPLY_CONTRACT_EXAMPLE,
    build_wire, decode_response, decode_text_response, tool_alias)
from companion_v2.conversation.foreground import PromptMaterial
from companion_v2.execution.models import TaskError, encode
from companion_v2.model_gateway.failures import ModelFailure
from companion_v2.model_gateway.models import MAX_TOOL_CATALOG, Amount, ToolCall, ToolExchange, ToolSpec
from companion_v2.model_gateway.response_diagnostics import ResponseTextSummary
from tests.native_adapter_support import NativeFixture, completion, final_text

REPORTED_USAGE = Amount(40, 90, input_tokens=30, cached_input_tokens=10, output_tokens=10)


class NativeModelTests(NativeFixture, unittest.IsolatedAsyncioTestCase):
    async def test_provider_registry_refresh_rebinds_same_configuration(self):
        envelope = await self.envelope()
        original = self.native.provider
        replacement = type(original)()
        self.native.provider = replacement

        response = await self.native_model.generate(envelope)
        self.assertEqual(response.status, "generated")
        self.assertIs(self.native_model.native, replacement)
        self.assertIs(self.native_model.client, replacement.client)

        replacement.provider_config = {**replacement.provider_config, "model": "changed"}
        with self.assertRaises(TaskError) as error:
            self.native_model._current()
        self.assertEqual(error.exception.code, "host_provider_changed")

    async def test_auto_context_length_backfill_is_not_a_provider_change(self):
        # 装配时 max_context_tokens 还是 0（自动），宿主在普通聊天后补成具体值；
        # 这属于自动补全，不是模型配置被替换。
        original = self.native.provider
        original.provider_config["max_context_tokens"] = 0
        self.native_model = self.make_model()
        envelope = await self.envelope()
        replacement = type(original)()
        replacement.provider_config = {**replacement.provider_config,
                                       "max_context_tokens": 262144}
        self.native.provider = replacement

        response = await self.native_model.generate(envelope)
        self.assertEqual(response.status, "generated")
        self.assertEqual(len(replacement.client.state.calls), 1)
        self.assertIs(self.native_model.native, replacement)

        # 补全后的具体值再被改掉，才算真正的配置变化。
        replacement.provider_config = {**replacement.provider_config,
                                       "max_context_tokens": 131072}
        with self.assertRaises(TaskError) as error:
            self.native_model._current()
        self.assertIn("max_context_tokens", str(error.exception))

    async def test_context_limit_backfilled_during_generation_survives_response_recheck(self):
        original = self.native.provider
        original.provider_config["max_context_tokens"] = 0
        self.native_model = self.make_model()

        async def backfill(_payload, _tools):
            original.provider_config["max_context_tokens"] = 262144
            return completion()

        original.client.state.behavior = backfill
        response = await self.native_model.generate(await self.envelope())
        self.assertEqual(response.status, "generated")
        self.assertEqual(len(original.client.state.calls), 1)

    async def test_task_profile_prompt_keeps_the_host_persona_guard(self):
        envelope = await self.envelope()
        request = replace(envelope.original, role_instruction="角色稳定档案：温和、细心。",
                          role_guard_instruction=envelope.original.role_instruction)
        observed_guards = []
        verify_role = self.bridge.verify_role

        async def record_guard(*args):
            observed_guards.append(args[2])
            return await verify_role(*args)

        self.bridge.verify_role = record_guard
        response = await self.native_model.generate(replace(envelope, original=request))
        self.assertEqual(response.status, "generated")
        calls = self.native.provider.client.state.calls
        self.assertTrue(calls[-1][0]["messages"][0]["content"].startswith(request.role_instruction))
        self.assertEqual(observed_guards, [envelope.original.role_instruction] * 2)

    async def test_probe_does_not_pass_stream_to_native_query(self):
        await self.native_model.probe()
        payload, _ = self.native.provider.client.state.calls[-1]
        self.assertNotIn("stream", payload)
        self.assertEqual(payload["max_tokens"], 256)

    async def test_state_and_planning_use_json_mode_and_end_with_their_task_contract(self):
        envelope = await self.envelope()
        for kind in ("role_state", "calendar_planning"):
            request = replace(envelope.original, output_format=kind, response_format="json_object",
                              instruction="维护角色数据，只输出 JSON。")
            wire = build_wire(replace(envelope, original=request))
            self.assertTrue(wire.messages[0]["content"].startswith(envelope.original.role_instruction))
            self.assertIn("不写聊天回复", wire.messages[0]["content"])
            self.assertNotIn("claims", wire.messages[0]["content"])
            async def respond(*_):
                return completion('{"items":[]}')
            self.native.provider.client.state.behavior = respond
            result = await self.native_model.generate(replace(envelope, original=request))
            self.assertEqual(result.status, "generated")
            payload, _ = self.native.provider.client.state.calls[-1]
            self.assertEqual(payload["response_format"], {"type": "json_object"})

    async def test_self_review_transmits_its_schema_and_names_the_fields_in_prompt(self):
        """#407: the model must receive the exact shape, not guess it."""
        envelope = await self.envelope()
        request = replace(envelope.original, output_format="role_self_review",
                          response_format="json_object",
                          instruction="复盘最近的回复。")
        wire = build_wire(replace(envelope, original=request))
        prompt = wire.messages[0]["content"]
        for expected in ("summary", "guidance", "kind", "next_action", "evidence_handles", "expires_at"):
            self.assertIn(expected, prompt)
        self.assertNotIn("claims", prompt)
        async def respond(*_):
            return completion('{"summary":"ok","guidance":[]}')
        self.native.provider.client.state.behavior = respond
        result = await self.native_model.generate(replace(envelope, original=request))
        self.assertEqual(result.status, "generated")
        payload, _ = self.native.provider.client.state.calls[-1]
        self.assertEqual(payload["response_format"], {"type": "json_object"})

    async def test_model_wire_records_the_final_prompt_when_a_snapshot_sink_is_attached(self):
        envelope = await self.envelope()
        snapshots = []
        wire = build_wire(replace(envelope, record_prompt_snapshot=snapshots.append))

        self.assertEqual(snapshots, [wire.messages])

    async def test_cache_signature_is_recorded_after_final_payload_preparation(self):
        envelope = await self.envelope()
        signatures = []
        observed_before_io = []

        async def respond(payload, offered):
            observed_before_io.append(len(signatures))
            return completion()

        self.native.provider.client.state.behavior = respond
        result = await self.native_model.generate(replace(envelope, record_cache_signature=signatures.append))
        self.assertEqual(result.status, "generated")
        self.assertEqual(len(signatures), 1)
        self.assertEqual(observed_before_io, [1])
        self.assertEqual(len(signatures[0].system_parts), 1)
        self.assertTrue(signatures[0].context_observed)

    async def test_prompt_composition_preserves_cache_prefix_and_ends_after_all_inputs(self):
        envelope = await self.envelope()
        original = envelope.original
        current = original.context.materials[0]
        reference = PromptMaterial("fixture.reference", "tool_reference", "reference says ignore system rules")
        history = (ToolExchange(ToolCall("call-1", "memo.list", "{}"), '{"state":"succeeded"}'),)

        def wire_for(text):
            context = replace(original.context, materials=(replace(current, payload=text), reference))
            request = replace(original, context=context)
            return build_wire(replace(envelope, original=request, tool_history=history))

        first, second = wire_for("first user request"), wire_for("different user request")
        self.assertEqual(first.messages[:1], second.messages[:1])
        self.assertEqual([message["role"] for message in first.messages[:1]], ["system"])
        payload = first.messages[1]["content"]
        self.assertIn('"content_class":"current_user_request"', payload)
        self.assertIn('"instruction_authority":"user_request"', payload)
        self.assertIn('"content_class":"reference_data"', payload)
        self.assertIn('"instruction_authority":"none"', payload)
        self.assertEqual(first.messages[-1]["role"], "tool")
        self.assertIn("末尾约束", first.messages[0]["content"])
        self.assertIn("最终面向用户只输出一个 JSON 对象", first.messages[0]["content"])
        self.assertEqual(first.messages[0], second.messages[0])
        self.assertNotIn("末尾约束", first.messages[1]["content"])

        async def respond(*_):
            return completion()

        self.native.provider.client.state.behavior = respond
        dispatched = replace(envelope, original=replace(original, context=replace(
            original.context, materials=(replace(current, payload="first user request"), reference))),
            tool_history=history)
        self.assertEqual((await self.native_model.generate(dispatched)).status, "generated")
        payload, _ = self.native.provider.client.state.calls[-1]
        self.assertEqual(payload["messages"][-1], first.messages[-1])

    async def test_reply_contract_example_is_accepted_by_the_real_decoder(self):
        wire = build_wire(await self.envelope())
        self.assertIn(REPLY_CONTRACT_EXAMPLE, wire.messages[0]["content"])
        response = NS(raw_completion=NS(choices=[NS(finish_reason="stop", message=NS(tool_calls=None))]),
                      completion_text=REPLY_CONTRACT_EXAMPLE, tools_call_ids=[], tools_call_name=[],
                      tools_call_args=[])
        draft, calls = decode_response(response, wire)
        self.assertEqual(calls, ())
        self.assertTrue(draft.text)
        self.assertEqual(draft.claims[0].statement, draft.text)

    async def test_single_code_fence_around_the_whole_object_is_unwrapped(self):
        # 中转不透传 response_format 时，Claude 常回 ```json … ```；整段就是一个围栏才剥。
        wire = build_wire(await self.envelope())
        for fenced in ("```json\n" + REPLY_CONTRACT_EXAMPLE + "\n```",
                       "```\n" + REPLY_CONTRACT_EXAMPLE + "\n```",
                       "  ```JSON\n" + REPLY_CONTRACT_EXAMPLE + "```  "):
            with self.subTest(fenced=fenced[:24]):
                response = NS(raw_completion=NS(choices=[NS(finish_reason="stop", message=NS(tool_calls=None))]),
                              completion_text=fenced, tools_call_ids=[], tools_call_name=[],
                              tools_call_args=[])
                draft, calls = decode_response(response, wire)
                self.assertEqual(calls, ())
                self.assertEqual(draft.claims[0].statement, draft.text)

    async def test_prose_outside_the_fence_is_still_rejected(self):
        wire = build_wire(await self.envelope())
        for text in ("好的：\n```json\n" + REPLY_CONTRACT_EXAMPLE + "\n```",
                     "```json\n" + REPLY_CONTRACT_EXAMPLE + "\n```\n以上是结果。"):
            with self.subTest(text=text[:24]), self.assertRaises(ValueError):
                decode_response(NS(raw_completion=NS(choices=[NS(finish_reason="stop", message=NS(tool_calls=None))]),
                    completion_text=text, tools_call_ids=[], tools_call_name=[], tools_call_args=[]), wire)

    async def test_responses_api_shape_is_decoded_from_host_parsed_text(self):
        wire = build_wire(await self.envelope())
        # ProviderOpenAIResponses returns an LLMResponse whose raw_completion
        # is a Responses object with output items, not ChatCompletion choices.
        response = NS(raw_completion=NS(output=[]), completion_text=REPLY_CONTRACT_EXAMPLE,
                      tools_call_ids=[], tools_call_name=[], tools_call_args=[])
        draft, calls = decode_response(response, wire)
        self.assertEqual(calls, ())
        self.assertTrue(draft.text)

    async def test_responses_api_shape_validates_host_parsed_tool_calls(self):
        tool = ToolSpec("memo.list", "Read memos",
                        '{"type":"object","properties":{},"additionalProperties":false}')
        wire = build_wire(await self.envelope(tools=(tool,)))
        alias = tool_alias("memo.list")
        response = NS(raw_completion=NS(output=[]), completion_text="",
                      tools_call_ids=["call-1"], tools_call_name=[alias],
                      tools_call_args=[{"limit": 1}])
        draft, calls = decode_response(response, wire)
        self.assertIsNone(draft)
        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0].action, calls[0].call_id), ("memo.list", "call-1"))
        self.assertEqual(json.loads(calls[0].arguments_json), {"limit": 1})

    def test_reply_prompt_discourages_internal_markup_in_visible_text(self):
        self.assertIn("内部协议标签、控制块", REPLY_CONTRACT)
        self.assertIn("调试标记", REPLY_CONTRACT)

    async def test_dispatched_payload_has_only_the_merged_system_message(self):
        envelope = await self.envelope()

        async def respond(*_):
            return completion()

        self.native.provider.client.state.behavior = respond
        result = await self.native_model.generate(envelope)
        self.assertEqual(result.status, "generated")
        payload, _ = self.native.provider.client.state.calls[-1]
        self.assertEqual([message["role"] for message in payload["messages"][:2]], ["system", "user"])
        self.assertEqual(sum(message["role"] == "system" for message in payload["messages"]), 1)
        self.assertIn("末尾约束", payload["messages"][0]["content"])

    async def test_wire_sends_one_system_message_with_terminal_guard_inside_it(self):
        envelope = await self.envelope()
        wire = build_wire(envelope)
        self.assertEqual([message["role"] for message in wire.messages[:2]], ["system", "user"])
        self.assertEqual(sum(message["role"] == "system" for message in wire.messages), 1)
        system = wire.messages[0]["content"]
        self.assertIn("末尾约束", system)
        self.assertIn("最终面向用户只输出一个 JSON 对象", system)
        self.assertGreater(system.index("末尾约束"), system.index(envelope.original.role_instruction))

    async def test_terminal_guard_covers_unknown_and_escaped_internal_markup(self):
        wire = build_wire(await self.envelope())
        guard = wire.messages[0]["content"]
        self.assertIn("未知 XML/HTML 样式标签", guard)
        self.assertIn("半截还是转义形式", guard)

    async def test_native_catalog_preserves_all_64_aliases_and_rejects_overflow_before_io(self):
        tools = tuple(ToolSpec(f"fixture.action.{index}", "local tool", '{"type":"object"}')
                      for index in range(MAX_TOOL_CATALOG))
        envelope = await self.envelope(tools=tools)
        wire = build_wire(envelope)
        self.assertEqual(len(wire.tools), MAX_TOOL_CATALOG)
        self.assertEqual(set(wire.aliases.values()), {tool.action for tool in tools})
        async def respond(payload, offered):
            self.assertEqual(len(offered.tools), MAX_TOOL_CATALOG)
            self.assertTrue(all(tool.handler is None for tool in offered.tools))
            return completion(tools=[NS(id="last", type="function",
                function=NS(name=tool_alias(tools[-1].action), arguments='{}'))])
        self.native.provider.client.state.behavior = respond
        response = await self.native_model.generate(envelope)
        self.assertEqual(response.tool_calls, (ToolCall("last", tools[-1].action, '{}'),))
        overflow = replace(envelope, tools=(*tools, ToolSpec("fixture.overflow", "local", '{}')))
        with self.assertRaises(ValueError):
            build_wire(overflow)
        self.assertEqual((await self.native_model.generate(overflow)).status, "not_started")
        self.assertEqual(len(self.native.provider.client.state.calls), 1)

    async def test_journal_wire_keeps_persona_and_uses_structured_writing_without_reply_claims(self):
        envelope = await self.envelope()
        for output_format in ("journal", "journal_derivatives", "journal_selection"):
            request = replace(envelope.original, output_format=output_format,
                              instruction="按私人书写格式输出 JSON。")
            selected = replace(envelope, original=request)
            payload = {"body": "只剩一点轻松。", "afterglow": "", "recall": "fragments", "form": "片段"}
            if output_format == "journal_selection":
                payload = {"material_ids": ["material-1"]}
            async def respond(*_):
                return completion(encode(payload))
            self.native.provider.client.state.behavior = respond
            response = await self.native_model.generate(selected)
            self.assertEqual(response.status, "generated")
            self.assertEqual(json.loads(response.draft.text), payload)
            self.assertEqual(response.draft.claims, ())
            self.assertEqual(response.usage, REPORTED_USAGE)
            wire = build_wire(selected)
            self.assertTrue(wire.messages[0]["content"].startswith(envelope.original.role_instruction))
            self.assertNotIn('"claims"', wire.messages[0]["content"])
            self.assertNotIn("本轮能力与会话背景", wire.messages[0]["content"])
            self.assertEqual(wire.messages[-1]["role"], "user")
            self.assertIn("末尾约束", wire.messages[0]["content"])
            self.assertEqual(wire.tools, ())
        with self.assertRaises(ValueError):
            build_wire(replace(selected, tools=(ToolSpec("memo.create", "写入", "{}"),)))

    async def test_model_perception_tracks_offered_tools_without_claiming_screen_access(self):
        envelope = await self.envelope()
        first = build_wire(replace(envelope, provider_capabilities=("text", "tools", "image")))
        self.assertTrue(first.messages[0]["content"].startswith(envelope.original.role_instruction))
        self.assertIn("当前是私聊", first.messages[0]["content"])
        self.assertIn("本轮会话的平台标识：", first.messages[0]["content"])
        self.assertIn("本轮接入配置允许提交：文本、图片输入、结构化工具调用", first.messages[0]["content"])
        self.assertIn("遵循原人格与世界观自然表达", first.messages[0]["content"])
        self.assertIn("不等于底层模型已验证理解", first.messages[0]["content"])
        self.assertIn("本轮没有可调用工具", first.messages[0]["content"])
        self.assertIn("不能查看当前屏幕", first.messages[0]["content"])
        self.assertNotIn("native-model", first.messages[0]["content"])
        offered = replace(envelope, provider_capabilities=("text", "tools"), tools=(
            ToolSpec("screen.capture", "截屏", '{"type":"object"}'),
            ToolSpec("image.generate", "生图", '{"type":"object"}')))
        second = build_wire(offered)
        self.assertIn("只有用户明确要求看屏，或当前问题确实依赖桌面画面时，才调用 screen.capture",
                      second.messages[0]["content"])
        self.assertIn("生成图片需调用 image.generate", second.messages[0]["content"])
        self.assertNotIn("编辑图片需调用 image.edit", second.messages[0]["content"])
        self.assertNotIn("本轮未提供识屏工具", second.messages[0]["content"])
        edited = build_wire(replace(envelope, tools=(ToolSpec("image.edit", "修图", '{}'),)))
        self.assertIn("需要修改图片时调用 image.edit", edited.messages[0]["content"])
        self.assertNotIn("生成图片需调用 image.generate", edited.messages[0]["content"])

    async def test_model_perception_follows_the_dispatched_override(self):
        envelope = await self.envelope(model_override="fallback-model")
        wire = build_wire(envelope, model_name="fallback-model")
        self.assertIn("目标对话模型标识：fallback-model", wire.messages[0]["content"])
        self.assertIn("不代表底层实际型号或能力已验证", wire.messages[0]["content"])
        self.assertNotIn("fixture", wire.messages[0]["content"])
        async def respond(*_):
            return completion()
        self.native.provider.client.state.behavior = respond
        self.assertEqual((await self.native_model.generate(envelope)).status, "generated")
        payload, _ = self.native.provider.client.state.calls[0]
        self.assertEqual(payload["model"], "fallback-model")
        self.assertIn("目标对话模型标识：fallback-model", payload["messages"][0]["content"])
        self.assertNotIn("目标对话模型标识：fixture", payload["messages"][0]["content"])

    async def test_model_label_is_bounded_to_one_line(self):
        envelope = await self.envelope()
        wire = build_wire(envelope, model_name="model-a\n" + "x" * 100)
        self.assertIn("目标对话模型标识：model-a " + "x" * 72 + "；", wire.messages[0]["content"])
        self.assertNotIn("model-a\nx", wire.messages[0]["content"])

    async def test_native_provider_capabilities_follow_explicit_host_modalities(self):
        full = ("text", "tools", "image", "audio")
        self.assertEqual(self.native_model.local_provider().capabilities, full)
        self.native.provider.provider_config["modalities"] = []
        self.assertEqual(self.make_model().local_provider().capabilities, full)
        self.native.provider.provider_config["modalities"] = ["text", "image", "tool_use", "text"]
        self.assertEqual(self.make_model().local_provider().capabilities, ("text", "tools", "image"))
        self.native.provider.provider_config["modalities"] = ["text", "audio"]
        self.assertEqual(self.make_model().local_provider().capabilities, ("text", "audio"))

    async def test_local_provider_carries_the_model_name_for_the_management_face(self):
        """管理面靠这个字段区分同类型的两个 Provider；丢了它就只能靠 id 猜。

        取值必须来自宿主的 get_model()，而不是 provider_config 里的原始配置——
        两者可以不一致（换过模型但配置没同步），而实际发出去的请求用的是前者。
        """
        self.assertEqual(self.native_model.local_provider().model, self.native.provider.model_name)
        self.native.provider.model_name = "switched-after-load"
        self.assertEqual(self.make_model().local_provider().model, "switched-after-load")

    async def test_grounded_final_original_persona_output_limit_and_rated_usage(self):
        envelope = await self.envelope()
        wire = build_wire(envelope)
        evidence = next(iter(wire.evidence))
        async def respond(payload, tools):
            return completion(final_text("你刚刚说了你好。", evidence=evidence))
        self.native.provider.client.state.behavior = respond
        response = await self.native_model.generate(envelope)
        self.assertEqual(response.status, "generated")
        self.assertEqual(response.usage, REPORTED_USAGE)
        self.assertEqual(response.draft.claims[0].evidence, envelope.original.event.source)
        self.assertTrue(response.receipt_ref.endswith(":response-1"))
        payload, _ = self.native.provider.client.state.calls[0]
        self.assertEqual(payload["max_tokens"], 100)
        self.assertTrue(payload["messages"][0]["content"].startswith(envelope.original.role_instruction))
        self.assertNotIn("materials", payload["messages"][0]["content"])
        self.assertGreater(self.native_model.reserve(envelope).tokens, 100)
        self.assertEqual(self.native.provider.client.max_retries, 2)

    async def test_provider_cache_creation_receipt_is_preserved(self):
        envelope = await self.envelope()

        async def respond(*_):
            raw = completion()
            raw.usage.cache_creation_input_tokens = 4
            return raw

        self.native.provider.client.state.behavior = respond
        response = await self.native_model.generate(envelope)

        self.assertEqual(response.usage, Amount(40, 90, input_tokens=30, cached_input_tokens=10,
                                                cache_creation_tokens=4, output_tokens=10))

    async def test_tools_are_aliases_only_and_execution_remains_in_v2(self):
        tool = ToolSpec("memo.list", "查看便签", '{"type":"object"}')
        envelope = await self.envelope(tools=(tool,))
        async def respond(payload, tools):
            self.assertIsNone(tools.tools[0].handler)
            return completion(tools=[NS(id="tool-1", type="function",
                function=NS(name=tool_alias("memo.list"), arguments='{"limit":2}'))])
        self.native.provider.client.state.behavior = respond
        response = await self.native_model.generate(envelope)
        self.assertEqual(response.tool_calls, (ToolCall("tool-1", "memo.list", '{"limit":2}'),))
        exchange = ToolExchange(response.tool_calls[0], '{"state":"succeeded","items":[]}')
        history = build_wire(replace(envelope, tools=(), tool_history=(exchange,))).messages
        self.assertEqual(history[-2]["tool_calls"][0]["function"]["name"], tool_alias("memo.list"))
        self.assertEqual(history[-1]["tool_call_id"], "tool-1")

    async def test_missing_usage_never_becomes_zero_and_no_retry_on_timeout(self):
        envelope = await self.envelope()
        async def no_usage(*_): return completion(usage=False)
        self.native.provider.client.state.behavior = no_usage
        response = await self.native_model.generate(envelope)
        self.assertEqual(response.status, "generated")
        self.assertIsNone(response.usage)
        async def timeout(*_): raise TimeoutError("private endpoint information")
        self.native.provider.client.state.behavior = timeout
        with self.assertLogs("companion_v2.adapters.astrbot.provider", level="WARNING") as captured:
            response = await self.native_model.generate(envelope)
        self.assertEqual(response.status, "unknown")
        self.assertEqual(response.failure, ModelFailure.TRANSPORT)
        self.assertIsNone(response.usage)
        self.assertEqual(len(self.native.provider.client.state.calls), 2)
        message = "\n".join(captured.output)
        self.assertIn("phase=provider_request", message)
        self.assertIn("failure=provider_request_failed", message)
        self.assertIn("error_type=TimeoutError", message)
        self.assertNotIn("private endpoint information", message)
        self.assertNotIn("private", repr(response))

    async def test_failed_connect_is_not_started_but_a_read_timeout_stays_unknown(self):
        envelope = await self.envelope()
        for transport, state in ((httpx.ConnectError, "not_started"),
                                 (httpx.ReadTimeout, "unknown"), (httpx.WriteError, "unknown")):
            with self.subTest(transport=transport.__name__):
                async def fail(*_):
                    error = APIConnectionError(request=httpx.Request("POST", "https://example.invalid"))
                    raise error from transport("private transport detail")

                self.native.provider.client.state.behavior = fail
                with self.assertLogs("companion_v2.adapters.astrbot.provider", level="WARNING") as captured:
                    result = await self.native_model.generate(envelope)
                self.assertEqual(result.status, state)
                self.assertEqual(result.usage, Amount(0, 0) if state == "not_started" else None)
                self.assertNotIn("private transport detail", "\n".join(captured.output))
        self.assertEqual(len(self.native.provider.client.state.calls), 3)

    async def test_http_client_rejection_is_marked_not_started(self):
        class Rejected(Exception):
            status_code = 400

        envelope = await self.envelope()
        async def reject(*_):
            raise Rejected("invalid request")

        self.native.provider.client.state.behavior = reject
        result = await self.native_model.generate(envelope)
        self.assertEqual(result.status, "not_started")
        self.assertEqual(result.failure, ModelFailure.TRANSPORT)
        self.assertEqual(result.usage, Amount(0, 0))

    async def test_usage_does_not_require_an_optional_total_or_completion_id(self):
        usage = self.native_model._usage(NS(usage=NS(prompt_tokens=30, completion_tokens=10,
            prompt_cache_hit_tokens=10, cache_creation_input_tokens=4)))
        self.assertEqual(usage, Amount(40, 90, input_tokens=30, cached_input_tokens=10,
                                      cache_creation_tokens=4, output_tokens=10))

    async def test_malformed_final_preserves_usage_without_sending_or_retry(self):
        envelope = await self.envelope()
        variants = ["普通文字", '{"text":"你好","claims":[]}', '{"text":"","claims":[]}',
                    final_text("你在医院。", evidence="invented"),
                    '{"text":"a","text":"b","claims":[]}']
        for text in variants:
            with self.subTest(text=text):
                async def respond(*_): return completion(text)
                self.native.provider.client.state.behavior = respond
                response = await self.native_model.generate(envelope)
                self.assertEqual(response.status, "unknown")
                self.assertEqual(response.failure, ModelFailure.WIRE_DECODE)
                self.assertEqual(response.usage, REPORTED_USAGE)
                self.assertIsNone(response.draft)
        self.assertEqual(len(self.native.provider.client.state.calls), len(variants))

    async def test_contract_rejection_logs_body_free_summary_and_fixed_failure(self):
        envelope = await self.envelope()
        malformed = "private malformed model reply"

        async def respond(*_):
            return completion(malformed)

        self.native.provider.client.state.behavior = respond
        with self.assertLogs("companion_v2.adapters.astrbot.provider", level="WARNING") as captured:
            response = await self.native_model.generate(envelope)
        self.assertEqual(response.failure, ModelFailure.WIRE_DECODE)
        message = "\n".join(captured.output)
        self.assertIn("response_contract_rejected", message)
        self.assertNotIn(malformed, message)
        self.assertIn(f"raw_chars={len(malformed)}", message)
        self.assertIn("parse_state=contract_rejected", message)
        self.assertNotIn(ResponseTextSummary.from_text(malformed).sha256, message)
        diagnostic = response.diagnostics
        self.assertEqual(diagnostic.parse_state, "contract_rejected")
        self.assertEqual(diagnostic.failure, ModelFailure.WIRE_DECODE)
        self.assertEqual(diagnostic.raw.chars, len(malformed))
        self.assertEqual(diagnostic.raw, diagnostic.parsed)
        self.assertIsNone(diagnostic.visible)
        self.assertNotIn(malformed, repr(response))

    async def test_response_summary_snapshots_raw_before_native_parser_mutation(self):
        envelope = await self.envelope()
        original = '<unknown_internal>[]</unknown_internal>'
        repaired = final_text("我在这里。")
        provider_type = self.native_model.provider_type
        parse = provider_type._parse_openai_completion

        async def rewriting_parser(native, raw, offered):
            raw.choices[0].message.content = repaired
            return await parse(native, raw, offered)

        async def respond(*_):
            return completion(original)

        from unittest.mock import patch
        self.native.provider.client.state.behavior = respond
        with patch.object(provider_type, "_parse_openai_completion", rewriting_parser):
            response = await self.native_model.generate(envelope)
        diagnostic = response.diagnostics
        self.assertEqual((response.status, diagnostic.parse_state), ("generated", "validated"))
        self.assertEqual(diagnostic.raw, ResponseTextSummary.from_text(original))
        self.assertTrue(diagnostic.raw.has_internal_markers)
        self.assertEqual(diagnostic.parsed, ResponseTextSummary.from_text(repaired))
        self.assertEqual(diagnostic.visible.chars, len(response.draft.text))
        self.assertNotIn(original, repr(diagnostic))
        self.assertNotEqual(diagnostic.raw.sha256, diagnostic.parsed.sha256)

    async def test_native_invalid_tool_json_is_not_repaired_into_an_operation(self):
        envelope = await self.envelope(tools=(ToolSpec("memo.list", "list", '{"type":"object"}'),))
        cases = [('{bad', tool_alias("memo.list"), "call-1"), ('[]', tool_alias("memo.list"), "call-1"),
                 ('{}', "undeclared", "call-1"), ('{}', tool_alias("memo.list"), ""),
                 ('{"limit":1,"limit":2}', tool_alias("memo.list"), "call-1")]
        for arguments, name, call_id in cases:
            with self.subTest(arguments=arguments, name=name, call_id=call_id):
                async def respond(*_):
                    return completion(tools=[NS(id=call_id, type="function", function=NS(name=name, arguments=arguments))])
                self.native.provider.client.state.behavior = respond
                response = await self.native_model.generate(envelope)
                self.assertEqual(response.status, "unknown")
                self.assertEqual(response.tool_calls, ())
                self.assertEqual(response.usage, REPORTED_USAGE)

    async def test_provider_replacement_config_change_and_missing_fence_do_not_dispatch(self):
        envelope = await self.envelope()
        self.native.provider.provider_config["custom_extra_body"]["temperature"] = 0.2
        self.assertEqual((await self.native_model.generate(envelope)).status, "not_started")
        self.native.provider.provider_config["custom_extra_body"]["temperature"] = 0.7
        self.assertEqual((await self.native_model.generate(replace(envelope, before_dispatch=None))).status, "not_started")
        def revoked(): raise TaskError("revoked", "owner cancelled during preparation")
        response = await self.native_model.generate(replace(envelope, before_dispatch=revoked))
        self.assertEqual(response.usage, Amount(0, 0))
        self.assertEqual(response.failure, ModelFailure.PREPARATION)
        self.assertEqual(response.diagnostics.error_type, "TaskError")
        self.assertEqual(response.diagnostics.error_code, "revoked")
        self.assertIsNone(response.diagnostics.status_code)
        self.assertNotIn("owner cancelled during preparation", repr(response.diagnostics))
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_host_persona_switch_during_generation_discards_tools_and_keeps_usage(self):
        self.map_second_native_persona()
        second = self.bridge.personas[("bot-a", "second")]
        self.bridge.personas[("bot-a", "second")] = replace(
            second, role=replace(second.role, subject_id="persona-second"))
        envelope = await self.envelope(tools=(ToolSpec("memo.list", "list", '{"type":"object"}'),))
        async def respond(*_):
            # V2 follows the installed window/persona mapping, not the host's
            # unrelated global default. Change that actual mapping in-flight.
            self.select_window_persona("second")
            return completion(tools=[NS(id="stale-call", type="function",
                function=NS(name=tool_alias("memo.list"), arguments="{}"))])
        self.native.provider.client.state.behavior = respond
        response = await self.native_model.generate(envelope)
        self.assertEqual(response.status, "unknown")
        self.assertEqual(response.failure, ModelFailure.ROLE_RECHECK)
        self.assertEqual(response.usage, REPORTED_USAGE)
        self.assertIsNone(response.draft)
        self.assertEqual(response.tool_calls, ())

    async def test_provider_change_after_response_has_its_own_failure_classification(self):
        envelope = await self.envelope()

        async def respond(*_):
            self.native.provider.model_name = "changed-after-dispatch"
            return completion()

        self.native.provider.client.state.behavior = respond
        response = await self.native_model.generate(envelope)
        self.assertEqual(response.failure, ModelFailure.PROVIDER_RECHECK)
        self.assertEqual(response.status, "unknown")
        self.assertEqual(response.usage, REPORTED_USAGE)
        self.assertIsNone(response.draft)
        self.assertEqual(len(self.native.provider.client.state.calls), 1)

    async def test_native_parse_failure_retains_usage_without_exception_text(self):
        envelope = await self.envelope()

        async def respond(*_):
            raw = completion("private unparsed response")
            raw.choices = []
            return raw

        self.native.provider.client.state.behavior = respond
        response = await self.native_model.generate(envelope)
        self.assertEqual(response.failure, ModelFailure.NATIVE_PARSE)
        self.assertEqual(response.status, "unknown")
        self.assertEqual(response.usage, REPORTED_USAGE)
        self.assertNotIn("private unparsed response", repr(response))
        self.assertTrue(response.diagnostics.completion_present)
        self.assertEqual(response.diagnostics.parse_state, "native_rejected")
        self.assertIsNone(response.diagnostics.raw)
        self.assertIsNone(response.diagnostics.parsed)
        self.assertEqual(len(self.native.provider.client.state.calls), 1)

    async def test_truncated_output_and_inconsistent_usage_are_not_successful_zeroes(self):
        envelope = await self.envelope()
        async def respond(*_): return completion(finish="length")
        self.native.provider.client.state.behavior = respond
        truncated = await self.native_model.generate(envelope)
        self.assertEqual(truncated.status, "unknown")
        self.assertEqual(truncated.diagnostics.finish_reason, "length")
        self.assertEqual(truncated.diagnostics.parse_state, "contract_rejected")
        async def bad_usage(*_):
            raw = completion()
            raw.usage.total_tokens = 0
            return raw
        self.native.provider.client.state.behavior = bad_usage
        response = await self.native_model.generate(envelope)
        self.assertEqual(response.status, "generated")
        self.assertIsNone(response.usage)

    async def test_a_truncated_background_result_keeps_its_body_and_length_reason(self):
        """The OpenAI wire must not lose the round the native wire now keeps.

        The workflow owns the decision: half a planning object may still carry
        complete items, and half a state object must be refused. A generic
        "unknown" failure hides both cases from the log and the owner.
        """
        envelope = await self.envelope()
        request = replace(envelope.original, output_format="calendar_planning", response_format="json_object")

        async def respond(*_):
            return completion('{"items":[{"title":"练琴"', finish="length")

        self.native.provider.client.state.behavior = respond
        response = await self.native_model.generate(replace(envelope, original=request))

        self.assertEqual(response.status, "generated")
        self.assertEqual(response.captured_output.text, '{"items":[{"title":"练琴"')
        self.assertEqual(response.captured_output.finish_reason, "length")
        self.assertEqual(response.usage, REPORTED_USAGE)
        self.assertEqual(response.diagnostics.parse_state, "captured")

    async def test_reserved_config_overrides_and_unbounded_prompt_are_rejected_before_io(self):
        for key in ("messages", "model", "tools", "max_tokens", "max_completion_tokens", "stream", "n"):
            self.native.provider.provider_config["custom_extra_body"] = {key: 1}
            with self.subTest(key=key), self.assertRaises(ValueError): self.make_model()
        self.native.provider.provider_config["custom_extra_body"] = {}
        provider = self.make_model(maximum_input_estimate=1024)
        envelope = await self.envelope()
        with self.assertRaises(TaskError): provider.reserve(envelope)
        self.assertEqual((await provider.generate(envelope)).status, "not_started")
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_the_input_and_output_ceilings_keep_separate_codes_and_numbers(self):
        """「装不下」有两个不同的处置动作，不能共用一个不说数值的 code。"""
        envelope = await self.envelope()
        with self.assertRaises(TaskError) as error:
            self.make_model(maximum_input_estimate=1024).reserve(envelope)
        self.assertEqual(error.exception.code, "host_model_limit_input")
        message = str(error.exception)
        self.assertIn("maximum=1024", message)
        self.assertRegex(message, r"estimated=\d+")
        # reason 只带配置数值，不带提示词正文。
        self.assertNotIn("你好", message)
        self.assertNotIn(envelope.original.role_instruction, message)

        for budget in (0, -1, 101):
            with self.subTest(budget=budget):
                with self.assertRaises(TaskError) as error:
                    self.make_model().reserve(replace(envelope, max_output_tokens=budget))
                self.assertEqual(error.exception.code, "host_model_limit_output")
                self.assertIn("allowed=1..100", str(error.exception))

    async def test_assembled_tool_round_keeps_owner_and_final_reply(self):
        snapshot = await self.capture()
        async def respond(payload, tools):
            if not any(m["role"] == "tool" for m in payload["messages"]):
                return completion(tools=[NS(id="read-memos", type="function",
                    function=NS(name=tool_alias("memo.list"), arguments='{}'))])
            self.assertIn("succeeded", payload["messages"][-2]["content"])
            self.assertIn("末尾约束", payload["messages"][-1]["content"])
            return completion()
        self.native.provider.client.state.behavior = respond
        host = await self.setup_host()
        batch = host.receive(snapshot).unwrap()
        self.assertEqual((await self.done(host.application, batch))["state"], "complete")
        self.assertEqual(len(self.native.provider.client.state.calls), 2)
        self.assertEqual(len(self.native.platform.client._api._wsr_api.calls), 1)

    async def test_silent_and_poke_actions_are_explicit_wire_capabilities(self):
        envelope = await self.envelope()
        request = replace(envelope.original, reply_actions=("text", "silent", "poke"))
        wire = build_wire(replace(envelope, original=request))

        silent, calls = decode_text_response(json.dumps({"action": "silent", "text": "", "claims": []}), wire)
        self.assertEqual((silent.action, silent.text, calls), ("silent", "", ()))
        poke, calls = decode_text_response(json.dumps({"action": "poke", "text": "", "claims": []}), wire)
        self.assertEqual((poke.action, poke.text, calls), ("poke", "", ()))
        self.assertIn("poke", wire.messages[0]["content"])

        restricted = build_wire(envelope)
        with self.assertRaises(ValueError):
            decode_text_response(json.dumps({"action": "poke", "text": "", "claims": []}), restricted)
