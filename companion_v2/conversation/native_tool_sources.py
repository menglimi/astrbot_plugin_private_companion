"""Evidence from authenticated AstrBot turns for the existing local tool owners."""

import json
from datetime import datetime

from companion_v2.execution.models import TaskContext, TaskError, digest, encode, require

from .messages import source_from_document
from .native_observations import observations_available, validate_observation


def native_turn(turn_id):
    return isinstance(turn_id, str) and turn_id.startswith("core-reply:")


def input_row(messages, context, turn_id):
    require(messages.tasks.check_context_access(context, action="conversation.reply", observed_at=messages.tasks.now()))
    if not observations_available(messages.runtime):
        raise TaskError("native_tool_source_unavailable", "the native message observation is unavailable")
    row = messages.runtime.read_one("""SELECT * FROM conversation_native_observations
        WHERE turn_id = ? AND context_key = ? AND kind = 'input' AND delivery_state = 'input_observed'""",
        (turn_id, context.key))
    if row is None:
        raise TaskError("native_tool_source_unavailable", "this native tool turn has no authenticated input")
    reference = source_from_document(json.loads(row["source_json"]))
    validate_observation(messages.runtime, row, context, reference)
    messages._read_source(reference, context, "text", messages.tasks.now())
    return row


def validate_tool_turn(messages, context, turn_id, *, evidence=True):
    if native_turn(turn_id):
        if evidence:
            return input_row(messages, context, turn_id)
        return require(messages.tasks.check_context_access(context, action="conversation.reply",
                                                          observed_at=messages.tasks.now()))
    return messages.owned(context, turn_id)


def native_invocation(runtime, invocation):
    """Read the host-created native operation; arguments cannot supply its identity."""
    if not native_turn(invocation.parent_task_id):
        return None
    saved = runtime.read_one("SELECT value FROM runtime_metadata WHERE key=?",
                            ("astrbot.core-tool." + invocation.invocation_id,))
    row = json.loads(saved["value"]) if saved is not None else None
    if (row is None or row["state"] != "running" or
            (row["invocation_id"], row["turn_id"], row["origin_key"], row["call_id"], row["action"],
             row["arguments_json"], row["provider_json"]) !=
            (invocation.invocation_id, invocation.parent_task_id, invocation.context.key, invocation.tool_call_id,
             invocation.descriptor.tool.action, invocation.arguments_json, encode(invocation.descriptor))):
        raise TaskError("native_tool_invocation_changed", "the original native operation changed")
    return row


def native_input_images(messages, context, turn_id, *, optional=False):
    row = messages.runtime.read_one("SELECT value FROM runtime_metadata WHERE key=?",
                                   ("astrbot.core-input-images." + digest(turn_id),))
    if row is None:
        if optional:
            return None
        raise TaskError("native_image_source_unavailable", "this native input has no captured image references")
    saved = json.loads(row["value"])
    from .messages import event_from_document
    event = event_from_document(json.loads(saved["event_json"]), context)
    if (saved["context_key"] != context.key or saved["turn_id"] != turn_id
            or saved["event_hash"] != digest(event) or event.scope != context.scope
            or event.principal != context.actor.principal or not getattr(event, "images", ())):
        raise TaskError("native_image_context_changed", "original native input images changed")
    messages.recalls.check(context.scope, event.event_id, event.principal.native_account_id)
    messages.authorize(context)
    return event


def native_offers(messages, context, turn_id):
    row = input_row(messages, context, turn_id)
    reference = source_from_document(json.loads(row["source_json"]))
    return {"native-input:" + row["observation_id"]: (reference, "text", "current_source")}


def group_reference(row):
    return {"kind": "native_input", "observation_id": row["observation_id"],
            "source_hash": row["content_hash"], "context_key": row["context_key"]}


def group_input(messages, owner, reference, *, at):
    from companion_v2.foundation.refs import ScopeKind
    from companion_v2.identity_policy.group_audience import group_audience
    from companion_v2.identity_policy.personas import PersonaDirectory
    from companion_v2.memory.group.access import owner_key
    from .models import InboundConversationEvent

    if (type(reference) is not dict
            or set(reference) != {"kind", "observation_id", "source_hash", "context_key"}
            or reference["kind"] != "native_input" or not observations_available(messages.runtime)):
        raise TaskError("group_memory_evidence_required", "select the exact authenticated native member input")
    row = messages.runtime.read_one("SELECT * FROM conversation_native_observations WHERE observation_id = ?",
                                    (reference["observation_id"],))
    if row is None or group_reference(row) != reference or row["kind"] != "input":
        raise TaskError("group_memory_evidence_unavailable", "the native group input changed or was withdrawn")
    from companion_v2.storage.native_dedup import resolve_context_json
    original = TaskContext.restore(json.loads(resolve_context_json(messages.runtime, row["context_json"])))
    source = source_from_document(json.loads(row["source_json"]))
    if (original.scope.kind is not ScopeKind.GROUP or owner_key(original) != owner
            or original.audience != group_audience(original.scope, original.actor.subject)):
        raise TaskError("group_memory_evidence_scope", "the native input belongs to another collective owner")
    current_actor = require(messages.tasks.identity.resolve_authenticated(original.actor.principal, observed_at=at))
    if (current_actor.subject, current_actor.binding_id, current_actor.binding_revision) != (
            original.actor.subject, original.actor.binding_id, original.actor.binding_revision):
        raise TaskError("binding_rebound", "the original member binding changed")
    role = require(PersonaDirectory(messages.runtime).resolve(original.scope, observed_at=at))
    if role.binding.role != original.role_binding.role:
        raise TaskError("group_memory_evidence_role", "native evidence no longer belongs to this role")
    for capability in ("text", "group.memory.write"):
        require(messages.tasks.identity.authorize(original.actor, scope=original.scope,
                                                  capability=capability, observed_at=at))
    validate_observation(messages.runtime, row, original, source)
    record = messages._read_source(source, original, "text", at)
    event = InboundConversationEvent(row["event_id"], original.actor.principal, original.scope, source,
                                    "text", record.content_hash, datetime.fromisoformat(row["observed_at"]))
    return record, original, event
