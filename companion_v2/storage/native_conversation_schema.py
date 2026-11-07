"""Optional native conversation observations, independent of V2 reply batches."""


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
    "CREATE INDEX IF NOT EXISTS conversation_native_source_idx ON conversation_native_observations(json_extract(source_json, '$.source_id'))",
    """CREATE TABLE IF NOT EXISTS conversation_native_turns (
        turn_id TEXT PRIMARY KEY, context_key TEXT NOT NULL, context_json TEXT NOT NULL,
        role_instruction TEXT NOT NULL, input_observation_id TEXT,
        observed_at TEXT NOT NULL, generation_state TEXT NOT NULL
            CHECK(generation_state IN ('prepared', 'running', 'completed', 'failed')),
        phase_at TEXT NOT NULL, last_sent_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS conversation_native_turn_context_idx ON conversation_native_turns(context_key, observed_at DESC, turn_id)",
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
)
