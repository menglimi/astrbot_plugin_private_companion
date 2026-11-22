"""Periodic group-scope episode settings, folded into the existing memory chain.

V1 kept a per-group "本群最近发生" cache refreshed on an interval and fed it back
into that group. V2 deliberately has no second memory: group material is produced
by the registered memory reflection, recorded as governed atoms/views, and read
back through the existing group memory context. This module therefore declares
only the settings and the bounded material those settings control -- it owns no
store, no table and no read path.

The interval decides how often a group conversation is worth re-describing, the
minimum decides when there is enough new material to be worth a pass, and the
batch limit bounds how many prior summaries one pass may see.
"""

from __future__ import annotations

import json

from companion_v2.configuration.models import ApplyMode, ConfigLayer, ConfigTarget
from companion_v2.configuration.owners import SparseConfigurationOwner
from companion_v2.configuration.registry import FieldDefinition, ValueKind
from companion_v2.execution.models import TaskError, digest, require
from companion_v2.foundation.refs import ScopeKind
from companion_v2.identity_policy.group_audience import group_audience
from companion_v2.conversation.reset import batch_current

from .group.access import owner_key as group_owner_key


# The fields live in the memory extension's own group, which already has one owner
# and one scope contract. This is one more bounded input to that reflection rather
# than a separate memory system, so it declares and reads the same way.
GROUP = "memory"
OWNER = "configuration"
MAX_REFRESH_MINUTES = 1440
MIN_REFRESH_MINUTES = 30
MAX_SUMMARY_CHARS = 1200
MAX_SOURCE_LINES = 40

DEFAULTS = {"group_episode_memory_enabled": True,
            "group_episode_refresh_minutes": 180,
            "group_episode_min_messages": 12,
            "max_group_episodes": 10}

LIMITS = {"group_episode_refresh_minutes": (MIN_REFRESH_MINUTES, MAX_REFRESH_MINUTES),
          "group_episode_min_messages": (1, 64),
          "max_group_episodes": (1, 32)}

GUIDANCE = (
    "本群近期对话只用于整理一段简短的本群动态描述，可以覆盖上一版。",
    "描述群内可见的话题与氛围，不把它写成任何成员的事实、身份、关系或承诺，",
    "不据此推断成员性格或立场，也不产生对成员的处理动作。",
    "只为确实有持续价值的内容写一条；没有新内容时不要产出条目。只引用本轮提供的 handle。",
)


def configuration_fields():
    """The episode fields, declared in the memory group so one owner governs them."""
    fields = []
    for name, default in DEFAULTS.items():
        low, high = LIMITS.get(name, (None, None))
        fields.append(FieldDefinition(
            GROUP + "." + name, GROUP, GROUP,
            ValueKind.BOOLEAN if type(default) is bool else ValueKind.INTEGER, default,
            minimum=low, maximum=high,
            apply_mode=ApplyMode.IMMEDIATE, core_only=True, owner_id=OWNER))
    return tuple(fields)


class EpisodeConfigurationOwner(SparseConfigurationOwner):
    owner_id = OWNER


def episode_settings(values):
    settings = {key: values.get(key, default) for key, default in DEFAULTS.items()}
    if type(settings["group_episode_memory_enabled"]) is not bool:
        raise TaskError("episode_configuration", "the group episode switch must be boolean")
    for name, (minimum, maximum) in LIMITS.items():
        value = settings[name]
        if type(value) is not int or isinstance(value, bool) or not minimum <= value <= maximum:
            raise TaskError("episode_configuration", "episode bounds are outside their declared range")
    return settings


def episode_namespace(context):
    """One namespace per group owner, so a group digest never mixes with private facts."""
    return "group.episode:" + digest(group_owner_key(context))[:32]


def settings_for(runtime, context):
    """Read the effective episode settings for this exact group window."""
    if context.scope.kind is not ScopeKind.GROUP:
        raise TaskError("episode_group_required", "group episode settings need a group conversation")
    target = ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW,
                          context.role_binding.role, context.scope)
    snapshot = EpisodeConfigurationOwner(runtime).read(target, configuration_fields())
    return episode_settings({key.removeprefix(GROUP + "."): item.value
                             for key, item in snapshot.fields.items()
                             if key.removeprefix(GROUP + ".") in DEFAULTS})


def recent_group_lines(runtime, context, manifest, *, limit=MAX_SOURCE_LINES):
    """The group's observable replies inside this reflection window, oldest first.

    Legacy replies retain their receipt proof; native replies require an actual
    send-return callback. Only this group's own current, authorized sources are
    sampled. A native send return never becomes a platform delivery receipt.
    """
    if context.scope.kind is not ScopeKind.GROUP:
        raise TaskError("episode_group_required", "group episode material needs a group conversation")
    if context.audience != group_audience(context.scope, context.actor.subject):
        raise TaskError("episode_group_audience_required",
                        "group episode material needs that group's collective audience")
    rows = runtime.read_all("""SELECT p.batch_id, p.text, p.delivered_at
        FROM conversation_parts p
        JOIN conversation_batches b ON b.batch_id = p.batch_id
        JOIN conversation_delivery_receipts r ON r.part_id = p.part_id AND r.receipt_ref = p.receipt_ref
        WHERE p.state = 'delivered' AND p.delivered_at >= ? AND p.delivered_at < ?
        AND json_extract(b.context_json, '$.audience.scope.account_id') = ?
        AND json_extract(b.context_json, '$.audience.scope.bot_id') = ?
        AND json_extract(b.context_json, '$.role_binding.role.subject_id') = ?
        AND json_extract(b.context_json, '$.audience.scope.installation_id') = ?
        AND json_extract(b.context_json, '$.audience.scope.platform') = ?
        AND json_extract(b.context_json, '$.audience.scope.kind') = 'group'
        AND json_extract(b.context_json, '$.audience.scope.native_scope_id') = ?
        AND json_extract(b.context_json, '$.audience.scope.session_id') = ?
        ORDER BY p.delivered_at DESC LIMIT ?""",
        (manifest["start"], manifest["end"], context.scope.account_id, context.scope.bot_id,
         context.role_binding.role.subject_id, context.scope.installation_id, context.scope.platform,
         context.scope.native_scope_id, context.scope.session_id, limit))
    lines = []
    for row in reversed(rows):
        if not batch_current(runtime, row["batch_id"]):
            continue
        text = " ".join(str(row["text"] or "").split())
        if text:
            lines.append((row["delivered_at"], text[:200]))
    from companion_v2.conversation.native_observations import observations_available, validate_observation
    from companion_v2.conversation.messages import source_from_document
    from companion_v2.execution.models import TaskContext
    from companion_v2.identity_policy.personas import PersonaDirectory
    from companion_v2.identity_policy.repository import PersistentIdentityDirectory
    from companion_v2.sources.repositories import SourceRepository
    from companion_v2.storage.codec import decode_timestamp
    from companion_v2.conversation.native_turns import turns_available
    if observations_available(runtime):
        from companion_v2.storage.native_dedup import matching_context_hashes, resolve_context_json
        time_column = "COALESCE(t.sent_at, n.observed_at)" if turns_available(runtime) else "n.observed_at"
        scope_params = (
            context.scope.installation_id, context.scope.platform, context.scope.bot_id, context.scope.account_id,
            context.scope.native_scope_id, context.scope.session_id,
            context.role_binding.role.subject_id,
        )
        hashes = matching_context_hashes(
            runtime,
            """json_extract(context_json, '$.audience.scope.installation_id') = ?
               AND json_extract(context_json, '$.audience.scope.platform') = ?
               AND json_extract(context_json, '$.audience.scope.bot_id') = ?
               AND json_extract(context_json, '$.audience.scope.account_id') = ?
               AND json_extract(context_json, '$.audience.scope.kind') = 'group'
               AND json_extract(context_json, '$.audience.scope.native_scope_id') = ?
               AND json_extract(context_json, '$.audience.scope.session_id') = ?
               AND json_extract(context_json, '$.role_binding.role.subject_id') = ?""",
            scope_params,
        )
        legacy_scope_sql = """(
              json_extract(n.context_json, '$.audience.scope.installation_id') = ?
              AND json_extract(n.context_json, '$.audience.scope.platform') = ?
              AND json_extract(n.context_json, '$.audience.scope.bot_id') = ?
              AND json_extract(n.context_json, '$.audience.scope.account_id') = ?
              AND json_extract(n.context_json, '$.audience.scope.kind') = 'group'
              AND json_extract(n.context_json, '$.audience.scope.native_scope_id') = ?
              AND json_extract(n.context_json, '$.audience.scope.session_id') = ?
              AND json_extract(n.context_json, '$.role_binding.role.subject_id') = ?
        )"""
        if hashes:
            marks = ",".join("?" for _ in hashes)
            scope_where = f"(n.context_hash IN ({marks}) OR {legacy_scope_sql})"
            query_params = (manifest["start"], manifest["end"], *hashes, *scope_params, limit)
        else:
            scope_where = legacy_scope_sql
            query_params = (manifest["start"], manifest["end"], *scope_params, limit)
        native = runtime.read_all("SELECT n.*, " + time_column + " AS sent_at FROM conversation_native_observations n "
            + ("LEFT JOIN conversation_native_sends t USING(observation_id) " if turns_available(runtime) else "") + """
            WHERE n.kind='assistant' AND n.delivery_state='send_returned'
              AND """ + time_column + " >= ? AND " + time_column + " < ? AND " + scope_where + """
            ORDER BY sent_at DESC LIMIT ?""", query_params)
        sources, identity = SourceRepository(runtime), PersistentIdentityDirectory(runtime)
        at = decode_timestamp(manifest["end"])
        for row in reversed(native):
            try:
                original = TaskContext.restore(json.loads(resolve_context_json(runtime, row["context_json"])))
                source = source_from_document(json.loads(row["source_json"]))
                if (group_owner_key(original) != group_owner_key(context)
                        or original.audience != group_audience(original.scope, original.actor.subject)):
                    continue
                validate_observation(runtime, row, original, source)
                role = require(PersonaDirectory(runtime).resolve(original.scope, observed_at=at))
                if role.binding.role != original.role_binding.role:
                    continue
                record = require(sources.read(source, resolved=original.actor, capability="text",
                                              observed_at=at, authorizer=identity))
                if (record.audience != original.audience or record.content_hash != row["content_hash"]
                        or digest(record.payload) != record.content_hash or not isinstance(record.payload, str)):
                    continue
                text = " ".join(record.payload.split())
            except (TaskError, ValueError, KeyError, TypeError):
                continue
            if text:
                lines.append((row["sent_at"], text[:200]))
    return tuple(text for _, text in sorted(lines, key=lambda item: item[0])[-limit:])


def episode_material(runtime, context, manifest):
    """The bounded group-episode material for one reflection window, or None.

    Additive material only: a private scope, a disabled switch, an unusable window
    or too little new activity all simply produce nothing, so no reflection pass
    is ever blocked by this setting.
    """
    if context.scope.kind is not ScopeKind.GROUP:
        return None
    try:
        settings = settings_for(runtime, context)
        if not settings["group_episode_memory_enabled"]:
            return None
        lines = recent_group_lines(runtime, context, manifest)
    except TaskError:
        return None
    if len(lines) < settings["group_episode_min_messages"]:
        return None
    return {"start": manifest["start"], "end": manifest["end"],
            "namespace": episode_namespace(context),
            "refresh_minutes": settings["group_episode_refresh_minutes"],
            "max_group_episodes": settings["max_group_episodes"],
            "max_summary_chars": MAX_SUMMARY_CHARS,
            "recent_group_replies": list(lines), "guidance": list(GUIDANCE)}
