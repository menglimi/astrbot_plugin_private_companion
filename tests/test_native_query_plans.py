"""EXPLAIN QUERY PLAN regressions for native observation, batch, and turn lookups.

Production slow-query logs identified four high-frequency read paths on large
stores:
1. ``companion_v2.conversation.native_history.rows``
2. ``companion_v2.expression.private_scope.private_people`` / ``role_private_contexts``
3. ``companion_v2.conversation.scope_family._windows`` (group & private families)
4. ``companion_v2.conversation.native_turns.context_rows`` / ``latest`` / ``latest_input``

Each test captures the exact SQL statement emitted by the production function
against a real ``StorageRuntime`` and verifies SQLite serves it via index
``SEARCH`` without a full table ``SCAN`` or ``TEMP B-TREE FOR ORDER BY`` where
covered by the index.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest import mock

from companion_v2.conversation import native_history
from companion_v2.conversation.native_turns import (
    NativeConversationTurns,
    context_rows,
    latest,
    latest_input,
)
from companion_v2.conversation.scope_family import group_scope_keys, private_scope_keys
from companion_v2.execution.models import TaskContext, encode, stamp
from companion_v2.expression.private_scope import private_people
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import AudienceRef, ScopeKind, ScopeRef, SubjectKind, SubjectRef
from companion_v2.identity_policy.models import AuthenticatedHostPrincipal, ResolvedIdentity, RoleBinding
from companion_v2.storage.runtime import StorageRuntime


INSTALLATION_ID = "native-index-plan-inst"
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def make_context(index: int, kind: ScopeKind = ScopeKind.PRIVATE) -> TaskContext:
    scope = ScopeRef(
        INSTALLATION_ID, "onebot", kind, f"native-{index}",
        "bot-a", "login-a", f"conv-{index}", f"sess-{index}",
    )
    subject = SubjectRef(INSTALLATION_ID, SubjectKind.HUMAN, f"user-{index}")
    return TaskContext(
        ResolvedIdentity(
            AuthenticatedHostPrincipal(
                INSTALLATION_ID, "host-a", "onebot", f"acct-{index}", "bot-a", "login-a",
            ),
            subject, f"binding-{index}", 1, 1,
        ),
        RoleBinding(scope, SubjectRef(INSTALLATION_ID, SubjectKind.BOT, "role-a"), 1),
        AudienceRef(scope, (subject,)),
    )


class NativeQueryPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = TemporaryDirectory(prefix="companion-native-qplan-")
        self.addCleanup(self._dir.cleanup)
        self.runtime = StorageRuntime.open(
            data_root=Path(self._dir.name) / "v2",
            installation_id=INSTALLATION_ID,
        )
        self.addCleanup(self.runtime.close)
        NativeConversationTurns(self.runtime)

    def _explain(self, statement: str, parameters: tuple[object, ...]) -> str:
        connection = sqlite3.connect(self.runtime.database_path)
        try:
            return " | ".join(
                str(tuple(row))
                for row in connection.execute("EXPLAIN QUERY PLAN " + statement, parameters)
            )
        finally:
            connection.close()

    def _capture_calls(self, method_name: str, fn):
        recorded: list[tuple[str, tuple[object, ...]]] = []
        original = getattr(self.runtime, method_name)

        def spy(statement, parameters):
            recorded.append((statement, tuple(parameters)))
            return original(statement, parameters)

        with mock.patch.object(self.runtime, method_name, side_effect=spy):
            result = fn()
        return result, recorded

    def _seed_observation(self, obs_id: str, ctx: TaskContext, observed_at: datetime, *,
                          kind: str = "input", delivery_state: str = "input_observed") -> None:
        with self.runtime.transaction() as connection:
            connection.execute(
                """INSERT INTO conversation_native_observations
                   (observation_id, context_key, memory_owner, conversation_owner, turn_id, event_id,
                    kind, delivery_state, context_json, source_json, content_hash, observed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', 'hash', ?)""",
                (obs_id, ctx.key, "mem-owner", "conv-owner", f"turn-{obs_id}", f"ev-{obs_id}",
                 kind, delivery_state, encode(ctx), stamp(observed_at)),
            )

    def _seed_turn(self, turn_id: str, ctx: TaskContext, observed_at: datetime,
                   state: str = "completed") -> None:
        with self.runtime.transaction() as connection:
            connection.execute(
                """INSERT INTO conversation_native_turns
                   (turn_id, context_key, context_json, role_instruction, input_observation_id,
                    observed_at, generation_state, phase_at, last_sent_at)
                   VALUES (?, ?, ?, 'instruction', ?, ?, ?, ?, NULL)""",
                (turn_id, ctx.key, encode(ctx), f"in-{turn_id}", stamp(observed_at),
                 state, stamp(observed_at)),
            )

    def test_native_history_rows_uses_context_delivery_index(self) -> None:
        ctx = make_context(1, ScopeKind.PRIVATE)
        self._seed_observation("obs-1", ctx, NOW, kind="input", delivery_state="input_observed")
        self._seed_observation("obs-2", ctx, NOW + timedelta(seconds=1),
                               kind="assistant", delivery_state="send_returned")

        # 1) Live query with conversation_native_sends present: outer table seeks via effective_at index with zero TEMP B-TREE.
        rows, calls = self._capture_calls("read_all", lambda: native_history.rows(self.runtime, ctx, limit=10))
        self.assertEqual(len(rows), 2)
        stmt, params = calls[-1]
        plan = self._explain(stmt, params)
        self.assertIn("SEARCH n USING INDEX idx_native_obs_ctx_effective", plan)
        self.assertNotIn("SCAN n", plan)
        self.assertNotIn("TEMP B-TREE", plan)

        # 2) Same query on conversation_native_observations alone (no sends table): zero TEMP B-TREE too.
        no_sends_plan = self._explain(
            "SELECT n.*, NULL AS sent_at FROM conversation_native_observations n "
            "WHERE n.context_key = ? AND n.delivery_state IN ('input_observed', 'send_returned') "
            "ORDER BY n.observed_at DESC, n.rowid DESC LIMIT ?",
            (ctx.key, 10),
        )
        self.assertIn("SEARCH n USING INDEX conversation_native_obs_context_delivery_idx", no_sends_plan)
        self.assertNotIn("SCAN n", no_sends_plan)
        self.assertNotIn("TEMP B-TREE", no_sends_plan)

    def test_private_scope_queries_use_role_recent_indexes_without_temp_btree(self) -> None:
        ctx = make_context(1, ScopeKind.PRIVATE)
        self._seed_observation("obs-priv-1", ctx, NOW)
        identity = SimpleNamespace(
            resolve_authenticated=lambda principal, observed_at: Outcome.success(ctx.actor),
            resolve_role=lambda scope, observed_at: Outcome.success(ctx.role_binding),
        )

        people, calls = self._capture_calls("read_all", lambda: private_people(self.runtime, identity, ctx, NOW))
        self.assertEqual(len(people), 1)
        by_table = {}
        for stmt, params in calls:
            if "FROM conversation_batches" in stmt:
                by_table["conversation_batches"] = self._explain(stmt, params)
            elif "FROM conversation_native_observations" in stmt:
                by_table["conversation_native_observations"] = self._explain(stmt, params)

        self.assertIn("conversation_batches", by_table)
        self.assertIn("SEARCH conversation_batches USING INDEX conversation_batches_role_recent_idx",
                      by_table["conversation_batches"])
        self.assertNotIn("SCAN conversation_batches", by_table["conversation_batches"])
        self.assertNotIn("TEMP B-TREE", by_table["conversation_batches"])

        self.assertIn("conversation_native_observations", by_table)
        self.assertIn("SEARCH conversation_native_observations USING INDEX conversation_native_obs_role_input_recent_idx",
                      by_table["conversation_native_observations"])
        self.assertNotIn("SCAN conversation_native_observations", by_table["conversation_native_observations"])
        self.assertNotIn("TEMP B-TREE", by_table["conversation_native_observations"])

    def test_scope_family_group_and_private_queries_use_composite_indexes_without_temp_btree(self) -> None:
        priv = make_context(1, ScopeKind.PRIVATE)
        grp = make_context(2, ScopeKind.GROUP)
        self._seed_turn("turn-p1", priv, NOW)
        self._seed_turn("turn-g1", grp, NOW)

        for label, fn in (
            ("group", lambda: group_scope_keys(self.runtime, grp.scope, grp.role_binding.role)),
            ("private", lambda: private_scope_keys(self.runtime, priv.scope, priv.role_binding.role)),
        ):
            with self.subTest(family=label):
                keys, calls = self._capture_calls("read_all", fn)
                self.assertTrue(keys)
                checked = 0
                for stmt, params in calls:
                    for table in ("conversation_native_turns", "conversation_batches"):
                        if f"FROM {table}" in stmt:
                            plan = self._explain(stmt, params)
                            self.assertIn(f"SEARCH {table} USING INDEX {table}_scope_family_idx", plan)
                            self.assertNotIn(f"SCAN {table}", plan)
                            self.assertNotIn("TEMP B-TREE", plan)
                            checked += 1
                self.assertEqual(checked, 2)

    def test_native_turns_latest_and_context_rows_avoid_full_table_scans(self) -> None:
        # Empty table must return empty cleanly under the recursive loose index scan.
        self.assertEqual(context_rows(self.runtime), ())
        self.assertEqual(context_rows(self.runtime, kind="group"), ())

        priv = make_context(1, ScopeKind.PRIVATE)
        grp = make_context(2, ScopeKind.GROUP)
        self._seed_turn("t-p1", priv, NOW, "completed")
        self._seed_turn("t-p2", priv, NOW + timedelta(seconds=5), "running")
        self._seed_turn("t-g1", grp, NOW, "completed")
        self._seed_turn("t-g2", grp, NOW, "completed")  # tie broken by rowid DESC

        # latest() and latest_input(): exact index SEARCH, zero SCAN, zero TEMP B-TREE.
        for label, fn in (
            ("latest", lambda: latest(self.runtime, priv.key)),
            ("latest_input", lambda: latest_input(self.runtime, priv.key)),
        ):
            with self.subTest(query=label):
                row, calls = self._capture_calls("read_one", fn)
                self.assertIsNotNone(row)
                stmt, params = calls[-1]
                plan = self._explain(stmt, params)
                self.assertIn("SEARCH conversation_native_turns USING INDEX conversation_native_turn_context_recent_idx", plan)
                self.assertNotIn("SCAN conversation_native_turns", plan)
                self.assertNotIn("TEMP B-TREE", plan)

        # context_rows(kind="group", open_only=False): loose index scan via covering index, no full table SCAN.
        group_rows, calls = self._capture_calls("read_all", lambda: context_rows(self.runtime, kind="group"))
        self.assertEqual([r["turn_id"] for r in group_rows], ["t-g2"])
        stmt, params = calls[-1]
        plan = self._explain(stmt, params)
        self.assertIn("conversation_native_turn_context_recent_idx", plan)
        self.assertNotIn("SCAN n", plan)
        self.assertNotIn("SCAN conversation_native_turns", plan)

        # context_rows(kind="private", open_only=True): still served by conversation_native_open_turn_idx,
        # and correlated NOT EXISTS seeks by (context_key=? AND observed_at>?).
        open_rows, calls = self._capture_calls(
            "read_all", lambda: context_rows(self.runtime, kind="private", open_only=True),
        )
        self.assertEqual([r["turn_id"] for r in open_rows], ["t-p2"])
        stmt, params = calls[-1]
        plan = self._explain(stmt, params)
        self.assertIn("conversation_native_open_turn_idx", plan)
        self.assertIn("conversation_native_turn_context_recent_idx (context_key=? AND observed_at>?)", plan)

    def test_store_with_new_composite_indexes_reopens_cleanly(self) -> None:
        data_root = self.runtime.database_path.parent
        self.runtime.close()
        reopened = StorageRuntime.open(data_root=data_root, installation_id=INSTALLATION_ID)
        try:
            names = {
                row["name"]
                for row in reopened.read_all("SELECT name FROM sqlite_master WHERE type = 'index'", ())
            }
        finally:
            reopened.close()
        expected = {
            "conversation_native_obs_context_delivery_idx",
            "conversation_native_obs_role_input_recent_idx",
            "conversation_batches_role_recent_idx",
            "conversation_native_turns_scope_family_idx",
            "conversation_batches_scope_family_idx",
            "conversation_native_turn_context_recent_idx",
        }
        self.assertTrue(expected.issubset(names), expected - names)


if __name__ == "__main__":
    unittest.main()
