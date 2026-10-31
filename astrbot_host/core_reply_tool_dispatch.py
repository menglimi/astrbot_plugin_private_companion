"""Bind native tool dispatch independently of optional usage statistics."""

import asyncio
import logging


CORE_TOOL_DISPATCH_EXTRA = "_companion_v2_core_tool_dispatch"
logger = logging.getLogger(__name__)


def _restore_tool_reply(state):
    pending = state.pop("deferred_reply", None)
    if pending is not None:
        response, text, chain = pending
        response.completion_text, response.result_chain = text, chain


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
    runner = state["runner"]
    _restore_tool_reply(state)
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
    state = {"runner": runner, "run_context": run_context, "methods": {}, "finished": [], "restored": False}

    original_responses = getattr(runner, "_iter_llm_responses_with_fallback", None)

    async def responses(*args, **kwargs):
        async for response in original_responses(*args, **kwargs):
            names = getattr(response, "tools_call_name", ())
            if (not getattr(response, "is_chunk", False)
                    and getattr(response, "role", None) == "assistant" and names
                    and set(names) == {"companion_v2_reaction_search"}):
                # AstrBot emits tool-step prose before executing the search.
                # Defer that draft until the final media reply; restore the exact
                # response before dispatch so tool IDs, arguments and history stay intact.
                # Streaming providers reach this same final response; the old
                # ``not runner.streaming`` guard let a group/private streaming
                # step send the draft and then send the post-tool reply again.
                state["deferred_reply"] = (response, response.completion_text, response.result_chain)
                # The SDK text setter mutates result_chain when one is attached.
                response.result_chain = None
                response.completion_text = ""
            yield response

    async def bound_tools(*args, **kwargs):
        _restore_tool_reply(state)
        # A binding error leaves the tool unavailable; it must never substitute
        # a model-supplied ID or execute an operation under a guessed identity.
        restore_tools = lambda: None
        try:
            if active_core_runner(event, run_context) is runner:
                from .core_reply_tools import bind_withheld_core_tools, observe_core_tool_calls
                response = args[1] if len(args) > 1 else kwargs.get("llm_response")
                observe_core_tool_calls(event, response, run_context)
                # A call this turn deliberately withholds (an explicit @ mounts no
                # participation tool) must be answered, not reported as missing.
                restore_tools = bind_withheld_core_tools(
                    event, args[0] if args else kwargs.get("req"), response)
        except Exception as error:
            logger.warning("[CompanionV2] 核心工具调用绑定暂不可用 error_type=%s", type(error).__name__)
        try:
            async for item in original_tools(*args, **kwargs):
                try:
                    if getattr(item, "kind", None) == "cached_image" and item.cached_image is not None:
                        from .core_reply_reactions import observe_core_cached_image
                        observe_core_cached_image(event, item.cached_image)
                except Exception:
                    pass
                yield item
        except (asyncio.CancelledError, GeneratorExit):
            restore_core_tool_dispatch(event, run_context)
            raise
        finally:
            # The batch is over; the next provider call must see the catalogue again.
            restore_tools()

    async def observed_step(*args, **kwargs):
        failed = False
        try:
            async for item in original_step(*args, **kwargs):
                yield item
        except BaseException:
            failed = True
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
        methods = [("_handle_function_tools", bound_tools), ("step", observed_step)]
        if callable(original_responses):
            methods.append(("_iter_llm_responses_with_fallback", responses))
        for name, wrapper in methods:
            local = getattr(runner, "__dict__", {})
            state["methods"][name] = (name in local, local.get(name), wrapper)
            setattr(runner, name, wrapper)
    except Exception:
        restore_core_tool_dispatch(event, run_context)
        return None
    return state
