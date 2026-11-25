"""Role-level cross-window presence: audience filtering, bounds and prompts."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import unittest

from companion_v2.conversation.awareness_context import ConversationAwarenessContext
from companion_v2.execution.models import AudienceRef, TaskContext, TaskError, encode
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind, ScopeRef, SourceRef, SubjectKind, SubjectRef, VersionRef
from companion_v2.identity_policy.models import AuthenticatedHostPrincipal, ResolvedIdentity, RoleBinding
from companion_v2.role_work.awareness import RoleAwareness
from companion_v2.role_work.awareness.models import MAX_EVENTS, PREFIX, owner_key
from astrbot_host.core_reply_context import _cross_window_section


INSTALLATION = "awareness-installation"
NOW = datetime(2026, 10, 19, 13, 0, tzinfo=timezone.utc)
ROLE = SubjectRef(INSTALLATION, SubjectKind.BOT, "role-a")
PRIVATE = ScopeRef(INSTALLATION, "onebot", ScopeKind.PRIVATE, "200", "bot-a", "100",
                   "conversation-private", "session-private")
GROUP = ScopeRef(INSTALLATION, "onebot", ScopeKind.GROUP, "300", "bot-a", "100",
                 "conversation-group", "session-group")
PRIVATE_B = ScopeRef(INSTALLATION, "onebot", ScopeKind.PRIVATE, "200", "bot-a", "101",
                     "conversation-private-b", "session-private-b")
GROUP_B = ScopeRef(INSTALLATION, "onebot", ScopeKind.GROUP, "300", "bot-a", "101",
                   "conversation-group-b", "session-group-b")


def context(scope, *, subject_id="200", account="200", role=ROLE):
    principal = AuthenticatedHostPrincipal(INSTALLATION, "host", "onebot", account, "bot-a", "100")
    actor = ResolvedIdentity(principal, SubjectRef(INSTALLATION, SubjectKind.HUMAN, subject_id),
                             "binding-" + subject_id, 1, 1)
    binding = RoleBinding(scope, role, 1)
    return TaskContext(actor, binding, AudienceRef(scope, (actor.subject,)))


class FakeCursor:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, statement, parameters=()):
        compact = " ".join(statement.split())
        if compact.startswith("SELECT value FROM runtime_metadata"):
            value = self.rows.get(parameters[0])
            return FakeCursor(None if value is None else {"value": value})
        if compact.startswith("INSERT INTO runtime_metadata"):
            self.rows[parameters[0]] = parameters[1]
            return FakeCursor()
        raise AssertionError("unexpected statement: " + compact)


class FakeRuntime:
    def __init__(self):
        self.rows = {}

    def read_one(self, statement, parameters=()):
        compact = " ".join(statement.split())
        if compact.startswith("SELECT value FROM runtime_metadata"):
            value = self.rows.get(parameters[0])
            return None if value is None else {"value": value}
        raise AssertionError("unexpected statement: " + compact)

    @contextmanager
    def transaction(self):
        yield FakeConnection(self.rows)


class FakeAcquaintance:
    def __init__(self, *, enabled=True, private=False):
        self.on, self.private = enabled, private

    def available(self, context):
        return self.on

    def private_contact(self, context, at):
        return self.private


class FakeSourceValidator:
    def __init__(self, current=True):
        self.current = current

    def validate_reference(self, reference, *, observed_at):
        return (Outcome.success(reference) if self.current
                else Outcome.failed("source_revoked", "fixture source was withdrawn"))


class FakeSettings:
    def __init__(self, **values):
        self.values = values

    def read(self, context):
        return dict(self.values)


class RoleAwarenessTests(unittest.TestCase):
    def setUp(self):
        self.runtime = FakeRuntime()
        self.private = context(PRIVATE)
        self.group = context(GROUP)
        self.other = context(GROUP, subject_id="201", account="201")
        self.acquaintance = FakeAcquaintance()
        self.service = RoleAwareness(self.runtime, acquaintance=self.acquaintance)
        self.prompt = ConversationAwarenessContext(self.service)

    def record_group(self, text="明天谁有空", at=NOW, key="group-1", ctx=None, source=None, peer_count=0,
                     quote_sender_ids=()):
        with self.runtime.transaction() as connection:
            self.service.record_input(connection, ctx or self.group, at, text, key, source=source,
                                      peer_count=peer_count, quote_sender_ids=quote_sender_ids)

    def record_private(self, text="先别告诉群里", at=NOW, key="private-1", ctx=None, source=None):
        with self.runtime.transaction() as connection:
            self.service.record_input(connection, ctx or self.private, at, text, key, source=source)

    def test_private_turn_sees_same_persons_public_group_activity(self):
        self.record_group(peer_count=3)
        payload = self.service.snapshot(self.private, NOW + timedelta(minutes=2))
        self.assertEqual(payload["recent_group_activity"],
                         [{"at": "2026-10-19T13:00:00.000000+00:00",
                           "group_id": "300", "topic": "明天谁有空", "peers": 3}])
        self.assertEqual(payload["attention"], {"last_inbound_window": "group", "minutes_ago": 2})
        material = self.prompt.contributions(self.prompt.capture(self.private, NOW), self.private, NOW)
        self.assertEqual(material.purpose, "cross_window_awareness")
        self.assertEqual(json.loads(material.payload)["recent_group_activity"][0]["peers"], 3)

    def test_peer_reply_edge_counts_without_copying_the_reply_text(self):
        third = context(GROUP, subject_id="202", account="202")
        self.record_group(text="我先说", key="peer-user", at=NOW)
        self.record_group(text="接你一句", key="peer-other", at=NOW + timedelta(seconds=1),
                          ctx=self.other, quote_sender_ids=("200",))
        self.record_group(text="再补一句", key="peer-other-2", at=NOW + timedelta(seconds=2),
                          ctx=self.other, quote_sender_ids=("200",))
        self.record_group(text="我也接", key="peer-third", at=NOW + timedelta(seconds=3),
                          ctx=third, quote_sender_ids=("200",))
        payload = self.service.snapshot(self.private, NOW + timedelta(minutes=2))
        self.assertEqual(payload["peer_replies"], 3)
        self.assertEqual(payload["peer_repliers"], 2)
        self.assertNotIn("接你一句", encode(payload))
        self.assertNotIn('"201"', encode(payload))
        self.assertNotIn('"202"', encode(payload))

    def test_peer_reply_window_is_configurable(self):
        self.record_group(text="我先说", key="peer-window-user", at=NOW)
        self.record_group(text="接你一句", key="peer-window-other", at=NOW + timedelta(seconds=1),
                          ctx=self.other, quote_sender_ids=("200",))
        self.acquaintance.settings = FakeSettings(**{
            "person.cross_window_awareness_enabled": True,
            "person.cross_window_peer_reply_minutes": 1,
        })
        payload = self.service.snapshot(self.private, NOW + timedelta(minutes=2))
        self.assertNotIn("peer_replies", payload)
        payload = self.service.snapshot(self.private, NOW + timedelta(seconds=45))
        self.assertEqual(payload["peer_replies"], 1)

    def test_group_turn_only_gets_a_boolean_private_contact(self):
        self.acquaintance.private = True
        self.record_private()
        snapshot = self.prompt.capture(self.group, NOW + timedelta(minutes=1))
        self.assertEqual(snapshot["payload"], {"private_contact": True})
        serialized = encode(snapshot)
        self.assertNotIn("先别告诉群里", serialized)
        self.assertNotIn("private-1", serialized)
        self.assertIsNotNone(self.prompt.contributions(snapshot, self.group, NOW))

    def test_group_turn_without_private_contact_and_private_text_is_not_stored(self):
        self.record_private()
        self.assertIsNone(self.prompt.capture(self.group, NOW)["payload"])
        row = self.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?",
                                    (PREFIX + owner_key(self.private),))
        self.assertNotIn("先别告诉群里", row["value"])

    def test_window_transition_projects_only_a_boolean_for_the_same_person_and_route(self):
        self.record_private()
        at = NOW + timedelta(minutes=1)
        messages = type("Messages", (), {"cross_window_context": self.prompt})()
        section = _cross_window_section(messages, self.group, at, "换个说法也能接话", input_at=at).render()
        self.assertEqual(json.loads(section.split("\n")[-1])[0]["content"], {"recent_window_transition": True})
        self.assertNotIn("先别告诉群里", section)
        self.assertIsNone(self.service.snapshot(self.other, at, input_at=at))
        self.assertIsNone(self.service.snapshot(context(GROUP_B), at, input_at=at))

        self.record_group(at=at)
        following = at + timedelta(seconds=1)
        self.assertIsNone(self.service.snapshot(self.group, following, input_at=following))
        private = self.service.snapshot(self.private, following, input_at=following)
        self.assertIs(private["recent_window_transition"], True)

    def test_window_transition_respects_input_time_expiry_and_source_withdrawal(self):
        source = SourceRef("private-source", "conversation.native_input", PRIVATE,
                           VersionRef("private-source", 1), NOW)
        self.service.source_validator = FakeSourceValidator()
        self.record_private(source=source)
        at = NOW + timedelta(minutes=31)
        self.assertIsNone(self.service.snapshot(self.group, at, input_at=NOW - timedelta(seconds=1)))
        self.assertIsNone(self.service.snapshot(self.group, at, input_at=at))
        input_at = NOW + timedelta(seconds=30)
        self.assertEqual(self.service.snapshot(self.group, at, input_at=input_at), {"recent_window_transition": True})
        self.service.source_validator.current = False
        self.assertIsNone(self.service.snapshot(self.group, at, input_at=input_at))
        self.service.source_validator.current = True
        self.acquaintance.settings = FakeSettings(**{"person.cross_window_continuity_enabled": False})
        self.assertIsNone(self.service.snapshot(self.group, at, input_at=input_at))

    def test_other_subject_and_other_role_do_not_share_presence(self):
        self.record_group()
        self.assertIsNone(self.service.snapshot(self.other, NOW))
        other_role = context(PRIVATE, role=SubjectRef(INSTALLATION, SubjectKind.BOT, "role-b"))
        self.assertIsNone(self.service.snapshot(other_role, NOW))

    def test_another_members_group_message_does_not_create_the_users_attention(self):
        self.record_group(text="别人的发言", key="other-member", ctx=self.other)
        self.assertIsNone(self.service.snapshot(self.private, NOW + timedelta(minutes=1)))

    def test_another_login_account_does_not_share_the_same_window(self):
        self.record_group(text="账号一百的群消息", key="account-100")
        self.assertIsNone(self.service.snapshot(context(PRIVATE_B), NOW + timedelta(minutes=1)))
        self.record_group(text="账号一零一的群消息", key="account-101", ctx=context(GROUP_B))
        payload = self.service.snapshot(context(PRIVATE_B), NOW + timedelta(minutes=1))
        self.assertEqual(payload["recent_group_activity"][0]["topic"], "账号一零一的群消息")

    def test_withdrawn_group_source_drops_the_topic_but_not_the_attention(self):
        source = json.loads(encode(SourceRef("awareness-source", "conversation.native_input",
                                             GROUP, VersionRef("awareness-source", 1), NOW)))
        self.service.source_validator = FakeSourceValidator(False)
        self.record_group(source=source)
        payload = self.service.snapshot(self.private, NOW + timedelta(minutes=1))
        self.assertNotIn("recent_group_activity", payload)
        self.assertEqual(payload["attention"], {"last_inbound_window": "group", "minutes_ago": 1})
        self.service.source_validator.current = True
        payload = self.service.snapshot(self.private, NOW + timedelta(minutes=1))
        self.assertEqual(payload["recent_group_activity"][0]["topic"], "明天谁有空")

    def test_attention_expires_while_group_topic_remains_bounded(self):
        self.record_group(text="", key="quiet")
        self.assertEqual(self.service.snapshot(self.private, NOW + timedelta(minutes=1)),
                         {"attention": {"last_inbound_window": "group", "minutes_ago": 1}})
        self.assertIsNone(self.service.snapshot(self.private, NOW + timedelta(minutes=31)))
        self.record_group(text="有内容", key="content")
        self.assertEqual(self.service.snapshot(self.private, NOW + timedelta(minutes=31))
                         ["recent_group_activity"][0]["topic"], "有内容")

    def test_returning_to_private_clears_the_just_came_from_group_cue(self):
        self.record_group()
        self.record_private(at=NOW + timedelta(seconds=1))
        payload = self.service.snapshot(self.private, NOW + timedelta(minutes=2))
        self.assertIn("recent_group_activity", payload)
        self.assertNotIn("attention", payload)

    def test_event_ledger_is_bounded(self):
        for index in range(MAX_EVENTS + 6):
            self.record_group(text="话题" + str(index), key="event-" + str(index),
                              at=NOW + timedelta(seconds=index))
        document = json.loads(self.runtime.read_one(
            "SELECT value FROM runtime_metadata WHERE key = ?",
            (PREFIX + owner_key(self.private),))["value"])
        self.assertEqual(len(document["events"]), MAX_EVENTS)
        payload = self.service.snapshot(self.private, NOW + timedelta(minutes=5))
        self.assertLessEqual(len(payload["recent_group_activity"]), 4)

    def test_duplicate_event_key_is_idempotent(self):
        self.record_group(text="第一次", key="same")
        self.record_group(text="第二次", key="same", at=NOW + timedelta(seconds=1))
        document = json.loads(self.runtime.read_one(
            "SELECT value FROM runtime_metadata WHERE key = ?",
            (PREFIX + owner_key(self.private),))["value"])
        self.assertEqual([item["topic"] for item in document["events"]], ["第一次"])

    def test_malformed_or_foreign_document_is_ignored_then_replaced(self):
        key = PREFIX + owner_key(self.private)
        with self.runtime.transaction() as connection:
            connection.execute("INSERT INTO runtime_metadata(key, value) VALUES (?, ?)", (key, "{bad"))
        self.assertIsNone(self.service.snapshot(self.private, NOW))
        self.record_group()
        self.assertIsNotNone(self.service.snapshot(self.private, NOW))

    def test_snapshot_is_bound_to_its_original_context_and_audience(self):
        self.record_group()
        snapshot = self.prompt.capture(self.private, NOW)
        with self.assertRaises(TaskError):
            self.prompt.contributions(snapshot, self.group, NOW)
        forged = {**snapshot, "context_key": self.group.key, "payload": {"recent_group_activity": [
            {"at": "2026-10-19T13:00:00.000000+00:00", "group_id": "300", "topic": "私聊内容"}]}}
        with self.assertRaises(TaskError):
            self.prompt.contributions(forged, self.group, NOW)

    def test_acquaintance_switch_disables_recording_and_reading(self):
        self.acquaintance.on = False
        self.record_group()
        self.assertIsNone(self.service.snapshot(self.private, NOW))
        self.acquaintance.on = True
        self.record_group(key="enabled")
        self.assertIsNotNone(self.service.snapshot(self.private, NOW))

    def test_independent_switches_and_limits_are_read_from_person_settings(self):
        self.acquaintance.settings = FakeSettings(**{
            "person.acquaintance_enabled": True,
            "person.cross_window_awareness_enabled": False,
        })
        self.assertFalse(self.service.enabled(self.private))
        self.record_group()
        self.assertIsNone(self.service.snapshot(self.private, NOW))

        self.acquaintance.settings = FakeSettings(**{
            "person.acquaintance_enabled": True,
            "person.cross_window_awareness_enabled": True,
            "person.cross_window_max_items": 2,
            "person.cross_window_attention_minutes": 0,
        })
        for index in range(6):
            self.record_group(text="话题" + str(index), key="limited-" + str(index),
                              at=NOW + timedelta(seconds=index))
        payload = self.service.snapshot(self.private, NOW + timedelta(minutes=1))
        self.assertEqual([item["topic"] for item in payload["recent_group_activity"]], ["话题4", "话题5"])
        self.assertNotIn("attention", payload)

    def test_core_reply_section_renders_private_activity(self):
        self.record_group()
        messages = type("Messages", (), {"cross_window_context": self.prompt})()
        section = _cross_window_section(messages, self.private, NOW).render()
        self.assertIn("【跨窗口在场】", section)
        self.assertIn("cross_window_awareness", section)
        self.assertIn("明天谁有空", json.loads(json.dumps(section, ensure_ascii=False)))

    def test_core_reply_section_renders_only_the_private_contact_boolean_in_group(self):
        self.acquaintance.private = True
        self.record_private()
        messages = type("Messages", (), {"cross_window_context": self.prompt})()
        section = _cross_window_section(messages, self.group, NOW).render()
        payload = json.loads(section.split("\n")[-1])
        self.assertEqual(payload[0]["content"], {"private_contact": True})
        self.assertNotIn("先别告诉群里", section)


if __name__ == "__main__":
    unittest.main()
