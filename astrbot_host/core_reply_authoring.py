"""Run native Content writing from AstrBot's admitted tool call."""

from collections.abc import Mapping
from datetime import timedelta
import json

from companion_v2.conversation.content.authoring_execution import AuthoringParent
from companion_v2.conversation.content.authoring_workflow import CREATE_ACTION, INSTRUCTION
from companion_v2.conversation.foreground import ForegroundContextAssembler, PromptRequest
from companion_v2.conversation.native_tool_results import PREFIX
from companion_v2.conversation.native_turns import input_event
from companion_v2.execution.models import TaskError, digest, encode, require
from companion_v2.model_gateway.models import ToolCall

from .core_reply_tool_execution import executor_for


class NativeAuthoringOperations:
    def __init__(self, session, wrapper, call, revalidate):
        self.session, self.wrapper, self.call = session, wrapper, call
        self.revalidate = revalidate
        self.executor = executor_for(session.application)
        self.external = session.application.tools.external
        self.actions = tuple(spec.action for spec in self.external.catalog_for(session.context)
            if self._native_content(spec.action))
        self.dispatch_session = _AuthoringDispatch(session, revalidate)

    def _native_content(self, action):
        provider = self.external.providers.get(action)
        return (provider is not None and action.startswith("content.work.")
                and provider.descriptor.binding.provider == "content.native"
                and provider.descriptor.source_owner == "content.native")

    async def execute(self, action, arguments, *, step):
        allowed = ({"content.work.create"} if self.call.action == CREATE_ACTION else
                   {"content.work.list", "content.work.describe", "content.work.read", "content.work.save_candidate"})
        if action not in allowed or not self._native_content(action) or type(step) is not int or step < 1:
            raise TaskError("content_authoring_action", "the original writing call cannot perform this operation")
        await self.session.authorize(self.wrapper)
        self.revalidate()
        call_id = "content-authoring-subcall:" + str(step) + ":" + digest(
            [self.session.turn_id, self.call.call_id, step])
        result = await self.executor.execute(self.dispatch_session, call_id, action, arguments)
        self.revalidate()
        if result.get("material_state") == "unavailable":
            raise TaskError(result.get("code") or "content_authoring_receipt", "the saved Content receipt is unavailable")
        row = self.external._row(result["invocation_id"])
        return self.external.snapshot(row, self.session.context, self.session.application.tasks.now())

    def settled_effect(self):
        rows = self.session.application.runtime.read_all("""SELECT value FROM runtime_metadata
            WHERE substr(key, 1, ?) = ? AND json_extract(value, '$.origin_key') = ?
              AND json_extract(value, '$.turn_id') = ?
              AND json_extract(value, '$.call_id') LIKE 'content-authoring-subcall:%'""",
            (len(PREFIX), PREFIX, self.session.context.key, self.session.turn_id))
        effects = []
        for saved in rows:
            row = json.loads(saved["value"])
            try:
                _, step_text, _ = row["call_id"].split(":", 2)
                step = int(step_text)
            except (TypeError, ValueError):
                continue
            expected = "content-authoring-subcall:" + str(step) + ":" + digest(
                [self.session.turn_id, self.call.call_id, step])
            if row["call_id"] == expected:
                effects.append(row["effect"])
        return "unknown" if "unknown" in effects else "committed" if "committed" in effects else "none"


class _AuthoringDispatch:
    """Keep the real native owner and recheck its source at every write guard."""

    def __init__(self, session, revalidate):
        self.context, self.turn_id = session.context, session.turn_id
        self.revalidate = revalidate

    def assert_current(self):
        self.revalidate()


async def execute_core_authoring(session, wrapper, call_id, action, arguments):
    app, context = session.application, session.context
    await session.wait_source()
    native_input = (session.prepared.get("native_input") if isinstance(session.prepared, Mapping)
                    else getattr(session.prepared, "native_input", None))
    if native_input is None:
        raise TaskError("content_authoring_request_unavailable", "writing requires the original native input")

    def revalidate():
        session.assert_current()
        require(app.tasks.check_context_access(context, action="conversation.reply", observed_at=app.tasks.now()))
        from companion_v2.storage.native_dedup import role_instruction_matches
        row = app.runtime.read_one("SELECT * FROM conversation_native_turns WHERE turn_id=?", (session.turn_id,))
        if (row is None or row["context_key"] != context.key
                or not role_instruction_matches(app.runtime, row["role_instruction"], native_input.instruction)
                or row["generation_state"] not in ("prepared", "running")):
            raise TaskError("content_authoring_request_mismatch", "the original native writing turn changed")
        event = input_event(app.messages, row, app.tasks.now())
        if event.event_id != native_input.event_id:
            raise TaskError("content_authoring_request_mismatch", "the native writing input changed")
        return event

    event = revalidate()
    record = app.messages._read_source(event.source, context, "text", app.tasks.now())
    request = PromptRequest(event, native_input.instruction, ForegroundContextAssembler().seal(primary=record),
                            INSTRUCTION, context.role_binding)
    call = ToolCall(call_id, action, encode(arguments))
    parent = AuthoringParent(context, session.turn_id, app.tasks.now() + timedelta(seconds=180), revalidate)
    operations = NativeAuthoringOperations(session, wrapper, call, revalidate)

    async def execute():
        try:
            return await app.tools.authoring.execute_native(parent, call, request, operations)
        except TaskError as error:
            effect = operations.settled_effect()
            return {"state": "failed" if effect == "none" else "unknown", "effect": effect, "code": error.code}

    executor = executor_for(app)
    result = await executor.local_async(session, call_id, action, arguments, execute, effectful=True)
    identifier = "native-tool-" + digest([session.turn_id, call_id])
    if result.get("state") == "succeeded":
        canonical = {key: value for key, value in result.items() if key != "replayed"}
        saved = executor._row(identifier)
        row = {**saved, "execution_path": "astrbot_core", "task_id": session.turn_id, "tool_call_id": call_id}
        try:
            revalidate()
            app.tools.authoring.validate_result(row, context, canonical, app.tools.external, app.tasks.now())
        except TaskError as error:
            return {"state": result["state"], "effect": result["effect"], "invocation_id": identifier,
                    "material_state": "unavailable", "code": error.code}
    return {**result, "invocation_id": identifier}
