"""Map trusted AstrBot catalog declarations to host-neutral supervised tools.

The production host supplies this port after authenticating/loading the package.
An arbitrary function-manager entry is not sufficient permission to expose a tool.
Identity, role and routing come exclusively from invocation.context, never arguments.
"""

from dataclasses import dataclass, replace
import json
import inspect
from typing import Protocol

from companion_v2.conversation.external_tools import (
    ExtensionToolDescriptor, ExternalToolExecutor, ExternalToolInvocation, ExternalToolProvider, ExternalToolResponse,
)
from companion_v2.execution.models import TaskError, digest, encode
from companion_v2.foundation.refs import SourceRef, VersionRef
from companion_v2.identity_policy.models import _opaque
from companion_v2.sources.models import SourceRecord
from companion_v2.qzone.capability import COMMENT_ACTION, LIKE_ACTION, PUBLISH_ACTION, READ_ACTION, tool_descriptors


@dataclass(frozen=True)
class AstrBotToolDeclaration:
    host_tool_id: str
    descriptor: ExtensionToolDescriptor

    def __post_init__(self):
        _opaque(self.host_tool_id, field="host tool ID")
        if not isinstance(self.descriptor, ExtensionToolDescriptor):
            raise ValueError("host tool requires an attested extension descriptor")


class AstrBotToolPort(Protocol):
    def declarations(self) -> tuple[AstrBotToolDeclaration, ...]:
        """Return bounded, trusted, fully loaded cooperative providers."""

    def current_declaration(self, host_tool_id: str) -> AstrBotToolDeclaration | None:
        """Bounded synchronous check; unloading or replacing a provider changes this result."""

    async def invoke(self, host_tool_id: str, invocation: ExternalToolInvocation) -> ExternalToolResponse:
        """Use the explicit authenticated context and retain original effect receipts."""


class CompositeAstrBotToolPort:
    """Expose several independently attested tool ports through one catalog.

    The core receives one executor, while each provider keeps its own declaration,
    binding and lifecycle.  A missing optional provider therefore contributes no
    declaration at all and cannot create a half-installed tool.
    This installation directory is independent of any one model request's budget.
    """

    def __init__(self, *ports):
        self.ports = tuple(port for port in ports if port is not None)
        declarations = tuple(item for port in self.ports for item in port.declarations())
        if len({item.host_tool_id for item in declarations}) != len(declarations):
            raise ValueError("composed host tool declarations must have unique identities")
        self._declarations = declarations

    def declarations(self):
        return self._declarations

    def current_declaration(self, host_tool_id):
        for port in self.ports:
            current = port.current_declaration(host_tool_id)
            if current is not None:
                return current
        return None

    def provider_admission(self, host_tool_id):
        for port in self.ports:
            if any(item.host_tool_id == host_tool_id for item in port.declarations()):
                factory = getattr(port, "provider_admission", None)
                return factory(host_tool_id) if callable(factory) else None
        return None

    async def invoke(self, host_tool_id, invocation):
        for port in self.ports:
            if any(item.host_tool_id == host_tool_id for item in port.declarations()):
                return await port.invoke(host_tool_id, invocation)
        raise RuntimeError("composed host tool declaration is unavailable")

    def attach(self, application):
        for port in self.ports:
            attach = getattr(port, "attach", None)
            if callable(attach):
                attach(application)
        return self


class QzoneToolPort:
    """Adapt the included QQ Zone service to the supervised model tool contract."""

    _ACTIONS = frozenset((READ_ACTION, LIKE_ACTION, COMMENT_ACTION, PUBLISH_ACTION))

    def __init__(self, transport):
        if transport is None:
            raise ValueError("QQ Zone tool port requires a host transport")
        self.transport = transport
        self.application = None
        self._declarations = tuple(
            AstrBotToolDeclaration("qzone:" + descriptor.tool.action, descriptor)
            for descriptor in tool_descriptors()
        )

    def declarations(self):
        return self._declarations

    def current_declaration(self, host_tool_id):
        if self.application is None or getattr(self.application, "qzone", None) is None:
            return None
        for declaration in self._declarations:
            if declaration.host_tool_id == host_tool_id:
                service = self.application.qzone
                return declaration if getattr(service, "transport", None) is self.transport else None
        return None

    def attach(self, application):
        service = getattr(application, "qzone", None)
        if service is None or getattr(service, "transport", None) is not self.transport:
            raise ValueError("QQ Zone service must be assembled from the original host transport")
        if self.application is not None and self.application is not application:
            raise ValueError("QQ Zone tool port can attach only once")
        self.application = application
        return self

    @staticmethod
    def _receipt(invocation, code):
        return "qzone-receipt:" + digest([invocation.invocation_id, code])

    @staticmethod
    def _source(invocation, result, observed_at):
        payload = encode(result)
        reference = SourceRef("qzone:" + invocation.invocation_id, "qzone.feed", invocation.context.scope,
                              VersionRef(invocation.invocation_id, 1), observed_at)
        return SourceRecord(reference, invocation.context.audience, digest(payload), payload)

    async def invoke(self, host_tool_id, invocation):
        declaration = next((item for item in self._declarations if item.host_tool_id == host_tool_id), None)
        if declaration is None or declaration.descriptor != invocation.descriptor:
            raise TaskError("qzone_provider_changed", "the original QQ Zone declaration is unavailable")
        service = getattr(self.application, "qzone", None)
        if service is None:
            raise TaskError("qzone_provider_unavailable", "QQ Zone is not assembled for this host")
        arguments = json.loads(invocation.arguments_json)
        action = declaration.descriptor.tool.action
        operation_key = invocation.invocation_id
        try:
            if action == READ_ACTION:
                result = await service.scan(invocation.context, scope=arguments.get("scope", "friends"),
                                            limit=arguments.get("limit"), detail=arguments.get("detail", False))
            elif action == LIKE_ACTION:
                result = await service.like(invocation.context, post_ref=arguments["post_ref"],
                                            operation_key=operation_key)
            elif action == COMMENT_ACTION:
                result = await service.comment(invocation.context, post_ref=arguments["post_ref"],
                                               text=arguments["text"], operation_key=operation_key)
            elif action == PUBLISH_ACTION:
                # The model proposes the topic; whether that proposal may go out
                # without a human is decided by the configured gates, so it never
                # confirms itself here.
                result = await service.publish(invocation.context, text=arguments["text"],
                                              topic=arguments["topic"], scene=arguments["scene"],
                                              confidence=arguments["confidence"], topic_confirmed=False,
                                              source_refs=arguments.get("source_refs", ()),
                                              agenda_proposal=arguments.get("agenda_proposal"),
                                              operation_key=operation_key)
            else:
                raise TaskError("qzone_action_unknown", "unknown QQ Zone action")
        except TaskError as error:
            if error.code == "qzone_delivery_unknown":
                return ExternalToolResponse(invocation.invocation_id, invocation.context.key,
                    invocation.arguments_hash, declaration.descriptor.binding, "unknown", effect="unknown")
            return ExternalToolResponse(invocation.invocation_id, invocation.context.key,
                invocation.arguments_hash, declaration.descriptor.binding, "failed", effect="not_committed",
                receipt_ref=self._receipt(invocation, error.code))
        observed_at = service.now()
        effectful = action in (LIKE_ACTION, COMMENT_ACTION, PUBLISH_ACTION)
        operation_id = result.get("operation_id") if effectful else None
        receipt_ref = result.get("receipt_ref") if effectful else None
        if effectful and (not operation_id or not receipt_ref):
            raise TaskError("qzone_receipt_missing", "QQ Zone action did not return its committed receipt")
        return ExternalToolResponse(invocation.invocation_id, invocation.context.key,
            invocation.arguments_hash, declaration.descriptor.binding, "succeeded",
            effect="committed" if effectful else "none", source=self._source(invocation, result, observed_at),
            operation_id=operation_id, receipt_ref=receipt_ref)


def _same_declaration(declaration, current):
    """The host may rebind the same tool after a provider registry refresh.

    Only the version/generation part of ``ProviderBinding`` is allowed to move;
    every other declared field, including provider ID and capability, still has
    to match the original declaration.
    """
    if current is None:
        return False
    if current == declaration:
        return True
    original, replacement = declaration.descriptor.binding, current.descriptor.binding
    return ((original.provider, original.capability) == (replacement.provider, replacement.capability)
            and replace(current.descriptor, binding=original) == declaration.descriptor)


def map_tools(runtime, port: AstrBotToolPort) -> ExternalToolExecutor:
    if (not inspect.iscoroutinefunction(port.invoke) or not callable(port.declarations)
            or not callable(port.current_declaration) or inspect.iscoroutinefunction(port.declarations)
            or inspect.iscoroutinefunction(port.current_declaration)):
        raise ValueError("host port requires synchronous attestations and cooperative asynchronous invocation")
    declarations = port.declarations()
    if (not isinstance(declarations, tuple) or not declarations
            or any(not isinstance(item, AstrBotToolDeclaration) for item in declarations)
            or len({item.host_tool_id for item in declarations}) != len(declarations)):
        raise ValueError("host declarations require bounded unique trusted tool identities")
    providers = []
    for declaration in declarations:
        def current(declaration=declaration):
            replacement = port.current_declaration(declaration.host_tool_id)
            return replacement.descriptor.binding if _same_declaration(declaration, replacement) else None

        async def invoke(invocation, declaration=declaration):
            if invocation.before_dispatch is not None:
                invocation.before_dispatch()
            if not _same_declaration(declaration, port.current_declaration(declaration.host_tool_id)):
                # The core has already crossed its dispatch barrier; an exception remains unknown.
                raise RuntimeError("original host declaration is no longer loaded")
            return await port.invoke(declaration.host_tool_id, invocation)

        admission_factory = getattr(port, "provider_admission", None)
        admit = admission_factory(declaration.host_tool_id) if callable(admission_factory) else None
        providers.append(ExternalToolProvider(declaration.descriptor, invoke, current, admit))
    return ExternalToolExecutor(runtime, tuple(providers))
