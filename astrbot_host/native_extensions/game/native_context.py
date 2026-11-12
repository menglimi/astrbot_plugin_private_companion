"""Read V2 background for the exact private window that opened a game room."""

from dataclasses import dataclass
import logging
from types import SimpleNamespace
from uuid import uuid4

from astrbot_host.core_reply_context import build_core_reply_prompt
from astrbot_host.session_topology import session_route
from companion_v2.execution.models import require
from companion_v2.foundation.refs import ScopeKind, ScopeRef


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GameRoomContext:
    session: object
    application: object
    context: object
    persona_prompt: str = ""


def _room_route(room, topology=None):
    if (getattr(room, "source", None) != "private"
            or not str(getattr(room, "creator_qq", "") or "").strip()):
        return None
    resolver = getattr(topology, "route", session_route)
    route = resolver(getattr(room, "session_id", None))
    expected = (str(getattr(room, "platform", "") or ""), "private", str(room.creator_qq))
    return expected if route == expected else None


def _scope_matches(scope, room, route):
    return bool(route and scope.kind is ScopeKind.PRIVATE
                and (scope.platform, scope.native_scope_id) == (route[0], route[2])
                and f"{scope.platform}:FriendMessage:{scope.session_id}" == room.session_id
                and scope.bot_id and scope.account_id)


def _context_matches(context, room, route):
    if context is None or not _scope_matches(context.scope, room, route):
        return False
    principal = getattr(getattr(context.actor, "principal", None), "native_account_id", None)
    return principal == route[2]


def _multi_context(controller, session, room, route):
    settings = getattr(controller, "settings", None)
    authority = getattr(controller, "authority", None)
    if settings is None or authority is None:
        return None
    targets = {}
    for account in getattr(settings, "accounts", ()):
        platform = str(getattr(account, "platform_id", "") or "")
        account_id = str(getattr(account, "self_id", "") or "")
        if platform != route[0] or not account_id:
            continue
        target = settings.private_target(platform, account_id, route[2])
        if target is not None:
            targets[target.target_id] = target
    if len(targets) != 1:
        return None
    target = next(iter(targets.values()))
    stored = authority.read("host.multi.active:" + target.target_id)
    if not isinstance(stored, dict):
        return None
    scope = ScopeRef.restore(stored)
    if (not _scope_matches(scope, room, route)
            or (scope.bot_id, scope.account_id)
                != (target.bot_id, target.account_id)):
        return None
    resolution = require(authority.personas.resolve(scope, observed_at=controller.now()))
    context = authority.context(target.target_id, resolution.binding.role.subject_id)
    if context.scope != scope or not _context_matches(context, room, route):
        return None
    return context


def current_room_context(runtime, room, visitor_qq=None):
    """Return a current, exact V2 private context or no private context."""
    controller = getattr(runtime, "controller", None)
    route = _room_route(room, getattr(controller, "session_topology", None))
    if controller is None or route is None or getattr(controller, "_terminated", False):
        return None
    if visitor_qq is not None:
        visitors = getattr(room, "visitors", None)
        if (str(visitor_qq or "") != route[2] or not visitors or len(visitors) != 1):
            return None
    session = getattr(controller, "session", None)
    application = getattr(session, "application", None) if session is not None else None
    registry = getattr(controller, "native_extensions", None)
    if (session is None or application is None or getattr(application, "state", None) != "running"
            or registry is None or registry.application is not application
            or registry.active_service("game") is not runtime):
        return None
    if getattr(controller, "_multi_host", False):
        context = _multi_context(controller, session, room, route)
    else:
        context = getattr(getattr(session, "profile", None), "target", None)
        if not _context_matches(context, room, route):
            return None
    if not _context_matches(context, room, route):
        return None
    return GameRoomContext(session, application, context)


async def resolve_room_context(runtime, room, visitor_qq=None):
    """Verify the mapped persona and reject a target that changes during lookup."""
    try:
        resolved = current_room_context(runtime, room, visitor_qq)
        if resolved is None:
            return None
        bridge = getattr(resolved.session, "bridge", None)
        verifier = getattr(bridge, "verified_persona", None)
        if not callable(verifier):
            return None
        persona = await verifier(resolved.context.scope, resolved.context.role_binding)
        scope = resolved.context.scope
        if (not isinstance(persona, dict)
                or persona.get("bot_id") != scope.bot_id
                or persona.get("account_id") != scope.account_id
                or not isinstance(persona.get("prompt", ""), str)):
            return None
        current = current_room_context(runtime, room, visitor_qq)
        if (current is None or current.session is not resolved.session
                or current.application is not resolved.application
                or current.context.scope != scope
                or current.context.role_binding != resolved.context.role_binding):
            return None
        return GameRoomContext(resolved.session, resolved.application, resolved.context,
                               persona["prompt"])
    except Exception as error:
        logger.debug("[GameCompanion] V2 私聊背景暂不可用: %s", type(error).__name__)
        return None


async def room_background(runtime, room, visitor_qq, query, *, resolved=None):
    if (not getattr(runtime, "private_qq_game_context_enabled", True)
            or not str(query or "").strip()):
        return ""
    if resolved is None:
        resolved = await resolve_room_context(runtime, room, visitor_qq)
    if resolved is None:
        return ""
    controller = runtime.controller
    native_input = SimpleNamespace(text=str(query), event_id="game-background-" + uuid4().hex)
    try:
        return build_core_reply_prompt(resolved.application, resolved.context, native_input,
                                       at=controller.now()).instruction
    except Exception as error:
        logger.debug("[GameCompanion] V2 私聊背景构建失败: %s", type(error).__name__)
        return ""
