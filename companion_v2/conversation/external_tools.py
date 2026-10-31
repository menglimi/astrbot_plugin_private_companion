"""Trusted cooperative extension tools, with independent durable child tasks.

No host library is imported here. The host must attest the current provider and
own any network/device effects. Blocking or untrusted code needs process isolation.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import datetime
import inspect
import json
import logging
import re
from threading import Lock
from types import MappingProxyType
from typing import Awaitable, Callable

from companion_v2.capabilities.models import CapabilityPackageDescriptor
from companion_v2.capabilities.privacy import PRIVACY_ACTIONS, is_core
from companion_v2.capabilities.repositories import CapabilityRepository
from companion_v2.execution.models import (
    Lane, ProviderBinding, SideEffect, TaskClaim, TaskContext, TaskDefinition, TaskError,
    TaskRegistration, TaskResult, TaskSpec, digest, encode, require, stamp,
)
from companion_v2.execution.runner import ExactTaskRunner
from companion_v2.execution.service import outcome
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind, SourceRef
from companion_v2.identity_policy.models import _opaque
from companion_v2.model_gateway.models import MAX_TOOL_CATALOG, ToolSpec
from companion_v2.sources.models import GovernanceState, SourceRecord

from .foreground import PromptMaterial
from .media.models import CapturedImage, check_manifest
from .media.generated import GROUP_IMAGE_ACTIONS, IMAGE_ACTIONS, REJECTED_ADOPTION
from .media.screen import ACTION as SCREEN_ACTION, REJECTED_ADOPTION as SCREEN_REJECTED_ADOPTION
from .messages import source_from_document


MAX_RESULT_BYTES = 65536
MAX_TURN_RESULT_BYTES = 262144
_ADOPTION_WITHDRAWN = frozenset({
    "message_withdrawn", "message_batch_closed", "source_revoked", "source_expired", "source_not_active",
    "asset_revoked", "asset_expired", "group_input_unavailable", "group_reply_unavailable",
    "authorization_revoked", "authorization_expired", "authorization_not_active", "authorization_denied",
    "binding_revoked", "binding_rebound", "binding_expired", "binding_not_active",
})
_TYPES = {"string": (str,), "integer": (int,), "number": (int, float),
          "boolean": (bool,), "null": (type(None),), "object": (dict,), "array": (list,)}
logger = logging.getLogger(__name__)


def _same_declaration(declared, descriptor):
    """A tool keeps its identity when only the host's current binding moved."""
    if descriptor == declared:
        return True
    binding = declared.binding
    return ((descriptor.binding.provider, descriptor.binding.capability)
            == (binding.provider, binding.capability)
            and replace(descriptor, binding=binding) == declared)


def _check_schema(schema, *, depth=0):
    """A closed, explicit subset; unsupported JSON Schema never silently weakens validation."""
    if (depth > 8 or not isinstance(schema, dict) or schema.get("type") not in _TYPES
            or set(schema) - {"type", "description", "title", "enum", "properties", "required",
                              "additionalProperties", "items"}):
        raise ValueError("extension parameters require the supported closed JSON Schema subset")
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        raise ValueError("parameter enum must be nonempty")
    structural = set(schema) & {"properties", "required", "additionalProperties", "items"}
    permitted = {"properties", "required", "additionalProperties"} if schema["type"] == "object" else (
        {"items"} if schema["type"] == "array" else set())
    if structural - permitted:
        raise ValueError("parameter constraints must match their declared type")
    if schema["type"] == "object":
        properties, required = schema.get("properties"), schema.get("required", [])
        if (not isinstance(properties, dict) or schema.get("additionalProperties") is not False
                or not isinstance(required, list) or any(not isinstance(k, str) for k in required)
                or len(set(required)) != len(required) or not set(required).issubset(properties)):
            raise ValueError("extension objects must declare their complete parameter set")
        for child in properties.values():
            _check_schema(child, depth=depth + 1)
    elif schema["type"] == "array":
        _check_schema(schema.get("items"), depth=depth + 1)


def _check_arguments(schema, value):
    if type(value) not in _TYPES[schema["type"]] or (
            "enum" in schema and encode(value) not in tuple(encode(item) for item in schema["enum"])):
        raise TaskError("invalid_tool_arguments", "arguments do not match the registered extension tool")
    if schema["type"] == "object":
        if set(value) - set(schema["properties"]) or not set(schema.get("required", ())).issubset(value):
            raise TaskError("invalid_tool_arguments", "arguments do not match the registered extension tool")
        for name, item in value.items():
            _check_arguments(schema["properties"][name], item)
    elif schema["type"] == "array":
        for item in value:
            _check_arguments(schema["items"], item)


@dataclass(frozen=True)
class ExtensionToolDescriptor:
    tool: ToolSpec
    package: CapabilityPackageDescriptor
    binding: ProviderBinding
    source_owner: str
    side_effect: SideEffect = SideEffect.NONE
    timeout_seconds: float = 15

    def __post_init__(self):
        if not isinstance(self.tool, ToolSpec) or not isinstance(self.package, CapabilityPackageDescriptor):
            raise ValueError("tool registration requires a trusted package and tool specification")
        _opaque(self.tool.action, field="tool action")
        if (not isinstance(self.tool.description, str) or not self.tool.description.strip()
                or len(self.tool.description) > 4096):
            raise ValueError("tool description is required")
        if not isinstance(self.binding, ProviderBinding) or self.binding.capability not in self.package.capabilities:
            raise ValueError("the pinned package must provide the declared capability")
        if self.source_owner not in self.package.dataset_owners:
            raise ValueError("tool originals must belong to a dataset owner declared by the package")
        if not isinstance(self.tool.parameters_json, str) or len(self.tool.parameters_json) > 16384:
            raise ValueError("tool schema must be bounded")
        schema = json.loads(self.tool.parameters_json)
        _check_schema(schema)
        if schema["type"] != "object":
            raise ValueError("tool parameters must be an object")
        if len(encode(schema)) > 16384:
            raise ValueError("canonical tool schema must remain bounded")
        object.__setattr__(self, "tool", ToolSpec(self.tool.action, self.tool.description, encode(schema)))
        # Use the supervisor's explicit effect and time-budget validation.
        self.definition()

    @property
    def kind(self):
        return "extension.tool." + digest([self.package.extension_id, self.tool.action])

    def definition(self):
        return TaskDefinition(self.kind, "extension.tools", "extension.tool.invoke", Lane.FOREGROUND,
                              version=digest(self), side_effect=self.side_effect, binding=self.binding,
                              timeout_seconds=self.timeout_seconds, max_attempts=1)


@dataclass(frozen=True)
class ExternalToolInvocation:
    invocation_id: str
    parent_task_id: str
    tool_call_id: str
    context: TaskContext
    descriptor: ExtensionToolDescriptor
    arguments_json: str
    deadline_at: datetime
    before_dispatch: Callable[[], object] | None = field(default=None, compare=False, repr=False)

    @property
    def arguments_hash(self):
        return digest(json.loads(self.arguments_json))


@dataclass(frozen=True)
class ExternalToolResponse:
    invocation_id: str
    origin_key: str
    arguments_hash: str
    binding: ProviderBinding
    state: str
    effect: str = "none"
    source: SourceRecord | None = None
    operation_id: str | None = None
    receipt_ref: str | None = None
    images: tuple[CapturedImage, ...] = field(default=(), repr=False)
    dependencies: tuple[SourceRef, ...] = field(default=(), repr=False)
    error_code: str | None = None

    def __post_init__(self):
        for name in ("invocation_id", "origin_key", "arguments_hash"):
            _opaque(getattr(self, name), field=name)
        if not isinstance(self.binding, ProviderBinding) or self.state not in ("succeeded", "failed", "unknown"):
            raise ValueError("external response requires its original binding and explicit outcome")
        TaskResult("uncertain" if self.state == "unknown" else self.state, self.effect,
                   receipt_ref=self.receipt_ref, operation_id=self.operation_id)
        if (self.error_code is not None and (type(self.error_code) is not str
                or re.fullmatch(r"[a-z][a-z0-9_]{0,95}", self.error_code) is None)):
            raise ValueError("external error code must be a bounded stable identifier")
        if self.state == "succeeded":
            if not isinstance(self.source, SourceRecord):
                raise ValueError("successful extension result requires a governed original")
        elif self.source is not None:
            raise ValueError("failed or unknown results cannot supply successful material")
        if self.state == "failed" and self.receipt_ref is None:
            raise ValueError("a known provider failure requires an actual receipt")
        if (not isinstance(self.images, tuple) or any(not isinstance(image, CapturedImage) for image in self.images)
                or self.images and self.state != "succeeded"):
            raise ValueError("image originals require a successful provider response")
        check_manifest(tuple(image.descriptor for image in self.images), allow_empty=True)
        if (not isinstance(self.dependencies, tuple) or len(self.dependencies) > 128
                or any(not isinstance(reference, SourceRef) for reference in self.dependencies)
                or self.dependencies and self.state != "succeeded"):
            raise ValueError("external dependencies require bounded original source references")


@dataclass(frozen=True)
class ExternalToolProvider:
    descriptor: ExtensionToolDescriptor
    invoke: Callable[[ExternalToolInvocation], Awaitable[ExternalToolResponse]]
    current_binding: Callable[[], ProviderBinding | None]
    admit: Callable[[TaskContext, datetime], object] | None = None

    def __post_init__(self):
        if (not isinstance(self.descriptor, ExtensionToolDescriptor) or not inspect.iscoroutinefunction(self.invoke)
                or not callable(self.current_binding) or inspect.iscoroutinefunction(self.current_binding)
                or self.admit is not None and (not callable(self.admit) or inspect.iscoroutinefunction(self.admit))):
            raise ValueError("provider requires a cooperative async call and bounded synchronous binding attestation")


class _SourceAccess:
    """SourceRepository authorization using the same current extension consent."""
    def __init__(self, executor, descriptor, context):
        self.executor, self.descriptor, self.context = executor, descriptor, context

    @outcome
    def authorize(self, resolved, *, scope, capability, observed_at):
        if (resolved != self.context.actor or scope != self.context.scope
                or capability != self.descriptor.binding.capability):
            raise TaskError("external_source_scope_mismatch", "source must retain its exact caller and capability")
        return self.executor._admit(self.descriptor, self.context, observed_at)


class ExternalToolExecutor:
    """Construct before TaskSupervisor; attach after registering its task definitions."""
    def __init__(self, runtime, providers: tuple[ExternalToolProvider, ...]):
        if (not isinstance(providers, tuple) or not providers
                or any(not isinstance(provider, ExternalToolProvider) for provider in providers)):
            raise ValueError("register a bounded tuple of trusted extension providers")
        values = {provider.descriptor.tool.action: provider for provider in providers}
        if len(values) != len(providers):
            raise ValueError("extension tool actions must have one exact owner")
        self.runtime = runtime
        self.providers = MappingProxyType(values)
        self.actions = frozenset(values)
        self.capabilities = CapabilityRepository(runtime)
        self.messages = self.tasks = self.runner = None
        self.registrations = tuple(TaskRegistration(p.descriptor.definition(), self.validate_child) for p in providers)
        # A host registry refresh moves every provider binding at once.  Keep the
        # last observed binding per action so a persistent drift is reported once
        # instead of turning every catalog round into a warning.
        self._rebindings = {}
        self._binding_lock = Lock()

    def attach(self, messages):
        if self.messages is not None or messages.runtime is not self.runtime:
            raise ValueError("extension executor must attach once to its original runtime")
        for entry in self.registrations:
            registered = messages.tasks.registrations.get(entry.definition.kind)
            if registered != entry:
                raise ValueError("supervisor must register the exact extension owner validation")
        self.messages, self.tasks = messages, messages.tasks
        self.runner = ExactTaskRunner(self.tasks, frozenset(e.definition.kind for e in self.registrations), self._invoke)
        return self

    def admit(self, descriptor, context, at):
        """Public admission check for trusted callers outside the model tool loop."""
        return self._admit(descriptor, context, at)

    def resolve(self, descriptor):
        """The declaration a dispatch would use after any host rebinding."""
        return self._provider(descriptor)[1]

    def _provider(self, descriptor):
        """Resolve the trusted owner and the binding that is current right now.

        A host registry refresh changes the package version or credential
        generation recorded in ``ProviderBinding`` while the same provider still
        serves the same capability.  Treating that as a permanent ownership
        change hid every external tool after one reload, so rebind instead.  A
        different provider ID or capability is never adopted.
        """
        provider = self.providers.get(descriptor.tool.action)
        if provider is None or not _same_declaration(provider.descriptor, descriptor):
            raise TaskError("tool_owner_unavailable", "original extension declaration is unavailable")
        try:
            current = provider.current_binding()
        except Exception as error:
            raise TaskError("external_provider_unavailable", "host cannot attest the original provider") from error
        if current is None:
            # Unloaded, disabled or unsupported: the host is not offering the
            # declaration at all, so no binding can be adopted.
            raise TaskError("external_provider_changed", "host provider is no longer loaded")
        if current == descriptor.binding:
            return provider, descriptor
        if (current.provider, current.capability) != (
                descriptor.binding.provider, descriptor.binding.capability):
            raise TaskError("external_provider_changed", "host provider now serves another provider or capability")
        self._note_rebinding(descriptor.tool.action, descriptor.binding, current)
        return provider, replace(descriptor, binding=current)

    def _note_rebinding(self, action, previous, current):
        with self._binding_lock:
            if self._rebindings.get(action) == current:
                return
            self._rebindings[action] = current
            logger.warning(
                "[CompanionV2] 外部工具绑定已变化，重映射到当前 Provider action=%s provider=%s capability=%s "
                "version=%s->%s generation=%s->%s",
                action, current.provider, current.capability,
                previous.version, current.version, previous.generation, current.generation)

    def _admit(self, descriptor, context, at):
        action = descriptor.tool.action
        privacy = action in PRIVACY_ACTIONS
        core = is_core(self.runtime, context.actor.subject) if privacy else False
        # 隐私能力不再按会话范围拦截：群聊和私聊使用同一份授权判定。
        if (context.scope.kind is ScopeKind.GROUP and action not in GROUP_IMAGE_ACTIONS
                and not privacy):
            raise TaskError("tool_not_available", "this external action is not exposed to a group conversation")
        provider, current = self._provider(descriptor)
        if provider.admit is not None:
            require(self.tasks.authorize_context(context, action="conversation.reply", observed_at=at))
            return require(provider.admit(context, at))
        # 核心管理员默认可用：不再要求该会话的 extension.tool.invoke 授权。
        if not core:
            require(self.tasks.authorize_context(context, action="extension.tool.invoke", observed_at=at))
        package = descriptor.package
        installed = require(self.capabilities.installation(extension_id=package.extension_id))
        if (installed.package_id, installed.package_version, installed.artifact_hash) != (
                package.package_id, package.package_version, package.artifact_hash):
            raise TaskError("external_package_changed", "original tool requires its pinned installed package")
        catalog = require(self.capabilities.descriptor(extension_id=package.extension_id,
                          package_id=package.package_id, package_version=package.package_version))
        if catalog != package:
            raise TaskError("external_package_changed", "tool differs from the trusted package catalog")
        require(self.capabilities.require_role_enabled(extension_id=package.extension_id,
                role=context.role_binding.role, bot_id=context.scope.bot_id))
        if core:
            return package
        return require(self.capabilities.authorize(extension_id=package.extension_id,
                       capability_id=current.binding.capability, subject=context.actor.subject, scope=context.scope))

    def catalog_for(self, context):
        return self.catalog_with_diagnostics(context)[0]

    def catalog_with_diagnostics(self, context):
        """Return this audience's offered tools and bounded omission codes from one check."""
        with self.runtime.read_snapshot():
            available = []
            filtered = []
            for provider in self.providers.values():
                action = provider.descriptor.tool.action
                try:
                    self._admit(provider.descriptor, context, self.tasks.now())
                except TaskError as error:
                    reason = ("not_supported_for_group" if context.scope.kind is ScopeKind.GROUP
                              and action not in GROUP_IMAGE_ACTIONS else error.code)
                    if type(reason) is not str or re.fullmatch(r"[a-z][a-z0-9_]{0,95}", reason) is None:
                        reason = "extension_unavailable_or_not_authorized"
                    filtered.append({"action": action, "reason": reason})
                    continue
                available.append(provider.descriptor.tool)
            return tuple(available), tuple(filtered)

    def _row(self, invocation_id):
        row = self.runtime.read_one("SELECT * FROM extension_tool_invocations WHERE invocation_id = ?", (invocation_id,))
        if row is None:
            from .native_tool_results import operation_row
            row = operation_row(self.runtime, invocation_id)
        if row is None:
            raise TaskError("external_invocation_unavailable", "original invocation is unavailable")
        return row

    def _descriptor(self, row):
        provider = self.providers.get(row["action"])
        if provider is None:
            raise TaskError("tool_owner_unavailable", "original extension owner or declaration changed")
        if (encode(provider.descriptor), encode(provider.descriptor.binding)) == (
                row["descriptor_json"], row["provider_json"]):
            return provider.descriptor
        try:
            binding = ProviderBinding(**json.loads(row["provider_json"]))
        except (TypeError, ValueError) as error:
            raise TaskError("tool_owner_unavailable", "original extension owner or declaration changed") from error
        # The ledger keeps the declaration as it was dispatched.  Every field
        # except the binding must still equal the trusted provider's identity;
        # _provider() later rebinds a stale version/generation to the host's
        # current attestation.  Cross-provider or cross-capability rows stay
        # rejected here.
        descriptor = replace(provider.descriptor, binding=binding)
        if (encode(descriptor) != row["descriptor_json"]
                or (binding.provider, binding.capability) != (
                    provider.descriptor.binding.provider, provider.descriptor.binding.capability)):
            raise TaskError("tool_owner_unavailable", "original extension owner or declaration changed")
        return descriptor

    @outcome
    def validate_child(self, spec, at):
        row = self._row(spec.request_ref)
        descriptor = self._descriptor(row)
        if (spec.task_id, spec.kind) != (row["task_id"], descriptor.kind):
            raise TaskError("external_task_mismatch", "child task does not match its original invocation")
        self._admit(descriptor, spec.context, at)
        batch = self.messages.batch(row["batch_id"])
        if batch["origin_key"] != spec.context.key or batch["generation_task_id"] != row["parent_task_id"]:
            raise TaskError("external_task_mismatch", "invocation belongs to a different original conversation")
        parent = require(self.tasks.read(spec.context, row["parent_task_id"]))
        call = self.runtime.read_one("SELECT * FROM conversation_tool_calls WHERE task_id = ? AND tool_call_id = ?",
                                    (row["parent_task_id"], row["tool_call_id"]))
        if call is None:
            call = self._authoring_parent(row, parent)
        if call is None or call["attempt_id"] != parent.attempt_id or call["batch_id"] != row["batch_id"]:
            raise TaskError("external_task_mismatch", "child must retain its actual model request")
        if call["action"].startswith("content.authoring."):
            allowed = ({"content.work.create"} if call["action"] == "content.authoring.create" else
                       {"content.work.list", "content.work.describe", "content.work.read",
                        "content.work.save_candidate"})
            if row["action"] not in allowed:
                raise TaskError("external_task_mismatch", "Content subcall is outside its authoring request")
        elif (call["action"], call["arguments_json"]) != (row["action"], row["arguments_json"]):
            raise TaskError("external_task_mismatch", "child must retain its actual model request")
        require(self.tasks.validate(TaskClaim(parent.task_id, parent.attempt_id, parent.generation)))
        return True

    def _authoring_parent(self, invocation, parent):
        """Resolve a trusted Content subcall to the model's high-level authoring call."""
        if not invocation["tool_call_id"].startswith("content-authoring-subcall:"):
            return None
        try:
            _, step_text, _ = invocation["tool_call_id"].split(":", 2)
            step = int(step_text)
        except (TypeError, ValueError):
            return None
        from companion_v2.conversation.content.authoring_workflow import (
            ACTIONS as AUTHORING_ACTIONS, CONTINUE_ACTION, CREATE_ACTION)
        permitted = ({"content.work.create"} if invocation["action"] == "content.work.create" else
                     {"content.work.list", "content.work.describe", "content.work.read",
                      "content.work.save_candidate"})
        if invocation["action"] not in permitted:
            return None
        rows = self.runtime.read_all("""SELECT * FROM conversation_tool_calls
            WHERE task_id=? AND attempt_id=? AND action IN (?,?)
            ORDER BY sequence""", (invocation["parent_task_id"], parent.attempt_id,
                CREATE_ACTION, CONTINUE_ACTION))
        for row in rows:
            expected = "content-authoring-subcall:" + str(step) + ":" + digest(
                [invocation["parent_task_id"], row["tool_call_id"], step])
            if (row["action"] in AUTHORING_ACTIONS and invocation["tool_call_id"] == expected
                    and (row["state"] in ("started", "succeeded", "unknown"))):
                if (row["action"] == CREATE_ACTION and invocation["action"] != "content.work.create"
                        or row["action"] == CONTINUE_ACTION and invocation["action"] == "content.work.create"):
                    return None
                return row
        return None

    async def execute(self, run, call):
        descriptor = self.providers[call.action].descriptor
        with self.runtime.transaction() as connection:
            run.validate()
            plan = connection.execute("SELECT plan_json FROM model_requests WHERE task_id = ?", (run.task.task_id,)).fetchone()
            if plan is None or call.action not in json.loads(plan["plan_json"]).get("tool_actions", ()):
                raise TaskError("tool_not_available", "extension was not offered in the original model catalog")
            self._admit(descriptor, run.task.spec.context, self.tasks.now())
            _check_arguments(json.loads(descriptor.tool.parameters_json), json.loads(call.arguments_json))
            if call.action in IMAGE_ACTIONS:
                self.messages.generated_images.require_capacity(run.task.spec.request_ref, run.task.spec.context, self.tasks.now())
                self.messages.image_quota.admit(run.task, self.tasks.now())
            unresolved = connection.execute("""SELECT 1 FROM extension_tool_invocations
                WHERE parent_task_id = ? AND action = ? AND arguments_json = ?
                AND state IN ('queued', 'running', 'unknown') LIMIT 1""",
                (run.task.task_id, call.action, call.arguments_json)).fetchone()
            if unresolved:
                raise TaskError("external_effect_unresolved", "an original unknown call cannot be retried under another ID")
            identifier = "extension-" + digest([run.task.task_id, call.call_id])
            child_id = "child-" + identifier
            connection.execute("""INSERT INTO extension_tool_invocations
                (invocation_id, parent_task_id, tool_call_id, batch_id, task_id, action, descriptor_json,
                 provider_json, arguments_json, state, effect, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 'none', ?)""",
                (identifier, run.task.task_id, call.call_id, run.task.spec.request_ref, child_id, call.action,
                 encode(descriptor), encode(descriptor.binding), call.arguments_json, stamp(self.tasks.now())))
            require(self.tasks.submit(TaskSpec(child_id, run.task.spec.context, descriptor.kind, identifier, identifier,
                                               self.tasks.now(), run.task.spec.deadline_at)))
        try:
            result = require(await self.runner.run(run.task.spec.context, child_id)).task
            if self._row(identifier)["state"] == "queued":
                # The exact child never started, so a queue sweep must not dispatch it later.
                self._not_dispatched(identifier, "external_capacity_unavailable" if result.state == "cancelled"
                                     else "external_not_started")
            return self.snapshot(self._row(identifier), run.task.spec.context, self.tasks.now())[0]
        except BaseException:
            self._unknown(identifier)
            raise

    async def execute_internal(self, run, parent_call, model_call_id, action, arguments, *, step):
        """Run a trusted Content operation inside one authorized authoring tool call."""
        from types import SimpleNamespace
        from companion_v2.conversation.content.authoring_workflow import ACTIONS as AUTHORING_ACTIONS

        if (action not in self.actions or not action.startswith("content.work.")
                or type(step) is not int or step < 1 or run.task.spec.context.scope.kind is not ScopeKind.PRIVATE):
            raise TaskError("content_authoring_action", "internal writes require a private native Content action")
        descriptor = self.providers[action].descriptor
        if descriptor.binding.provider != "content.native" or descriptor.source_owner != "content.native":
            raise TaskError("content_provider_changed", "internal authoring requires the original Content owner")
        inner_id = "content-authoring-subcall:" + str(step) + ":" + digest(
            [run.task.task_id, parent_call.call_id, step])
        call = SimpleNamespace(call_id=inner_id, action=action, arguments_json=encode(arguments))
        with self.runtime.transaction() as connection:
            run.validate()
            plan = connection.execute("SELECT plan_json FROM model_requests WHERE task_id=?",
                                      (run.task.task_id,)).fetchone()
            original = connection.execute("""SELECT * FROM conversation_tool_calls
                WHERE task_id=? AND tool_call_id=?""", (run.task.task_id, parent_call.call_id)).fetchone()
            if (plan is None or parent_call.action not in json.loads(plan["plan_json"]).get("tool_actions", ())
                    or original is None or original["model_call_id"] != model_call_id
                    or original["action"] not in AUTHORING_ACTIONS or original["state"] != "started"
                    or original["arguments_json"] != parent_call.arguments_json):
                raise TaskError("content_authoring_request_mismatch", "Content must answer the original authoring request")
            allowed = ({"content.work.create"} if original["action"] == "content.authoring.create" else
                       {"content.work.list", "content.work.describe", "content.work.read",
                        "content.work.save_candidate"})
            if action not in allowed:
                raise TaskError("content_authoring_action", "the authoring request cannot perform this Content action")
            self._admit(descriptor, run.task.spec.context, self.tasks.now())
            _check_arguments(json.loads(descriptor.tool.parameters_json), json.loads(call.arguments_json))
            prior = connection.execute("SELECT * FROM extension_tool_invocations WHERE parent_task_id=? AND tool_call_id=?",
                                       (run.task.task_id, inner_id)).fetchone()
            if prior is not None:
                if (prior["action"], prior["arguments_json"]) != (action, call.arguments_json):
                    raise TaskError("tool_call_conflict", "an original Content subcall cannot change arguments")
                if prior["state"] not in ("succeeded", "failed"):
                    raise TaskError("external_effect_unresolved", "an unknown Content subcall cannot be replayed")
                return self.snapshot(prior, run.task.spec.context, self.tasks.now())
            unresolved = connection.execute("""SELECT 1 FROM extension_tool_invocations
                WHERE parent_task_id=? AND action=? AND arguments_json=?
                AND state IN ('queued','running','unknown') LIMIT 1""",
                (run.task.task_id, action, call.arguments_json)).fetchone()
            if unresolved:
                raise TaskError("external_effect_unresolved", "an original unknown Content action cannot be repeated")
            identifier = "extension-" + digest([run.task.task_id, inner_id])
            child_id = "child-" + identifier
            connection.execute("""INSERT INTO extension_tool_invocations
                (invocation_id,parent_task_id,tool_call_id,batch_id,task_id,action,descriptor_json,
                 provider_json,arguments_json,state,effect,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,'queued','none',?)""",
                (identifier, run.task.task_id, inner_id, run.task.spec.request_ref, child_id, action,
                 encode(descriptor), encode(descriptor.binding), call.arguments_json, stamp(self.tasks.now())))
            require(self.tasks.submit(TaskSpec(child_id, run.task.spec.context, descriptor.kind, identifier,
                identifier, self.tasks.now(), run.task.spec.deadline_at)))
        try:
            result = require(await self.runner.run(run.task.spec.context, child_id)).task
            if self._row(identifier)["state"] == "queued":
                # The exact child never started, so a queue sweep must not dispatch it later.
                self._not_dispatched(identifier, "external_capacity_unavailable" if result.state == "cancelled"
                                     else "external_not_started")
            return self.snapshot(self._row(identifier), run.task.spec.context, self.tasks.now())
        except BaseException:
            self._unknown(identifier)
            raise

    def _not_dispatched(self, identifier, code):
        with self.runtime.transaction() as connection:
            row = self._row(identifier)
            effect = "none" if self._descriptor(row).side_effect is SideEffect.NONE else "not_committed"
            connection.execute("""UPDATE extension_tool_invocations SET state = 'failed', effect = ?,
                error_code = ?, finished_at = ? WHERE invocation_id = ? AND state = 'queued'""",
                (effect, code, stamp(self.tasks.now()), identifier))

    def _unknown(self, identifier):
        with self.runtime.transaction() as connection:
            connection.execute("""UPDATE extension_tool_invocations SET state = 'unknown', error_code = 'external_result_unknown',
                finished_at = ? WHERE invocation_id = ? AND state IN ('queued', 'running')""", (stamp(self.tasks.now()), identifier))

    async def _invoke(self, run):
        with self.runtime.transaction() as connection:
            run.validate()
            row = self._row(run.task.spec.request_ref)
            descriptor = self._descriptor(row)
            if row["state"] != "queued":
                raise TaskError("external_already_dispatched", "original invocation cannot be dispatched twice")
            effect = "none"
            if descriptor.side_effect is not SideEffect.NONE:
                run.begin_effect()
                effect = "unknown"
            connection.execute("UPDATE extension_tool_invocations SET state = 'running', effect = ? WHERE invocation_id = ?",
                               (effect, row["invocation_id"]))
            def before_dispatch():
                run.validate()
                self._admit(descriptor, run.task.spec.context, self.tasks.now())

            invocation = ExternalToolInvocation(row["invocation_id"], row["parent_task_id"], row["tool_call_id"],
                run.task.spec.context, descriptor, row["arguments_json"], run.task.spec.deadline_at, before_dispatch)
        try:
            provider, dispatch_descriptor = self._provider(descriptor)
            if dispatch_descriptor.binding != descriptor.binding:
                # The original ledger row stays immutable; only this dispatch
                # adopts the host's current binding.
                invocation = replace(invocation, descriptor=dispatch_descriptor)
            response = await provider.invoke(invocation)
            self._record_response(invocation, response)
        except asyncio.CancelledError:
            self._unknown(invocation.invocation_id)
            raise
        except Exception:
            self._unknown(invocation.invocation_id)
        return self._result(self._row(invocation.invocation_id))

    def _record_response(self, invocation, response):
        if (not isinstance(response, ExternalToolResponse) or
                (response.invocation_id, response.origin_key, response.arguments_hash, response.binding) !=
                (invocation.invocation_id, invocation.context.key, invocation.arguments_hash, invocation.descriptor.binding)):
            raise TaskError("external_receipt_mismatch", "provider receipt does not match the original invocation")
        effectful = invocation.descriptor.side_effect is not SideEffect.NONE
        if (not effectful and (response.effect != "none" or response.operation_id is not None)
                or effectful and response.effect != {"succeeded": "committed", "failed": "not_committed", "unknown": "unknown"}[response.state]):
            raise TaskError("external_receipt_mismatch", "provider outcome differs from its declared effect contract")
        source = response.source
        if source is not None and (source.reference.owner != invocation.descriptor.source_owner
                or source.audience != invocation.context.audience or source.reference.scope != invocation.context.scope):
            raise TaskError("external_source_scope_mismatch", "result must retain its registered owner and exact audience")
        if any(reference.scope != invocation.context.scope for reference in response.dependencies):
            raise TaskError("external_source_scope_mismatch", "dependency handles must retain the original context")
        with self.runtime.transaction() as connection:
            row = self._row(invocation.invocation_id)
            if row["state"] not in ("running", "unknown"):
                raise TaskError("external_receipt_conflict", "original outcome is already settled")
            adoption_error = None
            if source is not None:
                size = len(encode(source.payload).encode("utf-8"))
                summary_size = len(encode({"invocation_id": invocation.invocation_id, "state": response.state,
                    "effect": response.effect, "operation_id": response.operation_id, "receipt_ref": response.receipt_ref,
                    "code": None}).encode("utf-8"))
                prior = connection.execute("""SELECT COALESCE(SUM(length(CAST(result_json AS BLOB))), 0)
                    FROM conversation_tool_calls WHERE batch_id = ?""", (row["batch_id"],)).fetchone()[0]
                # External bodies live only in sources. Count earlier originals as well.
                prior += self.material_bytes(row["batch_id"])
                if size + summary_size > MAX_RESULT_BYTES or prior + size + summary_size > MAX_TURN_RESULT_BYTES:
                    raise TaskError("tool_result_too_large", "extension original exceeds the material budget")
                try:
                    with self.runtime.transaction():
                        for reference in response.dependencies:
                            for recipient in invocation.context.audience.recipients:
                                require(self.messages.sources.validate_reference_for_audience(
                                    reference, recipient=recipient, observed_at=self.tasks.now()))
                        require(self.messages.sources.publish(source, observed_at=self.tasks.now()))
                        from companion_v2.sources.dependencies import link
                        link(self.runtime, source.reference, response.dependencies, self.tasks.now())
                except TaskError as error:
                    # A confirmed operation remains confirmed when its original
                    # inputs were withdrawn before material publication.
                    if not response.dependencies or not error.code.startswith(("source_", "content_")):
                        raise
                    source = replace(source, state=GovernanceState.REVOKED)
                    require(self.messages.sources.publish(source, observed_at=self.tasks.now()))
                if invocation.descriptor.tool.action in (*IMAGE_ACTIONS, SCREEN_ACTION) and source.state is GovernanceState.REVOKED:
                    adoption_error = "source_revoked"
                    try:
                        metadata = json.loads(source.payload)
                        status = metadata.get("image_adoption", metadata.get("adoption", {}))
                        if (isinstance(status, dict) and status.get("state") == "unavailable"
                                and isinstance(status.get("code"), str) and 0 < len(status["code"]) <= 128):
                            adoption_error = status["code"]
                    except (ValueError, TypeError, AttributeError):
                        pass
                else:
                    try:
                        if response.images:
                            self._admit(invocation.descriptor, invocation.context, self.tasks.now())
                    except TaskError as error:
                        adoption_error = error.code
                    if adoption_error is None:
                        try:
                            # The image owner uses a savepoint. A governance
                            # rejection rolls back adoption, not the real receipt.
                            self.messages.generated_images.accept(invocation, response)
                        except TaskError as error:
                            if not response.images or error.code not in _ADOPTION_WITHDRAWN:
                                raise
                            adoption_error = error.code
                    if adoption_error is not None:
                        source = require(self.messages.sources.revoke(source.reference,
                            expected_governance_revision=source.governance_revision, observed_at=self.tasks.now()))
            # Account for actual receipts even if cancellation/revocation arrived during I/O.
            # Exposure still requires the current permission and a successful child task.
            connection.execute("""UPDATE extension_tool_invocations SET state = ?, effect = ?, operation_id = ?,
                receipt_ref = ?, result_source_json = ?, result_hash = ?, error_code = ?, finished_at = ?
                WHERE invocation_id = ?""", (response.state, response.effect, response.operation_id, response.receipt_ref,
                encode(source.reference) if source else None, digest(source) if source else None,
                (SCREEN_REJECTED_ADOPTION if invocation.descriptor.tool.action == SCREEN_ACTION
                 else REJECTED_ADOPTION) + adoption_error if adoption_error is not None else
                None if response.state == "succeeded" else response.error_code or
                ("external_not_started" if response.state == "failed" else "external_result_unknown"),
                stamp(self.tasks.now()), invocation.invocation_id))

    def material_bytes(self, batch_id):
        rows = self.runtime.read_all("""SELECT s.payload_json
            FROM extension_tool_invocations i JOIN sources s
            ON s.source_id = json_extract(i.result_source_json, '$.source_id')
            AND s.lineage_id = json_extract(i.result_source_json, '$.version.lineage_id')
            AND s.source_revision = json_extract(i.result_source_json, '$.version.revision')
            WHERE i.batch_id = ?""", (batch_id,))
        return sum(len(encode(json.loads(row["payload_json"])).encode("utf-8")) for row in rows)

    @staticmethod
    def _result(row):
        return TaskResult("uncertain" if row["state"] in ("running", "unknown") else row["state"], row["effect"],
                          result_ref=row["invocation_id"] if row["state"] == "succeeded" else None,
                          receipt_ref=row["receipt_ref"], operation_id=row["operation_id"])

    def snapshot(self, row, context, at):
        if isinstance(row, dict) and row.get("execution_path") == "astrbot_core":
            from .native_tool_results import snapshot
            return snapshot(self, row, context, at)
        descriptor = self._descriptor(row)
        self._admit(descriptor, context, at)
        batch = self.messages.batch(row["batch_id"])
        if batch["origin_key"] != context.key or batch["generation_task_id"] != row["parent_task_id"]:
            raise TaskError("external_invocation_unavailable", "original invocation belongs to another conversation")
        task = require(self.tasks.read(context, row["task_id"]))
        if (task.spec.kind, task.spec.request_ref) != (descriptor.kind, row["invocation_id"]):
            raise TaskError("external_task_mismatch", "original child does not belong to this invocation")
        state = row["state"] if row["state"] in ("succeeded", "failed") else "unknown"
        if state == "succeeded" and (task.state, task.effect, task.operation_id, task.result_ref) != (
                "succeeded", row["effect"], row["operation_id"], row["invocation_id"]):
            raise TaskError("external_task_unresolved", "original child has not accepted its final receipt")
        if state == "failed" and row["receipt_ref"] is not None and task.state != "failed":
            raise TaskError("external_task_unresolved", "original child has not accepted the owner's failure receipt")
        result = {"invocation_id": row["invocation_id"], "state": state, "effect": row["effect"],
                  "operation_id": row["operation_id"], "receipt_ref": row["receipt_ref"], "code": row["error_code"]}
        material = None
        if state == "succeeded":
            adoption = self.messages.generated_images.adoption_status(row, context, at)
            if adoption is not None:
                result["image_adoption"] = adoption
            if self.messages.generated_images.rejected(row) is not None:
                # This is receipt-only metadata. The rejected provider original
                # remains permanently revoked, even if authorization is restored.
                reference = source_from_document(json.loads(row["result_source_json"]))
                stored = self.runtime.read_one("""SELECT state FROM sources WHERE source_id = ?
                    AND lineage_id = ? AND source_revision = ?""",
                    (reference.source_id, reference.version.lineage_id, reference.version.revision))
                if stored is None or stored["state"] != GovernanceState.REVOKED.value:
                    raise TaskError("generated_image_changed", "rejected adoption must remain unavailable")
                return result, None
            if row["action"] == SCREEN_ACTION:
                if self.messages.screen_images.rejected(row) is not None:
                    reference = source_from_document(json.loads(row["result_source_json"]))
                    stored = self.runtime.read_one("""SELECT state FROM sources WHERE source_id = ?
                        AND lineage_id = ? AND source_revision = ?""",
                        (reference.source_id, reference.version.lineage_id, reference.version.revision))
                    if stored is None or stored["state"] != GovernanceState.REVOKED.value:
                        raise TaskError("screen_original_changed", "rejected Screen material must remain unavailable")
                    return result, None
                capture_id = "screen-capture-" + digest(row["invocation_id"])
                capture = require(self.messages.screen_images.screens.published_reference(
                    capture_id, scope=context.scope))
                checked = self.messages.screen_images.screens.read_capture(
                    capture, resolved=context.actor, observed_at=at)
                if not checked.is_success:
                    if checked.failure.code in {"source_revoked", "source_expired", "asset_revoked"}:
                        return result, None
                    raise TaskError(checked.failure.code, checked.failure.reason)
            reference = source_from_document(json.loads(row["result_source_json"]))
            require(self.messages.sources.validate_reference(reference, observed_at=at))
            source = require(self.messages.sources.read(reference, resolved=context.actor, capability=descriptor.binding.capability,
                             observed_at=at, authorizer=_SourceAccess(self, descriptor, context)))
            if digest(source) != row["result_hash"] or source.audience != context.audience:
                raise TaskError("tool_result_changed", "extension original has changed")
            material = PromptMaterial(reference.owner, "tool_source:" + row["tool_call_id"], source.payload, reference)
        return result, material

    @outcome
    def reconcile(self, context, invocation_id):
        """Trusted receipt-only recovery after quiescence; never invoke, retry or generate."""
        with self.runtime.transaction():
            row = self._row(invocation_id)
            descriptor = self._descriptor(row)
            self._admit(descriptor, context, self.tasks.now())
            if self.messages.batch(row["batch_id"])["origin_key"] != context.key:
                raise TaskError("external_invocation_unavailable", "receipt belongs to another conversation")
            task = require(self.tasks.read(context, row["task_id"]))
            if (task.spec.kind, task.spec.request_ref) != (descriptor.kind, invocation_id):
                raise TaskError("external_task_mismatch", "receipt belongs to another child task")
            if row["state"] not in ("succeeded", "failed") or row["receipt_ref"] is None:
                raise TaskError("external_task_unresolved", "no original provider receipt confirms this outcome")
            result = self._result(row)
            if task.state == result.status:
                return task
            return require(self.tasks.reconcile(context, task.task_id, expected_revision=task.revision, result=result))

    def validate_result(self, call_row, context, at):
        row = self.runtime.read_one("SELECT * FROM extension_tool_invocations WHERE parent_task_id = ? AND tool_call_id = ?",
                                    (call_row["task_id"], call_row["tool_call_id"]))
        if row is None:
            return None
        if (row["action"], row["arguments_json"], row["batch_id"]) != (
                call_row["action"], call_row["arguments_json"], call_row["batch_id"]):
            raise TaskError("external_invocation_mismatch", "tool ledger differs from the original child")
        result, material = self.snapshot(row, context, at)
        if call_row["state"] in ("succeeded", "failed") and encode(result) != call_row["result_json"]:
            raise TaskError("external_receipt_conflict", "tool ledger differs from its original receipt")
        return result, material

    async def close(self):
        return await self.runner.close() if self.runner is not None else ()
