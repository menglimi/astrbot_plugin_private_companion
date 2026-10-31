"""Native core tools retain real owner permissions and effect receipts."""

from dataclasses import replace
import asyncio
import base64
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from tests import test_astrbot_staging_host as _host_import
from astrbot.core.agent.message import Message, ToolCall
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor, call_local_llm_tool
from astrbot.core.pipeline.process_stage.follow_up import _ACTIVE_AGENT_RUNNERS
from astrbot.core.provider.entities import LLMResponse, ProviderRequest
from mcp.types import CallToolResult

from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA, prepare_core_reply_event
from astrbot_host.core_reply_tools import (CoreReplyToolSession, close_core_reply_tools, group_tool_manifest,
                                           inject_core_reply_tools, native_tool_name)
from astrbot_host.core_reply_telemetry import observe_core_agent_begin, prepare_core_reply_telemetry
from astrbot_host.core_reply_tool_dispatch import observe_core_tool_dispatch_begin, restore_core_tool_dispatch
from companion_v2.agenda.service import AgendaService
from companion_v2.execution.models import TaskError, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind
from companion_v2.model_gateway.models import MAX_TOOL_CATALOG
from tests.test_astrbot_core_reply import FakeEvent
from tests.test_external_tools import ExternalFixture
from tests.test_management_access import SCOPE
from tests.test_semantic_integration import picture


class CoreReplyToolsTests(ExternalFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.app = NS(runtime=self.runtime, tasks=self.tasks, messages=self.messages, tools=self.tools,
                      agenda=AgendaService(self.runtime), require_running=lambda: require(self.tasks.check_generation()))
        self.addAsyncCleanup(close_core_reply_tools, self.app)
        for action in ("memo.create", "memo.read", "memo.list", "memo.edit", "memo.complete", "memo.pin",
                       "memo.unpin", "reminder.create", "reminder.read", "reminder.list", "reminder.cancel",
                       "reminder.reschedule", "operation.lookup"):
            self.grant(self.important, SCOPE, action)
        self.authorized = 0
        self.authorize_result = Outcome.success(True)
        self.event, self.request = self.prepare()

    def prepare(self, event_id="native-event"):
        event = FakeEvent()

        async def authorize(_prepared):
            self.authorized += 1
            return self.authorize_result

        prepared = {"context": self.context, "event_id": event_id}
        prepare_core_reply_event(event, prepared, authorize=authorize)
        request = NS(func_tool=None, system_prompt="original persona")
        inject_core_reply_tools(event, request, application=self.app)
        return event, request

    def wrapper(self, action, arguments, call_id="native-call", event=None):
        call = ToolCall(id=call_id, function=ToolCall.FunctionBody(name=native_tool_name(action),
                                                               arguments=json.dumps(arguments)))
        return NS(context=NS(event=event or self.event), messages=[Message(role="assistant", tool_calls=[call])])

    async def call_native(self, action, arguments, call_id="native-call", request=None, event=None):
        tool = (request or self.request).func_tool.get_tool(native_tool_name(action))
        wrapper = self.wrapper(action, arguments, call_id, event)
        output = [value async for value in call_local_llm_tool(wrapper, tool.call, "call", **arguments)]
        return json.loads(output[-1])

    def test_group_tool_manifest_is_static_and_ordered(self):
        offered = tuple(NS(spec=NS(action=action)) for action in (
            "news.lookup", "group.memory.query", "slang.search",
            "group.participation.choose", "group.behavior.learn", "turn.evidence"))
        lean = [item.spec.action for item in group_tool_manifest(offered, "lean")]
        self.assertEqual(lean, ["group.participation.choose", "group.memory.query", "slang.search"])
        standard = [item.spec.action for item in group_tool_manifest(offered, "standard")]
        self.assertEqual(standard, ["group.participation.choose", "group.memory.query", "slang.search",
                                    "group.behavior.learn", "turn.evidence"])
        full = [item.spec.action for item in group_tool_manifest(offered, "full")]
        self.assertEqual(full, [item.spec.action for item in offered])

    def test_tool_order_is_canonical_for_gemini_and_anthropic_serialisers(self):
        async def handler(event, **arguments):
            return "native"

        # AstrBot sorts only the OpenAI schema; Gemini/Anthropic keep insertion
        # order, so host registration order would otherwise reach the provider.
        later = FunctionTool("zz_native", "Later tool", {"type": "object"}, handler)
        earlier = FunctionTool("aa_native", "Earlier tool", {"type": "object"}, handler)
        self.request.func_tool = ToolSet(tools=[later, earlier])
        inject_core_reply_tools(self.event, self.request, application=self.app)
        names = [tool.name for tool in self.request.func_tool.tools]
        self.assertEqual(names, sorted(names))
        self.assertIn("aa_native", names)

    def test_append_keeps_native_handlers_and_does_not_mutate_shared_catalog(self):
        async def existing_handler(event, **arguments):
            return "native"

        native = FunctionTool("native_existing", "Native tool", {"type": "object"}, existing_handler)
        original = ToolSet(tools=[native])
        self.request.func_tool = original
        first = inject_core_reply_tools(self.event, self.request, application=self.app)
        second = inject_core_reply_tools(self.event, self.request, application=self.app)
        self.assertEqual(first, second)
        self.assertEqual(original.tools, [native])
        self.assertIs(self.request.func_tool.get_tool("native_existing"), native)
        self.assertIs(native.handler, existing_handler)
        self.assertEqual(len(self.request.func_tool.tools), len(first) + 1)

    def test_unadmitted_core_runner_is_never_wrapped(self):
        from astrbot_host.core_reply_tool_dispatch import CORE_TOOL_DISPATCH_EXTRA
        event = FakeEvent()
        event.unified_msg_origin = "unadmitted-core-runner"
        wrapper = NS(context=NS(event=event))
        step, dispatch = object(), object()
        runner = NS(run_context=wrapper, step=step, _handle_function_tools=dispatch)
        _ACTIVE_AGENT_RUNNERS[event.unified_msg_origin] = runner
        self.addCleanup(_ACTIVE_AGENT_RUNNERS.pop, event.unified_msg_origin, None)
        self.assertIsNone(observe_core_tool_dispatch_begin(event, wrapper))
        self.assertIs(runner.step, step)
        self.assertIs(runner._handle_function_tools, dispatch)
        self.assertIsNone(event.get_extra(CORE_TOOL_DISPATCH_EXTRA))

    async def test_native_agenda_command_replays_owner_receipt_without_reply_batch(self):
        created = await self.call_native("memo.create", {"title": "散步", "body": "傍晚一起出去"})
        self.assertEqual(created["state"], "succeeded")
        payload = created["payload"]
        same = await self.call_native("memo.create", {"title": "散步", "body": "傍晚一起出去"})
        self.assertEqual(same["operation_id"], created["operation_id"])
        self.assertTrue(same["replayed"])
        read = await self.call_native("memo.read", {"memo_id": payload["object_id"]}, "read-memo")
        self.assertEqual(read["payload"]["body"], "傍晚一起出去")
        changed = await self.call_native("memo.edit", {"memo_id": payload["object_id"], "expected_revision": 1,
                                                   "changes": {"body": "饭后出去"}}, "edit-memo")
        self.assertEqual(changed["payload"]["revision"], 2)
        self.assertEqual(self.runtime.read_one("SELECT COUNT(*) n FROM conversation_batches", ())["n"], 0)
        self.assertEqual(self.runtime.read_one("SELECT COUNT(*) n FROM model_rounds", ())["n"], 0)
        self.assertEqual(self.event.sent, [])

    async def test_external_operation_uses_real_task_and_original_receipt(self):
        generation = self.tasks.generation
        result = await self.call_native("device.play", {"query": "music"})
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(result["effect"], "committed")
        self.assertEqual(result["operation_id"], "op-" + result["invocation_id"])
        self.assertEqual(len(self.extension_calls), 1)
        self.assertEqual(self.extension_calls[0].context, self.context)
        self.assertEqual(self.tasks.generation, generation)
        task = self.runtime.read_one("SELECT state, record_json FROM execution_tasks WHERE task_id = ?",
                                     (result["invocation_id"],))
        self.assertEqual(task["state"], "succeeded")
        self.assertEqual(json.loads(task["record_json"])["effect"], "committed")
        row = self.tools.external._row(result["invocation_id"])
        receipt, material = self.tools.external.snapshot(row, self.context, self.clock.now())
        self.assertEqual(receipt["operation_id"], result["operation_id"])
        self.assertIn("57%", material.payload)
        with self.assertRaises(TaskError):
            self.tools.external.snapshot(row, replace(self.context, actor=self.core), self.clock.now())
        self.assertEqual(self.runtime.read_one("SELECT COUNT(*) n FROM conversation_batches", ())["n"], 0)
        self.assertEqual(self.runtime.read_one("SELECT COUNT(*) n FROM model_rounds", ())["n"], 0)
        self.assertEqual(self.event.sent, [])

    async def test_native_external_tool_remaps_a_changed_provider_binding(self):
        changed = replace(self.bindings["device.play"], version="2", generation="host-reloaded")
        self.bindings["device.play"] = changed
        result = await self.call_native("device.play", {"query": "music"})
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(len(self.extension_calls), 1)
        self.assertEqual(self.extension_calls[0].descriptor.binding, changed)

    async def test_unknown_external_action_cannot_be_replayed_under_another_id_or_event(self):
        async def unknown(invocation):
            return self.response(invocation, state="unknown")

        self.extension_behavior = unknown
        result = await self.call_native("device.play", {"query": "music"})
        self.assertEqual(result["state"], "unknown")
        lookup = await self.call_native("operation.lookup", {"operation_id": result["operation_id"]}, "wrong-owner-lookup")
        self.assertEqual(lookup["code"], "invalid_agenda_id")
        self.assertEqual(lookup["state"], "failed")
        original = self.tools.external._row(result["invocation_id"])
        receipt, _ = self.tools.external.snapshot(original, self.context, self.clock.now())
        self.assertEqual(receipt["state"], "unknown")
        again = await self.call_native("device.play", {"query": "music"}, "another-call")
        self.assertEqual(again["code"], "external_effect_unresolved")
        event, request = self.prepare("another-event")
        again = await self.call_native("device.play", {"query": "music"}, "third-call", request=request, event=event)
        self.assertEqual(again["code"], "external_effect_unresolved")
        self.assertEqual(len(self.extension_calls), 1)

    async def test_revocation_and_event_mismatch_prevent_dispatch(self):
        self.authorize_result = Outcome.failed("revoked", "this event is no longer authorized")
        denied = await self.call_native("device.play", {"query": "music"})
        self.assertEqual(denied["code"], "revoked")
        self.authorize_result = Outcome.success(True)
        denied = await self.call_native("device.play", {"query": "music"}, event=FakeEvent())
        self.assertEqual(denied["code"], "core_tool_event_mismatch")
        self.assertEqual(self.extension_calls, [])

    async def test_actual_native_call_id_cannot_be_rebound_to_different_arguments(self):
        await self.call_native("device.play", {"query": "music"})
        changed = await self.call_native("device.play", {"query": "different"})
        self.assertEqual(changed["code"], "tool_call_conflict")
        self.assertEqual(len(self.extension_calls), 1)

    async def test_model_arguments_cannot_supply_an_operation_without_native_tool_request(self):
        tool = self.request.func_tool.get_tool(native_tool_name("device.play"))
        denied = json.loads(await tool.call(NS(context=NS(event=self.event), messages=[]), query="music"))
        self.assertEqual(denied["code"], "core_tool_call_missing")
        self.assertEqual(self.extension_calls, [])

    async def test_invalid_native_arguments_return_tool_failure_and_keep_reply_available(self):
        denied = await self.call_native("device.play", {"query": 7})
        self.assertEqual(denied["code"], "invalid_tool_arguments")
        self.assertEqual(self.extension_calls, [])

    async def test_group_skip_latches_delivery_guard_when_authorization_fails(self):
        event = FakeEvent()
        prepared = {"context": self.context, "event_id": "group-skip-failed"}

        async def stopped(_prepared):
            return Outcome.failed("host_not_running", "test host stopped")

        prepare_core_reply_event(event, prepared, authorize=stopped)
        state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        session = CoreReplyToolSession.__new__(CoreReplyToolSession)
        session.event, session.state, session.prepared = event, state, prepared
        session.context = NS(scope=NS(kind=ScopeKind.GROUP))
        session.application = self.app
        session.native_run_context, session.native_calls = None, []
        arguments = {"mode": "skip", "reason": "话题已经结束"}
        wrapper = self.wrapper("group.participation.choose", arguments, event=event)

        result = json.loads(await session.call("group.participation.choose", wrapper, arguments))

        self.assertEqual(result["code"], "host_not_running")
        self.assertEqual(state["group_participation"]["mode"], "skip")
        state["delivery_guard_active"] = True
        await event.send("不应发送")
        self.assertEqual(event.sent, [])

    async def test_images_use_native_mcp_contract_and_original_generation_is_reused(self):
        async def with_image(invocation):
            return self.response(invocation, images=(picture(),))

        self.extension_behavior = with_image
        tool = self.request.func_tool.get_tool(native_tool_name("device.play"))
        arguments = {"query": "picture"}
        wrapper = self.wrapper("device.play", arguments)
        outputs = [value async for value in call_local_llm_tool(wrapper, tool.call, "call", **arguments)]
        received = outputs[-1]
        self.assertIsInstance(received, CallToolResult)
        self.assertEqual(json.loads(received.content[0].text)["effect"], "committed")
        self.assertEqual(base64.b64decode(received.content[1].data), picture().content)
        again = await tool.call(wrapper, **arguments)
        self.assertEqual(again.content, received.content)
        self.assertEqual(len(self.extension_calls), 1)
        self.assertEqual(self.runtime.read_one("SELECT COUNT(*) n FROM conversation_batches", ())["n"], 0)
        self.assertEqual(self.event.sent, [])

    async def test_executor_close_stops_only_native_worker_and_preserves_shared_generation(self):
        entered, stopped = asyncio.Event(), asyncio.Event()

        async def hanging(_invocation):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        self.extension_behavior = hanging
        generation = self.tasks.generation
        pending = asyncio.create_task(self.call_native("device.play", {"query": "music"}))
        await asyncio.wait_for(entered.wait(), 2)
        await close_core_reply_tools(self.app)
        await asyncio.wait_for(stopped.wait(), 2)
        await asyncio.wait_for(pending, 2)
        self.assertEqual(self.tasks.generation, generation)
        self.assertTrue(self.tasks.check_generation().is_success)

    async def run_real_core_runner(self, schema_mode, *, telemetry="enabled"):
        """Exercise SDK response dispatch before it appends assistant tool calls."""
        event, request = self.prepare("real-core-runner-" + schema_mode)
        event.unified_msg_origin = "native-runner:" + schema_mode
        req = ProviderRequest(prompt="播放音乐", system_prompt=request.system_prompt, func_tool=request.func_tool)
        calls = []
        expected_id = "real-parameter-call" if schema_mode == "skills_like" else "real-full-call"
        async def text_chat(**arguments):
            calls.append(arguments)
            if len(calls) == 1:
                return LLMResponse(role="assistant", completion_text="", tools_call_name=[native_tool_name("device.play")],
                    tools_call_args=[{} if schema_mode == "skills_like" else {"query": "music"}],
                    tools_call_ids=["real-light-call" if schema_mode == "skills_like" else expected_id])
            if schema_mode == "skills_like" and len(calls) == 2:
                return LLMResponse(role="assistant", completion_text="", tools_call_name=[native_tool_name("device.play")],
                    tools_call_args=[{"query": "music"}], tools_call_ids=[expected_id])
            return LLMResponse(role="assistant", completion_text="播放好了")

        provider = NS(provider_config={"id": "real-core-model", "modalities": ["text"]},
                      get_model=lambda: "real-model", text_chat=text_chat)
        wrapper = NS(context=NS(event=event), messages=[], tool_call_timeout=5)
        if telemetry == "enabled":
            prepare_core_reply_telemetry(event, req, application=self.app, now=self.tasks.now)
        elif telemetry == "failed":
            with patch("astrbot_host.core_reply_telemetry.prepare_core_reply_telemetry", side_effect=OSError("statistics unavailable")) as prepare:
                try:
                    prepare(event, req, application=self.app, now=self.tasks.now)
                except OSError:
                    pass

        class Hooks(BaseAgentRunHooks):
            async def on_agent_begin(self, run_context):
                observe_core_tool_dispatch_begin(event, run_context)
                observe_core_agent_begin(event, run_context)

            async def on_agent_done(self, run_context, response):
                restore_core_tool_dispatch(event, run_context)

        runner = ToolLoopAgentRunner()
        await runner.reset(provider, req, wrapper, FunctionToolExecutor(), Hooks(), tool_schema_mode=schema_mode)
        _ACTIVE_AGENT_RUNNERS[event.unified_msg_origin] = runner
        self.addCleanup(_ACTIVE_AGENT_RUNNERS.pop, event.unified_msg_origin, None)
        self.assertEqual([m for m in wrapper.messages if getattr(m, "tool_calls", None)], [])
        for _ in range(2):
            _responses = [item async for item in runner.step()]
        self.assertTrue(runner.done())
        self.assertEqual(len(self.extension_calls), 1)
        self.assertEqual(self.extension_calls[0].tool_call_id, expected_id)
        self.assertEqual(runner.final_llm_resp.completion_text, "播放好了")
        results = [json.loads(m.content) for m in wrapper.messages if m.role == "tool"]
        self.assertEqual(results[0]["state"], "succeeded", results)
        self.assertEqual(self.runtime.read_one("SELECT COUNT(*) n FROM conversation_batches", ())["n"], 0)
        self.assertEqual(self.runtime.read_one("SELECT COUNT(*) n FROM model_rounds", ())["n"], 0)
        self.assertEqual(event.sent, [])
        self.assertNotIn("_handle_function_tools", runner.__dict__)

    async def test_real_sdk_runner_dispatch_binds_current_tool_response_before_history_append(self):
        await self.run_real_core_runner("full")

    async def test_real_sdk_skills_like_requery_uses_executed_call_instead_of_light_schema_id(self):
        await self.run_real_core_runner("skills_like")

    async def test_real_sdk_tools_work_without_optional_telemetry(self):
        await self.run_real_core_runner("full", telemetry="missing")

    async def test_real_sdk_skills_like_tools_work_when_telemetry_preparation_fails(self):
        await self.run_real_core_runner("skills_like", telemetry="failed")


class WideCoreReplyToolCatalogTests(ExternalFixture, unittest.IsolatedAsyncioTestCase):
    def make_extension(self, runtime):
        original = self.descriptors[0]
        self.descriptors = (original,) + tuple(replace(original,
            tool=replace(original.tool, action=f"device.catalog.read.{index}"))
            for index in range(MAX_TOOL_CATALOG))
        self.bindings = {item.tool.action: item.binding for item in self.descriptors}
        return super().make_extension(runtime)

    async def test_core_reply_calls_tail_tool_from_a_catalog_larger_than_the_plugin_gateway_budget(self):
        app = NS(runtime=self.runtime, tasks=self.tasks, messages=self.messages, tools=self.tools,
                 agenda=AgendaService(self.runtime), require_running=lambda: require(self.tasks.check_generation()))
        self.addAsyncCleanup(close_core_reply_tools, app)
        event = FakeEvent()

        async def authorize(_prepared):
            return Outcome.success(True)

        prepare_core_reply_event(event, {"context": self.context, "event_id": "wide-catalog-event"},
                                 authorize=authorize)
        request = NS(func_tool=None, system_prompt="original persona")
        offered = inject_core_reply_tools(event, request, application=app)
        self.assertTrue(set(self.extension.providers).issubset(offered))
        self.assertGreater(len(request.func_tool.tools), MAX_TOOL_CATALOG)
        tail = self.descriptors[-1]
        arguments = {"query": "status"}
        call = ToolCall(id="tail-call", function=ToolCall.FunctionBody(name=native_tool_name(tail.tool.action),
                                                                     arguments=json.dumps(arguments)))
        wrapper = NS(context=NS(event=event), messages=[Message(role="assistant", tool_calls=[call])])
        tool = request.func_tool.get_tool(native_tool_name(tail.tool.action))
        output = [value async for value in call_local_llm_tool(wrapper, tool.call, "call", **arguments)]
        self.assertEqual(json.loads(output[-1])["state"], "succeeded")
        self.assertEqual(len(self.extension_calls), 1)
        self.assertEqual(self.extension_calls[0].descriptor, tail)
        self.assertEqual(self.extension_calls[0].context, self.context)
        self.assertEqual(self.runtime.read_one("SELECT COUNT(*) n FROM conversation_batches", ())["n"], 0)
        self.assertEqual(self.runtime.read_one("SELECT COUNT(*) n FROM model_calls", ())["n"], 0)


if __name__ == "__main__":
    unittest.main()
