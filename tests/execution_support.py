"""Remove newer tables when constructing historical upgrade fixtures."""

from companion_v2.storage.schema import (
    MEMORY_TABLES, V10_MIGRATION_STATEMENTS, V15_MIGRATION_STATEMENTS, V36_MIGRATION_STATEMENTS,
    V56_MIGRATION_STATEMENTS, V59_MIGRATION_STATEMENTS, V61_MIGRATION_STATEMENTS,
)


def remove_v61(connection):
    remove_v62(connection)
    if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'laya_decision_events'").fetchone():
        has_intervention_events = connection.execute(
            "SELECT 1 FROM laya_decision_events WHERE source = 'role_intervention' LIMIT 1").fetchone()
        if has_intervention_events:
            raise ValueError("cannot downgrade role_intervention events to a v60 fixture")
        connection.execute("CREATE TEMP TABLE laya_decision_events_v61_saved AS SELECT * FROM laya_decision_events")
        connection.execute('DROP TABLE "laya_decision_events"')
        connection.execute(V56_MIGRATION_STATEMENTS[0])
        for statement in V59_MIGRATION_STATEMENTS:
            connection.execute(statement)
        connection.execute("INSERT INTO laya_decision_events SELECT * FROM laya_decision_events_v61_saved")
        connection.execute("DROP TABLE laya_decision_events_v61_saved")
        for statement in V56_MIGRATION_STATEMENTS[1:]:
            connection.execute(statement)
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v60-to-v61'")


def remove_v62(connection):
    remove_v63(connection)
    connection.execute("DROP INDEX IF EXISTS conversation_inputs_scope_received_idx")
    connection.execute("DROP INDEX IF EXISTS conversation_batches_role_binding_idx")
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v61-to-v62'")


def remove_v63(connection):
    remove_v64(connection)
    connection.execute("DROP INDEX IF EXISTS agenda_open_loop_activity_followup_idx")
    connection.execute('DROP TABLE IF EXISTS "agenda_open_loop_activity"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v62-to-v63'")


def remove_v64(connection):
    remove_v65(connection)
    connection.execute('DROP TABLE IF EXISTS "proactive_prompt_snapshots"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v63-to-v64'")


def remove_v66(connection):
    remove_v67(connection)
    connection.execute('DROP INDEX IF EXISTS "relationship_anniversary_active_day_idx"')
    connection.execute('DROP INDEX IF EXISTS "relationship_anniversary_active_idx"')
    connection.execute('DROP TABLE IF EXISTS "relationship_anniversaries"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v65-to-v66'")


def remove_v67(connection):
    """Restore the narrow `mode` constraint that only knows local and remote.

    v67 rebuilt this table to admit `jev`; v61 already owns the exact v66 DDL,
    so the fixture reuses it instead of restating a second copy that could drift.
    """
    if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'laya_decision_events'").fetchone():
        if connection.execute(
                "SELECT 1 FROM laya_decision_events WHERE mode = 'jev' LIMIT 1").fetchone():
            raise ValueError("cannot downgrade Jev events to a v66 fixture")
        connection.execute("CREATE TEMP TABLE laya_decision_events_v67_saved AS SELECT * FROM laya_decision_events")
        connection.execute('DROP TABLE "laya_decision_events"')
        for statement in (V61_MIGRATION_STATEMENTS[1],):
            connection.execute(statement)
        connection.execute("INSERT INTO laya_decision_events SELECT * FROM laya_decision_events_v67_saved")
        connection.execute("DROP TABLE laya_decision_events_v67_saved")
        for statement in V61_MIGRATION_STATEMENTS[4:]:
            connection.execute(statement)
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v66-to-v67'")


def remove_v65(connection):
    remove_v66(connection)
    columns = {row[1] for row in connection.execute('PRAGMA table_info("outbox")')}
    if "receipt_ref" in columns:
        connection.execute('ALTER TABLE outbox DROP COLUMN "receipt_ref"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v64-to-v65'")


def remove_v60(connection):
    remove_v61(connection)
    connection.execute('DROP TABLE IF EXISTS "proactive_schedule_diagnostics"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v59-to-v60'")


def remove_v59(connection):
    remove_v60(connection)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(laya_decision_events)")}
    for column in ("processing_json", "response_json", "request_json"):
        if column in columns:
            connection.execute(f'ALTER TABLE laya_decision_events DROP COLUMN "{column}"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v58-to-v59'")


def remove_v58(connection):
    remove_v59(connection)
    connection.execute('ALTER TABLE expression_style_signals DROP COLUMN applicability_json')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v57-to-v58'")


def remove_v57(connection):
    remove_v58(connection)
    connection.execute('DROP TABLE IF EXISTS "proactive_platform_circuits"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v56-to-v57'")


def remove_v56(connection):
    remove_v57(connection)
    connection.execute('DROP TABLE IF EXISTS "laya_decision_events"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v55-to-v56'")


def remove_v55(connection):
    """Restore the v54 failure-code constraint for historical schema fixtures."""
    remove_v56(connection)
    connection.execute("PRAGMA foreign_keys = OFF")
    if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'model_call_failures'").fetchone():
        connection.execute("CREATE TEMP TABLE model_call_failures_v54_saved AS SELECT * FROM model_call_failures")
        connection.execute('DROP TABLE "model_call_failures"')
        connection.execute(V36_MIGRATION_STATEMENTS[0])
        connection.execute("INSERT INTO model_call_failures_v36 SELECT * FROM model_call_failures_v54_saved")
        connection.execute("DROP TABLE model_call_failures_v54_saved")
        connection.execute("ALTER TABLE model_call_failures_v36 RENAME TO model_call_failures")
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v54-to-v55'")


def remove_v54(connection):
    """Undo incremental reply publication records before older fixtures."""
    remove_v55(connection)
    connection.execute('DROP TABLE IF EXISTS "conversation_reply_streams"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v53-to-v54'")


def remove_v53(connection):
    remove_v54(connection)
    """Undo the response-stage summaries for historical fixtures."""
    connection.execute('DROP TABLE IF EXISTS "model_call_diagnostics"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v52-to-v53'")


def remove_v52(connection):
    """Undo the reply presentation action column for historical fixtures."""
    remove_v53(connection)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(conversation_batches)")}
    if "presentation_action" in columns:
        connection.execute('ALTER TABLE conversation_batches DROP COLUMN presentation_action')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v51-to-v52'")


def remove_v51(connection):
    """Undo V51 gallery ordering for historical fixtures."""
    remove_v52(connection)
    connection.execute('DROP TABLE IF EXISTS "reference_asset_usage"')
    connection.execute('DROP INDEX IF EXISTS "reference_assets_gallery_order_idx"')
    columns = {row[1] for row in connection.execute("PRAGMA table_info(reference_assets)")}
    if "position" in columns:
        connection.execute('ALTER TABLE reference_assets DROP COLUMN position')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v50-to-v51'")


def remove_v50(connection):
    """Undo V50's diagnostic code column and later migrations."""
    remove_v51(connection)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(inbound_no_replies)")}
    if "code" in columns:
        connection.execute('ALTER TABLE inbound_no_replies DROP COLUMN code')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v49-to-v50'")


def remove_v49(connection):
    """Undo the V49 inbound no-reply ledger."""
    remove_v50(connection)
    connection.execute('DROP INDEX IF EXISTS "inbound_no_replies_role_recent_idx"')
    connection.execute('DROP TABLE IF EXISTS "inbound_no_replies"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v48-to-v49'")


def remove_v48(connection):
    """Undo V48 cache signatures."""
    remove_v49(connection)
    connection.execute('DROP TABLE IF EXISTS "model_call_cache_signatures"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v47-to-v48'")


def remove_v47(connection):
    """Undo the V47 reference/outfit additions."""
    remove_v48(connection)
    connection.execute('DROP INDEX IF EXISTS "reference_asset_payload_owner_idx"')
    connection.execute('DROP INDEX IF EXISTS "wardrobe_daily_outfits_recent_idx"')
    connection.execute('DROP TABLE IF EXISTS "reference_asset_payloads"')
    connection.execute('DROP TABLE IF EXISTS "wardrobe_daily_outfits"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v46-to-v47'")


def remove_v46(connection):
    """Undo V47 additions and model usage columns so historical fixtures match."""
    remove_v47(connection)
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'model_calls'"
    ).fetchone()
    if exists is not None:
        columns = {row[1] for row in connection.execute('PRAGMA table_info("model_calls")')}
        for name in ("input_tokens", "cached_input_tokens", "cache_creation_tokens", "output_tokens"):
            if name in columns:
                connection.execute(f'ALTER TABLE model_calls DROP COLUMN "{name}"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v45-to-v46'")


def remove_v45(connection):
    """Undo V46 usage columns and the persona draft ledger for a v44 fixture."""
    remove_v46(connection)
    for name in ("persona_draft_versions_append_only_update",
                 "persona_draft_versions_append_only_delete"):
        connection.execute(f'DROP TRIGGER IF EXISTS "{name}"')
    connection.execute('DROP INDEX IF EXISTS "persona_draft_versions_order_idx"')
    connection.execute('DROP TABLE IF EXISTS "persona_draft_versions"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v44-to-v45'")


def remove_v44(connection):
    """Undo the reading-archive trigger cursors so a v43 fixture can be rebuilt."""
    remove_v45(connection)
    connection.execute('DROP TABLE IF EXISTS "reading_archive_triggers"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v43-to-v44'")


def remove_v43(connection):
    """Undo the reference-asset tables so a v42 fixture can be rebuilt."""
    remove_v44(connection)
    for index in ("reference_assets_kind_idx", "reference_assets_hash_idx",
                  "reference_asset_roles_role_idx", "wardrobe_items_state_idx"):
        connection.execute(f'DROP INDEX IF EXISTS "{index}"')
    # Children before parents: wardrobe_items and reference_asset_roles both carry
    # a foreign key into reference_assets.
    for table in ("reference_asset_roles", "wardrobe_items", "reference_assets"):
        connection.execute(f'DROP TABLE IF EXISTS "{table}"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v42-to-v43'")


def remove_v42(connection):
    remove_v43(connection)
    for index in ("reading_archive_entries_state_idx", "reading_archive_entries_origin_idx",
                  "reading_archive_reviews_entry_idx"):
        connection.execute(f'DROP INDEX IF EXISTS "{index}"')
    for table in ("reading_archive_reviews", "reading_archive_progress",
                  "reading_archive_consents", "reading_archive_entries"):
        connection.execute(f'DROP TABLE IF EXISTS "{table}"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v41-to-v42'")


def remove_v41(connection):
    remove_v42(connection)
    connection.execute('DROP INDEX IF EXISTS "group_member_safety_hits_time_idx"')
    connection.execute('DROP INDEX IF EXISTS "group_member_safety_hits_member_idx"')
    connection.execute('DROP TABLE IF EXISTS "group_member_safety_hits"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v40-to-v41'")


def remove_v40(connection):
    remove_v41(connection)
    connection.execute('DROP INDEX IF EXISTS "expression_style_signal_adopted_idx"')
    connection.execute('DROP INDEX IF EXISTS "expression_style_signal_queue_idx"')
    connection.execute('DROP TABLE IF EXISTS "expression_style_signals"')
    connection.execute('DROP TABLE IF EXISTS "expression_style_runs"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v39-to-v40'")


def remove_v39(connection):
    remove_v40(connection)
    connection.execute('DROP INDEX IF EXISTS "role_self_review_item_active_idx"')
    connection.execute('DROP INDEX IF EXISTS "role_self_review_item_queue_idx"')
    connection.execute('DROP TABLE IF EXISTS "role_self_review_items"')
    connection.execute('DROP TABLE IF EXISTS "role_self_review_runs"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v38-to-v39'")


def remove_v38(connection):
    remove_v39(connection)
    connection.execute('DROP TABLE IF EXISTS "proactive_news_polls"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v37-to-v38'")


def remove_v37(connection):
    remove_v38(connection)
    connection.execute('DROP TABLE IF EXISTS "proactive_balance_polls"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v36-to-v37'")


def remove_v36(connection):
    remove_v37(connection)
    if connection.execute(
        "SELECT 1 FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v35-to-v36'"
    ).fetchone() is None:
        return
    connection.execute("CREATE TEMP TABLE fixture_failures AS SELECT * FROM model_call_failures")
    connection.execute('DROP TABLE "model_call_failures"')
    connection.execute(V15_MIGRATION_STATEMENTS[0])
    connection.execute("INSERT INTO model_call_failures SELECT * FROM fixture_failures")
    connection.execute("DROP TABLE fixture_failures")
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v35-to-v36'")


def remove_v35(connection):
    remove_v36(connection)
    connection.execute('DROP TABLE IF EXISTS "conversation_voice_diagnostics"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v34-to-v35'")


def remove_v34(connection):
    remove_v35(connection)
    connection.execute('DROP INDEX IF EXISTS "proactive_official_alert_chain_idx"')
    connection.execute('DROP TABLE IF EXISTS "proactive_official_alerts"')
    connection.execute('DROP TABLE IF EXISTS "proactive_official_alert_polls"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v33-to-v34'")


def remove_v33(connection):
    remove_v34(connection)
    connection.execute('DROP TABLE IF EXISTS "proactive_weather_samples"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v32-to-v33'")


def remove_v32(connection):
    remove_v33(connection)
    connection.execute('DROP TABLE IF EXISTS "conversation_voice_cooldowns"')
    connection.execute('DROP TABLE IF EXISTS "conversation_voice_parts"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v31-to-v32'")


def remove_v31(connection):
    remove_v32(connection)
    connection.execute('DROP INDEX IF EXISTS "role_growth_progress_queue_idx"')
    connection.execute('DROP TABLE IF EXISTS "role_growth_progress_reviews"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v30-to-v31'")


def remove_v30(connection):
    remove_v31(connection)
    connection.execute('DROP TABLE IF EXISTS "conversation_video_payloads"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v29-to-v30'")


def remove_v29(connection):
    remove_v30(connection)
    connection.execute('DROP TABLE IF EXISTS "conversation_audio_payloads"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v28-to-v29'")


def remove_v28(connection):
    remove_v29(connection)
    connection.execute('DROP TABLE IF EXISTS "memory_reflection_jobs"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v27-to-v28'")


def remove_v27(connection):
    remove_v28(connection)
    for name in ("reflection_inputs_insert", "reflection_inputs_update"):
        connection.execute(f'DROP TRIGGER IF EXISTS "{name}"')
    connection.execute('DROP INDEX IF EXISTS "memory_pending_selection_idx"')
    connection.execute('DROP TABLE IF EXISTS "memory_reflection_inputs"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v26-to-v27'")


def remove_v26(connection):
    remove_v27(connection)
    for kind in ("human", "reply", "experience", "fact"):
        for action in ("insert", "update", "delete"):
            connection.execute(f'DROP TRIGGER IF EXISTS "recall_{kind}_{action}"')
    connection.execute('DROP INDEX IF EXISTS "memory_guard_evidence_idx"')
    connection.execute('DROP TABLE IF EXISTS "memory_recall_changes"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v25-to-v26'")


def remove_v25(connection):
    remove_v26(connection)
    connection.execute('DROP TABLE IF EXISTS "conversation_image_payloads"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v24-to-v25'")


def remove_v24(connection):
    remove_v25(connection)
    from companion_v2.storage.state_schema import STATE_TABLES
    for table in sorted(STATE_TABLES, key=lambda name: name.endswith("_records")):
        connection.execute(f'DROP TABLE IF EXISTS "{table}"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v23-to-v24'")


def remove_v23(connection):
    remove_v24(connection)
    connection.execute('DROP TABLE IF EXISTS "role_activity_revisions"')
    connection.execute('DROP INDEX IF EXISTS "role_growth_history_time_idx"')
    connection.execute('DROP INDEX IF EXISTS "role_journal_history_time_idx"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v22-to-v23'")


def remove_v22(connection):
    remove_v23(connection)
    for table in ("role_growth_operations", "role_growth_revisions", "role_growth_items"):
        connection.execute(f'DROP TABLE IF EXISTS "{table}"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v21-to-v22'")


def remove_v21(connection):
    remove_v22(connection)
    for table in sorted(MEMORY_TABLES):
        connection.execute(f'DROP TABLE IF EXISTS "{table}"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v20-to-v21'")


def remove_v20(connection):
    remove_v21(connection)
    connection.execute('DROP TABLE IF EXISTS "proactive_contacts"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v19-to-v20'")


def remove_v19(connection):
    remove_v20(connection)
    for table in ("agenda_dispatches", "background_delivery_receipts", "background_deliveries"):
        connection.execute(f'DROP TABLE IF EXISTS "{table}"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v18-to-v19'")


def remove_v18(connection):
    remove_v19(connection)
    for table in ("relationship_memory_operations", "relationship_memory_revisions", "relationship_memories"):
        connection.execute(f'DROP TABLE IF EXISTS "{table}"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v17-to-v18'")


def remove_v17(connection):
    remove_v18(connection)
    for table in ("role_journal_operations", "role_journal_derivatives", "role_journals"):
        connection.execute(f'DROP TABLE IF EXISTS "{table}"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v16-to-v17'")


def remove_v16(connection):
    remove_v17(connection)
    for table in ("role_life_operations", "role_artifacts", "role_activities"):
        connection.execute(f'DROP TABLE IF EXISTS "{table}"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v15-to-v16'")


def remove_v15(connection):
    remove_v16(connection)
    connection.execute('DROP TABLE IF EXISTS "model_call_failures"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v14-to-v15'")


def remove_v14(connection):
    remove_v15(connection)
    connection.execute('DROP TABLE IF EXISTS "conversation_flows"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v13-to-v14'")


def remove_v13(connection):
    remove_v14(connection)
    connection.execute('DROP TABLE IF EXISTS "extension_tool_invocations"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v12-to-v13'")


def remove_v12(connection):
    remove_v13(connection)
    for table in ("conversation_tool_calls", "conversation_tool_sources", "model_rounds"):
        connection.execute(f'DROP TABLE IF EXISTS "{table}"')
    has_model_calls = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'model_calls'"
    ).fetchone() is not None
    has_model_accounts = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'model_call_accounts'"
    ).fetchone() is not None
    if not has_model_calls and not has_model_accounts:
        connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v11-to-v12'")
        return
    if has_model_calls != has_model_accounts:
        raise RuntimeError("historical fixture has only one model call table")
    connection.execute("CREATE TEMP TABLE fixture_calls AS SELECT * FROM model_calls")
    connection.execute("CREATE TEMP TABLE fixture_accounts AS SELECT * FROM model_call_accounts")
    connection.execute("DROP TABLE model_call_accounts")
    connection.execute("DROP TABLE model_calls")
    for statement in V10_MIGRATION_STATEMENTS[2:]:
        connection.execute(statement)
    connection.execute("INSERT INTO model_calls SELECT * FROM fixture_calls")
    connection.execute("INSERT INTO model_call_accounts SELECT * FROM fixture_accounts")
    connection.execute("DROP TABLE fixture_accounts")
    connection.execute("DROP TABLE fixture_calls")
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v11-to-v12'")


def remove_v11(connection):
    remove_v12(connection)
    for table in ("conversation_sent_history", "conversation_delivery_receipts", "conversation_parts",
                  "conversation_inputs", "conversation_batches"):
        connection.execute(f'DROP TABLE IF EXISTS "{table}"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v10-to-v11'")


def remove_v10(connection):
    remove_v11(connection)
    for table in ("model_call_accounts", "model_calls", "model_requests", "model_budget_accounts"):
        connection.execute(f'DROP TABLE IF EXISTS "{table}"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v9-to-v10'")


def remove_v9(connection):
    remove_v10(connection)
    for table in ("execution_events", "execution_attempts", "execution_tasks", "task_supervisor"):
        connection.execute(f'DROP TABLE IF EXISTS "{table}"')
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 'companion-v2.storage.v8-to-v9'")
