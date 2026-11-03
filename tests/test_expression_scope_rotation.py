"""Expression rules learned before a conversation rotation stay usable and counted.

A rotation renames the conversation, not the owner: review and injection both
resolve one scope family, so a rule that is listed as usable is also still
injected.
"""

import json
import sqlite3
import unittest
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from astrbot_host.management_identity import dashboard_operator
from companion_v2.conversation import style_context as style_context_module
from companion_v2.conversation.native_turns import NativeConversationTurns
from companion_v2.conversation.scope_family import scope_family_keys
from companion_v2.conversation.style_context import MAX_POOL_SCAN
from companion_v2.execution.models import (ResolvedIdentity, TaskContext, TaskError, digest,
                                           stamp)
from companion_v2.expression import configuration as configuration_module
from companion_v2.expression import rule_details
from companion_v2.expression.style_management import ExpressionStyleManagement
from companion_v2.foundation.refs import (AudienceRef, RoleOwnerRef, ScopeKind, ScopeRef,
                                          SubjectKind, SubjectRef)
from companion_v2.identity_policy.models import AuthenticatedHostPrincipal, RoleBinding
from tests import test_expression_v1_pipeline as pipeline
from tests.multi_host_support import MultiFixture, multi_config
from tests.test_expression_v1_pipeline import RULE, ExpressionFixture
from tests.test_host_application import HostFixture
from tests.test_management_access import SCOPE


class ExpressionScopeRotationTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def test_rules_from_the_previous_conversation_remain_readable(self):
        self.controller = self.make_controller(multi_config(delivery_mode="onebot_private"))
        app = await self.start_host()
        first, _ = await self.accepted(text="第一条")
        self.native.conversations["qq-main"] = "conversation-b"
        second, _ = await self.accepted(identifier="event-2", text="第二条")
        self.assertNotEqual(digest(first.scope), digest(second.scope))

        owner = RoleOwnerRef(second.scope.installation_id, second.scope.bot_id,
                             second.role_binding.role.subject_id)
        owner_key, old_scope = digest(owner), digest(first.scope)
        at = stamp(app.tasks.now())
        window = json.dumps({"opened_at": at, "closed_at": at, "handles": [], "characters": 0,
                             "source_kind": "shared_pack"})
        with app.runtime.transaction() as connection:
            connection.execute("INSERT INTO expression_style_runs VALUES (?,?,?,?,?,?,?,?,?)",
                               ("run-old", owner_key, old_scope, window, "hash", "旧记录", "decided", at, at))
            connection.execute("""INSERT INTO expression_style_signals
                (signal_id, run_id, owner_key, scope_key, dimension, observation, proposal, confidence,
                 evidence_json, state, created_at, decided_at, decided_by, decision_key, ordinal)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("sig-old", "run-old", owner_key, old_scope, "diction", "旧观察", "旧提案", 0.5,
                 "[]", "pending", at, None, None, None, 0))

        management = ExpressionStyleManagement(app)
        row = management._row("sig-old", owner, second)
        self.assertEqual(row["signal_id"], "sig-old")
        operator = dashboard_operator(app.runtime, "tester", at=app.tasks.now())
        result = management.list(operator=operator, context=second, state="pending")
        self.assertTrue(result.is_success, result.failure)
        self.assertIn("sig-old", [item["signal_id"] for item in result.unwrap()["items"]])


class ExpressionWindowIdentityTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    """The source check re-derives one window, not one identity snapshot.

    ``policy_revision`` is installation-wide and advances on every unrelated
    bind or grant, so a snapshot written before such a change must still count
    as the same conversation. Anything that actually changed the window — the
    participant, the conversation, or the role — must keep failing.
    """

    async def start_window(self):
        self.controller = self.make_controller(multi_config(delivery_mode="onebot_private"))
        app = await self.start_host()
        context, _ = await self.accepted(text="我不喝咖啡")
        return app, context

    @staticmethod
    def with_policy_revision(context, revision):
        return replace(context, actor=replace(context.actor, policy_revision=revision))

    async def test_the_window_key_ignores_only_the_policy_revision(self):
        _, context = await self.start_window()
        drifted = self.with_policy_revision(context, context.actor.policy_revision + 7)

        self.assertTrue(drifted.same_window(context))
        self.assertEqual(drifted.key, context.key)
        self.assertNotEqual(drifted, context)
        self.assertFalse(context.same_window(object()))

    async def test_a_policy_revision_change_does_not_move_the_window(self):
        app, context = await self.start_window()
        drifted = self.with_policy_revision(context, context.actor.policy_revision + 1)
        self.assertNotEqual(drifted, context)
        live_context, instruction, guard = app.state_maintenance.bindings[context.key]
        self.assertTrue(drifted.same_window(live_context))
        # The live binding still owns this window, so its guard is returned
        # instead of a false "the original role is no longer bound".
        self.assertIs(app.expression_style._bound(drifted, instruction), guard)

    async def test_a_new_participant_is_still_another_window(self):
        app, context = await self.start_window()
        scope = context.scope
        stranger_subject = SubjectRef(scope.installation_id, SubjectKind.HUMAN, "onebot-user:999")
        stranger = replace(context, actor=replace(context.actor, subject=stranger_subject),
                           audience=AudienceRef(scope, (stranger_subject,)))
        self.assertNotEqual(stranger.key, context.key)
        instruction = app.state_maintenance.bindings[context.key][1]

        with self.assertRaises(TaskError) as caught:
            app.expression_style._bound(stranger, instruction)
        self.assertEqual(caught.exception.code, "expression_binding_changed")

    async def test_a_rotated_conversation_is_still_another_window(self):
        app, context = await self.start_window()
        scope = replace(context.scope, conversation_id="conversation-rotated",
                        session_id="session-rotated")
        rotated = TaskContext(context.actor,
                              RoleBinding(scope, context.role_binding.role, context.role_binding.revision),
                              AudienceRef(scope, context.audience.recipients))
        self.assertNotEqual(rotated.key, context.key)
        instruction = app.state_maintenance.bindings[context.key][1]

        with self.assertRaises(TaskError) as caught:
            app.expression_style._bound(rotated, instruction)
        self.assertEqual(caught.exception.code, "expression_binding_changed")

    async def test_another_role_in_the_same_conversation_is_still_another_window(self):
        app, context = await self.start_window()
        role = context.role_binding.role
        other_role = SubjectRef(role.installation_id, role.kind, "role-someone-else")
        other = TaskContext(context.actor,
                            RoleBinding(context.scope, other_role, context.role_binding.revision),
                            context.audience)
        self.assertNotEqual(other.key, context.key)
        instruction = app.state_maintenance.bindings[context.key][1]

        with self.assertRaises(TaskError) as caught:
            app.expression_style._bound(other, instruction)
        self.assertEqual(caught.exception.code, "expression_binding_changed")

    async def test_a_changed_role_instruction_is_still_another_binding(self):
        app, context = await self.start_window()
        instruction = app.state_maintenance.bindings[context.key][1]

        with self.assertRaises(TaskError) as caught:
            app.expression_style._bound(context, instruction + "\n这是另一个角色设定。")
        self.assertEqual(caught.exception.code, "expression_binding_changed")

    async def test_a_stored_native_turn_kept_an_older_policy_revision(self):
        app, context = await self.start_window()
        instruction = app.state_maintenance.bindings[context.key][1]
        NativeConversationTurns(app.runtime).mark(
            {"context": context, "turn_id": "turn-a", "instruction": instruction}, "completed",
            at=app.tasks.now())
        app.state_maintenance.bindings.pop(context.key)
        drifted = self.with_policy_revision(context, context.actor.policy_revision + 1)

        self.assertIsNone(app.expression_style._bound(drifted, instruction))

    async def test_a_stored_private_batch_kept_an_older_policy_revision(self):
        app, context = await self.start_window()
        instruction = app.state_maintenance.bindings[context.key][1]
        app.state_maintenance.bindings.pop(context.key)
        drifted = self.with_policy_revision(context, context.actor.policy_revision + 1)

        self.assertIsNone(app.expression_style._bound(drifted, instruction))


class ExpressionLearningFamilyQuotaTests(ExpressionFixture, HostFixture, unittest.IsolatedAsyncioTestCase):
    """Learning counts capacity and priors by the scope family, not by one window.

    AstrBot rotates ``conversation_id``/``session_id`` often, and the review page,
    the capacity gate and injection already read one participant's rules across
    those rotations (``conversation.scope_family``). Learning has to count the same
    set: a rotated window must start "full" when its family is full, must see what
    the family already learned instead of learning it again, and must never pull in
    another participant's or another role's rules.
    """

    def setUp(self):
        super().setUp()
        self.expression_profiles()
        self.model_behavior = self.expression_model

    @staticmethod
    def rotated(scope, conversation):
        """The same participant's window after AstrBot rotated its conversation."""
        return replace(scope, conversation_id=conversation, session_id="session-" + conversation)

    async def open_window(self, host, conversation, *, count=6):
        """Deliver ``count`` learnable messages in one private conversation window."""
        app = host.application
        scope = SCOPE if conversation is None else self.rotated(SCOPE, conversation)
        if conversation is not None:
            # The fixture grants one scope at setUp; a rotated window authenticates
            # the same participant on its own route, exactly as the host would.
            self.grant(self.important, scope, "text")
        batch = None
        for index in range(count):
            batch = host.receive(self.event(f"{conversation or 'window-a'}-{index}",
                text="嗯哼，那就这么定啦。" + str(index), scope=scope)).unwrap()
        self.advance(app)
        await self.done(app, batch)
        return self.context(app, batch)

    @staticmethod
    def seed_rules(app, context, *, count, state="adopted", proposal="上一窗口已学的规则",
                   observation="上一窗口的观察"):
        """Rules a previous window of the same participant already learned."""
        owner_key = digest(app.expression_style.samples.role_owner(context))
        scope_key = digest(context.scope)
        at = stamp(app.tasks.now())
        window = json.dumps({"opened_at": at, "closed_at": at, "handles": [],
                             "characters": 0, "source_kind": "conversation_input"})
        rows = []
        with app.runtime.transaction() as connection:
            connection.execute("INSERT INTO expression_style_runs VALUES (?,?,?,?,?,?,?,?,?)",
                ("run-family", owner_key, scope_key, window, "family-hash", "上一窗口已学", "decided", at, at))
            for index in range(count):
                signal_id = "signal-family-" + str(index)
                rows.append(signal_id)
                connection.execute("""INSERT INTO expression_style_signals
                    (signal_id, run_id, owner_key, scope_key, dimension, observation, proposal,
                     confidence, evidence_json, state, created_at, decided_at, decided_by,
                     decision_key, ordinal)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (signal_id, "run-family", owner_key, scope_key, "diction", observation,
                     proposal + str(index), 0.6, "[]", state, at, at, "dashboard:test",
                     "family:" + signal_id, index))
        return rows

    @staticmethod
    def learning_material(called):
        """The learning batch's material, exactly as the model receives it."""
        envelope = next(item for item in reversed(called)
                        if item.original.output_format == "role_expression_learning")
        return json.loads(next(item.payload for item in envelope.original.context.materials
                               if item.purpose == "role_expression_samples"))

    async def test_a_rotated_private_window_counts_the_family_budget(self):
        host = await self.start()
        app = host.application
        first = await pipeline.ExpressionReplyPipelineTests.seed(self, host)
        self.configure_expression(app, {"private_learning_enabled": True, "active_signal_limit": 12})
        second = await self.open_window(host, "conversation-b")
        self.assertNotEqual(digest(first.scope), digest(second.scope))
        service = app.expression_style
        limit = service._policy(second, app.tasks.now()).active_signal_limit
        self.assertEqual(limit, 12)
        self.seed_rules(app, first, count=limit)

        self.assertEqual(service._active_signal_count(first), limit)
        self.assertEqual(service._active_signal_count(second), limit,
                         "a rotated window must count the rules its family already holds")
        service.next_checks.clear()
        self.assertFalse(service._scan(second, "", app.tasks.now()))
        self.assertEqual(service.results[service._work_key(second)]["state"], "capacity_reached",
                         "a full family budget must gate the rotated window too")

    async def test_a_rotated_private_window_reads_the_family_priors(self):
        host = await self.start()
        app = host.application
        first = await pipeline.ExpressionReplyPipelineTests.seed(self, host)
        self.configure_expression(app, {"private_learning_enabled": True})
        (row,) = self.seed_rules(app, first, count=1)
        second = await self.open_window(host, "conversation-b")

        instruction = app.state_maintenance.bindings[second.key][1]
        job = app.expression_style.prepare(second, instruction, app.tasks.now())
        self.assertIsNotNone(job, "the rotated window must still prepare a batch")
        self.assertIn(row, [item["signal_id"] for item in job["existing_rules"]],
                      "deduplication priors come from the family, not only this window")

        await self.learn_now(app, second)
        material = self.learning_material(self.called)
        self.assertIn(row, [item["signal_id"] for item in material["existing_rules"]],
                      "the model must see the rules its owner already has")

    async def test_a_rotated_private_window_does_not_relearn_a_family_rule(self):
        host = await self.start()
        app = host.application
        first = await pipeline.ExpressionReplyPipelineTests.seed(self, host)
        self.configure_expression(app, {"private_learning_enabled": True})
        (row,) = self.seed_rules(app, first, count=1, state="rejected",
                                 observation=RULE["observation"], proposal=RULE["proposal"])
        rule_details.write(app.runtime, row, RULE["expression"], at=app.tasks.now(),
                           actor="learning_model", operation_key="run-family")
        second = await self.open_window(host, "conversation-b")

        await self.learn_now(app, second)

        page = ExpressionStyleManagement(app).list(operator=self.core, context=second, state="pending")
        self.assertTrue(page.is_success, page.failure)
        self.assertEqual(page.unwrap()["items"], [],
                         "the family already decided this exact form; it must not be learned twice")

    async def test_a_rotated_private_window_page_counts_the_family(self):
        host = await self.start()
        app = host.application
        first = await pipeline.ExpressionReplyPipelineTests.seed(self, host)
        self.configure_expression(app, {"private_learning_enabled": True})
        second = await self.open_window(host, "conversation-b")
        self.seed_rules(app, first, count=3)

        # ``status()`` is what the page reads: it must report the family's active
        # count, not the rotated window's own (empty) one.
        self.assertEqual(app.expression_style.status(second)["active"], 3,
                         "the page reads the family's active count, not this window's")

    async def test_another_participant_or_role_is_not_in_the_family(self):
        host = await self.start()
        app = host.application
        first = await pipeline.ExpressionReplyPipelineTests.seed(self, host)
        self.configure_expression(app, {"private_learning_enabled": True})
        self.seed_rules(app, first, count=3)
        service = app.expression_style
        self.assertEqual(service._active_signal_count(first), 3)

        stranger_scope = replace(first.scope, native_scope_id="participant-9999",
                                 conversation_id="conversation-stranger",
                                 session_id="session-stranger")
        stranger_subject = SubjectRef(first.scope.installation_id, SubjectKind.HUMAN,
                                      "onebot-user:9999")
        stranger = TaskContext(replace(first.actor, subject=stranger_subject),
            RoleBinding(stranger_scope, first.role_binding.role, first.role_binding.revision),
            AudienceRef(stranger_scope, (stranger_subject,)))
        self.assertEqual(service._active_signal_count(stranger), 0,
                         "one participant's rules must never count for another's window")

        other_role = TaskContext(first.actor,
            RoleBinding(first.scope, SubjectRef(first.scope.installation_id, SubjectKind.BOT,
                                                "role-someone-else"), first.role_binding.revision),
            first.audience)
        self.assertEqual(service._active_signal_count(other_role), 0,
                         "the same window under another role owns none of these rules")


AT = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
OPENED = stamp(AT - timedelta(hours=2))
CLOSED = stamp(AT - timedelta(hours=1))
INSTALLATION = "installation-a"
APPLICABILITY = {"channels": ["private"], "relationship_stages": ["any"],
                 "emotion_gates": ["any"], "intent": "any", "keywords": []}
LEGACY_TEXT = "好，等会见。"
SHARED_RUN, SHARED_SIGNAL = "expression-share-import:private-family", "expression-share-signal:private-family"
LEGACY_RUN, LEGACY_SIGNAL = "legacy-expression:private-family", "legacy-expression-rule:private-family"
DIALOGUE_RUN, DIALOGUE_SIGNAL = "expression-learn:private-family", "expression-learn-signal:private-family"
OTHER_PERSON_RUN, OTHER_PERSON_SIGNAL = "expression-share-import:other-person", "expression-share-signal:other-person"
OTHER_ROLE_RUN, OTHER_ROLE_SIGNAL = "expression-share-import:other-role", "expression-share-signal:other-role"


def private_scope(conversation, session, *, participant="participant-200", platform="onebot",
                  bot="bot-a", account="login-a"):
    return ScopeRef(INSTALLATION, platform, ScopeKind.PRIVATE, participant, bot, account,
                    conversation, session)


def private_context(scope, *, participant="200", role="role-a"):
    actor = ResolvedIdentity(
        AuthenticatedHostPrincipal(INSTALLATION, "host-" + participant, "onebot", participant,
                                   bot_id=scope.bot_id, account_id=scope.account_id),
        SubjectRef(INSTALLATION, SubjectKind.HUMAN, "onebot-user:" + participant),
        "binding-" + participant, 1, 1)
    return TaskContext(actor, RoleBinding(scope, SubjectRef(INSTALLATION, SubjectKind.BOT, role), 1),
                       AudienceRef(scope, (actor.subject,)))


def shared_window():
    """An imported share pack: only the reusable form, no original samples."""
    return {"opened_at": OPENED, "closed_at": CLOSED, "handles": [], "characters": 0,
            "source_kind": "shared_pack"}


def legacy_window(signal_id=LEGACY_SIGNAL):
    """The window shape a V1 private import stored next to its rule."""
    handle = signal_id + ":evidence:0"
    return {"opened_at": OPENED, "closed_at": CLOSED, "handles": [handle],
            "characters": len(LEGACY_TEXT), "source_kind": "legacy_expression",
            "legacy": {"source_kind": "private", "source_id": "v1-user-200", "samples": [],
                       "evidence": {handle: LEGACY_TEXT}, "rules": {}}}, [handle]


def dialogue_window():
    handle = DIALOGUE_SIGNAL + ":evidence:0"
    return {"opened_at": OPENED, "closed_at": CLOSED, "handles": [handle], "characters": 8,
            "source_kind": "confirmed_reply"}, [handle]


def reply_policy(*, rules=3, candidates=8):
    return SimpleNamespace(rules_per_turn=rules, payload_chars=4000,
                           rotation_candidate_limit=candidates,
                           applies_to=lambda context: True)


class FakeRuntime:
    """Just enough of the storage surface the two expression domains call."""

    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE expression_style_runs (run_id TEXT PRIMARY KEY, owner_key TEXT NOT NULL,
                scope_key TEXT NOT NULL, sample_json TEXT NOT NULL, material_hash TEXT NOT NULL,
                summary TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL, decided_at TEXT,
                UNIQUE(owner_key, scope_key, run_id));
            CREATE TABLE expression_style_signals (signal_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                owner_key TEXT NOT NULL, scope_key TEXT NOT NULL, dimension TEXT NOT NULL,
                observation TEXT NOT NULL, proposal TEXT NOT NULL, applicability_json TEXT NOT NULL,
                confidence REAL NOT NULL, evidence_json TEXT NOT NULL, state TEXT NOT NULL,
                created_at TEXT NOT NULL, decided_at TEXT, decided_by TEXT, decision_key TEXT,
                ordinal INTEGER NOT NULL, UNIQUE(run_id, ordinal));
            CREATE TABLE runtime_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE conversation_batches (context_json TEXT NOT NULL, origin_key TEXT);
        """)
        # Every statement the domains issue, so a test can assert that an
        # ordinary turn never touches the conversation tables.
        self.reads = []

    def read_all(self, sql, parameters=()):
        self.reads.append(sql)
        return self.db.execute(sql, parameters).fetchall()

    def read_one(self, sql, parameters=()):
        self.reads.append(sql)
        return self.db.execute(sql, parameters).fetchone()

    @contextmanager
    def read_snapshot(self):
        yield self.db

    @contextmanager
    def transaction(self):
        yield self.db
        self.db.commit()

    def record_window(self, context):
        """Store one admitted window, the way the rotation leaves it on record."""
        with self.transaction() as connection:
            connection.execute("INSERT INTO conversation_batches (context_json) VALUES (?)",
                               (json.dumps(asdict(context), ensure_ascii=False),))

    def write_rule(self, *, scope, role, signal_id, run_id, window, evidence, state="adopted"):
        owner = RoleOwnerRef(scope.installation_id, scope.bot_id, role.subject_id)
        with self.transaction() as connection:
            connection.execute("""INSERT INTO expression_style_runs
                (run_id, owner_key, scope_key, sample_json, material_hash, summary, state,
                 created_at, decided_at) VALUES (?, ?, ?, ?, ?, '导入的表达', 'decided', ?, ?)""",
                (run_id, digest(owner), digest(scope), json.dumps(window, ensure_ascii=False),
                 digest([run_id, signal_id]), OPENED, CLOSED))
            connection.execute("""INSERT INTO expression_style_signals
                (signal_id, run_id, owner_key, scope_key, dimension, observation, proposal,
                 applicability_json, confidence, evidence_json, state, created_at, decided_at,
                 decided_by, decision_key, ordinal)
                VALUES (?, ?, ?, ?, 'diction', '观察', '按短句收尾', ?, 0.8, ?, ?, ?, ?, 'tester', ?, 0)""",
                (signal_id, run_id, digest(owner), digest(scope),
                 json.dumps(APPLICABILITY, ensure_ascii=False), json.dumps(evidence),
                 state, OPENED, CLOSED, "decision:" + signal_id))

    def write_rules(self, *, scope, role, prefix, count, window, evidence):
        """An adopted backlog of one window, inserted in a single transaction."""
        owner = RoleOwnerRef(scope.installation_id, scope.bot_id, role.subject_id)
        owner_key, scope_key = digest(owner), digest(scope)
        sample = json.dumps(window, ensure_ascii=False)
        applicability = json.dumps(APPLICABILITY, ensure_ascii=False)
        runs, signals = [], []
        for index in range(count):
            run_id, signal_id = f"{prefix}:run:{index:05d}", f"{prefix}:{index:05d}"
            runs.append((run_id, owner_key, scope_key, sample, digest([run_id, signal_id]),
                         OPENED, CLOSED))
            signals.append((signal_id, run_id, owner_key, scope_key, applicability,
                            json.dumps(evidence), "adopted", OPENED, CLOSED,
                            "decision:" + signal_id))
        with self.transaction() as connection:
            connection.executemany("""INSERT INTO expression_style_runs
                (run_id, owner_key, scope_key, sample_json, material_hash, summary, state,
                 created_at, decided_at) VALUES (?, ?, ?, ?, ?, '导入的表达', 'decided', ?, ?)""", runs)
            connection.executemany("""INSERT INTO expression_style_signals
                (signal_id, run_id, owner_key, scope_key, dimension, observation, proposal,
                 applicability_json, confidence, evidence_json, state, created_at, decided_at,
                 decided_by, decision_key, ordinal)
                VALUES (?, ?, ?, ?, 'diction', '观察', '按短句收尾', ?, 0.8, ?, ?, ?, ?, 'tester', ?, 0)""",
                signals)


class FakeReviews:
    def allows_handles(self, source, handles):
        return True


class ReplyCatalog:
    """The reply-side catalogue; only the source availability verdict is a double."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.reviews = FakeReviews()
        self.source_available = True

    def role_owner(self, context):
        return RoleOwnerRef(context.scope.installation_id, context.scope.bot_id,
                            context.role_binding.role.subject_id)

    def evidence_context(self, window, context):
        return {"window": window, "context": context}

    def private_source_available(self, source, context, at):
        return self.source_available

    def resolve_handles(self, handles, opened_at, closed_at, context, at,
                        *, source_kind="confirmed_reply"):
        return True


class ReviewSamples:
    """The review page's sample recheck, which only needs a description."""

    def __init__(self):
        self.reviews = FakeReviews()

    def evidence_context(self, window, context):
        return {"window": window, "context": context}

    def describe_handles(self, handles, opened_at, closed_at, context, at,
                         *, source_kind="confirmed_reply", durable=False):
        # ``durable`` is how the review side asks for evidence that survives a
        # /reset date; the in-memory double only needs to accept the keyword.
        return ["样本原文" for _ in handles]


class PrivateSelfContainedFamilyTests(unittest.TestCase):
    """A private participant's own text carries across conversation rotations.

    A shared-pack import or a V1 migration carries its own text, so it belongs
    to the participant and role rather than to one conversation id. Review and
    injection resolve the same family, so a rotated window keeps injecting it.
    A dialogue rule keeps its own gate: the original source relationship must
    still be authorized, and another participant or role never joins.
    """

    def setUp(self):
        self.runtime = FakeRuntime()
        self.catalog = ReplyCatalog(self.runtime)
        self.first_scope = private_scope("conversation-a", "session-a")
        self.rotated_scope = private_scope("conversation-b", "session-b")
        self.first = private_context(self.first_scope)
        self.rotated = private_context(self.rotated_scope)
        self.assertNotEqual(digest(self.first_scope), digest(self.rotated_scope))
        # Both windows are on record, exactly like after an AstrBot rotation.
        for context in (self.first, self.rotated):
            self.runtime.record_window(context)
        self.role = self.first.role_binding.role
        legacy, legacy_handles = legacy_window()
        dialogue, dialogue_handles = dialogue_window()
        self.runtime.write_rule(scope=self.first_scope, role=self.role, signal_id=SHARED_SIGNAL,
                                run_id=SHARED_RUN, window=shared_window(), evidence=[])
        self.runtime.write_rule(scope=self.first_scope, role=self.role, signal_id=LEGACY_SIGNAL,
                                run_id=LEGACY_RUN, window=legacy, evidence=legacy_handles)
        self.runtime.write_rule(scope=self.first_scope, role=self.role, signal_id=DIALOGUE_SIGNAL,
                                run_id=DIALOGUE_RUN, window=dialogue, evidence=dialogue_handles)
        self.style = style_context_module.ConversationExpressionStyleContext(self.catalog)

    def capture(self, context, *, policy=None):
        # The only double here is the unrelated "application enabled" gate: it
        # reads the configuration domain, not expression storage.
        with mock.patch.object(configuration_module, "application_subject_allowed_for_context",
                               lambda runtime, context: True):
            return self.style.capture(context, AT, policy=policy)

    def references(self, context, *, policy=None):
        snapshot = self.capture(context, policy=policy)
        self.assertEqual(snapshot["state"], "ready", snapshot)
        return snapshot, {item["signal_id"] for item in snapshot["references"]}

    @staticmethod
    def owner(context):
        return RoleOwnerRef(context.scope.installation_id, context.scope.bot_id,
                            context.role_binding.role.subject_id)

    def management(self):
        return ExpressionStyleManagement(SimpleNamespace(
            runtime=self.runtime, expression_samples=ReviewSamples(),
            role_growth=SimpleNamespace(runtime=self.runtime)))

    def test_a_rotated_window_injects_the_participants_own_text(self):
        snapshot, signals = self.references(self.rotated, policy=reply_policy())
        self.assertEqual({SHARED_SIGNAL, LEGACY_SIGNAL, DIALOGUE_SIGNAL}, signals)
        # Materialising them no longer reports a foreign scope.
        for signal_id, run_id in ((SHARED_SIGNAL, SHARED_RUN), (LEGACY_SIGNAL, LEGACY_RUN)):
            payload = json.loads(self.style._material({"signal_id": signal_id, "run_id": run_id},
                                                      self.rotated, AT))
            self.assertEqual(payload["signal_id"], signal_id)
        injected = {json.loads(item.payload)["signal_id"]: json.loads(item.payload)["basis"]
                    for item in self.style.contributions(snapshot, self.rotated, AT,
                                                         policy=reply_policy())
                    if item.purpose == "expression_style_rule"}
        self.assertEqual(injected[SHARED_SIGNAL], "adopted_shared_expression")
        self.assertEqual(injected[LEGACY_SIGNAL], "adopted_legacy_expression")

    def test_a_current_window_rule_never_resolves_the_family(self):
        """An ordinary private turn reads its own window, not the family.

        A shared-pack or legacy rule stored in the window being answered is
        eligible without knowing the participant's other windows, so answering
        it must not scan the conversation tables at all.
        """
        original = style_context_module.scope_family_keys
        resolutions = []

        def counting(runtime, context):
            resolutions.append(context.key)
            return original(runtime, context)

        self.runtime.reads.clear()
        with mock.patch.object(style_context_module, "scope_family_keys", counting):
            _, signals = self.references(self.first, policy=reply_policy())
        self.assertEqual({SHARED_SIGNAL, LEGACY_SIGNAL, DIALOGUE_SIGNAL}, signals)
        self.assertEqual([], resolutions)
        self.assertEqual([], [sql for sql in self.runtime.reads
                              if "conversation_batches" in sql
                              or "conversation_native_turns" in sql])

    def test_another_participant_or_role_never_joins_the_family(self):
        stranger = private_scope("conversation-stranger", "session-stranger",
                                 participant="participant-999")
        self.runtime.write_rule(scope=stranger, role=self.role, signal_id=OTHER_PERSON_SIGNAL,
                                run_id=OTHER_PERSON_RUN, window=shared_window(), evidence=[])
        # The same participant on another role keeps its own window on record,
        # exactly like a real second role would after a rotation.
        other_role = SubjectRef(INSTALLATION, SubjectKind.BOT, "role-b")
        other_window = private_scope("conversation-role-b", "session-role-b")
        self.runtime.record_window(private_context(other_window, role="role-b"))
        self.runtime.write_rule(scope=other_window, role=other_role, signal_id=OTHER_ROLE_SIGNAL,
                                run_id=OTHER_ROLE_RUN, window=shared_window(), evidence=[])

        family = scope_family_keys(self.runtime, self.rotated)
        self.assertNotIn(digest(stranger), family)
        self.assertNotIn(digest(other_window), family)
        _, signals = self.references(self.rotated, policy=reply_policy())
        self.assertNotIn(OTHER_PERSON_SIGNAL, signals)
        self.assertNotIn(OTHER_ROLE_SIGNAL, signals)
        self.assertFalse(self.style._owns_rule_scope(digest(stranger), self.rotated))

        manager = self.management()
        for signal_id in (OTHER_PERSON_SIGNAL, OTHER_ROLE_SIGNAL):
            with self.assertRaises(TaskError) as caught:
                manager._row(signal_id, self.owner(self.rotated), self.rotated)
            self.assertEqual(caught.exception.code, "expression_signal_unavailable")

    def test_a_dialogue_rule_still_needs_its_original_source(self):
        self.catalog.source_available = False
        _, blocked = self.references(self.rotated, policy=reply_policy())
        self.assertNotIn(DIALOGUE_SIGNAL, blocked)
        # A shared family is not a substitute for the source gate: the rules
        # that carry their own text still inject while the dialogue rule stays out.
        self.assertIn(SHARED_SIGNAL, blocked)
        self.assertIn(LEGACY_SIGNAL, blocked)

        self.catalog.source_available = True
        _, allowed = self.references(self.rotated, policy=reply_policy())
        self.assertIn(DIALOGUE_SIGNAL, allowed)

    def test_the_review_page_and_the_injection_agree_on_the_family(self):
        manager = self.management()
        owner = self.owner(self.rotated)
        family = manager._family(self.rotated)
        self.assertEqual(set(family), set(scope_family_keys(self.runtime, self.rotated)))
        self.assertIn(digest(self.first_scope), family)

        _, injected = self.references(self.rotated, policy=reply_policy())
        listed = [manager._row(signal_id, owner, self.rotated)["signal_id"]
                  for signal_id in sorted(injected)]
        self.assertEqual(sorted(injected), sorted(listed))
        # Everything the review side lists is usable there as well.
        basis = {row["signal_id"]: manager._basis(row, owner, self.rotated, AT)
                 for row in (manager._row(signal_id, owner, self.rotated) for signal_id in listed)}
        self.assertEqual(basis[SHARED_SIGNAL], [])
        self.assertEqual(basis[LEGACY_SIGNAL], [LEGACY_TEXT])
        # The page's capacity counts exactly the rules the injection side can use.
        placeholders = ",".join("?" * len(family))
        capacity = self.runtime.read_one(f"""SELECT COUNT(*) AS count FROM expression_style_signals
            WHERE owner_key = ? AND scope_key IN ({placeholders})
              AND state IN ('pending', 'adopted')""", (digest(owner), *family))["count"]
        self.assertEqual(capacity, len(injected))

    def test_a_relationship_backlog_cannot_crowd_out_the_pool(self):
        # Another relationship of the same role keeps more adopted rules than one
        # bounded pool page, and its ids sort first. Reading only that page and
        # filtering by family would push this participant's rules out of the pool.
        stranger = private_scope("conversation-crowd", "session-crowd",
                                 participant="participant-999")
        self.runtime.write_rules(scope=stranger, role=self.role, prefix="aaa-backlog",
                                 count=MAX_POOL_SCAN + 8, window=shared_window(), evidence=[])
        window, handles = legacy_window("zzz-" + LEGACY_SIGNAL)
        self.runtime.write_rule(scope=self.first_scope, role=self.role,
                                signal_id="zzz-" + LEGACY_SIGNAL, run_id="zzz-" + LEGACY_RUN,
                                window=window, evidence=handles)

        _, signals = self.references(self.rotated, policy=reply_policy(rules=4, candidates=16))
        self.assertIn("zzz-" + LEGACY_SIGNAL, signals)
        self.assertNotIn("aaa-backlog:00000", signals)


if __name__ == "__main__":
    unittest.main()
