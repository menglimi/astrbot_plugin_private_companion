"""Read a bounded, redacted summary from the active external memory provider."""

from collections.abc import Mapping
from typing import Any

from companion_v2.adapters.astrbot.memory_owner import read_memory_owner
from companion_v2.adapters.astrbot.memory_providers import memory_provider_registry
from companion_v2.foundation.refs import ScopeKind

from .external_memory import memory_read_request


V2_PLUGIN_ID = "astrbot_plugin_private_companion"
SUMMARY_CATEGORIES = ("profile", "preference", "relationship", "private_conversation", "other")


def _count(value: Any) -> int:
    try:
        return max(0, min(1000000000, int(value)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _producer(context):
    getter = getattr(context, "get_all_stars", None)
    if not callable(getter):
        return None
    try:
        matches = [metadata for metadata in tuple(getter() or ())
                   if getattr(metadata, "activated", True) is True
                   and (getattr(metadata, "root_dir_name", "") == V2_PLUGIN_ID
                        or getattr(metadata, "name", "") == V2_PLUGIN_ID)]
    except Exception:
        return None
    if len(matches) != 1:
        return None
    return getattr(matches[0], "star_cls", None)


def _unavailable(state: str, provider: str, display_name: str, reason: str) -> dict[str, Any]:
    return {"state": state, "provider": provider, "display_name": display_name,
            "reason": reason, "counts": None, "categories": []}


async def read_external_memory_summary(controller, context) -> dict[str, Any]:
    """Read only category counts for one authorized private target and role.

    The external bridge revalidates an opaque capability and exact user/session
    identity. This function deliberately does not fall back to the provider's
    database, generic search, or unscoped memory APIs.
    """

    native_context = getattr(controller, "context", None)
    providers = memory_provider_registry(native_context)
    entry = providers.select("summary") if providers is not None else None
    if entry is not None:
        if context.scope.kind is not ScopeKind.PRIVATE:
            return _unavailable("unsupported_scope", entry.provider_id, entry.display_name, "private_target_required")
        try:
            request = memory_read_request(context, purpose="summary", max_chars=0)
            raw = await providers.invoke(entry, "summary", request)
            if (not isinstance(raw, Mapping) or not isinstance(raw.get("counts"), Mapping)
                    or raw.get("state", "ready") != "ready" or raw.get("ok", True) is not True):
                raise ValueError("memory summary requires counts")
            counts = {key: _count(raw["counts"].get(key)) for key in (*SUMMARY_CATEGORIES, "total")}
        except Exception:
            return _unavailable("unavailable", entry.provider_id, entry.display_name, "summary_read_failed")
        return {"state": "ready", "provider": entry.provider_id, "display_name": entry.display_name,
                "reason": "", "counts": counts,
                "categories": [{"key": key, "count": counts[key]} for key in SUMMARY_CATEGORIES]}

    owner = read_memory_owner(native_context)
    provider = owner.ownership.provider or ""
    display_name = owner.ownership.display_name or "记忆插件"
    if not owner.ownership.delegated or owner.handle is None:
        return _unavailable("not_owner", provider, display_name,
                            getattr(owner.ownership, "reason", "not_memory_owner"))
    scope = context.scope
    if scope.kind is not ScopeKind.PRIVATE or not scope.native_scope_id:
        return _unavailable("unsupported_scope", provider, display_name, "private_target_required")

    producer = _producer(getattr(controller, "context", None))
    if producer is None:
        return _unavailable("unavailable", provider, display_name, "v2_registration_unavailable")
    try:
        register = getattr(owner.handle, "register_private_companion_memory_summary_reader", None)
        create_context = getattr(owner.handle, "create_private_companion_memory_summary_context", None)
        read_summary = getattr(owner.handle, "read_user_memory_summary", None)
        if not all(callable(method) for method in (register, create_context, read_summary)):
            return _unavailable("unavailable", provider, display_name, "summary_contract_unavailable")
        capability = register(producer)
        if capability is None:
            return _unavailable("unavailable", provider, display_name, "v2_capability_denied")
        session_id = f"{scope.platform}:FriendMessage:{scope.native_scope_id}"
        requester = create_context(capability, bot_id=scope.bot_id or "", scope="private",
                                   platform=scope.platform, user_id=scope.native_scope_id,
                                   session_id=session_id)
        if requester is None:
            return _unavailable("unavailable", provider, display_name, "private_context_unavailable")
        raw = await read_summary(scope.native_scope_id, session_id=session_id, limit=8,
                                 requester_context=requester)
    except Exception:
        return _unavailable("unavailable", provider, display_name, "summary_read_failed")

    if not isinstance(raw, Mapping) or raw.get("contract") != "memory.user_memory_summary.v1":
        return _unavailable("unavailable", provider, display_name, "invalid_summary_response")
    if raw.get("state") != "ready" or raw.get("ok") is not True:
        error = str(raw.get("error_code") or "summary_not_ready")
        allowed = {"bridge_disabled", "bridge_exception", "bridge_method_unavailable", "bridge_unavailable",
                   "invalid_store_response", "invalid_bridge_response", "missing_user_id", "store_query_failed",
                   "store_unavailable", "session_identity_mismatch"}
        return _unavailable("unavailable", provider, display_name, error if error in allowed else "summary_not_ready")
    raw_counts = raw.get("counts") if isinstance(raw.get("counts"), Mapping) else {}
    counts = {key: _count(raw_counts.get(key)) for key in (*SUMMARY_CATEGORIES, "total")}
    return {"state": "ready", "provider": provider, "display_name": display_name,
            "reason": "", "counts": counts,
            "categories": [{"key": key, "count": counts[key]} for key in SUMMARY_CATEGORIES]}


__all__ = ["read_external_memory_summary"]
