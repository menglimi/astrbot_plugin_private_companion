"""The per-pass native-turn read must not walk the whole turn table.

``presence`` asks for the turns that can still be typing on every drive pass, and
``latest_scope`` probes one scope per active window. Both used to be full table
scans: "newest turn of this context" proves itself by looking at every row of the
context, and the seven-field scope probe had no index at all. On a store with a
real history that is a multi-second stall inside the event loop, which the user
sees as a page request that never returns.

These tests pin the shape that makes those reads cheap — and, more importantly,
pin that the cheap answer is exactly the answer the expensive one gave.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from companion_v2.conversation.native_turns import (NativeConversationTurns, context_rows,
                                                    latest_scope)
from companion_v2.execution.models import TaskContext, encode, stamp
from companion_v2.foundation.refs import (AudienceRef, ScopeKind, ScopeRef, SubjectKind,
                                          SubjectRef)
from companion_v2.identity_policy.models import (AuthenticatedHostPrincipal, ResolvedIdentity,
                                                 RoleBinding)
from companion_v2.storage.native_conversation_schema import OPEN_TURN_STATES
from companion_v2.storage.runtime import StorageRuntime


INSTALLATION_ID = "plan-installation"
NOW = datetime(2026, 9, 21, 6, 0, tzinfo=timezone.utc)


def context_for(index, kind=ScopeKind.PRIVATE):
    scope = ScopeRef(INSTALLATION_ID, "onebot", kind, f"native-{index}", "bot-a", "login-a",
                     f"conversation-{index}", f"session-{index}")
    subject = SubjectRef(INSTALLATION_ID, SubjectKind.HUMAN, f"user-{index}")
    return TaskContext(
        ResolvedIdentity(
            AuthenticatedHostPrincipal(INSTALLATION_ID, "host-a", "onebot", "native-account-a",
                                       "bot-a", "login-a"),
            subject, f"binding-{index}", 2, 7),
        RoleBinding(scope, SubjectRef(INSTALLATION_ID, SubjectKind.BOT, "role-a"), 1),
        AudienceRef(scope, (subject,)))


class NativeTurnStore(unittest.TestCase):
    """A real store with the native-conversation tables the host creates."""

    def setUp(self):
        directory = TemporaryDirectory(prefix="companion-native-plan-")
        self.addCleanup(directory.cleanup)
        self.runtime = StorageRuntime.open(data_root=Path(directory.name) / "v2",
                                           installation_id=INSTALLATION_ID)
        self.addCleanup(self.runtime.close)
        NativeConversationTurns(self.runtime)

    def add_turn(self, turn_id, context, observed_at, state):
        with self.runtime.transaction() as connection:
            connection.execute("""INSERT INTO conversation_native_turns
                (turn_id, context_key, context_json, role_instruction, input_observation_id,
                 observed_at, generation_state, phase_at, last_sent_at)
                VALUES (?, ?, ?, 'instruction', ?, ?, ?, ?, NULL)""",
                (turn_id, context.key, encode(context), f"input-{turn_id}", stamp(observed_at),
                 state, stamp(observed_at)))

    def issued(self, method, call):
        """The statement and parameters one runtime call actually issued."""

        with mock.patch.object(self.runtime, method, wraps=getattr(self.runtime, method)) as spy:
            call()
        return spy.call_args_list[-1][0]

    def plan(self, statement, parameters):
        # A second connection reads the same schema; the store itself is in WAL mode.
        connection = sqlite3.connect(self.runtime.database_path)
        try:
            return " | ".join(str(tuple(row)) for row in
                              connection.execute("EXPLAIN QUERY PLAN " + statement, parameters))
        finally:
            connection.close()


class NativeTurnReadPlanTests(NativeTurnStore):
    def test_scope_probe_is_an_index_seek_instead_of_a_table_scan(self):
        self.add_turn("turn-1", context_for(1), NOW, "completed")
        statement, parameters = self.issued("read_one",
                                            lambda: latest_scope(self.runtime, context_for(1).scope))
        plan = self.plan(statement, parameters)
        self.assertIn("conversation_native_scope_idx", plan)
        self.assertNotIn("SCAN conversation_native_turns", plan)

    def test_open_turn_pass_is_served_by_the_partial_index(self):
        self.add_turn("turn-open", context_for(1), NOW, "running")
        for index in range(2, 12):
            self.add_turn(f"turn-done-{index}", context_for(index), NOW, "completed")
        statement, parameters = self.issued(
            "read_all",
            lambda: context_rows(self.runtime, kind="private", open_only=True))
        plan = self.plan(statement, parameters)
        self.assertIn("conversation_native_open_turn_idx", plan)


class NativeTurnOpenFilterTests(NativeTurnStore):
    """``open_only`` must answer the question the caller asked before filtering."""

    def store_superseded_and_live_turns(self):
        # A superseded open turn: the context's newest turn already finished, so
        # this window is not typing any more. Filtering before the latest-per-
        # context probe is only correct if it still excludes this row.
        superseded = context_for(1)
        self.add_turn("turn-superseded", superseded, NOW, "running")
        self.add_turn("turn-finished", superseded, NOW + timedelta(seconds=5), "completed")
        # A newest open turn stays visible.
        live = context_for(2)
        self.add_turn("turn-live", live, NOW, "completed")
        self.add_turn("turn-live-open", live, NOW + timedelta(seconds=9), "prepared")
        # Two rows in one context at the same instant: rowid breaks the tie.
        tied = context_for(3)
        self.add_turn("turn-tie-old", tied, NOW, "failed")
        self.add_turn("turn-tie-new", tied, NOW, "running")
        # Private and group scopes are answered independently.
        self.add_turn("turn-group", context_for(4, ScopeKind.GROUP), NOW, "running")
        return (context_for(1), context_for(2), context_for(3))

    def test_open_pass_equals_reading_every_context_and_filtering_after(self):
        private = self.store_superseded_and_live_turns()
        private_keys = {context.key for context in private}
        everything = context_rows(self.runtime)
        expected = tuple(row for row in everything if row["generation_state"] in OPEN_TURN_STATES)
        expected_private = tuple(row for row in expected if row["context_key"] in private_keys)

        self.assertEqual([row["turn_id"] for row in expected],
                         ["turn-live-open", "turn-group", "turn-tie-new"])
        self.assertEqual([row["turn_id"] for row in context_rows(self.runtime, open_only=True)],
                         ["turn-live-open", "turn-group", "turn-tie-new"])
        self.assertEqual(
            [row["turn_id"] for row in context_rows(self.runtime, kind="private", open_only=True)],
            [row["turn_id"] for row in expected_private])
        self.assertEqual(
            [row["turn_id"] for row in context_rows(self.runtime, open_only=True, kind="group")],
            ["turn-group"])

    def test_a_store_carrying_the_new_indexes_still_reopens(self):
        self.store_superseded_and_live_turns()
        data_root = self.runtime.database_path.parent
        self.runtime.close()
        reopened = StorageRuntime.open(data_root=data_root, installation_id=INSTALLATION_ID)
        try:
            names = {row["name"] for row in reopened.read_all(
                "SELECT name FROM sqlite_master WHERE type = 'index'", ())}
        finally:
            reopened.close()
        self.assertLessEqual({"conversation_native_open_turn_idx", "conversation_native_scope_idx"},
                             names)


if __name__ == "__main__":
    unittest.main()
