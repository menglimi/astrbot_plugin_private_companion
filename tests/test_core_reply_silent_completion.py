"""A native group skip ends the runner without speaking or losing new input."""

import asyncio
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock

from tests import astrbot_data_root  # noqa: F401

from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.astr_agent_run_util import run_agent
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
from astrbot.core.pipeline.process_stage.follow_up import _ACTIVE_AGENT_RUNNERS
from astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal import InternalAgentSubStage
from astrbot.core.provider.entities import ProviderRequest

from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA, prepare_core_reply_event
from astrbot_host.core_reply_tool_dispatch import (
    CORE_TOOL_DISPATCH_EXTRA, observe_core_tool_dispatch_begin, restore_core_tool_dispatch,
)
from astrbot_host.core_reply_tool_guard import install_core_reply_tool_guard, close_core_reply_tool_guard
from astrbot_host.core_reply_tools import CoreReplyFunctionTool, CoreReplyToolSession, _OfferedTool, native_tool_name
from astrbot_host.core_reply_turn_gate import install_core_reply_turn, close_core_reply_turn
from companion_v2.conversation.participation import GroupParticipation
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind
from tests.test_core_reply_turn_gate import GateEvent, ScriptedProvider, plain_text


CHOOSE = native_tool_name("group.participation.choose")
LEARN = native_tool_name("group.behavior.learn")
HOST = "astrbot_execute_shell"
DRAFT = "这段是没有发出的工具轮草稿。"
OLD = "这是之前已经说过的话。"
INPUT = "你们先聊，我在旁边看看。"


def choose_round(*, mode="skip", text=DRAFT, before=(), after=()):
    return {"text": text, "tool": CHOOSE, "calls": [
        *((name, {}, "before-" + str(i)) for i, name in enumerate(before)),
        (CHOOSE, {"mode": mode, "reason": "这条消息在与别人交谈"}, "choice"),
        *((name, {}, "after-" + str(i)) for i, name in enumerate(after)),
    ]}


class ParticipationProvider(ScriptedProvider):
    def _response(self, spec, *, is_chunk=False):
        response = super()._response(spec, is_chunk=is_chunk)
        if spec.get("calls"):
            names, arguments, identifiers = zip(*spec["calls"])
            response.tools_call_name = list(names)
            response.tools_call_args = list(arguments)
            response.tools_call_ids = list(identifiers)
        return response


class CountingTool(FunctionTool):
    def __init__(self, name):
        super().__init__(name=name, description="Local test tool", parameters={"type": "object"})
        self.calls = 0

    async def call(self, context, **kwargs):
        self.calls += 1
        return "tool completed"


class SilentCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def run_turn(self, rounds, *, streaming=False, max_step=6, allowed=True,
                       follow_up_on=None, external_completion=False, buffered=False,
                       direct_runner=False, fail_done=False, cancel_tool=False):
        event = GateEvent()
        prepared = {"channel": "group", "event_ref": "silent-turn"}

        async def authorize(_):
            return Outcome.success(True) if allowed else Outcome.failed("host_not_running", "Host stopped")

        prepare_core_reply_event(event, prepared, authorize=authorize)
        core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        core["delivery_guard_active"] = True
        session = CoreReplyToolSession.__new__(CoreReplyToolSession)
        session.event, session.state, session.prepared = event, core, prepared
        session.context = NS(scope=NS(kind=ScopeKind.GROUP))
        session.application = NS(require_running=lambda: None)
        session.native_response = session.native_run_context = None
        session.native_calls = []
        offered = _OfferedTool(GroupParticipation.catalog[0], "participation")
        session.catalog = lambda: (offered,)
        core["_tool_session"] = session
        host, learning = CountingTool(HOST), CountingTool(LEARN)
        request = ProviderRequest(
            prompt=INPUT,
            contexts=[{"role": "user", "content": "之前的问题"},
                      {"role": "assistant", "content": OLD}],
            conversation=NS(cid="silent-conversation", token_usage=0),
            func_tool=ToolSet(tools=[CoreReplyFunctionTool(session, offered), host, learning]),
        )
        provider = ParticipationProvider(rounds)
        wrapper = NS(context=NS(event=event), messages=[], tool_call_timeout=5)
        observation = {"done": 0, "tools": [], "tickets": []}
        gate = None
        runner = ToolLoopAgentRunner()
        current_task = asyncio.current_task()

        class Hooks(BaseAgentRunHooks):
            async def on_agent_begin(self, run_context):
                nonlocal gate
                observe_core_tool_dispatch_begin(event, run_context)
                gate = install_core_reply_turn(event, run_context)
                install_core_reply_tool_guard(event, run_context)

            async def on_tool_start(self, run_context, tool, arguments):
                observation["tools"].append(tool.name)
                if tool.name == follow_up_on:
                    observation["tickets"].append(runner.follow_up(message_text="这是刚到的新消息"))
                if cancel_tool and tool.name == CHOOSE:
                    # Cancel the caller while a later tool is waiting, after an
                    # earlier result would normally have consumed the follow-up.
                    current_task.cancel()
                    await asyncio.Future()

            async def on_tool_end(self, run_context, tool, arguments, result):
                if tool.name == CHOOSE:
                    observation["choice_result"] = result

            async def on_agent_done(self, run_context, response):
                observation["done"] += 1
                observation["done_messages"] = [message.model_dump() for message in run_context.messages]
                if fail_done:
                    raise RuntimeError("fixture done hook failed")
                await close_core_reply_turn(event, response, release=True)
                close_core_reply_tool_guard(event, run_context)
                restore_core_tool_dispatch(event, run_context)

        await runner.reset(provider, request, wrapper, FunctionToolExecutor(), Hooks(), streaming=streaming)
        if external_completion:
            runner.request_silent_completion = Mock(side_effect=AssertionError("Host API must not be used"))
        _ACTIVE_AGENT_RUNNERS[event.unified_msg_origin] = runner
        self.addCleanup(_ACTIVE_AGENT_RUNNERS.pop, event.unified_msg_origin, None)
        chains = []
        try:
            if direct_runner:
                async for response in runner.step_until_done(max_step):
                    if response.type in ("llm_result", "streaming_delta"):
                        chain = response.data["chain"]
                        if chain.chain and chain.type != "reasoning":
                            chains.append(chain)
                            await event.send(chain)
            else:
                async for chain in run_agent(runner, max_step, False, False,
                                             buffer_intermediate_messages=buffered):
                    if chain is not None and chain.chain:
                        chains.append(chain)
                        await event.send(chain)
        except asyncio.CancelledError:
            if not cancel_tool:
                raise
            return NS(runner=runner, observation=observation, gate=gate, event=event)
        finally:
            _ACTIVE_AGENT_RUNNERS.pop(event.unified_msg_origin, None)

        storage = NS(conv_manager=NS(update_conversation=AsyncMock()))
        await InternalAgentSubStage._save_to_history(
            storage, event, request, runner.get_final_llm_resp(), wrapper.messages, runner.stats,
        )
        storage.conv_manager.update_conversation.assert_awaited_once()
        history = storage.conv_manager.update_conversation.await_args.kwargs["history"]
        return NS(runner=runner, provider=provider, event=event, core=core, gate=gate,
                  request=request, observation=observation, host=host, learning=learning,
                  history=history, messages=wrapper.messages,
                  visible="".join(plain_text(chain) for chain in chains))

    def assert_silent(self, result, *, model_calls=1):
        self.assertEqual(result.provider.calls, model_calls)
        self.assertTrue(result.runner.done())
        self.assertFalse(result.runner.was_aborted())
        dispatch = result.event.get_extra(CORE_TOOL_DISPATCH_EXTRA)
        self.assertTrue(dispatch["silently_completed"])
        self.assertTrue(dispatch["restored"])
        for name in ("_handle_function_tools", "step", "_consume_follow_up_notice"):
            self.assertNotIn(name, vars(result.runner))
        self.assertEqual(result.observation["done"], 1)
        self.assertEqual(result.runner.get_final_llm_resp().completion_text, "")
        self.assertEqual(result.event.sent, [])
        self.assertEqual(result.visible, "")
        self.assertEqual(result.gate["released"], 0)
        text = json.dumps(result.history, ensure_ascii=False)
        self.assertIn(INPUT, text)
        self.assertIn(OLD, text)
        self.assertNotIn(DRAFT, text)
        self.assertNotIn("Output stopped.", text)
        self.assertNotIn("Stop output.", text)
        self.assertNotIn("step budget exhausted", text)
        calls = [call["id"] for message in result.history for call in message.get("tool_calls", ()) or ()]
        results = [message["tool_call_id"] for message in result.history if message["role"] == "tool"]
        self.assertCountEqual(calls, results)
        self.assertTrue(calls)
        completed = result.observation["done_messages"]
        self.assertCountEqual(
            [call["id"] for message in completed for call in message.get("tool_calls", ()) or ()],
            [message["tool_call_id"] for message in completed if message["role"] == "tool"],
        )
        self.assertEqual(sum(message["role"] == "tool" for message in completed), len(results))

    async def test_skip_stops_before_next_host_tool_and_model_in_both_stream_modes(self):
        for streaming in (False, True):
            for max_step in (1, 6):
                with self.subTest(streaming=streaming, max_step=max_step):
                    result = await self.run_turn([
                        choose_round(), {"text": "不该再查工具", "tool": HOST}, {"text": "不该生成正文"},
                    ], streaming=streaming, max_step=max_step)
                    self.assert_silent(result)
                    self.assertEqual(result.host.calls, 0)

    async def test_later_skip_removes_only_this_turn_unsent_tool_prose(self):
        result = await self.run_turn([
            {"text": DRAFT, "tool": HOST, "call_id": "history-read"}, choose_round(),
            {"text": "不该生成正文"},
        ], buffered=True)
        self.assert_silent(result, model_calls=2)
        self.assertEqual(result.host.calls, 1)

    async def test_locked_skip_with_failed_authorization_also_finishes(self):
        result = await self.run_turn([choose_round(), {"text": "不该重试"}], allowed=False)
        self.assert_silent(result)
        self.assertIn("host_not_running", json.dumps(result.history))
        self.assertEqual(result.core["group_participation"]["mode"], "skip")

    async def test_learning_batch_finishes_and_new_messages_are_not_consumed(self):
        for before, after in (((LEARN,), ()), ((), (LEARN,))):
            with self.subTest(before=before, after=after):
                result = await self.run_turn([choose_round(before=before, after=after)], follow_up_on=LEARN)
                self.assert_silent(result)
                self.assertEqual(result.learning.calls, 1)
                ticket, = result.observation["tickets"]
                self.assertIsNotNone(ticket)
                self.assertTrue(ticket.resolved.is_set())
                self.assertFalse(ticket.consumed)
                self.assertNotIn("这是刚到的新消息", json.dumps(result.history, ensure_ascii=False))

    async def test_reply_choice_continues_and_saves_the_answer(self):
        result = await self.run_turn([choose_round(mode="reply"), {"text": "这是正常回复。"}])
        self.assertEqual(result.provider.calls, 2)
        self.assertEqual(result.core["group_participation"]["mode"], "reply")
        self.assertEqual(result.visible, "这是正常回复。")
        self.assertIn("这是正常回复。", json.dumps(result.history, ensure_ascii=False))

    async def test_rejected_mixed_skip_does_not_end_the_runner(self):
        result = await self.run_turn([choose_round(after=(HOST,)), {"text": "业务操作已完成。"}])
        self.assertEqual(result.provider.calls, 2)
        self.assertNotIn("group_participation", result.core)
        self.assertEqual(result.host.calls, 1)
        self.assertEqual(result.visible, "业务操作已完成。")
        self.assertIn("group_participation_skip_alone", json.dumps(result.history))

    async def test_host_completion_extension_is_not_used(self):
        result = await self.run_turn([choose_round(), {"text": "不应生成"}], external_completion=True)
        self.assert_silent(result)
        result.runner.request_silent_completion.assert_not_called()

    async def test_direct_runner_finishes_at_the_step_budget_without_another_model_call(self):
        result = await self.run_turn([choose_round()], max_step=1, direct_runner=True)
        self.assert_silent(result)

    async def test_reply_batch_consumes_follow_up_once_and_continues(self):
        result = await self.run_turn([
            choose_round(mode="reply", before=(LEARN,)), {"text": "收到新消息了。"},
        ], follow_up_on=LEARN)
        ticket, = result.observation["tickets"]
        self.assertTrue(ticket.consumed)
        self.assertTrue(ticket.resolved.is_set())
        self.assertEqual(result.provider.calls, 2)
        self.assertEqual(json.dumps(result.history, ensure_ascii=False).count("这是刚到的新消息"), 1)
        self.assertEqual(result.visible, "收到新消息了。")

    async def test_cancelled_batch_releases_deferred_input_and_restores_runner(self):
        result = await self.run_turn([
            choose_round(before=(LEARN,)),
        ], follow_up_on=LEARN, cancel_tool=True)
        ticket, = result.observation["tickets"]
        self.assertTrue(ticket.resolved.is_set())
        self.assertFalse(ticket.consumed)
        self.assertEqual(result.observation["done"], 0)
        self.assertTrue(result.gate["closed"])
        for name in ("_handle_function_tools", "step", "_consume_follow_up_notice",
                     "_iter_llm_responses_with_fallback"):
            self.assertNotIn(name, vars(result.runner))

    async def test_failed_done_hook_still_releases_new_input(self):
        with self.assertLogs("astrbot_host.core_reply_tool_dispatch", level="ERROR"):
            result = await self.run_turn([
                choose_round(before=(LEARN,)),
            ], follow_up_on=LEARN, fail_done=True)
        self.assert_silent(result)
        ticket, = result.observation["tickets"]
        self.assertTrue(ticket.resolved.is_set())
        self.assertFalse(ticket.consumed)


if __name__ == "__main__":
    unittest.main()
