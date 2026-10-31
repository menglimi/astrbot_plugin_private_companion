"""Answer a tool call this group turn withholds instead of "tool not found".

An explicit ``@`` already selected this turn for a reply, so the participation
catalogue stays off (that is what prevents the duplicate send the group path was
fixed for).  The model still sees its own earlier ``choose`` calls in the
conversation history and repeats the ritual, and the host answered that call
with ``Tool ... not found``, which wasted a model round and wrote that error text
back into the history.  These tests keep the catalogue unchanged and require the
dispatch layer to answer the withheld call itself.
"""

import json
from types import SimpleNamespace as NS
import unittest

from tests import test_astrbot_staging_host as _host_import
from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
from astrbot.core.pipeline.process_stage.follow_up import _ACTIVE_AGENT_RUNNERS
from astrbot.core.provider.entities import LLMResponse, ProviderRequest

from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA, prepare_core_reply_event
from astrbot_host.core_reply_tool_dispatch import observe_core_tool_dispatch_begin, restore_core_tool_dispatch
from astrbot_host.core_reply_tools import inject_core_reply_tools, native_tool_name
from companion_v2.execution.models import require
from tests.group_host_support import GroupFixture


ADDRESSED_REPLY = "……谁跟你说人生无常了。"


class CoreReplyWithheldToolTests(GroupFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = await self.start_host()

    async def addressed_turn(self, text="人生无常"):
        event = self.group_event(identifier="native-group-addressed", text=text)
        event.message_obj.message.insert(0, NS(type="At", qq="100"))
        admission = require(await self.controller.group_reply_candidate(event))
        self.assertTrue(admission["admitted"], admission)
        self.assertTrue(admission["mentioned"], admission)
        prepared = require(await self.controller.prepare_core_reply(event, group_admission=admission))
        prepared["group_admission"] = dict(admission)
        prepare_core_reply_event(event, prepared,
            authorize=lambda value: self.controller.authorize_core_reply(event, value))
        request = NS(func_tool=None, system_prompt="原人格")
        inject_core_reply_tools(event, request, application=self.app)
        return event, request

    async def run_step(self, event, request, calls):
        """Run the real agent runner over one withheld tool batch and its reply."""
        steps = [
            LLMResponse(role="assistant", completion_text="",
                tools_call_name=[native_tool_name(action) for action, _, _ in calls],
                tools_call_args=[arguments for _, arguments, _ in calls],
                tools_call_ids=[identifier for _, _, identifier in calls]),
            LLMResponse(role="assistant", completion_text=ADDRESSED_REPLY),
        ]
        contexts = []

        async def respond(**kwargs):
            contexts.append(kwargs["contexts"])
            return steps.pop(0)

        provider = NS(provider_config={"id": "fake-addressed", "modalities": ["text", "tool_use"]},
                      text_chat=respond, text_chat_stream=respond)
        req = ProviderRequest(prompt="人生无常", func_tool=request.func_tool)
        run = NS(context=NS(event=event), messages=[], tool_call_timeout=5)

        class Hooks(BaseAgentRunHooks):
            async def on_agent_begin(self, run_context):
                observe_core_tool_dispatch_begin(event, run_context)

            async def on_agent_done(self, run_context, response):
                restore_core_tool_dispatch(event, run_context)

        runner = ToolLoopAgentRunner()
        await runner.reset(provider, req, run, FunctionToolExecutor(), Hooks(), streaming=False)
        _ACTIVE_AGENT_RUNNERS[event.unified_msg_origin] = runner
        self.addCleanup(_ACTIVE_AGENT_RUNNERS.pop, event.unified_msg_origin, None)
        shown = []
        with self.assertLogs("astrbot", level="DEBUG") as captured:
            async for output in runner.step_until_done(3):
                if output.type == "llm_result":
                    shown.append(output.data["chain"].get_plain_text())
        return req, run, shown, "\n".join(captured.output), contexts

    def assert_paired_tools(self, run, identifiers):
        """Every withheld call must have exactly one tool answer, in order."""
        assistant = [message for message in run.messages
                     if getattr(message, "role", None) == "assistant" and message.tool_calls]
        self.assertEqual([[call.id for call in message.tool_calls] for message in assistant],
                         [identifiers], "the model tool call must stay paired with its answers")
        answers = [message for message in run.messages if getattr(message, "role", None) == "tool"]
        self.assertEqual([message.tool_call_id for message in answers], identifiers)
        for answer in answers:
            self.assertNotIn("not found", answer.content)
        return [json.loads(answer.content) for answer in answers]

    async def test_addressed_turn_answers_the_withheld_choice_without_a_dead_round(self):
        event, request = await self.addressed_turn()
        # The design stands: an explicit @ turn does not mount the choice tool.
        self.assertIsNone(request.func_tool.get_tool(native_tool_name("group.participation.choose")))

        req, run, shown, log, contexts = await self.run_step(event, request,
            [("group.participation.choose", {"mode": "reply", "reason": "被点名"}, "call-addressed")])

        self.assertEqual(shown, [ADDRESSED_REPLY], shown)
        self.assertNotIn("未找到指定的工具", log)
        self.assertEqual(len(contexts), 2, "the turn must spend one tool step and one reply step")
        answer, = self.assert_paired_tools(run, ["call-addressed"])
        self.assertEqual((answer["state"], answer["effect"]), ("failed", "none"))
        self.assertEqual(answer["code"], "core_tool_not_offered")
        self.assertEqual(answer["action"], "group.participation.choose")
        self.assertEqual(answer["participation"], {"mode": "reply", "basis": "explicit_mention"})
        self.assertIn("直接写出要说的话", answer["instruction"])
        # The answer carries no participation side effect of its own.
        self.assertIsNone(event.get_extra(CORE_REPLY_CONTEXT_EXTRA).get("group_participation"))
        # The withheld tool existed only for that batch.
        self.assertIsNone(req.func_tool.get_tool(native_tool_name("group.participation.choose")))

    async def test_whole_batch_of_withheld_calls_is_answered_together(self):
        # AstrBot keeps only one result block per batch, so a partial answer would
        # leave the other call unpaired and break the next request outright.
        event, request = await self.addressed_turn()
        _, run, shown, log, _ = await self.run_step(event, request, [
            ("group.participation.choose", {"mode": "reply", "reason": "被点名"}, "call-choice"),
            ("group.behavior.learn", {"label": "被夸琴技时嘴硬", "situation": "群友夸琴技",
                "action": "傲娇地承认", "boundaries": "不主动炫耀", "cues": [], "evidence_handles": ["h1"]},
                "call-learn"),
        ])

        self.assertEqual(shown, [ADDRESSED_REPLY], shown)
        self.assertNotIn("未找到指定的工具", log)
        choice, learn = self.assert_paired_tools(run, ["call-choice", "call-learn"])
        self.assertEqual([answer["code"] for answer in (choice, learn)], ["core_tool_not_offered"] * 2)
        self.assertEqual(learn["action"], "group.behavior.learn")
        # Neither the participation decision nor a business tool ran.
        self.assertIsNone(event.get_extra(CORE_REPLY_CONTEXT_EXTRA).get("group_participation"))
        self.assert_no_reply_work()

    def assert_no_reply_work(self):
        self.assertEqual(self.app.runtime.read_one("SELECT COUNT(*) n FROM conversation_batches", ())["n"], 0)
        self.assertEqual(self.app.runtime.read_one("SELECT COUNT(*) n FROM model_rounds", ())["n"], 0)

    async def test_unaddressed_turn_still_mounts_the_real_choice(self):
        event, request = await self.addressed_turn(text="大家周末去哪玩")
        self.assertIsNone(request.func_tool.get_tool(native_tool_name("group.participation.choose")))
        # The same admitted group turn keeps the ordinary candidate surface, so
        # the fix cannot be "always hide the tool".
        candidate = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["prepared"]
        candidate["group_admission"] = {"admitted": True, "mentioned": False,
                                       "reason": "question", "reasons": ["question"]}
        request.func_tool, request.system_prompt = None, "原人格"
        inject_core_reply_tools(event, request, application=self.app)
        self.assertIsNotNone(request.func_tool.get_tool(native_tool_name("group.participation.choose")))
