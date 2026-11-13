"""One reply per turn, whichever provider shape a tool round arrives in.

The agent loop delivers the prose of a model round before it runs that round's
tools, so a turn that calls a tool and then answers says two things. These tests
drive the real ``ToolLoopAgentRunner`` and the real ``run_agent`` loop, and read
the sequence a chat would actually see, for the four turn shapes that matter:

* A the tool round and the answer say the same thing -- one message;
* B the answer differs from the tool round's prose -- only the answer;
* C no tool at all -- untouched;
* D the turn ends without text -- the prose still has to be said, once.

Provider shapes differ in the tool round's ``role`` and in where the text lives
(plain, in the chain, or in streamed fragments). A shape that classified a round
by ``role`` would fail at least one of them; the gate asks the host's own
question instead, so the assertions below are the same for every shape.

The turn can also end with no final round at all: the step budget runs out, the
user stops the turn, the provider raises, or the provider answers with an error.
Those exits are the ones that used to swallow the draft, so they get their own
tests: a turn that wrote something must never send nothing.
"""

import inspect
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from tests import astrbot_data_root  # noqa: F401  (must precede astrbot imports)

from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.message import ThinkPart
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.astr_agent_run_util import run_agent
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
from astrbot.core.message.components import At, Image, Plain, Reply
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.pipeline.process_stage.follow_up import _ACTIVE_AGENT_RUNNERS
from astrbot.core.provider.entities import LLMResponse, ProviderRequest

from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA
from astrbot_host.core_reply_tool_dispatch import (
    CORE_TOOL_DISPATCH_EXTRA, observe_core_tool_dispatch_begin, restore_core_tool_dispatch,
)
from astrbot_host.core_reply_turn_gate import (
    RESPONSE_STREAM, _blank, _downstream_buffers, _restore_blank,
    _turn_round, close_core_reply_turn, discard_skipped_core_reply,
    install_core_reply_turn, turn_gate,
)

BODY = "我先看看这事的来龙去脉。"
OTHER = "其实我刚查了，是服务器在维护。"
LATER = "那就等它维护完。"
TOOL = "companion_v2_group_participation_choose"
SHAPES = ("openai", "anthropic", "gemini")


def tool_round(text=BODY, *, shape="openai", media=False, decorated=None, call_id="call-1"):
    return {"text": text, "tool": TOOL, "shape": shape, "media": media,
            "decorated": decorated, "call_id": call_id}


def text_round(text):
    return {"text": text}


def silent_round():
    return {"text": ""}


def failed_round(text="模型这边出了点问题。"):
    return {"text": text, "role": "err"}


def raising_round():
    return {"raise": RuntimeError("provider exploded")}


async def close_turn(event, response=None, release=True):
    """Call the gate's close hook, tolerating the pre-fix synchronous signature."""
    try:
        result = close_core_reply_turn(event, response, release=release)
    except TypeError:
        result = close_core_reply_turn(event)
    if inspect.isawaitable(result):
        await result


class ParticipationTool(FunctionTool):
    def __init__(self):
        super().__init__(name=TOOL, description="Group participation",
                         parameters={"type": "object", "properties": {}})
        self.calls = 0
        self.runner = None
        self.stop_after = False
        self.skip_after = 0

    async def call(self, context, **kwargs):
        self.calls += 1
        if self.skip_after and self.calls >= self.skip_after:
            context.context.event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["group_participation"] = {
                "mode": "skip", "reason": "保持旁听",
            }
        if self.stop_after and self.runner is not None:
            self.runner.request_stop()
        return '{"state":"succeeded","effect":"none","payload":{}}'


class ScriptedProvider:
    """Answers each model round the way one provider shape would."""

    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.calls = 0
        self.responses = []
        self.provider_config = {"id": "scripted-model", "modalities": ["text"]}

    def get_model(self):
        return "scripted-model"

    def _next(self):
        spec = self.rounds[min(self.calls, len(self.rounds) - 1)]
        self.calls += 1
        return spec

    def _response(self, spec, *, is_chunk=False):
        text = spec.get("text") or ""
        chain = None
        if spec.get("media"):
            parts = [Plain(text)] if text else []
            parts.append(Image(file="fixture.png"))
            chain = MessageChain(chain=parts)
        elif spec.get("decorated") == "at":
            parts = [Plain(text)] if text else []
            parts.append(At(qq="12345"))
            chain = MessageChain(chain=parts)
        elif spec.get("decorated") == "reply":
            parts = [Plain(text)] if text else []
            parts.append(Reply(id="quoted-1"))
            chain = MessageChain(chain=parts)
        elif text and spec.get("shape") == "gemini":
            # Gemini answers in parts, so the text arrives as an attached chain.
            chain = MessageChain(chain=[Plain(text)])
        tool = spec.get("tool")
        response = LLMResponse(
            role=spec.get("role") or ("tool" if tool and spec.get("shape") != "anthropic" else "assistant"),
            completion_text="" if chain is not None else text,
            result_chain=chain,
            tools_call_name=[tool] if tool else [],
            tools_call_args=[{}] if tool else [],
            tools_call_ids=[spec.get("call_id") or "call-1"] if tool else [],
            reasoning_content=spec.get("reasoning"),
            is_chunk=is_chunk,
            id="scripted-%d" % self.calls,
        )
        self.responses.append(response)
        return response

    async def text_chat(self, **_):
        spec = self._next()
        if spec.get("raise"):
            raise spec["raise"]
        return self._response(spec)

    async def text_chat_stream(self, **_):
        spec = self._next()
        if spec.get("raise"):
            raise spec["raise"]
        fragments = spec.get("fragments")
        if fragments is None:
            fragments = [spec["text"]] if spec.get("text") and not spec.get("media") else []
        for fragment in fragments:
            yield self._response({**spec, "text": fragment, "media": False}, is_chunk=True)
        # A streaming provider's final response is not the text itself.
        yield self._response({**spec, "text": ""})


class GateEvent:
    """Only what the agent loop and the gate touch on a real message event."""

    def __init__(self, *, buffered=True):
        self.unified_msg_origin = "test:turn-gate"
        self.platform_meta = NS(name="aiocqhttp", id="qq-main")
        self.sent = []
        self.result = None
        self.stopped = False
        self.trace = NS(record=lambda *args, **kwargs: None)
        self._extra = {"enable_streaming": False} if buffered else {}

    def get_extra(self, key, default=None):
        return self._extra.get(key, default)

    def set_extra(self, key, value):
        self._extra[key] = value

    def get_platform_name(self):
        return self.platform_meta.name

    def get_platform_id(self):
        return self.platform_meta.id

    def is_stopped(self):
        return self.stopped

    def set_result(self, result):
        self.result = result

    def get_result(self):
        return self.result

    def clear_result(self):
        self.result = None

    async def send(self, message):
        self.sent.append(message)

    async def send_streaming(self, *args, **kwargs):
        self.sent.append(args)


def plain_text(chain):
    getter = getattr(chain, "get_plain_text", None)
    return getter() if callable(getter) else ""


def sent_text(event):
    return "".join(plain_text(message) for message in event.sent
                   if hasattr(message, "get_plain_text"))


class TurnGateTests(unittest.IsolatedAsyncioTestCase):
    async def run_turn(self, rounds, *, streaming=False, buffered=True, observe=True, install=True,
                       event=None, max_step=8, stop_after=False, skip_after=0, contexts=None,
                       tool_schema_mode="full"):
        """Run one turn end to end and return what a chat would see."""
        event = event or GateEvent(buffered=buffered)
        event.set_extra(CORE_REPLY_CONTEXT_EXTRA, {"prepared": {"channel": "group", "event_ref": "turn-gate"}})
        provider = ScriptedProvider(rounds)
        tool = ParticipationTool()
        tool.stop_after = stop_after
        tool.skip_after = skip_after
        request = ProviderRequest(prompt="在吗", func_tool=ToolSet(tools=[tool]), contexts=contexts or [])
        wrapper = NS(context=NS(event=event), messages=[], tool_call_timeout=5)
        gate = {}

        class Hooks(BaseAgentRunHooks):
            async def on_agent_begin(self, run_context):
                if observe:
                    observe_core_tool_dispatch_begin(event, run_context)
                if install:
                    gate["state"] = install_core_reply_turn(event, run_context)

            async def on_agent_done(self, run_context, response):
                await close_turn(event, response, release=True)
                restore_core_tool_dispatch(event, run_context)

        runner = ToolLoopAgentRunner()
        await runner.reset(provider, request, wrapper, FunctionToolExecutor(), Hooks(), streaming=streaming,
                           tool_schema_mode=tool_schema_mode)
        tool.runner = runner
        _ACTIVE_AGENT_RUNNERS[event.unified_msg_origin] = runner
        self.addCleanup(_ACTIVE_AGENT_RUNNERS.pop, event.unified_msg_origin, None)
        chains = []
        try:
            async for chain in run_agent(runner, max_step, False, False):
                chains.append(chain)
        finally:
            _ACTIVE_AGENT_RUNNERS.pop(event.unified_msg_origin, None)
        return {"event": event, "runner": runner, "provider": provider, "tool": tool,
                "messages": wrapper.messages, "gate": gate.get("state"), "chains": chains,
                "visible": [plain_text(chain) for chain in chains], "text": "".join(
                    plain_text(chain) for chain in chains), "sent": sent_text(event)}

    # -- A: the tool round and the answer say the same thing -------------------
    async def test_same_text_twice_is_delivered_once_for_every_provider_shape(self):
        for shape in SHAPES:
            for streaming in (False, True):
                with self.subTest(shape=shape, streaming=streaming):
                    result = await self.run_turn([tool_round(shape=shape), text_round(BODY)],
                                                 streaming=streaming)
                    self.assertEqual(result["text"], BODY, result["visible"])
                    self.assertEqual(result["provider"].calls, 2)
                    self.assertEqual(result["tool"].calls, 1)

    # -- B: the answer differs from the prose ---------------------------------
    async def test_the_answer_replaces_the_tool_round_prose(self):
        for shape in SHAPES:
            for streaming in (False, True):
                with self.subTest(shape=shape, streaming=streaming):
                    result = await self.run_turn([tool_round(shape=shape), text_round(OTHER)],
                                                 streaming=streaming)
                    self.assertEqual(result["text"], OTHER, result["visible"])
                    self.assertNotIn(BODY, result["text"])

    # -- C: no tool at all -----------------------------------------------------
    async def test_a_turn_without_tools_is_delivered_once(self):
        result = await self.run_turn([text_round(BODY)])
        self.assertEqual(result["visible"], [BODY])
        self.assertEqual(result["provider"].calls, 1)
        self.assertEqual(result["tool"].calls, 0)

    # -- D: the turn ends without text ----------------------------------------
    async def test_a_turn_that_ends_silently_still_says_its_draft_once(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                result = await self.run_turn([tool_round(), silent_round()], streaming=streaming)
                self.assertEqual(result["text"], BODY, result["visible"])
                self.assertEqual(result["gate"]["released"], 1)

    async def test_only_the_latest_draft_is_released(self):
        result = await self.run_turn([tool_round(BODY), tool_round(LATER, call_id="call-2"), silent_round()])
        self.assertEqual(result["text"], LATER, result["visible"])
        self.assertEqual(result["gate"]["rounds_with_tools"], 2)

    # -- the turn ends with no final round at all -----------------------------
    async def test_a_turn_that_exhausts_its_steps_still_says_its_draft_once(self):
        # The step budget runs out while the model is still calling tools. The
        # host forces one last round with the tool set removed; that round can
        # never dispatch, so the turn ends there and the draft must go out.
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                result = await self.run_turn(
                    [tool_round(BODY), tool_round(LATER, call_id="call-2"),
                     tool_round("第三句。", call_id="call-3")],
                    max_step=2, streaming=streaming)
                self.assertEqual(result["text"], "第三句。", result["visible"])
                self.assertEqual(result["gate"]["released"], 1)
                self.assertIsNone(result["gate"]["draft"])

    async def test_a_cancelled_turn_still_says_its_draft_once(self):
        # The user stops the turn while the tool runs. The abort response is not
        # delivered as text, so the draft is the only thing the turn can say.
        result = await self.run_turn([tool_round(), text_round(OTHER)], stop_after=True)
        self.assertEqual(result["gate"]["released"], 1)
        self.assertIn(BODY, result["sent"], "the draft must reach the event")
        self.assertIsNone(result["gate"]["draft"])

    async def test_a_provider_failure_still_says_its_draft_once(self):
        # The fallback wrapper turns a provider exception into an error response;
        # the draft must ride out with it instead of being dropped.
        result = await self.run_turn([tool_round(), raising_round()])
        self.assertEqual(result["gate"]["released"], 1)
        self.assertIn(BODY, result["text"], result["visible"])
        self.assertTrue(result["gate"]["closed"])

    async def test_a_provider_crash_releases_the_draft_and_closes_the_gate(self):
        # An exception that escapes the response stream ends the turn with no
        # round left. The draft goes out through the event and the gate closes,
        # so the next turn on this event installs a fresh wrapper.
        event = GateEvent()
        event.set_extra(CORE_REPLY_CONTEXT_EXTRA, {"prepared": {}})
        event.set_extra(CORE_TOOL_DISPATCH_EXTRA, {"runner": None, "run_context": None})

        class CrashingRunner:
            streaming = False
            _step_budget_max = 8
            _step_budget_used = 1

            def __init__(self):
                self.run_context = NS(context=NS(event=event))

            async def _iter_llm_responses_with_fallback(self):
                raise RuntimeError("provider exploded")
                yield  # pragma: no cover

            async def _handle_function_tools(self, *args, **kwargs):
                yield  # pragma: no cover

            async def step(self):
                yield  # pragma: no cover

            def done(self):
                return False

            def was_aborted(self):
                return False

        runner = CrashingRunner()
        event.set_extra(CORE_TOOL_DISPATCH_EXTRA, {"runner": runner, "run_context": runner.run_context})
        with patch("astrbot_host.core_reply_turn_gate.discover_runner", return_value=runner):
            gate = install_core_reply_turn(event, runner.run_context)
        self.assertIsNotNone(gate)
        gate["draft"] = {"fragments": [], "text": BODY, "chain": None}
        with self.assertRaises(RuntimeError):
            async for _ in runner._iter_llm_responses_with_fallback():
                pass
        self.assertEqual(gate["released"], 1)
        self.assertIn(BODY, sent_text(event), "the draft must reach the event")
        self.assertTrue(gate["closed"])
        self.assertNotIn(RESPONSE_STREAM, runner.__dict__)

    # -- never swallow what the turn did say ---------------------------------
    async def test_a_media_round_is_delivered_as_it_is(self):
        result = await self.run_turn([tool_round(media=True), text_round(OTHER)])
        self.assertEqual(result["visible"], [BODY, OTHER])
        messages = [part for chain in result["chains"] for part in getattr(chain, "chain", ())]
        self.assertTrue(any(isinstance(part, Image) for part in messages), "the picture must survive")

    async def test_an_error_round_still_says_the_draft_it_replaced(self):
        # The provider fails after the tool round wrote prose. The error round is
        # not a tool round, but dropping the draft there would lose the only text
        # the turn produced; the draft rides out with the error instead.
        result = await self.run_turn([tool_round(), failed_round()])
        self.assertIn(BODY, result["text"], result["visible"])
        self.assertIn("模型这边出了点问题。", result["text"])
        self.assertEqual(result["gate"]["released"], 1)

    async def test_a_round_without_text_keeps_the_earlier_draft(self):
        # Round two calls a tool and says nothing; round three says nothing either.
        result = await self.run_turn([tool_round(BODY), tool_round("", call_id="call-2"), silent_round()])
        self.assertEqual(result["text"], BODY, result["visible"])

    async def test_two_turns_with_the_same_text_both_speak(self):
        first = await self.run_turn([text_round(BODY)])
        second = await self.run_turn([text_round(BODY)])
        self.assertEqual(first["visible"], [BODY])
        self.assertEqual(second["visible"], [BODY])

    # -- a mention or a quote is never withheld -------------------------------
    async def test_a_tool_round_with_a_mention_is_never_withheld(self):
        # Withholding the round would drop the mention together with the prose.
        # A repeat is recoverable; a lost mention is not.
        result = await self.run_turn([tool_round(decorated="at"), text_round(OTHER)])
        self.assertEqual(result["visible"], [BODY, OTHER])
        parts = [part for chain in result["chains"] for part in getattr(chain, "chain", ())]
        self.assertTrue(any(isinstance(part, At) for part in parts), "the mention must survive")

    async def test_a_tool_round_with_a_quote_is_never_withheld(self):
        result = await self.run_turn([tool_round(decorated="reply"), text_round(OTHER)])
        self.assertEqual(result["visible"], [BODY, OTHER])
        parts = [part for chain in result["chains"] for part in getattr(chain, "chain", ())]
        self.assertTrue(any(isinstance(part, Reply) for part in parts), "the quote must survive")

    # -- a malformed round never loses a response silently --------------------
    async def test_a_second_final_response_is_kept_and_logged(self):
        gate = {"hold_fragments": True, "draft": None, "emitted": False, "blank": None,
                "failed": False, "rounds_with_tools": 0, "deferred": 0, "released": 0,
                "restored": False, "closed": False, "runner": None, "event": None, "methods": {}}
        first = NS(role="assistant", completion_text=BODY, result_chain=None, is_chunk=False,
                   tools_call_name=[], tools_call_args=[], tools_call_ids=[])
        second = NS(role="assistant", completion_text=OTHER, result_chain=None, is_chunk=False,
                    tools_call_name=[], tools_call_args=[], tools_call_ids=[])

        async def source():
            yield first
            yield second

        with self.assertLogs("astrbot_host.core_reply_turn_gate", level="WARNING") as logs:
            shown = [response async for response in _turn_round(gate, source())]
        self.assertEqual([response.completion_text for response in shown], [OTHER])
        self.assertIn("extra_final", "\n".join(logs.output))

    # -- the withheld text never outlives the turn ---------------------------
    async def test_withheld_text_returns_before_the_assistant_history_is_written(self):
        result = await self.run_turn([tool_round(), text_round(OTHER)])
        self.assertEqual(result["messages"][1].role, "assistant")
        self.assertTrue(result["messages"][1].tool_calls)
        self.assertIn(BODY, [part.text for part in result["messages"][1].content], "history lost the prose")
        self.assertEqual(result["provider"].responses[0].completion_text, BODY)
        self.assertNotIn(RESPONSE_STREAM, result["runner"].__dict__)
        self.assertNotIn("step", result["runner"].__dict__)

    async def test_skip_discards_drafts_without_losing_tool_pairs_or_reasoning(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                first = {**tool_round(), "reasoning": "先判断是否参与"}
                result = await self.run_turn(
                    [first, tool_round(LATER, call_id="call-2"), text_round(OTHER)],
                    streaming=streaming, skip_after=2,
                )
                self.assertEqual(result["text"] + result["sent"], "")
                self.assertIsNone(result["gate"]["draft"])
                self.assertEqual(result["gate"]["released"], 0)
                messages = result["messages"]
                assistants = [message for message in messages if message.tool_calls]
                calls = [call.id for message in assistants for call in message.tool_calls]
                answers = [message.tool_call_id for message in messages if message.role == "tool"]
                self.assertEqual(calls, answers)
                self.assertEqual(calls, ["call-1", "call-2"])
                self.assertTrue(any(isinstance(part, ThinkPart) for part in assistants[0].content))
                self.assertFalse(any(getattr(part, "type", None) == "text"
                                     for message in assistants for part in message.content or ()))
                finals = [response for response in result["provider"].responses if not response.is_chunk]
                self.assertEqual(finals[0].completion_text, "")
                self.assertEqual(finals[1].completion_text, "")

    async def test_skip_keeps_existing_history_even_when_call_ids_repeat(self):
        old_tool = {"role": "assistant", "content": BODY,
                    "tool_calls": [{"id": "call-1", "type": "function",
                                    "function": {"name": TOOL, "arguments": "{}"}}]}
        contexts = [{"role": "user", "content": "上一轮"}, old_tool,
                    {"role": "tool", "tool_call_id": "call-1", "content": "旧结果"},
                    {"role": "assistant", "content": OTHER}]
        result = await self.run_turn([tool_round(), text_round(LATER)],
                                     skip_after=1, contexts=contexts)
        messages = result["messages"]
        self.assertEqual(messages[1].content, BODY)
        self.assertEqual(messages[2].content, "旧结果")
        self.assertEqual(messages[3].content, OTHER)
        self.assertEqual(messages[4].content[0].text, "在吗")
        self.assertFalse(messages[5].content)
        self.assertEqual(result["text"] + result["sent"], "")

    async def test_skip_clears_the_executed_batch_after_skills_like_replaces_call_ids(self):
        result = await self.run_turn(
            [tool_round(BODY, call_id="selected-1"), tool_round("", call_id="executed-1"),
             tool_round("", call_id="selected-skip"), tool_round("", call_id="executed-skip"),
             text_round(OTHER)],
            skip_after=2, tool_schema_mode="skills_like",
        )
        batches = result["runner"].req.tool_calls_result
        self.assertEqual([batch.tool_calls_info.tool_calls[0].id for batch in batches],
                         ["executed-1", "executed-skip"])
        self.assertFalse(batches[0].tool_calls_info.content)
        self.assertEqual(result["provider"].responses[0].completion_text, "")
        self.assertEqual(result["text"] + result["sent"], "")
        self.assertEqual([message.tool_call_id for message in result["messages"] if message.role == "tool"],
                         ["executed-1", "executed-skip"])

    async def test_skip_clears_only_the_withheld_batch_when_this_turn_reuses_a_call_id(self):
        result = await self.run_turn(
            [tool_round(BODY, decorated="at", call_id="reused"),
             tool_round(LATER, call_id="reused"), text_round(OTHER)],
            skip_after=2,
        )
        batches = result["runner"].req.tool_calls_result
        self.assertEqual([batch.tool_calls_info.tool_calls[0].id for batch in batches], ["reused", "reused"])
        self.assertEqual([part.text for part in batches[0].tool_calls_info.content], [BODY])
        self.assertFalse(batches[1].tool_calls_info.content)
        self.assertEqual(result["provider"].responses[0].completion_text, BODY)
        self.assertEqual(result["provider"].responses[1].completion_text, "")
        self.assertEqual(result["text"], BODY)

    async def test_skip_does_not_remove_prose_already_exposed_by_the_gate(self):
        for first, streaming, buffered in (
            (tool_round(decorated="at"), False, True),
            (tool_round(media=True), False, True),
            (tool_round(), True, False),
        ):
            with self.subTest(first=first, streaming=streaming):
                result = await self.run_turn(
                    [first, tool_round(LATER, call_id="call-2"), text_round(OTHER)],
                    skip_after=2, streaming=streaming, buffered=buffered,
                )
                self.assertIn(BODY, result["text"])
                if streaming:
                    self.assertEqual(result["provider"].responses[0].completion_text, BODY)
                else:
                    self.assertIn(BODY, [getattr(part, "text", "")
                                         for part in result["messages"][1].content or ()])
                self.assertNotIn(OTHER, result["text"])

    async def test_skip_discards_draft_at_step_limit_and_on_error(self):
        for ending in (silent_round(), failed_round(), raising_round()):
            with self.subTest(ending=ending):
                result = await self.run_turn([tool_round(), ending], skip_after=1, max_step=1)
                self.assertNotIn(BODY, result["text"] + result["sent"])
                self.assertIsNone(result["gate"]["draft"])
                self.assertEqual(result["gate"]["released"], 0)

    def test_skip_is_reported_even_without_a_gate(self):
        event = GateEvent()
        self.assertFalse(discard_skipped_core_reply(event))
        event.set_extra(CORE_REPLY_CONTEXT_EXTRA, {"group_participation": {"mode": "skip"}})
        self.assertTrue(discard_skipped_core_reply(event))

    async def test_the_gate_is_installed_once_per_turn(self):
        result = await self.run_turn([tool_round(), text_round(OTHER)])
        state = result["gate"]
        self.assertEqual(list(state["methods"]), [RESPONSE_STREAM, "step"])
        self.assertTrue(state["closed"])
        self.assertTrue(state["restored"])

    async def test_a_provider_error_closes_the_gate_so_the_next_turn_installs_a_new_one(self):
        # The error response ends the turn without on_agent_done. If the gate is
        # left open, the next turn on the same event reuses it, installs no new
        # wrapper, and the tool round's prose is delivered twice.
        event = GateEvent()
        first = await self.run_turn([failed_round()], event=event)
        self.assertTrue(first["gate"]["closed"], "an error exit must close the gate")
        second = await self.run_turn([tool_round(), text_round(OTHER)], event=event)
        self.assertIsNot(second["gate"], first["gate"])
        self.assertEqual(second["text"], OTHER, second["visible"])

    # -- fail open ------------------------------------------------------------
    async def test_without_the_dispatch_observer_nothing_is_withheld(self):
        # The observer owns the point where withheld text goes back, so the gate
        # must refuse to run rather than risk corrupting the assistant history.
        result = await self.run_turn([tool_round(), text_round(BODY)], observe=False)
        self.assertIsNone(result["gate"])
        self.assertIsNone(turn_gate(result["event"]))
        self.assertEqual(result["visible"], [BODY, BODY], "unchanged from today without the gate")

    async def test_without_an_active_runner_nothing_is_withheld(self):
        with patch("astrbot_host.core_reply_tool_dispatch.active_core_runner", return_value=None):
            result = await self.run_turn([tool_round(), text_round(BODY)])
        self.assertIsNone(result["gate"])
        self.assertEqual(result["visible"], [BODY, BODY])

    async def test_a_runner_without_a_response_stream_is_left_alone(self):
        runner = NS(run_context=NS(context=NS(event=None)), _handle_function_tools=lambda: None,
                    step=lambda: None, done=lambda: False, streaming=False)
        event = GateEvent()
        event.set_extra(CORE_REPLY_CONTEXT_EXTRA, {"prepared": {}})
        event.set_extra(CORE_TOOL_DISPATCH_EXTRA, {"runner": runner, "run_context": runner.run_context})
        with patch("astrbot_host.core_reply_turn_gate.discover_runner", return_value=runner):
            self.assertIsNone(install_core_reply_turn(event, runner.run_context))
        self.assertNotIn(RESPONSE_STREAM, runner.__dict__)
        self.assertIsNone(turn_gate(event))

    async def test_a_failing_decision_never_swallows_the_round(self):
        with patch("astrbot_host.core_reply_turn_gate._round_plan", side_effect=RuntimeError("boom")):
            result = await self.run_turn([tool_round(), text_round(BODY)])
        self.assertEqual(result["text"], BODY + BODY, "fail open: duplicate, never silent")
        self.assertTrue(result["gate"]["failed"])

    async def test_a_streaming_turn_without_a_buffered_downstream_keeps_its_fragments(self):
        # Known residual: when nothing collects the fragments the gate can only
        # withhold the final response. The identical answer is left to the send
        # side, and no text is ever lost.
        result = await self.run_turn([tool_round(), text_round(BODY)], streaming=True, buffered=False)
        self.assertEqual(result["text"], BODY + BODY)

    # -- the gate does not depend on the provider's tool round role ----------
    async def test_the_tool_round_role_and_call_ids_do_not_change_the_outcome(self):
        shapes = {
            "openai": tool_round(shape="openai"),
            "anthropic": tool_round(shape="anthropic"),
            "gemini": tool_round(shape="gemini"),
        }
        seen = {}
        for name, spec in shapes.items():
            result = await self.run_turn([spec, text_round(OTHER)])
            seen[name] = (result["provider"].responses[0].role, result["text"])
        self.assertEqual(seen, {"openai": ("tool", OTHER), "anthropic": ("assistant", OTHER),
                                "gemini": ("tool", OTHER)})


class BlankRollbackTests(unittest.TestCase):
    """A response whose text setter raises must keep its chain."""

    class FragileResponse:
        def __init__(self):
            self._text = BODY
            self._chain = MessageChain(chain=[Plain(BODY)])
            self.fail_text = False
            self.failures_left = 0

        @property
        def completion_text(self):
            return self._text

        @completion_text.setter
        def completion_text(self, value):
            if self.fail_text or self.failures_left:
                self.failures_left = max(0, self.failures_left - 1)
                raise RuntimeError("text setter exploded")
            self._text = value

        @property
        def result_chain(self):
            return self._chain

        @result_chain.setter
        def result_chain(self, value):
            self._chain = value

    def test_a_failing_blank_rolls_back_and_keeps_the_chain(self):
        response = self.FragileResponse()
        chain = response.result_chain
        gate = {"blank": None}
        # The blanking setter raises once; the rollback must put the chain back.
        response.failures_left = 1
        self.assertFalse(_blank(gate, response))
        self.assertIs(response.result_chain, chain, "the chain must not be lost")
        self.assertEqual(response.completion_text, BODY)
        self.assertIsNone(gate["blank"])
        # The failed attempt left nothing behind, so the next round can still be
        # withheld and restored normally.
        self.assertTrue(_blank(gate, response))
        self.assertIsNone(response.result_chain)
        self.assertEqual(response.completion_text, "")
        _restore_blank(gate)
        self.assertIs(response.result_chain, chain)
        self.assertEqual(response.completion_text, BODY)


class DownstreamBufferReasonTests(unittest.TestCase):
    """An unjudgeable downstream is reported, not silently called unbuffered."""

    def _reason(self, error):
        event = GateEvent()
        with patch("astrbot_host.core_reply_segmentation.uses_managed_group_stream",
                   side_effect=error):
            with self.assertLogs("astrbot_host.core_reply_turn_gate", level="WARNING") as logs:
                buffered = _downstream_buffers(event)
        self.assertFalse(buffered, "unknown must not withhold fragments")
        return "\n".join(logs.output)

    def test_a_missing_dependency_is_reported(self):
        self.assertIn("dependency_missing", self._reason(ImportError("no module")))

    def test_a_changed_shape_is_reported(self):
        self.assertIn("shape_changed", self._reason(AttributeError("no attribute")))

    def test_a_broken_detector_is_reported(self):
        self.assertIn("detector_error", self._reason(RuntimeError("boom")))


if __name__ == "__main__":
    unittest.main()
