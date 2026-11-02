"""Which conversation windows share one expression ownership.

AstrBot rotates ``conversation_id``/``session_id`` often, and the V1 group
expression import wrote into a synthetic group identity scope
(``conversation_id = session_id = native_scope_id``) that no conversation table
ever holds. Expression rules belong to a participant or to a group and role, not
to one conversation window, so review and injection resolve a *scope family*:
the current window plus every earlier window of the same owner.

The family is deliberately narrow. A group family keeps the exact installation,
platform, bot, account, native group and role, so another group, another Bot
account or another role never enters it; a legacy V1 group import is the same
group, so its identity scope is part of that family. A private family keeps the
exact participant, so one person's windows never carry another person's rules.

Nothing here reads or writes expression storage: the review domain and the reply
domain both call in, so the two can never disagree about what a group owns.
"""

from __future__ import annotations

import json
import logging
import sqlite3

from companion_v2.execution.models import TaskContext, digest
from companion_v2.foundation.refs import ScopeKind, ScopeRef


logger = logging.getLogger(__name__)

# One bounded page of earlier windows covers the rotations an installation
# accumulates; a family never grows without bound.
MAX_FAMILY_WINDOWS = 512
_WINDOW_TABLES = ("conversation_native_turns", "conversation_batches")


def group_identity_scope(scope):
    """The synthetic scope the V1 group expression import wrote under.

    Old group imports stored the native group id as both ``conversation_id`` and
    ``session_id``. That window is never written to the conversation tables, so
    the family has to name it explicitly or those rules stay invisible and can
    never be injected. An unresolved scope has no identity to synthesise, so its
    family degrades to the current window alone.
    """
    if scope.kind is not ScopeKind.GROUP or not scope.is_resolved:
        return None
    return ScopeRef(scope.installation_id, scope.platform, ScopeKind.GROUP, scope.native_scope_id,
                    scope.bot_id, scope.account_id, scope.native_scope_id, scope.native_scope_id)


def group_identity_key(scope):
    """Digest of the synthetic group identity scope, or ``None``.

    The reply path checks this without a query, so a V1 rule is recognised even
    when the group has no earlier window to scan.
    """
    identity = group_identity_scope(scope)
    return None if identity is None else digest(identity)


def group_scope_keys(runtime, scope, role):
    """Scope digests of every window of one group and role.

    The group owns its expression rules across conversation rotations, so the
    current window, the synthetic V1 identity scope and every earlier window of
    the same installation, platform, bot, account and native group belong to one
    family. Another group or another role never matches.
    """
    if scope.kind is not ScopeKind.GROUP:
        return (digest(scope),)
    keys = [digest(scope)]
    identity = group_identity_scope(scope)
    if identity is not None and digest(identity) not in keys:
        keys.append(digest(identity))
    filter_sql = """json_extract(context_json, '$.role_binding.scope.installation_id') = ?
        AND json_extract(context_json, '$.role_binding.scope.platform') = ?
        AND json_extract(context_json, '$.role_binding.scope.bot_id') = ?
        AND json_extract(context_json, '$.role_binding.scope.account_id') = ?
        AND json_extract(context_json, '$.role_binding.scope.native_scope_id') = ?
        AND json_extract(context_json, '$.role_binding.role.subject_id') = ?
        AND json_extract(context_json, '$.audience.scope.kind') = 'group'"""
    parameters = (scope.installation_id, scope.platform, scope.bot_id, scope.account_id,
                  scope.native_scope_id, role.subject_id)
    for candidate in _windows(runtime, filter_sql, parameters):
        if (candidate.scope.kind is not ScopeKind.GROUP
                or candidate.scope.installation_id != scope.installation_id
                or candidate.scope.platform != scope.platform
                or candidate.scope.bot_id != scope.bot_id
                or candidate.scope.account_id != scope.account_id
                or candidate.scope.native_scope_id != scope.native_scope_id
                or candidate.role_binding.role != role):
            continue
        key = digest(candidate.scope)
        if key not in keys:
            keys.append(key)
    return tuple(keys)


def private_scope_keys(runtime, scope, role):
    """Scope digests of this participant's current and earlier private windows.

    AstrBot rotates ``conversation_id``/``session_id`` frequently. Expression
    rules are learned per participant and role, not per one conversation id, so
    the review page must still find rules learned in the previous window. Only
    the exact same installation, platform, bot, account, native scope, kind and
    role qualify; another participant never enters the family.
    """
    if scope.kind is not ScopeKind.PRIVATE:
        return (digest(scope),)
    keys = [digest(scope)]
    filter_sql = """json_extract(context_json, '$.role_binding.scope.installation_id') = ?
        AND json_extract(context_json, '$.role_binding.scope.platform') = ?
        AND json_extract(context_json, '$.role_binding.scope.bot_id') = ?
        AND json_extract(context_json, '$.role_binding.scope.account_id') = ?
        AND json_extract(context_json, '$.role_binding.scope.native_scope_id') = ?
        AND json_extract(context_json, '$.audience.scope.kind') = 'private'"""
    parameters = (scope.installation_id, scope.platform, scope.bot_id, scope.account_id,
                  scope.native_scope_id)
    for candidate in _windows(runtime, filter_sql, parameters):
        if (candidate.scope.kind is not ScopeKind.PRIVATE
                or candidate.scope.installation_id != scope.installation_id
                or candidate.scope.platform != scope.platform
                or candidate.scope.bot_id != scope.bot_id
                or candidate.scope.account_id != scope.account_id
                or candidate.scope.native_scope_id != scope.native_scope_id
                or candidate.role_binding.role != role):
            continue
        key = digest(candidate.scope)
        if key not in keys:
            keys.append(key)
    return tuple(keys)


def scope_family_keys(runtime, context):
    """Every scope digest whose expression rules this conversation may read.

    Group rules belong to the group and private rules belong to one participant,
    so the two families are resolved separately; any other scope kind owns only
    its own window.
    """
    if context.scope.kind is ScopeKind.GROUP:
        return group_scope_keys(runtime, context.scope, context.role_binding.role)
    if context.scope.kind is ScopeKind.PRIVATE:
        return private_scope_keys(runtime, context.scope, context.role_binding.role)
    return (digest(context.scope),)


def _windows(runtime, filter_sql, parameters):
    """Restore the stored turn contexts matching one SQL filter, newest first."""
    for table in _WINDOW_TABLES:
        try:
            rows = runtime.read_all(f"""SELECT context_json FROM {table} WHERE {filter_sql}
                ORDER BY rowid DESC LIMIT ?""", (*parameters, MAX_FAMILY_WINDOWS))
        except (sqlite3.Error, TypeError, ValueError, KeyError) as error:
            # A missing table or a schema drift is not fatal for a family: the
            # remaining tables and the current window still describe the owner,
            # but the reason must stay visible in developer logs.
            logger.debug("[CompanionV2] 作用域家族窗口读取失败 table=%s error_type=%s",
                         table, type(error).__name__)
            continue
        for row in rows:
            try:
                yield TaskContext.restore(json.loads(row["context_json"]))
            except (TypeError, ValueError, KeyError):
                continue
