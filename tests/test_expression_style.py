"""Expression-style learning contracts: parsing, sample bounds, rotation, snapshot.

Pure-contract checks plus one storage round trip. They need no host, model or platform.
"""

from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest

from companion_v2.configuration.registry import FieldRegistry, ValueKind
from companion_v2.conversation.style_context import (EXPRESSION_STYLE_INSTRUCTION, MAX_CANDIDATES,
    SNAPSHOT_VERSION, ConversationExpressionStyleContext)
from companion_v2.conversation.models import InboundConversationEvent
from companion_v2.execution.models import TaskContext, TaskError, encode, stamp
from companion_v2.expression import learning, style_rotation
from companion_v2.expression.configuration import (GROUP, ExpressionLearningSettings,
    POLICY_LAYERS, ROLE_DEFAULTS, ExpressionConfigurationOwner, configuration_fields, read_settings,
    target_for)
from companion_v2.expression.style_service import ExpressionStyleService
from companion_v2.expression.style_evidence import (DEFAULT_SAMPLE_CHARS, INPUT_SAMPLE_SOURCE, MAX_SAMPLE_CHARS,
    MAX_WINDOW_HOURS, ExpressionSampleCatalog, SampleSet, StyleSample, sample_document, sample_handles)
from companion_v2.foundation.refs import (AudienceRef, ScopeKind, ScopeRef, SourceRef, SubjectKind, SubjectRef,
    VersionRef)
from companion_v2.identity_policy.group_audience import group_audience
from companion_v2.identity_policy.models import AuthenticatedHostPrincipal, ResolvedIdentity, RoleBinding
from companion_v2.storage.runtime import StorageRuntime
from tests.test_management_access import INSTALLATION


NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
SCOPE = ScopeRef(INSTALLATION, "onebot", ScopeKind.PRIVATE, "native-a", "bot-a", "login-a",
                 "conversation-a", "session-a")
ROLE = SubjectRef(INSTALLATION, SubjectKind.BOT, "persona-a")


def handle(name):
    return "sample:" + name


class StyleLearningOutputTests(unittest.TestCase):
    def parse(self, payload, *, allowed=None):
        return learning.parse_output(
            json.dumps(payload, ensure_ascii=False) if not isinstance(payload, str) else payload,
            allowed_evidence=allowed if allowed is not None else {handle("a")})

    def signal(self, **overrides):
        item = {"dimension": "punctuation", "observation": "常以句号收束短句",
                "proposal": "保持短句和句号的节奏", "confidence": 0.5,
                "expression": {"kind": "grammar", "situation": "回应日常安排时",
                    "pattern": "先用短句确认，再用短句收束", "avoid": "需要解释细节时"},
                "applicability": {"channels": ["private"], "relationship_stages": ["any"],
                    "emotion_gates": ["any"], "intent": "any", "keywords": []},
                "evidence_handles": [handle("a")]}
        item.update(overrides)
        return {"summary": "形式观察", "signals": [item]}

    def test_accepts_a_bounded_candidate(self):
        output = self.parse(self.signal())
        self.assertEqual(output.summary, "形式观察")
        self.assertEqual(output.signals[0].dimension, "punctuation")
        self.assertEqual(output.signals[0].applicability["channels"], ["private"])

    def test_source_channel_is_preserved_when_model_selects_another_channel(self):
        payload = self.signal()
        payload["signals"][0]["applicability"]["channels"] = ["private"]
        output = learning.parse_output(payload, allowed_evidence={handle("a")}, default_channels=("group",))
        self.assertEqual(output.signals[0].applicability["channels"], ["private", "group"])

    def test_expression_form_is_required_for_each_candidate(self):
        payload = self.signal()
        del payload["signals"][0]["expression"]
        with self.assertRaisesRegex(ValueError, "invalid_signal"):
            self.parse(payload)

    def test_applicability_is_bounded_and_channel_specific(self):
        from companion_v2.expression.learning import applicability_matches_channel, laya_intent_matches
        applicability = {"channels": ["private"], "relationship_stages": ["close"],
            "emotion_gates": ["positive"], "intent": "question", "keywords": ["晚安"]}
        self.assertTrue(applicability_matches_channel(applicability, "private"))
        self.assertFalse(applicability_matches_channel(applicability, "group"))
        self.assertFalse(laya_intent_matches(applicability, {}))
        self.assertTrue(laya_intent_matches(applicability, {"reply_intent": "answer", "reply_style": "normal"}))
        with self.assertRaises(ValueError):
            learning.normalize_applicability({"channels": ["private", "unknown"]})

    def test_empty_signals_allowed(self):
        self.assertEqual(self.parse({"summary": "样本不足", "signals": []}).signals, ())

    def test_rejects_unknown_dimension(self):
        with self.assertRaises(ValueError):
            self.parse(self.signal(dimension="personality"))

    def test_rejects_unknown_evidence_handle(self):
        with self.assertRaises(ValueError) as caught:
            self.parse(self.signal(evidence_handles=[handle("nope")]))
        self.assertEqual(str(caught.exception), "invalid_evidence")

    def test_rejects_duplicate_evidence(self):
        with self.assertRaises(ValueError):
            self.parse(self.signal(evidence_handles=[handle("a"), handle("a")]))

    def test_rejects_missing_evidence(self):
        with self.assertRaises(ValueError):
            self.parse(self.signal(evidence_handles=[]))

    def test_rejects_blank_observation_or_proposal(self):
        for field in ("observation", "proposal"):
            with self.assertRaises(ValueError):
                self.parse(self.signal(**{field: "   "}))

    def test_rejects_out_of_range_confidence(self):
        for value in (-0.1, 1.1):
            with self.assertRaises(ValueError):
                self.parse(self.signal(confidence=value))

    def test_rejects_extra_fields(self):
        payload = self.signal()
        payload["signals"][0]["note"] = "x"
        with self.assertRaises(ValueError):
            self.parse(payload)

    def test_bounds_candidate_count_and_text(self):
        payload = self.signal()
        payload["signals"] = [dict(payload["signals"][0]) for _ in range(learning.MAX_SIGNALS_PER_RUN + 1)]
        with self.assertRaises(ValueError):
            self.parse(payload)
        for field, maximum in (("observation", learning.MAX_OBSERVATION_CHARS),
                               ("proposal", learning.MAX_PROPOSAL_CHARS)):
            payload = self.signal(**{field: "x" * (maximum + 1)})
            with self.assertRaises(ValueError):
                self.parse(payload)

    def test_accepts_json_wrapped_in_a_markdown_fence(self):
        payload = self.signal()
        fenced = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
        result = self.parse(fenced)
        self.assertEqual(result.signals[0].proposal, payload["signals"][0]["proposal"])

    def test_rejects_invalid_json(self):
        with self.assertRaises(ValueError) as caught:
            self.parse("{nope")
        self.assertEqual(str(caught.exception), "invalid_json")

    def test_instruction_forbids_trait_inference(self):
        self.assertIn("不从少量样本推断身份", learning.INSTRUCTION)


class SampleCatalogTests(unittest.TestCase):
    class Runtime:
        def __init__(self, rows):
            self.rows = rows
            self.query = None

        @contextmanager
        def read_snapshot(self):
            yield

        def read_all(self, query, parameters):
            if "FROM expression_style_runs" in query:
                return []
            self.query = (query, parameters)
            return self.rows

    class Messages:
        def __init__(self, runtime, payload="嗯哼，那就这么定啦"):
            self.runtime = runtime
            self.payload = payload
            self.reads = []

        def authorize(self, context, at=None):
            return None

        def _read_source(self, reference, context, capability, at):
            self.reads.append((reference.source_id, context.actor.subject.subject_id, capability))
            return SimpleNamespace(content_hash="input-hash", payload=self.payload)

    @staticmethod
    def private_context(speaker="speaker-a"):
        principal = AuthenticatedHostPrincipal(INSTALLATION, "test-host", "onebot", "account-a",
                                               "bot-a", "login-a")
        subject = SubjectRef(INSTALLATION, SubjectKind.HUMAN, speaker)
        return TaskContext(ResolvedIdentity(principal, subject, "binding-" + speaker, 1, 1),
                           RoleBinding(SCOPE, ROLE, 1), AudienceRef(SCOPE, (subject,)))

    @staticmethod
    def group_context(speaker="member-a"):
        scope = ScopeRef(INSTALLATION, "onebot", ScopeKind.GROUP, "group-a", "bot-a", "login-a",
                         "conversation-g", "session-g")
        principal = AuthenticatedHostPrincipal(INSTALLATION, "test-host", "onebot", "account-a",
                                               "bot-a", "login-a")
        subject = SubjectRef(INSTALLATION, SubjectKind.HUMAN, speaker)
        return TaskContext(ResolvedIdentity(principal, subject, "binding-" + speaker, 1, 1),
                           RoleBinding(scope, ROLE, 1), group_audience(scope, subject))

    @staticmethod
    def input_row(context, event_id="event-a"):
        reference = SourceRef("input-" + event_id, "conversation.host_input", context.scope,
                              VersionRef("lineage-" + event_id, 1), NOW)
        event = InboundConversationEvent(event_id, context.actor.principal, context.scope, reference,
                                         "conversation.read", "input-hash", NOW)
        return {"input_sequence": 1, "event_key": event.scoped_key, "event_json": encode(event),
                "context_json": encode(context)}, event

    def test_window_bounds_are_enforced(self):
        for hours in (0, MAX_WINDOW_HOURS + 1):
            with self.assertRaises(ValueError):
                ExpressionSampleCatalog(None, window_hours=hours)
        ExpressionSampleCatalog(None, window_hours=MAX_WINDOW_HOURS)

    def test_requires_attached_owner(self):
        catalog = ExpressionSampleCatalog(None)
        catalog._authorize = staticmethod(lambda context, at: None)
        with self.assertRaises(TaskError) as caught:
            catalog._owner()
        self.assertEqual(caught.exception.code, "expression_sample_owner_unavailable")

    def test_attach_rejects_foreign_runtime(self):
        class Messages:
            runtime = object()

        catalog = ExpressionSampleCatalog(object())
        with self.assertRaises(ValueError):
            catalog.attach(Messages())

    def test_material_hash_is_order_sensitive(self):
        first, second = StyleSample(handle("a"), "嗯"), StyleSample(handle("b"), "好的")
        forward = SampleSet(NOW, NOW, (first, second))
        reverse = SampleSet(NOW, NOW, (second, first))
        self.assertEqual(forward.material_hash, SampleSet(NOW, NOW, (first, second)).material_hash)
        self.assertNotEqual(forward.material_hash, reverse.material_hash)

    def test_sample_document_counts_characters(self):
        samples = SampleSet(NOW, NOW, (StyleSample(handle("a"), "12"), StyleSample(handle("b"), "345")))
        document = sample_document(samples)
        self.assertEqual(document["characters"], 5)
        self.assertEqual(document["handles"], [handle("a"), handle("b")])
        self.assertEqual(document["source_kind"], "confirmed_reply")
        self.assertEqual(sample_handles(samples), {handle("a"), handle("b")})

    def test_inbound_samples_are_rechecked_and_keep_exact_private_scope(self):
        context = self.private_context()
        row, event = self.input_row(context)
        runtime = self.Runtime([row])
        messages = self.Messages(runtime)
        catalog = ExpressionSampleCatalog(runtime)
        catalog.attach(messages)

        samples = catalog.collect(context, NOW, source_kind=INPUT_SAMPLE_SOURCE)
        self.assertEqual(samples.source_kind, INPUT_SAMPLE_SOURCE)
        self.assertEqual(samples.samples[0].text, "嗯哼，那就这么定啦")
        self.assertEqual(samples.samples[0].handle, catalog.input_handle(event))
        self.assertIn(context.key, runtime.query[1])
        self.assertEqual(messages.reads[0][1], "speaker-a")

        runtime.rows = []
        with self.assertRaises(TaskError) as caught:
            catalog.describe_handles((samples.samples[0].handle,), samples.opened_at, samples.closed_at,
                                     context, NOW, source_kind=INPUT_SAMPLE_SOURCE)
        self.assertEqual(caught.exception.code, "expression_sample_changed")

    def test_group_samples_include_other_authenticated_members_of_same_role_binding(self):
        original = self.group_context("member-a")
        current = self.group_context("member-b")
        row, _ = self.input_row(original)
        runtime = self.Runtime([row])
        catalog = ExpressionSampleCatalog(runtime)
        catalog.attach(self.Messages(runtime))

        samples = catalog.collect(current, NOW, source_kind=INPUT_SAMPLE_SOURCE)
        self.assertEqual(len(samples.samples), 1)
        self.assertIn("$.role_binding", runtime.query[0])
        self.assertNotIn("$.actor.subject.subject_id", runtime.query[0])

    def test_private_samples_cannot_cross_to_another_participant(self):
        original = self.private_context("speaker-a")
        other = self.private_context("speaker-b")
        row, _ = self.input_row(original)
        runtime = self.Runtime([row])
        catalog = ExpressionSampleCatalog(runtime)
        catalog.attach(self.Messages(runtime))

        samples = catalog.collect(other, NOW, source_kind=INPUT_SAMPLE_SOURCE)
        self.assertEqual(samples.samples, ())

    def test_per_sample_bound_is_used(self):
        self.assertGreater(DEFAULT_SAMPLE_CHARS, 0)
        self.assertGreaterEqual(MAX_SAMPLE_CHARS, DEFAULT_SAMPLE_CHARS)

    def test_private_scope_needs_the_exact_participant(self):
        other = SimpleNamespace(subject=object(), kind=SubjectKind.HUMAN)
        context = SimpleNamespace(scope=SCOPE, audience=SimpleNamespace(recipients=()),
                                  actor=SimpleNamespace(subject=other))
        with self.assertRaises(TaskError) as caught:
            ExpressionSampleCatalog._authorize(context, NOW)
        self.assertEqual(caught.exception.code, "expression_private_audience_required")

    def test_group_scope_needs_the_collective_audience(self):
        group_scope = ScopeRef(INSTALLATION, "onebot", ScopeKind.GROUP, "group-a", "bot-a", "login-a",
                               "conversation-g", "session-g")
        context = SimpleNamespace(scope=group_scope, audience=SimpleNamespace(recipients=()),
                                  actor=SimpleNamespace(subject=SubjectRef(INSTALLATION, SubjectKind.HUMAN, "member-a")))
        with self.assertRaises(TaskError) as caught:
            ExpressionSampleCatalog._authorize(context, NOW)
        self.assertEqual(caught.exception.code, "expression_group_audience_required")

    def test_other_scope_kinds_are_rejected(self):
        thread = ScopeRef(INSTALLATION, "onebot", ScopeKind.THREAD, "thread-a", "bot-a", "login-a",
                          "conversation-t", "session-t")
        context = SimpleNamespace(scope=thread, audience=SimpleNamespace(recipients=()),
                                  actor=SimpleNamespace(subject=SubjectRef(INSTALLATION, SubjectKind.HUMAN, "member-a")))
        with self.assertRaises(TaskError) as caught:
            ExpressionSampleCatalog._authorize(context, NOW)
        self.assertEqual(caught.exception.code, "expression_scope_required")


class ExpressionRotationTests(unittest.TestCase):
    """The use-then-de-prioritise rotation adapted from V1's dream-fragment pool."""

    def pool(self, size):
        return tuple((f"s{index:02d}", "run") for index in range(size))

    def test_a_used_rule_decays_and_recovers(self):
        fresh = style_rotation.effective_weight((1, NOW), at=NOW)
        day = style_rotation.effective_weight((1, NOW - timedelta(days=1)), at=NOW)
        month = style_rotation.effective_weight((1, NOW - timedelta(days=30)), at=NOW)
        self.assertLess(fresh, day)
        self.assertLess(day, month)
        self.assertLess(month, 0.8)
        self.assertGreater(style_rotation.effective_weight((1, NOW - timedelta(days=1)), at=NOW,
            decay_hours=1), day)
        # A rule drawn many times settles on the frequency floor and stays there,
        # which is what makes a favourite fade rather than dominate.
        busy = style_rotation.effective_weight((200, NOW), at=NOW)
        self.assertGreater(busy, 0)
        self.assertLess(busy, fresh)
        self.assertEqual(style_rotation.effective_weight((0, None), at=NOW), style_rotation.UNUSED_WEIGHT)

    def test_an_unused_rule_outweighs_every_used_rule(self):
        just_used = style_rotation.effective_weight((50, NOW), at=NOW)
        unused = style_rotation.effective_weight((0, None), at=NOW)
        self.assertGreater(unused, just_used)
        self.assertEqual(unused, style_rotation.UNUSED_WEIGHT)

    def test_frequent_use_is_a_lower_probability_not_a_lock_out(self):
        pool = self.pool(2)
        usage = {"s00": (99, NOW), "s01": (1, NOW)}
        counts = {"s00": 0, "s01": 0}
        for index in range(400):
            picked, = style_rotation.sample(pool, usage_map=usage, at=NOW, count=1, seed=["turn", index])
            counts[picked[0]] += 1
        # Equal timestamps, so only the frequency factor separates them.
        self.assertGreater(counts["s01"], counts["s00"])
        self.assertGreater(counts["s00"], 0)
        # Frequency decays a rule towards the floor; waiting only ever helps on
        # top of that, so a heavily used rule cannot crowd out a fresh one.
        waited = {"s00": (99, NOW), "s01": (1, NOW)}
        self.assertGreater(style_rotation.effective_weight(waited["s01"], at=NOW),
                           style_rotation.effective_weight(waited["s00"], at=NOW))
        self.assertEqual(style_rotation.order(pool, usage_map=waited, at=NOW, decay_hours=336)[0][0], "s01")

    def test_ordering_puts_unused_first_then_the_longest_waiting(self):
        candidates = [("used-recently", "r"), ("never", "r"), ("used-long-ago", "r")]
        usage = {"used-recently": (3, NOW - timedelta(hours=1)), "used-long-ago": (9, NOW - timedelta(hours=300))}
        ordered = style_rotation.order(candidates, usage_map=usage, at=NOW, decay_hours=336)
        self.assertEqual(ordered[0][0], "never")
        self.assertEqual({item[0] for item in ordered[1:]}, {"used-long-ago", "used-recently"})
        # The just-used rule carries the heavier penalty, so the long-waiting one
        # is offered first among the two used rules.
        self.assertGreater(style_rotation.effective_weight(usage["used-long-ago"], at=NOW),
                        style_rotation.effective_weight(usage["used-recently"], at=NOW))
        self.assertEqual(ordered[1][0], "used-long-ago")

    def test_ordering_is_a_pure_function_of_stored_state(self):
        candidates = [("a", "r"), ("b", "r"), ("c", "r")]
        usage = {"a": (2, NOW - timedelta(hours=3)), "b": (0, None), "c": (7, NOW - timedelta(hours=900))}
        self.assertEqual(style_rotation.order(candidates, usage_map=usage, at=NOW, decay_hours=336),
                         style_rotation.order(tuple(reversed(candidates)), usage_map=usage, at=NOW,
                                              decay_hours=336))

    def test_a_rule_that_keeps_losing_the_draw_is_still_offered(self):
        stale = NOW - timedelta(days=200)
        weight = style_rotation.effective_weight((1, stale), at=NOW)
        self.assertGreater(weight, style_rotation.effective_weight((1, NOW), at=NOW))
        self.assertGreater(weight, 0)
        # Nothing is ever locked out: even the coldest adopted rule can still be
        # drawn, which is what keeps "learned but never used" impossible.
        self.assertEqual(len(style_rotation.sample([("cold", "r")], usage_map={"cold": (1, stale)},
                                                   at=NOW, count=1, seed=["turn"])), 1)

    def test_sampling_is_seeded_deduplicated_and_bounded(self):
        pool = self.pool(10)
        first = style_rotation.sample(pool, usage_map={}, at=NOW, count=4, seed=["turn", "a"])
        again = style_rotation.sample(pool, usage_map={}, at=NOW, count=4, seed=["turn", "a"])
        self.assertEqual(first, again)
        self.assertEqual(len(first), 4)
        self.assertEqual(len({item[0] for item in first}), 4)
        self.assertEqual(len(style_rotation.sample(pool, usage_map={}, at=NOW, count=99, seed=["turn"])), 10)

    def test_damaged_counters_read_as_never_used(self):
        self.assertEqual(style_rotation._entry("not json"), None)
        self.assertEqual(style_rotation._entry('{"injections": 1}'), None)
        self.assertEqual(style_rotation._entry('{"signal_id": "s", "scope_key": "k", '
                                               '"injections": -1, "last_injected_at": null}'), None)
        self.assertEqual(style_rotation._entry('{"signal_id": "s", "scope_key": "k", '
                                               '"injections": 0, "last_injected_at": "2026-09-26T12:00:00"}'), None)

    def test_rotation_round_trips_through_storage(self):
        with tempfile.TemporaryDirectory(prefix="companion-v2-rotation-") as temporary:
            runtime = StorageRuntime.open(data_root=Path(temporary) / "data", installation_id=INSTALLATION)
            try:
                self.assertEqual(style_rotation.usage(runtime, "owner", "scope"), {})
                style_rotation.record(runtime, owner_key="owner", scope_key="scope",
                                      signal_ids=("s2", "s1"), at=NOW)
                first = style_rotation.usage(runtime, "owner", "scope")
                self.assertEqual(sorted(first), ["s1", "s2"])
                self.assertEqual(first["s1"][0], 1)
                self.assertEqual(first["s1"][1], NOW)
                style_rotation.record(runtime, owner_key="owner", scope_key="scope",
                                      signal_ids=("s1",), at=NOW + timedelta(hours=1))
                second = style_rotation.usage(runtime, "owner", "scope")
                self.assertEqual(second["s1"][0], 2)
                self.assertEqual(second["s1"][1], NOW + timedelta(hours=1))
                # Another scope never sees this scope's counters.
                self.assertEqual(style_rotation.usage(runtime, "owner", "other"), {})
            finally:
                runtime.close()


class ExpressionSettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="companion-v2-expression-settings-")
        self.addCleanup(temporary.cleanup)
        self.runtime = StorageRuntime.open(data_root=Path(temporary.name) / "data", installation_id=INSTALLATION)
        self.addCleanup(self.runtime.close)

    def context(self, scope=SCOPE):
        actor = SampleCatalogTests.private_context().actor
        return TaskContext(actor, RoleBinding(scope, ROLE, 1), AudienceRef(scope, (actor.subject,)))

    def write(self, changes):
        fields = configuration_fields()
        snapshot = ExpressionConfigurationOwner(self.runtime).read(target_for(installation_id=INSTALLATION), fields)
        ExpressionConfigurationOwner(self.runtime).write(target_for(installation_id=INSTALLATION), fields,
            changes={GROUP + "." + key: value for key, value in changes.items()},
            reset=(), expected_revision=snapshot.revision, observed_at=NOW)

    def test_registry_contract_uses_installation_policy_fields(self):
        fields = configuration_fields()
        self.assertEqual({field.field_id for field in fields},
                         {GROUP + "." + name for name in ROLE_DEFAULTS})
        self.assertTrue(all(field.layers == POLICY_LAYERS for field in fields))
        self.assertTrue(all(field.consumer_id == GROUP and field.owner_id == GROUP for field in fields))
        FieldRegistry(version="expression.v1", fields=fields)
        kinds = {field.field_id.removeprefix(GROUP + "."): field.kind for field in fields}
        self.assertEqual(kinds["private_learning_enabled"], ValueKind.BOOLEAN)
        self.assertEqual(kinds["group_learning_enabled"], ValueKind.BOOLEAN)
        self.assertEqual(kinds["group_min_samples"], ValueKind.INTEGER)
        self.assertEqual(kinds["active_signal_limit"], ValueKind.INTEGER)
        self.assertEqual(kinds["rules_per_turn"], ValueKind.INTEGER)

    def test_defaults_are_used_before_anything_is_written(self):
        self.assertEqual(read_settings(self.runtime, self.context()),
                         ExpressionLearningSettings.from_values(ROLE_DEFAULTS))
        self.assertTrue(read_settings(self.runtime, self.context()).private_learning_enabled)
        self.assertFalse(read_settings(self.runtime, self.context()).group_learning_enabled)
        self.assertEqual(read_settings(self.runtime, self.context()).min_samples, 2)
        self.assertEqual(read_settings(self.runtime, self.context()).group_min_samples, 20)
        self.assertEqual(read_settings(self.runtime, self.context()).active_signal_limit, 60)

    def test_written_values_round_trip(self):
        self.write({"private_learning_enabled": False, "group_learning_enabled": True,
                    "window_hours": 48, "sample_limit": 40, "group_min_samples": 32,
                    "active_signal_limit": 72,
                    "min_samples": 2, "rules_per_turn": 4, "rotation_decay_hours": 12})
        settings = read_settings(self.runtime, self.context())
        self.assertFalse(settings.private_learning_enabled)
        self.assertTrue(settings.group_learning_enabled)
        self.assertEqual(settings.group_min_samples, 32)
        self.assertEqual(settings.active_signal_limit, 72)
        self.assertEqual((settings.window_hours, settings.sample_limit, settings.min_samples),
                         (48, 40, 2))
        self.assertEqual((settings.rules_per_turn, settings.rotation_decay_hours), (4, 12))

    def test_installation_policy_is_shared_across_scopes(self):
        self.write({"window_hours": 48})
        other = ScopeRef(INSTALLATION, "onebot", ScopeKind.PRIVATE, "native-b", "bot-a", "login-a",
                         "conversation-b", "session-b")
        self.assertEqual(read_settings(self.runtime, self.context(other)).window_hours,
                         48)

    def test_reset_cannot_save_an_invalid_batch_minimum_combination(self):
        from companion_v2.expression.configuration import ExpressionConfigurationOwner, target_for
        from companion_v2.configuration.models import ConfigurationError
        owner, fields = ExpressionConfigurationOwner(self.runtime), configuration_fields()
        target = target_for(self.context())
        owner.write(target, fields, changes={GROUP + ".sample_limit": 64, GROUP + ".group_min_samples": 64},
                    reset=(), expected_revision=0, observed_at=NOW)
        with self.assertRaises(ConfigurationError):
            owner.write(target, fields, changes={}, reset=(GROUP + ".sample_limit",),
                        expected_revision=1, observed_at=NOW)
        self.assertEqual(read_settings(self.runtime, self.context()).sample_limit, 64)
        owner.write(target, fields, changes={}, reset=(GROUP + ".sample_limit", GROUP + ".group_min_samples"),
                    expected_revision=1, observed_at=NOW)
        self.assertEqual(read_settings(self.runtime, self.context()), ExpressionLearningSettings.from_values(ROLE_DEFAULTS))

    def test_inconsistent_combinations_are_rejected(self):
        for values in ({"sample_limit": 4, "min_samples": 8},
                       {"sample_limit": 19, "group_min_samples": 20},
                       {"rotation_candidate_limit": 4, "rules_per_turn": 8},
                       {"private_learning_enabled": "yes"},
                       {"group_learning_enabled": "yes"},
                       {"window_hours": MAX_WINDOW_HOURS + 1}):
            with self.assertRaises(Exception):
                ExpressionLearningSettings.from_values(values)

    def test_group_scheduler_reads_one_context_per_role_and_group(self):
        first = SampleCatalogTests.group_context("member-a")
        latest = SampleCatalogTests.group_context("member-b")
        with self.runtime.transaction() as connection:
            for index, context in enumerate((first, latest)):
                connection.execute("""INSERT INTO conversation_batches
                    (batch_id, origin_key, context_json, role_instruction, state, revision, created_at,
                     contributions_json) VALUES (?, ?, ?, '', 'open', 1, ?, '{}')""",
                    (f"expression-group-{index}", context.key, encode(context), stamp(NOW)))

        service = ExpressionStyleService(self.runtime)
        contexts = service._group_candidates(NOW)
        self.assertEqual(len(contexts), 1)
        self.assertEqual(contexts[0].actor.subject.subject_id, "member-b")


class StyleContextSnapshotTests(unittest.TestCase):
    class Context:
        key = "private:1"

    def snapshot(self, **overrides):
        # captured_at must be in stamp() form: validate_snapshot re-stamps the parsed
        # value and compares, which an isoformat() string without microseconds fails.
        value = {"version": SNAPSHOT_VERSION, "context_key": self.Context.key,
                 "captured_at": stamp(NOW), "state": "ready", "references": [],
                 "truncated": False, "reason": None, "rules_per_turn": 8,
                 "payload_chars": 4000, "injected": []}
        value.update(overrides)
        return value

    def validate(self, snapshot):
        ConversationExpressionStyleContext.validate_snapshot(snapshot, self.Context())

    def test_accepts_empty_ready_snapshot(self):
        self.validate(self.snapshot())

    def test_accepts_adopted_rule_reference(self):
        self.validate(self.snapshot(references=[{"signal_id": "s1", "run_id": "r1"}]))

    def test_accepts_an_injected_prefix(self):
        self.validate(self.snapshot(references=[{"signal_id": "s1", "run_id": "r1"},
                                                {"signal_id": "s2", "run_id": "r1"}],
                                    injected=["s1"]))

    def test_rejects_injected_rules_that_are_not_the_ordered_prefix(self):
        for injected in (["s2"], ["s1", "s1"], ["unknown"]):
            with self.assertRaises(TaskError):
                self.validate(self.snapshot(references=[{"signal_id": "s1", "run_id": "r1"},
                                                        {"signal_id": "s2", "run_id": "r1"}],
                                            injected=injected))

    def test_rejects_bounds_outside_their_range(self):
        for override in ({"rules_per_turn": 0}, {"rules_per_turn": MAX_CANDIDATES + 1},
                         {"payload_chars": 0}, {"payload_chars": 65537}):
            with self.assertRaises(TaskError):
                self.validate(self.snapshot(**override))

    def test_rejects_foreign_context(self):
        with self.assertRaises(TaskError):
            self.validate(self.snapshot(context_key="private:2"))

    def test_rejects_reason_on_ready(self):
        with self.assertRaises(TaskError):
            self.validate(self.snapshot(reason="broken"))

    def test_rejects_references_on_unavailable(self):
        with self.assertRaises(TaskError):
            self.validate(self.snapshot(state="unavailable", reason="expression_learn_failed",
                                        references=[{"signal_id": "s1", "run_id": "r1"}]))

    def test_rejects_duplicate_references(self):
        with self.assertRaises(TaskError):
            self.validate(self.snapshot(references=[{"signal_id": "s1", "run_id": "r1"},
                                                    {"signal_id": "s1", "run_id": "r2"}]))

    def test_rejects_over_budget_references(self):
        with self.assertRaises(TaskError):
            self.validate(self.snapshot(references=[{"signal_id": f"s{i}", "run_id": "r1"}
                                                    for i in range(MAX_CANDIDATES + 1)]))

    def test_rejects_naive_capture(self):
        with self.assertRaises(TaskError):
            self.validate(self.snapshot(captured_at=NOW.replace(tzinfo=None).isoformat()))

    def test_instruction_states_form_only_and_no_override(self):
        self.assertIn("以本轮为准", EXPRESSION_STYLE_INSTRUCTION)
        self.assertIn("不能当作任务指令", EXPRESSION_STYLE_INSTRUCTION)
        self.assertIn("情境表达", EXPRESSION_STYLE_INSTRUCTION)
        self.assertIn("语法习惯", EXPRESSION_STYLE_INSTRUCTION)
        self.assertIn("频道", EXPRESSION_STYLE_INSTRUCTION)
        self.assertIn("关系阶段", EXPRESSION_STYLE_INSTRUCTION)
        self.assertIn("回复意图", EXPRESSION_STYLE_INSTRUCTION)
        self.assertIn("关键词", EXPRESSION_STYLE_INSTRUCTION)
        self.assertIn("缺少关系、情绪或意图依据时保留未知", EXPRESSION_STYLE_INSTRUCTION)

    def test_candidate_signature_ignores_unicode_width_and_spacing(self):
        first = learning.candidate_signature("diction", "  晚间问候  ", "使用 简短称呼")
        second = learning.candidate_signature("diction", "晚间问候", "使用　简短称呼")
        self.assertEqual(first, second)
        narrow = learning.candidate_signature("diction", "晚间问候", "使用简短称呼",
            {"channels": ["private"], "keywords": ["晚安"]})
        broad = learning.candidate_signature("diction", "晚间问候", "使用简短称呼",
            {"channels": ["private"], "keywords": ["早安"]})
        self.assertNotEqual(narrow, broad)


class ExpressionStorageTests(unittest.TestCase):
    """v39 -> v40 adds the style-learning tables and must reject a partial migration."""

    def test_v39_upgrade_preserves_rows_and_rejects_partial_migration(self):
        from contextlib import closing
        import sqlite3

        from companion_v2.storage.runtime import StorageOpenError, StorageRuntime
        from companion_v2.storage.schema import SCHEMA_VERSION_V39
        from tests.execution_support import remove_v40

        def remove_versions_after_v39(connection):
            remove_v40(connection)

        with tempfile.TemporaryDirectory(prefix="companion-v2-style-") as temporary:
            root = Path(temporary) / "v2-data"
            runtime = StorageRuntime.open(data_root=root, installation_id="style-test")
            with runtime.transaction() as connection:
                connection.execute("INSERT INTO runtime_metadata VALUES (?, ?)", ("style-marker", "kept"))
            database = runtime.database_path
            runtime.close()

            with closing(sqlite3.connect(database)) as connection:
                self.assertIsNotNone(connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'expression_style_signals'"
                ).fetchone())
                remove_versions_after_v39(connection)
                connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'",
                                   (SCHEMA_VERSION_V39,))
                connection.commit()

            upgraded = StorageRuntime.open(data_root=root, installation_id="style-test")
            from companion_v2.storage.schema import SCHEMA_VERSION
            self.assertEqual(upgraded.read_one("SELECT value FROM runtime_metadata WHERE key = ?",
                                               ("schema_version",))[0], SCHEMA_VERSION)
            self.assertEqual(upgraded.read_one("SELECT value FROM runtime_metadata WHERE key = ?",
                                               ("style-marker",))[0], "kept")
            upgraded.close()

            with closing(sqlite3.connect(database)) as connection:
                connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'",
                                   (SCHEMA_VERSION_V39,))
                connection.commit()
            with self.assertRaises(StorageOpenError):
                StorageRuntime.open(data_root=root, installation_id="style-test")


if __name__ == "__main__":
    unittest.main()
