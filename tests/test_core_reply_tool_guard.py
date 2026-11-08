"""A tool this request never declared must be named, and must not cost the turn.

Production audit (2026-11-07 22:41:53 and 2026-11-08 14:59:56): the model asked
for ``companion_v2_group_participation_choose`` in a turn whose request declared
no companion tool at all -- V2 had declined the turn and handed it to AstrBot's
own pipeline -- so the host logged ``未找到指定的工具`` and executed nothing. The
name came from the history this plugin writes: every companion turn stores its
assistant tool calls, so a later request without those tools shows the model an
example of calling one.

Three properties are asserted here, and each of them fails before the fix:

* the miss is reported once, with the tool name, the declared set and the turn's
  identity, instead of disappearing into the host's one Chinese warning;
* the round keeps a matching tool result even when the request declares no tools
  at all, because the host returns before writing one and an unpaired assistant
  tool call makes the *next* request fail with HTTP 400 -- the whole reply lost;
* a declared tool round is untouched: same execution count, same result, no line.

The history half is covered too: the stored example is removed together with its
result when this request does not declare that tool, and kept when it does.
"""

import logging
import unittest
from contextlib import contextmanager
from types import SimpleNamespace as NS

from tests import astrbot_data_root  # noqa: F401  (must precede astrbot imports)

from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.astr_agent_run_util import run_agent
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
from astrbot.core.pipeline.process_stage.follow_up import _ACTIVE_AGENT_RUNNERS
from astrbot.core.provider.entities import ProviderRequest

from astrbot_host.core_reply_history_guard import repair_request_history
from astrbot_host.core_reply_tool_guard import (
    CORE_TOOL_GUARD_EXTRA, close_core_reply_tool_guard, install_core_reply_tool_guard,
    mark_managed_turn, managed_turn, summarize_names,
)
from tests.test_core_reply_turn_gate import (
    GateEvent, ParticipationTool, ScriptedProvider, TOOL, plain_text, text_round, tool_round,
)

HOST_TOOL = "astr_kb_search"
ANSWER = "德语不是考试科目，别问了。"
BODY = "我先想想怎么说。"


@contextmanager
def captured_warnings(name):
    """Collect this module's warning lines without asserting that one exists.

    A property and a log line are asserted separately so a failure says which of
    the two broke: the turn keeping its round, or the miss becoming visible.
    """
    logger = logging.getLogger(name)
    lines = []

    class _Collect(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())

    handler = _Collect()
    previous, logger.level = logger.level, logging.WARNING
    logger.addHandler(handler)
    try:
        yield lines
    finally:
        logger.removeHandler(handler)
        logger.level = previous


def tool_round_for(name, *, text=BODY, call_id="call-1"):
    """One model round that asks for ``name`` before it answers."""
    return {"text": text, "tool": name, "shape": "openai", "call_id": call_id}


class HostTool(FunctionTool):
    """A tool this request really declares, so the round has somewhere to land."""

    def __init__(self):
        super().__init__(name=HOST_TOOL, description="knowledge search",
                         parameters={"type": "object", "properties": {}})
        self.calls = 0

    async def call(self, context, **kwargs):
        self.calls += 1
        return "knowledge"


def field(message, name, default=None):
    if isinstance(message, dict):
        return message.get(name, default)
    return getattr(message, name, default)


def tool_call_ids(messages):
    """Every tool_call_id answered in this history."""
    ids = set()
    for message in messages:
        if field(message, "role") != "tool":
            continue
        identifier = field(message, "tool_call_id")
        if isinstance(identifier, str):
            ids.add(identifier)
    return ids


def assistant_call_ids(messages):
    ids = set()
    for message in messages:
        if field(message, "role") != "assistant":
            continue
        for call in field(message, "tool_calls") or ():
            identifier = field(call, "id")
            if isinstance(identifier, str):
                ids.add(identifier)
    return ids


class GuardRunnerTests(unittest.IsolatedAsyncioTestCase):
    """The guard on a real ``ToolLoopAgentRunner`` driven by the real ``run_agent``."""

    async def run_turn(self, rounds, *, request, event=None, application=None, install=True,
                       max_step=6):
        event = event or GateEvent()
        provider = ScriptedProvider(rounds)
        wrapper = NS(context=NS(event=event), messages=[], tool_call_timeout=5)
        state = {}

        class Hooks(BaseAgentRunHooks):
            async def on_agent_begin(self, run_context):
                if install:
                    state["guard"] = install_core_reply_tool_guard(event, run_context)

            async def on_agent_done(self, run_context, response):
                close_core_reply_tool_guard(event, run_context)

        runner = ToolLoopAgentRunner()
        await runner.reset(provider, request, wrapper, FunctionToolExecutor(), Hooks(),
                           streaming=False)
        _ACTIVE_AGENT_RUNNERS[event.unified_msg_origin] = runner
        self.addCleanup(_ACTIVE_AGENT_RUNNERS.pop, event.unified_msg_origin, None)
        chains = []
        try:
            async for chain in run_agent(runner, max_step, False, False):
                chains.append(chain)
        finally:
            _ACTIVE_AGENT_RUNNERS.pop(event.unified_msg_origin, None)
        return {"event": event, "runner": runner, "provider": provider,
                "messages": wrapper.messages, "guard": state.get("guard"),
                "text": "".join(plain_text(chain) for chain in chains if chain is not None)}

    async def test_an_undeclared_tool_round_is_reported_and_the_turn_still_answers(self):
        """V2-declined turn: the host has its own tools, none of them ours."""
        event = GateEvent()
        mark_managed_turn(event, channel="group", event_ref="host-turn-1")
        host_tool = HostTool()
        request = ProviderRequest(prompt="为什么不考德语", func_tool=ToolSet(tools=[host_tool]))
        with captured_warnings("astrbot_host.core_reply_tool_guard") as lines:
            result = await self.run_turn(
                [tool_round_for(TOOL, call_id="call-9"), text_round(ANSWER)], request=request,
                event=event)
        # The turn still says its answer, and the undeclared tool ran nothing.
        self.assertIn(ANSWER, result["text"])
        self.assertEqual(host_tool.calls, 0)
        self.assertIn("call-9", tool_call_ids(result["messages"]))
        # The miss is named once, with the tool, the declared set and the turn.
        line = "\n".join(lines)
        self.assertIn(TOOL, line)
        self.assertIn("tool_not_in_request", line)
        self.assertIn(summarize_names({HOST_TOOL}), line)
        self.assertIn("host-turn-1", line)
        self.assertEqual(len([item for item in lines if TOOL in item]), 1, line)

    async def test_a_request_without_tools_still_pairs_the_call_it_cannot_run(self):
        """The host returns before writing a result; an unpaired call kills the turn."""
        event = GateEvent()
        mark_managed_turn(event, channel="group", event_ref="host-turn-2")
        request = ProviderRequest(prompt="为什么不考德语", func_tool=None)
        with captured_warnings("astrbot_host.core_reply_tool_guard") as lines:
            result = await self.run_turn(
                [tool_round_for(TOOL, text="", call_id="call-7"), text_round(ANSWER)],
                request=request, event=event)
        # The invariant a provider enforces: every requested call has a result.
        self.assertIn("call-7", tool_call_ids(result["messages"]))
        self.assertLessEqual(assistant_call_ids(result["messages"]),
                             tool_call_ids(result["messages"]))
        self.assertIn(ANSWER, result["text"])
        self.assertEqual(result["guard"]["filled"], 1)
        self.assertIn(TOOL, "\n".join(lines))

    async def test_a_declared_tool_round_is_untouched(self):
        """The normal path keeps exactly one execution and no guard line."""
        event = GateEvent()
        mark_managed_turn(event, channel="group", event_ref="host-turn-3")
        tool = ParticipationTool()
        request = ProviderRequest(prompt="在吗", func_tool=ToolSet(tools=[tool]))
        result = await self.run_turn([tool_round(text=BODY), text_round(ANSWER)],
                                     request=request, event=event)
        self.assertEqual(tool.calls, 1)
        self.assertIn(ANSWER, result["text"])
        guard = result["guard"] or {}
        self.assertEqual(guard.get("warned", {}), {})
        self.assertEqual(guard.get("filled", 0), 0)
        results = [field(message, "content") for message in result["messages"]
                   if field(message, "role") == "tool"]
        self.assertTrue(results)
        self.assertNotIn("not found", str(results))

    async def test_an_unmanaged_event_never_gets_a_guard(self):
        """Another plugin's chat is not ours to instrument."""
        event = GateEvent()
        request = ProviderRequest(prompt="hi", func_tool=None)
        result = await self.run_turn([text_round(ANSWER)], request=request, event=event)
        self.assertIsNone(result["guard"])
        self.assertIsNone(event.get_extra(CORE_TOOL_GUARD_EXTRA))
        self.assertEqual(result["text"], ANSWER)


class HistoryDeclarationTests(unittest.TestCase):
    """The stored example is removed with its result, and only when undeclared."""

    def event(self):
        event = GateEvent()
        mark_managed_turn(event, channel="group", event_ref="host-turn-4")
        return event

    def contexts(self):
        return [
            {"role": "user", "content": "为什么不考德语"},
            {"role": "assistant", "content": "我先看看。",
             "tool_calls": [{"id": "call-1", "type": "function",
                             "function": {"name": TOOL, "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call-1", "content": '{"state":"succeeded"}'},
            {"role": "assistant", "content": "德语不是科目。"},
        ]

    def request(self, tools):
        return NS(contexts=self.contexts(), func_tool=ToolSet(tools=tools) if tools is not None else None)

    def test_an_undeclared_companion_example_is_removed_with_its_result(self):
        event = self.event()
        request = self.request([HostTool()])
        with captured_warnings("astrbot_host.core_reply_history_guard") as lines:
            repair_request_history(event, request)
        self.assertEqual([message["role"] for message in request.contexts],
                         ["user", "assistant", "assistant"])
        self.assertEqual(request.contexts[1]["content"], "我先看看。")
        self.assertNotIn("tool_calls", request.contexts[1])
        # The model's own words stay; the tool machinery that fed them does not.
        self.assertEqual(request.contexts[2]["content"], "德语不是科目。")
        line = "\n".join(lines)
        self.assertIn(TOOL, line)
        self.assertIn("tool_not_in_request", line)
        self.assertIn("host-turn-4", line)

    def test_a_request_without_any_tool_still_drops_the_example(self):
        event = self.event()
        request = self.request(None)
        with captured_warnings("astrbot_host.core_reply_history_guard"):
            repair_request_history(event, request)
        self.assertNotIn("call-1", str(request.contexts))
        self.assertNotIn(TOOL, str(request.contexts))

    def test_a_declared_companion_call_is_kept_verbatim(self):
        event = self.event()
        request = self.request([ParticipationTool()])
        repair_request_history(event, request)
        self.assertEqual(len(request.contexts), 4)
        self.assertEqual(request.contexts[1]["tool_calls"][0]["function"]["name"], TOOL)
        self.assertEqual(request.contexts[2]["tool_call_id"], "call-1")

    def test_a_turn_outside_the_plugin_is_left_alone(self):
        event = GateEvent()
        self.assertIsNone(managed_turn(event))
        request = self.request([HostTool()])
        repair_request_history(event, request)
        self.assertEqual(len(request.contexts), 4)
        self.assertEqual(request.contexts[1]["tool_calls"][0]["function"]["name"], TOOL)


if __name__ == "__main__":
    unittest.main()
