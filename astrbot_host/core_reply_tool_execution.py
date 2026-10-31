"""Native tool operation ownership without a Companion reply generation task."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
import json
import logging

from companion_v2.conversation.external_tools import ExternalToolInvocation, ExternalToolResponse, _SourceAccess
from companion_v2.execution.models import (
    SideEffect, TaskError, TaskRegistration, TaskResult, TaskSpec, digest, encode, require, stamp,
)
from companion_v2.execution.runner import ExactTaskRunner
from companion_v2.execution.service import TaskSupervisor, outcome
from companion_v2.conversation.messages import source_from_document
from companion_v2.conversation.native_tool_media import publish_native_images, read_native_images
from companion_v2.conversation.media.generated import IMAGE_ACTIONS
from companion_v2.sources.dependencies import link


PREFIX = "astrbot.core-tool."
logger = logging.getLogger(__name__)
def supports_native_external(action):
    return True


class CoreReplyToolExecutor:
    """Attach an exact operation worker to the existing supervisor generation."""

    def __init__(self, application):
        self.application, self.runtime = application, application.runtime
        self.external = getattr(application.tools, "external", None)
        self.providers = ({action: provider for action, provider in self.external.providers.items()
                           if supports_native_external(action)} if self.external is not None else {})
        self.registrations = tuple(TaskRegistration(replace(provider.descriptor.definition(),
            kind=PREFIX + digest(provider.descriptor), owner="astrbot.core-tool"), self.validate)
            for provider in self.providers.values())
        self.kinds = {provider.descriptor.tool.action: registration.definition.kind
                      for provider, registration in zip(self.providers.values(), self.registrations)}
        self.tasks = self.runner = None
        self._sessions = {}
        self._closed = False

    def _attach(self):
        self.application.require_running()
        if not self.registrations:
            raise TaskError("core_external_unavailable", "no native external tool owner is installed")
        if self.tasks is None:
            original = self.application.tasks
            self.tasks = TaskSupervisor(self.runtime, self.registrations, limits=original.limits, now=original.now)
            self.tasks.bind_worker_lifecycle(original.worker_lifecycle) if original.worker_lifecycle is not None else None
            require(self.tasks.attach(expected_generation=original.generation))
            self.runner = ExactTaskRunner(self.tasks, frozenset(self.kinds.values()), self._invoke)

    def _key(self, identifier):
        return PREFIX + identifier

    def _row(self, identifier):
        row = self.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?", (self._key(identifier),))
        if row is None:
            raise TaskError("core_tool_invocation_missing", "the original native operation is unavailable")
        return json.loads(row["value"])

    def _save(self, row):
        with self.runtime.transaction() as connection:
            connection.execute("INSERT INTO runtime_metadata (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (self._key(row["invocation_id"]), encode(row)))

    def _existing(self, session, call_id, action, arguments):
        identifier = "native-tool-" + digest([session.turn_id, call_id])
        saved = self.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?", (self._key(identifier),))
        if saved is not None:
            row = json.loads(saved["value"])
            if (row["origin_key"], row["action"], row["arguments_json"]) != (session.context.key, action, encode(arguments)):
                raise TaskError("tool_call_conflict", "a native call ID cannot change its original arguments")
            return identifier, row
        unresolved = self.runtime.read_one("""SELECT value FROM runtime_metadata WHERE key LIKE ?
            AND json_extract(value, '$.origin_key') = ? AND json_extract(value, '$.action') = ?
            AND json_extract(value, '$.arguments_json') = ?
            AND json_extract(value, '$.state') IN ('queued', 'running', 'unknown') LIMIT 1""",
            (PREFIX + "%", session.context.key, action, encode(arguments)))
        if unresolved is not None:
            raise TaskError("external_effect_unresolved", "resolve the original unknown operation before repeating it")
        return identifier, None

    def local(self, session, call_id, action, arguments, command):
        """Keep a SQLite owner's command and native result in one transaction."""
        with self.runtime.transaction():
            identifier, prior = self._existing(session, call_id, action, arguments)
            if prior is not None:
                if prior["state"] not in ("succeeded", "failed"):
                    return {"state": "unknown", "effect": "unknown", "invocation_id": identifier}
                return {**prior["result"], "replayed": True}
            result = command(identifier)
            self._save({"invocation_id": identifier, "origin_key": session.context.key, "turn_id": session.turn_id,
                "call_id": call_id, "action": action, "arguments_json": encode(arguments),
                "state": result["state"], "effect": result.get("effect", "none"), "result": result})
            return result

    async def local_async(self, session, call_id, action, arguments, command, *, effectful=False):
        """Track another SQLite owner's awaited command without replaying uncertainty."""
        with self.runtime.transaction():
            identifier, prior = self._existing(session, call_id, action, arguments)
            if prior is not None:
                if prior["state"] in ("succeeded", "failed"):
                    return {**prior["result"], "replayed": True}
                return {"state": "unknown", "effect": prior["effect"], "invocation_id": identifier}
            row = {"invocation_id": identifier, "origin_key": session.context.key, "turn_id": session.turn_id,
                "call_id": call_id, "action": action, "arguments_json": encode(arguments), "state": "running",
                "effect": "unknown" if effectful else "none", "result": None}
            self._save(row)
        try:
            result = await command()
            row.update(state=result["state"], effect=result["effect"], result=result)
            self._save(row)
            return result
        except BaseException:
            row.update(state="unknown")
            self._save(row)
            raise

    @outcome
    def validate(self, spec, at):
        self.application.require_running()
        row = self._row(spec.request_ref)
        provider = self.providers.get(row["action"])
        if (provider is None or row["provider_json"] != encode(provider.descriptor)
                or row["origin_key"] != spec.context.key or row["task_id"] != spec.task_id
                or self.kinds[row["action"]] != spec.kind):
            raise TaskError("core_tool_invocation_changed", "the native operation changed its original owner or context")
        self.external.admit(provider.descriptor, spec.context, at)
        return True

    async def execute(self, session, call_id, action, arguments):
        if self._closed:
            raise TaskError("core_tool_owner_closed", "native tool ownership has closed")
        self._attach()
        provider = self.providers.get(action)
        if provider is None:
            raise TaskError("core_external_unavailable", "this action requires an unavailable native adapter")
        with self.runtime.transaction():
            self.external.admit(provider.descriptor, session.context, self.tasks.now())
            identifier, prior = self._existing(session, call_id, action, arguments)
            if prior is not None:
                return self._snapshot(prior, session.context)
            now = self.tasks.now()
            if action in IMAGE_ACTIONS:
                self.application.messages.image_quota.admit_native(session.context, now)
            deadline = now + timedelta(seconds=provider.descriptor.timeout_seconds)
            row = {"invocation_id": identifier, "origin_key": session.context.key, "turn_id": session.turn_id,
                "context_json": encode(session.context),
                "call_id": call_id, "action": action, "arguments_json": encode(arguments),
                "provider_json": encode(provider.descriptor), "task_id": identifier,
                "state": "queued", "effect": "none", "result": None}
            self._save(row)
            require(self.tasks.submit(TaskSpec(identifier, session.context, self.kinds[action], identifier,
                                               identifier, now, deadline)))
            self._sessions[identifier] = session
        try:
            run_result = require(await self.runner.run(session.context, identifier)).task
            row = self._row(identifier)
            if run_result.state in ("created", "retry_scheduled"):
                require(self.tasks.cancel(session.context, identifier, expected_revision=run_result.revision))
                row.update(state="failed", effect="not_committed" if provider.descriptor.side_effect is not SideEffect.NONE else "none",
                           result={"state": "failed", "effect": "none", "code": "core_tool_capacity_unavailable"})
                self._save(row)
            elif row["state"] in ("queued", "running"):
                row.update(state="unknown", effect="unknown" if provider.descriptor.side_effect is not SideEffect.NONE else "none")
                self._save(row)
            return self._snapshot(self._row(identifier), session.context)
        except BaseException:
            row = self._row(identifier)
            if row["state"] in ("queued", "running"):
                row.update(state="unknown", effect="unknown" if provider.descriptor.side_effect is not SideEffect.NONE else "none")
                self._save(row)
            raise
        finally:
            self._sessions.pop(identifier, None)

    async def _invoke(self, run):
        row = self._row(run.task.spec.request_ref)
        provider = self.providers[row["action"]]
        session = self._sessions.get(row["invocation_id"])
        if session is None or row["state"] != "queued":
            raise TaskError("core_tool_dispatch_closed", "the original native tool call cannot be dispatched again")
        run.validate()
        effectful = provider.descriptor.side_effect is not SideEffect.NONE
        if effectful:
            run.begin_effect()
        row.update(state="running", effect="unknown" if effectful else "none")
        self._save(row)

        def before_dispatch():
            session.assert_current()
            run.validate()
            self.external.admit(provider.descriptor, run.task.spec.context, self.tasks.now())

        try:
            descriptor = self.external.resolve(provider.descriptor)
            invocation = ExternalToolInvocation(row["invocation_id"], session.turn_id, row["call_id"],
                run.task.spec.context, descriptor, row["arguments_json"], run.task.spec.deadline_at, before_dispatch)
            response = await provider.invoke(invocation)
            self._accept(row, invocation, response)
        except asyncio.CancelledError:
            row.update(state="unknown", effect="unknown" if effectful else "none")
            self._save(row)
            raise
        except Exception as error:
            code = error.code if isinstance(error, TaskError) else "core_tool_provider_unavailable"
            logger.warning("[CompanionV2] 原生工具调用未取得可确认结果 action=%s code=%s", row["action"], code)
            row.update(state="unknown", effect="unknown" if effectful else "none")
            row["result"] = {"state": "unknown", "effect": row["effect"],
                             "invocation_id": row["invocation_id"], "code": code}
            self._save(row)
        current = self._row(row["invocation_id"])
        result = current.get("result") or {}
        return TaskResult("uncertain" if current["state"] == "unknown" else current["state"], current["effect"],
            result_ref=current["invocation_id"] if current["state"] == "succeeded" else None,
            operation_id=result.get("operation_id"), receipt_ref=result.get("receipt_ref"))

    def _accept(self, row, invocation, response):
        if (not isinstance(response, ExternalToolResponse) or
                (response.invocation_id, response.origin_key, response.arguments_hash, response.binding) !=
                (invocation.invocation_id, invocation.context.key, invocation.arguments_hash, invocation.descriptor.binding)):
            raise TaskError("external_receipt_mismatch", "the native receipt differs from its original operation")
        effectful = invocation.descriptor.side_effect is not SideEffect.NONE
        expected = {"succeeded": "committed", "failed": "not_committed", "unknown": "unknown"}[response.state] if effectful else "none"
        if response.effect != expected:
            raise TaskError("external_receipt_mismatch", "the native outcome does not match this adapter's effect contract")
        source = response.source
        if source is not None and (source.reference.owner != invocation.descriptor.source_owner
                or source.audience != invocation.context.audience or source.reference.scope != invocation.context.scope
                or source.content_hash != digest(source.payload)):
            raise TaskError("external_source_scope_mismatch", "the native original must retain its owner and audience")
        with self.runtime.transaction():
            if source is not None:
                for reference in response.dependencies:
                    if reference.scope != invocation.context.scope:
                        raise TaskError("external_source_scope_mismatch", "native dependencies crossed the original scope")
                    for recipient in invocation.context.audience.recipients:
                        require(self.application.messages.sources.validate_reference_for_audience(
                            reference, recipient=recipient, observed_at=self.tasks.now()))
                require(self.application.messages.sources.publish(source, observed_at=self.tasks.now()))
                link(self.runtime, source.reference, response.dependencies, self.tasks.now())
            images = publish_native_images(self.application.messages, invocation.context, source.reference,
                invocation.descriptor.tool.action, response.images, at=self.tasks.now()) if response.images else []
            row.update(state=response.state, effect=response.effect,
                result={"state": response.state, "effect": response.effect, "invocation_id": invocation.invocation_id,
                        "operation_id": response.operation_id, "receipt_ref": response.receipt_ref},
                source=json.loads(encode(source.reference)) if source is not None else None,
                source_hash=digest(source) if source is not None else None, images=images)
            self._save(row)

    def _snapshot(self, row, context):
        provider = self.providers[row["action"]]
        self.external.admit(provider.descriptor, context, self.tasks.now())
        result = row.get("result") or {"state": "unknown", "effect": row["effect"], "invocation_id": row["invocation_id"]}
        if row["state"] == "succeeded":
            task = require(self.tasks.read(context, row["task_id"]))
            if task.state != "succeeded" or task.result_ref != row["invocation_id"]:
                raise TaskError("core_tool_result_unresolved", "the original task has not accepted its result")
            try:
                reference = source_from_document(row["source"])
                source = require(self.application.messages.sources.read(reference, resolved=context.actor,
                    capability=provider.descriptor.binding.capability, observed_at=self.tasks.now(),
                    authorizer=_SourceAccess(self.external, provider.descriptor, context)))
                if digest(source) != row["source_hash"]:
                    raise TaskError("core_tool_result_changed", "the original native source has changed")
                images = read_native_images(self.application.messages, context, reference, row["action"], row.get("images", []),
                    capability=provider.descriptor.binding.capability,
                    authorizer=_SourceAccess(self.external, provider.descriptor, context), at=self.tasks.now())
            except TaskError as error:
                return {**result, "material_state": "unavailable", "code": error.code}
            return {**result, "payload": source.payload, "_images": images}
        return result

    async def close(self):
        self._closed = True
        if self.runner is not None:
            await self.runner.close()


def executor_for(application):
    executor = getattr(application, "_core_reply_tool_executor", None)
    if executor is None:
        executor = CoreReplyToolExecutor(application)
        application._core_reply_tool_executor = executor
    return executor
