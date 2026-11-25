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
from astrbot_host.external_memory import prepare_external_memory
from astrbot_host.mobile_together import MobileTogetherBridge
from companion_v2.adapters.astrbot.memory_owner import read_memory_owner
from companion_v2.adapters.astrbot.memory_providers import memory_provider_registry


logger = logging.getLogger(__name__)
ROOM_MEMORY_TIMEOUT_SECONDS = 1.5
ROOM_MEMORY_MAX_CHARS = 1600
ROOM_RECALL_MAX_CHARS = 1200


def canonical_private_session_id(platform: str, session_id: str, native_scope_id: str = "") -> str:
    """Return canonical private AstrBot UMO ``{platform}:FriendMessage:{id}`` when possible."""
    raw_platform = str(platform or "").strip()
    raw_session = str(session_id or "").strip()
    raw_user = str(native_scope_id or "").strip()
    if raw_session and ":" in raw_session:
        return raw_session
    target_id = raw_session or raw_user
    if not target_id:
        return ""
    if raw_platform:
        return f"{raw_platform}:FriendMessage:{target_id}"
    return target_id


def private_memory_session_context(resolved, binding, query: str = "") -> dict[str, str]:
    """Build exact private-chat session context from verified V2 room binding."""
    scope = resolved.context.scope
    platform = str(getattr(scope, "platform", "") or getattr(binding, "platform", "") or "").strip()
    raw_session = str(getattr(scope, "session_id", "") or getattr(binding, "session_id", "") or "").strip()
    native_user_id = str(getattr(scope, "native_scope_id", "") or "").strip()
    bot_id = str(getattr(scope, "account_id", "") or getattr(binding, "account_id", "")
                 or getattr(scope, "bot_id", "") or getattr(binding, "bot_id", "") or "").strip()
    persona_id = str(getattr(binding, "persona_id", "") or "").strip()
    return {
        "scope": "private",
        "session_id": canonical_private_session_id(platform, raw_session, native_user_id),
        "platform": platform,
        "user_id": native_user_id or raw_session,
        "bot_id": bot_id,
        "persona_id": persona_id,
        "message_text": str(query or "").strip()[:1000],
    }


@dataclass(frozen=True)
class RoomContext:
    session: object
    application: object
    context: object


def current_room_context(runtime, room, *, allow_closing: bool = False):
    """Local authorization, repeated after awaited host lookups and before writes."""
    controller = runtime.controller
    registry = getattr(controller, "native_extensions", None)
    binding = room.native_v2
    if (binding is None or (room.integration_closed and not allow_closing) or registry is None
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


async def resolve_room_context(runtime, room, *, allow_closing: bool = False):
    """A stale room may keep chatting, but cannot read or write another window."""
    try:
        resolved = current_room_context(runtime, room, allow_closing=allow_closing)
        if resolved is None:
            return None
        registry = runtime.controller.native_extensions
        persona = await registry.persona_guard(resolved.context.scope, resolved.context.role_binding)
        binding = room.native_v2
        if (not isinstance(persona, dict) or any(persona.get(key) != getattr(binding, key)
                for key in ("persona_id", "bot_id", "account_id"))):
            return None
        persona_role_id = persona.get("role_id")
        if persona_role_id and persona_role_id != resolved.context.role_binding.role.subject_id:
            return None
        current = current_room_context(runtime, room, allow_closing=allow_closing)
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


def _delegated_memory_handle(runtime, application):
    """Return the active delegated memory bridge handle if memory_companion owns memory."""
    owner = read_memory_owner(getattr(runtime, "context", None))
    if owner.ownership.delegated and owner.handle is not None:
        return owner.handle
    memory_context = getattr(getattr(application, "messages", None), "memory_context", None)
    if memory_context is not None and callable(getattr(memory_context, "delegated", None)):
        try:
            if memory_context.delegated() and owner.handle is not None:
                return owner.handle
        except Exception:
            pass
    return None


def _has_third_party_external_provider(runtime, *, exclude_memory_companion: bool = False) -> bool:
    registry = memory_provider_registry(getattr(runtime, "context", None))
    if registry is None:
        return False
    entry = registry.select("read", handles_model_memory=False)
    if entry is None:
        return False
    if exclude_memory_companion and entry.provider_id == "memory_companion":
        return False
    return True


def _memory_access_allowed(application, context, *, require_automatic: bool = False) -> bool:
    """Enforce V2 memory authorization & policy before delegated or local room recall."""
    memory_owner = getattr(application, "memory", None)
    writer = getattr(memory_owner, "writer", None)
    access = getattr(writer, "access", None)
    if access is None:
        return True
    try:
        access.check(context)
        if require_automatic:
            policy = access.policy_for(context)
            if not policy.automatic_context or not policy.context_chars:
                return False
        return True
    except Exception:
        return False


def room_memory_recall_available(runtime, resolved) -> bool:
    """Whether an authorized single-user private room has a read-only recall source."""
    if resolved is None or not getattr(runtime, "enable_memory_context", False):
        return False
    if not _memory_access_allowed(resolved.application, resolved.context, require_automatic=False):
        return False
    handle = _delegated_memory_handle(runtime, resolved.application)
    if handle is not None and (
        callable(getattr(handle, "search", None))
        or callable(getattr(handle, "compose_context", None))
    ):
        return True
    if _has_third_party_external_provider(runtime, exclude_memory_companion=False):
        return True
    memory_context = getattr(getattr(resolved.application, "messages", None), "memory_context", None)
    if memory_context is not None:
        try:
            if not (callable(getattr(memory_context, "delegated", None)) and memory_context.delegated()):
                return getattr(resolved.application, "memory", None) is not None
        except Exception:
            return False
    return False


async def _read_delegated_room_memory(handle, session_context, query: str, *,
                                      max_chars: int = ROOM_MEMORY_MAX_CHARS,
                                      timeout_seconds: float = ROOM_MEMORY_TIMEOUT_SECONDS) -> str:
    compose = getattr(handle, "compose_context", None)
    if not callable(compose):
        return ""
    try:
        text = await asyncio.wait_for(
            compose(query=str(query or ""), session_context=session_context, max_chars=max_chars),
            timeout=timeout_seconds,
        )
    except Exception as error:
        logger.debug("[TogetherCompanion] 房间长期记忆桥接降级 error_type=%s", type(error).__name__)
        return ""
    cleaned = str(text or "").strip()
    return cleaned[:max_chars] if cleaned else ""


def _format_recall_items(items, *, max_chars: int = ROOM_RECALL_MAX_CHARS) -> str:
    lines = []
    used = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        content = " ".join(str(item.get("content") or item.get("summary") or item.get("text") or "").split()).strip()
        if not content:
            continue
        occurred_at = str(item.get("occurred_at") or item.get("created_at") or "").strip()[:32]
        prefix = f"[{occurred_at}] " if occurred_at else ""
        line = f"- {prefix}{content}"
        if used + len(line) + 1 > max_chars:
            if not lines:
                lines.append(line[:max_chars])
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


async def recall_room_memory(runtime, room, query: str, *,
                             max_chars: int = ROOM_RECALL_MAX_CHARS,
                             timeout_seconds: float = ROOM_MEMORY_TIMEOUT_SECONDS) -> str:
    """Bounded read-only private memory recall for an authorized single-user room."""
    clean_query = " ".join(str(query or "").split()).strip()[:300]
    if not clean_query or not getattr(runtime, "enable_memory_context", False):
        return ""
    resolved = await resolve_room_context(runtime, room)
    if resolved is None:
        return ""
    application = resolved.application
    if not _memory_access_allowed(application, resolved.context, require_automatic=False):
        return ""
    session_context = private_memory_session_context(resolved, room.native_v2, clean_query)
    handle = _delegated_memory_handle(runtime, application)
    recalled = ""
    if handle is not None:
        search = getattr(handle, "search", None)
        if callable(search):
            try:
                hits = await asyncio.wait_for(
                    search(clean_query, session_context=session_context, top_k=5),
                    timeout=timeout_seconds,
                )
                if isinstance(hits, (list, tuple)):
                    recalled = _format_recall_items(hits, max_chars=max_chars)
            except Exception as error:
                logger.debug("[TogetherCompanion] 房间按需检索降级 error_type=%s", type(error).__name__)
        if not recalled:
            recalled = await _read_delegated_room_memory(
                handle, session_context, clean_query, max_chars=max_chars, timeout_seconds=timeout_seconds
            )
    if not recalled and _has_third_party_external_provider(runtime, exclude_memory_companion=False):
        try:
            native_input = SimpleNamespace(text=clean_query, event_id="together-recall-" + uuid4().hex)
            ext = await asyncio.wait_for(
                prepare_external_memory(getattr(runtime, "context", None), application, resolved.context, native_input),
                timeout=timeout_seconds,
            )
            if isinstance(ext, dict) and isinstance(ext.get("items"), list):
                recalled = _format_recall_items(ext["items"], max_chars=max_chars)
        except Exception as error:
            logger.debug("[TogetherCompanion] 房间外部记忆检索降级 error_type=%s", type(error).__name__)
    if not recalled:
        memory_context = getattr(getattr(application, "messages", None), "memory_context", None)
        delegated = False
        if memory_context is not None and callable(getattr(memory_context, "delegated", None)):
            try:
                delegated = bool(memory_context.delegated())
            except Exception:
                delegated = False
        memory_owner = getattr(application, "memory", None)
        if not delegated and memory_owner is not None and getattr(memory_owner, "query", None) is not None:
            def _query_local():
                session_id = "together-recall-" + uuid4().hex
                memory_owner.writer.access.check(resolved.context)
                memory_owner.query.begin(resolved.context, session_id)
                result = require(memory_owner.query.query(
                    resolved.context, session_id, query_intent=clean_query, limit=5
                ))
                return result.get("items", [])

            try:
                local_items = await asyncio.wait_for(asyncio.to_thread(_query_local), timeout=timeout_seconds)
                if isinstance(local_items, (list, tuple)):
                    recalled = _format_recall_items(local_items, max_chars=max_chars)
            except Exception as error:
                logger.debug("[TogetherCompanion] 房间内置记忆检索降级 error_type=%s", type(error).__name__)
    if (current_room_context(runtime, room) != resolved
            or not _memory_access_allowed(application, resolved.context, require_automatic=False)):
        return ""
    return recalled[:max_chars] if recalled else ""


async def room_background(runtime, room, query):
    resolved = await resolve_room_context(runtime, room)
    if resolved is None:
        return ""
    application = resolved.application
    native_input = SimpleNamespace(text=query, event_id="together-background-" + uuid4().hex)
    delegated_memory_text = ""
    external_memory = None
    memory_source = "disabled"
    if not runtime.enable_memory_context:
        application = _ApplicationView(application)
    else:
        handle = _delegated_memory_handle(runtime, application)
        if handle is not None and _memory_access_allowed(application, resolved.context, require_automatic=True):
            session_context = private_memory_session_context(resolved, room.native_v2, str(query or ""))
            delegated_memory_text = await _read_delegated_room_memory(
                handle, session_context, str(query or ""),
                max_chars=ROOM_MEMORY_MAX_CHARS, timeout_seconds=ROOM_MEMORY_TIMEOUT_SECONDS,
            )
            if delegated_memory_text:
                memory_source = "memory_companion"
        if _has_third_party_external_provider(runtime, exclude_memory_companion=bool(delegated_memory_text)):
            try:
                external_memory = await asyncio.wait_for(
                    prepare_external_memory(getattr(runtime, "context", None), application,
                                            resolved.context, native_input),
                    timeout=ROOM_MEMORY_TIMEOUT_SECONDS,
                )
            except Exception as error:
                logger.debug("[TogetherCompanion] 房间外部记忆准备降级 error_type=%s", type(error).__name__)
                external_memory = None
            if external_memory is not None and memory_source == "disabled":
                memory_source = "external_provider"
        if memory_source == "disabled":
            memory_context = getattr(getattr(application, "messages", None), "memory_context", None)
            if memory_context is not None:
                try:
                    memory_source = "none" if (callable(getattr(memory_context, "delegated", None))
                                               and memory_context.delegated()) else "v2_local"
                except Exception:
                    memory_source = "none"
            else:
                memory_source = "none"
        if current_room_context(runtime, room) != resolved:
            return ""
    from astrbot_host.knowledge_sources import prepare_official_knowledge_hits
    official_hits = await prepare_official_knowledge_hits(
        application, resolved.context, str(query or ""), include_official=True)
    if current_room_context(runtime, room) != resolved:
        return ""
    prompt = await asyncio.to_thread(
        build_core_reply_prompt, application, resolved.context, native_input, at=runtime.controller.now(),
        external_memory=external_memory,
        official_knowledge_hits=official_hits, include_official_knowledge=True,
    )
    instruction = prompt.instruction
    if (delegated_memory_text
            and current_room_context(runtime, room) == resolved
            and _memory_access_allowed(application, resolved.context, require_automatic=True)):
        memory_block = (
            "【私聊长期记忆参考】\n"
            "以下是当前单人私聊对象在私聊与过往相处中沉淀的相关记忆（仅供当前私聊房间自然接话参考，不混淆时间、不编造未提及细节）：\n"
            + delegated_memory_text
        )
        instruction = (instruction + "\n\n" + memory_block) if instruction else memory_block
    logger.debug(
        "[TogetherCompanion] 房间私聊记忆桥接 room=%s memory_source=%s delegated_chars=%d external_items=%d",
        getattr(room, "room_id", "")[:10],
        memory_source,
        len(delegated_memory_text),
        len(external_memory.get("items", ())) if isinstance(external_memory, dict) else 0,
    )
    return instruction


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
    await bridge.verify_role(context.scope, context.role_binding)
    identifier, _, _, _ = await bridge._selected_persona(account, origin)
    if not identifier or current_room_context(runtime, room) != resolved:
        return None, "", ""
    manager = runtime.context.conversation_manager
    conversation = await manager.get_conversation(origin, identifier, create_if_not_exists=False)
    if conversation is None or current_room_context(runtime, room) != resolved:
        return None, "", ""
    await bridge.verify_role(context.scope, context.role_binding)
    if current_room_context(runtime, room) != resolved:
        return None, "", ""
    return manager, origin, str(identifier)
