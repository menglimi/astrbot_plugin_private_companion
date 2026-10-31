"""Run native media search continuations without sending the same reply twice."""

from copy import deepcopy
from types import SimpleNamespace as NS
import unittest

from tests import test_astrbot_staging_host as _host_import
from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.pipeline.process_stage.follow_up import _ACTIVE_AGENT_RUNNERS
from astrbot.core.provider.entities import LLMResponse, ProviderRequest

from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA
from astrbot_host.core_reply_tool_dispatch import observe_core_tool_dispatch_begin, restore_core_tool_dispatch
from astrbot_host.core_reply_voice_authoring import prepare_core_voice_authoring, restore_core_voice_authoring
from companion_v2.conversation.voice_reply import VoiceReplyPolicy
from tests.test_core_reply_voice import FakeEvent


class SearchTool(FunctionTool):
    def __init__(self, name):
        super().__init__(name=name, description="Test search", parameters={"type": "object", "properties": {}})
        self.calls = 0

    async def call(self, context, **kwargs):
        self.calls += 1
        return '{"items":[],"retrieval":{"eligible":0}}'


class MediaToolPresentationTests(unittest.IsolatedAsyncioTestCase):
    async def run_search(self, name, *, voice=False, streaming=False):
        event = FakeEvent()
        event.unified_msg_origin = "test:media-tool-presentation"
        policy = VoiceReplyPolicy(generation_mode="model_tag", language="ja", foreign_text_mode="translation")
        event.set_extra(CORE_REPLY_CONTEXT_EXTRA, {"prepared": {"event_ref": "test-media",
            "voice_reply_policy": policy if voice else None}})
        text = "<pc_tts>おめでとう。</pc_tts>生日快乐。" if voice else "生日快乐。"
        draft = LLMResponse(role="assistant", completion_text=text,
            result_chain=MessageChain(chain=[Plain(text)]), tools_call_name=[name],
            tools_call_args=[{}], tools_call_ids=["real-media-call"])
        outputs = [draft, LLMResponse(role="assistant", completion_text=text)]
        requests = []

        async def respond(**kwargs):
            requests.append(deepcopy(kwargs["contexts"]))
            return outputs.pop(0)

        async def stream(**kwargs):
            requests.append(deepcopy(kwargs["contexts"]))
            while outputs:
                yield outputs.pop(0)

        tool = SearchTool(name)
        provider = NS(provider_config={"id": "fake-media", "modalities": ["text"]},
                      text_chat=respond, text_chat_stream=stream)
        req = ProviderRequest(prompt="笨蛋", func_tool=ToolSet(tools=[tool]))
        run = NS(context=NS(event=event), messages=[], tool_call_timeout=5)

        class Hooks(BaseAgentRunHooks):
            async def on_agent_begin(self, run_context):
                prepare_core_voice_authoring(event, run_context)
                observe_core_tool_dispatch_begin(event, run_context)

            async def on_agent_done(self, run_context, response):
                restore_core_voice_authoring(event)
                restore_core_tool_dispatch(event, run_context)

        runner = ToolLoopAgentRunner()
        await runner.reset(provider, req, run, FunctionToolExecutor(), Hooks(), streaming=streaming)
        _ACTIVE_AGENT_RUNNERS[event.unified_msg_origin] = runner
        self.addCleanup(_ACTIVE_AGENT_RUNNERS.pop, event.unified_msg_origin, None)
        shown = []
        async for output in runner.step_until_done(3):
            if output.type == "llm_result":
                shown.append(output.data["chain"].get_plain_text())
        self.assertEqual(tool.calls, 1)
        self.assertEqual(len(requests), 2)
        self.assertEqual(draft.completion_text, text)
        self.assertEqual(draft.result_chain.get_plain_text(), text)
        self.assertTrue(any(message.role == "assistant" and message.tool_calls for message in run.messages))
        self.assertNotIn("_iter_llm_responses_with_fallback", runner.__dict__)
        self.assertNotIn("_handle_function_tools", runner.__dict__)
        return event, shown

    async def test_reaction_search_defers_complete_text_until_final_reply(self):
        _, shown = await self.run_search("companion_v2_reaction_search")
        self.assertEqual(shown, ["生日快乐。"])

    async def test_reaction_search_defers_streamed_draft_too(self):
        _, shown = await self.run_search("companion_v2_reaction_search", streaming=True)
        self.assertEqual(shown, ["生日快乐。"])

    async def test_reaction_search_and_voice_keep_one_final_speech_span(self):
        event, shown = await self.run_search("companion_v2_reaction_search", voice=True)
        self.assertEqual(shown, ["生日快乐。"])
        authored = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["_voice_authored"]
        self.assertEqual(len(authored["spans"]), 1)

    async def test_group_participation_choice_defers_the_tool_step_prose_too(self):
        # A group turn that calls choose(reply) and also writes prose must not
        # send that prose: AstrBot would deliver it and then deliver the reply
        # generated after the tool, which is the duplicate send the group path
        # used to avoid by removing the tool from the turn's tool set.
        _, shown = await self.run_search("companion_v2_group_participation_choose")
        self.assertEqual(shown, ["生日快乐。"])

    async def test_other_tools_keep_native_progress_prose(self):
        _, shown = await self.run_search("other_search")
        self.assertEqual(shown, ["生日快乐。", "生日快乐。"])
