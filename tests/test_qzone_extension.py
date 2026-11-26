from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import json
import shutil
import tempfile
import unittest
from uuid import uuid4

from companion_v2.capabilities.models import CapabilityAuthorizationState
from companion_v2.capabilities.qzone_access import QzoneAccess, catalogue
from companion_v2.capabilities.repositories import CapabilityRepository
from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.foundation.outcome import Outcome
from companion_v2.configuration.registry import FieldRegistry
from companion_v2.configuration.service import ConfigurationService
from companion_v2.execution.models import TaskContext, TaskError
from companion_v2.foundation.clock import DeterministicClock
from companion_v2.foundation.refs import (AudienceRef, OperationRef, ScopeKind, ScopeRef, SubjectKind,
                                          SubjectRef, VersionRef)
from companion_v2.identity_policy.directory import IdentityDirectory
from companion_v2.identity_policy.models import AuthenticatedHostPrincipal, AuthorizationTier, RoleBinding
from companion_v2.identity_policy.repository import PersistentIdentityDirectory
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.identity_policy.personas import (ConversationRoute, PersonaBinding, PersonaDirectory,
                                                   PersonaSelector, SelectorKind)
from companion_v2.qzone import feed as F, policy as Y, protocol as P
from companion_v2.qzone.capability import (
    CAPABILITIES, EXTENSION, PUBLISH_ACTION, package_descriptor, tool_descriptors,
)
from companion_v2.qzone.topics import SCENES, TOPIC_SPECS, QzoneTopic, parse_decision
from companion_v2.qzone.topic_prompt import parse_output
from companion_v2.qzone.configuration import (GROUP, QzoneConfigurationOwner, QzoneSettings,
                                              configuration_fields, target_for)
from companion_v2.qzone.references import COMMENT, LIKE, QzonePost, QzoneReferenceStore
from companion_v2.qzone.service import QzoneService, local_day, owner_key
from companion_v2.qzone.transport import TransportOutcome
from companion_v2.storage.models import OperationState
from companion_v2.storage.runtime import StorageRuntime


NOW = datetime(2026, 10, 2, 4, 0, tzinfo=timezone.utc)
INSTALLATION = "installation-qzone"
COOKIE = "uin=o123456789; p_skey=SECRET; qzonetoken=TOKEN"
MSGLIST = ("_preloadCallback({'code':0, msglist:["
           "{tid:'T1', uin:123456789, name:'me', content:'今天把窗台擦干净了', created_time:1, "
           "unikey:'UK1', curkey:'CK1', appid:'311', typeid:'0', abstime:'1', fid:'T1'},"
           "{tid:'T2', uin:987654321, name:'friend', content:'路过看到一只猫', created_time:2, "
           "unikey:'UK2', curkey:'CK2', appid:'311', typeid:'0', abstime:'2', fid:'T2'}]});"
           "document.domain='qq.com';")
LIKED_MSGLIST = MSGLIST.replace("unikey:'UK1'", "liked:1, unikey:'UK1'")
# Every publish states its topic and scene: the service no longer accepts a post
# without a classified decision.
PUBLISH = {"topic": "life_documentary", "scene": "role_activity", "confidence": 0.9, "topic_confirmed": True}


class FakeTransport:
    """One scripted session: it records what was sent and never retries."""

    def __init__(self, *, text=MSGLIST, error="", delivery_unknown=False, status=200):
        self.text, self.error, self.delivery_unknown, self.status = text, error, delivery_unknown, status
        self.sent = []
        self.acquired = 0

    async def credential(self, *, refresh=False):
        self.acquired += 1
        return P.credential_from_cookies(P.parse_cookie_text(COOKIE), source="manual")

    async def send(self, request, *, timeout):
        self.sent.append(request)
        return TransportOutcome(status=self.status, text=self.text, error=self.error,
                                retryable=not self.error, delivery_unknown=self.delivery_unknown)

    async def qzonetoken(self, credential):
        return credential.qzonetoken


class FakeSettings:
    def __init__(self, **overrides):
        self.values = {"enabled": True, "feed_recent_posts": 10, "like_enabled": True, "comment_enabled": True,
                       "publish_enabled": True, "action_min_interval_minutes": 30, "max_actions_per_tick": 1,
                       "comment_recent_posts": 5, "publish_max_daily": 1, **overrides}

    def read(self, context, at):
        return dict(self.values)


class ProtocolTests(unittest.TestCase):
    def test_cookie_shapes_and_signing(self):
        for raw in (COOKIE, {"cookies": COOKIE}, [{"name": "uin", "value": "o123456789"}, {"name": "p_skey", "value": "SECRET"}]):
            fields = P.parse_cookie_text(raw)
            self.assertTrue(P.has_identity_and_secret(fields), raw)
            credential = P.credential_from_cookies(fields)
            self.assertEqual(credential.uin, 123456789)
            self.assertEqual(credential.g_tk, P.gtk("SECRET"))
            self.assertIn("uin=o123456789", credential.header)

    def test_credentials_without_secret_or_uin_are_refused(self):
        with self.assertRaises(TaskError):
            P.credential_from_cookies({"uin": "o123"})
        with self.assertRaises(TaskError):
            P.credential_from_cookies({"p_skey": "SECRET"})

    def test_answers_survive_script_tails_and_javascript_objects(self):
        payload = P.parse_response(MSGLIST)
        self.assertEqual(P.response_code(payload), 0)
        self.assertEqual(len(payload["msglist"]), 2)
        self.assertEqual(P.response_code(P.parse_response('{"code":-3000,"data":{}}')), -3000)
        self.assertEqual(P.response_code(P.parse_response("")), -1)
        self.assertTrue(P.looks_like_auth_failure("请先登录空间", "code=-3000"))
        self.assertFalse(P.looks_like_auth_failure("今天过得不错"))

    def test_requests_carry_the_original_tickets(self):
        credential = P.credential_from_cookies(P.parse_cookie_text(COOKIE))
        post = QzonePost(post_id="T2", author_uin="987654321", author_name="friend", excerpt="路过看到一只猫",
                         unikey="UK2", curkey="CK2", appid="311", typeid="0", abstime="2", fid="T2")
        like = P.like_request(credential, post)
        self.assertTrue(like.effectful)
        self.assertEqual((like.data["unikey"], like.data["curkey"], like.data["fid"]), ("UK2", "CK2", "T2"))
        self.assertEqual(like.params["g_tk"], credential.g_tk)
        self.assertIn("/987654321/mood/T2", like.headers["Referer"])
        comment = P.comment_request(credential, post, "好可爱")
        self.assertEqual(comment.data["topicId"], "987654321_T2__1")
        self.assertEqual(comment.data["content"], "好可爱")
        publish = P.publish_request(credential, "今天很好")
        self.assertEqual(publish.data["hostuin"], 123456789)
        self.assertEqual(publish.data["con"], "今天很好")

    def test_text_is_bounded_and_single_line(self):
        self.assertEqual(P.clean_text("  a\nb  ", limit=10), "a b")
        for bad in ("", "   ", "x" * 11, "a\x00b"):
            with self.assertRaises(TaskError):
                P.clean_text(bad, limit=10)


class FeedTests(unittest.TestCase):
    def test_msglist_becomes_bounded_posts(self):
        posts = F.posts_from_msglist(P.parse_response(MSGLIST), my_uin=123456789, limit=10)
        self.assertEqual([(post.post_id, post.own, post.unikey) for post in posts],
                         [("T1", True, "UK1"), ("T2", False, "UK2")])
        self.assertEqual(F.posts_from_msglist(P.parse_response(MSGLIST), my_uin=123456789, limit=1)[0].post_id, "T1")

    def test_entries_without_identity_or_text_are_dropped(self):
        payload = {"code": 0, "msglist": [{"tid": "T3", "content": "no author"},
                                          {"uin": 1, "content": "no identity"},
                                          {"tid": "T4", "uin": 1, "content": ""}]}
        self.assertEqual(F.posts_from_msglist(payload, my_uin=1, limit=10), ())

    def test_official_promotions_are_filtered(self):
        payload = {"code": 0, "msglist": [{"tid": "T5", "uin": 9, "name": "官方Qzone", "content": "推广"}]}
        self.assertEqual(F.posts_from_msglist(payload, my_uin=1, limit=10), ())

    def test_a_missing_nickname_never_exposes_the_account_number(self):
        payload = {"code": 0, "msglist": [{"tid": "T7", "uin": 123456789, "content": "没有昵称"}]}
        posts = F.posts_from_msglist(payload, my_uin=1, limit=10)
        self.assertEqual(posts[0].author_name, "1******89")
        self.assertNotIn("123456789", posts[0].author_name)
        self.assertEqual(F.display_name("", ""), "QQ空间用户")
        self.assertEqual(F.display_name("真昵称", "123456789"), "真昵称")

    def test_h5_blocks_keep_their_tickets(self):
        html = ('<li data-uin="987654321" data-key="T9" data-nickname="friend" data-unikey="UK9" '
                'data-curkey="CK9" data-fid="F9"><div class="f-info">今天天气不错</div></li>')
        posts = F.posts_from_html(html, my_uin=123456789, limit=5)
        self.assertEqual(len(posts), 1)
        self.assertEqual((posts[0].post_id, posts[0].author_uin, posts[0].unikey, posts[0].fid),
                         ("T9", "987654321", "UK9", "F9"))


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.store = QzoneReferenceStore(ttl_minutes=30, capacity=2)
        self.post = QzonePost(post_id="T1", author_uin="1", author_name="me", excerpt="hello")

    def test_reference_expires_and_never_crosses_a_conversation(self):
        issued = self.store.issue((self.post,), owner_key="role", scope_key="scope", at=NOW)[0]
        self.assertEqual(self.store.resolve(issued.token, owner_key="role", scope_key="scope", at=NOW).post.post_id, "T1")
        with self.assertRaises(TaskError):
            self.store.resolve(issued.token, owner_key="role", scope_key="other", at=NOW)
        with self.assertRaises(TaskError):
            self.store.resolve(issued.token, owner_key="role", scope_key="scope", at=NOW + timedelta(minutes=31))

    def test_one_action_per_reference_and_bounded_capacity(self):
        issued = self.store.issue((self.post,), owner_key="role", scope_key="scope", at=NOW)[0]
        self.store.mark_used(issued, LIKE)
        with self.assertRaises(TaskError):
            self.store.resolve(issued.token, owner_key="role", scope_key="scope", at=NOW, kind=LIKE)
        others = tuple(QzonePost(post_id=f"T{index}", author_uin="1", author_name="me", excerpt="hello")
                       for index in (2, 3, 4))
        self.store.issue(others, owner_key="role", scope_key="scope", at=NOW + timedelta(minutes=1))
        with self.assertRaises(TaskError):
            self.store.resolve(issued.token, owner_key="role", scope_key="scope", at=NOW)


class PolicyTests(unittest.TestCase):
    SETTINGS = FakeSettings().values

    def test_cooldown_escalates_and_clears(self):
        state = Y.QzoneRuntimeState()
        state = Y.note_failure(state, reason="code=-3000", source="feed", at=NOW)
        self.assertEqual(state.last_auth_status, "blocked")
        self.assertTrue(Y.block_reason(state, at=NOW))
        state = Y.note_failure(state, reason="again", source="feed", at=NOW)
        self.assertEqual(state.auth_failure_count, 2)
        state = Y.note_failure(state, reason="again", source="feed", at=NOW)
        self.assertEqual(state.last_auth_status, "stopped")
        self.assertFalse(Y.block_reason(Y.clear_failure(state), at=NOW))

    def test_a_missing_connection_does_not_escalate(self):
        state = Y.note_failure(Y.QzoneRuntimeState(), reason="没有可用的 OneBot 连接", source="credential",
                               at=NOW, recoverable=True)
        self.assertEqual(state.auth_failure_count, 0)
        self.assertEqual(Y.block_reason(state, at=NOW), "")

    def test_spacing_daily_cap_and_duplicates(self):
        state = Y.QzoneRuntimeState()
        self.assertEqual(Y.action_reason(state, self.SETTINGS, kind=LIKE, at=NOW, day="2026-10-02"), "")
        state = Y.note_action(state, kind=LIKE, at=NOW)
        self.assertIn("分钟", Y.action_reason(state, self.SETTINGS, kind=LIKE, at=NOW, day="2026-10-02"))
        self.assertEqual(Y.publish_reason(state, self.SETTINGS, text="今天很好", at=NOW + timedelta(hours=1),
                                          day="2026-10-02"), "")
        state = Y.note_publish(state, text="今天很好", at=NOW, day="2026-10-02")
        self.assertIn("发过", Y.publish_reason(state, self.SETTINGS, text="换一条完全不同的内容",
                                              at=NOW + timedelta(hours=2), day="2026-10-02"))
        self.assertIn("重复", Y.publish_reason(state, self.SETTINGS, text="今天很好",
                                              at=NOW + timedelta(hours=2), day="2026-10-03"))
        self.assertEqual(Y.publish_reason(state, self.SETTINGS, text="下班路上买了花",
                                          at=NOW + timedelta(hours=2), day="2026-10-03"), "")

    def test_unlimited_daily_posts_keep_accounting_spacing_and_duplicates(self):
        settings = {**self.SETTINGS, "daily_limits_enabled": False}
        day = "2026-10-02"
        state = Y.QzoneRuntimeState(publish_day=day, publish_count=20)
        state = Y.note_publish(state, text="今天很好", at=NOW, day=day, topic="life_documentary")
        self.assertEqual(state.publish_count, 21)
        self.assertEqual(state.topic_publish_count["life_documentary"], 1)
        self.assertIn("分钟", Y.publish_reason(state, settings, text="下班路上买了花", at=NOW, day=day))
        later = NOW + timedelta(hours=1)
        self.assertEqual(Y.publish_reason(state, settings, text="下班路上买了花", at=later, day=day), "")
        self.assertIn("重复", Y.publish_reason(state, settings, text="今天很好", at=later, day=day))
        self.assertIn("发过", Y.publish_reason(state, {**settings, "daily_limits_enabled": True},
                                              text="下班路上买了花", at=later, day=day))
        media = Y.note_publish(state, text="一张照片", at=NOW, day=day, media_key="photo-1")
        self.assertIn("重复", Y.publish_reason(media, settings, text="一张照片", at=later, day=day,
                                              media_key="photo-1"))
        self.assertEqual(Y.publish_reason(media, settings, text="另一张照片", at=later, day=day,
                                          media_key="photo-2"), "")
        self.assertIn("未开启", Y.publish_reason(state, {**settings, "publish_enabled": False},
                                                text="下班路上买了花", at=later, day=day))


class QzoneServiceFixture(unittest.TestCase):
    """One assembled service per test; the subclasses below add the cases."""

    def setUp(self):
        # A private directory per test, removed best-effort: the sandbox can deny the
        # permission reset that ``TemporaryDirectory`` performs on Windows.
        self.root = Path(tempfile.gettempdir()) / f"companion-v2-qzone-{uuid4().hex}"
        self.root.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.runtime = StorageRuntime.open(data_root=self.root / "v2", installation_id=INSTALLATION)
        self.addCleanup(self.runtime.close)
        self.scope = ScopeRef(INSTALLATION, "onebot", ScopeKind.PRIVATE, "private-a", bot_id="bot-a",
                              account_id="account-a", conversation_id="private-a", session_id="private-a")
        subject = SubjectRef(INSTALLATION, SubjectKind.HUMAN, "subject-a")
        self.role = SubjectRef(INSTALLATION, SubjectKind.BOT, "role-a")
        self.clock = DeterministicClock.at(NOW)
        identities = IdentityDirectory()
        identities.bind(binding_id="binding-a", principal=AuthenticatedHostPrincipal(
            INSTALLATION, "fixture-host", "onebot", "account-a"), subject=subject, valid_from=NOW - timedelta(minutes=1))
        identities.issue_grant(grant_id="grant-a", subject=subject, scope=self.scope,
                               capability="conversation.reply", tier=AuthorizationTier.CORE,
                               valid_from=NOW - timedelta(minutes=1))
        resolved = identities.resolve_authenticated(AuthenticatedHostPrincipal(
            INSTALLATION, "fixture-host", "onebot", "account-a"), observed_at=NOW).unwrap()
        self.context = TaskContext(resolved, RoleBinding(self.scope, self.role, 1),
                                   AudienceRef(self.scope, (subject,)))
        self.settings = FakeSettings()
        self.transport = FakeTransport()
        self.service = QzoneService(self.runtime, self.settings, self.transport, now=lambda: NOW,
                                    verify_delays=(0.0,), publish_verify_delays=(0.0,))
        self.install()

    def install(self, *, role=True, consent=True):
        repository = CapabilityRepository(self.runtime)
        repository.register_descriptor(package_descriptor(), registered_at=NOW).unwrap()
        repository.acquire(extension_id=EXTENSION, package_id=package_descriptor().package_id,
                           package_version=package_descriptor().package_version, expected_revision=0,
                           observed_at=NOW).unwrap()
        if role:
            repository.set_role_enabled(extension_id=EXTENSION, role=self.role, bot_id=self.scope.bot_id,
                                        enabled=True, expected_revision=0, observed_at=NOW).unwrap()
        if consent:
            for index, capability in enumerate(CAPABILITIES):
                repository.set_authorization(extension_id=EXTENSION, capability_id=capability,
                    subject=self.context.actor.subject, scope=self.scope,
                    state=CapabilityAuthorizationState.ACTIVE, expected_revision=0, observed_at=NOW).unwrap()

    def scan(self, **kwargs):
        import asyncio
        return asyncio.run(self.service.scan(self.context, **kwargs))

class ServiceTests(QzoneServiceFixture):
    def test_scan_issues_references_and_records_no_operation(self):
        result = self.scan(scope="own")
        self.assertEqual(result["count"], 2)
        self.assertEqual([item["author"] for item in result["items"]], ["me", "friend"])
        self.assertTrue(result["items"][0]["own_post"])
        self.assertNotIn("tid", result["items"][0])
        self.assertTrue(all(item["post_ref"] for item in result["items"]))

    def test_a_disabled_extension_never_touches_the_transport(self):
        self.settings.values["enabled"] = False
        with self.assertRaises(TaskError):
            self.scan(scope="own")
        self.assertEqual(self.transport.sent, [])

    def test_without_this_conversation_consent_nothing_is_sent(self):
        repository = CapabilityRepository(self.runtime)
        for capability in CAPABILITIES:
            repository.set_authorization(extension_id=EXTENSION, capability_id=capability,
                subject=self.context.actor.subject, scope=self.scope,
                state=CapabilityAuthorizationState.REVOKED, expected_revision=1, observed_at=NOW).unwrap()
        with self.assertRaises(TaskError):
            self.scan(scope="own")
        self.assertEqual(self.transport.sent, [])

    def test_an_uninstalled_extension_is_refused(self):
        CapabilityRepository(self.runtime).request_discard(extension_id=EXTENSION, expected_revision=1,
                                                           observed_at=NOW).unwrap()
        with self.assertRaises(TaskError):
            self.scan(scope="own")
        self.assertEqual(self.transport.sent, [])

    def test_like_sends_the_tickets_and_refuses_a_repeat(self):
        import asyncio
        items = self.scan(scope="own")["items"]
        self.transport.text = LIKED_MSGLIST
        result = asyncio.run(self.service.like(self.context, post_ref=items[0]["post_ref"], operation_key="k1"))
        self.assertEqual(result["status"], "done")
        like = next(request for request in self.transport.sent if request.description == "post.like")
        self.assertEqual((like.data["unikey"], like.data["curkey"]), ("UK1", "CK1"))
        sent = len(self.transport.sent)
        # Spacing already refuses an immediate repeat; with the interval relaxed the
        # reference itself refuses it, and neither path sends anything new.
        with self.assertRaises(TaskError):
            asyncio.run(self.service.like(self.context, post_ref=items[0]["post_ref"], operation_key="k2"))
        self.service.store.write(owner_key(self.context), Y.QzoneRuntimeState(), at=NOW)
        with self.assertRaises(TaskError) as caught:
            asyncio.run(self.service.like(self.context, post_ref=items[0]["post_ref"], operation_key="k3"))
        self.assertEqual(caught.exception.code, "qzone_post_already_done")
        self.assertEqual(len(self.transport.sent), sent)
        row = self.runtime.read_one("SELECT state FROM operations WHERE owner = ?", ("qzone.actions",))
        self.assertEqual(row["state"], OperationState.COMPLETED.value)

    def test_a_post_already_liked_on_qq_is_not_liked_again(self):
        import asyncio
        self.transport.text = LIKED_MSGLIST
        items = self.scan(scope="own")["items"]
        self.assertTrue(items[0]["liked"])
        sent = len(self.transport.sent)
        result = asyncio.run(self.service.like(self.context, post_ref=items[0]["post_ref"], operation_key="k4"))
        self.assertEqual(result["status"], "already")
        self.assertEqual(len(self.transport.sent), sent)

    def test_an_unconfirmed_like_is_only_reported_as_accepted(self):
        import asyncio
        items = self.scan(scope="own")["items"]
        result = asyncio.run(self.service.like(self.context, post_ref=items[0]["post_ref"], operation_key="k9"))
        self.assertEqual(result["status"], "accepted")
        self.assertFalse(result["verified"])
        self.assertIn("已受理", result["message"])

    def test_publish_respects_the_daily_cap_and_never_replays_an_unknown(self):
        import asyncio
        first = asyncio.run(self.service.publish(self.context, text="今天把窗台擦干净了", operation_key="p1", **PUBLISH))
        self.assertEqual(first["status"], "done")
        with self.assertRaises(TaskError):
            asyncio.run(self.service.publish(self.context, text="换一条完全不同的内容", operation_key="p2", **PUBLISH))
        self.transport.delivery_unknown = True
        self.service.store.write(owner_key(self.context), Y.QzoneRuntimeState(), at=NOW)
        with self.assertRaises(TaskError) as caught:
            asyncio.run(self.service.publish(self.context, text="结果未知的一条", operation_key="p3", **PUBLISH))
        self.assertEqual(caught.exception.code, "qzone_delivery_unknown")
        state = self.runtime.read_one("SELECT state FROM operations WHERE owner = ? AND state = ?",
                                      ("qzone.actions", OperationState.UNKNOWN.value))
        self.assertIsNotNone(state)

    def test_an_auth_failure_cools_down_and_is_persisted(self):
        import asyncio
        self.transport.text = '{"code":-3000,"message":"请先登录空间"}'
        with self.assertRaises(TaskError) as caught:
            asyncio.run(self.service.scan(self.context, scope="own"))
        self.assertEqual(caught.exception.code, "qzone_auth_blocked")
        persisted = self.service.state(self.context)
        self.assertTrue(persisted.auth_block_until)
        self.assertEqual(Y.QzoneRuntimeStore(self.runtime).read(owner_key(self.context)).last_auth_status, "blocked")

    def test_a_repeated_operation_key_is_answered_from_its_receipt(self):
        import asyncio
        first = asyncio.run(self.service.publish(self.context, text="今天把窗台擦干净了", operation_key="same", **PUBLISH))
        self.assertEqual(first["status"], "done")
        # The local guards still refuse an ordinary repeat; clear them the way the
        # next day would, and the recorded receipt answers the retry instead.
        self.service.store.write(owner_key(self.context), Y.QzoneRuntimeState(), at=NOW)
        sent = len(self.transport.sent)
        again = asyncio.run(self.service.publish(self.context, text="今天把窗台擦干净了", operation_key="same", **PUBLISH))
        self.assertEqual(again["status"], "replayed")
        self.assertEqual(len(self.transport.sent), sent)
        with self.assertRaises(TaskError):
            asyncio.run(self.service.publish(self.context, text="同一个键但不同内容",
                                             operation_key="same", **PUBLISH))

    def test_a_made_up_reference_is_refused_before_any_request(self):
        import asyncio
        with self.assertRaises(TaskError) as caught:
            asyncio.run(self.service.like(self.context, post_ref="not-a-real-ref", operation_key="k8"))
        self.assertEqual(caught.exception.code, "qzone_post_ref_stale")
        with self.assertRaises(TaskError) as malformed:
            asyncio.run(self.service.like(self.context, post_ref="", operation_key="k8b"))
        self.assertEqual(malformed.exception.code, "qzone_post_ref_invalid")
        self.assertEqual(self.transport.sent, [])

    def test_day_is_the_local_day(self):
        self.assertEqual(local_day(datetime(2026, 10, 1, 17, 0, tzinfo=timezone.utc)), "2026-10-02")


class CapabilityTests(unittest.TestCase):
    def test_the_included_package_declares_the_four_actions(self):
        descriptor = package_descriptor()
        self.assertEqual(descriptor.extension_id, EXTENSION)
        self.assertEqual(descriptor.capabilities, CAPABILITIES)
        self.assertEqual(len(descriptor.furniture), 1)
        self.assertEqual(descriptor.furniture[0].panel_id, "qzone")


class ConfigurationTests(unittest.TestCase):
    """The group must be readable through the shared service, not merely declared."""

    def setUp(self):
        self.root = Path(tempfile.gettempdir()) / f"companion-v2-qzone-config-{uuid4().hex}"
        self.root.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.runtime = StorageRuntime.open(data_root=self.root / "v2", installation_id=INSTALLATION)
        self.addCleanup(self.runtime.close)
        self.scope = ScopeRef(INSTALLATION, "onebot", ScopeKind.PRIVATE, "private-a", bot_id="bot-a",
                              account_id="account-a", conversation_id="private-a", session_id="private-a")
        subject = SubjectRef(INSTALLATION, SubjectKind.HUMAN, "subject-a")
        self.role = SubjectRef(INSTALLATION, SubjectKind.BOT, "role-a")
        identities = PersistentIdentityDirectory(self.runtime)
        identities.bind(binding_id="binding-a", principal=AuthenticatedHostPrincipal(
            INSTALLATION, "fixture-host", "onebot", "account-a"), subject=subject, valid_from=NOW, expected_revision=1)
        for capability in ("conversation.reply", "configuration.read", "configuration.write"):
            identities.issue_grant(grant_id=f"grant-{capability}", subject=subject, scope=self.scope,
                                   capability=capability, tier=AuthorizationTier.CORE, valid_from=NOW,
                                   expected_revision=identities.revision)
        self.actor = identities.resolve_authenticated(AuthenticatedHostPrincipal(
            INSTALLATION, "fixture-host", "onebot", "account-a"), observed_at=NOW).unwrap()
        # A window target resolves its role through the registered persona route and
        # binding, exactly as it does in production.
        personas = PersonaDirectory(self.runtime)
        personas.register_route(ConversationRoute(self.scope, user=subject), observed_at=NOW)
        personas.set_binding(PersonaBinding("base", PersonaSelector(INSTALLATION, SelectorKind.BOT, bot_id="bot-a"),
                                            self.role, 0, NOW, "fixture", "admin"),
                             expected_revision=0, observed_at=NOW)
        self.context = TaskContext(self.actor, RoleBinding(self.scope, self.role, 1),
                                   AudienceRef(self.scope, (subject,)))
        self.owner = QzoneConfigurationOwner(self.runtime)
        self.service = ConfigurationService(
            self.runtime, FieldRegistry(version="qzone-config.v1", fields=configuration_fields()),
            owners=(self.owner,))
        self.settings = QzoneSettings(self.service)

    def patch(self, changes, *, key):
        target = target_for(self.context)
        snapshot = self.service.read(self.actor, target=target, group_id=GROUP, observed_at=NOW).unwrap()
        return self.service.patch(self.actor, target=target, group_id=GROUP,
                                  operation=OperationRef(key, self.owner.owner_id, self.scope,
                                                         VersionRef(key + "-lineage", 1), key),
                                  expected_edit_token=snapshot.edit_token, changes=changes, observed_at=NOW)

    def test_defaults_are_safe_and_saved_values_are_read_back(self):
        values = self.settings.read(self.context, NOW)
        self.assertFalse(values["enabled"])
        self.assertFalse(values["like_enabled"])
        self.assertFalse(values["publish_enabled"])
        self.assertTrue(values["daily_limits_enabled"])
        self.assertEqual(values["feed_recent_posts"], 10)
        self.patch({GROUP + ".enabled": True, GROUP + ".publish_enabled": True, GROUP + ".feed_recent_posts": 7},
                   key="qzone-config-1").unwrap()
        updated = self.settings.read(self.context, NOW)
        self.assertTrue(updated["enabled"])
        self.assertTrue(updated["publish_enabled"])
        self.assertEqual(updated["feed_recent_posts"], 7)

    def test_an_out_of_range_value_is_refused_before_it_is_stored(self):
        self.assertFalse(self.patch({GROUP + ".feed_recent_posts": 99}, key="qzone-config-2").is_success)
        self.assertEqual(self.settings.read(self.context, NOW)["feed_recent_posts"], 10)

    def test_daily_limits_switch_roundtrips_and_rejects_non_boolean_values(self):
        name = GROUP + ".daily_limits_enabled"
        self.patch({name: False}, key="qzone-daily-unlimited").unwrap()
        self.assertFalse(self.settings.read(self.context, NOW)["daily_limits_enabled"])
        for index, value in enumerate(("false", 0)):
            with self.subTest(value=value):
                self.assertFalse(self.patch({name: value}, key=f"qzone-daily-invalid-{index}").is_success)
                self.assertFalse(self.settings.read(self.context, NOW)["daily_limits_enabled"])
        self.patch({name: True}, key="qzone-daily-limited").unwrap()
        self.assertTrue(self.settings.read(self.context, NOW)["daily_limits_enabled"])


class AccessTests(unittest.TestCase):
    """The page's own path: an operator installs, and only then can the role act."""

    def setUp(self):
        self.root = Path(tempfile.gettempdir()) / f"companion-v2-qzone-access-{uuid4().hex}"
        self.root.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.runtime = StorageRuntime.open(data_root=self.root / "v2", installation_id=INSTALLATION)
        self.addCleanup(self.runtime.close)
        self.scope = ScopeRef(INSTALLATION, "onebot", ScopeKind.PRIVATE, "private-a", bot_id="bot-a",
                              account_id="account-a", conversation_id="private-a", session_id="private-a")
        self.identities = PersistentIdentityDirectory(self.runtime)
        self.policy = ObjectAccessPolicy(self.runtime)
        target = self.person("target", AuthorizationTier.IMPORTANT)
        self.operator = self.person("operator", AuthorizationTier.CORE)
        self.role = SubjectRef(INSTALLATION, SubjectKind.BOT, "role-a")
        personas = PersonaDirectory(self.runtime)
        personas.register_route(ConversationRoute(self.scope, user=target.subject), observed_at=NOW)
        personas.set_binding(PersonaBinding("base", PersonaSelector(INSTALLATION, SelectorKind.BOT, bot_id="bot-a"),
                                            self.role, 0, NOW, "fixture", "admin"),
                             expected_revision=0, observed_at=NOW)
        self.context = TaskContext(target, RoleBinding(self.scope, self.role, 1),
                                   AudienceRef(self.scope, (target.subject,)))
        self.application = SimpleNamespace(runtime=self.runtime, tasks=SimpleNamespace(
            authorize_context=lambda context, action=None, observed_at=None: Outcome.success(True)))
        self.settings = FakeSettings()
        self.transport = FakeTransport()
        self.service = QzoneService(self.runtime, self.settings, self.transport, now=lambda: NOW,
                                    verify_delays=(0.0,), publish_verify_delays=(0.0,))
        self.access = QzoneAccess(self.application, self.context, service=self.service)

    def person(self, name, tier):
        principal = AuthenticatedHostPrincipal(INSTALLATION, "host", "onebot", name, "bot-a", "login-a")
        subject = SubjectRef(INSTALLATION, SubjectKind.HUMAN, name)
        self.identities.bind(binding_id=name, principal=principal, subject=subject, valid_from=NOW,
                             expected_revision=self.identities.revision)
        self.policy.set_tier(subject, tier, expected_revision=self.policy.revision, observed_at=NOW)
        return self.identities.resolve_authenticated(principal, observed_at=NOW).unwrap()

    def configure(self, enabled, *, key, command=None):
        status = self.access.status(self.operator, NOW)
        self.last_command = command or {"operation_key": key, "edit_token": status["edit_token"],
                                       "enabled": enabled}
        return self.access.configure(self.operator, dict(self.last_command), NOW)

    def test_install_enables_the_role_and_revoking_stops_it(self):
        import asyncio
        before = self.access.status(self.operator, NOW)
        self.assertFalse(before["installed"])
        self.assertEqual(before["reason"], "qzone_not_installed")
        with self.assertRaises(TaskError):
            asyncio.run(self.service.scan(self.context, scope="own"))
        installed = self.configure(True, key="op-1")
        status = installed["status"]
        self.assertTrue(status["installed"])
        self.assertTrue(status["active"])
        self.assertTrue(status["role_enabled"])
        self.assertTrue(all(status["consented"].values()))
        self.assertTrue(status["invoke_granted"])
        self.assertTrue(status["publish_granted"])
        self.assertTrue(self.identities.authorize(self.context.actor, scope=self.scope,
                                                  capability="qzone.post.publish", observed_at=NOW).is_success)
        self.assertEqual(status["reason"], "")
        scanned = asyncio.run(self.service.scan(self.context, scope="own"))
        self.assertEqual(scanned["count"], 2)
        # 同一个操作键必须带着同样的参数重放，才会答以「此前已完成」。
        self.assertTrue(self.configure(True, key="op-1", command=self.last_command)["replayed"])
        revoked = self.configure(False, key="op-2")["status"]
        self.assertFalse(any(revoked["consented"].values()))
        self.assertFalse(revoked["publish_granted"])
        self.assertFalse(self.identities.authorize(self.context.actor, scope=self.scope,
                                                   capability="qzone.post.publish", observed_at=NOW).is_success)
        self.assertTrue(revoked["installed"])
        with self.assertRaises(TaskError):
            asyncio.run(self.service.scan(self.context, scope="own"))

    def test_rotated_conversation_can_be_reauthorized_from_its_own_status(self):
        self.configure(True, key="authorize-original")
        scope = replace(self.scope, conversation_id="new-conversation")
        PersonaDirectory(self.runtime).register_route(
            ConversationRoute(scope, user=self.context.actor.subject), observed_at=NOW,
        )
        context = replace(
            self.context, role_binding=RoleBinding(scope, self.role, 1),
            audience=AudienceRef(scope, (self.context.actor.subject,)),
        )
        access = QzoneAccess(self.application, context, service=self.service)

        status = access.status(self.operator, NOW)
        self.assertEqual(status["reason"], "qzone_conversation_changed")
        self.assertFalse(status["consented"][PUBLISH_ACTION])
        with self.assertRaises(TaskError) as caught:
            self.service.require_publish_authorization(context)
        self.assertEqual(caught.exception.code, "qzone_conversation_changed")
        self.assertEqual(self.transport.sent, [])

        result = access.configure(self.operator, {
            "operation_key": "authorize-current", "edit_token": status["edit_token"], "enabled": True,
        }, NOW)["status"]
        self.assertTrue(result["consented"][PUBLISH_ACTION])
        self.assertTrue(result["publish_granted"])
        self.assertEqual(result["reason"], "")
        self.service.require_publish_authorization(context)

    def test_a_stale_edit_token_is_refused(self):
        status = self.access.status(self.operator, NOW)
        self.configure(True, key="op-3")
        with self.assertRaises(TaskError) as caught:
            self.access.configure(self.operator, {"operation_key": "op-4", "edit_token": status["edit_token"],
                                                  "enabled": False}, NOW)
        self.assertEqual(caught.exception.code, "qzone_access_changed")

    def test_reconfiguring_an_enabled_install_restores_a_missing_publish_grant(self):
        installed = self.configure(True, key="install-before-upgrade")["status"]
        settings = dict(self.settings.values)
        publish = next(grant for grant in self.identities.snapshot().grants
                       if grant.capability == "qzone.post.publish")
        self.identities.revoke_grant(publish.grant_id, revoked_at=NOW,
                                    expected_revision=self.identities.revision)
        missing = self.access.status(self.operator, NOW)
        self.assertTrue(missing["installed"] and missing["active"] and missing["role_enabled"])
        self.assertEqual(missing["consented"], installed["consented"])
        self.assertTrue(missing["invoke_granted"])
        self.assertFalse(missing["publish_granted"])
        self.assertEqual(missing["reason"], "qzone_grant_missing")

        restored = self.configure(True, key="repair-existing-install")["status"]
        self.assertTrue(restored["publish_granted"])
        self.assertEqual(restored["reason"], "")
        self.assertEqual(restored["consented"], installed["consented"])
        self.assertEqual(self.settings.values, settings)
        revoked = self.configure(False, key="revoke-repaired-install")["status"]
        self.assertFalse(revoked["publish_granted"])

    def test_a_malformed_command_is_refused(self):
        for command in ({}, {"operation_key": "k", "edit_token": "t"}, {"operation_key": "k", "edit_token": "t",
                                                                        "enabled": "yes", "extra": 1}):
            with self.assertRaises(TaskError):
                self.access.configure(self.operator, command, NOW)


class TopicTests(unittest.TestCase):
    """The catalogue is the only classifier; the program never reads the text."""

    def settings(self, **overrides):
        values = dict(FakeSettings().values)
        values.update({"topic_auto_enabled": False, "topic_require_confirmation": True,
                       "topic_prompt_guidance": ""})
        for item in TOPIC_SPECS:
            key = item.topic.value
            values.update({f"{key}_enabled": True, f"{key}_daily_max": 1, f"{key}_cooldown_minutes": 30,
                           f"{key}_min_confidence": 0.65,
                           f"{key}_scenes": ",".join(scene.value for scene in item.scenes)})
        values.update(overrides)
        return values

    def state(self, **overrides):
        return Y.QzoneRuntimeState(**overrides)

    def reason(self, *, topic, scene, confidence=0.9, confirmed=True, settings=None, state=None, at=NOW):
        return Y.topic_reason(state or self.state(), settings or self.settings(), topic=topic, scene=scene,
                              confidence=confidence, topic_confirmed=confirmed, at=at, day="2026-10-02")

    def test_every_topic_may_only_use_its_own_scenes(self):
        for item in TOPIC_SPECS:
            allowed = {scene.value for scene in item.scenes}
            for scene in SCENES:
                code, _message = self.reason(topic=item.topic.value, scene=scene)
                if scene in allowed:
                    self.assertEqual(code, "", f"{item.topic.value}/{scene} should be allowed")
                else:
                    self.assertEqual(code, "qzone_scene_invalid", f"{item.topic.value}/{scene} must be refused")

    def test_topic_and_scene_are_both_required(self):
        self.assertEqual(self.reason(topic="", scene="role_activity")[0], "qzone_topic_required")
        self.assertEqual(self.reason(topic="life_documentary", scene="")[0], "qzone_scene_required")
        self.assertEqual(self.reason(topic="not_a_topic", scene="role_activity")[0], "qzone_topic_invalid")
        self.assertEqual(self.reason(topic="life_documentary", scene="not_a_scene")[0], "qzone_scene_invalid")

    def test_configured_gates_match_the_catalogue(self):
        off = self.settings(life_documentary_enabled=False)
        self.assertEqual(self.reason(topic="life_documentary", scene="role_activity",
                                     settings=off)[0], "qzone_topic_disabled")
        narrowed = self.settings(life_documentary_scenes="conversation")
        self.assertEqual(self.reason(topic="life_documentary", scene="role_activity",
                                     settings=narrowed)[0], "qzone_topic_not_allowed")
        strict = self.settings(opinion_hot_min_confidence=0.95)
        self.assertEqual(self.reason(topic="opinion_hot", scene="external_material", confidence=0.9,
                                     settings=strict)[0], "qzone_topic_confidence_low")

    def test_a_model_proposal_needs_a_human_until_auto_mode_is_on(self):
        self.assertEqual(self.reason(topic="life_documentary", scene="role_activity",
                                     confirmed=False)[0], "qzone_topic_confirmation_required")
        auto = self.settings(topic_auto_enabled=True, topic_require_confirmation=False)
        self.assertEqual(self.reason(topic="life_documentary", scene="role_activity",
                                     confirmed=False, settings=auto)[0], "")
        # Turning auto on is not enough while the confirmation gate stays on.
        half = self.settings(topic_auto_enabled=True, topic_require_confirmation=True)
        self.assertEqual(self.reason(topic="life_documentary", scene="role_activity",
                                     confirmed=False, settings=half)[0], "qzone_topic_confirmation_required")

    def test_per_topic_daily_cap_and_cooldown(self):
        capped = self.state(topic_publish_day={"life_documentary": "2026-10-02"},
                            topic_publish_count={"life_documentary": 1})
        self.assertEqual(self.reason(topic="life_documentary", scene="role_activity",
                                     state=capped)[0], "qzone_topic_daily_limit")
        self.assertEqual(self.reason(topic="emotion_reflection", scene="conversation",
                                     state=capped)[0], "")
        cooling = self.state(topic_last_action_at={"life_documentary": (NOW - timedelta(minutes=5)).isoformat()})
        self.assertEqual(self.reason(topic="life_documentary", scene="role_activity",
                                     state=cooling)[0], "qzone_topic_cooldown")

    def test_zero_topic_daily_cap_blocks_first_post_on_any_day(self):
        settings = self.settings(life_documentary_daily_max=0)
        for state in (self.state(), self.state(topic_publish_day={"life_documentary": "2026-10-01"},
                                              topic_publish_count={"life_documentary": 1})):
            with self.subTest(state=state):
                self.assertEqual(self.reason(topic="life_documentary", scene="role_activity",
                                             settings=settings, state=state)[0], "qzone_topic_daily_limit")

    def test_unlimited_daily_topics_keep_cooldowns_and_topic_enablement(self):
        settings = self.settings(daily_limits_enabled=False, life_documentary_daily_max=0)
        state = self.state(topic_publish_day={"life_documentary": "2026-10-02"},
                           topic_publish_count={"life_documentary": 20})
        self.assertEqual(self.reason(topic="life_documentary", scene="role_activity",
                                     settings=settings, state=state)[0], "")
        self.assertEqual(self.reason(topic="life_documentary", scene="role_activity", state=state,
                                     settings={**settings, "daily_limits_enabled": True})[0],
                         "qzone_topic_daily_limit")
        cooling = self.state(topic_last_action_at={"life_documentary": NOW.isoformat()})
        self.assertEqual(self.reason(topic="life_documentary", scene="role_activity",
                                     settings=settings, state=cooling)[0], "qzone_topic_cooldown")
        self.assertEqual(self.reason(topic="life_documentary", scene="role_activity", state=state,
                                     settings={**settings, "life_documentary_enabled": False})[0],
                         "qzone_topic_disabled")

    def test_the_decision_document_is_strict(self):
        decision = parse_decision({"topic": "help_interaction", "scene": "agenda", "confidence": 0.8,
                                   "should_publish": True, "reason": "日程受阻",
                                   "evidence_refs": ["agenda:1"],
                                   "agenda_proposal": {"agenda_id": "agenda-1", "expected_revision": 3,
                                                       "intent": "把时间挪到晚上", "source_refs": ["agenda:1"]}})
        self.assertEqual(decision.topic, QzoneTopic.HELP_INTERACTION)
        self.assertEqual(decision.agenda_proposal.status, "pending_confirmation")
        self.assertEqual(decision.agenda_proposal.expected_revision, 3)
        for bad in ({"topic": "life_documentary", "scene": "agenda", "confidence": 0.9},
                    {"topic": "life_documentary", "scene": "role_activity", "confidence": 1.5},
                    {"topic": "life_documentary", "scene": "role_activity", "confidence": 0.9, "extra": 1},
                    {"topic": "help_interaction", "scene": "conversation", "confidence": 0.9,
                     "agenda_proposal": {"agenda_id": "a", "expected_revision": 1, "intent": "x"}}):
            with self.assertRaises(ValueError):
                parse_decision(bad)

    def test_the_prompt_gate_uses_the_configured_confidence(self):
        payload = {"topic": "life_documentary", "scene": "role_activity", "confidence": 0.5,
                   "should_publish": True, "reason": ""}
        self.assertFalse(parse_output(payload, settings=self.settings()).should_publish)
        self.assertTrue(parse_output(payload, settings=self.settings(
            life_documentary_min_confidence=0.4)).should_publish)
        with self.assertRaises(ValueError):
            parse_output(payload, settings=self.settings(), require_publish=True)

    def test_the_tool_description_carries_the_whole_catalogue(self):
        publish = next(item for item in tool_descriptors() if item.tool.action == "qzone.post.publish")
        schema = json.loads(publish.tool.parameters_json)
        self.assertEqual(sorted(schema["required"]), ["confidence", "scene", "text", "topic"])
        self.assertEqual(schema["properties"]["topic"]["enum"], [item.topic.value for item in TOPIC_SPECS])
        for item in TOPIC_SPECS:
            self.assertIn(item.topic.value, publish.tool.description)
            self.assertIn(item.label, publish.tool.description)
            for scene in item.scenes:
                self.assertIn(scene.value, publish.tool.description)
        self.assertIn("agenda_proposal", schema["properties"])
        self.assertEqual(schema["additionalProperties"], False)

    def test_the_conversation_guidance_is_not_the_classification_contract(self):
        from companion_v2.qzone.topic_prompt import CONVERSATION_GUIDANCE, INSTRUCTION
        self.assertIn("只输出一个 JSON 对象", INSTRUCTION)
        # The turn guidance must not tell a normal reply to answer with JSON only.
        self.assertNotIn("只输出一个 JSON 对象", CONVERSATION_GUIDANCE)
        for item in TOPIC_SPECS:
            self.assertIn(item.topic.value, CONVERSATION_GUIDANCE)
        self.assertIn("不要声称已经修改日程", CONVERSATION_GUIDANCE)

    def test_the_status_exposes_the_same_catalogue_to_the_page(self):
        published = catalogue()
        self.assertEqual([item["id"] for item in published["topics"]], [item.topic.value for item in TOPIC_SPECS])
        self.assertEqual(published["scenes"], dict(SCENES))
        for item in published["topics"]:
            self.assertTrue(item["label"] and item["guidance"] and item["scenes"])

    def test_the_catalogue_offers_only_scenes_the_gate_would_allow(self):
        narrowed = catalogue({"life_documentary_scenes": "conversation",
                              "life_documentary_enabled": False})
        topic = next(item for item in narrowed["topics"] if item["id"] == "life_documentary")
        self.assertEqual(topic["scenes"], ["conversation"])
        self.assertIn("role_activity", topic["declared_scenes"])
        self.assertFalse(topic["enabled"])
        # A broken configured list falls back to the declared scenes instead of
        # offering an empty picker.
        broken = next(item for item in catalogue({"life_documentary_scenes": "not_a_scene"})["topics"]
                      if item["id"] == "life_documentary")
        self.assertEqual(broken["scenes"], broken["declared_scenes"])


class TopicPublishTests(QzoneServiceFixture):
    """Publishing without a topic is impossible; the gates are the service's, not the caller's."""

    def test_a_model_call_cannot_confirm_its_own_topic(self):
        import asyncio
        with self.assertRaises(TaskError) as caught:
            asyncio.run(self.service.publish(self.context, text="今天把窗台擦干净了",
                                             topic="life_documentary", scene="role_activity",
                                             confidence=0.9, topic_confirmed=False, operation_key="m1"))
        self.assertEqual(caught.exception.code, "qzone_topic_confirmation_required")
        self.assertEqual([request.description for request in self.transport.sent if request.effectful], [])

    def test_auto_mode_lets_the_model_publish_and_records_the_topic(self):
        import asyncio
        self.settings.values.update({"topic_auto_enabled": True, "topic_require_confirmation": False})
        for item in TOPIC_SPECS:
            key = item.topic.value
            self.settings.values.update({f"{key}_min_confidence": 0.65, f"{key}_scenes":
                                         ",".join(scene.value for scene in item.scenes)})
        result = asyncio.run(self.service.publish(self.context, text="今天把窗台擦干净了",
                                                 topic="life_documentary", scene="role_activity",
                                                 confidence=0.9, topic_confirmed=False, operation_key="m2"))
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["topic"], "life_documentary")
        self.assertFalse(result["topic_confirmed"])
        state = self.service.state(self.context)
        self.assertEqual(state.topic_publish_count.get("life_documentary"), 1)

    def test_a_help_post_carries_a_pending_agenda_proposal_without_touching_agenda(self):
        import asyncio
        self.settings.values.update({"topic_auto_enabled": True, "topic_require_confirmation": False,
                                     "help_interaction_scenes": "agenda"})
        result = asyncio.run(self.service.publish(
            self.context, text="日程里的那份稿子卡在资料上，有人知道去哪找吗",
            topic="help_interaction", scene="agenda", confidence=0.8, topic_confirmed=False,
            source_refs=["agenda:9"], operation_key="h1",
            agenda_proposal={"agenda_id": "agenda-9", "expected_revision": 2, "intent": "补齐资料再推进",
                             "source_refs": ["agenda:9"]}))
        self.assertEqual(result["topic"], "help_interaction")
        self.assertEqual(result["agenda_proposal"]["status"], "pending_confirmation")
        self.assertIn("待确认", result["message"])
        # The proposal is carried as evidence in the operation's parameter hash and
        # in the result; nothing in the agenda domain is modified here.
        self.assertEqual(result["agenda_proposal"]["agenda_id"], "agenda-9")
        self.assertEqual(result["source_refs"], ["agenda:9"])
        row = self.runtime.read_one("SELECT parameter_hash, state FROM operations WHERE owner = ?", ("qzone.actions",))
        self.assertEqual(row["state"], "completed")
        self.assertTrue(row["parameter_hash"])

    def test_a_proposal_is_refused_outside_the_help_agenda_pair(self):
        import asyncio
        with self.assertRaises(TaskError) as caught:
            asyncio.run(self.service.publish(self.context, text="今天把窗台擦干净了",
                                             topic="life_documentary", scene="role_activity",
                                             confidence=0.9, topic_confirmed=True, operation_key="h2",
                                             agenda_proposal={"agenda_id": "a", "expected_revision": 1,
                                                              "intent": "改一下"}))
        self.assertEqual(caught.exception.code, "qzone_request_invalid")


class AgendaSuggestionTests(QzoneServiceFixture):
    """A help post leaves a queryable question; the agenda write stays in its own domain."""

    def help_post(self, *, key="h1", proposal=True):
        import asyncio
        self.settings.values.update({"topic_auto_enabled": True, "topic_require_confirmation": False,
                                     "help_interaction_scenes": "agenda"})
        arguments = {"text": "日程里的那份稿子卡在资料上，有人知道去哪找吗", "topic": "help_interaction",
                     "scene": "agenda", "confidence": 0.8, "topic_confirmed": False,
                     "source_refs": ["agenda:9"], "operation_key": key}
        if proposal:
            arguments["agenda_proposal"] = {"agenda_id": "agenda-9", "expected_revision": 2,
                                            "intent": "补齐资料再推进", "source_refs": ["agenda:9"]}
        return asyncio.run(self.service.publish(self.context, **arguments))

    def test_a_help_post_records_exactly_one_pending_question(self):
        self.help_post()
        pending = self.service.agenda_suggestions(self.context)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].agenda_id, "agenda-9")
        self.assertEqual(pending[0].expected_revision, 2)
        self.assertEqual(pending[0].state, "pending")
        # A replay of the same publish must not create a second question. The local
        # gates would refuse the repeat first, so clear them the way the next day
        # would and let the recorded receipt answer it.
        self.service.store.write(owner_key(self.context), Y.QzoneRuntimeState(), at=NOW)
        self.help_post(key="h1")
        self.assertEqual(len(self.service.agenda_suggestions(self.context)), 1)
        status = self.service.status(self.context, NOW)
        self.assertEqual(status["agenda_suggestions"][0]["agenda_id"], "agenda-9")

    def test_the_question_is_offered_to_the_model_until_it_is_closed(self):
        from companion_v2.qzone.agenda_link import SuggestionStore
        from companion_v2.qzone.service import owner_key as qzone_owner_key
        store = SuggestionStore(self.runtime)
        self.assertEqual(store.material(qzone_owner_key(self.context)), "")
        self.help_post()
        material = json.loads(store.material(qzone_owner_key(self.context)))
        self.assertEqual(material["open_agenda_suggestions"][0]["agenda_id"], "agenda-9")
        self.assertIn("intent", material["open_agenda_suggestions"][0])

    def test_applying_needs_a_calendar_receipt_and_is_idempotent(self):
        import asyncio
        self.help_post()
        suggestion_id = self.service.agenda_suggestions(self.context)[0].suggestion_id
        with self.assertRaises(TaskError) as caught:
            asyncio.run(self.service.resolve_agenda_suggestion(
                self.context, suggestion_id=suggestion_id, state="applied", operation_key="r1"))
        self.assertEqual(caught.exception.code, "qzone_suggestion_receipt_required")
        self.assertEqual(self.service.agenda_suggestions(self.context)[0].state, "pending")
        applied = asyncio.run(self.service.resolve_agenda_suggestion(
            self.context, suggestion_id=suggestion_id, state="applied", operation_key="r2",
            calendar_operation_id="calendar:9", result_refs=["comment:7"]))
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(applied["suggestion"]["calendar_operation_id"], "calendar:9")
        self.assertEqual(self.service.agenda_suggestions(self.context), ())
        again = asyncio.run(self.service.resolve_agenda_suggestion(
            self.context, suggestion_id=suggestion_id, state="applied", operation_key="r2",
            calendar_operation_id="calendar:9", result_refs=["comment:7"]))
        self.assertEqual(again["status"], "replayed")
        self.assertEqual(again["suggestion"]["calendar_operation_id"], "calendar:9")

    def test_dismissing_needs_no_receipt_and_keeps_the_record(self):
        import asyncio
        self.help_post()
        suggestion_id = self.service.agenda_suggestions(self.context)[0].suggestion_id
        result = asyncio.run(self.service.resolve_agenda_suggestion(
            self.context, suggestion_id=suggestion_id, state="dismissed", operation_key="d1"))
        self.assertEqual(result["status"], "dismissed")
        self.assertEqual(self.service.agenda_suggestions(self.context), ())
        history = self.service.all_agenda_suggestions(self.context)
        self.assertEqual([item.state for item in history], ["dismissed"])
        with self.assertRaises(TaskError):
            asyncio.run(self.service.resolve_agenda_suggestion(
                self.context, suggestion_id=suggestion_id, state="whatever", operation_key="d2"))
        # A closed question cannot be re-judged under a fresh operation key.
        with self.assertRaises(TaskError) as caught:
            asyncio.run(self.service.resolve_agenda_suggestion(
                self.context, suggestion_id=suggestion_id, state="applied", operation_key="d3",
                calendar_operation_id="calendar:9"))
        self.assertEqual(caught.exception.code, "qzone_suggestion_state_invalid")

    def test_another_role_cannot_close_this_question(self):
        from companion_v2.qzone.agenda_link import SuggestionStore
        self.help_post()
        foreign = SuggestionStore(self.runtime)
        self.assertIsNone(foreign.read("qzone-role:someone-else",
                                       self.service.agenda_suggestions(self.context)[0].suggestion_id))

    def test_a_known_failure_drops_the_question_but_an_unknown_keeps_it(self):
        import asyncio
        self.transport.text = '{"code":-4001,"message":"发布被拒绝"}'
        with self.assertRaises(TaskError):
            self.help_post(key="h5")
        self.assertEqual(self.service.agenda_suggestions(self.context), ())
        history = self.service.all_agenda_suggestions(self.context)
        self.assertEqual([item.state for item in history], ["dismissed"])
        self.service.store.write(owner_key(self.context), Y.QzoneRuntimeState(), at=NOW)
        self.transport.text = MSGLIST
        self.transport.delivery_unknown = True
        with self.assertRaises(TaskError) as caught:
            self.help_post(key="h6")
        self.assertEqual(caught.exception.code, "qzone_delivery_unknown")
        self.assertEqual(len(self.service.agenda_suggestions(self.context)), 1)


if __name__ == "__main__":
    unittest.main()
