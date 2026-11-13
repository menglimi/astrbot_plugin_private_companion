"""Trusted AstrBot port composition, without loading or importing the real host."""

from __future__ import annotations

from dataclasses import replace
import json
from typing import Protocol

from companion_v2.capabilities.configuration import CapabilityConfigurationOwner
from companion_v2.conversation.messages import event_from_document
from companion_v2.execution.models import TaskContext, require
from companion_v2.execution.service import outcome
from companion_v2.host.application import CompanionApplication
from companion_v2.identity_policy.personas import PersonaDirectory

from .inbound import AstrBotInboundAdapter
from .tools import CompositeAstrBotToolPort, QzoneToolPort, map_tools


class AstrBotRolePort(Protocol):
    def role_instruction(self, host_event: object, context: TaskContext) -> str:
        """Capture the trusted original persona prompt for this exact host route."""


class AstrBotCompanionHost:
    def __init__(self, runtime, *, event_port, identity_port, role_port, runtime_identity,
                 tool_port=None, memory_owner=None, memory_push=None, configuration_store=None,
                 **application_options):
        # Native providers and the included QQ Zone provider share one supervised
        # executor.  The latter is optional and is absent when the host supplied no
        # transport, so a missing account never creates model-visible declarations.
        qzone_transport = application_options.get("qzone_transport")
        qzone_port = QzoneToolPort(qzone_transport) if qzone_transport is not None else None
        if qzone_port is not None:
            tool_port = CompositeAstrBotToolPort(tool_port, qzone_port)
        declarations = tool_port.declarations() if tool_port is not None else ()
        external = map_tools(runtime, tool_port) if declarations else None
        # Every declared extension keeps its own scope preferences (proactive sharing
        # and thought weight) on the shared configuration entry point.
        internal = getattr(tool_port, "provider_admission", None)
        owners = tuple(CapabilityConfigurationOwner(runtime, extension_id=extension_id)
                       for extension_id in sorted({item.descriptor.package.extension_id for item in declarations
                           if not callable(internal) or internal(item.host_tool_id) is None}))
        # 记忆归属由组装方注入（真实 AstrBot 装配用 ``memory_owner_provider(context)``
        # 读别家的声明，测试可以注入一个假的）；缺省是 V2 自己。判定只有一个来源，
        # 不在核心和适配层各写一遍。写入端口同理：它把已经完成的日记、梦境与主动
        # 消息推给当前对外表达记忆的一方（``memory_push_provider(context)``），
        # 缺省是空端口——没接线的宿主不会因为多了这条旁路而少做任何事。
        self.application = CompanionApplication(runtime, external=external,
                                                runtime_identity=runtime_identity,
                                                capability_owners=owners,
                                                memory_owner=memory_owner,
                                                memory_push=memory_push,
                                                configuration_store=configuration_store,
                                                **application_options)
        self.application.native_tool_port = tool_port
        if tool_port is not None and callable(getattr(tool_port, "attach", None)):
            tool_port.attach(self.application)
        self.event_port, self.role_port = event_port, role_port
        self.inbound = AstrBotInboundAdapter(event_port=event_port, identity_port=identity_port,
            identities=self.application.tasks.identity, personas=PersonaDirectory(runtime),
            runtime_identity=runtime_identity, sources=self.application.messages.sources)

    @outcome
    def receive(self, host_event, *, capability="text", on_admitted=None):
        app = self.application
        app.require_running()
        # Route/source publication and durable batch admission succeed or roll back together.
        with app.runtime.transaction():
            event = require(self.inbound.ingest(host_event, capability=capability))
            actor = require(app.tasks.identity.resolve_authenticated(event.principal, observed_at=event.received_at))
            context = TaskContext(actor, event.runtime_scope.persona, self.event_port.audience(host_event))
            prompt = self.role_port.role_instruction(host_event, context)
            previous = app.runtime.read_one("SELECT event_json FROM conversation_inputs WHERE event_key = ?", (event.scoped_key,))
            if previous:
                original = event_from_document(json.loads(previous["event_json"]), context)
                if original.runtime_scope is not None:
                    captured = replace(event, runtime_scope=replace(event.runtime_scope,
                        runtime_instance_id=original.runtime_scope.runtime_instance_id))
                    if captured == original:
                        event = original  # A host restart does not restamp an otherwise identical input.
            batch_id = require(app.accept(context, event, role_instruction=prompt))
            if getattr(event, "images", ()):
                app.messages.images.publish(context, event, self.event_port.images(host_event))
            if getattr(event, "quotes", ()):
                app.messages.quotes.publish(context, event, self.event_port.quotes(host_event))
            if getattr(event, "forwards", ()):
                app.messages.forwards.publish(context, event, self.event_port.forwards(host_event))
            if getattr(event, "audios", ()):
                app.messages.audio.publish(context, event, self.event_port.audios(host_event))
            if getattr(event, "videos", ()):
                app.messages.videos.publish(context, event, self.event_port.videos(host_event))
            if on_admitted is not None:
                # Trusted synchronous host policy is committed with the original input.
                on_admitted(context, batch_id, replayed=previous is not None)
            return batch_id
