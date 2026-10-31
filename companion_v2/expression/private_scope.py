"""Role-wide private expression scopes derived from currently bound identities."""

import json

from companion_v2.execution.models import TaskContext, digest
from companion_v2.foundation.refs import ScopeKind


def scope_family_keys(runtime, context):
    """Scope digests of this participant's current and earlier private windows.

    AstrBot rotates ``conversation_id``/``session_id`` frequently. Expression
    rules are learned per participant and role, not per one conversation id, so
    the review page must still find rules learned in the previous window. Only
    the exact same installation, platform, bot, account, native scope, kind and
    role qualify; another participant never enters the family.
    """
    keys = [digest(context.scope)]
    scope = context.scope
    if scope.kind is not ScopeKind.PRIVATE:
        return tuple(keys)
    parameters = (scope.installation_id, scope.platform, scope.bot_id, scope.account_id,
                  scope.native_scope_id)
    filter_sql = """json_extract(context_json, '$.role_binding.scope.installation_id') = ?
        AND json_extract(context_json, '$.role_binding.scope.platform') = ?
        AND json_extract(context_json, '$.role_binding.scope.bot_id') = ?
        AND json_extract(context_json, '$.role_binding.scope.account_id') = ?
        AND json_extract(context_json, '$.role_binding.scope.native_scope_id') = ?
        AND json_extract(context_json, '$.audience.scope.kind') = 'private'"""
    for table in ("conversation_native_turns", "conversation_batches"):
        try:
            rows = runtime.read_all(f"""SELECT context_json FROM {table}
                WHERE {filter_sql} ORDER BY rowid DESC LIMIT 512""", parameters)
        except Exception:
            continue
        for row in rows:
            try:
                candidate = TaskContext.restore(json.loads(row["context_json"]))
            except (TypeError, ValueError, KeyError):
                continue
            if (candidate.scope.kind is not ScopeKind.PRIVATE
                    or candidate.scope.installation_id != scope.installation_id
                    or candidate.scope.platform != scope.platform
                    or candidate.scope.bot_id != scope.bot_id
                    or candidate.scope.account_id != scope.account_id
                    or candidate.scope.native_scope_id != scope.native_scope_id
                    or candidate.role_binding.role != context.role_binding.role):
                continue
            key = digest(candidate.scope)
            if key not in keys:
                keys.append(key)
    return tuple(keys)


def private_people(runtime, identity, context, at):
    """List only participants with a stored, currently bound private context for this role."""
    if context.scope.kind is not ScopeKind.PRIVATE:
        return ()
    candidates = {}
    scope_filter = """json_extract(context_json, '$.role_binding.role.subject_id') = ?
        AND json_extract(context_json, '$.role_binding.scope.bot_id') = ?
        AND json_extract(context_json, '$.role_binding.scope.installation_id') = ?"""
    parameters = (context.role_binding.role.subject_id, context.scope.bot_id,
                  context.scope.installation_id)
    for query in (
        "SELECT context_json FROM conversation_batches WHERE " + scope_filter + " ORDER BY rowid DESC LIMIT 2000",
        "SELECT context_json FROM conversation_native_observations WHERE kind = 'input' AND "
        + scope_filter + " ORDER BY observed_at DESC, rowid DESC LIMIT 2000",
    ):
        try:
            rows = runtime.read_all(query, parameters)
        except Exception:
            continue
        for row in rows:
            try:
                candidate = TaskContext.restore(json.loads(row["context_json"]))
            except (TypeError, ValueError, KeyError):
                continue
            if (candidate.scope.kind is not ScopeKind.PRIVATE
                    or candidate.scope.installation_id != context.scope.installation_id
                    or candidate.scope.bot_id != context.scope.bot_id
                    or candidate.role_binding.role != context.role_binding.role
                    or candidate.actor.subject.kind.value != "human"):
                continue
            subject_id = candidate.actor.subject.subject_id
            if subject_id in candidates:
                continue
            resolved = identity.resolve_authenticated(candidate.actor.principal, observed_at=at)
            current_role = identity.resolve_role(candidate.scope, observed_at=at)
            if (not resolved.is_success or not current_role.is_success
                    or resolved.unwrap().subject != candidate.actor.subject
                    or resolved.unwrap().binding_id != candidate.actor.binding_id
                    or resolved.unwrap().binding_revision != candidate.actor.binding_revision
                    or current_role.unwrap() != candidate.role_binding):
                continue
            candidates[subject_id] = {
                "subject_id": subject_id,
                "label": candidate.actor.principal.native_account_id or subject_id,
                "platform": candidate.scope.platform,
                "scope_label": candidate.scope.native_scope_id,
            }
    selected = candidates.get(context.actor.subject.subject_id)
    if selected is not None:
        candidates[context.actor.subject.subject_id] = {
            **selected,
            "label": selected["label"] + " · 当前",
        }
    return tuple(sorted(candidates.values(), key=lambda item: (item["label"], item["subject_id"])))


def role_private_contexts(runtime, identity, messages, context, at):
    from .configuration import read_settings, source_subject_allowed_for_context

    people = private_people(runtime, identity, context, at)
    allowed_ids = {item["subject_id"] for item in people}
    contexts = {}
    scope_filter = """json_extract(context_json, '$.role_binding.role.subject_id') = ?
        AND json_extract(context_json, '$.role_binding.scope.bot_id') = ?
        AND json_extract(context_json, '$.role_binding.scope.installation_id') = ?"""
    parameters = (context.role_binding.role.subject_id, context.scope.bot_id,
                  context.scope.installation_id)
    queries = (
        "SELECT context_json FROM conversation_batches WHERE " + scope_filter + " ORDER BY rowid DESC LIMIT 2000",
        "SELECT context_json FROM conversation_native_observations WHERE kind = 'input' AND "
        + scope_filter + " ORDER BY observed_at DESC, rowid DESC LIMIT 2000",
    )
    for query in queries:
        try:
            rows = runtime.read_all(query, parameters)
        except Exception:
            continue
        for row in rows:
            try:
                candidate = TaskContext.restore(json.loads(row["context_json"]))
            except (TypeError, ValueError, KeyError):
                continue
            subject_id = candidate.actor.subject.subject_id
            if candidate.key in contexts or subject_id not in allowed_ids:
                continue
            try:
                messages.authorize(candidate, at)
                policy = read_settings(runtime, candidate)
                if (candidate.scope.kind is ScopeKind.PRIVATE
                        and candidate.scope.installation_id == context.scope.installation_id
                        and candidate.scope.bot_id == context.scope.bot_id
                        and candidate.role_binding.role == context.role_binding.role
                        and policy.private_learning_enabled
                        and source_subject_allowed_for_context(runtime, candidate)):
                    contexts[candidate.key] = candidate
            except Exception:
                continue
    return tuple(contexts.values())


def current_private_context(runtime, key, role_binding):
    """Recover an exact private source context by its opaque stable context key."""
    if type(key) is not str or len(key) != 64:
        return None
    rows = runtime.read_all("""SELECT context_json FROM conversation_batches WHERE origin_key = ?
        ORDER BY rowid DESC LIMIT 1""", (key,))
    if not rows:
        try:
            rows = runtime.read_all("""SELECT context_json FROM conversation_native_observations
                WHERE context_key = ? AND kind = 'input' ORDER BY observed_at DESC, rowid DESC LIMIT 1""", (key,))
        except Exception:
            rows = ()
    for row in rows:
        try:
            candidate = TaskContext.restore(json.loads(row["context_json"]))
        except (TypeError, ValueError, KeyError):
            continue
        if (candidate.key == key and candidate.scope.kind is ScopeKind.PRIVATE
                and candidate.scope.installation_id == role_binding.scope.installation_id
                and candidate.scope.bot_id == role_binding.scope.bot_id
                and candidate.role_binding.role == role_binding.role):
            return candidate
    return None
