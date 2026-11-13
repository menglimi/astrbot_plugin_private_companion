"""Bind native tool dispatch independently of optional usage statistics."""

import asyncio
import logging
import time

from .core_reply_turn_gate import (
    begin_core_reply_tool_batch, discard_skipped_core_reply, restore_core_turn_text,
)


CORE_TOOL_DISPATCH_EXTRA = "_companion_v2_core_tool_dispatch"
logger = logging.getLogger(__name__)


def active_core_runner(event, run_context):
    try:
        from astrbot.core.pipeline.process_stage.follow_up import _ACTIVE_AGENT_RUNNERS
        runner = _ACTIVE_AGENT_RUNNERS.get(event.unified_msg_origin)
        if (runner is not None and getattr(runner, "run_context", None) is run_context
                and getattr(getattr(run_context, "context", None), "event", None) is event):
            return runner
    except (ImportError, AttributeError):
        pass
    return None


def restore_core_tool_dispatch(event, run_context=None):
    state = event.get_extra(CORE_TOOL_DISPATCH_EXTRA)
    if not isinstance(state, dict) or (run_context is not None and state["run_context"] is not run_context):
        return
    restore_core_turn_text(event)
    runner = state["runner"]
    if "budget_max" in state:
        runner._step_budget_max = state["budget_max"]
    for name, (had_local, old_local, wrapper) in state["methods"].items():
        try:
            if getattr(runner, name, None) is wrapper:
                if had_local:
                    setattr(runner, name, old_local)
                else:
                    delattr(runner, name)
        except Exception:
            pass
    state["restored"] = True


def observe_core_tool_dispatch_begin(event, run_context):
    """Capture authentic call IDs at the SDK dispatch point, before execution."""
    from .core_reply import CORE_REPLY_CONTEXT_EXTRA
    if not isinstance(event.get_extra(CORE_REPLY_CONTEXT_EXTRA), dict):
        return None
    runner = active_core_runner(event, run_context)
    if runner is None:
        return None
    state = event.get_extra(CORE_TOOL_DISPATCH_EXTRA)
    if isinstance(state, dict) and state["runner"] is runner and not state["restored"]:
        return state
    original_tools, original_step = getattr(runner, "_handle_function_tools", None), getattr(runner, "step", None)
    if not callable(original_tools) or not callable(original_step):
        return None
    state = {"runner": runner, "run_context": run_context, "methods": {}, "finished": [],
             "restored": False, "batch_active": False, "silent_pending": False}
    original_consume = getattr(runner, "_consume_follow_up_notice", None)

    def consume_follow_up():
        # A later tool in this batch can still choose to listen. Do not mark new
        # input consumed until the complete batch has made its participation choice.
        if state["batch_active"] or state["silent_pending"]:
            return ""
        return original_consume()

    def cleanup_dispatch():
        # Cancellation or a failed done hook can bypass the normal close sequence.
        # Release deferred input and unwind the instance wrappers from outside in.
        from .core_reply_turn_gate import _finish_gate, turn_gate
        from .core_reply_tool_guard import close_core_reply_tool_guard
        resolve = getattr(runner, "_resolve_unconsumed_follow_ups", None)
        if callable(resolve):
            resolve()
        _finish_gate(turn_gate(event))
        close_core_reply_tool_guard(event, run_context)
        restore_core_tool_dispatch(event, run_context)

    async def bound_tools(*args, **kwargs):
        # Text the round gate withheld belongs on the response again before the
        # tool runs: the SDK writes the assistant history from it right below.
        restore_core_turn_text(event)
        # A binding error leaves the tool unavailable; it must never substitute
        # a model-supplied ID or execute an operation under a guessed identity.
        try:
            if active_core_runner(event, run_context) is runner:
                from .core_reply_tools import observe_core_tool_calls
                response = args[1] if len(args) > 1 else kwargs.get("llm_response")
                begin_core_reply_tool_batch(event, response)
                observe_core_tool_calls(event, response, run_context)
        except Exception as error:
            logger.warning("[CompanionV2] 核心工具调用绑定暂不可用 error_type=%s", type(error).__name__)
        blocks = None
        state["batch_active"] = True
        try:
            async for item in original_tools(*args, **kwargs):
                if getattr(item, "kind", None) == "tool_call_result_blocks":
                    blocks = item.tool_call_result_blocks
                try:
                    if getattr(item, "kind", None) == "cached_image" and item.cached_image is not None:
                        from .core_reply_reactions import observe_core_cached_image
                        observe_core_cached_image(event, item.cached_image)
                except Exception:
                    pass
                yield item
            if discard_skipped_core_reply(event):
                state["silent_pending"] = True
                state["budget_max"] = getattr(runner, "_step_budget_max", None)
                state["tool_set"] = runner.req.func_tool
                # The native step still has to append this batch to both histories.
                # Its budget notice asks for another answer, which this turn declined.
                runner._step_budget_max = None
            elif blocks and callable(original_consume):
                notice = original_consume()
                if notice:
                    from astrbot.core.agent.message import TextPart
                    last = blocks[-1]
                    if isinstance(last.content, str):
                        last.content += notice
                    else:
                        last.content = [*(last.content or ()), TextPart(text=notice)]
        except (asyncio.CancelledError, GeneratorExit):
            cleanup_dispatch()
            raise
        except Exception:
            # Native user-stop exceptions still run the host's abort hook, which
            # owns ordinary draft delivery. Only release our deferred input here.
            resolve = getattr(runner, "_resolve_unconsumed_follow_ups", None)
            if callable(resolve):
                resolve()
            raise
        finally:
            state["batch_active"] = False

    async def complete_skipped_turn():
        from astrbot.core.agent.runners.base import AgentState
        from astrbot.core.provider.entities import LLMResponse

        # on_agent_begin runs inside the first step, so its wrapper only takes
        # effect on the next step. At the budget boundary the host may have added
        # its forced-answer instruction before reaching that wrapper. The native
        # batch is already recorded; only that exact trailing host notice is removed.
        messages = runner.run_context.messages
        if (state["budget_max"] is not None
                and runner._step_budget_used >= state["budget_max"]
                and runner.req.func_tool is None and state["tool_set"] is not None):
            if (messages and messages[-1].role == "user"
                    and messages[-1].content == runner.MAX_STEPS_REACHED_PROMPT):
                messages.pop()
            runner.req.func_tool = state["tool_set"]
        discard_skipped_core_reply(event)
        state["silent_pending"] = False
        state["silently_completed"] = True
        runner.final_llm_resp = LLMResponse(role="assistant", completion_text="")
        runner._transition_state(AgentState.DONE)
        runner.stats.end_time = time.time()
        try:
            await runner.agent_hooks.on_agent_done(run_context, runner.final_llm_resp)
        except Exception:
            logger.exception("[CompanionV2] 群聊旁听结束回调失败")
        finally:
            cleanup_dispatch()
        logger.info("[CompanionV2] 群聊旁听已静默结束原生 Agent")

    async def observed_step(*args, **kwargs):
        failed = False
        try:
            if not state["silent_pending"]:
                async for item in original_step(*args, **kwargs):
                    yield item
            if state["silent_pending"]:
                await complete_skipped_turn()
        except BaseException:
            failed = True
            cleanup_dispatch()
            raise
        finally:
            try:
                done = runner.done()
            except Exception:
                done = False
            if failed or done:
                for callback in tuple(state["finished"]):
                    try:
                        callback(runner, failed)
                    except Exception:
                        pass
                restore_core_tool_dispatch(event, run_context)

    event.set_extra(CORE_TOOL_DISPATCH_EXTRA, state)
    try:
        wrappers = [("_handle_function_tools", bound_tools), ("step", observed_step)]
        if callable(original_consume):
            wrappers.append(("_consume_follow_up_notice", consume_follow_up))
        for name, wrapper in wrappers:
            local = getattr(runner, "__dict__", {})
            state["methods"][name] = (name in local, local.get(name), wrapper)
            setattr(runner, name, wrapper)
    except Exception:
        restore_core_tool_dispatch(event, run_context)
        return None
    return state
