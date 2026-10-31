"""Expose event-scoped V2 owner tools to AstrBot's native tool runner."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import asyncio
import json
import logging
import re
from typing import Any

from jsonschema.exceptions import ValidationError
from mcp.types import CallToolResult, ImageContent, TextContent

from astrbot.core.agent.tool import FunctionTool, ToolSet

from companion_v2.agenda.models import AgendaTarget, MemoCursor
from companion_v2.conversation.agenda_tools import AgendaRoleSession
from companion_v2.conversation.tool_loop import READ_ACTIONS
from companion_v2.conversation.native_tool_media import native_image_data
from companion_v2.execution.models import TaskContext, TaskError, digest, encode, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import OperationRef, ScopeKind, VersionRef
from companion_v2.model_gateway.models import ToolSpec

from .core_reply import CORE_REPLY_CONTEXT_EXTRA
from .core_reply_tool_execution import executor_for, supports_native_external


logger = logging.getLogger(__name__)


MEMORY_READ_ACTIONS = frozenset({
    "memory.query", "memory.legacy.query", "memory.sources", "memory.operation.get", "memory.query.status",
})
GROUP_READ_ACTIONS = frozenset({"group.memory.query", "group.memory.legacy.query", "group.memory.operation"})

# Stable window-level manifests.  The order is part of the cached tool prefix:
# append new actions at the end and bump TOOL_MANIFEST_VERSION when the schema
# changes, never splice a tool in because one message looks special.
TOOL_MANIFEST_VERSION = "2026-10-29.1"
LEAN_GROUP_TOOL_ORDER = ("group.participation.choose", "group.memory.query",
                         "group.memory.remember", "acquaintance.recall",
                         "reaction.search", "slang.search")
LEAN_GROUP_ACTIONS = frozenset(LEAN_GROUP_TOOL_ORDER)
STANDARD_GROUP_ACTIONS = LEAN_GROUP_ACTIONS | frozenset((
    "group.behavior.learn", "group.behavior.feedback", "slang.learn", "turn.evidence"))
LEAN_GROUP_INSTRUCTION = ("【群聊精简工具】本轮工具集固定且很小。决定接话时直接写出回复，不要先调用 "
    "group.participation.choose；只有决定旁听或等待时才调用 choose(mode=skip)。"
    "不要因为缺少某个工具就声称无法回应，先用自然语言把当前能说的说完。")


def group_tool_manifest(offered, profile):
    """Return the fixed group manifest for one window-level profile.

    ``full`` keeps the ordinary catalogue.  ``lean`` and ``standard`` filter to
    the same ordered allowlist on every turn, so the provider's cached tool
    prefix does not change when a message happens to carry an image or mention.
    """
    if profile not in ("lean", "standard"):
        return tuple(offered)
    allowed = LEAN_GROUP_ACTIONS if profile == "lean" else STANDARD_GROUP_ACTIONS
    order = {action: index for index, action in enumerate(LEAN_GROUP_TOOL_ORDER)}
    filtered, seen = [], set()
    for item in offered:
        action = item.spec.action
        if action not in allowed or action in seen:
            continue
        seen.add(action)
        filtered.append(item)
    filtered.sort(key=lambda item: order.get(item.spec.action, len(order)))
    return tuple(filtered)

TOOL_INSTRUCTION = """陪伴工具作用于当前会话与角色，日常聊天可直接回应，需要行动时再调用。工具材料供参考，按实际结果说明：查不到不证明从未发生；unknown 表示结果暂未确认，既不证明成功，也不证明失败或保存丢失。自然说明尚未确认，不编造已经拍好、生成后丢失等过程，也不更换调用编号或引导用户重做同一未知操作。只能用该能力提供的查询方式核对，其他工具查不到不能改变原操作状态。提醒登记不代表平台发送。
证据句柄用于工具，当前消息需作为证据时先调用 turn.evidence；记忆提议引用本轮提供的真实句柄。来源通常不打断聊天，被追问时再自然说明。
对方接受刚提出的具体游戏、共处或通话邀请时，调用当前开放的对应工具创建或接续活动，以成功结果为准并给出完整房间链接，不用口头报坐标代替真实对局。不编链接，不在入口未就绪或工具未开放时声称已建房、开局或拨通。
按完整本轮原话分清双方先手与颜色，已被更正的旧选择不沿用。human_side 是用户方：五子棋中“你执黑”“你先下”或点名角色“黑先”表示角色黑、用户白，human_side=white；“我执黑”“我先下”才是 human_side=black。
access_scope=same_device 的链接仅供运行 AstrBot 的电脑使用；public_access=unavailable 表示公网入口未就绪，不能把本机链接说成手机或外网入口。"""
EVIDENCE_SPEC = ToolSpec("turn.evidence", "读取本轮已认证发言的证据句柄，供记忆、日历、成长和关系提案使用。",
                        encode({"type": "object", "properties": {}, "additionalProperties": False}))


def native_tool_name(action: str) -> str:
    name = "companion_v2_" + re.sub(r"[^a-zA-Z0-9_]", "_", action)
    return name if len(name) <= 64 else name[:47] + "_" + digest(action)[:16]


def _field(prepared, name, default=None):
    return prepared.get(name, default) if isinstance(prepared, Mapping) else getattr(prepared, name, default)


def native_call_id(wrapper, action, arguments):
    """Use the actual assistant tool call, never a model-supplied routing field."""
    event = getattr(getattr(wrapper, "context", None), "event", None)
    state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA) if event is not None else None
    session = state.get("_tool_session") if isinstance(state, dict) else None
    if isinstance(session, CoreReplyToolSession) and session.native_run_context is not None:
        if session.native_run_context is not wrapper:
            raise TaskError("core_tool_runner_mismatch", "tool execution belongs to another native runner")
        matching = [call for call in session.native_calls
                    if call["name"] == native_tool_name(action) and call["arguments"] == arguments]
        if matching:
            selected = next((call for call in matching if not call["used"]), matching[0])
            selected["used"] = True
            return selected["id"]
        raise TaskError("core_tool_call_missing", "the native runner did not request this operation")
    messages = getattr(wrapper, "messages", ())
    completed = {_field(message, "tool_call_id") for message in messages if _field(message, "role") == "tool"}
    for message in reversed(messages):
        if _field(message, "role") != "assistant":
            continue
        matches = []
        for call in _field(message, "tool_calls", ()) or ():
            function = _field(call, "function")
            if _field(function, "name") != native_tool_name(action):
                continue
            try:
                requested = json.loads(_field(function, "arguments") or "{}")
            except (TypeError, ValueError):
                continue
            identifier = _field(call, "id")
            if requested == arguments and isinstance(identifier, str) and identifier:
                matches.append(identifier)
        if matches:
            return next((identifier for identifier in matches if identifier not in completed), matches[0])
    raise TaskError("core_tool_call_missing", "an operation requires the actual native assistant tool call")


def observe_core_tool_calls(event, response, run_context):
    """Freeze the actual dispatch response before AstrBot executes its tools.

    AstrBot appends the assistant tool message after executing the batch. The
    host observer therefore supplies this response at the native dispatch point.
    The copy lives only on this admitted event and never enters telemetry.
    """
    if (getattr(response, "is_chunk", False)
            or getattr(getattr(run_context, "context", None), "event", None) is not event):
        return False
    state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    session = state.get("_tool_session") if isinstance(state, dict) else None
    if not isinstance(session, CoreReplyToolSession):
        return False
    session.assert_current()
    if session.native_response is response and session.native_run_context is run_context:
        return True
    calls = []
    for name, arguments, identifier in zip(getattr(response, "tools_call_name", ()) or (),
                                         getattr(response, "tools_call_args", ()) or (),
                                         getattr(response, "tools_call_ids", ()) or ()):
        if not isinstance(identifier, str) or not identifier:
            continue
        arguments = {} if arguments is None else arguments
        if not isinstance(arguments, dict):
            continue
        calls.append({"name": name, "arguments": json.loads(encode(arguments)),
                      "id": identifier, "used": False})
    session.native_response, session.native_run_context = response, run_context
    session.native_calls = calls
    return True


@dataclass(frozen=True)
class _OfferedTool:
    spec: Any
    owner: str


class CoreReplyFunctionTool(FunctionTool):
    def __init__(self, session, offered):
        from .core_reply_image_prompt import core_image_tool_contract
        reference_limit = None
        if offered.spec.action == "image.edit":
            try:
                from .model_route_material import image_edit_reference_limit
                reference_limit = image_edit_reference_limit(
                    getattr(session.application, "native_tool_port", None))
            except Exception:
                reference_limit = None
        description, parameters = core_image_tool_contract(offered.spec.action, offered.spec.description,
                                                          json.loads(offered.spec.parameters_json),
                                                          reference_limit=reference_limit)
        super().__init__(name=native_tool_name(offered.spec.action), description=description, parameters=parameters)
        self._session, self._action = session, offered.spec.action

    async def call(self, context, **kwargs):
        return await self._session.call(self._action, context, kwargs)


class CoreReplyToolSession:
    """Bind tools to the admitted native event without creating a reply batch."""

    def __init__(self, event, state, application):
        self.event, self.state, self.application = event, state, application
        self.prepared = state["prepared"]
        self.context = _field(self.prepared, "context")
        if not isinstance(self.context, TaskContext) or not callable(state.get("authorize")):
            raise TaskError("core_tool_context_invalid", "core tools require the admitted event context")
        self.turn_id = "core-reply:" + digest([self.context.key, _field(self.prepared, "event_id")])
        profile = _field(self.prepared, "group_tool_profile", "full")
        self.group_tool_profile = profile if profile in ("lean", "standard", "full") else "full"
        self.native_response = self.native_run_context = None
        self.native_calls = []
        from .core_reply_media import frozen_image_locators
        self.image_locators = frozen_image_locators(event)
        semantic = getattr(application.tools, "semantic", None)
        original = _field(self.prepared, "native_input")
        if semantic is not None and original is not None:
            semantic.reaction_preferences.prepare_native(self.context, self.turn_id,
                event_id=original.event_id, text=original.text, at=original.at)

    def catalog(self):
        app, context = self.application, self.context
        app.require_running()
        tools = app.tools
        offered = []
        if context.scope.kind is ScopeKind.PRIVATE:
            if _field(self.prepared, "native_source_future") is not None:
                for spec in tools.catalog:
                    if spec.action == "source.read":
                        offered.append(_OfferedTool(spec, "source"))
            if getattr(tools, "diagnostics", None) is not None:
                for spec in tools.catalog:
                    if spec.action.startswith("diagnostics."):
                        offered.append(_OfferedTool(spec, "diagnostics"))
                command = self.state.get("functional_command")
                intent = getattr(command, "intent", None)
                if intent in ("diagnostics_confirm", "diagnostics_cancel"):
                    from companion_v2.conversation.diagnostic_tools import diagnostic_control_specs
                    action = "diagnostics.proposal.confirm" if intent == "diagnostics_confirm" else "diagnostics.proposal.cancel"
                    spec = next(item for item in diagnostic_control_specs(getattr(command, "argument", ""))
                                if item.action == action)
                    offered.append(_OfferedTool(spec, "diagnostics"))
            if getattr(tools, "agenda", False):
                policy = app.agenda.policy
                for spec in tools.catalog:
                    if (spec.action.startswith(("memo.", "reminder.")) or spec.action == "operation.lookup") and policy.feature_access(context.actor, scope=context.scope,
                            action=spec.action, observed_at=app.tasks.now()).is_success:
                        offered.append(_OfferedTool(spec, "agenda"))
            memory = getattr(tools, "memory", None)
            if memory is not None and not tools._memory_is_external():
                offered.extend(_OfferedTool(spec, "memory") for spec in memory.catalog_for(context))
            calendar = getattr(tools, "calendar", None)
            if calendar is not None:
                offered.extend(_OfferedTool(spec, "calendar") for spec in calendar.catalog_for(context))
            for owner in ("growth", "relationships", "timeline"):
                reader = getattr(tools, owner, None)
                if reader is not None:
                    offered.extend(_OfferedTool(spec, owner) for spec in reader.catalog_for(context))
        else:
            participation = getattr(tools, "group_participation", None)
            # An explicit @ already selected this turn for a reply, but hiding
            # the participation tool here does not stop the model from calling
            # it: the call is in the conversation history, so the host answers
            # "tool not found" and the turn is wasted. The duplicate send that
            # hiding used to avoid is handled where the tool-step prose is
            # deferred (core_reply_tool_dispatch), not by removing a tool the
            # model is going to call.
            if participation is not None:
                offered.extend(_OfferedTool(spec, "participation") for spec in participation.catalog_for(context))
                behavior = getattr(participation, "behavior", None)
                if behavior is not None and _field(self.prepared, "native_input") is not None:
                    offered.extend(_OfferedTool(spec, "behavior") for spec in behavior.catalog_for(context))
            group = getattr(tools, "group_memory", None)
            if group is not None:
                offered.extend(_OfferedTool(spec, "group") for spec in group.catalog_for(context))
        acquaintance = getattr(tools, "acquaintance", None)
        if acquaintance is not None:
            offered.extend(_OfferedTool(spec, "acquaintance") for spec in acquaintance.catalog_for(context))
        weather = getattr(tools, "weather", None)
        if weather is not None:
            offered.extend(_OfferedTool(spec, "weather") for spec in weather.catalog_for(context))
        semantic = getattr(tools, "semantic", None)
        if semantic is not None:
            try:
                semantic_tools = tuple(semantic.catalog_for(context, self.turn_id, mount_stable=True))
            except Exception as error:
                logger.warning("[CompanionV2] 语义工具目录暂不可用，继续挂载其他能力 error_type=%s",
                               type(error).__name__)
            else:
                offered.extend(_OfferedTool(spec, "semantic") for spec in semantic_tools)
            news = getattr(semantic, "news_context", None)
            if news is not None:
                offered.extend(_OfferedTool(spec, "news") for spec in news.catalog_for(context))
        external = getattr(tools, "external", None)
        if external is not None:
            native_tools = tuple(spec for spec in external.catalog_for(context) if supports_native_external(spec.action))
            offered.extend(_OfferedTool(spec, "external") for spec in native_tools)
            authoring = getattr(tools, "authoring", None)
            if authoring is not None and _field(self.prepared, "native_input") is not None and _field(
                    self.prepared, "native_source_future") is not None:
                offered.extend(_OfferedTool(spec, "authoring") for spec in authoring.catalog_for(
                    context, (spec.action for spec in native_tools)))
        if _field(self.prepared, "native_source_future") is not None:
            offered.append(_OfferedTool(EVIDENCE_SPEC, "evidence"))
        if self.context.scope.kind is ScopeKind.GROUP and self.group_tool_profile in ("lean", "standard"):
            return group_tool_manifest(offered, self.group_tool_profile)
        return tuple(offered)

    def choose_participation(self, wrapper, arguments):
        from companion_v2.conversation.native_participation import AUTONOMOUS_REASONS, native_participation_turn
        from companion_v2.conversation.reply_settings import GROUP
        call_id = native_call_id(wrapper, "group.participation.choose", arguments)
        decision = self.state.get("group_participation")
        if decision is not None:
            if decision["mode"] != arguments["mode"]:
                raise TaskError("group_participation_fixed", "the original native group choice is already fixed")
            return {"state": "succeeded", "effect": "none", "payload": dict(decision), "replayed": True}
        if arguments["mode"] == "skip":
            if self.state.get("_business_tools_started") or self.state.get("send_attempts", 0):
                raise TaskError("group_participation_started", "a group turn cannot revoke an operation that already began")
            if not self._skip_is_alone(wrapper):
                raise TaskError("group_participation_skip_alone", "listening may accompany learning notes, but no business operation")
        admission = _field(self.prepared, "group_admission", {}) or {}
        act, signature = "", ""
        if arguments["mode"] == "reply" and not admission.get("mentioned") and admission.get("reason") in AUTONOMOUS_REASONS:
            try:
                snapshot = self.application.reply_settings.snapshot(self.context.actor, self.context, self.application.tasks.now())
                settings = {key.removeprefix(GROUP + "."): value for key, value in snapshot.values.items()}
                turn = native_participation_turn(self.application.tools.group_participation, self.context,
                                                _field(self.prepared, "native_input"), settings)
                act, signature = turn["act"], turn["signature"]
            except Exception:
                pass
        decision = {"mode": arguments["mode"], "reason": " ".join(arguments.get("reason", "").split())[:256],
                    "act": act, "signature": signature, "call_id": call_id}
        self.state["group_participation"] = decision
        from .core_reply_tracking import observe_core_group_participation
        observe_core_group_participation(self.event, decision)
        return {"state": "succeeded", "effect": "none", "payload": dict(decision), "replayed": False}

    def _skip_is_alone(self, wrapper):
        calls = self.native_calls
        if self.native_run_context is None:
            latest = next((message for message in reversed(getattr(wrapper, "messages", ()))
                           if _field(message, "role") == "assistant" and _field(message, "tool_calls")), None)
            calls = _field(latest, "tool_calls", ())
        from companion_v2.conversation.behavior.service import ANNOTATIONS
        annotation_names = {native_tool_name(action) for action in ANNOTATIONS}
        operations = [call for call in calls
                      if _field(call, "name", _field(_field(call, "function"), "name")) not in annotation_names]
        return len(operations) == 1

    def _latch_group_skip(self, wrapper, action, arguments):
        reason = arguments.get("reason", "")
        if (action != "group.participation.choose" or arguments.get("mode") != "skip"
                or not isinstance(reason, str)
                or self.context.scope.kind is not ScopeKind.GROUP
                or self.state.get("group_participation") is not None
                or self.state.get("_business_tools_started") or self.state.get("send_attempts", 0)):
            return
        try:
            call_id = native_call_id(wrapper, action, arguments)
        except TaskError:
            return
        if not self._skip_is_alone(wrapper):
            return
        decision = {"mode": "skip", "reason": " ".join(reason.split())[:256],
                    "act": "", "signature": "", "call_id": call_id}
        self.state["group_participation"] = decision
        from .core_reply_tracking import observe_core_group_participation
        observe_core_group_participation(self.event, decision)

    async def wait_source(self):
        pending = _field(self.prepared, "native_source_future")
        if pending is None:
            raise TaskError("native_tool_source_unavailable", "this native turn has no source observation")
        reference = await asyncio.shield(pending)
        self.assert_current()
        if reference is None:
            raise TaskError("native_tool_source_unavailable", "this message could not be recorded as evidence")
        return reference

    async def evidence(self):
        reference = await self.wait_source()
        from companion_v2.conversation.native_tool_sources import native_offers
        offers = native_offers(self.application.messages, self.context, self.turn_id)
        result = {"state": "succeeded", "effect": "none", "evidence_handles": list(offers)}
        if self.context.scope.kind is ScopeKind.GROUP:
            result["input_handle"] = "group-input-native"
        else:
            memory = getattr(self.application.tools, "memory", None)
            if memory is not None and not self.application.tools._memory_is_external():
                query = memory.memory.query
                query.begin(self.context, "memory:" + self.turn_id)
                captured = memory.memory.writer.access.capture(self.context, reference)
                result["memory_evidence_handle"] = query.offer(self.context, "memory:" + self.turn_id, captured)
        return result

    def assert_current(self):
        if (self.event.get_extra(CORE_REPLY_CONTEXT_EXTRA) is not self.state
                or self.state.get("prepared") is not self.prepared):
            raise TaskError("core_tool_context_changed", "the original admitted tool context changed")
        self.application.require_running()

    async def authorize(self, wrapper):
        native_event = getattr(getattr(wrapper, "context", None), "event", None)
        if native_event is not self.event or self.event.get_extra(CORE_REPLY_CONTEXT_EXTRA) is not self.state:
            raise TaskError("core_tool_event_mismatch", "tool execution belongs to another native event")
        self.assert_current()
        result = await self.state["authorize"](self.prepared)
        if not isinstance(result, Outcome):
            raise TypeError("core tool authorization must return an Outcome")
        require(result)

    async def call(self, action, wrapper, arguments):
        result = None
        try:
            self._latch_group_skip(wrapper, action, arguments)
            await self.authorize(wrapper)
            from companion_v2.conversation.behavior.service import ANNOTATIONS
            if (self.state.get("group_participation", {}).get("mode") == "skip"
                    and action not in ANNOTATIONS and action != "group.participation.choose"):
                raise TaskError("group_participation_skipped", "this group turn chose to keep listening")
            offered = next((item for item in self.catalog() if item.spec.action == action), None)
            if offered is None:
                raise TaskError("core_tool_unavailable", "this tool is no longer authorized for this event")
            schema = json.loads(offered.spec.parameters_json)
            from jsonschema import validate
            validate(arguments, schema)
            if offered.owner == "participation":
                result = self.choose_participation(wrapper, arguments)
            elif action not in ANNOTATIONS:
                self.state["_business_tools_started"] = True
            if offered.owner == "participation":
                pass
            elif offered.owner == "behavior":
                from .core_reply_behavior import execute_core_behavior
                result = execute_core_behavior(self, action, arguments, native_call_id(wrapper, action, arguments))
            elif offered.owner == "evidence":
                result = await self.evidence()
            elif offered.owner == "external":
                if action in ("image.edit", "image.mask_edit"):
                    from .core_reply_media import prepare_native_input_images
                    await prepare_native_input_images(self, wrapper)
                if action in ("reality.camera.observe", "content.work.create", "content.work.save_candidate"):
                    await self.wait_source()
                result = await executor_for(self.application).execute(
                    self, native_call_id(wrapper, action, arguments), action, arguments)
            elif offered.owner == "authoring":
                from .core_reply_authoring import execute_core_authoring
                result = await execute_core_authoring(self, wrapper, native_call_id(wrapper, action, arguments),
                                                      action, arguments)
            elif offered.owner == "source":
                from .core_reply_sources import execute_core_source_read
                result = await execute_core_source_read(self, native_call_id(wrapper, action, arguments), arguments)
            elif offered.owner == "diagnostics":
                from .core_reply_diagnostics import execute_core_diagnostic
                result = await execute_core_diagnostic(self, native_call_id(wrapper, action, arguments), action, arguments)
            elif offered.owner == "weather":
                session = self

                class WeatherRead:
                    def validate(self):
                        session.assert_current()
                        require(session.application.tasks.check_context_access(session.context,
                            action="conversation.reply", observed_at=session.application.tasks.now()))

                result = await self.application.tools.weather.execute(WeatherRead(), self.context,
                    arguments, observed_at=self.application.tasks.now())
            elif offered.owner == "news":
                call_id = native_call_id(wrapper, action, arguments)
                news = self.application.tools.semantic.news_context
                result = await executor_for(self.application).local_async(self, call_id, action, arguments,
                    lambda: news.lookup_native(self.context, self.turn_id, call_id, arguments["query"]))
                result = news.validate_native(self.context, result)
            elif offered.owner == "semantic":
                call_id = native_call_id(wrapper, action, arguments)
                if action == "slang.learn":
                    await self.wait_source()
                semantic = self.application.tools.semantic
                result = await executor_for(self.application).local_async(self, call_id, action, arguments,
                    lambda: semantic.execute_native(self.context, self.turn_id, call_id, action, arguments),
                    effectful=action == "slang.learn")
                result = semantic.validate_native(self.context, self.turn_id, call_id, action, result)
                if result["state"] == "succeeded" and action == "reaction.search":
                    reaction = self.state.get("_reaction_reply")
                    if isinstance(reaction, dict):
                        reaction["attempted"] = True
                    result["_images"] = semantic.reactions.native_images(
                        self.context, self.turn_id, call_id, result["payload"])
                    from .core_reply_reactions import offer_core_reactions
                    offer_core_reactions(self.event, self, call_id, result["payload"], result["_images"])
            elif ((offered.owner == "agenda" and action not in READ_ACTIONS)
                    or (offered.owner == "memory" and action not in MEMORY_READ_ACTIONS)
                    or action in ("calendar.command", "role.growth.propose", "role.growth.revise",
                                  "relationship.memory.propose", "group.memory.remember", "group.memory.retract")):
                call_id = native_call_id(wrapper, action, arguments)
                if offered.owner in ("calendar", "growth", "relationships", "group"):
                    await self.wait_source()
                result = executor_for(self.application).local(self, call_id, action, arguments,
                    lambda identifier: self._command(offered.owner, action, arguments, call_id, identifier))
            else:
                result = self._read(offered.owner, action, arguments)
            await self.authorize(wrapper)
            images = result.pop("_images", ())
            if images:
                return CallToolResult(content=[TextContent(type="text", text=encode(result)),
                    *(ImageContent(type="image", data=native_image_data(image), mimeType=image.descriptor.mime_type)
                      for image in images)])
            return encode(result)
        except TaskError as error:
            if isinstance(result, dict) and result.get("effect") in ("committed", "unknown"):
                return encode({key: value for key, value in {**result, "material_state": "unavailable", "code": error.code}.items()
                               if key in {"state", "effect", "invocation_id", "operation_id", "receipt_ref", "material_state", "code"}})
            return encode({"state": "failed", "effect": "none", "code": error.code})
        except (ValueError, ValidationError):
            return encode({"state": "failed", "effect": "none", "code": "invalid_tool_arguments"})

    def _read(self, owner, action, arguments):
        app, context = self.application, self.context
        if owner == "agenda":
            if context.audience.recipients != (context.actor.subject,):
                raise TaskError("private_result_required", "agenda reads require the original private audience")
            target = AgendaTarget(context.scope, context.role_binding.role, context.actor.subject)
            parameters = dict(arguments)
            if action == "memo.list" and "after" in parameters:
                parameters["after"] = MemoCursor(**parameters["after"])
            readers = {"memo.read": app.agenda.read_memo, "memo.list": app.agenda.list_memos,
                       "reminder.read": app.agenda.read_reminder, "reminder.list": app.agenda.list_reminders,
                       "operation.lookup": app.agenda.receipt}
            payload = require(readers[action](context.actor, target=target, observed_at=app.tasks.now(), **parameters))
            return {"state": "succeeded", "effect": "none", "payload": payload}
        if owner == "memory":
            memory = app.tools.memory
            memory.memory.query.begin(context, "memory:" + self.turn_id)
            result, _ = memory.execute(context, self.turn_id, "read", action, arguments)
            return result
        if owner == "calendar":
            payload = app.tools.calendar.calendar.inventory(context, **{"limit": 20, **arguments})
            return {"state": "succeeded", "effect": "none", "payload": payload}
        if owner in ("growth", "timeline"):
            reader = getattr(app.tools, owner)
            result, _ = reader.execute(context, self.turn_id, "read", action, arguments)
            return result
        reader = app.tools.group_memory if owner == "group" else app.tools.acquaintance
        result, _ = reader.execute(context, self.turn_id, "read", action, arguments)
        return result

    def _command(self, owner, action, arguments, call_id, identifier):
        app, context = self.application, self.context
        if owner == "memory":
            memory = app.tools.memory
            memory.memory.query.begin(context, "memory:" + self.turn_id)
            result, _ = memory.execute(context, self.turn_id, call_id, action, arguments)
            return result
        if owner in ("calendar", "growth", "relationships", "group"):
            reader = app.tools.group_memory if owner == "group" else getattr(app.tools, owner)
            result, _ = reader.execute(context, self.turn_id, call_id, action, arguments)
            return result
        if context.audience.recipients != (context.actor.subject,):
            raise TaskError("private_result_required", "agenda commands require the original private audience")
        target = AgendaTarget(context.scope, context.role_binding.role, context.actor.subject)
        operation = OperationRef(identifier, "agenda", target.scope, VersionRef(identifier, 1), identifier)
        parameters = dict(arguments)
        if "schedule" in parameters:
            parameters["schedule"] = AgendaRoleSession._schedule(parameters["schedule"])
        common = {"target": target, "operation": operation, "expected_role_revision": context.role_binding.revision,
                  "observed_at": app.tasks.now()}
        if action == "memo.create":
            receipt = require(app.agenda.create_memo(context.actor, memo_id="memo-" + identifier,
                                                    **common, **parameters))
        elif action.startswith("memo."):
            if action == "memo.edit":
                changes = dict(parameters["changes"])
                if "schedule" in changes:
                    changes["schedule"] = AgendaRoleSession._schedule(changes["schedule"])
                parameters["changes"] = changes
            receipt = require(app.agenda.change_memo(context.actor, action=action.split(".", 1)[1],
                                                     **common, **parameters))
        elif action == "reminder.create":
            receipt = require(app.agenda.create_reminder(context.actor, reminder_id="reminder-" + identifier,
                                                        **common, **parameters))
        else:
            receipt = require(app.agenda.change_reminder(context.actor, action=action.split(".", 1)[1],
                                                       **common, **parameters))
        return {"state": "succeeded", "effect": "committed", "payload": receipt,
                "operation_id": receipt.operation_id}


def inject_core_reply_tools(event, req, *, application):
    """Copy the request's ToolSet and append authorized tools for this event only."""
    state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    if not isinstance(state, dict):
        return ()
    state["_turn_notes"] = {}
    session = state.get("_tool_session")
    if (not isinstance(session, CoreReplyToolSession) or session.application is not application
            or session.prepared is not state.get("prepared")):
        session = CoreReplyToolSession(event, state, application)
        state["_tool_session"] = session
    from .core_reply_image_prompt import core_image_tools, inject_core_image_prompt
    # Consent is scoped to the exact AstrBot conversation. A new conversation id
    # (persona switch, restart, new private session) makes every retained image
    # capability invisible until it is provisioned for this scope again, so do it
    # here, on the real event context, before the catalogue is filtered.
    from .image_access import ensure_image_access
    ensure_image_access(application.runtime, session.context, observed_at=application.tasks.now())
    offered, image_values = core_image_tools(session, session.catalog())
    semantic = getattr(application.tools, "semantic", None)
    if semantic is not None:
        from .core_reply_reaction_delivery import inject_core_reaction_instruction
        inject_core_reaction_instruction(event, req, semantic, session.turn_id)
    existing = list(getattr(getattr(req, "func_tool", None), "tools", ()))
    existing = [tool for tool in existing if not isinstance(tool, CoreReplyFunctionTool)]
    if session.group_tool_profile == "lean" and session.context.scope.kind is ScopeKind.GROUP:
        # The lean manifest owns the group tool surface.  Host shell, file,
        # python and search schemas are large and rarely needed for banter.
        existing = []
    names = {tool.name for tool in existing}
    appended = [CoreReplyFunctionTool(session, item) for item in offered
                if native_tool_name(item.spec.action) not in names]
    if appended:
        # AstrBot only canonicalises the OpenAI schema; the Gemini and Anthropic
        # serialisers keep insertion order.  Sort here so every provider sees the
        # same declaration order no matter how the host registered its tools.
        req.func_tool = ToolSet(tools=sorted([*existing, *appended], key=lambda tool: tool.name))
        current = getattr(req, "system_prompt", "") or ""
        if TOOL_INSTRUCTION not in current:
            req.system_prompt = (current.rstrip() + "\n\n" + TOOL_INSTRUCTION).lstrip()
        if (session.group_tool_profile == "lean" and session.context.scope.kind is ScopeKind.GROUP
                and LEAN_GROUP_INSTRUCTION not in req.system_prompt):
            req.system_prompt = (req.system_prompt.rstrip() + "\n\n" + LEAN_GROUP_INSTRUCTION).lstrip()
        if any(item.owner == "authoring" for item in offered):
            from companion_v2.conversation.content.authoring_workflow import INSTRUCTION as AUTHORING_INSTRUCTION
            if AUTHORING_INSTRUCTION not in req.system_prompt:
                req.system_prompt += "\n\n" + AUTHORING_INSTRUCTION
        if any(item.owner == "diagnostics" for item in offered):
            from companion_v2.conversation.diagnostic_tools import DIAGNOSTIC_INSTRUCTION
            if DIAGNOSTIC_INSTRUCTION not in req.system_prompt:
                req.system_prompt += "\n\n" + DIAGNOSTIC_INSTRUCTION
    elif getattr(req, "func_tool", None) is not None:
        req.func_tool = ToolSet(tools=sorted(existing, key=lambda tool: tool.name))
    inject_core_image_prompt(session, req, offered, image_values)
    return tuple(tool._action for tool in appended)


def inject_core_turn_notes(event, req):
    """Apply per-turn directives as user content, after the host cleared them.

    System prompt and tool definitions stay identical between turns; only this
    turn's directive rides the current user message, which no prefix cache
    covers anyway.
    """
    state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    if not isinstance(state, dict):
        return
    notes = state.get("_turn_notes")
    if not isinstance(notes, dict):
        return
    from .native_extensions.request_context import set_turn_context
    for name in ("reactions", "image"):
        set_turn_context(req, name, str(notes.get(name) or ""))


async def close_core_reply_tools(application):
    executor = getattr(application, "_core_reply_tool_executor", None)
    if executor is not None:
        await executor.close()
