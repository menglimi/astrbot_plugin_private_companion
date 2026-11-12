"""Global contact defaults and read-only recovery of legacy personal settings."""

from dataclasses import replace
import json

from companion_v2.configuration.models import ConfigLayer, ConfigTarget, EffectiveValue
from companion_v2.configuration.owners import SparseConfigurationOwner
from companion_v2.execution.models import digest
from companion_v2.foundation.refs import SubjectKind, SubjectRef


# Old rhythm saves were also written directly to a window before the public
# editor moved to installation scope. Keep those fields compatible even when
# their old receipt is unavailable. Other fields require a proven public save
# receipt so a private window record cannot become a global default.
SHARED_RHYTHM_NAMES = frozenset({
    "enabled", "automatic_enabled", "quiet_enabled", "quiet_start", "quiet_end", "quiet_timezone",
    "proactive_hour_activity_enabled", "proactive_hour_activity_curve", "proactive_intensity_preset",
    "proactive_intensity_body_chars", "interval_seconds", "idle_seconds", "enable_proactive_burst",
    "proactive_burst_max_messages", "proactive_burst_gap_min_seconds", "proactive_burst_gap_max_seconds",
    "proactive_closing_grace_minutes", "proactive_closing_contacts_threshold",
    "enable_daily_greetings", "greeting_morning_window", "greeting_noon_window", "greeting_evening_window",
    "greeting_idle_minutes", "allow_insomnia_night_message", "insomnia_night_start", "insomnia_night_end",
    "insomnia_require_sleep_state", "insomnia_night_daily_limit",
})


def relationship_target_for(context):
    return ConfigTarget(context.scope.installation_id, ConfigLayer.RELATIONSHIP,
        context.role_binding.role, bot_id=context.scope.bot_id, subject=context.actor.subject)


LEGACY_CONTACT_OPERATION_PREFIX = "proactive.contact-config:"
WINDOW_CONTACT_OPERATION_PREFIX = "proactive.contact-window-config:"


def _private_contact_operations(runtime, target_keys, prefixes):
    """Identify member saves by their exact parent/child operation hash."""
    store = runtime.configuration_store
    if not target_keys:
        return set()
    placeholders = ",".join("?" for _ in target_keys)
    operation_filters = " OR ".join("c.operation_id LIKE ?" for _ in prefixes)
    routes = store.read_all(f"""SELECT DISTINCT c.target_key, c.operation_id AS child_id,
            p.operation_id AS parent_id
        FROM configuration_changes c JOIN operations o ON o.operation_id = c.operation_id
        JOIN operations p ON p.owner = 'relationships.private-settings'
            AND p.installation_id = o.installation_id AND p.platform = o.platform
            AND p.scope_kind = o.scope_kind AND p.native_scope_id = o.native_scope_id
            AND p.bot_id = o.bot_id AND p.account_id = o.account_id
            AND p.conversation_id = o.conversation_id AND p.session_id = o.session_id
        WHERE c.target_key IN ({placeholders}) AND c.group_id = 'proactive.contact'
            AND ({operation_filters})
            AND p.state = 'completed'""", (*target_keys, *(prefix + "%" for prefix in prefixes)))
    if not routes:
        return set()
    operators = tuple(SubjectRef(runtime.installation_id, SubjectKind.HUMAN, row["subject_id"])
        for row in runtime.read_all("SELECT subject_id FROM subject_access_tiers WHERE subject_kind = 'human'", ()))
    return {row["child_id"] for row in routes if any(row["child_id"] == prefix +
        digest([operator, row["target_key"], row["parent_id"] + ":contact"])
        for operator in operators for prefix in prefixes)}


def legacy_personal_operations(runtime, target_keys):
    return _private_contact_operations(runtime, target_keys, (LEGACY_CONTACT_OPERATION_PREFIX,))


def private_contact_operations(runtime, target_keys):
    return _private_contact_operations(runtime, target_keys,
        (LEGACY_CONTACT_OPERATION_PREFIX, WINDOW_CONTACT_OPERATION_PREFIX))


def personal_contact_snapshot(runtime, target, fields, current):
    """Read proven old member overrides on the same stable human relationship."""
    store = runtime.configuration_store
    if target.layer is not ConfigLayer.RELATIONSHIP:
        return current
    missing = {field.field_id: field for field in fields if not current.fields[field.field_id].explicit}
    if not missing:
        return current
    document = "CASE WHEN json_valid(t.target_json) THEN t.target_json ELSE '{}' END"
    receipt = "CASE WHEN json_valid(c.result_json) THEN c.result_json ELSE '{}' END"
    rows = store.read_all(f"""SELECT t.target_key, t.revision AS target_revision,
            v.field_id, v.value_json, v.revision, c.operation_id
        FROM configuration_targets t JOIN configuration_values v ON v.target_key = t.target_key
        JOIN configuration_changes c ON c.target_key = t.target_key AND c.group_id = ?
            AND c.changed = 1 AND json_extract({receipt}, '$.revision') = v.revision
        WHERE v.field_id IN ({','.join('?' for _ in missing)})
            AND json_extract({document}, '$.installation_id') = ?
            AND json_extract({document}, '$.layer') = 'window'
            AND json_extract({document}, '$.scope.kind') = 'private'
            AND json_extract({document}, '$.subject_id') = ?
            AND json_extract({document}, '$.bot_id') = ?
            AND json_extract({document}, '$.role_id') = ?
            AND c.operation_id LIKE 'proactive.contact-config:%'
        ORDER BY c.created_at DESC, c.sequence DESC, t.target_key""",
        (fields[0].group_id, *missing, target.installation_id, target.subject.subject_id,
         target.bot_id, target.role.subject_id))
    personal = legacy_personal_operations(runtime, {row["target_key"] for row in rows})
    values, dependencies, recovered = dict(current.fields), dict(current.dependencies), set()
    for row in rows:
        key = row["field_id"]
        if key in recovered or row["operation_id"] not in personal:
            continue
        try:
            value = missing[key].validate(json.loads(row["value_json"]))
        except (TypeError, ValueError):
            continue
        values[key] = EffectiveValue(value, target, False, row["revision"])
        dependencies[row["target_key"]] = row["target_revision"]
        recovered.add(key)
    return replace(current, fields=values, dependencies=tuple(dependencies.items()))


def global_contact_snapshot(runtime, target, fields):
    """Installation defaults never depend on a selected bot, role or chat ID."""
    store = runtime.configuration_store
    installation = ConfigTarget(target.installation_id, ConfigLayer.INSTALLATION)
    current = SparseConfigurationOwner(runtime).read(installation, fields)
    missing = {field.field_id: field for field in fields
               if field.field_id.removeprefix(field.group_id + ".") in SHARED_RHYTHM_NAMES
               and current.fields[field.field_id].source is None}
    if not missing:
        return current
    document = "CASE WHEN json_valid(t.target_json) THEN t.target_json ELSE '{}' END"
    receipt = "CASE WHEN json_valid(c.result_json) THEN c.result_json ELSE '{}' END"
    placeholders = ",".join("?" for _ in missing)
    rows = store.read_all(f"""SELECT t.target_key, t.revision AS target_revision,
            v.field_id, v.value_json, v.revision, c.operation_id
        FROM configuration_targets t JOIN configuration_values v ON v.target_key = t.target_key
        LEFT JOIN configuration_changes c ON c.target_key = t.target_key AND c.group_id = ?
            AND c.changed = 1 AND json_extract({receipt}, '$.revision') = v.revision
        WHERE v.field_id IN ({placeholders})
            AND json_extract({document}, '$.installation_id') = ?
            AND json_extract({document}, '$.layer') = 'window'
            AND json_extract({document}, '$.scope.kind') = 'private'
            AND (c.operation_id IS NULL OR c.operation_id LIKE 'proactive.contact-config:%')
        ORDER BY COALESCE(c.created_at, t.updated_at) DESC, c.sequence DESC,
            t.revision DESC, t.target_key""",
        (fields[0].group_id, *missing, target.installation_id))
    values, dependencies = dict(current.fields), dict(current.dependencies)
    personal = legacy_personal_operations(runtime, {row["target_key"] for row in rows})
    recovered = set()
    for row in rows:
        key = row["field_id"]
        if key in recovered or row["operation_id"] in personal:
            continue
        if row["operation_id"] is None and key.removeprefix(fields[0].group_id + ".") not in SHARED_RHYTHM_NAMES:
            continue
        try:
            value = missing[key].validate(json.loads(row["value_json"]))
        except (TypeError, ValueError):
            continue
        # Personal operations were removed above; temporary switches use a
        # different owner and are excluded from the legacy public query.
        values[key] = EffectiveValue(value, installation, False, row["revision"])
        dependencies[row["target_key"]] = row["target_revision"]
        recovered.add(key)
    return replace(current, fields=values, dependencies=tuple(dependencies.items()))
