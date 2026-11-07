"""Single-participant inference for the default expression learning source."""

import json
import sqlite3
import unittest
from types import SimpleNamespace as NS

from companion_v2.expression.configuration import _sole_private_participant
from companion_v2.storage.native_conversation_schema import STATEMENTS


class FakeRuntime:
    def __init__(self, rows, *, fail=False):
        self.rows, self.fail = rows, fail
        self.queries = []

    def read_all(self, query, parameters):
        self.queries.append(query)
        if self.fail:
            raise RuntimeError("table unavailable")
        return self.rows


def context():
    scope = NS(bot_id="bot-a", installation_id="installation")
    role = NS(subject_id="role-a")
    return NS(scope=scope, role_binding=NS(role=role))


class SolePrivateParticipantTests(unittest.TestCase):
    def test_sqlite_uses_private_participant_indexes_and_keeps_ambiguity(self):
        tables = ("conversation_batches", "conversation_native_turns",
                  "conversation_native_observations")
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.row_factory = sqlite3.Row
        for table in tables:
            connection.execute(f"CREATE TABLE {table} (context_json TEXT NOT NULL)")
        for statement in STATEMENTS:
            if "_private_participant_idx" in statement:
                connection.execute(statement)

        def record(table, subject):
            document = {"role_binding": {"role": {"subject_id": "role-a"},
                        "scope": {"bot_id": "bot-a", "installation_id": "installation"}},
                        "audience": {"scope": {"kind": "private"}},
                        "actor": {"subject": {"subject_id": subject}}}
            connection.execute(f"INSERT INTO {table} VALUES (?)", (json.dumps(document),))

        class Runtime:
            def __init__(self):
                self.queries = []

            def read_all(self, query, parameters):
                self.queries.append((query, parameters))
                return connection.execute(query, parameters).fetchall()

        runtime = Runtime()
        record("conversation_native_observations", "onebot-user:1")
        self.assertEqual(_sole_private_participant(runtime, context()), "onebot-user:1")
        for (query, parameters), table in zip(runtime.queries, tables):
            plan = tuple(row[3] for row in connection.execute(
                "EXPLAIN QUERY PLAN " + query, parameters))
            self.assertTrue(any(f"{table}_private_participant_idx" in step for step in plan), plan)
        record("conversation_native_turns", "onebot-user:2")
        self.assertIsNone(_sole_private_participant(runtime, context()))

    def test_one_participant_becomes_the_implicit_main_companion(self):
        runtime = FakeRuntime([{"subject": "onebot-user:1"}])
        self.assertEqual(_sole_private_participant(runtime, context()), "onebot-user:1")

    def test_several_participants_stay_ambiguous(self):
        runtime = FakeRuntime([{"subject": "onebot-user:1"}, {"subject": "onebot-user:2"}])
        self.assertIsNone(_sole_private_participant(runtime, context()))

    def test_missing_tables_do_not_break_the_default(self):
        runtime = FakeRuntime([{"subject": "onebot-user:1"}], fail=True)
        self.assertIsNone(_sole_private_participant(runtime, context()))


if __name__ == "__main__":
    unittest.main()
