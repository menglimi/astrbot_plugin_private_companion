"""Versioned SQLite schema and explicit in-place migrations owned by V2."""

from __future__ import annotations

import hashlib
import re

from .relationship_anniversary_schema import (
    RELATIONSHIP_ANNIVERSARY_STATEMENTS,
    RELATIONSHIP_ANNIVERSARY_TABLES,
)


SCHEMA_VERSION_V1 = "companion-v2.storage.v1"
SCHEMA_VERSION_V2 = "companion-v2.storage.v2"
SCHEMA_VERSION_V3 = "companion-v2.storage.v3"
SCHEMA_VERSION_V4 = "companion-v2.storage.v4"
SCHEMA_VERSION_V5 = "companion-v2.storage.v5"
SCHEMA_VERSION_V6 = "companion-v2.storage.v6"
SCHEMA_VERSION_V7 = "companion-v2.storage.v7"
SCHEMA_VERSION_V8 = "companion-v2.storage.v8"
SCHEMA_VERSION_V9 = "companion-v2.storage.v9"
SCHEMA_VERSION_V10 = "companion-v2.storage.v10"
SCHEMA_VERSION_V11 = "companion-v2.storage.v11"
SCHEMA_VERSION_V12 = "companion-v2.storage.v12"
SCHEMA_VERSION_V13 = "companion-v2.storage.v13"
SCHEMA_VERSION_V14 = "companion-v2.storage.v14"
SCHEMA_VERSION_V15 = "companion-v2.storage.v15"
SCHEMA_VERSION_V16 = "companion-v2.storage.v16"
SCHEMA_VERSION_V17 = "companion-v2.storage.v17"
SCHEMA_VERSION_V18 = "companion-v2.storage.v18"
SCHEMA_VERSION_V19 = "companion-v2.storage.v19"
SCHEMA_VERSION_V20 = "companion-v2.storage.v20"
SCHEMA_VERSION_V21 = "companion-v2.storage.v21"
SCHEMA_VERSION_V22 = "companion-v2.storage.v22"
SCHEMA_VERSION_V23 = "companion-v2.storage.v23"
SCHEMA_VERSION_V24 = "companion-v2.storage.v24"
SCHEMA_VERSION_V25 = "companion-v2.storage.v25"
SCHEMA_VERSION_V26 = "companion-v2.storage.v26"
SCHEMA_VERSION_V27 = "companion-v2.storage.v27"
SCHEMA_VERSION_V28 = "companion-v2.storage.v28"
SCHEMA_VERSION_V29 = "companion-v2.storage.v29"
SCHEMA_VERSION_V30 = "companion-v2.storage.v30"
SCHEMA_VERSION_V31 = "companion-v2.storage.v31"
SCHEMA_VERSION_V32 = "companion-v2.storage.v32"
SCHEMA_VERSION_V33 = "companion-v2.storage.v33"
SCHEMA_VERSION_V34 = "companion-v2.storage.v34"
SCHEMA_VERSION_V35 = "companion-v2.storage.v35"
SCHEMA_VERSION_V36 = "companion-v2.storage.v36"
SCHEMA_VERSION_V37 = "companion-v2.storage.v37"
SCHEMA_VERSION_V38 = "companion-v2.storage.v38"
SCHEMA_VERSION_V39 = "companion-v2.storage.v39"
SCHEMA_VERSION_V40 = "companion-v2.storage.v40"
SCHEMA_VERSION = SCHEMA_VERSION_V40
MIGRATION_V39_TO_V40_ID = "companion-v2.storage.v39-to-v40"
MIGRATION_V38_TO_V39_ID = "companion-v2.storage.v38-to-v39"
MIGRATION_V37_TO_V38_ID = "companion-v2.storage.v37-to-v38"
MIGRATION_V36_TO_V37_ID = "companion-v2.storage.v36-to-v37"
MIGRATION_V35_TO_V36_ID = "companion-v2.storage.v35-to-v36"
MIGRATION_V34_TO_V35_ID = "companion-v2.storage.v34-to-v35"
MIGRATION_V33_TO_V34_ID = "companion-v2.storage.v33-to-v34"
MIGRATION_V32_TO_V33_ID = "companion-v2.storage.v32-to-v33"
MIGRATION_V31_TO_V32_ID = "companion-v2.storage.v31-to-v32"
MIGRATION_V30_TO_V31_ID = "companion-v2.storage.v30-to-v31"
MIGRATION_V29_TO_V30_ID = "companion-v2.storage.v29-to-v30"
MIGRATION_V28_TO_V29_ID = "companion-v2.storage.v28-to-v29"
MIGRATION_V27_TO_V28_ID = "companion-v2.storage.v27-to-v28"
MIGRATION_V26_TO_V27_ID = "companion-v2.storage.v26-to-v27"
MIGRATION_V25_TO_V26_ID = "companion-v2.storage.v25-to-v26"
MIGRATION_V24_TO_V25_ID = "companion-v2.storage.v24-to-v25"
MIGRATION_V23_TO_V24_ID = "companion-v2.storage.v23-to-v24"
MIGRATION_V22_TO_V23_ID = "companion-v2.storage.v22-to-v23"
MIGRATION_V21_TO_V22_ID = "companion-v2.storage.v21-to-v22"
MIGRATION_V20_TO_V21_ID = "companion-v2.storage.v20-to-v21"
MIGRATION_V19_TO_V20_ID = "companion-v2.storage.v19-to-v20"
MIGRATION_V18_TO_V19_ID = "companion-v2.storage.v18-to-v19"
MIGRATION_V17_TO_V18_ID = "companion-v2.storage.v17-to-v18"
MIGRATION_V16_TO_V17_ID = "companion-v2.storage.v16-to-v17"
MIGRATION_V15_TO_V16_ID = "companion-v2.storage.v15-to-v16"
MIGRATION_V14_TO_V15_ID = "companion-v2.storage.v14-to-v15"
MIGRATION_V13_TO_V14_ID = "companion-v2.storage.v13-to-v14"
MIGRATION_V12_TO_V13_ID = "companion-v2.storage.v12-to-v13"
MIGRATION_V11_TO_V12_ID = "companion-v2.storage.v11-to-v12"
MIGRATION_V10_TO_V11_ID = "companion-v2.storage.v10-to-v11"
MIGRATION_V9_TO_V10_ID = "companion-v2.storage.v9-to-v10"
MIGRATION_V1_TO_V2_ID = "companion-v2.storage.v1-to-v2"
MIGRATION_V2_TO_V3_ID = "companion-v2.storage.v2-to-v3"
MIGRATION_V3_TO_V4_ID = "companion-v2.storage.v3-to-v4"
MIGRATION_V4_TO_V5_ID = "companion-v2.storage.v4-to-v5"
MIGRATION_V5_TO_V6_ID = "companion-v2.storage.v5-to-v6"
MIGRATION_V6_TO_V7_ID = "companion-v2.storage.v6-to-v7"
MIGRATION_V7_TO_V8_ID = "companion-v2.storage.v7-to-v8"
MIGRATION_V8_TO_V9_ID = "companion-v2.storage.v8-to-v9"


V1_REQUIRED_TABLES = frozenset(
    {
        "runtime_metadata",
        "namespaces",
        "sources",
        "evidence",
        "assets",
        "operations",
        "outbox",
        "inbox",
        "migration_checkpoints",
    }
)

V1_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS runtime_metadata (
        key TEXT PRIMARY KEY NOT NULL,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS namespaces (
        namespace_id TEXT PRIMARY KEY NOT NULL,
        owner TEXT NOT NULL,
        installation_id TEXT NOT NULL,
        platform TEXT NOT NULL,
        scope_kind TEXT NOT NULL,
        native_scope_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision > 0),
        created_at TEXT NOT NULL,
        UNIQUE(owner, installation_id, platform, scope_kind, native_scope_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sources (
        source_id TEXT NOT NULL,
        lineage_id TEXT NOT NULL,
        source_revision INTEGER NOT NULL CHECK (source_revision > 0),
        owner TEXT NOT NULL,
        installation_id TEXT NOT NULL,
        platform TEXT NOT NULL,
        scope_kind TEXT NOT NULL,
        native_scope_id TEXT NOT NULL,
        issued_at TEXT NOT NULL,
        expires_at TEXT,
        recipients_json TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('active', 'revoked')),
        governance_revision INTEGER NOT NULL CHECK (governance_revision > 0),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(source_id, lineage_id, source_revision)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS evidence (
        evidence_id TEXT PRIMARY KEY NOT NULL,
        source_id TEXT NOT NULL,
        source_lineage_id TEXT NOT NULL,
        source_revision INTEGER NOT NULL CHECK (source_revision > 0),
        lineage_id TEXT NOT NULL,
        evidence_revision INTEGER NOT NULL CHECK (evidence_revision > 0),
        observed_at TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('active', 'revoked')),
        governance_revision INTEGER NOT NULL CHECK (governance_revision > 0),
        UNIQUE(evidence_id, lineage_id, evidence_revision),
        FOREIGN KEY(source_id, source_lineage_id, source_revision)
            REFERENCES sources(source_id, lineage_id, source_revision)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS assets (
        asset_id TEXT NOT NULL,
        lineage_id TEXT NOT NULL,
        asset_revision INTEGER NOT NULL CHECK (asset_revision > 0),
        owner TEXT NOT NULL,
        installation_id TEXT NOT NULL,
        platform TEXT NOT NULL,
        scope_kind TEXT NOT NULL,
        native_scope_id TEXT NOT NULL,
        recipients_json TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('active', 'revoked')),
        governance_revision INTEGER NOT NULL CHECK (governance_revision > 0),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(asset_id, lineage_id, asset_revision)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS operations (
        operation_id TEXT PRIMARY KEY NOT NULL,
        owner TEXT NOT NULL,
        installation_id TEXT NOT NULL,
        platform TEXT NOT NULL,
        scope_kind TEXT NOT NULL,
        native_scope_id TEXT NOT NULL,
        lineage_id TEXT NOT NULL,
        operation_revision INTEGER NOT NULL CHECK (operation_revision > 0),
        idempotency_key TEXT NOT NULL,
        parameter_hash TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('accepted', 'completed', 'unknown')),
        commit_state TEXT NOT NULL CHECK (commit_state IN ('committed', 'not_committed', 'unknown')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(owner, installation_id, platform, scope_kind, native_scope_id, idempotency_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS outbox (
        outbox_id TEXT PRIMARY KEY NOT NULL,
        operation_id TEXT NOT NULL,
        part_id TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('pending', 'accepted', 'unknown', 'cancelled')),
        created_at TEXT NOT NULL,
        UNIQUE(operation_id, part_id),
        FOREIGN KEY(operation_id) REFERENCES operations(operation_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS inbox (
        consumer TEXT NOT NULL,
        event_id TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(consumer, event_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS migration_checkpoints (
        checkpoint_id TEXT PRIMARY KEY NOT NULL,
        owner TEXT NOT NULL,
        source_fingerprint TEXT NOT NULL,
        cursor TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision > 0),
        state TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS sources_scope_idx ON sources(installation_id, platform, scope_kind, native_scope_id)",
    "CREATE INDEX IF NOT EXISTS assets_scope_idx ON assets(installation_id, platform, scope_kind, native_scope_id)",
    "CREATE INDEX IF NOT EXISTS outbox_state_idx ON outbox(state, created_at)",
)


V2_MIGRATION_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        migration_id TEXT PRIMARY KEY NOT NULL,
        from_version TEXT NOT NULL,
        to_version TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        applied_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS capability_descriptors (
        extension_id TEXT NOT NULL,
        package_id TEXT NOT NULL,
        package_version TEXT NOT NULL,
        publisher_id TEXT NOT NULL,
        artifact_hash TEXT NOT NULL,
        descriptor_json TEXT NOT NULL,
        registered_at TEXT NOT NULL,
        PRIMARY KEY(extension_id, package_id, package_version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS capability_installations (
        extension_id TEXT PRIMARY KEY NOT NULL,
        package_id TEXT NOT NULL,
        package_version TEXT NOT NULL,
        artifact_hash TEXT NOT NULL,
        desired_state TEXT NOT NULL CHECK (
            desired_state IN ('active', 'paused', 'discard_requested', 'discarded')
        ),
        revision INTEGER NOT NULL CHECK (revision > 0),
        retained_dataset_owners_json TEXT NOT NULL,
        acquired_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(extension_id, package_id, package_version)
            REFERENCES capability_descriptors(extension_id, package_id, package_version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS capability_role_selections (
        extension_id TEXT NOT NULL,
        role_kind TEXT NOT NULL,
        role_id TEXT NOT NULL,
        enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
        revision INTEGER NOT NULL CHECK (revision > 0),
        updated_at TEXT NOT NULL,
        PRIMARY KEY(extension_id, role_kind, role_id),
        FOREIGN KEY(extension_id) REFERENCES capability_installations(extension_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS capability_authorizations (
        extension_id TEXT NOT NULL,
        capability_id TEXT NOT NULL,
        subject_kind TEXT NOT NULL,
        subject_id TEXT NOT NULL,
        installation_id TEXT NOT NULL,
        platform TEXT NOT NULL,
        scope_kind TEXT NOT NULL,
        native_scope_id TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('active', 'revoked')),
        revision INTEGER NOT NULL CHECK (revision > 0),
        updated_at TEXT NOT NULL,
        PRIMARY KEY(
            extension_id, capability_id, subject_kind, subject_id,
            installation_id, platform, scope_kind, native_scope_id
        ),
        FOREIGN KEY(extension_id) REFERENCES capability_installations(extension_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS capability_settings (
        extension_id TEXT NOT NULL,
        installation_id TEXT NOT NULL,
        platform TEXT NOT NULL,
        scope_kind TEXT NOT NULL,
        native_scope_id TEXT NOT NULL,
        proactive_enabled INTEGER NOT NULL CHECK (proactive_enabled IN (0, 1)),
        thought_weight REAL NOT NULL CHECK (thought_weight > 0),
        revision INTEGER NOT NULL CHECK (revision > 0),
        updated_at TEXT NOT NULL,
        PRIMARY KEY(extension_id, installation_id, platform, scope_kind, native_scope_id),
        FOREIGN KEY(extension_id) REFERENCES capability_installations(extension_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS screen_captures (
        capture_id TEXT PRIMARY KEY NOT NULL,
        source_id TEXT NOT NULL,
        source_lineage_id TEXT NOT NULL,
        source_revision INTEGER NOT NULL CHECK (source_revision > 0),
        asset_id TEXT NOT NULL,
        asset_lineage_id TEXT NOT NULL,
        asset_revision INTEGER NOT NULL CHECK (asset_revision > 0),
        device_id TEXT NOT NULL,
        media_kind TEXT NOT NULL CHECK (media_kind IN ('image', 'video')),
        coverage_json TEXT NOT NULL,
        captured_at TEXT NOT NULL,
        received_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(source_id, source_lineage_id, source_revision),
        FOREIGN KEY(source_id, source_lineage_id, source_revision)
            REFERENCES sources(source_id, lineage_id, source_revision),
        FOREIGN KEY(asset_id, asset_lineage_id, asset_revision)
            REFERENCES assets(asset_id, lineage_id, asset_revision)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS screen_analyses (
        analysis_id TEXT NOT NULL,
        lineage_id TEXT NOT NULL,
        analysis_revision INTEGER NOT NULL CHECK (analysis_revision > 0),
        capture_id TEXT NOT NULL,
        processor_id TEXT NOT NULL,
        purpose TEXT NOT NULL,
        coverage_json TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(analysis_id, lineage_id, analysis_revision),
        FOREIGN KEY(capture_id) REFERENCES screen_captures(capture_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS screen_feedback_anchors (
        feedback_id TEXT PRIMARY KEY NOT NULL,
        capture_id TEXT NOT NULL,
        analysis_id TEXT,
        analysis_lineage_id TEXT,
        analysis_revision INTEGER,
        feedback_kind TEXT NOT NULL CHECK (
            feedback_kind IN ('attention', 'reply', 'proactive', 'journal', 'silence')
        ),
        created_at TEXT NOT NULL,
        CHECK (
            (analysis_id IS NULL AND analysis_lineage_id IS NULL AND analysis_revision IS NULL)
            OR (analysis_id IS NOT NULL AND analysis_lineage_id IS NOT NULL AND analysis_revision IS NOT NULL)
        ),
        FOREIGN KEY(capture_id) REFERENCES screen_captures(capture_id),
        FOREIGN KEY(analysis_id, analysis_lineage_id, analysis_revision)
            REFERENCES screen_analyses(analysis_id, lineage_id, analysis_revision)
    )
    """,
    "CREATE INDEX IF NOT EXISTS capability_authorizations_scope_idx ON capability_authorizations(installation_id, platform, scope_kind, native_scope_id)",
    "CREATE INDEX IF NOT EXISTS screen_captures_received_idx ON screen_captures(received_at)",
)

V2_REQUIRED_TABLES = V1_REQUIRED_TABLES.union(
    {
        "schema_migrations",
        "capability_descriptors",
        "capability_installations",
        "capability_role_selections",
        "capability_authorizations",
        "capability_settings",
        "screen_captures",
        "screen_analyses",
        "screen_feedback_anchors",
    }
)

MIGRATION_V1_TO_V2_FINGERPRINT = hashlib.sha256(
    "\n".join(V2_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V3_MIGRATION_STATEMENTS = (
    """
    CREATE TABLE identity_policy_metadata (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        revision INTEGER NOT NULL CHECK (revision > 0)
    )
    """,
    "INSERT INTO identity_policy_metadata VALUES (1, 1)",
    """
    CREATE TABLE identity_bindings (
        binding_id TEXT PRIMARY KEY NOT NULL,
        installation_id TEXT NOT NULL,
        host_id TEXT NOT NULL,
        platform TEXT NOT NULL,
        native_account_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision > 0),
        payload_json TEXT NOT NULL,
        UNIQUE(installation_id, host_id, platform, native_account_id, revision)
    )
    """,
    """
    CREATE TABLE identity_grants (
        grant_id TEXT PRIMARY KEY NOT NULL,
        subject_kind TEXT NOT NULL,
        subject_id TEXT NOT NULL,
        capability TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX identity_grants_subject_idx ON identity_grants(subject_kind, subject_id, capability)",
    """
    CREATE TABLE role_bindings (
        installation_id TEXT NOT NULL,
        platform TEXT NOT NULL,
        scope_kind TEXT NOT NULL,
        native_scope_id TEXT NOT NULL,
        role_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision > 0),
        PRIMARY KEY(installation_id, platform, scope_kind, native_scope_id)
    )
    """,
    """
    CREATE TABLE configuration_targets (
        target_key TEXT PRIMARY KEY NOT NULL,
        target_json TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 0),
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE configuration_values (
        target_key TEXT NOT NULL,
        field_id TEXT NOT NULL,
        value_json TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision > 0),
        PRIMARY KEY(target_key, field_id),
        FOREIGN KEY(target_key) REFERENCES configuration_targets(target_key)
    )
    """,
    """
    CREATE TABLE configuration_changes (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        operation_id TEXT UNIQUE NOT NULL,
        target_key TEXT NOT NULL,
        group_id TEXT NOT NULL,
        changed INTEGER NOT NULL CHECK (changed IN (0, 1)),
        result_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(operation_id) REFERENCES operations(operation_id),
        FOREIGN KEY(target_key) REFERENCES configuration_targets(target_key)
    )
    """,
    """
    CREATE TABLE configuration_consumers (
        consumer_id TEXT PRIMARY KEY NOT NULL,
        generation INTEGER NOT NULL CHECK (generation > 0),
        started_sequence INTEGER NOT NULL CHECK (started_sequence >= 0)
    )
    """,
    "CREATE INDEX configuration_changes_target_idx ON configuration_changes(target_key, group_id, changed, sequence)",
    """
    CREATE TABLE configuration_applications (
        target_key TEXT NOT NULL,
        group_id TEXT NOT NULL,
        consumer_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK (generation > 0),
        resolution_token TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('applied', 'failed')),
        adopted_values_json TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(target_key, group_id, consumer_id),
        FOREIGN KEY(consumer_id) REFERENCES configuration_consumers(consumer_id)
    )
    """,
)

V3_REQUIRED_TABLES = V2_REQUIRED_TABLES.union({
    "identity_policy_metadata", "identity_bindings", "identity_grants", "role_bindings",
    "configuration_targets", "configuration_values", "configuration_changes",
    "configuration_consumers", "configuration_applications",
})

MIGRATION_V2_TO_V3_FINGERPRINT = hashlib.sha256(
    "\n".join(V3_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V4_MIGRATION_STATEMENTS = (
    """
    CREATE TABLE agenda_memos (
        memo_id TEXT PRIMARY KEY NOT NULL,
        target_key TEXT NOT NULL,
        target_json TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision > 0),
        state TEXT NOT NULL CHECK (state IN ('active', 'completed', 'deleted')),
        pinned INTEGER NOT NULL CHECK (pinned IN (0, 1)),
        record_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX agenda_memos_target_idx ON agenda_memos(target_key, state, memo_id)",
    "CREATE INDEX agenda_memos_order_idx ON agenda_memos(target_key, pinned DESC, memo_id)",
    """
    CREATE TABLE agenda_reminders (
        reminder_id TEXT PRIMARY KEY NOT NULL,
        target_key TEXT NOT NULL,
        target_json TEXT NOT NULL,
        memo_id TEXT UNIQUE,
        revision INTEGER NOT NULL CHECK (revision > 0),
        state TEXT NOT NULL CHECK (state IN ('active', 'cancelled')),
        record_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(memo_id) REFERENCES agenda_memos(memo_id)
    )
    """,
    "CREATE INDEX agenda_reminders_target_idx ON agenda_reminders(target_key, state, reminder_id)",
    """
    CREATE TABLE agenda_occurrences (
        occurrence_id TEXT PRIMARY KEY NOT NULL,
        reminder_id TEXT NOT NULL,
        reminder_revision INTEGER NOT NULL CHECK (reminder_revision > 0),
        sequence INTEGER NOT NULL CHECK (sequence >= 0),
        due_at TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('scheduled', 'accepted', 'prepared', 'cancelled')),
        revision INTEGER NOT NULL CHECK (revision > 0),
        memo_revision INTEGER,
        prepared_json TEXT,
        updated_at TEXT NOT NULL,
        UNIQUE(reminder_id, reminder_revision, sequence),
        FOREIGN KEY(reminder_id) REFERENCES agenda_reminders(reminder_id)
    )
    """,
    "CREATE INDEX agenda_occurrences_due_idx ON agenda_occurrences(state, due_at, occurrence_id)",
    """
    CREATE TABLE agenda_results (
        operation_id TEXT PRIMARY KEY NOT NULL,
        target_key TEXT NOT NULL,
        subject_id TEXT NOT NULL,
        action TEXT NOT NULL,
        result_json TEXT NOT NULL,
        FOREIGN KEY(operation_id) REFERENCES operations(operation_id)
    )
    """,
)

V4_REQUIRED_TABLES = V3_REQUIRED_TABLES.union({
    "agenda_memos", "agenda_reminders", "agenda_occurrences", "agenda_results",
})
MIGRATION_V3_TO_V4_FINGERPRINT = hashlib.sha256(
    "\n".join(V4_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()
V5_MIGRATION_STATEMENTS = (
    """
    CREATE TABLE object_policy_metadata (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        revision INTEGER NOT NULL CHECK (revision > 0)
    )
    """,
    "INSERT INTO object_policy_metadata VALUES (1, 1)",
    """
    CREATE TABLE subject_access_tiers (
        subject_kind TEXT NOT NULL,
        subject_id TEXT NOT NULL,
        tier TEXT NOT NULL CHECK (tier IN ('core', 'important', 'standard')),
        revision INTEGER NOT NULL CHECK (revision > 0),
        updated_at TEXT NOT NULL,
        PRIMARY KEY(subject_kind, subject_id)
    )
    """,
    """
    CREATE TABLE access_objects (
        object_key TEXT PRIMARY KEY NOT NULL,
        owner_kind TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        installation_id TEXT NOT NULL,
        platform TEXT NOT NULL,
        scope_kind TEXT NOT NULL,
        native_scope_id TEXT NOT NULL,
        domain TEXT NOT NULL
    )
    """,
    "CREATE INDEX access_objects_scope_idx ON access_objects(installation_id, platform, scope_kind, native_scope_id, owner_kind, owner_id, domain)",
    """
    CREATE TABLE object_access_shares (
        object_key TEXT NOT NULL,
        subject_kind TEXT NOT NULL,
        subject_id TEXT NOT NULL,
        action TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('active', 'revoked')),
        revision INTEGER NOT NULL CHECK (revision > 0),
        valid_from TEXT NOT NULL,
        expires_at TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(object_key, subject_kind, subject_id, action),
        FOREIGN KEY(object_key) REFERENCES access_objects(object_key)
    )
    """,
    "CREATE INDEX object_access_shares_subject_idx ON object_access_shares(subject_kind, subject_id, action, state, object_key)",
)
V5_REQUIRED_TABLES = V4_REQUIRED_TABLES.union({
    "object_policy_metadata", "subject_access_tiers", "access_objects", "object_access_shares",
})
MIGRATION_V4_TO_V5_FINGERPRINT = hashlib.sha256(
    "\n".join(V5_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()
V5_SCHEMA_STATEMENTS = (
    V1_SCHEMA_STATEMENTS + V2_MIGRATION_STATEMENTS + V3_MIGRATION_STATEMENTS
    + V4_MIGRATION_STATEMENTS + V5_MIGRATION_STATEMENTS
)

# Rebuild only tables whose indexed identity changes. The v1-v5 DDL above and
# their migration fingerprints remain immutable. Empty fields mean unresolved
# historical scope, never the currently logged-in bot/account.
V6_REBUILT_TABLES = (
    "namespaces", "sources", "assets", "operations", "role_bindings", "access_objects",
    "capability_authorizations", "capability_settings", "identity_bindings", "capability_role_selections",
)


def _v6_statement(statement: str) -> str:
    if "native_scope_id" in statement:
        statement = statement.replace("native_scope_id TEXT NOT NULL,", """native_scope_id TEXT NOT NULL,
        bot_id TEXT NOT NULL DEFAULT '',
        account_id TEXT NOT NULL DEFAULT '',
        conversation_id TEXT NOT NULL DEFAULT '',
        session_id TEXT NOT NULL DEFAULT '',""")
        statement = re.sub(r"native_scope_id(?=[,)\s]*(?:\)|owner_kind|idempotency_key))",
                           "native_scope_id, bot_id, account_id, conversation_id, session_id", statement)
    if "CREATE TABLE identity_bindings" in statement:
        statement = statement.replace("native_account_id TEXT NOT NULL,", """native_account_id TEXT NOT NULL,
        bot_id TEXT NOT NULL DEFAULT '',
        account_id TEXT NOT NULL DEFAULT '',""")
        statement = statement.replace("native_account_id, revision)", "native_account_id, bot_id, account_id, revision)")
    if "CREATE TABLE IF NOT EXISTS capability_role_selections" in statement:
        statement = statement.replace("role_id TEXT NOT NULL,", "role_id TEXT NOT NULL,\n        bot_id TEXT NOT NULL DEFAULT '',")
        statement = statement.replace("PRIMARY KEY(extension_id, role_kind, role_id)", "PRIMARY KEY(extension_id, role_kind, role_id, bot_id)")
    return statement


V6_SCHEMA_STATEMENTS = tuple(_v6_statement(statement) for statement in V5_SCHEMA_STATEMENTS)
V6_TABLE_STATEMENTS = {
    name: next(statement for statement in V6_SCHEMA_STATEMENTS
               if re.search(r"CREATE TABLE (?:IF NOT EXISTS )?" + name + r"\s*\(", statement))
    for name in V6_REBUILT_TABLES
}
V6_INDEX_STATEMENTS = tuple(statement for statement in V6_SCHEMA_STATEMENTS
                            if re.search(r"CREATE INDEX .* ON (" + "|".join(V6_REBUILT_TABLES) + r")\(", statement))
V6_REQUIRED_TABLES = V5_REQUIRED_TABLES
MIGRATION_V5_TO_V6_FINGERPRINT = hashlib.sha256(
    ("copy-original-columns-preserve-unresolved.v1\n" + "\n".join(V6_TABLE_STATEMENTS.values())
     + "\n" + "\n".join(V6_INDEX_STATEMENTS)).encode("utf-8")
).hexdigest()

V7_MIGRATION_STATEMENTS = (
    """
    CREATE TABLE conversation_routes (
        scope_key TEXT PRIMARY KEY NOT NULL,
        record_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE persona_binding_events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        binding_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision > 0),
        selector_key TEXT NOT NULL,
        recorded_at TEXT NOT NULL,
        record_json TEXT NOT NULL,
        UNIQUE(binding_id, revision)
    )
    """,
    "CREATE INDEX persona_binding_selector_idx ON persona_binding_events(selector_key, sequence)",
    """
    CREATE TABLE legacy_scope_mappings (
        mapping_id TEXT PRIMARY KEY NOT NULL,
        original_key TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision > 0),
        state TEXT NOT NULL CHECK (state IN ('active', 'revoked')),
        record_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX legacy_scope_mapping_original_idx ON legacy_scope_mappings(original_key, state)",
)
V7_REQUIRED_TABLES = V6_REQUIRED_TABLES.union({
    "conversation_routes", "persona_binding_events", "legacy_scope_mappings",
})
V7_SCHEMA_STATEMENTS = V6_SCHEMA_STATEMENTS + V7_MIGRATION_STATEMENTS
MIGRATION_V6_TO_V7_FINGERPRINT = hashlib.sha256(
    "\n".join(V7_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V8_MIGRATION_STATEMENTS = (
    """
    CREATE TABLE diagnostic_repair_proposals (
        proposal_id TEXT PRIMARY KEY NOT NULL,
        origin_key TEXT NOT NULL,
        target_key TEXT NOT NULL,
        group_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision > 0),
        state TEXT NOT NULL CHECK (state IN ('pending', 'confirmed', 'cancelled')),
        record_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX diagnostic_proposal_origin_idx ON diagnostic_repair_proposals(origin_key, state, proposal_id)",
)
V8_REQUIRED_TABLES = V7_REQUIRED_TABLES.union({"diagnostic_repair_proposals"})
V8_SCHEMA_STATEMENTS = V7_SCHEMA_STATEMENTS + V8_MIGRATION_STATEMENTS
MIGRATION_V7_TO_V8_FINGERPRINT = hashlib.sha256(
    "\n".join(V8_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V9_MIGRATION_STATEMENTS = (
    """
    CREATE TABLE task_supervisor (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        generation INTEGER NOT NULL CHECK (generation > 0),
        limits_json TEXT NOT NULL,
        started_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE execution_tasks (
        task_id TEXT PRIMARY KEY NOT NULL,
        origin_key TEXT NOT NULL,
        kind TEXT NOT NULL,
        lane TEXT NOT NULL CHECK (lane IN ('foreground', 'background')),
        dedupe_key TEXT NOT NULL UNIQUE,
        request_hash TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision > 0),
        state TEXT NOT NULL CHECK (state IN (
            'created', 'admitted', 'running', 'retry_scheduled', 'cancelling',
            'succeeded', 'failed', 'uncertain', 'cancelled', 'expired', 'dead_letter'
        )),
        generation INTEGER NOT NULL CHECK (generation > 0),
        attempt_no INTEGER NOT NULL CHECK (attempt_no >= 0),
        attempt_id TEXT,
        next_run_at TEXT NOT NULL,
        deadline_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        record_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX execution_due_idx ON execution_tasks(lane, state, next_run_at, task_id)",
    "CREATE INDEX execution_recovery_idx ON execution_tasks(state, generation, task_id)",
    "CREATE INDEX execution_origin_idx ON execution_tasks(origin_key, task_id)",
    """
    CREATE TABLE execution_attempts (
        attempt_id TEXT PRIMARY KEY NOT NULL,
        task_id TEXT NOT NULL,
        attempt_no INTEGER NOT NULL CHECK (attempt_no > 0),
        generation INTEGER NOT NULL CHECK (generation > 0),
        lane TEXT NOT NULL CHECK (lane IN ('foreground', 'background')),
        kind TEXT NOT NULL,
        origin_key TEXT NOT NULL,
        scope_key TEXT NOT NULL,
        slot_held INTEGER NOT NULL CHECK (slot_held IN (0, 1)),
        lease_until TEXT NOT NULL,
        record_json TEXT NOT NULL,
        UNIQUE(task_id, attempt_no),
        FOREIGN KEY(task_id) REFERENCES execution_tasks(task_id)
    )
    """,
    "CREATE INDEX execution_slots_idx ON execution_attempts(slot_held, lane, kind, scope_key)",
    "CREATE INDEX execution_kind_slots_idx ON execution_attempts(kind, slot_held, scope_key)",
    "CREATE INDEX execution_lease_idx ON execution_attempts(slot_held, lease_until, attempt_id)",
    """
    CREATE TABLE execution_events (
        task_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision > 0),
        state TEXT NOT NULL,
        reason TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        PRIMARY KEY(task_id, revision),
        FOREIGN KEY(task_id) REFERENCES execution_tasks(task_id)
    )
    """,
)
V9_REQUIRED_TABLES = V8_REQUIRED_TABLES.union({
    "task_supervisor", "execution_tasks", "execution_attempts", "execution_events",
})
V9_SCHEMA_STATEMENTS = V8_SCHEMA_STATEMENTS + V9_MIGRATION_STATEMENTS
MIGRATION_V8_TO_V9_FINGERPRINT = hashlib.sha256(
    "\n".join(V9_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V10_MIGRATION_STATEMENTS = (
    """
    CREATE TABLE model_requests (
        task_id TEXT PRIMARY KEY NOT NULL,
        request_hash TEXT NOT NULL,
        plan_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(task_id) REFERENCES execution_tasks(task_id)
    )
    """,
    """
    CREATE TABLE model_budget_accounts (
        account_key TEXT PRIMARY KEY NOT NULL,
        policy_hash TEXT NOT NULL,
        token_limit INTEGER NOT NULL CHECK (token_limit >= 0),
        cost_limit INTEGER NOT NULL CHECK (cost_limit >= 0),
        held_tokens INTEGER NOT NULL CHECK (held_tokens >= 0),
        held_cost INTEGER NOT NULL CHECK (held_cost >= 0),
        spent_tokens INTEGER NOT NULL CHECK (spent_tokens >= 0),
        spent_cost INTEGER NOT NULL CHECK (spent_cost >= 0)
    )
    """,
    """
    CREATE TABLE model_calls (
        call_id TEXT PRIMARY KEY NOT NULL,
        task_id TEXT NOT NULL,
        attempt_id TEXT NOT NULL,
        position INTEGER NOT NULL CHECK (position IN (0, 1)),
        state TEXT NOT NULL CHECK (state IN ('reserved', 'dispatched', 'generated', 'not_started', 'unknown', 'discarded')),
        provider_json TEXT NOT NULL,
        reserved_tokens INTEGER NOT NULL CHECK (reserved_tokens >= 0),
        reserved_cost INTEGER NOT NULL CHECK (reserved_cost >= 0),
        actual_tokens INTEGER CHECK (actual_tokens >= 0),
        actual_cost INTEGER CHECK (actual_cost >= 0),
        receipt_ref TEXT,
        started_at TEXT,
        finished_at TEXT,
        latency_ms REAL CHECK (latency_ms >= 0),
        UNIQUE(task_id, position),
        FOREIGN KEY(task_id) REFERENCES model_requests(task_id),
        FOREIGN KEY(attempt_id) REFERENCES execution_attempts(attempt_id)
    )
    """,
    "CREATE INDEX model_calls_attempt_idx ON model_calls(attempt_id, state)",
    """
    CREATE TABLE model_call_accounts (
        call_id TEXT NOT NULL,
        account_key TEXT NOT NULL,
        PRIMARY KEY(call_id, account_key),
        FOREIGN KEY(call_id) REFERENCES model_calls(call_id),
        FOREIGN KEY(account_key) REFERENCES model_budget_accounts(account_key)
    )
    """,
)
V10_REQUIRED_TABLES = V9_REQUIRED_TABLES.union({
    "model_requests", "model_budget_accounts", "model_calls", "model_call_accounts",
})
V10_SCHEMA_STATEMENTS = V9_SCHEMA_STATEMENTS + V10_MIGRATION_STATEMENTS
MIGRATION_V9_TO_V10_FINGERPRINT = hashlib.sha256(
    "\n".join(V10_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V11_MIGRATION_STATEMENTS = (
    """CREATE TABLE conversation_batches (
        batch_id TEXT PRIMARY KEY NOT NULL,
        origin_key TEXT NOT NULL,
        context_json TEXT NOT NULL,
        role_instruction TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('open', 'sealed', 'ready', 'cancelled', 'invalidated')),
        revision INTEGER NOT NULL CHECK(revision > 0),
        created_at TEXT NOT NULL,
        generation_task_id TEXT,
        contributions_json TEXT NOT NULL,
        call_id TEXT,
        manifest_hash TEXT,
        part_count INTEGER NOT NULL DEFAULT 0 CHECK(part_count BETWEEN 0 AND 64)
    )""",
    "CREATE INDEX conversation_batches_origin_idx ON conversation_batches(origin_key, created_at)",
    """CREATE TABLE conversation_inputs (
        event_key TEXT PRIMARY KEY NOT NULL,
        batch_id TEXT NOT NULL REFERENCES conversation_batches(batch_id),
        position INTEGER NOT NULL CHECK(position BETWEEN 1 AND 32),
        event_json TEXT NOT NULL,
        event_hash TEXT NOT NULL,
        withdrawn INTEGER NOT NULL DEFAULT 0 CHECK(withdrawn IN (0, 1)),
        UNIQUE(batch_id, position)
    )""",
    """CREATE TABLE conversation_parts (
        part_id TEXT PRIMARY KEY NOT NULL,
        batch_id TEXT NOT NULL REFERENCES conversation_batches(batch_id),
        sequence INTEGER NOT NULL CHECK(sequence BETWEEN 1 AND 64),
        text TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending', 'dispatched', 'accepted', 'delivered', 'failed', 'unknown', 'cancelled')),
        task_id TEXT UNIQUE,
        operation_id TEXT UNIQUE REFERENCES operations(operation_id),
        sender_json TEXT,
        dispatched_at TEXT,
        receipt_ref TEXT,
        platform_ref TEXT,
        delivered_at TEXT,
        UNIQUE(batch_id, sequence)
    )""",
    "CREATE INDEX conversation_parts_batch_idx ON conversation_parts(batch_id, sequence, state)",
    """CREATE TABLE conversation_delivery_receipts (
        part_id TEXT NOT NULL REFERENCES conversation_parts(part_id),
        receipt_ref TEXT NOT NULL,
        receipt_json TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        PRIMARY KEY(part_id, receipt_ref)
    )""",
    """CREATE TABLE conversation_sent_history (
        history_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        part_id TEXT NOT NULL UNIQUE REFERENCES conversation_parts(part_id),
        batch_id TEXT NOT NULL REFERENCES conversation_batches(batch_id),
        origin_key TEXT NOT NULL,
        delivered_at TEXT NOT NULL,
        receipt_ref TEXT NOT NULL
    )""",
    "CREATE INDEX conversation_sent_history_origin_idx ON conversation_sent_history(origin_key, history_sequence)",
)
V11_REQUIRED_TABLES = V10_REQUIRED_TABLES.union({
    "conversation_batches", "conversation_inputs", "conversation_parts", "conversation_delivery_receipts",
    "conversation_sent_history",
})
V11_SCHEMA_STATEMENTS = V10_SCHEMA_STATEMENTS + V11_MIGRATION_STATEMENTS
MIGRATION_V10_TO_V11_FINGERPRINT = hashlib.sha256(
    "\n".join(V11_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

# Rebuild the two related tables together; retain every original call and account.
V12_MIGRATION_STATEMENTS = (
    "CREATE TABLE model_calls_saved AS SELECT * FROM model_calls",
    "CREATE TABLE model_accounts_saved AS SELECT * FROM model_call_accounts",
    "DROP TABLE model_call_accounts",
    "DROP TABLE model_calls",
    V10_MIGRATION_STATEMENTS[2].replace("position IN (0, 1)", "position BETWEEN 0 AND 17"),
    V10_MIGRATION_STATEMENTS[3],
    V10_MIGRATION_STATEMENTS[4],
    "INSERT INTO model_calls SELECT * FROM model_calls_saved",
    "INSERT INTO model_call_accounts SELECT * FROM model_accounts_saved",
    "DROP TABLE model_accounts_saved",
    "DROP TABLE model_calls_saved",
    """CREATE TABLE model_rounds (
        task_id TEXT NOT NULL REFERENCES model_requests(task_id),
        round_no INTEGER NOT NULL CHECK(round_no BETWEEN 0 AND 8),
        attempt_id TEXT NOT NULL REFERENCES execution_attempts(attempt_id),
        request_hash TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('started', 'tools', 'final')),
        call_id TEXT REFERENCES model_calls(call_id),
        tool_calls_json TEXT NOT NULL DEFAULT '[]',
        PRIMARY KEY(task_id, round_no)
    )""",
    """CREATE TABLE conversation_tool_sources (
        batch_id TEXT NOT NULL REFERENCES conversation_batches(batch_id),
        handle TEXT NOT NULL,
        source_json TEXT NOT NULL,
        capability TEXT NOT NULL,
        PRIMARY KEY(batch_id, handle)
    )""",
    """CREATE TABLE conversation_tool_calls (
        task_id TEXT NOT NULL REFERENCES model_requests(task_id),
        tool_call_id TEXT NOT NULL,
        batch_id TEXT NOT NULL REFERENCES conversation_batches(batch_id),
        sequence INTEGER NOT NULL CHECK(sequence BETWEEN 1 AND 32),
        model_call_id TEXT NOT NULL REFERENCES model_calls(call_id),
        attempt_id TEXT NOT NULL REFERENCES execution_attempts(attempt_id),
        action TEXT NOT NULL,
        arguments_json TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('started', 'succeeded', 'failed', 'unknown')),
        effect TEXT NOT NULL CHECK(effect IN ('none', 'committed', 'not_committed', 'unknown')),
        operation_id TEXT REFERENCES operations(operation_id),
        result_json TEXT,
        created_at TEXT NOT NULL,
        finished_at TEXT,
        PRIMARY KEY(task_id, tool_call_id),
        UNIQUE(batch_id, sequence)
    )""",
    "CREATE INDEX conversation_tool_calls_batch_idx ON conversation_tool_calls(batch_id, sequence)",
)
V12_REQUIRED_TABLES = V11_REQUIRED_TABLES.union({
    "model_rounds", "conversation_tool_sources", "conversation_tool_calls",
})
V12_SCHEMA_STATEMENTS = V11_SCHEMA_STATEMENTS + V12_MIGRATION_STATEMENTS
MIGRATION_V11_TO_V12_FINGERPRINT = hashlib.sha256(
    "\n".join(V12_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V13_MIGRATION_STATEMENTS = (
    """CREATE TABLE extension_tool_invocations (
        invocation_id TEXT PRIMARY KEY NOT NULL,
        parent_task_id TEXT NOT NULL REFERENCES model_requests(task_id),
        tool_call_id TEXT NOT NULL,
        batch_id TEXT NOT NULL REFERENCES conversation_batches(batch_id),
        task_id TEXT NOT NULL UNIQUE,
        action TEXT NOT NULL,
        descriptor_json TEXT NOT NULL,
        provider_json TEXT NOT NULL,
        arguments_json TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('queued', 'running', 'succeeded', 'failed', 'unknown')),
        effect TEXT NOT NULL CHECK(effect IN ('none', 'committed', 'not_committed', 'unknown')),
        operation_id TEXT,
        receipt_ref TEXT,
        result_source_json TEXT,
        result_hash TEXT,
        error_code TEXT,
        created_at TEXT NOT NULL,
        finished_at TEXT,
        UNIQUE(parent_task_id, tool_call_id),
        UNIQUE(provider_json, operation_id),
        UNIQUE(provider_json, receipt_ref)
    )""",
    "CREATE INDEX extension_tool_invocations_task_idx ON extension_tool_invocations(task_id, state)",
    "CREATE INDEX extension_tool_invocations_batch_idx ON extension_tool_invocations(batch_id, created_at)",
)
V13_REQUIRED_TABLES = V12_REQUIRED_TABLES.union({"extension_tool_invocations"})
V13_SCHEMA_STATEMENTS = V12_SCHEMA_STATEMENTS + V13_MIGRATION_STATEMENTS
MIGRATION_V12_TO_V13_FINGERPRINT = hashlib.sha256(
    "\n".join(V13_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V14_MIGRATION_STATEMENTS = (
    """CREATE TABLE conversation_flows (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id TEXT NOT NULL UNIQUE REFERENCES conversation_batches(batch_id),
        route_key TEXT NOT NULL,
        merge_key TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('collecting', 'generating', 'delivering', 'complete', 'blocked', 'closed')),
        due_at TEXT NOT NULL,
        merge_until TEXT NOT NULL,
        deadline_at TEXT NOT NULL,
        policy_json TEXT NOT NULL,
        reason TEXT NOT NULL
    )""",
    "CREATE INDEX conversation_flows_due_idx ON conversation_flows(state, due_at, sequence)",
    "CREATE INDEX conversation_flows_route_idx ON conversation_flows(route_key, state, sequence)",
)
V14_REQUIRED_TABLES = V13_REQUIRED_TABLES.union({"conversation_flows"})
V14_SCHEMA_STATEMENTS = V13_SCHEMA_STATEMENTS + V14_MIGRATION_STATEMENTS
MIGRATION_V13_TO_V14_FINGERPRINT = hashlib.sha256(
    "\n".join(V14_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V15_MIGRATION_STATEMENTS = (
    """CREATE TABLE model_call_failures (
        call_id TEXT PRIMARY KEY NOT NULL REFERENCES model_calls(call_id),
        code TEXT NOT NULL CHECK(code IN (
            'request_preparation_failed', 'provider_request_failed', 'native_response_parse_failed',
            'provider_recheck_failed', 'role_recheck_failed', 'response_contract_rejected',
            'adapter_failed', 'provider_wait_interrupted', 'request_recheck_failed', 'runtime_interrupted'
        )),
        observed_at TEXT NOT NULL
    )""",
)
V15_REQUIRED_TABLES = V14_REQUIRED_TABLES.union({"model_call_failures"})
V15_SCHEMA_STATEMENTS = V14_SCHEMA_STATEMENTS + V15_MIGRATION_STATEMENTS
MIGRATION_V14_TO_V15_FINGERPRINT = hashlib.sha256(
    "\n".join(V15_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V16_MIGRATION_STATEMENTS = (
    """CREATE TABLE role_activities (
        owner_key TEXT NOT NULL,
        activity_id TEXT NOT NULL,
        owner_json TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision > 0),
        state TEXT NOT NULL CHECK(state IN ('planned', 'active', 'paused', 'completed', 'cancelled')),
        updated_at TEXT NOT NULL,
        record_json TEXT NOT NULL,
        PRIMARY KEY(owner_key, activity_id)
    )""",
    "CREATE INDEX role_activities_state_idx ON role_activities(owner_key, state, activity_id)",
    """CREATE TABLE role_artifacts (
        owner_key TEXT NOT NULL,
        artifact_id TEXT NOT NULL,
        activity_id TEXT NOT NULL,
        scope_key TEXT NOT NULL,
        record_json TEXT NOT NULL,
        PRIMARY KEY(owner_key, artifact_id),
        FOREIGN KEY(owner_key, activity_id) REFERENCES role_activities(owner_key, activity_id)
    )""",
    "CREATE INDEX role_artifacts_scope_idx ON role_artifacts(owner_key, scope_key, artifact_id)",
    """CREATE TABLE role_life_operations (
        operation_id TEXT PRIMARY KEY NOT NULL REFERENCES operations(operation_id),
        owner_key TEXT NOT NULL,
        result_json TEXT NOT NULL
    )""",
)
V16_REQUIRED_TABLES = V15_REQUIRED_TABLES.union({"role_activities", "role_artifacts", "role_life_operations"})
V16_SCHEMA_STATEMENTS = V15_SCHEMA_STATEMENTS + V16_MIGRATION_STATEMENTS
MIGRATION_V15_TO_V16_FINGERPRINT = hashlib.sha256(
    "\n".join(V16_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V17_MIGRATION_STATEMENTS = (
    """CREATE TABLE role_journals (
        entry_key TEXT PRIMARY KEY NOT NULL,
        owner_key TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('diary', 'dream')),
        local_date TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision > 0),
        state TEXT NOT NULL CHECK(state IN ('active', 'deleted')),
        context_json TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        content_json TEXT,
        UNIQUE(owner_key, kind, local_date),
        CHECK((state = 'active' AND content_json IS NOT NULL) OR (state = 'deleted' AND content_json IS NULL))
    )""",
    """CREATE TABLE role_journal_derivatives (
        entry_key TEXT PRIMARY KEY NOT NULL REFERENCES role_journals(entry_key),
        entry_revision INTEGER NOT NULL CHECK(entry_revision > 0),
        revision INTEGER NOT NULL CHECK(revision > 0),
        record_json TEXT NOT NULL
    )""",
    """CREATE TABLE role_journal_operations (
        operation_id TEXT PRIMARY KEY NOT NULL REFERENCES operations(operation_id),
        owner_key TEXT NOT NULL,
        result_json TEXT NOT NULL
    )""",
)
V17_REQUIRED_TABLES = V16_REQUIRED_TABLES.union({"role_journals", "role_journal_derivatives", "role_journal_operations"})
V17_SCHEMA_STATEMENTS = V16_SCHEMA_STATEMENTS + V17_MIGRATION_STATEMENTS
MIGRATION_V16_TO_V17_FINGERPRINT = hashlib.sha256(
    "\n".join(V17_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V18_MIGRATION_STATEMENTS = (
    """CREATE TABLE relationship_memories (
        owner_key TEXT NOT NULL,
        memory_id TEXT NOT NULL,
        owner_json TEXT NOT NULL,
        subject_json TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision > 0),
        kind TEXT NOT NULL CHECK(kind IN ('context', 'emotion_episode')),
        state TEXT NOT NULL CHECK(state IN ('proposed', 'reviewed', 'adopted', 'retracted')),
        updated_at TEXT NOT NULL,
        record_json TEXT NOT NULL,
        PRIMARY KEY(owner_key, memory_id)
    )""",
    "CREATE INDEX relationship_memories_subject_state_idx ON relationship_memories(owner_key, state, kind, memory_id)",
    """CREATE TABLE relationship_memory_revisions (
        owner_key TEXT NOT NULL,
        memory_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision > 0),
        record_json TEXT NOT NULL,
        PRIMARY KEY(owner_key, memory_id, revision),
        FOREIGN KEY(owner_key, memory_id) REFERENCES relationship_memories(owner_key, memory_id)
    )""",
    """CREATE TABLE relationship_memory_operations (
        operation_id TEXT PRIMARY KEY NOT NULL REFERENCES operations(operation_id),
        owner_key TEXT NOT NULL,
        memory_id TEXT NOT NULL,
        result_json TEXT NOT NULL,
        FOREIGN KEY(owner_key, memory_id) REFERENCES relationship_memories(owner_key, memory_id)
    )""",
)
V18_REQUIRED_TABLES = V17_REQUIRED_TABLES.union({
    "relationship_memories", "relationship_memory_revisions", "relationship_memory_operations",
})
V18_SCHEMA_STATEMENTS = V17_SCHEMA_STATEMENTS + V18_MIGRATION_STATEMENTS
MIGRATION_V17_TO_V18_FINGERPRINT = hashlib.sha256(
    "\n".join(V18_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()


V19_MIGRATION_STATEMENTS = (
    """CREATE TABLE background_deliveries (
        delivery_id TEXT PRIMARY KEY NOT NULL,
        owner TEXT NOT NULL,
        origin_key TEXT NOT NULL,
        request_json TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        sender_json TEXT NOT NULL,
        task_id TEXT NOT NULL UNIQUE,
        state TEXT NOT NULL CHECK(state IN ('pending', 'dispatched', 'accepted', 'delivered', 'failed', 'unknown', 'cancelled')),
        operation_id TEXT,
        dispatched_at TEXT,
        receipt_ref TEXT,
        platform_ref TEXT,
        delivered_at TEXT,
        reason TEXT NOT NULL DEFAULT ''
    )""",
    "CREATE INDEX background_deliveries_state_idx ON background_deliveries(state, delivery_id)",
    """CREATE TABLE background_delivery_receipts (
        delivery_id TEXT NOT NULL REFERENCES background_deliveries(delivery_id),
        receipt_ref TEXT NOT NULL,
        receipt_json TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        PRIMARY KEY(delivery_id, receipt_ref)
    )""",
    """CREATE TABLE agenda_dispatches (
        occurrence_id TEXT PRIMARY KEY NOT NULL REFERENCES agenda_occurrences(occurrence_id),
        occurrence_revision INTEGER NOT NULL CHECK(occurrence_revision > 0),
        state TEXT NOT NULL CHECK(state IN ('queued', 'skipped', 'blocked')),
        delivery_id TEXT REFERENCES background_deliveries(delivery_id),
        reason TEXT NOT NULL,
        next_sequence INTEGER,
        observed_at TEXT NOT NULL,
        CHECK((state = 'queued' AND delivery_id IS NOT NULL) OR (state != 'queued' AND delivery_id IS NULL))
    )""",
)
V19_REQUIRED_TABLES = V18_REQUIRED_TABLES.union({
    "background_deliveries", "background_delivery_receipts", "agenda_dispatches",
})
V19_SCHEMA_STATEMENTS = V18_SCHEMA_STATEMENTS + V19_MIGRATION_STATEMENTS
MIGRATION_V18_TO_V19_FINGERPRINT = hashlib.sha256(
    "\n".join(V19_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

# v20 is already used by proactive contact persistence; memory is an additive v21 migration.
V20_MIGRATION_STATEMENTS = (
    """CREATE TABLE proactive_contacts (
        contact_id TEXT PRIMARY KEY NOT NULL,
        task_id TEXT NOT NULL UNIQUE,
        origin_key TEXT NOT NULL,
        record_json TEXT NOT NULL,
        body TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        operation_id TEXT NOT NULL UNIQUE REFERENCES operations(operation_id),
        created_at TEXT NOT NULL
    )""",
)
V20_REQUIRED_TABLES = V19_REQUIRED_TABLES.union({"proactive_contacts"})
V20_SCHEMA_STATEMENTS = V19_SCHEMA_STATEMENTS + V20_MIGRATION_STATEMENTS

# Memory facts use the same transactional store; retrieval remains rebuildable.
from .memory_schema import MEMORY_STATEMENTS, MEMORY_TABLES

V21_MIGRATION_STATEMENTS = MEMORY_STATEMENTS
V21_REQUIRED_TABLES = V20_REQUIRED_TABLES.union(MEMORY_TABLES)
V21_SCHEMA_STATEMENTS = V20_SCHEMA_STATEMENTS + V21_MIGRATION_STATEMENTS
MIGRATION_V20_TO_V21_FINGERPRINT = hashlib.sha256(
    "\n".join(V21_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V22_MIGRATION_STATEMENTS = (
    """CREATE TABLE role_growth_items (
        owner_key TEXT NOT NULL,
        growth_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision > 0),
        kind TEXT NOT NULL CHECK(kind IN ('skill', 'goal')),
        state TEXT NOT NULL CHECK(state IN ('proposed', 'active', 'paused', 'achieved', 'retired')),
        updated_at TEXT NOT NULL,
        record_json TEXT NOT NULL,
        PRIMARY KEY(owner_key, growth_id)
    )""",
    "CREATE INDEX role_growth_state_idx ON role_growth_items(owner_key, kind, state, growth_id)",
    """CREATE TABLE role_growth_revisions (
        owner_key TEXT NOT NULL,
        growth_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision > 0),
        record_json TEXT NOT NULL,
        PRIMARY KEY(owner_key, growth_id, revision),
        FOREIGN KEY(owner_key, growth_id) REFERENCES role_growth_items(owner_key, growth_id)
    )""",
    """CREATE TABLE role_growth_operations (
        operation_id TEXT PRIMARY KEY NOT NULL REFERENCES operations(operation_id),
        owner_key TEXT NOT NULL,
        growth_id TEXT NOT NULL,
        result_json TEXT NOT NULL,
        FOREIGN KEY(owner_key, growth_id) REFERENCES role_growth_items(owner_key, growth_id)
    )""",
)
V22_REQUIRED_TABLES = V21_REQUIRED_TABLES.union({
    "role_growth_items", "role_growth_revisions", "role_growth_operations",
})
V22_SCHEMA_STATEMENTS = V21_SCHEMA_STATEMENTS + V22_MIGRATION_STATEMENTS
MIGRATION_V21_TO_V22_FINGERPRINT = hashlib.sha256(
    "\n".join(V22_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()
MIGRATION_V19_TO_V20_FINGERPRINT = hashlib.sha256(
    "\n".join(V20_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V23_MIGRATION_STATEMENTS = (
    """CREATE TABLE role_activity_revisions (
        owner_key TEXT NOT NULL,
        activity_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision > 0),
        updated_at TEXT NOT NULL,
        provenance TEXT NOT NULL CHECK(provenance IN ('recorded', 'legacy_receipt', 'legacy_head')),
        operation_id TEXT REFERENCES operations(operation_id),
        record_json TEXT NOT NULL,
        PRIMARY KEY(owner_key, activity_id, revision),
        FOREIGN KEY(owner_key, activity_id) REFERENCES role_activities(owner_key, activity_id)
    )""",
    "CREATE INDEX role_activity_history_time_idx ON role_activity_revisions(owner_key, updated_at, activity_id, revision)",
    "CREATE INDEX role_growth_history_time_idx ON role_growth_revisions(owner_key, json_extract(record_json, '$.updated_at'), growth_id, revision)",
    "CREATE INDEX role_journal_history_time_idx ON role_journal_operations(owner_key, json_extract(result_json, '$.head.updated_at'), operation_id)",
)
V23_REQUIRED_TABLES = V22_REQUIRED_TABLES.union({"role_activity_revisions"})
V23_SCHEMA_STATEMENTS = V22_SCHEMA_STATEMENTS + V23_MIGRATION_STATEMENTS
REQUIRED_TABLES = V23_REQUIRED_TABLES
SCHEMA_STATEMENTS = V23_SCHEMA_STATEMENTS
MIGRATION_V22_TO_V23_FINGERPRINT = hashlib.sha256(
    ("\n".join(V23_MIGRATION_STATEMENTS) + "\nbackfill:existing-activity-heads-and-completed-receipts.v1").encode("utf-8")
).hexdigest()

from .state_schema import STATE_STATEMENTS, STATE_TABLES

V24_MIGRATION_STATEMENTS = STATE_STATEMENTS
V24_REQUIRED_TABLES = V23_REQUIRED_TABLES.union(STATE_TABLES)
V24_SCHEMA_STATEMENTS = V23_SCHEMA_STATEMENTS + V24_MIGRATION_STATEMENTS
REQUIRED_TABLES = V24_REQUIRED_TABLES
SCHEMA_STATEMENTS = V24_SCHEMA_STATEMENTS
MIGRATION_V23_TO_V24_FINGERPRINT = hashlib.sha256(
    "\n".join(V24_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

from .media_schema import MEDIA_STATEMENTS, MEDIA_TABLES

V25_MIGRATION_STATEMENTS = MEDIA_STATEMENTS
V25_REQUIRED_TABLES = V24_REQUIRED_TABLES.union(MEDIA_TABLES)
V25_SCHEMA_STATEMENTS = V24_SCHEMA_STATEMENTS + V25_MIGRATION_STATEMENTS
REQUIRED_TABLES = V25_REQUIRED_TABLES
SCHEMA_STATEMENTS = V25_SCHEMA_STATEMENTS
MIGRATION_V24_TO_V25_FINGERPRINT = hashlib.sha256(
    "\n".join(V25_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

from .memory_recall_schema import RECALL_STATEMENTS, RECALL_TABLES

V26_MIGRATION_STATEMENTS = RECALL_STATEMENTS
V26_REQUIRED_TABLES = V25_REQUIRED_TABLES.union(RECALL_TABLES)
V26_SCHEMA_STATEMENTS = V25_SCHEMA_STATEMENTS + V26_MIGRATION_STATEMENTS
REQUIRED_TABLES = V26_REQUIRED_TABLES
SCHEMA_STATEMENTS = V26_SCHEMA_STATEMENTS
MIGRATION_V25_TO_V26_FINGERPRINT = hashlib.sha256(
    "\n".join(V26_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

from .reflection_schema import REFLECTION_INPUT_STATEMENTS, REFLECTION_INPUT_TABLES

V27_MIGRATION_STATEMENTS = REFLECTION_INPUT_STATEMENTS
V27_REQUIRED_TABLES = V26_REQUIRED_TABLES.union(REFLECTION_INPUT_TABLES)
V27_SCHEMA_STATEMENTS = V26_SCHEMA_STATEMENTS + V27_MIGRATION_STATEMENTS
REQUIRED_TABLES = V27_REQUIRED_TABLES
SCHEMA_STATEMENTS = V27_SCHEMA_STATEMENTS
MIGRATION_V26_TO_V27_FINGERPRINT = hashlib.sha256(
    "\n".join(V27_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

from .reflection_job_schema import REFLECTION_JOB_STATEMENTS, REFLECTION_JOB_TABLES

V28_MIGRATION_STATEMENTS = REFLECTION_JOB_STATEMENTS
V28_REQUIRED_TABLES = V27_REQUIRED_TABLES.union(REFLECTION_JOB_TABLES)
V28_SCHEMA_STATEMENTS = V27_SCHEMA_STATEMENTS + V28_MIGRATION_STATEMENTS
REQUIRED_TABLES = V28_REQUIRED_TABLES
SCHEMA_STATEMENTS = V28_SCHEMA_STATEMENTS
MIGRATION_V27_TO_V28_FINGERPRINT = hashlib.sha256(
    "\n".join(V28_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

from .audio_schema import AUDIO_STATEMENTS, AUDIO_TABLES

V29_MIGRATION_STATEMENTS = AUDIO_STATEMENTS
V29_REQUIRED_TABLES = V28_REQUIRED_TABLES.union(AUDIO_TABLES)
V29_SCHEMA_STATEMENTS = V28_SCHEMA_STATEMENTS + V29_MIGRATION_STATEMENTS
REQUIRED_TABLES = V29_REQUIRED_TABLES
SCHEMA_STATEMENTS = V29_SCHEMA_STATEMENTS
MIGRATION_V28_TO_V29_FINGERPRINT = hashlib.sha256(
    "\n".join(V29_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

from .video_schema import VIDEO_STATEMENTS, VIDEO_TABLES

V30_MIGRATION_STATEMENTS = VIDEO_STATEMENTS
V30_REQUIRED_TABLES = V29_REQUIRED_TABLES.union(VIDEO_TABLES)
V30_SCHEMA_STATEMENTS = V29_SCHEMA_STATEMENTS + V30_MIGRATION_STATEMENTS
REQUIRED_TABLES = V30_REQUIRED_TABLES
SCHEMA_STATEMENTS = V30_SCHEMA_STATEMENTS
MIGRATION_V29_TO_V30_FINGERPRINT = hashlib.sha256(
    "\n".join(V30_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

from .growth_progress_schema import PROGRESS_STATEMENTS, PROGRESS_TABLES

V31_MIGRATION_STATEMENTS = PROGRESS_STATEMENTS
V31_REQUIRED_TABLES = V30_REQUIRED_TABLES.union(PROGRESS_TABLES)
V31_SCHEMA_STATEMENTS = V30_SCHEMA_STATEMENTS + V31_MIGRATION_STATEMENTS
REQUIRED_TABLES = V31_REQUIRED_TABLES
SCHEMA_STATEMENTS = V31_SCHEMA_STATEMENTS
MIGRATION_V30_TO_V31_FINGERPRINT = hashlib.sha256(
    "\n".join(V31_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V32_MIGRATION_STATEMENTS = (
    """CREATE TABLE conversation_voice_parts (
        part_id TEXT PRIMARY KEY NOT NULL REFERENCES conversation_parts(part_id),
        speech TEXT NOT NULL,
        provider_id TEXT NOT NULL,
        delivery_mode TEXT NOT NULL CHECK(delivery_mode IN ('voice_and_text', 'voice_only')),
        position TEXT NOT NULL CHECK(position IN ('after_media', 'after_text', 'before_text')),
        timeout_seconds INTEGER NOT NULL CHECK(timeout_seconds BETWEEN 1 AND 120),
        cooldown_seconds INTEGER NOT NULL CHECK(cooldown_seconds BETWEEN 0 AND 86400)
    )""",
    """CREATE TABLE conversation_voice_cooldowns (
        route_key TEXT PRIMARY KEY NOT NULL,
        part_id TEXT NOT NULL,
        until_at TEXT NOT NULL
    )""",
)
V32_REQUIRED_TABLES = V31_REQUIRED_TABLES.union({"conversation_voice_parts", "conversation_voice_cooldowns"})
V32_SCHEMA_STATEMENTS = V31_SCHEMA_STATEMENTS + V32_MIGRATION_STATEMENTS
REQUIRED_TABLES = V32_REQUIRED_TABLES
SCHEMA_STATEMENTS = V32_SCHEMA_STATEMENTS
MIGRATION_V31_TO_V32_FINGERPRINT = hashlib.sha256(
    "\n".join(V32_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V33_MIGRATION_STATEMENTS = (
    """CREATE TABLE proactive_weather_samples (
        origin_key TEXT PRIMARY KEY NOT NULL,
        location_hash TEXT NOT NULL,
        sample_json TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        checked_at TEXT NOT NULL,
        event_json TEXT,
        last_event_at TEXT
    )""",
)
V33_REQUIRED_TABLES = V32_REQUIRED_TABLES.union({"proactive_weather_samples"})
V33_SCHEMA_STATEMENTS = V32_SCHEMA_STATEMENTS + V33_MIGRATION_STATEMENTS
REQUIRED_TABLES = V33_REQUIRED_TABLES
SCHEMA_STATEMENTS = V33_SCHEMA_STATEMENTS
MIGRATION_V32_TO_V33_FINGERPRINT = hashlib.sha256(
    "\n".join(V33_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V34_MIGRATION_STATEMENTS = (
    """CREATE TABLE proactive_official_alert_polls (
        origin_key TEXT PRIMARY KEY NOT NULL,
        location_hash TEXT NOT NULL,
        checked_at TEXT NOT NULL
    )""",
    """CREATE TABLE proactive_official_alerts (
        origin_key TEXT NOT NULL,
        location_hash TEXT NOT NULL,
        alert_id TEXT NOT NULL,
        chain_id TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('current', 'retired')),
        alert_json TEXT NOT NULL,
        attributions_json TEXT NOT NULL,
        queried_at TEXT NOT NULL,
        PRIMARY KEY(origin_key, location_hash, alert_id)
    )""",
    "CREATE INDEX proactive_official_alert_chain_idx ON proactive_official_alerts(origin_key, location_hash, chain_id, state)",
)
V34_REQUIRED_TABLES = V33_REQUIRED_TABLES.union({"proactive_official_alert_polls", "proactive_official_alerts"})
V34_SCHEMA_STATEMENTS = V33_SCHEMA_STATEMENTS + V34_MIGRATION_STATEMENTS
REQUIRED_TABLES = V34_REQUIRED_TABLES
SCHEMA_STATEMENTS = V34_SCHEMA_STATEMENTS
MIGRATION_V33_TO_V34_FINGERPRINT = hashlib.sha256(
    "\n".join(V34_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V35_MIGRATION_STATEMENTS = (
    """CREATE TABLE conversation_voice_diagnostics (
        part_id TEXT PRIMARY KEY NOT NULL REFERENCES conversation_parts(part_id),
        candidate_reason TEXT NOT NULL CHECK(candidate_reason IN
            ('planned', 'voice_disabled', 'trigger_skipped', 'segment_skipped',
             'no_speech', 'too_long', 'empty_part')),
        render_state TEXT NOT NULL CHECK(render_state IN
            ('pending', 'not_applicable', 'cooldown', 'unavailable', 'failed',
             'empty', 'invalid', 'ready')),
        component_order_json TEXT NOT NULL,
        text_hidden INTEGER NOT NULL CHECK(text_hidden IN (0, 1))
    )""",
)
V35_REQUIRED_TABLES = V34_REQUIRED_TABLES.union({"conversation_voice_diagnostics"})
V35_SCHEMA_STATEMENTS = V34_SCHEMA_STATEMENTS + V35_MIGRATION_STATEMENTS
REQUIRED_TABLES = V35_REQUIRED_TABLES
SCHEMA_STATEMENTS = V35_SCHEMA_STATEMENTS
MIGRATION_V34_TO_V35_FINGERPRINT = hashlib.sha256(
    "\n".join(V35_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V36_MIGRATION_STATEMENTS = (
    """CREATE TABLE model_call_failures_v36 (
        call_id TEXT PRIMARY KEY NOT NULL REFERENCES model_calls(call_id),
        code TEXT NOT NULL CHECK(code IN (
            'request_preparation_failed', 'provider_request_failed', 'native_response_parse_failed',
            'provider_recheck_failed', 'role_recheck_failed', 'response_contract_rejected',
            'adapter_failed', 'provider_wait_interrupted', 'request_recheck_failed', 'runtime_interrupted',
            'provider_refused'
        )),
        observed_at TEXT NOT NULL
    )""",
    """INSERT INTO model_call_failures_v36(call_id, code, observed_at)
        SELECT call_id, code, observed_at FROM model_call_failures""",
    "DROP TABLE model_call_failures",
    "ALTER TABLE model_call_failures_v36 RENAME TO model_call_failures",
)
V36_REQUIRED_TABLES = V35_REQUIRED_TABLES
V36_SCHEMA_STATEMENTS = V35_SCHEMA_STATEMENTS + V36_MIGRATION_STATEMENTS
REQUIRED_TABLES = V36_REQUIRED_TABLES
SCHEMA_STATEMENTS = V36_SCHEMA_STATEMENTS
MIGRATION_V35_TO_V36_FINGERPRINT = hashlib.sha256(
    "\n".join(V36_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V37_MIGRATION_STATEMENTS = (
    """CREATE TABLE proactive_balance_polls (
        origin_key TEXT PRIMARY KEY NOT NULL,
        settings_hash TEXT NOT NULL,
        checked_at TEXT NOT NULL,
        level TEXT NOT NULL CHECK(level IN ('normal', 'low', 'critical')),
        last_notice_at TEXT,
        last_notice_level TEXT CHECK(last_notice_level IN ('low', 'critical')),
        event_json TEXT
    )""",
)
V37_REQUIRED_TABLES = V36_REQUIRED_TABLES.union({"proactive_balance_polls"})
V37_SCHEMA_STATEMENTS = V36_SCHEMA_STATEMENTS + V37_MIGRATION_STATEMENTS
REQUIRED_TABLES = V37_REQUIRED_TABLES
SCHEMA_STATEMENTS = V37_SCHEMA_STATEMENTS
MIGRATION_V36_TO_V37_FINGERPRINT = hashlib.sha256(
    "\n".join(V37_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V38_MIGRATION_STATEMENTS = (
    """CREATE TABLE proactive_news_polls (
        origin_key TEXT PRIMARY KEY NOT NULL,
        settings_hash TEXT NOT NULL,
        checked_at TEXT,
        retry_after TEXT,
        seen_json TEXT NOT NULL,
        event_json TEXT
    )""",
)
V38_REQUIRED_TABLES = V37_REQUIRED_TABLES.union({"proactive_news_polls"})
V38_SCHEMA_STATEMENTS = V37_SCHEMA_STATEMENTS + V38_MIGRATION_STATEMENTS
REQUIRED_TABLES = V38_REQUIRED_TABLES
SCHEMA_STATEMENTS = V38_SCHEMA_STATEMENTS
MIGRATION_V37_TO_V38_FINGERPRINT = hashlib.sha256(
    "\n".join(V38_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

from .self_review_schema import SELF_REVIEW_STATEMENTS, SELF_REVIEW_TABLES

V39_MIGRATION_STATEMENTS = SELF_REVIEW_STATEMENTS
V39_REQUIRED_TABLES = V38_REQUIRED_TABLES.union(SELF_REVIEW_TABLES)
V39_SCHEMA_STATEMENTS = V38_SCHEMA_STATEMENTS + V39_MIGRATION_STATEMENTS
REQUIRED_TABLES = V39_REQUIRED_TABLES
SCHEMA_STATEMENTS = V39_SCHEMA_STATEMENTS
MIGRATION_V38_TO_V39_FINGERPRINT = hashlib.sha256(
    "\n".join(V39_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

from .expression_style_schema import EXPRESSION_STATEMENTS, EXPRESSION_TABLES

V40_MIGRATION_STATEMENTS = EXPRESSION_STATEMENTS
V40_REQUIRED_TABLES = V39_REQUIRED_TABLES.union(EXPRESSION_TABLES)
V40_SCHEMA_STATEMENTS = V39_SCHEMA_STATEMENTS + V40_MIGRATION_STATEMENTS
REQUIRED_TABLES = V40_REQUIRED_TABLES
SCHEMA_STATEMENTS = V40_SCHEMA_STATEMENTS
MIGRATION_V39_TO_V40_FINGERPRINT = hashlib.sha256(
    "\n".join(V40_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

from .group_safety_schema import GROUP_SAFETY_STATEMENTS, GROUP_SAFETY_TABLES

V41_MIGRATION_STATEMENTS = GROUP_SAFETY_STATEMENTS
V41_REQUIRED_TABLES = V40_REQUIRED_TABLES.union(GROUP_SAFETY_TABLES)
V41_SCHEMA_STATEMENTS = V40_SCHEMA_STATEMENTS + V41_MIGRATION_STATEMENTS
REQUIRED_TABLES = V41_REQUIRED_TABLES
SCHEMA_STATEMENTS = V41_SCHEMA_STATEMENTS
SCHEMA_VERSION_V41 = "companion-v2.storage.v41"
SCHEMA_VERSION = SCHEMA_VERSION_V41
MIGRATION_V40_TO_V41_ID = "companion-v2.storage.v40-to-v41"
MIGRATION_V40_TO_V41_FINGERPRINT = hashlib.sha256(
    "\n".join(V41_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

from .reading_archive_schema import READING_ARCHIVE_STATEMENTS, READING_ARCHIVE_TABLES

V42_MIGRATION_STATEMENTS = READING_ARCHIVE_STATEMENTS
V42_REQUIRED_TABLES = V41_REQUIRED_TABLES.union(READING_ARCHIVE_TABLES)
V42_SCHEMA_STATEMENTS = V41_SCHEMA_STATEMENTS + V42_MIGRATION_STATEMENTS
REQUIRED_TABLES = V42_REQUIRED_TABLES
SCHEMA_STATEMENTS = V42_SCHEMA_STATEMENTS
SCHEMA_VERSION_V42 = "companion-v2.storage.v42"
SCHEMA_VERSION = SCHEMA_VERSION_V42
MIGRATION_V41_TO_V42_ID = "companion-v2.storage.v41-to-v42"
MIGRATION_V41_TO_V42_FINGERPRINT = hashlib.sha256(
    "\n".join(V42_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

from .reference_asset_schema import REFERENCE_ASSET_STATEMENTS, REFERENCE_ASSET_TABLES

V43_MIGRATION_STATEMENTS = REFERENCE_ASSET_STATEMENTS
V43_REQUIRED_TABLES = V42_REQUIRED_TABLES.union(REFERENCE_ASSET_TABLES)
V43_SCHEMA_STATEMENTS = V42_SCHEMA_STATEMENTS + V43_MIGRATION_STATEMENTS
REQUIRED_TABLES = V43_REQUIRED_TABLES
SCHEMA_STATEMENTS = V43_SCHEMA_STATEMENTS
SCHEMA_VERSION_V43 = "companion-v2.storage.v43"
SCHEMA_VERSION = SCHEMA_VERSION_V43
MIGRATION_V42_TO_V43_ID = "companion-v2.storage.v42-to-v43"
MIGRATION_V42_TO_V43_FINGERPRINT = hashlib.sha256(
    "\n".join(V43_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

from .reading_archive_schema import (READING_ARCHIVE_TRIGGER_STATEMENTS,
                                     READING_ARCHIVE_TRIGGER_TABLES)

V44_MIGRATION_STATEMENTS = READING_ARCHIVE_TRIGGER_STATEMENTS
V44_REQUIRED_TABLES = V43_REQUIRED_TABLES.union(READING_ARCHIVE_TRIGGER_TABLES)
V44_SCHEMA_STATEMENTS = V43_SCHEMA_STATEMENTS + V44_MIGRATION_STATEMENTS
REQUIRED_TABLES = V44_REQUIRED_TABLES
SCHEMA_STATEMENTS = V44_SCHEMA_STATEMENTS
SCHEMA_VERSION_V44 = "companion-v2.storage.v44"
SCHEMA_VERSION = SCHEMA_VERSION_V44
MIGRATION_V43_TO_V44_ID = "companion-v2.storage.v43-to-v44"
MIGRATION_V43_TO_V44_FINGERPRINT = hashlib.sha256(
    "\n".join(V44_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

from .persona_craft_schema import (PERSONA_CRAFT_STATEMENTS, PERSONA_CRAFT_TABLES,
                                   PERSONA_CRAFT_TRIGGERS)

V45_MIGRATION_STATEMENTS = PERSONA_CRAFT_STATEMENTS
V45_REQUIRED_TABLES = V44_REQUIRED_TABLES.union(PERSONA_CRAFT_TABLES)
V45_SCHEMA_STATEMENTS = V44_SCHEMA_STATEMENTS + V45_MIGRATION_STATEMENTS
REQUIRED_TABLES = V45_REQUIRED_TABLES
SCHEMA_STATEMENTS = V45_SCHEMA_STATEMENTS
SCHEMA_VERSION_V45 = "companion-v2.storage.v45"
SCHEMA_VERSION = SCHEMA_VERSION_V45
MIGRATION_V44_TO_V45_ID = "companion-v2.storage.v44-to-v45"
MIGRATION_V44_TO_V45_FINGERPRINT = hashlib.sha256(
    "\n".join(V45_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V46_MIGRATION_STATEMENTS = (
    "ALTER TABLE model_calls ADD COLUMN input_tokens INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0)",
    "ALTER TABLE model_calls ADD COLUMN cached_input_tokens INTEGER CHECK (cached_input_tokens IS NULL OR cached_input_tokens >= 0)",
    "ALTER TABLE model_calls ADD COLUMN cache_creation_tokens INTEGER CHECK (cache_creation_tokens IS NULL OR cache_creation_tokens >= 0)",
    "ALTER TABLE model_calls ADD COLUMN output_tokens INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0)",
)
V46_REQUIRED_TABLES = V45_REQUIRED_TABLES
V46_SCHEMA_STATEMENTS = V45_SCHEMA_STATEMENTS + V46_MIGRATION_STATEMENTS
REQUIRED_TABLES = V46_REQUIRED_TABLES
SCHEMA_STATEMENTS = V46_SCHEMA_STATEMENTS
SCHEMA_VERSION_V46 = "companion-v2.storage.v46"
SCHEMA_VERSION = SCHEMA_VERSION_V46
MIGRATION_V45_TO_V46_ID = "companion-v2.storage.v45-to-v46"
MIGRATION_V45_TO_V46_FINGERPRINT = hashlib.sha256(
    "\n".join(V46_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V47_MIGRATION_STATEMENTS = (
    """CREATE TABLE reference_asset_payloads (
        owner_key TEXT NOT NULL,
        asset_id TEXT NOT NULL,
        content_hash TEXT NOT NULL CHECK(length(content_hash) = 64),
        size_bytes INTEGER NOT NULL CHECK(size_bytes > 0 AND size_bytes <= 20971520),
        mime_type TEXT NOT NULL,
        content BLOB NOT NULL CHECK(length(content) = size_bytes),
        created_at TEXT NOT NULL,
        PRIMARY KEY(owner_key, asset_id),
        FOREIGN KEY(owner_key, asset_id) REFERENCES reference_assets(owner_key, asset_id)
    )""",
    "CREATE INDEX reference_asset_payload_owner_idx ON reference_asset_payloads(owner_key, asset_id)",
    """CREATE TABLE wardrobe_daily_outfits (
        owner_key TEXT NOT NULL,
        local_date TEXT NOT NULL,
        item_id TEXT NOT NULL,
        item_revision INTEGER NOT NULL CHECK(item_revision > 0),
        created_at TEXT NOT NULL,
        PRIMARY KEY(owner_key, local_date),
        FOREIGN KEY(owner_key, item_id) REFERENCES wardrobe_items(owner_key, item_id)
    )""",
    "CREATE INDEX wardrobe_daily_outfits_recent_idx ON wardrobe_daily_outfits(owner_key, local_date DESC)",
)
V47_REQUIRED_TABLES = V46_REQUIRED_TABLES.union({"reference_asset_payloads", "wardrobe_daily_outfits"})
V47_SCHEMA_STATEMENTS = V46_SCHEMA_STATEMENTS + V47_MIGRATION_STATEMENTS
REQUIRED_TABLES = V47_REQUIRED_TABLES
SCHEMA_STATEMENTS = V47_SCHEMA_STATEMENTS
SCHEMA_VERSION_V47 = "companion-v2.storage.v47"
SCHEMA_VERSION = SCHEMA_VERSION_V47
MIGRATION_V46_TO_V47_ID = "companion-v2.storage.v46-to-v47"
MIGRATION_V46_TO_V47_FINGERPRINT = hashlib.sha256(
    "\n".join(V47_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V48_MIGRATION_STATEMENTS = (
    """CREATE TABLE model_call_cache_signatures (
        call_id TEXT PRIMARY KEY NOT NULL,
        signature_json TEXT NOT NULL CHECK(json_valid(signature_json) AND length(signature_json) <= 32768),
        FOREIGN KEY(call_id) REFERENCES model_calls(call_id)
    )""",
)
V48_REQUIRED_TABLES = V47_REQUIRED_TABLES.union({"model_call_cache_signatures"})
V48_SCHEMA_STATEMENTS = V47_SCHEMA_STATEMENTS + V48_MIGRATION_STATEMENTS
REQUIRED_TABLES = V48_REQUIRED_TABLES
SCHEMA_STATEMENTS = V48_SCHEMA_STATEMENTS
SCHEMA_VERSION_V48 = "companion-v2.storage.v48"
SCHEMA_VERSION = SCHEMA_VERSION_V48
MIGRATION_V47_TO_V48_ID = "companion-v2.storage.v47-to-v48"
MIGRATION_V47_TO_V48_FINGERPRINT = hashlib.sha256(
    "\n".join(V48_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V49_MIGRATION_STATEMENTS = (
    """CREATE TABLE inbound_no_replies (
        event_ref TEXT PRIMARY KEY NOT NULL CHECK(length(event_ref) = 32),
        origin_key TEXT,
        channel TEXT NOT NULL CHECK(channel IN ('private', 'group', 'unknown')),
        reason_code TEXT NOT NULL CHECK(length(reason_code) BETWEEN 1 AND 96),
        observed_at TEXT NOT NULL
    )""",
    "CREATE INDEX inbound_no_replies_role_recent_idx ON inbound_no_replies(origin_key, observed_at DESC)",
)
V49_REQUIRED_TABLES = V48_REQUIRED_TABLES.union({"inbound_no_replies"})
V49_SCHEMA_STATEMENTS = V48_SCHEMA_STATEMENTS + V49_MIGRATION_STATEMENTS
REQUIRED_TABLES = V49_REQUIRED_TABLES
SCHEMA_STATEMENTS = V49_SCHEMA_STATEMENTS
SCHEMA_VERSION_V49 = "companion-v2.storage.v49"
SCHEMA_VERSION = SCHEMA_VERSION_V49
MIGRATION_V48_TO_V49_ID = "companion-v2.storage.v48-to-v49"
MIGRATION_V48_TO_V49_FINGERPRINT = hashlib.sha256(
    "\n".join(V49_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V50_MIGRATION_STATEMENTS = (
    "ALTER TABLE inbound_no_replies ADD COLUMN code TEXT CHECK(code IS NULL OR length(code) BETWEEN 1 AND 96)",
)
V50_REQUIRED_TABLES = V49_REQUIRED_TABLES
V50_SCHEMA_STATEMENTS = V49_SCHEMA_STATEMENTS + V50_MIGRATION_STATEMENTS
REQUIRED_TABLES = V50_REQUIRED_TABLES
SCHEMA_STATEMENTS = V50_SCHEMA_STATEMENTS
SCHEMA_VERSION_V50 = "companion-v2.storage.v50"
SCHEMA_VERSION = SCHEMA_VERSION_V50
MIGRATION_V49_TO_V50_ID = "companion-v2.storage.v49-to-v50"
MIGRATION_V49_TO_V50_FINGERPRINT = hashlib.sha256(
    "\n".join(V50_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V51_MIGRATION_STATEMENTS = (
    "ALTER TABLE reference_assets ADD COLUMN position INTEGER NOT NULL DEFAULT 0 CHECK(position >= 0)",
    """UPDATE reference_assets AS asset SET position = (
        SELECT COUNT(*) FROM reference_assets AS earlier
        WHERE earlier.owner_key = asset.owner_key
          AND (earlier.created_at < asset.created_at
               OR (earlier.created_at = asset.created_at AND earlier.asset_id < asset.asset_id))
    )""",
    "CREATE INDEX reference_assets_gallery_order_idx ON reference_assets(owner_key, state, position, asset_id)",
    """CREATE TABLE reference_asset_usage (
        owner_key TEXT NOT NULL,
        asset_id TEXT NOT NULL,
        usage_json TEXT NOT NULL CHECK(json_valid(usage_json) AND length(usage_json) <= 8192),
        PRIMARY KEY(owner_key, asset_id),
        FOREIGN KEY(owner_key, asset_id) REFERENCES reference_assets(owner_key, asset_id)
    )""",
)
V51_REQUIRED_TABLES = V50_REQUIRED_TABLES.union({"reference_asset_usage"})
V51_SCHEMA_STATEMENTS = V50_SCHEMA_STATEMENTS + V51_MIGRATION_STATEMENTS
REQUIRED_TABLES = V51_REQUIRED_TABLES
SCHEMA_STATEMENTS = V51_SCHEMA_STATEMENTS
SCHEMA_VERSION_V51 = "companion-v2.storage.v51"
SCHEMA_VERSION = SCHEMA_VERSION_V51
MIGRATION_V50_TO_V51_ID = "companion-v2.storage.v50-to-v51"
MIGRATION_V50_TO_V51_FINGERPRINT = hashlib.sha256(
    "\n".join(V51_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V52_MIGRATION_STATEMENTS = (
    "ALTER TABLE conversation_batches ADD COLUMN presentation_action TEXT NOT NULL DEFAULT 'text' "
    "CHECK(presentation_action IN ('text', 'image', 'voice_only', 'poke', 'silent'))",
)
V52_REQUIRED_TABLES = V51_REQUIRED_TABLES
V52_SCHEMA_STATEMENTS = V51_SCHEMA_STATEMENTS + V52_MIGRATION_STATEMENTS
REQUIRED_TABLES = V52_REQUIRED_TABLES
SCHEMA_STATEMENTS = V52_SCHEMA_STATEMENTS
SCHEMA_VERSION_V52 = "companion-v2.storage.v52"
SCHEMA_VERSION = SCHEMA_VERSION_V52
MIGRATION_V51_TO_V52_ID = "companion-v2.storage.v51-to-v52"
MIGRATION_V51_TO_V52_FINGERPRINT = hashlib.sha256(
    "\n".join(V52_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V53_MIGRATION_STATEMENTS = (
    """CREATE TABLE model_call_diagnostics (
        call_id TEXT PRIMARY KEY NOT NULL REFERENCES model_calls(call_id),
        summary_json TEXT NOT NULL CHECK(json_valid(summary_json) AND length(summary_json) <= 8192),
        observed_at TEXT NOT NULL
    )""",
)
V53_REQUIRED_TABLES = V52_REQUIRED_TABLES.union({"model_call_diagnostics"})
V53_SCHEMA_STATEMENTS = V52_SCHEMA_STATEMENTS + V53_MIGRATION_STATEMENTS
REQUIRED_TABLES = V53_REQUIRED_TABLES
SCHEMA_STATEMENTS = V53_SCHEMA_STATEMENTS
SCHEMA_VERSION_V53 = "companion-v2.storage.v53"
SCHEMA_VERSION = SCHEMA_VERSION_V53
MIGRATION_V52_TO_V53_ID = "companion-v2.storage.v52-to-v53"
MIGRATION_V52_TO_V53_FINGERPRINT = hashlib.sha256(
    "\n".join(V53_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()


V54_MIGRATION_STATEMENTS = (
    """CREATE TABLE conversation_reply_streams (
        batch_id TEXT PRIMARY KEY NOT NULL REFERENCES conversation_batches(batch_id),
        call_id TEXT NOT NULL REFERENCES model_calls(call_id),
        state TEXT NOT NULL CHECK(state IN ('open', 'complete', 'interrupted')),
        observed_at TEXT NOT NULL
    )""",
)
V54_REQUIRED_TABLES = V53_REQUIRED_TABLES.union({"conversation_reply_streams"})
V54_SCHEMA_STATEMENTS = V53_SCHEMA_STATEMENTS + V54_MIGRATION_STATEMENTS
REQUIRED_TABLES = V54_REQUIRED_TABLES
SCHEMA_STATEMENTS = V54_SCHEMA_STATEMENTS
SCHEMA_VERSION_V54 = "companion-v2.storage.v54"
SCHEMA_VERSION = SCHEMA_VERSION_V54
MIGRATION_V53_TO_V54_ID = "companion-v2.storage.v53-to-v54"
MIGRATION_V53_TO_V54_FINGERPRINT = hashlib.sha256(
    "\n".join(V54_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V55_MIGRATION_STATEMENTS = (
    """CREATE TABLE model_call_failures_v55 (
        call_id TEXT PRIMARY KEY NOT NULL REFERENCES model_calls(call_id),
        code TEXT NOT NULL CHECK(code IN (
            'request_preparation_failed', 'provider_request_failed', 'native_response_parse_failed',
            'provider_recheck_failed', 'role_recheck_failed', 'response_contract_rejected',
            'adapter_failed', 'provider_wait_interrupted', 'provider_wait_timed_out',
            'request_recheck_failed', 'runtime_interrupted', 'provider_refused'
        )),
        observed_at TEXT NOT NULL
    )""",
    """INSERT INTO model_call_failures_v55(call_id, code, observed_at)
        SELECT call_id, code, observed_at FROM model_call_failures""",
    "DROP TABLE model_call_failures",
    "ALTER TABLE model_call_failures_v55 RENAME TO model_call_failures",
)
V55_REQUIRED_TABLES = V54_REQUIRED_TABLES
V55_SCHEMA_STATEMENTS = V54_SCHEMA_STATEMENTS + V55_MIGRATION_STATEMENTS
REQUIRED_TABLES = V55_REQUIRED_TABLES
SCHEMA_STATEMENTS = V55_SCHEMA_STATEMENTS
SCHEMA_VERSION_V55 = "companion-v2.storage.v55"
SCHEMA_VERSION = SCHEMA_VERSION_V55
MIGRATION_V54_TO_V55_ID = "companion-v2.storage.v54-to-v55"
MIGRATION_V54_TO_V55_FINGERPRINT = hashlib.sha256(
    "\n".join(V55_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V56_MIGRATION_STATEMENTS = (
    """CREATE TABLE laya_decision_events (
        event_id TEXT PRIMARY KEY NOT NULL CHECK(length(event_id) = 32),
        origin_key TEXT NOT NULL CHECK(length(origin_key) BETWEEN 1 AND 256),
        batch_id TEXT CHECK(batch_id IS NULL OR length(batch_id) BETWEEN 1 AND 128),
        source TEXT NOT NULL CHECK(source IN ('conversation', 'dashboard_preview', 'probe')),
        status TEXT NOT NULL CHECK(status IN ('ok', 'skipped', 'failed')),
        mode TEXT NOT NULL CHECK(mode IN ('local', 'remote')),
        reason_code TEXT CHECK(reason_code IS NULL OR length(reason_code) BETWEEN 1 AND 96),
        reason TEXT CHECK(reason IS NULL OR length(reason) <= 1000),
        elapsed_ms INTEGER CHECK(elapsed_ms IS NULL OR elapsed_ms >= 0),
        decision_json TEXT CHECK(decision_json IS NULL OR (json_valid(decision_json) AND length(decision_json) <= 4096)),
        enabled_fields_json TEXT NOT NULL CHECK(json_valid(enabled_fields_json) AND length(enabled_fields_json) <= 2048),
        observed_at TEXT NOT NULL
    )""",
    "CREATE INDEX laya_decision_events_role_recent_idx ON laya_decision_events(origin_key, observed_at DESC)",
    "CREATE INDEX laya_decision_events_batch_idx ON laya_decision_events(origin_key, batch_id, observed_at)",
)
V56_REQUIRED_TABLES = V55_REQUIRED_TABLES.union({"laya_decision_events"})
V56_SCHEMA_STATEMENTS = V55_SCHEMA_STATEMENTS + V56_MIGRATION_STATEMENTS
REQUIRED_TABLES = V56_REQUIRED_TABLES
SCHEMA_STATEMENTS = V56_SCHEMA_STATEMENTS
SCHEMA_VERSION_V56 = "companion-v2.storage.v56"
SCHEMA_VERSION = SCHEMA_VERSION_V56
MIGRATION_V55_TO_V56_ID = "companion-v2.storage.v55-to-v56"
MIGRATION_V55_TO_V56_FINGERPRINT = hashlib.sha256(
    "\n".join(V56_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V57_MIGRATION_STATEMENTS = (
    """CREATE TABLE proactive_platform_circuits (
        account_key TEXT PRIMARY KEY NOT NULL CHECK(length(account_key) = 64),
        failures_json TEXT NOT NULL CHECK(json_valid(failures_json) AND length(failures_json) <= 2048),
        cooldown_until TEXT,
        updated_at TEXT NOT NULL
    )""",
)
V57_REQUIRED_TABLES = V56_REQUIRED_TABLES.union({"proactive_platform_circuits"})
V57_SCHEMA_STATEMENTS = V56_SCHEMA_STATEMENTS + V57_MIGRATION_STATEMENTS
REQUIRED_TABLES = V57_REQUIRED_TABLES
SCHEMA_STATEMENTS = V57_SCHEMA_STATEMENTS
SCHEMA_VERSION_V57 = "companion-v2.storage.v57"
SCHEMA_VERSION = SCHEMA_VERSION_V57
MIGRATION_V56_TO_V57_ID = "companion-v2.storage.v56-to-v57"
MIGRATION_V56_TO_V57_FINGERPRINT = hashlib.sha256(
    "\n".join(V57_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V58_MIGRATION_STATEMENTS = (
    "ALTER TABLE expression_style_signals ADD COLUMN applicability_json TEXT NOT NULL DEFAULT "
    "'{\"channels\":[\"private\",\"group\",\"proactive\",\"qzone\",\"tts\"],"
    "\"relationship_stages\":[\"any\"],\"emotion_gates\":[\"any\"],\"intent\":\"any\",\"keywords\":[]}' "
    "CHECK(json_valid(applicability_json) AND length(applicability_json) <= 4096)",
)
V58_REQUIRED_TABLES = V57_REQUIRED_TABLES
V58_SCHEMA_STATEMENTS = V57_SCHEMA_STATEMENTS + V58_MIGRATION_STATEMENTS
REQUIRED_TABLES = V58_REQUIRED_TABLES
SCHEMA_STATEMENTS = V58_SCHEMA_STATEMENTS
SCHEMA_VERSION_V58 = "companion-v2.storage.v58"
SCHEMA_VERSION = SCHEMA_VERSION_V58
MIGRATION_V57_TO_V58_ID = "companion-v2.storage.v57-to-v58"
MIGRATION_V57_TO_V58_FINGERPRINT = hashlib.sha256(
    "\n".join(V58_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V59_MIGRATION_STATEMENTS = (
    "ALTER TABLE laya_decision_events ADD COLUMN request_json TEXT CHECK(request_json IS NULL OR (json_valid(request_json) AND length(request_json) <= 131072))",
    "ALTER TABLE laya_decision_events ADD COLUMN response_json TEXT CHECK(response_json IS NULL OR (json_valid(response_json) AND length(response_json) <= 65536))",
    "ALTER TABLE laya_decision_events ADD COLUMN processing_json TEXT CHECK(processing_json IS NULL OR (json_valid(processing_json) AND length(processing_json) <= 16384))",
)
V59_REQUIRED_TABLES = V58_REQUIRED_TABLES
V59_SCHEMA_STATEMENTS = V58_SCHEMA_STATEMENTS + V59_MIGRATION_STATEMENTS
REQUIRED_TABLES = V59_REQUIRED_TABLES
SCHEMA_STATEMENTS = V59_SCHEMA_STATEMENTS
SCHEMA_VERSION_V59 = "companion-v2.storage.v59"
SCHEMA_VERSION = SCHEMA_VERSION_V59
MIGRATION_V58_TO_V59_ID = "companion-v2.storage.v58-to-v59"
MIGRATION_V58_TO_V59_FINGERPRINT = hashlib.sha256(
    "\n".join(V59_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V60_MIGRATION_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS proactive_schedule_diagnostics (
        diagnostic_key TEXT PRIMARY KEY NOT NULL CHECK(length(diagnostic_key) = 64),
        conversation_ref TEXT CHECK(conversation_ref IS NULL OR length(conversation_ref) = 64),
        observed_at TEXT NOT NULL,
        stage TEXT NOT NULL CHECK(length(stage) BETWEEN 1 AND 48),
        outcome TEXT NOT NULL CHECK(outcome IN ('running', 'blocked', 'candidate', 'queued', 'pending', 'skipped', 'failed')),
        reason_code TEXT NOT NULL CHECK(length(reason_code) BETWEEN 1 AND 96),
        purpose TEXT CHECK(purpose IS NULL OR length(purpose) BETWEEN 1 AND 48),
        next_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS proactive_schedule_diagnostics_recent_idx ON proactive_schedule_diagnostics(observed_at)",
)
V60_REQUIRED_TABLES = V59_REQUIRED_TABLES.union({"proactive_schedule_diagnostics"})
V60_SCHEMA_STATEMENTS = V59_SCHEMA_STATEMENTS + V60_MIGRATION_STATEMENTS
REQUIRED_TABLES = V60_REQUIRED_TABLES
SCHEMA_STATEMENTS = V60_SCHEMA_STATEMENTS
SCHEMA_VERSION_V60 = "companion-v2.storage.v60"
SCHEMA_VERSION = SCHEMA_VERSION_V60
MIGRATION_V59_TO_V60_ID = "companion-v2.storage.v59-to-v60"
MIGRATION_V59_TO_V60_FINGERPRINT = hashlib.sha256(
    "\n".join(V60_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V61_MIGRATION_STATEMENTS = (
    "ALTER TABLE laya_decision_events RENAME TO laya_decision_events_v60",
    """CREATE TABLE laya_decision_events (
        event_id TEXT PRIMARY KEY NOT NULL CHECK(length(event_id) = 32),
        origin_key TEXT NOT NULL CHECK(length(origin_key) BETWEEN 1 AND 256),
        batch_id TEXT CHECK(batch_id IS NULL OR length(batch_id) BETWEEN 1 AND 128),
        source TEXT NOT NULL CHECK(source IN ('conversation', 'dashboard_preview', 'probe', 'role_intervention')),
        status TEXT NOT NULL CHECK(status IN ('ok', 'skipped', 'failed')),
        mode TEXT NOT NULL CHECK(mode IN ('local', 'remote')),
        reason_code TEXT CHECK(reason_code IS NULL OR length(reason_code) BETWEEN 1 AND 96),
        reason TEXT CHECK(reason IS NULL OR length(reason) <= 1000),
        elapsed_ms INTEGER CHECK(elapsed_ms IS NULL OR elapsed_ms >= 0),
        decision_json TEXT CHECK(decision_json IS NULL OR (json_valid(decision_json) AND length(decision_json) <= 4096)),
        enabled_fields_json TEXT NOT NULL CHECK(json_valid(enabled_fields_json) AND length(enabled_fields_json) <= 2048),
        observed_at TEXT NOT NULL,
        request_json TEXT CHECK(request_json IS NULL OR (json_valid(request_json) AND length(request_json) <= 131072)),
        response_json TEXT CHECK(response_json IS NULL OR (json_valid(response_json) AND length(response_json) <= 65536)),
        processing_json TEXT CHECK(processing_json IS NULL OR (json_valid(processing_json) AND length(processing_json) <= 16384))
    )""",
    """INSERT INTO laya_decision_events (
        event_id, origin_key, batch_id, source, status, mode, reason_code, reason, elapsed_ms,
        decision_json, enabled_fields_json, observed_at, request_json, response_json, processing_json
    ) SELECT
        event_id, origin_key, batch_id, source, status, mode, reason_code, reason, elapsed_ms,
        decision_json, enabled_fields_json, observed_at, request_json, response_json, processing_json
    FROM laya_decision_events_v60""",
    "DROP TABLE laya_decision_events_v60",
    "CREATE INDEX laya_decision_events_role_recent_idx ON laya_decision_events(origin_key, observed_at DESC)",
    "CREATE INDEX laya_decision_events_batch_idx ON laya_decision_events(origin_key, batch_id, observed_at)",
)
V61_REQUIRED_TABLES = V60_REQUIRED_TABLES
V61_SCHEMA_STATEMENTS = V60_SCHEMA_STATEMENTS + V61_MIGRATION_STATEMENTS
REQUIRED_TABLES = V61_REQUIRED_TABLES
SCHEMA_STATEMENTS = V61_SCHEMA_STATEMENTS
SCHEMA_VERSION_V61 = "companion-v2.storage.v61"
SCHEMA_VERSION = SCHEMA_VERSION_V61
MIGRATION_V60_TO_V61_ID = "companion-v2.storage.v60-to-v61"
MIGRATION_V60_TO_V61_FINGERPRINT = hashlib.sha256(
    "\n".join(V61_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V62_MIGRATION_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS conversation_inputs_scope_received_idx ON conversation_inputs(json_extract(event_json, '$.scope.kind'), json_extract(event_json, '$.received_at') DESC, batch_id) WHERE withdrawn = 0",
    "CREATE INDEX IF NOT EXISTS conversation_batches_role_binding_idx ON conversation_batches(json_extract(context_json, '$.role_binding'), batch_id)",
)
V62_REQUIRED_TABLES = V61_REQUIRED_TABLES
V62_SCHEMA_STATEMENTS = V61_SCHEMA_STATEMENTS + V62_MIGRATION_STATEMENTS
REQUIRED_TABLES = V62_REQUIRED_TABLES
SCHEMA_STATEMENTS = V62_SCHEMA_STATEMENTS
SCHEMA_VERSION_V62 = "companion-v2.storage.v62"
SCHEMA_VERSION = SCHEMA_VERSION_V62
MIGRATION_V61_TO_V62_ID = "companion-v2.storage.v61-to-v62"
MIGRATION_V61_TO_V62_FINGERPRINT = hashlib.sha256(
    "\n".join(V62_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V63_MIGRATION_STATEMENTS = (
    """
    CREATE TABLE agenda_open_loop_activity (
        memo_id TEXT PRIMARY KEY NOT NULL,
        last_context_at TEXT,
        context_count INTEGER NOT NULL DEFAULT 0 CHECK (context_count >= 0),
        last_followup_at TEXT,
        followup_count INTEGER NOT NULL DEFAULT 0 CHECK (followup_count >= 0),
        updated_at TEXT NOT NULL,
        FOREIGN KEY(memo_id) REFERENCES agenda_memos(memo_id)
    )
    """,
    "CREATE INDEX agenda_open_loop_activity_followup_idx ON agenda_open_loop_activity(followup_count, last_followup_at)",
)
V63_REQUIRED_TABLES = V62_REQUIRED_TABLES.union({"agenda_open_loop_activity"})
V63_SCHEMA_STATEMENTS = V62_SCHEMA_STATEMENTS + V63_MIGRATION_STATEMENTS
REQUIRED_TABLES = V63_REQUIRED_TABLES
SCHEMA_STATEMENTS = V63_SCHEMA_STATEMENTS
SCHEMA_VERSION_V63 = "companion-v2.storage.v63"
SCHEMA_VERSION = SCHEMA_VERSION_V63
MIGRATION_V62_TO_V63_ID = "companion-v2.storage.v62-to-v63"
MIGRATION_V62_TO_V63_FINGERPRINT = hashlib.sha256(
    "\n".join(V63_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V64_MIGRATION_STATEMENTS = (
    """CREATE TABLE proactive_prompt_snapshots (
        snapshot_id INTEGER PRIMARY KEY,
        task_id TEXT NOT NULL,
        call_id TEXT NOT NULL UNIQUE,
        provider_id TEXT NOT NULL,
        model_name TEXT NOT NULL,
        prompt_hash TEXT NOT NULL,
        messages_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(task_id) REFERENCES model_requests(task_id),
        FOREIGN KEY(call_id) REFERENCES model_calls(call_id)
    )""",
)
V64_REQUIRED_TABLES = V63_REQUIRED_TABLES.union({"proactive_prompt_snapshots"})
V64_SCHEMA_STATEMENTS = V63_SCHEMA_STATEMENTS + V64_MIGRATION_STATEMENTS
REQUIRED_TABLES = V64_REQUIRED_TABLES
SCHEMA_STATEMENTS = V64_SCHEMA_STATEMENTS
SCHEMA_VERSION_V64 = "companion-v2.storage.v64"
SCHEMA_VERSION = SCHEMA_VERSION_V64
MIGRATION_V63_TO_V64_ID = "companion-v2.storage.v63-to-v64"
MIGRATION_V63_TO_V64_FINGERPRINT = hashlib.sha256(
    "\n".join(V64_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V65_MIGRATION_STATEMENTS = (
    "ALTER TABLE outbox ADD COLUMN receipt_ref TEXT CHECK(receipt_ref IS NULL OR length(receipt_ref) BETWEEN 1 AND 512)",
)
V65_REQUIRED_TABLES = V64_REQUIRED_TABLES
V65_SCHEMA_STATEMENTS = V64_SCHEMA_STATEMENTS + V65_MIGRATION_STATEMENTS
REQUIRED_TABLES = V65_REQUIRED_TABLES
SCHEMA_STATEMENTS = V65_SCHEMA_STATEMENTS
SCHEMA_VERSION_V65 = "companion-v2.storage.v65"
SCHEMA_VERSION = SCHEMA_VERSION_V65
MIGRATION_V64_TO_V65_ID = "companion-v2.storage.v64-to-v65"
MIGRATION_V64_TO_V65_FINGERPRINT = hashlib.sha256(
    "\n".join(V65_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

V66_MIGRATION_STATEMENTS = RELATIONSHIP_ANNIVERSARY_STATEMENTS
V66_REQUIRED_TABLES = V65_REQUIRED_TABLES.union(RELATIONSHIP_ANNIVERSARY_TABLES)
V66_SCHEMA_STATEMENTS = V65_SCHEMA_STATEMENTS + V66_MIGRATION_STATEMENTS
REQUIRED_TABLES = V66_REQUIRED_TABLES
SCHEMA_STATEMENTS = V66_SCHEMA_STATEMENTS
SCHEMA_VERSION_V66 = "companion-v2.storage.v66"
SCHEMA_VERSION = SCHEMA_VERSION_V66
MIGRATION_V65_TO_V66_ID = "companion-v2.storage.v65-to-v66"
MIGRATION_V65_TO_V66_FINGERPRINT = hashlib.sha256(
    "\n".join(V66_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()

# `jev` is a third judgment backend beside local and SystemOne, so the decision
# ledger has to record it. SQLite cannot relax a CHECK constraint in place, so
# this rebuilds the table exactly the way v61 did; the DDL stays frozen here
# because every data root verifies this migration by fingerprint.
V67_MIGRATION_STATEMENTS = (
    "ALTER TABLE laya_decision_events RENAME TO laya_decision_events_v66",
    """CREATE TABLE laya_decision_events (
        event_id TEXT PRIMARY KEY NOT NULL CHECK(length(event_id) = 32),
        origin_key TEXT NOT NULL CHECK(length(origin_key) BETWEEN 1 AND 256),
        batch_id TEXT CHECK(batch_id IS NULL OR length(batch_id) BETWEEN 1 AND 128),
        source TEXT NOT NULL CHECK(source IN ('conversation', 'dashboard_preview', 'probe', 'role_intervention')),
        status TEXT NOT NULL CHECK(status IN ('ok', 'skipped', 'failed')),
        mode TEXT NOT NULL CHECK(mode IN ('local', 'remote', 'jev')),
        reason_code TEXT CHECK(reason_code IS NULL OR length(reason_code) BETWEEN 1 AND 96),
        reason TEXT CHECK(reason IS NULL OR length(reason) <= 1000),
        elapsed_ms INTEGER CHECK(elapsed_ms IS NULL OR elapsed_ms >= 0),
        decision_json TEXT CHECK(decision_json IS NULL OR (json_valid(decision_json) AND length(decision_json) <= 4096)),
        enabled_fields_json TEXT NOT NULL CHECK(json_valid(enabled_fields_json) AND length(enabled_fields_json) <= 2048),
        observed_at TEXT NOT NULL,
        request_json TEXT CHECK(request_json IS NULL OR (json_valid(request_json) AND length(request_json) <= 131072)),
        response_json TEXT CHECK(response_json IS NULL OR (json_valid(response_json) AND length(response_json) <= 65536)),
        processing_json TEXT CHECK(processing_json IS NULL OR (json_valid(processing_json) AND length(processing_json) <= 16384))
    )""",
    """INSERT INTO laya_decision_events (
        event_id, origin_key, batch_id, source, status, mode, reason_code, reason, elapsed_ms,
        decision_json, enabled_fields_json, observed_at, request_json, response_json, processing_json
    ) SELECT
        event_id, origin_key, batch_id, source, status, mode, reason_code, reason, elapsed_ms,
        decision_json, enabled_fields_json, observed_at, request_json, response_json, processing_json
    FROM laya_decision_events_v66""",
    "DROP TABLE laya_decision_events_v66",
    "CREATE INDEX laya_decision_events_role_recent_idx ON laya_decision_events(origin_key, observed_at DESC)",
    "CREATE INDEX laya_decision_events_batch_idx ON laya_decision_events(origin_key, batch_id, observed_at)",
)
V67_REQUIRED_TABLES = V66_REQUIRED_TABLES
V67_SCHEMA_STATEMENTS = V66_SCHEMA_STATEMENTS + V67_MIGRATION_STATEMENTS
REQUIRED_TABLES = V67_REQUIRED_TABLES
SCHEMA_STATEMENTS = V67_SCHEMA_STATEMENTS
SCHEMA_VERSION_V67 = "companion-v2.storage.v67"
SCHEMA_VERSION = SCHEMA_VERSION_V67
MIGRATION_V66_TO_V67_ID = "companion-v2.storage.v66-to-v67"
MIGRATION_V66_TO_V67_FINGERPRINT = hashlib.sha256(
    "\n".join(V67_MIGRATION_STATEMENTS).encode("utf-8")
).hexdigest()


def migration_count() -> int:
    """How many ordered migrations a fully upgraded data root records.

    Tests assert on the length of ``schema_migrations`` to prove an upgrade ran the
    whole chain. Deriving that number keeps the assertion honest when a migration is
    added: a hard-coded total silently becomes a statement about the past.
    """
    names = {name for name in globals() if name.startswith("MIGRATION_") and name.endswith("_ID")}
    return len(names)
