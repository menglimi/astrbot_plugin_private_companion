"""生活说说：判定、概率、操作键与草稿边界。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

# Import the host fixture before companion_v2 / AstrBot SDK imports so ASTRBOT_ROOT stays isolated.
from tests import test_astrbot_staging_host as staging_support
from tests.multi_host_support import MultiFixture, multi_config
from tests.native_adapter_support import FakeEvent
from tests.test_qzone_extension import FakeTransport

from astrbot_host.management_identity import dashboard_operator
from astrbot_host.persona_workshop_api import _session_provider_id, current_role_profile
from astrbot_host.qzone_life_publish import QzoneLifePublisher
from companion_v2.adapters.astrbot.staging import NativeAdapterTypes
from companion_v2.capabilities import CapabilityAuthorizationState
from companion_v2.foundation.refs import (OperationRef, RoleOwnerRef, ScopeKind, ScopeRef,
                                          SubjectKind, SubjectRef, VersionRef)
from companion_v2.qzone import life_publish
from companion_v2.qzone.capability import CAPABILITIES, EXTENSION, package_descriptor
from companion_v2.qzone.configuration import (GROUP as QZONE_GROUP, OWNER as QZONE_OWNER,
                                            target_for as qzone_target_for)
from companion_v2.qzone.policy import QzoneRuntimeState
from companion_v2.qzone.service import local_day
from companion_v2.qzone.topics import QzoneScene, QzoneTopic
from companion_v2.role_profile.configuration import (GROUP as ROLE_PROFILE_GROUP,
                                                 OWNER as ROLE_PROFILE_OWNER,
                                                 target_for as role_profile_target_for)
from companion_v2.role_work.journal import JournalContext, JournalDraft, JournalKey, JournalKind


NOW = datetime(2026, 11, 9, 4, 0, tzinfo=timezone.utc)  # 12:00 Asia/Shanghai
DAY = "2026-11-09"


def settings(**changes):
    values = {"enabled": True, "publish_enabled": True, "life_publish_enabled": True,
              "life_publish_probability": 0.5, "life_publish_min_interval_hours": 24,
              "life_publish_max_daily": 2, "life_publish_style_prompt": ""}
    values.update(changes)
    return values


def state(**changes):
    values = {"publish_day": "", "publish_count": 0, "last_action_at": {}}
    values.update(changes)
    return QzoneRuntimeState(**values)


class DecisionTests(unittest.TestCase):
    def test_master_and_life_switches_are_both_required(self):
        for changes, reason in (({"enabled": False}, "qzone_disabled"),
                                ({"publish_enabled": False}, "publish_disabled"),
                                ({"life_publish_enabled": False}, "life_publish_disabled")):
            self.assertEqual(life_publish.decide(settings(**changes), state(), now=NOW).reason, reason)

    def test_daily_cap_resets_on_a_new_day(self):
        reached = state(publish_day=DAY, publish_count=2)
        self.assertEqual(life_publish.decide(settings(), reached, now=NOW), life_publish.Decision(False, "daily_limit", DAY))
        next_day = life_publish.decide(settings(), reached, now=NOW + timedelta(days=1))
        self.assertTrue(next_day.due)

    def test_unlimited_daily_posts_still_observe_attempt_interval_and_switches(self):
        unlimited = settings(daily_limits_enabled=False)
        reached = state(publish_day=DAY, publish_count=20)
        self.assertTrue(life_publish.decide(unlimited, reached, now=NOW).due)
        self.assertEqual(life_publish.decide({**unlimited, "daily_limits_enabled": True},
                                            reached, now=NOW).reason, "daily_limit")
        attempted = state(publish_day=DAY, publish_count=20,
                          last_action_at={life_publish.ATTEMPT_KEY: NOW.isoformat()})
        self.assertEqual(life_publish.decide(unlimited, attempted, now=NOW).reason, "interval")
        self.assertEqual(life_publish.decide({**unlimited, "life_publish_enabled": False},
                                            reached, now=NOW).reason, "life_publish_disabled")

    def test_interval_counts_failed_attempts_not_only_published_posts(self):
        attempted = state(last_action_at={life_publish.ATTEMPT_KEY: "2026-11-09T02:00:00+00:00"})
        self.assertEqual(life_publish.decide(settings(), attempted, now=NOW).reason, "interval")
        published = state(last_action_at={"publish": "2026-11-07T02:00:00+00:00"})
        self.assertTrue(life_publish.decide(settings(), published, now=NOW).due)

    def test_failed_try_uses_short_retry_window_instead_of_full_interval(self):
        future = state(last_action_at={
            life_publish.ATTEMPT_KEY: "2026-11-09T03:55:00+00:00",
            life_publish.RETRY_KEY: "2026-11-09T04:25:00+00:00",
        })
        self.assertEqual(life_publish.decide(settings(), future, now=NOW).reason, "retry_wait")
        ready = state(last_action_at={
            life_publish.ATTEMPT_KEY: "2026-11-09T03:25:00+00:00",
            life_publish.RETRY_KEY: "2026-11-09T03:55:00+00:00",
        })
        self.assertTrue(life_publish.decide(settings(), ready, now=NOW).due)

    def test_due_when_every_gate_is_open(self):
        self.assertEqual(life_publish.decide(settings(), state(), now=NOW),
                         life_publish.Decision(True, "due", DAY))


class ProbabilityTests(unittest.TestCase):
    def test_zero_never_and_one_always(self):
        self.assertFalse(life_publish.should_attempt(settings(life_publish_probability=0), 0.0))
        self.assertTrue(life_publish.should_attempt(settings(life_publish_probability=1), 0.999))
        self.assertFalse(life_publish.should_attempt({}, 0.0))

    def test_draw_is_compared_against_the_probability(self):
        half = settings()
        self.assertTrue(life_publish.should_attempt(half, 0.49))
        self.assertFalse(life_publish.should_attempt(half, 0.5))


class KeyAndDraftTests(unittest.TestCase):
    def test_operation_key_is_stable_per_attempt_and_distinct_across_attempts(self):
        first = life_publish.operation_key("owner", DAY, NOW)
        self.assertEqual(first, life_publish.operation_key("owner", DAY, NOW))
        self.assertNotEqual(first, life_publish.operation_key("owner", DAY, NOW + timedelta(hours=1)))
        self.assertNotEqual(first, life_publish.operation_key("other", DAY, NOW))

    def test_draft_strips_fences_quotes_and_rejects_empty_or_oversized(self):
        self.assertEqual(life_publish.parse_draft('```\n“今天风很轻。”\n```'), "今天风很轻。")
        with self.assertRaises(ValueError):
            life_publish.parse_draft("   ")
        with self.assertRaises(ValueError):
            life_publish.parse_draft("字" * (life_publish.MAX_TEXT_CHARS + 1))

    def test_request_carries_profile_diary_and_style(self):
        request = life_publish.build_request(
            {"name": "星缘", "personality": "安静"}, "今天去看了海。",
            now=NOW, style="口语一点")
        self.assertIn("星缘", request["prompt"])
        self.assertIn("性格：安静", request["prompt"])
        self.assertIn("今天去看了海。", request["prompt"])
        self.assertIn("口语一点", request["prompt"])
        self.assertEqual((life_publish.TOPIC, life_publish.SCENE),
                         (QzoneTopic.LIFE_DOCUMENTARY, QzoneScene.ROLE_ACTIVITY))


def _patch_role_config(application, operator, target, group_id, owner_id, changes, *, at, key):
    snapshot = application.configuration.read(operator, target=target, group_id=group_id, observed_at=at).unwrap()
    operation = OperationRef(key, owner_id, target.authorization_scope, VersionRef(key + "-lineage", 1), key)
    return application.configuration.patch(
        operator, target=target, group_id=group_id, operation=operation,
        expected_edit_token=snapshot.edit_token, changes=changes, observed_at=at,
    ).unwrap()


def _configure_qzone_role(controller, context, at, *, enable_role=True, consent=True,
                          enabled=True, publish_enabled=True, life_publish_enabled=True,
                          probability=1.0, daily_limits_enabled=False, op_prefix="qzone-test"):
    application = controller.session.application
    operator = dashboard_operator(controller.runtime, "admin", at=at)
    repo = application.qzone.capabilities
    if repo.installation(extension_id=EXTENSION).failure is not None:
        repo.register_descriptor(package_descriptor(), registered_at=at)
        repo.acquire(
            extension_id=EXTENSION, package_id=package_descriptor().package_id,
            package_version=package_descriptor().package_version, expected_revision=0, observed_at=at,
        ).unwrap()
    selection = repo.role_selection(
        extension_id=EXTENSION, role=context.role_binding.role, bot_id=context.scope.bot_id)
    if enable_role and (selection.failure is not None or not selection.unwrap().enabled):
        revision = selection.unwrap().revision if selection.is_success else 0
        repo.set_role_enabled(
            extension_id=EXTENSION, role=context.role_binding.role, bot_id=context.scope.bot_id,
            enabled=True, expected_revision=revision, observed_at=at,
        ).unwrap()
    target_state = (CapabilityAuthorizationState.ACTIVE if consent
                    else CapabilityAuthorizationState.REVOKED)
    for capability in CAPABILITIES:
        current = repo.consent(
            extension_id=EXTENSION, capability_id=capability,
            subject=context.actor.subject, scope=context.scope,
        )
        if current.is_success:
            if current.unwrap().state is not target_state:
                repo.set_authorization(
                    extension_id=EXTENSION, capability_id=capability,
                    subject=context.actor.subject, scope=context.scope,
                    state=target_state, expected_revision=current.unwrap().revision, observed_at=at,
                ).unwrap()
        elif consent:
            repo.set_authorization(
                extension_id=EXTENSION, capability_id=capability,
                subject=context.actor.subject, scope=context.scope,
                state=CapabilityAuthorizationState.ACTIVE, expected_revision=0, observed_at=at,
            ).unwrap()
    _patch_role_config(
        application, operator, qzone_target_for(context), QZONE_GROUP, QZONE_OWNER,
        {
            f"{QZONE_GROUP}.enabled": enabled,
            f"{QZONE_GROUP}.publish_enabled": publish_enabled,
            f"{QZONE_GROUP}.daily_limits_enabled": daily_limits_enabled,
            f"{QZONE_GROUP}.life_publish_enabled": life_publish_enabled,
            f"{QZONE_GROUP}.life_publish_probability": probability,
            f"{QZONE_GROUP}.topic_auto_enabled": True,
            f"{QZONE_GROUP}.topic_require_confirmation": False,
        },
        at=at, key=f"{op_prefix}-settings",
    )


def _save_role_profile_and_diary(controller, context, at, *, name, personality, diary_body, op_prefix):
    application = controller.session.application
    operator = dashboard_operator(controller.runtime, "admin", at=at)
    _patch_role_config(
        application, operator, role_profile_target_for(context),
        ROLE_PROFILE_GROUP, ROLE_PROFILE_OWNER,
        {f"{ROLE_PROFILE_GROUP}.name": name, f"{ROLE_PROFILE_GROUP}.personality": personality},
        at=at, key=f"{op_prefix}-profile",
    )
    owner = RoleOwnerRef(context.scope.installation_id, context.scope.bot_id,
                         context.role_binding.role.subject_id)
    journal_key = JournalKey(owner, JournalKind.DIARY, local_day(at))
    journal_context = JournalContext(journal_key, context.scope, "Asia/Shanghai")
    application.role_journal.save(
        JournalDraft(journal_context, body=diary_body),
        expected_revision=0, operation_id=f"{op_prefix}-diary",
    )


class SingleHostPublisherTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_host_publishes_with_profile_and_diary_then_respects_interval(self):
        temporary = TemporaryDirectory(prefix="companion-qzone-single-")
        self.addCleanup(temporary.cleanup)
        native = staging_support.WebContext()
        native.persona = {"name": "original", "prompt": "安静温和地聊天。"}
        types = NativeAdapterTypes("4.27.2", staging_support.FakeEvent, staging_support.FakeProvider,
                                   NS, NS, staging_support.FakeReverseApi)
        transport = FakeTransport()
        with patch("astrbot_host.controller.build_qzone_transport", return_value=transport):
            controller = staging_support.AstrBotStagingController(
                native, staging_support.host_config(), Path(temporary.name),
                native_types=types, now=lambda: staging_support.NOW,
            )
            self.addAsyncCleanup(controller.terminate)
            (await controller.initialize_profile()).unwrap()
            (await controller.start()).unwrap()
            self.addAsyncCleanup(controller.stop)

        at = staging_support.NOW
        context = controller.session.profile.target
        _configure_qzone_role(controller, context, at, op_prefix="single")
        _save_role_profile_and_diary(
            controller, context, at,
            name="云栖", personality="冷静细心",
            diary_body="傍晚收拾好了书桌上的旧胶片。", op_prefix="single",
        )
        # Drop .common if anything checked controller.settings: HostSettings only exposes .provider_id.
        self.assertFalse(hasattr(controller.settings, "common"))

        publisher = QzoneLifePublisher(controller, draw=lambda: 0.0)
        draft_reply = NS(completion_text="把桌角积灰的旧胶片理了一遍，刚好窗外风也不大。")
        with patch.object(native.provider, "text_chat", AsyncMock(return_value=draft_reply)) as chat:
            first = await publisher.tick(now=at)
            second = await publisher.tick(now=at)

        self.assertEqual(first["reason"], "published", first)
        self.assertIn(first["status"], ("accepted", "done"))
        self.assertTrue(first["operation_id"])
        self.assertEqual(chat.await_count, 1)
        prompt = chat.await_args.kwargs["prompt"]
        self.assertIn("云栖", prompt)
        self.assertIn("性格：冷静细心", prompt)
        self.assertIn("傍晚收拾好了书桌上的旧胶片。", prompt)
        self.assertTrue(any(req.description == "post.publish" for req in transport.sent))
        self.assertEqual(second, {"reason": "interval"})


_FORK_MULTI_HOST_OFF = "fork: no autonomous life posts from multi-host sessions (see qzone_life_publish._target_contexts)"


class MultiHostPublisherTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def test_fork_multi_host_session_never_drafts_or_publishes_a_life_post(self):
        transport = FakeTransport()
        with patch("astrbot_host.multi_controller.build_qzone_transport", return_value=transport):
            app = await self.start_host()
        session = self.controller.session
        context, _ = await session.synchronize(session.settings.target("first"))
        at = self.clock.now()

        # Every gate is open: the single-host path would publish here.
        _configure_qzone_role(self.controller, context, at, op_prefix="fork-multi-off")
        _save_role_profile_and_diary(
            self.controller, context, at,
            name="星缘", personality="细腻克制",
            diary_body="午后去楼下买了热柠檬茶。", op_prefix="fork-multi-off",
        )

        publisher = QzoneLifePublisher(self.controller, draw=lambda: 0.0)
        draft_reply = NS(completion_text="午后下楼买了杯热柠檬茶。")
        with patch.object(self.native.provider, "text_chat", AsyncMock(return_value=draft_reply)) as chat:
            result = await publisher.tick(now=at)

        self.assertEqual(result, {"reason": "qzone_unavailable"})
        self.assertEqual(chat.await_count, 0)
        self.assertEqual(transport.sent, [])
        self.assertEqual(app.qzone.state(context).last_action_at, {})

    @unittest.skip(_FORK_MULTI_HOST_OFF)
    async def test_multi_host_resolves_context_profile_and_publishes(self):
        transport = FakeTransport()
        with patch("astrbot_host.multi_controller.build_qzone_transport", return_value=transport):
            app = await self.start_host()
        session = self.controller.session
        self.assertFalse(hasattr(session, "profile"))
        target = session.settings.target("first")
        context, _ = await session.synchronize(target)
        at = self.clock.now()

        _configure_qzone_role(self.controller, context, at, op_prefix="multi-basic")
        _save_role_profile_and_diary(
            self.controller, context, at,
            name="星缘", personality="细腻克制",
            diary_body="午后去楼下买了热柠檬茶。", op_prefix="multi-basic",
        )
        self.assertEqual(
            current_role_profile(self.controller.runtime, session, context=context)["name"],
            "星缘",
        )
        self.assertEqual(
            current_role_profile(self.controller.runtime, context)["name"],
            "星缘",
        )
        self.assertEqual(_session_provider_id(NS(settings=NS(provider_id="host-direct"))), "host-direct")

        publisher = QzoneLifePublisher(self.controller, draw=lambda: 0.0)
        draft_reply = NS(completion_text="午后下楼买了杯热柠檬茶，回来刚好赶上风凉。")
        with patch.object(self.native.provider, "text_chat", AsyncMock(return_value=draft_reply)) as chat:
            result = await publisher.tick(now=at)

        self.assertEqual(result["reason"], "published", result)
        self.assertIn(result["status"], ("accepted", "done"))
        self.assertTrue(result["operation_id"])
        self.assertEqual(chat.await_count, 1)
        prompt = chat.await_args.kwargs["prompt"]
        self.assertIn("星缘", prompt)
        self.assertIn("性格：细腻克制", prompt)
        self.assertIn("午后去楼下买了热柠檬茶。", prompt)
        self.assertTrue(any(req.description == "post.publish" for req in transport.sent))
        self.assertEqual(app.qzone.state(context).publish_count, 1)

    @unittest.skip(_FORK_MULTI_HOST_OFF)
    async def test_multi_host_prefers_consented_window_when_first_window_lacks_consent(self):
        transport = FakeTransport()
        with patch("astrbot_host.multi_controller.build_qzone_transport", return_value=transport):
            await self.start_host()
        session = self.controller.session
        first_ctx, _ = await session.synchronize(session.settings.target("first"))
        second_ctx, _ = await session.synchronize(session.settings.target("second"))
        at = self.clock.now()

        _configure_qzone_role(self.controller, first_ctx, at, consent=False, op_prefix="multi-first-unconsented")
        _configure_qzone_role(self.controller, second_ctx, at, consent=True, op_prefix="multi-second-consented")
        _save_role_profile_and_diary(
            self.controller, second_ctx, at,
            name="白露", personality="温和从容",
            diary_body="雨停之后阳台多了一只停脚的雀。", op_prefix="multi-consented",
        )

        publisher = QzoneLifePublisher(self.controller, draw=lambda: 0.0)
        draft_reply = NS(completion_text="雨刚停，阳台栏杆上落了一只很安静的雀。")
        with patch.object(self.native.provider, "text_chat", AsyncMock(return_value=draft_reply)) as chat:
            result = await publisher.tick(now=at)

        self.assertEqual(result["reason"], "published", result)
        self.assertEqual(chat.await_count, 1)
        self.assertTrue(any(req.description == "post.publish" for req in transport.sent))

    @unittest.skip(_FORK_MULTI_HOST_OFF)
    async def test_multi_host_handles_wildcard_target_and_skips_disabled_other_role(self):
        config = multi_config()
        config["multi_host"]["targets"] = [
            config["multi_host"]["targets"][0],
            {"target_id": "wild-b", "platform_id": "qq-second", "account_id": "101",
             "bot_id": "bot-b", "participant_id": "*"},
        ]
        config["multi_host"]["personas"] = [
            {"bot_id": "bot-a", "persona_id": "original", "role_id": "role-a"},
            {"bot_id": "bot-b", "persona_id": "second", "role_id": "role-b"},
        ]
        self.controller = self.make_controller(config)
        self.window_personas["qq-second:FriendMessage:300"] = "second"
        transport = FakeTransport()
        with patch("astrbot_host.multi_controller.build_qzone_transport", return_value=transport):
            await self.start_host()

        session = self.controller.session
        first_ctx, _ = await session.synchronize(session.settings.target("first"))
        wildcard_event = FakeEvent(
            NS(platform=self.native.platforms["qq-second"]),
            event_id="wild-evt-1", sender="300", login="101", text="你好",
        )
        prepared = (await self.controller.prepare_core_reply(wildcard_event)).unwrap()
        await prepared["native_source_future"]
        wildcard_ctx = prepared["context"]
        at = self.clock.now()

        _configure_qzone_role(
            self.controller, first_ctx, at,
            enabled=True, publish_enabled=True, life_publish_enabled=False, op_prefix="role-a-off",
        )
        _configure_qzone_role(
            self.controller, wildcard_ctx, at,
            enabled=True, publish_enabled=True, life_publish_enabled=True, op_prefix="role-b-on",
        )
        _save_role_profile_and_diary(
            self.controller, wildcard_ctx, at,
            name="望舒", personality="明快直接",
            diary_body="晚上散步经过河堤听见街头弹吉他。", op_prefix="role-b-diary",
        )

        publisher = QzoneLifePublisher(self.controller, draw=lambda: 0.0)
        draft_reply = NS(completion_text="晚饭后沿着河堤走了一圈，有人在路灯底下弹吉他。")
        with patch.object(self.native.provider, "text_chat", AsyncMock(return_value=draft_reply)) as chat:
            result = await publisher.tick(now=at)

        self.assertEqual(result["reason"], "published", result)
        self.assertEqual(chat.await_count, 1)
        prompt = chat.await_args.kwargs["prompt"]
        self.assertIn("望舒", prompt)
        self.assertIn("晚上散步经过河堤听见街头弹吉他。", prompt)
        self.assertTrue(any(req.description == "post.publish" for req in transport.sent))

    @unittest.skip(_FORK_MULTI_HOST_OFF)
    async def test_multi_host_respects_persona_switch_and_never_publishes_retired_role(self):
        config = multi_config()
        config["multi_host"]["personas"] = [
            {"bot_id": "bot-a", "persona_id": "original", "role_id": "role-a"},
            {"bot_id": "bot-a", "persona_id": "second", "role_id": "role-b"},
        ]
        config["multi_host"]["targets"] = [config["multi_host"]["targets"][0]]
        self.controller = self.make_controller(config)
        transport = FakeTransport()
        with patch("astrbot_host.multi_controller.build_qzone_transport", return_value=transport):
            await self.start_host()

        session = self.controller.session
        target = session.settings.target("first")
        retired_ctx, _ = await session.synchronize(target)
        at = self.clock.now()
        _configure_qzone_role(
            self.controller, retired_ctx, at,
            enabled=True, publish_enabled=True, life_publish_enabled=True, op_prefix="retired-a",
        )

        switched_scope = ScopeRef(
            retired_ctx.scope.installation_id, target.platform_id, ScopeKind.PRIVATE,
            target.participant_id, bot_id=target.bot_id, account_id=target.account_id,
            conversation_id="conv-role-b", session_id=target.participant_id,
        )
        active_role = SubjectRef(retired_ctx.scope.installation_id, SubjectKind.BOT, "role-b")
        active_ctx = session.authority.select(target, switched_scope, active_role)
        _configure_qzone_role(
            self.controller, active_ctx, at,
            enabled=True, publish_enabled=True, life_publish_enabled=False, op_prefix="active-b",
        )

        publisher = QzoneLifePublisher(self.controller, draw=lambda: 0.0)
        with patch.object(self.native.provider, "text_chat", AsyncMock()) as chat:
            result = await publisher.tick(now=at)

        self.assertEqual(result, {"reason": "life_publish_disabled"})
        self.assertEqual(chat.await_count, 0)
        self.assertEqual(transport.sent, [])

    @unittest.skip(_FORK_MULTI_HOST_OFF)
    async def test_multi_host_unauthorized_zone_does_not_spend_model_or_lock_attempt(self):
        transport = FakeTransport()
        with patch("astrbot_host.multi_controller.build_qzone_transport", return_value=transport):
            app = await self.start_host()
        session = self.controller.session
        context, _ = await session.synchronize(session.settings.target("first"))
        at = self.clock.now()

        _configure_qzone_role(self.controller, context, at, consent=False, op_prefix="multi-unauth")
        _save_role_profile_and_diary(
            self.controller, context, at,
            name="栖云", personality="安静", diary_body="今天把书架摆整齐了。", op_prefix="multi-unauth",
        )

        publisher = QzoneLifePublisher(self.controller, draw=lambda: 0.0)
        draft_reply = NS(completion_text="把书架重新理了理，总算顺眼多了。")
        with patch.object(self.native.provider, "text_chat", AsyncMock(return_value=draft_reply)) as chat:
            denied = await publisher.tick(now=at)
            self.assertEqual(denied, {"reason": "qzone_unauthorized"})
            self.assertEqual(chat.await_count, 0)
            self.assertEqual(app.qzone.state(context).last_action_at, {})

            _configure_qzone_role(self.controller, context, at, consent=True, op_prefix="multi-regrant")
            granted = await publisher.tick(now=at)
            self.assertEqual(granted["reason"], "published", granted)
            self.assertEqual(chat.await_count, 1)
            self.assertTrue(any(req.description == "post.publish" for req in transport.sent))

    @unittest.skip(_FORK_MULTI_HOST_OFF)
    async def test_multi_host_falls_back_to_persona_mapping_when_active_key_absent_and_skips_groups(self):
        config = multi_config()
        config["multi_host"]["group_targets"] = [
            {"target_id": "grp-1", "platform_id": "qq-main", "account_id": "100",
             "bot_id": "bot-a", "group_id": "9001"},
        ]
        self.controller = self.make_controller(config)
        transport = FakeTransport()
        with patch("astrbot_host.multi_controller.build_qzone_transport", return_value=transport):
            await self.start_host()
        session = self.controller.session
        context, _ = await session.synchronize(session.settings.target("first"))
        with self.controller.runtime.transaction() as connection:
            connection.execute("DELETE FROM runtime_metadata WHERE key = ?", ("host.multi.active:first",))
        at = self.clock.now()

        _configure_qzone_role(self.controller, context, at, op_prefix="multi-fallback")
        _save_role_profile_and_diary(
            self.controller, context, at,
            name="霁初", personality="稳重", diary_body="晚上整理好了相册。", op_prefix="multi-fallback",
        )
        publisher = QzoneLifePublisher(self.controller, draw=lambda: 0.0)
        draft_reply = NS(completion_text="晚间把旧相册理顺了。")
        with patch.object(self.native.provider, "text_chat", AsyncMock(return_value=draft_reply)):
            result = await publisher.tick(now=at)

        self.assertEqual(result["reason"], "published", result)
        self.assertTrue(any(req.description == "post.publish" for req in transport.sent))


if __name__ == "__main__":
    unittest.main()
