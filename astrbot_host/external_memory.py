"""Scoped recall and summary requests to explicitly registered memory plugins."""

from collections.abc import Mapping
import json
import logging

from companion_v2.adapters.astrbot.memory_providers import (
    PROVIDER_API_VERSION, memory_context_document, memory_provider_registry,
)
from companion_v2.execution.models import TaskError
from companion_v2.foundation.refs import ScopeKind


logger = logging.getLogger(__name__)
SOURCE_PLUGIN = "astrbot_plugin_private_companion"


def memory_read_request(context, *, purpose, query="", limit=8, max_chars=2400):
    if (context.scope.kind is not ScopeKind.PRIVATE
            or context.audience.recipients != (context.actor.subject,)):
        raise TaskError("memory_private_context_required", "external recall requires the original private audience")
    # Use the same serialized identities as the core; native user, bot, account,
    # persona and window identifiers must never be inferred from a query string.
    return {"schema": PROVIDER_API_VERSION, "source_plugin": SOURCE_PLUGIN,
            "purpose": purpose, "query": query, "limit": limit, "max_chars": max_chars,
            "context": memory_context_document(context)}


def authorize_memory_request(application, context, registry, entry, request):
    from companion_v2.relationships.account_links import PrivateAccountLinks
    if not entry.linked_memory_read:
        group = PrivateAccountLinks(application.runtime).group_for(context)
        if group and group.get("memory", {}).get("enabled") is True:
            raise TaskError("linked_memory_provider_upgrade_required", "memory provider must support revocable linked reads")
        # Do not put opaque Python objects into an older provider's JSON API.
        return request
    from companion_v2.relationships.account_link_memory import read_scope
    return registry.issue_memory_read(entry, request,
        lambda: read_scope(application, context, request["purpose"]))


def validate_memory_request(registry, request):
    if "_memory_read_lease" in request:
        registry.authorize_memory_read(request)


async def prepare_external_memory(native_context, application, context, native_input):
    registry = memory_provider_registry(native_context)
    if registry is None or context.scope.kind is not ScopeKind.PRIVATE:
        return None
    entry = registry.select("read", handles_model_memory=False)
    if entry is None:
        return None
    try:
        access = application.memory.writer.access
        access.check(context)
        policy = access.policy_for(context)
        if not policy.automatic_context or not policy.context_items or not policy.context_chars:
            return None
        request = memory_read_request(context, purpose="recall",
            query=str(getattr(native_input, "text", "") or "")[-policy.query_chars:],
            limit=policy.context_items, max_chars=policy.context_chars)
        request = authorize_memory_request(application, context, registry, entry, request)
        result = await registry.invoke(entry, "read", request)
        validate_memory_request(registry, request)
        access.check(context)
        policy = access.policy_for(context)
        if not policy.automatic_context:
            return None
        if not isinstance(result, Mapping) or not isinstance(result.get("items"), (list, tuple)):
            raise ValueError("memory reader must return an items list")
        items, remaining = [], policy.context_chars
        for value in result["items"][:policy.context_items]:
            if not isinstance(value, Mapping) or not isinstance(value.get("content"), str):
                continue
            item = {key: value[key] for key in ("id", "content", "occurred_at", "source",
                    "source_session_id", "source_user_id", "source_bot_id", "source_original_bot_id")
                    if isinstance(value.get(key), str)}
            if not item["content"].strip():
                continue
            size = len(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
            if size > remaining:
                continue
            items.append(item)
            remaining -= size
        if not items:
            return None
        return {"provider_id": entry.provider_id, "items": items, "_context_key": context.key,
                "_registry": registry, "_registration": entry, "_request": request}
    except Exception as error:
        logger.warning("[CompanionV2] 外部记忆召回未注入 error_type=%s", type(error).__name__)
        return None


def external_memory_section(material, application, context):
    from .core_reply_context import MEMORY_INSTRUCTION

    if (not material or context.scope.kind is not ScopeKind.PRIVATE
            or material["_context_key"] != context.key):
        return ""
    registry, entry = material["_registry"], material["_registration"]
    if not registry.active(entry):
        return ""
    try:
        validate_memory_request(registry, material.get("_request", {}))
    except Exception:
        return ""
    application.memory.writer.access.check(context)
    if not application.memory.writer.access.policy_for(context).automatic_context:
        return ""
    payload = json.dumps({"provider_id": material["provider_id"], "items": material["items"]},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "\n\n【外部记忆参考】\n" + MEMORY_INSTRUCTION + "\n" + payload
