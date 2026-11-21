"""Optional native conversation observations, independent of V2 reply batches."""

# The two phases in which an admitted turn is still using its window. They appear
# as literals below and in the query that reads them, because SQLite only applies
# a partial index when it can prove the statement implies the index predicate, and
# a bound parameter cannot be proven. Keep the three spellings identical.
OPEN_TURN_STATES = ("prepared", "running")

TABLE = "conversation_native_observations"
STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS conversation_native_observations (
        observation_id TEXT PRIMARY KEY,
        context_key TEXT NOT NULL, memory_owner TEXT NOT NULL, conversation_owner TEXT NOT NULL,
        turn_id TEXT NOT NULL, event_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('input', 'assistant')),
        delivery_state TEXT NOT NULL CHECK(delivery_state IN ('input_observed', 'output_observed', 'send_returned')),
        context_json TEXT NOT NULL, source_json TEXT NOT NULL,
        content_hash TEXT NOT NULL, observed_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS conversation_native_owner_idx ON conversation_native_observations(memory_owner, observed_at, observation_id)",
    "CREATE INDEX IF NOT EXISTS conversation_native_input_context_idx ON conversation_native_observations(context_key, observed_at DESC, observation_id) WHERE kind = 'input' AND delivery_state = 'input_observed'",
    "CREATE INDEX IF NOT EXISTS conversation_native_input_role_idx ON conversation_native_observations(json_extract(context_json, '$.role_binding'), observed_at DESC, observation_id) WHERE kind = 'input' AND delivery_state = 'input_observed'",
    # ``native_history.rows`` reads one context's input + sent-reply rows in
    # reverse chronological order; ascending observed_at lets SQLite walk the
    # index backwards in exact (observed_at DESC, rowid DESC) order.
    "CREATE INDEX IF NOT EXISTS conversation_native_obs_context_delivery_idx "
    "ON conversation_native_observations(context_key, observed_at) "
    "WHERE delivery_state IN ('input_observed', 'send_returned')",
    # ``private_scope.private_people`` / ``role_private_contexts`` scan recent
    # inputs for one role/bot/installation ordered by observed_at DESC, rowid DESC.
    "CREATE INDEX IF NOT EXISTS conversation_native_obs_role_input_recent_idx "
    "ON conversation_native_observations("
    "json_extract(context_json, '$.role_binding.role.subject_id'), "
    "json_extract(context_json, '$.role_binding.scope.bot_id'), "
    "json_extract(context_json, '$.role_binding.scope.installation_id'), "
    "observed_at) WHERE kind = 'input'",
    "CREATE INDEX IF NOT EXISTS conversation_native_source_idx ON conversation_native_observations(json_extract(source_json, '$.source_id'))",
    """CREATE TABLE IF NOT EXISTS conversation_native_turns (
        turn_id TEXT PRIMARY KEY, context_key TEXT NOT NULL, context_json TEXT NOT NULL,
        role_instruction TEXT NOT NULL, input_observation_id TEXT,
        observed_at TEXT NOT NULL, generation_state TEXT NOT NULL
            CHECK(generation_state IN ('prepared', 'running', 'completed', 'failed')),
        phase_at TEXT NOT NULL, last_sent_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS conversation_native_turn_context_idx ON conversation_native_turns(context_key, observed_at DESC, turn_id)",
    # Ascending (context_key, observed_at) has implicit trailing rowid ASC, so
    # backwards scans satisfy ORDER BY observed_at DESC, rowid DESC with no temp
    # B-tree and power both loose-index-scan latest-per-context reads and O(log N)
    # row-value NOT EXISTS probes without shadowing conversation_native_open_turn_idx.
    "CREATE INDEX IF NOT EXISTS conversation_native_turn_context_recent_idx "
    "ON conversation_native_turns(context_key, observed_at)",
    # Latest-turn-per-context probes must prove no newer row exists for the same
    # context, so their cost is the whole table unless the caller can cut the
    # candidate set first. In-flight turns are the per-pass case (presence asks
    # which private windows are still typing on every drive pass), so the open
    # rows get their own index and a finished turn is never read for that answer.
    # The leading expression is the audience kind: callers that also narrow to one
    # scope kind are served by the same entry, which is why a plain kind index is
    # deliberately absent — it would win the planner and lose the state filter.
    "CREATE INDEX IF NOT EXISTS conversation_native_open_turn_idx ON conversation_native_turns("
    "json_extract(context_json, '$.audience.scope.kind'), context_key, observed_at) "
    "WHERE generation_state IN ('prepared', 'running')",
    # ``latest_scope`` probes seven scope fields and then orders by observed_at.
    # Without this index the probe is a full table scan per call, and presence
    # calls it once per active window on every pass.
    "CREATE INDEX IF NOT EXISTS conversation_native_scope_idx ON conversation_native_turns("
    "json_extract(context_json, '$.audience.scope.installation_id'), "
    "json_extract(context_json, '$.audience.scope.platform'), "
    "json_extract(context_json, '$.audience.scope.kind'), "
    "json_extract(context_json, '$.audience.scope.native_scope_id'), "
    "json_extract(context_json, '$.audience.scope.bot_id'), "
    "json_extract(context_json, '$.audience.scope.account_id'), "
    "json_extract(context_json, '$.audience.scope.session_id'), observed_at DESC)",
    """CREATE TABLE IF NOT EXISTS conversation_native_sends (
        observation_id TEXT PRIMARY KEY, turn_id TEXT NOT NULL, sent_at TEXT NOT NULL
    )""",
    *(
        f"""CREATE INDEX IF NOT EXISTS {table}_private_participant_idx ON {table}(
            json_extract(context_json, '$.role_binding.role.subject_id'),
            json_extract(context_json, '$.role_binding.scope.bot_id'),
            json_extract(context_json, '$.role_binding.scope.installation_id'),
            json_extract(context_json, '$.audience.scope.kind'),
            json_extract(context_json, '$.actor.subject.subject_id'))"""
        for table in ("conversation_batches", "conversation_native_turns",
                      "conversation_native_observations")
    ),
    # ``private_scope.private_people`` / ``role_private_contexts`` query
    # conversation_batches by (role, bot, installation) ORDER BY rowid DESC.
    """CREATE INDEX IF NOT EXISTS conversation_batches_role_recent_idx ON conversation_batches(
        json_extract(context_json, '$.role_binding.role.subject_id'),
        json_extract(context_json, '$.role_binding.scope.bot_id'),
        json_extract(context_json, '$.role_binding.scope.installation_id'))""",
    # ``scope_family._windows`` resolves both group and private scope families
    # across conversation_native_turns and conversation_batches ORDER BY rowid DESC.
    *(
        f"""CREATE INDEX IF NOT EXISTS {table}_scope_family_idx ON {table}(
            json_extract(context_json, '$.role_binding.scope.installation_id'),
            json_extract(context_json, '$.role_binding.scope.platform'),
            json_extract(context_json, '$.role_binding.scope.bot_id'),
            json_extract(context_json, '$.role_binding.scope.account_id'),
            json_extract(context_json, '$.role_binding.scope.native_scope_id'),
            json_extract(context_json, '$.audience.scope.kind'))"""
        for table in ("conversation_native_turns", "conversation_batches")
    ),
)
