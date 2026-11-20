"""Optional V2 context for the exact private window that opened a room."""

import asyncio
from dataclasses import dataclass
import json
import logging
from types import SimpleNamespace
from uuid import uuid4

from companion_v2.execution.models import encode, require
from companion_v2.together.provider import scope_key
from astrbot_host.core_reply_context import build_core_reply_prompt
from astrbot_host.mobile_together import MobileTogetherBridge


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoomContext:
    session: object
    application: object
    context: object


def current_room_context(runtime, room):
    """Local authorization, repeated after awaited host lookups and before writes."""
    controller = runtime.controller
    registry = getattr(controller, "native_extensions", None)
    binding = room.native_v2
    if (binding is None or room.integration_closed or registry is None
            or registry.active_service("together") is not runtime
            or any(member.role == "guest" for member in runtime._ensure_room_members(room).values())):
        return None
    resolved = MobileTogetherBridge(controller)._target_context(room.user_id)
    if resolved is None:
        return None
    session, application, context = resolved
    scope = context.scope
    if (registry.application is not application or scope.kind.value != "private"
            or (scope.bot_id, scope.account_id, scope.platform, scope.session_id, scope.kind.value)
               != (binding.bot_id, binding.account_id, binding.platform, binding.session_id, binding.scope_kind)
            or scope_key(json.loads(encode(context))) != binding.scope_key):
        return None
    if MobileTogetherBridge(controller)._admitted(registry, application, context, "together.room.create") is None:
        return None
    return RoomContext(session, application, context)


async def resolve_room_context(runtime, room):
    """A stale room may keep chatting, but cannot read or write another window."""
    try:
        resolved = current_room_context(runtime, room)
        if resolved is None:
            return None
        registry = runtime.controller.native_extensions
        persona = await registry.persona_guard(resolved.context.scope, resolved.context.role_binding)
        binding = room.native_v2
        if (not isinstance(persona, dict) or any(persona.get(key) != getattr(binding, key)
                for key in ("persona_id", "prompt", "bot_id", "account_id"))):
            return None
        current = current_room_context(runtime, room)
        return current if current == resolved else None
    except Exception as error:
        logger.debug("[TogetherCompanion] V2 房间背景暂不可用: %s", type(error).__name__)
        return None


class _WithoutMemory:
    def __init__(self, owner):
        self.owner = owner
        self.memory_context = None

    def __getattr__(self, key):
        return getattr(self.owner, key)


class _ApplicationView:
    def __init__(self, application):
        self.owner = application
        self.messages = _WithoutMemory(application.messages)

    def __getattr__(self, key):
        return getattr(self.owner, key)


async def room_background(runtime, room, query):
    resolved = await resolve_room_context(runtime, room)
    if resolved is None:
        return ""
    application = resolved.application
    if not runtime.enable_memory_context:
        application = _ApplicationView(application)
    native_input = SimpleNamespace(text=query, event_id="together-background-" + uuid4().hex)
    prompt = await asyncio.to_thread(
        build_core_reply_prompt, application, resolved.context, native_input, at=runtime.controller.now()
    )
    return prompt.instruction


async def room_work_context(runtime, room):
    resolved = await resolve_room_context(runtime, room)
    if resolved is None:
        return {}
    repository = getattr(resolved.application, "screen_repository", None)
    if repository is None:
        return {}
    at = runtime.controller.now()
    try:
        materials = require(await asyncio.to_thread(
            repository.latest_analysis, scope=resolved.context.scope,
            resolved=resolved.context.actor, observed_at=at
        ))
        if not materials:
            return {"available": True, "context_available": False}
        item = materials[0]
        raw = item.analysis.interpretation
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            value = raw
        value = value if isinstance(value, dict) else {"summary": str(value)}
        captured = item.material.capture.captured_at
        return {"available": True, "context_available": True, "tracking_enabled": False,
            "captured_at": captured.timestamp(),
            "current": {"scene": value.get("scene", ""), "resource_label": value.get("resource_label", "")},
            "observation": {"summary": value.get("summary") or value.get("description") or raw,
                "scene": value.get("scene", ""), "observed_at": captured.timestamp(),
                "age_seconds": max(0, int((at - captured).total_seconds()))}}
    except Exception as error:
        logger.debug("[TogetherCompanion] V2 已授权屏幕分析暂不可用: %s", type(error).__name__)
        return {}


async def existing_host_conversation(runtime, room):
    """Resolve only the existing native conversation; never guess or create one."""
    try:
        return await _existing_host_conversation(runtime, room)
    except Exception as error:
        logger.debug("[TogetherCompanion] V2 现有会话暂不可用: %s", type(error).__name__)
        return None, "", ""


async def _existing_host_conversation(runtime, room):
    resolved = await resolve_room_context(runtime, room)
    if resolved is None:
        return None, "", ""
    bridge, context = resolved.session.bridge, resolved.context
    account = bridge.account(context.scope)
    origin = bridge._umo(account.platform_id, context.scope.native_scope_id)
    await bridge.verify_role(context.scope, context.role_binding, room.native_v2.prompt)
    identifier, _, _, _ = await bridge._selected_persona(account, origin)
    if not identifier or current_room_context(runtime, room) != resolved:
        return None, "", ""
    manager = runtime.context.conversation_manager
    conversation = await manager.get_conversation(origin, identifier, create_if_not_exists=False)
    if conversation is None or current_room_context(runtime, room) != resolved:
        return None, "", ""
    await bridge.verify_role(context.scope, context.role_binding, room.native_v2.prompt)
    if current_room_context(runtime, room) != resolved:
        return None, "", ""
    return manager, origin, str(identifier)
