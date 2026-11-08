"""Local agenda tools and continuation on the original role's model boundary.

The host binds the event, target and audience. Model arguments cannot replace them.
This adapter produces a draft only; no platform routing or sender is installed here.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import json
from typing import Callable, Mapping

from companion_v2.execution.models import encode_prompt
from companion_v2.agenda.models import AgendaError, AgendaReceipt, AgendaTarget, MemoCursor, Schedule, encode, fingerprint, opaque
from companion_v2.agenda.service import AgendaService, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import AudienceRef, OperationRef, ScopeKind, VersionRef
from companion_v2.sources.repositories import SourceRepository
from companion_v2.storage.runtime import StorageRuntime

from .foreground import GroundedDraftReviewer, LocalModelTransport, ModelDraft, PromptMaterial, PromptRequest, SealedForegroundContext


TOOL_FIELDS = {
    "memo.create": (frozenset({"title", "body"}), frozenset({"schedule", "purpose"})),
    "memo.read": (frozenset({"memo_id"}), frozenset()),
    "memo.list": (frozenset(), frozenset({"limit", "after", "search", "state", "purpose"})),
    "memo.edit": (frozenset({"memo_id", "expected_revision", "changes"}), frozenset()),
    **{f"memo.{action}": (frozenset({"memo_id", "expected_revision"}), frozenset())
       for action in ("complete", "reopen", "pin", "unpin", "delete")},
    "reminder.create": (frozenset(), frozenset({"schedule", "message", "memo_id", "expected_memo_revision"})),
    "reminder.read": (frozenset({"reminder_id"}), frozenset()),
    "reminder.list": (frozenset(), frozenset({"limit", "after_id"})),
    "reminder.cancel": (frozenset({"reminder_id", "expected_revision"}), frozenset()),
    "reminder.reschedule": (frozenset({"reminder_id", "expected_revision", "schedule"}), frozenset({"expected_memo_revision"})),
    "operation.lookup": (frozenset({"operation_id"}), frozenset()),
}

CONTINUATION_INSTRUCTION = (
    "继续当前角色和当前会话，不另起办事人格。以下工具结果是本轮实际操作记录，便签保存、提醒安排和实际投递分别解释：local_registered/local_prepared 只证明本地阶段，delivery=not_started 时不得说已经发出或用户已收到。便签完成是一次状态操作，不证明用户实际做过对应活动；replayed 表示返回原操作结果，不是又执行了一次。操作回执的 revision 和状态属于该次已完成操作；同轮后来又修改过对象时，以最新实际结果解释变化，不把较早回执当成对象当前状态。权限不足或操作失败不影响正常聊天，但不编造成功、私有内容或换对象重试。private_result_required 表示本轮没有可用于当前受众的私密结果；说明需在私密入口查看，不声称已另外私信。按角色自然表达，不朗读内部字段。"
)


@dataclass(frozen=True)
class AgendaToolResult:
    call_id: str
    action: str
    state: str
    payload_json: str


class AgendaRoleSession:
    """A turn-local adapter; durable business replay remains in AgendaService."""

    def __init__(
        self, runtime: StorageRuntime, *, request: PromptRequest, target: AgendaTarget,
        audience: AudienceRef, now: Callable[[], datetime], sources: SourceRepository,
    ) -> None:
        self.service = AgendaService(runtime)
        self.request = request
        self.target = target
        self.audience = audience
        self._audience_snapshot = audience
        self.now = now
        self.sources = sources
        self.cancelled = False
        self.actor = require(self.service.identity.resolve_authenticated(request.event.principal, observed_at=now()))
        if audience.scope != request.event.scope or target.scope.installation_id != request.event.scope.installation_id:
            raise AgendaError("tool_context_mismatch", "host audience or target belongs to another scope/installation")
        self.origin_binding = request.role_binding
        target_result = self.service.identity.resolve_role(target.scope, observed_at=now())
        if target_result.failure is not None and target_result.failure.code != "role_binding_missing":
            require(target_result)
        target_binding = target_result.value
        self.target_role_revision = target_binding.revision if target_binding else 0
        self._results: dict[str, tuple[str, AgendaToolResult, Callable[[], object]]] = {}
        self._current()

    def cancel(self) -> None:
        self.cancelled = True

    def _current(self) -> None:
        if self.cancelled:
            raise AgendaError("role_turn_cancelled", "role turn was cancelled")
        if self.audience != self._audience_snapshot:
            raise AgendaError("tool_audience_changed", "host audience changed during the original turn")
        current = require(self.service.identity.resolve_authenticated(self.request.event.principal, observed_at=self.now()))
        if (current.subject != self.actor.subject or current.binding_id != self.actor.binding_id
                or current.binding_revision != self.actor.binding_revision):
            raise AgendaError("binding_rebound", "original caller binding changed")
        binding = require(self.service.identity.resolve_role(self.request.event.scope, observed_at=self.now()))
        if binding != self.origin_binding:
            raise AgendaError("role_binding_changed", "original role changed before continuation")
        for reference in set((self.request.event.source, *self.request.context.evidence_sources)):
            material = require(self.sources.read(reference, resolved=self.actor, capability=self.request.event.capability,
                                                 observed_at=self.now(), authorizer=self.service.identity))
            if not set(self.audience.recipients).issubset(material.audience.recipients):
                raise AgendaError("tool_material_audience_mismatch", "original material is not available to this whole audience")

    def _private_result_allowed(self) -> bool:
        return self.audience.scope.kind is ScopeKind.PRIVATE and self.audience.recipients == (self.actor.subject,)

    @staticmethod
    def _schedule(value: object) -> Schedule | None:
        if value is None:
            return None
        if not isinstance(value, dict) or set(value).difference({"anchor", "timezone_name", "frequency", "interval", "count", "fold", "gap_policy"}):
            raise AgendaError("invalid_tool_arguments", "schedule must contain only supported calendar fields")
        if not {"anchor", "timezone_name"}.issubset(value):
            raise AgendaError("invalid_tool_arguments", "schedule needs absolute time and timezone")
        complete = {"frequency": "once", "interval": 1, "count": None, "fold": 0, "gap_policy": "reject", **value}
        return Schedule.restore(complete)

    def _operation(self, call_id: str) -> OperationRef:
        identifier = fingerprint([self.request.event.event_id, self.request.event.source, self.actor.binding_id,
                                  self.actor.binding_revision, self.target, call_id])
        return OperationRef(identifier, "agenda", self.target.scope, VersionRef(identifier, 1), identifier)

    def _dispatch(self, call_id: str, action: str, parameters: dict) -> tuple[object, Callable[[], object]]:
        common = {"target": self.target, "observed_at": self.now()}
        operation = self._operation(call_id)
        mutation = {**common, "operation": operation, "expected_role_revision": self.target_role_revision}
        reader: Callable[[], object] | None = None
        if action in ("memo.read", "memo.list", "reminder.read", "reminder.list", "operation.lookup"):
            if action == "memo.list" and "after" in parameters:
                parameters["after"] = MemoCursor(**parameters["after"])
            readers = {
                "memo.read": self.service.read_memo, "memo.list": self.service.list_memos,
                "reminder.read": self.service.read_reminder, "reminder.list": self.service.list_reminders,
                "operation.lookup": self.service.receipt,
            }
            reader = lambda: require(readers[action](self.actor, target=self.target, observed_at=self.now(), **parameters))
            value = reader()
        elif action == "memo.create":
            if "schedule" in parameters:
                parameters["schedule"] = self._schedule(parameters["schedule"])
            value = require(self.service.create_memo(self.actor, memo_id="memo-" + operation.operation_id, **mutation, **parameters))
        elif action.startswith("memo."):
            if action == "memo.edit":
                changes = parameters.get("changes")
                if not isinstance(changes, dict):
                    raise AgendaError("invalid_tool_arguments", "changes must be a field patch")
                if "schedule" in changes:
                    changes["schedule"] = self._schedule(changes["schedule"])
            value = require(self.service.change_memo(self.actor, action=action.split(".", 1)[1], **mutation, **parameters))
        elif action == "reminder.create":
            if "schedule" in parameters:
                parameters["schedule"] = self._schedule(parameters["schedule"])
            value = require(self.service.create_reminder(self.actor, reminder_id="reminder-" + operation.operation_id, **mutation, **parameters))
        else:
            if "schedule" in parameters:
                parameters["schedule"] = self._schedule(parameters["schedule"])
            value = require(self.service.change_reminder(self.actor, action=action.split(".", 1)[1], **mutation, **parameters))
        if reader is None:
            receipt = value
            # Recheck the original operation authority, not a new permission inferred from a tool result.
            def reader():
                domain = action.split(".", 1)[0]
                require(self.service.policy.authorize(self.actor, scope=self.target.scope, action=action, owner=self.target.owner,
                                                      object_key=self.service.object_key(domain, receipt.object_id), observed_at=self.now()))
                current = self.service.repository.memo(receipt.object_id) if domain == "memo" else self.service.repository.reminder(receipt.object_id)
                if current is None or current.target != self.target:
                    raise AgendaError("tool_result_changed", "original object is unavailable")
                return receipt
        return value, reader

    def execute(self, *, call_id: str, action: str, arguments: Mapping[str, object]) -> Outcome[AgendaToolResult]:
        try:
            self._current()
            opaque(call_id)
            fields = TOOL_FIELDS.get(action)
            if fields is None:
                raise AgendaError("tool_not_available", "action is not exposed by this local adapter")
            if not isinstance(arguments, Mapping) or not fields[0].issubset(arguments) or set(arguments).difference(fields[0] | fields[1]):
                raise AgendaError("invalid_tool_arguments", "tool arguments do not match its explicit schema")
            # A detached JSON copy prevents a caller from altering a retained revalidation request.
            parameters = json.loads(encode(dict(arguments)))
            request_hash = fingerprint([action, parameters])
            cached = self._results.get(call_id)
            if cached is not None:
                if cached[0] != request_hash:
                    raise AgendaError("tool_call_conflict", "call ID was already used for different arguments")
                current = cached[2]()
                if cached[1].state == "succeeded" and encode(current) != cached[1].payload_json:
                    raise AgendaError("tool_result_changed", "original tool material is no longer current")
                return Outcome.success(cached[1])
            try:
                value, reader = self._dispatch(call_id, action, parameters)
                payload = encode(value)
                state = "succeeded"
            except AgendaError as error:
                payload = encode({"code": error.code, "committed": "unknown_if_prior_operation_exists"})
                state = "failed"
                reader = lambda: True
            if not self._private_result_allowed():
                state, payload = "private_result_required", encode({"result_available_to_current_audience": False})
            result = AgendaToolResult(call_id, action, state, payload)
            self._results[call_id] = (request_hash, result, reader)
            return Outcome.success(result)
        except AgendaError as error:
            return Outcome.failed(error.code, str(error))
        except (ValueError, TypeError, KeyError) as error:
            return Outcome.failed("invalid_tool_arguments", "invalid tool value or unsupported calendar input")

    def continue_role(self, model: LocalModelTransport) -> Outcome[ModelDraft]:
        """Exactly one continuation generation on the provided original model, no sender."""
        try:
            self._current()
            if not self._results:
                raise AgendaError("tool_result_missing", "this turn has no tool results to continue from")
            visible = []
            for _, result, reader in self._results.values():
                if result.state == "succeeded":
                    if not self._private_result_allowed():
                        raise AgendaError("tool_audience_changed", "private result is no longer allowed here")
                    # Do not silently substitute a newer object for the material the model saw.
                    if encode(reader()) != result.payload_json:
                        raise AgendaError("tool_result_changed", "tool result changed before continuation")
                visible.append(result)
            context = SealedForegroundContext(
                materials=(*self.request.context.materials,
                           PromptMaterial("agenda", "actual_operation_results", encode_prompt(visible))),
                deferred=self.request.context.deferred, unavailable=self.request.context.unavailable,
            )
            request = replace(self.request, context=context, instruction=self.request.instruction + "\n" + CONTINUATION_INSTRUCTION)
            draft = model.generate(request)
            self._current()
            if not GroundedDraftReviewer().review(draft, context, observed_at=self.now()).approved:
                raise AgendaError("draft_not_grounded", "continued draft cites unavailable evidence")
            for _, result, reader in self._results.values():
                if result.state == "succeeded" and encode(reader()) != result.payload_json:
                    raise AgendaError("tool_result_changed", "tool result changed during continuation")
            return Outcome.success(draft)
        except AgendaError as error:
            return Outcome.failed(error.code, str(error))

    def _validate_observation(self, domain: str, document: dict, later_results) -> None:
        identifier = document[domain + "_id"]
        reader = self.service.read_memo if domain == "memo" else self.service.read_reminder
        current = require(reader(self.actor, target=self.target, observed_at=self.now(), **{domain + "_id": identifier}))
        if encode(current) == encode(document):
            return
        revision = document["revision"]
        for result, arguments in later_results:
            if result.state != "succeeded" or arguments.get(domain + "_id") != identifier:
                continue
            if not result.action.startswith(domain + ".") or "expected_revision" not in arguments:
                continue
            receipt = self._validate_receipt(result)
            if (arguments["expected_revision"] != revision or receipt.object_id != identifier
                    or receipt.revision not in (revision, revision + 1)):
                raise AgendaError("tool_result_changed", "the original observation has an unexplained revision gap")
            revision = receipt.revision
        if revision != current.revision or revision == document["revision"]:
            raise AgendaError("tool_result_changed", "the original observation changed outside this turn's operations")

    def _validate_receipt(self, result: AgendaToolResult) -> AgendaReceipt:
        receipt = AgendaReceipt(**json.loads(result.payload_json))
        domain = result.action.split(".", 1)[0]
        require(self.service.policy.authorize(
            self.actor, scope=self.target.scope, action=result.action, owner=self.target.owner,
            object_key=self.service.object_key(domain, receipt.object_id), observed_at=self.now()))
        current = (self.service.repository.memo(receipt.object_id) if domain == "memo"
                   else self.service.repository.reminder(receipt.object_id))
        if current is None or current.target != self.target or current.state == "deleted":
            raise AgendaError("tool_result_changed", "original operation object is unavailable")
        original = require(self.service.receipt(self.actor, target=self.target,
                           operation_id=receipt.operation_id, observed_at=self.now()))
        if (replace(original, replayed=receipt.replayed) != receipt or receipt.action != result.action
                or receipt.operation_id != self._operation(result.call_id).operation_id):
            raise AgendaError("tool_result_changed", "original operation receipt changed")
        return receipt

    def validate_result(self, result: AgendaToolResult, *, arguments: Mapping[str, object],
                        later_results: tuple = ()) -> Outcome[bool]:
        """Validate a durable result without dispatching a mutation or reading newer content into it."""
        try:
            self._current()
            if result.state != "succeeded":
                return Outcome.success(True)
            if not self._private_result_allowed():
                raise AgendaError("tool_audience_changed", "private result is no longer allowed here")
            if result.action in ("memo.read", "memo.list", "reminder.read", "reminder.list"):
                domain, action = result.action.split(".", 1)
                document = json.loads(result.payload_json)
                if action == "list":
                    decision = require(self.service.policy.feature_access(
                        self.actor, scope=self.target.scope, action=result.action, observed_at=self.now()))
                    self.service._role_revision(self.target, decision.tier, self.now())
                # A list is the original observed page, not a query to refill with newer items.
                for item in document if action == "list" else (document,):
                    self._validate_observation(domain, item, later_results)
            elif result.action == "operation.lookup":
                value, _ = self._dispatch(result.call_id, result.action, json.loads(encode(dict(arguments))))
                if encode(value) != result.payload_json:
                    raise AgendaError("tool_result_changed", "the original tool observation changed")
            else:
                self._validate_receipt(result)
            return Outcome.success(True)
        except AgendaError as error:
            return Outcome.failed(error.code, str(error))
