"""Query budget of the 学习与反思 reads: the page must not scale with row count.

The dashboard lists learned rules one page at a time, but re-checking each
rule's original samples used to re-scan the whole sample window once per rule,
and the per-rule lookups (run row, stored form, rotation counters) each opened
their own statement. Every one of those statements competes for the single
storage lock, so a 24-rule page held it for seconds and every other request in
the process queued behind it.

These tests assert *statement counts*, never wall-clock time: the contract is
"one page costs a bounded number of reads", which cannot flake on a busy machine.
"""

import json
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from astrbot_host.management_identity import dashboard_operator
from companion_v2.conversation.native_observations import inbound_event
from companion_v2.execution.models import TaskError, digest, encode, stamp
from companion_v2.expression import rule_details, style_rotation
from companion_v2.expression.style_management import ExpressionStyleManagement
from companion_v2.foundation.refs import (RoleOwnerRef, ScopeKind, ScopeRef, SubjectKind,
                                          SubjectRef)
from companion_v2.role_work.journal.self_review_management import SelfReviewManagement
from companion_v2.storage.runtime import StorageRuntime
from tests.group_host_support import GroupFixture


NOW = datetime(2026, 11, 8, 22, 0, tzinfo=timezone(timedelta(hours=8)))
OWNER = RoleOwnerRef("install-1", "bot-1", "kazusa")
SCOPE = ScopeRef("install-1", "qq", ScopeKind.PRIVATE, "private-kazusa", bot_id="bot-1",
                 account_id="acc-1", conversation_id="conv-1", session_id="session-1")
CONTEXT = SimpleNamespace(key="companion:role:kazusa", scope=SCOPE)
OPERATOR = SimpleNamespace(subject=SubjectRef("install-1", SubjectKind.HUMAN, "owner-1"),
                           binding_id="binding:1", binding_revision=3)


FORM = {"kind": "style", "situation": "群里聊到熟悉的话题时", "pattern": "一句话说完", "avoid": ""}
APPLICABILITY = {"channels": ["group"], "relationship_stages": ["any"],
                 "emotion_gates": ["any"], "intent": "any", "keywords": []}


class StatementCounter:
    """Count the statements one read issues through the shared storage runtime."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.counts = {"read_one": 0, "read_all": 0, "read_snapshot": 0}
        self._saved = {}

    def __enter__(self):
        for name in self.counts:
            self.counts[name] = 0
            self._saved[name] = getattr(self.runtime, name)
            setattr(self.runtime, name, self._wrap(name, self._saved[name]))
        return self

    def _wrap(self, name, original):
        def counted(*arguments, **keywords):
            self.counts[name] += 1
            return original(*arguments, **keywords)
        return counted

    def __exit__(self, exception_type, exception, traceback):
        for name, original in self._saved.items():
            setattr(self.runtime, name, original)
        self._saved = {}
        return False

    @property
    def statements(self):
        return self.counts["read_one"] + self.counts["read_all"]


class LearningPageQueryBudgetTests(GroupFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = await self.start_host()
        self.sequence = 0
        self.counter = StatementCounter(self.app.runtime)

    async def observed(self, *, text, sender="200"):
        """One group message captured as a native observation."""
        self.sequence += 1
        result = await self.controller.prepare_core_reply(
            self.group_event(identifier="budget-" + str(self.sequence), text=text, sender=sender))
        self.assertTrue(result.is_success, result.failure)
        prepared = result.unwrap()
        self.assertIsNotNone(await prepared["native_source_future"])
        return prepared["context"]

    def sample(self, context):
        row = self.app.runtime.read_one("""SELECT * FROM conversation_native_observations
            WHERE kind = 'input' AND context_key = ? ORDER BY observed_at DESC LIMIT 1""",
            (context.key,))
        return self.app.expression_samples.input_handle(inbound_event(row))

    @staticmethod
    def owner_key(context):
        return digest(RoleOwnerRef(context.scope.installation_id, context.scope.bot_id,
                                   context.role_binding.role.subject_id))

    def write_rule(self, context, *, index, handle, closed_at):
        at = stamp(self.app.tasks.now())
        owner_key, scope_key = self.owner_key(context), digest(context.scope)
        run_id = f"expression-run:budget:{index}"
        signal_id = run_id + ":0"
        window = {"opened_at": stamp(closed_at - timedelta(hours=1)), "closed_at": stamp(closed_at),
                  "handles": [handle], "characters": 12,
                  "context_key": context.key, "source_kind": "conversation_input"}
        with self.app.runtime.transaction() as connection:
            connection.execute("""INSERT INTO expression_style_runs
                (run_id, owner_key, scope_key, sample_json, material_hash, summary, state,
                 created_at, decided_at) VALUES (?, ?, ?, ?, ?, ?, 'decided', ?, ?)""",
                (run_id, owner_key, scope_key, encode(window), run_id + ":material",
                 f"对话表达 {index}", at, at))
            connection.execute("""INSERT INTO expression_style_signals
                (signal_id, run_id, owner_key, scope_key, dimension, observation, proposal,
                 applicability_json, confidence, evidence_json, state, created_at, decided_at,
                 decided_by, decision_key, ordinal)
                VALUES (?, ?, ?, ?, 'diction', ?, ?, ?, 0.8, ?, 'adopted', ?, ?,
                        'tester', ?, 0)""",
                (signal_id, run_id, owner_key, scope_key, f"观察 {index}", f"建议 {index}",
                 encode(APPLICABILITY), encode([handle]), at, at, "tester:" + signal_id))
        rule_details.write(self.app.runtime, signal_id, FORM, at=self.app.tasks.now(),
                           actor="tester", operation_key=f"budget-form-{index}")

    def page(self, context, state="adopted"):
        return ExpressionStyleManagement(self.app).list(
            operator=dashboard_operator(self.app.runtime, "tester", at=self.app.tasks.now()),
            context=context, state=state)

    async def seeded_rules(self, count):
        """`count` adopted rules, each citing one real message of the same window."""
        context = None
        for index in range(count):
            context = await self.observed(text=f"群里的第 {index} 句话，说完整")
            handle = self.sample(context)
            self.write_rule(context, index=index, handle=handle, closed_at=self.app.tasks.now())
        return context

    async def test_one_rule_costs_the_same_wherever_its_sample_sits_in_the_window(self):
        """Finding a rule's sample must not rescan the messages it does not cite.

        The recheck used to read every observed message of the window, so a rule
        citing an old sample cost more than one citing a fresh sample, and the
        whole window's reads were repeated for every rule on the page.
        """
        context = None
        for index in range(3):
            context = await self.observed(text=f"窗口里的第 {index} 句话")
        self.write_rule(context, index=0, handle=self.oldest_sample(context),
                        closed_at=self.app.tasks.now())
        with self.counter:
            short = self.page(context)
        self.assertTrue(short.is_success, short.failure)
        self.assertEqual(len(short.unwrap()["items"]), 1)
        self.assertTrue(short.unwrap()["items"][0]["evidence"],
                        "the cited message is still reported")
        in_short_window = self.counter.statements
        # The same rule, now the oldest message of a window six times longer.
        for index in range(3, 18):
            context = await self.observed(text=f"窗口里的第 {index} 句话")
        closed_at = self.app.tasks.now()
        with self.app.runtime.transaction() as connection:
            connection.execute("""UPDATE expression_style_runs
                SET sample_json = json_set(sample_json, '$.closed_at', ?)""",
                (stamp(closed_at),))
        with self.counter:
            long = self.page(context)
        self.assertTrue(long.is_success, long.failure)
        self.assertTrue(long.unwrap()["items"][0]["evidence"])
        self.assertLessEqual(self.counter.statements, in_short_window,
                             f"3-message window={in_short_window} statements, "
                             f"18-message window={self.counter.statements} statements: "
                             f"the window is rescanned")

    def oldest_sample(self, context):
        row = self.app.runtime.read_one("""SELECT * FROM conversation_native_observations
            WHERE kind = 'input' AND context_key = ? ORDER BY observed_at ASC, rowid ASC LIMIT 1""",
            (context.key,))
        return self.app.expression_samples.input_handle(inbound_event(row))

    async def test_page_cost_per_rule_does_not_grow_with_the_family(self):
        """Adding rules to the family must not make each rule cost more.

        The injectable-count pass reads every pending/adopted rule of the family,
        so a per-row sample-context lookup there made the page quadratic.
        """
        context = await self.seeded_rules(2)
        with self.counter:
            self.assertTrue(self.page(context).is_success)
        two_rules = self.counter.statements
        for index in range(2, 12):
            self.write_rule(context, index=index, handle=self.sample(context),
                            closed_at=self.app.tasks.now())
        with self.counter:
            page = self.page(context)
        self.assertTrue(page.is_success, page.failure)
        self.assertEqual(len(page.unwrap()["items"]), 12)
        # Linear with a small constant per rule; quadratic growth would be ~36x.
        self.assertLessEqual(self.counter.statements, two_rules * 6,
                             f"2 rules={two_rules} statements, 12 rules="
                             f"{self.counter.statements} statements")

    async def test_rule_list_index_is_used_for_the_page_query(self):
        """The page read must stay indexed; a schema edit cannot silently drop it."""
        context = await self.seeded_rules(1)
        plan = " ".join(row["detail"] for row in self.app.runtime.read_all(
            "EXPLAIN QUERY PLAN SELECT * FROM expression_style_signals "
            "WHERE owner_key = ? AND scope_key IN (?) AND state = ? "
            "ORDER BY created_at DESC, signal_id DESC LIMIT ?",
            (self.owner_key(context), digest(context.scope), "adopted", 51)))
        self.assertIn("expression_style_signal", plan)
        self.assertNotIn("SCAN expression_style_signals", plan)

    def test_self_review_list_index_is_used_for_each_bucket(self):
        plan = " ".join(row["detail"] for row in self.app.runtime.read_all(
            "EXPLAIN QUERY PLAN SELECT * FROM role_self_review_items "
            "WHERE owner_key = ? AND scope_key = ? AND (state = 'pending') "
            "ORDER BY created_at DESC, item_id DESC LIMIT ?", ("o", "s", 51)))
        self.assertIn("role_self_review_item", plan)
        self.assertNotIn("SCAN role_self_review_items", plan)

    async def test_batched_lookups_issue_one_statement_and_match_the_per_row_reads(self):
        """The page's shared lookups cost one statement, with identical results."""
        context = await self.seeded_rules(6)
        owner_key = self.owner_key(context)
        scopes = (digest(context.scope),)
        signal_ids = [row["signal_id"] for row in self.app.runtime.read_all(
            "SELECT signal_id FROM expression_style_signals WHERE state = 'adopted'", ())]
        self.assertEqual(len(signal_ids), 6)
        with self.counter:
            forms = rule_details.read_many(self.app.runtime, signal_ids)
            rotation = style_rotation.usage_many(self.app.runtime, owner_key, scopes)
        self.assertEqual(self.counter.statements, 2,
                         f"{self.counter.statements} statements for two batched lookups")
        self.assertEqual(sorted(forms), sorted(signal_ids))
        for signal_id in signal_ids:
            self.assertEqual(forms[signal_id], rule_details.read(self.app.runtime, signal_id))
        for scope_key in scopes:
            self.assertEqual(rotation[scope_key],
                             style_rotation.usage(self.app.runtime, owner_key, scope_key))

    async def test_page_runs_are_read_once(self):
        context = await self.seeded_rules(6)
        rows = self.app.runtime.read_all(
            "SELECT * FROM expression_style_signals WHERE state = 'adopted'", ())
        manager = ExpressionStyleManagement(self.app)
        with self.counter:
            runs = manager._page_runs(rows, self.owner_key(context))
        self.assertEqual(self.counter.statements, 1,
                         f"{self.counter.statements} statements for one page of run rows")
        self.assertEqual(len(runs), 6)
        for row in rows:
            self.assertIsNotNone(runs.get((row["run_id"], row["scope_key"])))


class UnreadableEvidence:
    """A sample recheck that fails the way a reset window fails."""

    def describe_handles(self, handles, opened_at, closed_at, context, at):
        raise TaskError("conversation_context_reset", "native history precedes this window's reset")


class SelfReviewReadTests(unittest.TestCase):
    """A failed recheck is one row's reason, never a 500 for the whole bucket."""

    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="companion-v2-self-review-budget-")
        self.addCleanup(self.temporary.cleanup)
        self.runtime = StorageRuntime.open(data_root=Path(self.temporary.name) / "v2-data",
                                          installation_id="self-review-budget")
        self.addCleanup(self.runtime.close)
        app = SimpleNamespace(runtime=self.runtime, role_self_review_evidence=UnreadableEvidence(),
                              role_self_review=None, notify=lambda: None,
                              role_growth=SimpleNamespace(runtime=self.runtime))
        self.management = SelfReviewManagement(app)
        self.management.management._authorize = lambda operator, context, at: (OWNER, 4)
        self.management.management.ledger = SimpleNamespace(now=lambda: NOW)

    def seed(self):
        at = stamp(NOW)
        window = {"opened_at": at, "closed_at": at, "handles": ["input:gone"],
                  "kinds": ["ask_user"]}
        with self.runtime.transaction() as connection:
            connection.execute("INSERT INTO role_self_review_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("self-review-run:unreadable", digest(OWNER), digest(SCOPE), json.dumps(window),
                 "material", "一次性复盘", "pending", at, None))
            connection.execute("INSERT INTO role_self_review_items VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("self-review-item:unreadable", "self-review-run:unreadable", digest(OWNER),
                 digest(SCOPE), "ask_user", "少说一句", "先问再答", 0.6,
                 json.dumps(["input:gone"]), at, "pending", at, None, None, None, 0))

    def test_unreadable_evidence_degrades_instead_of_raising(self):
        self.seed()
        page = self.management.list(operator=OPERATOR, context=CONTEXT, state="pending", limit=20)
        self.assertTrue(page.is_success, page.failure)
        items = page.unwrap()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["unavailable_reason"], "self_review_evidence_changed")
        self.assertEqual(items[0]["actions"], ["reject"])


class SelfReviewOffLoopTests(GroupFixture, unittest.IsolatedAsyncioTestCase):
    """The growth reads must not run their SQLite work on the event loop."""

    async def asyncSetUp(self):
        self.app = await self.start_host()
        self.controller.register_routes()

    async def test_growth_self_review_read_runs_off_the_event_loop(self):
        from astrbot.api.web import PluginRequest, bind_request_context
        from starlette.requests import Request

        loop_thread = threading.get_ident()
        seen = []
        original = SelfReviewManagement.list

        def record(manager, **arguments):
            seen.append(threading.get_ident())
            return original(manager, **arguments)

        candidates = [(route, handler) for route, handler, methods, _ in self.native.registered_web_apis
                      if route.endswith("/growth/self-review") and "GET" in methods]
        self.assertTrue(candidates, "the growth self-review route is not registered")
        path, handler = candidates[0]

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        native = Request({"type": "http", "method": "GET", "path": path,
                          "headers": [], "query_string": b"state=pending"}, receive=receive)
        SelfReviewManagement.list = record
        self.addCleanup(setattr, SelfReviewManagement, "list", original)
        with bind_request_context(PluginRequest(native, username="tester")):
            await handler()
        self.assertEqual(len(seen), 1, "the read did not run")
        self.assertNotEqual(seen[0], loop_thread,
                            "the SQLite read still ran on the event loop thread")


if __name__ == "__main__":
    unittest.main()
