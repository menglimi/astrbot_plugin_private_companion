"""Private-chat expression scope helpers.

Only private concerns live here: the participants bound to this role and the
private source contexts that belong to them. Scope-family resolution -- for both
private participants and group ownership -- lives in
``companion_v2.conversation.scope_family`` so the review page and reply
injection share one implementation instead of two entries.
"""

from contextlib import nullcontext
import json

from companion_v2.execution.models import TaskContext
from companion_v2.foundation.refs import ScopeKind


def _distinct_role_private_contexts(runtime, context):
    """Collect distinct private TaskContexts for this role, deduplicating before JSON decode."""
    if context.scope.kind is not ScopeKind.PRIVATE:
        return ()
    from companion_v2.storage.native_dedup import (
        has_unhashed_context_rows,
        matching_context_hashes,
        resolve_context_json,
    )

    scope_filter = """json_extract(context_json, '$.role_binding.role.subject_id') = ?
        AND json_extract(context_json, '$.role_binding.scope.bot_id') = ?
        AND json_extract(context_json, '$.role_binding.scope.installation_id') = ?"""
    parameters = (context.role_binding.role.subject_id, context.scope.bot_id,
                  context.scope.installation_id)
    queries = [
        (
            "SELECT origin_key AS ctx_key, context_json FROM conversation_batches WHERE "
            + scope_filter + " ORDER BY rowid DESC LIMIT 2000",
            parameters,
        ),
    ]
    hashes = matching_context_hashes(runtime, scope_filter, parameters)
    if hashes:
        for start in range(0, len(hashes), 400):
            chunk = hashes[start:start + 400]
            marks = ",".join("?" for _ in chunk)
            queries.append((
                f"SELECT context_key AS ctx_key, context_json FROM conversation_native_observations "
                f"WHERE kind = 'input' AND context_hash IN ({marks}) ORDER BY observed_at DESC, rowid DESC LIMIT 2000",
                chunk,
            ))
    if has_unhashed_context_rows(runtime, "conversation_native_observations", kind="input"):
        queries.append((
            "SELECT context_key AS ctx_key, context_json FROM conversation_native_observations WHERE kind = 'input' AND "
            + scope_filter + " ORDER BY observed_at DESC, rowid DESC LIMIT 2000",
            parameters,
        ))
    seen_keys = set()
    seen_raw = set()
    distinct = []
    for query, query_params in queries:
        try:
            rows = runtime.read_all(query, query_params)
        except Exception:
            continue
        for row in rows:
            ctx_key = row["ctx_key"]
            if ctx_key:
                if ctx_key in seen_keys:
                    continue
                seen_keys.add(ctx_key)
            raw_json = row["context_json"]
            if not ctx_key:
                if raw_json in seen_raw:
                    continue
                seen_raw.add(raw_json)
            try:
                candidate = TaskContext.restore(json.loads(resolve_context_json(runtime, raw_json)))
            except (TypeError, ValueError, KeyError):
                continue
            if (candidate.scope.kind is not ScopeKind.PRIVATE
                    or candidate.scope.installation_id != context.scope.installation_id
                    or candidate.scope.bot_id != context.scope.bot_id
                    or candidate.role_binding.role != context.role_binding.role):
                continue
            distinct.append(candidate)
    return tuple(distinct)


def _bound_private_people(identity, context, at, distinct_contexts):
    candidates = {}
    for candidate in distinct_contexts:
        if candidate.actor.subject.kind.value != "human":
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


def private_people(runtime, identity, context, at):
    """List only participants with a stored, currently bound private context for this role."""
    if context.scope.kind is not ScopeKind.PRIVATE:
        return ()
    distinct_contexts = _distinct_role_private_contexts(runtime, context)
    return _bound_private_people(identity, context, at, distinct_contexts)


def role_private_contexts(runtime, identity, messages, context, at):
    from .configuration import read_settings, resolve_role_source_gate, source_subject_allowed

    if context.scope.kind is not ScopeKind.PRIVATE:
        return ()
    snapshot = runtime.read_snapshot() if callable(getattr(runtime, "read_snapshot", None)) else nullcontext()
    with snapshot:
        distinct_contexts = _distinct_role_private_contexts(runtime, context)
        if not distinct_contexts:
            return ()
        people = _bound_private_people(identity, context, at, distinct_contexts)
        allowed_ids = {item["subject_id"] for item in people}
        if not allowed_ids:
            return ()
        role_scope, owner_subject = resolve_role_source_gate(runtime, context)
        permitted_ids = {
            subject_id for subject_id in allowed_ids
            if source_subject_allowed(role_scope, subject_id, owner_subject)
        }
        if not permitted_ids:
            return ()
        contexts = {}
        for candidate in distinct_contexts:
            subject_id = candidate.actor.subject.subject_id
            if candidate.key in contexts or subject_id not in permitted_ids:
                continue
            try:
                messages.authorize(candidate, at)
                policy = read_settings(runtime, candidate)
                if policy.private_learning_enabled:
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
            # Include the partial index predicate for native input lookups.
            rows = runtime.read_all("""SELECT context_json FROM conversation_native_observations
                WHERE context_key = ? AND kind = 'input' AND delivery_state = 'input_observed'
                ORDER BY observed_at DESC, rowid DESC LIMIT 1""", (key,))
        except Exception:
            rows = ()
    from companion_v2.storage.native_dedup import resolve_context_json
    for row in rows:
        try:
            candidate = TaskContext.restore(json.loads(resolve_context_json(runtime, row["context_json"])))
        except (TypeError, ValueError, KeyError):
            continue
        if (candidate.key == key and candidate.scope.kind is ScopeKind.PRIVATE
                and candidate.scope.installation_id == role_binding.scope.installation_id
                and candidate.scope.bot_id == role_binding.scope.bot_id
                and candidate.role_binding.role == role_binding.role):
            return candidate
    return None
