"""Offline AstrBot stages with a real tool runner and temporary SQL history."""

from copy import deepcopy
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
from astrbot.core.config import DEFAULT_CONFIG
from astrbot.core.conversation_mgr import ConversationManager
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.message.message_event_result import MessageEventResult, ResultContentType
from astrbot.core.pipeline.process_stage.follow_up import _ACTIVE_AGENT_RUNNERS
from astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal import InternalAgentSubStage
from astrbot.core.pipeline.process_stage.stage import ProcessStage
from astrbot.core.pipeline.respond.stage import RespondStage
from astrbot.core.pipeline.result_decorate import stage as decorate_module
from astrbot.core.provider.entities import LLMResponse, ProviderRequest


async def install_history(case):
    database = SQLiteDatabase(str(case.root / "astrbot-history.db"))
    await database.initialize()
    case.addAsyncCleanup(database.engine.dispose)
    manager = ConversationManager(database)
    case.native.conversation_manager = manager
    await manager.new_conversation("tg-main:FriendMessage:5123456789012", persona_id="original")
    return manager


class TextAgent:
    """Keep host/model IO synthetic; use the SDK runner and history serializer."""
    def __init__(self, plugin, manager, text, *, tool=False, host_tools=None):
        self.plugin, self.manager, self.text, self.tool = plugin, manager, text, tool
        self.host_tools = host_tools
        self.requests, self.calls, self.tool_results = [], [], []

    async def process(self, event):
        manager, plugin = self.manager, self.plugin
        cid = await manager.get_curr_conversation_id(event.unified_msg_origin)
        conversation = await manager.get_conversation(event.unified_msg_origin, cid)
        req = ProviderRequest(prompt=event.message_str, system_prompt="Synthetic persona", conversation=conversation,
                              contexts=json.loads(conversation.history), func_tool=self.host_tools)
        await plugin.prepare_companion_core_request(event, req)
        self.requests.append(req)
        if event.is_stopped():
            return

        async def text_chat(**kwargs):
            self.calls.append(kwargs)
            if self.tool and len(self.calls) == 1:
                return LLMResponse(role="assistant", completion_text="", tools_call_name=["companion_v2_turn_evidence"],
                                   tools_call_args=[{}], tools_call_ids=["synthetic-evidence-call"])
            return LLMResponse(role="assistant", completion_text=self.text)

        class Hooks(BaseAgentRunHooks):
            async def on_agent_begin(self, context):
                await plugin.observe_companion_core_agent_begin(event, context)

            async def on_agent_done(self, context, response):
                await plugin.observe_companion_model_session(event, response)
                await plugin.observe_companion_core_agent_done(event, context, response)

        provider = NS(provider_config={"id": "synthetic-text", "modalities": ["text"]},
                      get_model=lambda: "synthetic", text_chat=text_chat)
        wrapper = NS(context=NS(event=event), messages=[], tool_call_timeout=5)
        runner = ToolLoopAgentRunner()
        await runner.reset(provider, req, wrapper, FunctionToolExecutor(), Hooks(), tool_schema_mode="full")
        _ACTIVE_AGENT_RUNNERS[event.unified_msg_origin] = runner
        try:
            for _ in range(3):
                _ = [item async for item in runner.step()]
                if runner.done():
                    break
            assert runner.done(), "synthetic model did not finish"
            self.tool_results = [json.loads(item.content) for item in wrapper.messages if item.role == "tool"]
            response = runner.get_final_llm_resp()
            event.set_result(MessageEventResult().message(response.completion_text).set_result_content_type(
                ResultContentType.LLM_RESULT))
            yield
            history_stage = InternalAgentSubStage()
            history_stage.conv_manager = manager
            await history_stage._save_to_history(event, req, response, wrapper.messages, runner.stats)
        finally:
            _ACTIVE_AGENT_RUNNERS.pop(event.unified_msg_origin, None)


async def run_pipeline(case, event, *, text="我在这里 😀 https://example.invalid", tool=False, host_tools=None):
    config = deepcopy(DEFAULT_CONFIG)
    config["t2i"] = False
    config["provider_tts_settings"]["enable"] = False
    config["platform_settings"].update(reply_with_mention=False, reply_with_quote=False, reply_prefix="")
    config["platform_settings"]["segmented_reply"]["enable"] = False
    case.native.get_using_tts_provider_async = AsyncMock(return_value=None)
    ctx = NS(astrbot_config=config, plugin_manager=NS(context=case.native))
    process = ProcessStage()
    process.ctx = ctx
    from astrbot.core.pipeline.process_stage.method.star_request import StarRequestSubStage
    process.star_request_sub_stage = StarRequestSubStage()
    agent = TextAgent(case.plugin, case.native.conversation_manager, text, tool=tool, host_tools=host_tools)
    process.agent_sub_stage = agent
    decorate = decorate_module.ResultDecorateStage()
    await decorate.initialize(ctx)
    respond = RespondStage()
    await respond.initialize(ctx)
    from astrbot.core.star.star import star_map
    from astrbot.core.star.star_handler import EventType, StarHandlerMetadata, star_handlers_registry
    module = "telegram_reply_test"
    event.plugins_name = [module]
    star_map[module] = NS(name=module, display_name=module, activated=True, reserved=False)
    handlers = []
    for kind, callback in ((EventType.OnDecoratingResultEvent, case.plugin.guard_companion_core_delivery),
                           (EventType.OnAfterMessageSentEvent, case.plugin.observe_companion_core_reply_sent)):
        handler = StarHandlerMetadata(event_type=kind, handler_full_name=module + "." + callback.__name__,
                                      handler_name=callback.__name__, handler_module_path=module,
                                      handler=callback, event_filters=[], extras_configs={})
        star_handlers_registry.append(handler)
        handlers.append(handler)
    try:
        async for _ in process.process(event):
            async for _ in decorate.process(event):
                pass
            await respond.process(event)
    finally:
        for handler in handlers:
            star_handlers_registry.remove(handler)
        star_map.pop(module, None)
    await case.flush()
    return agent
