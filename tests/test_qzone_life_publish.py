"""生活说说：判定、概率、操作键与草稿边界。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
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
from companion_v2.agenda.models import Schedule
from companion_v2.adapters.astrbot.staging import NativeAdapterTypes
from companion_v2.capabilities import CapabilityAuthorizationState
from companion_v2.execution.models import TaskError, encode
from companion_v2.foundation.refs import (OperationRef, RoleOwnerRef, ScopeKind, ScopeRef,
                                          SubjectKind, SubjectRef, VersionRef)
from companion_v2.identity_policy.models import AuthorizationTier, GrantState
from companion_v2.identity_policy.repository import PersistentIdentityDirectory
from companion_v2.qzone import life_publish
from companion_v2.qzone.capability import CAPABILITIES, EXTENSION, PUBLISH_ACTION, package_descriptor
from companion_v2.qzone.configuration import (GROUP as QZONE_GROUP, OWNER as QZONE_OWNER,
                                            target_for as qzone_target_for)
from companion_v2.qzone.policy import QzoneRuntimeState, note_publish, publish_reason
from companion_v2.qzone.service import local_day, owner_key
from companion_v2.qzone.topics import QzoneScene, QzoneTopic
from companion_v2.role_profile.configuration import (GROUP as ROLE_PROFILE_GROUP,
                                                 OWNER as ROLE_PROFILE_OWNER,
                                                 target_for as role_profile_target_for)
from companion_v2.role_work.journal import JournalContext, JournalDraft, JournalKey, JournalKind
from companion_v2.role_work.models import RoleActivity, RoleActivityState


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
        self.assertNotIn("最近已经发过的说说", request["prompt"])
        self.assertEqual((life_publish.TOPIC, life_publish.SCENE),
                         (QzoneTopic.LIFE_DOCUMENTARY, QzoneScene.ROLE_ACTIVITY))

    def test_request_includes_bounded_recent_publish_texts(self):
        recent = tuple(f"第{idx}条旧说说：" + "日常片段" * 40 for idx in range(7))
        request = life_publish.build_request(
            {"name": "星缘"}, "今天整理了书桌。", now=NOW, recent_publish_texts=recent)
        prompt = request["prompt"]
        self.assertIn("最近已经发过的说说", prompt)
        # Only the latest MAX_RECENT_POSTS entries are included, each bounded in length.
        self.assertNotIn("第0条旧说说", prompt)
        self.assertNotIn("第1条旧说说", prompt)
        for idx in range(2, 7):
            self.assertIn(f"第{idx}条旧说说", prompt)
        bullet_lines = [line for line in prompt.splitlines() if line.startswith("- ")]
        self.assertEqual(len(bullet_lines), life_publish.MAX_RECENT_POSTS)
        for line in bullet_lines:
            self.assertLessEqual(len(line) - 2, life_publish.MAX_RECENT_POST_CHARS)

    def test_request_and_parse_plan_support_activities_schedules_album_and_generation(self):
        request = life_publish.build_request(
            {"name": "星缘"},
            [{"local_date": "2026-11-09", "body": "雨停后沿着河边散步。"}],
            now=NOW,
            activities=[{"state": "completed", "description": "傍晚散步", "checkpoint": "走到南桥"}],
            schedules=[{"state": "accepted", "start": "2026-11-09T10:00", "end": "2026-11-09T11:00",
                        "title": "街角买花", "detail": "路过花店"}],
            album_candidates=[{"image_id": "generated-image:inv-1:0", "created_at": "2026-11-09T09:00:00+00:00",
                               "summary": "清晨窗边的茶杯"}],
            can_generate_image=True,
        )
        self.assertIn("傍晚散步", request["prompt"])
        self.assertIn("街角买花", request["prompt"])
        self.assertIn("generated-image:inv-1:0", request["prompt"])
        self.assertIn("image_source", request["prompt"])

        gen_plan = life_publish.parse_plan(
            '```json\n{"event_summary":"雨后散步","text":"雨停后沿着河边走了一段，晚风把树叶吹得很轻。",'
            '"image_source":"generate","album_image_id":"","image_prompt":"雨后湿润的河边小路与路灯","include_character":true}\n```',
            allowed_album_ids=["generated-image:inv-1:0"],
            allow_generate=True,
        )
        self.assertEqual(gen_plan.image_source, "generate")
        self.assertTrue(gen_plan.include_character)
        self.assertIn("河边小路", gen_plan.image_prompt)

        album_plan = life_publish.parse_plan(
            '{"event_summary":"清晨喝茶","text":"早上靠窗泡了杯热茶，晒了会儿太阳。",'
            '"image_source":"album","album_image_id":"generated-image:inv-1:0","image_prompt":""}',
            allowed_album_ids=["generated-image:inv-1:0"],
            allow_generate=True,
        )
        self.assertEqual(album_plan.image_source, "album")
        self.assertEqual(album_plan.album_image_id, "generated-image:inv-1:0")

        forged_album = life_publish.parse_plan(
            '{"text":"雨停后沿着河边走了一段，晚风把树叶吹得很轻。",'
            '"image_source":"album","album_image_id":"forged-id","image_prompt":"雨后街景"}',
            allowed_album_ids=["generated-image:inv-1:0"],
            allow_generate=True,
        )
        self.assertEqual(forged_album.image_source, "generate")

        fallback_plain = life_publish.parse_plan(
            "只是普通的纯文字说说。",
            allowed_album_ids=["generated-image:inv-1:0"],
            allow_generate=True,
        )
        self.assertEqual(fallback_plain.text, "只是普通的纯文字说说。")
        self.assertEqual(fallback_plain.image_source, "none")


class SimilarityPolicyTests(unittest.TestCase):
    def _policy_settings(self):
        return {
            "enabled": True,
            "publish_enabled": True,
            "daily_limits_enabled": False,
            "action_min_interval_minutes": 30,
        }

    def test_normal_life_posts_sharing_common_phrases_are_not_blocked(self):
        prev = "午后下楼买了杯热柠檬茶，回来的时候风有点凉，顺手把阳台上的衣服收进来了，晚上打算早点睡。"
        curr = "晚上整理书桌的时候翻出几张旧胶片，窗外吹进来的风有点凉，听了一会儿歌整个人松快了不少。"
        st = note_publish(state(), text=prev, at=NOW - timedelta(hours=2), day=DAY)
        self.assertEqual(publish_reason(st, self._policy_settings(), text=curr, at=NOW, day=DAY), "")

        # Even multiple scattered common 3-grams with low Jaccard must pass.
        prev2 = "今天下午去菜市场买了几根玉米和新鲜番茄，回家之后慢悠悠炖了一锅汤，味道比想象中还要好，窗外天色也慢慢暗下来了。"
        curr2 = "今天下午在阳台给绿萝换了新花盆，回家之后顺手擦了书架上的灰尘，顺便翻开一本旧画册，心情比想象中还要放松许多。"
        st2 = note_publish(state(), text=prev2, at=NOW - timedelta(hours=2), day=DAY)
        self.assertEqual(publish_reason(st2, self._policy_settings(), text=curr2, at=NOW, day=DAY), "")

    def test_genuine_near_duplicate_and_short_post_variants_are_still_blocked(self):
        prev = "午后下楼买了杯热柠檬茶，回来的时候风有点凉，顺手把阳台上的衣服都收进来了，准备早点休息。"
        near_dup = "午后下楼买了杯热柠檬茶，回来的时候风挺凉的，顺手把阳台上的衣服收进来了，打算早点休息。"
        st = note_publish(state(), text=prev, at=NOW - timedelta(hours=2), day=DAY)
        self.assertIn("太像", publish_reason(st, self._policy_settings(), text=near_dup, at=NOW, day=DAY))

        short_st = note_publish(state(), text="今天很好", at=NOW - timedelta(hours=2), day=DAY)
        self.assertIn("重复", publish_reason(short_st, self._policy_settings(), text="今天很好", at=NOW, day=DAY))
        self.assertIn("太像", publish_reason(short_st, self._policy_settings(), text="今天很好！", at=NOW, day=DAY))
        # A short old post must not block a much longer brand-new life post.
        long_new = "今天很好，顺便去楼下买了热柠檬茶，又把书架上的旧书和胶片理了一遍，晚风挺轻的。"
        self.assertEqual(publish_reason(short_st, self._policy_settings(), text=long_new, at=NOW, day=DAY), "")

        short_punct = note_publish(state(), text="今天下班路上买了花。", at=NOW - timedelta(hours=2), day=DAY)
        self.assertIn("太像", publish_reason(short_punct, self._policy_settings(),
                                             text="今天下班路上买了花！", at=NOW, day=DAY))


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
    identities = PersistentIdentityDirectory(controller.runtime)
    binding = next((item for item in identities.snapshot().bindings
                    if item.binding_id == context.actor.binding_id), None)
    if consent and binding is not None:
        for capability in ("extension.tool.invoke", *CAPABILITIES):
            if identities.authorize(context.actor, scope=context.scope,
                                    capability=capability, observed_at=at).is_success:
                continue
            identities.issue_grant(
                grant_id=f"qzone-test:{op_prefix}:{identities.revision}:{capability}",
                subject=context.actor.subject, scope=context.scope, capability=capability,
                tier=AuthorizationTier.STANDARD, valid_from=at, expires_at=binding.expires_at,
                expected_revision=identities.revision,
            )
    elif not consent:
        owned = {*CAPABILITIES, "extension.tool.invoke"}
        for grant in identities.snapshot().grants:
            if (grant.subject == context.actor.subject and grant.scope == context.scope
                    and grant.capability in owned and grant.state is GrantState.ACTIVE):
                identities.revoke_grant(grant.grant_id, revoked_at=at, expected_revision=identities.revision)
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


class MultiHostPublisherTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
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

            previous_scope = replace(context.scope, conversation_id="previous-conversation")
            app.qzone.capabilities.set_authorization(
                extension_id=EXTENSION, capability_id=PUBLISH_ACTION,
                subject=context.actor.subject, scope=previous_scope,
                state=CapabilityAuthorizationState.ACTIVE, expected_revision=0, observed_at=at,
            ).unwrap()
            with self.assertLogs("astrbot_host.qzone_life_publish", level="INFO") as logs:
                rotated = await publisher.tick(now=at)
            self.assertEqual(rotated, {"reason": "qzone_conversation_changed"})
            self.assertIn("qzone_conversation_changed", logs.output[-1])
            self.assertEqual(chat.await_count, 0)
            self.assertEqual(transport.sent, [])
            self.assertEqual(app.qzone.state(context).last_action_at, {})

            _configure_qzone_role(self.controller, context, at, consent=True, op_prefix="multi-regrant")
            granted = await publisher.tick(now=at)
            self.assertEqual(granted["reason"], "published", granted)
            self.assertEqual(chat.await_count, 1)
            self.assertTrue(any(req.description == "post.publish" for req in transport.sent))

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

    async def test_multi_host_scopes_diary_to_selected_window_and_never_leaks_other_window(self):
        config = multi_config()
        config["multi_host"]["targets"] = [
            config["multi_host"]["targets"][0],
            {"target_id": "second-same-bot", "platform_id": "qq-main", "account_id": "100",
             "bot_id": "bot-a", "participant_id": "201"},
        ]
        config["multi_host"]["personas"] = [
            {"bot_id": "bot-a", "persona_id": "original", "role_id": "role-a"},
        ]
        self.controller = self.make_controller(config)
        transport = FakeTransport()
        with patch("astrbot_host.multi_controller.build_qzone_transport", return_value=transport):
            app = await self.start_host()
        session = self.controller.session
        window_a, _ = await session.synchronize(session.settings.target("first"))
        window_b, _ = await session.synchronize(session.settings.target("second-same-bot"))
        at = self.clock.now()
        yesterday = at - timedelta(days=1)

        # Both windows share the exact same role owner; window A is consented, window B is not.
        owner_a = RoleOwnerRef(window_a.scope.installation_id, window_a.scope.bot_id,
                               window_a.role_binding.role.subject_id)
        owner_b = RoleOwnerRef(window_b.scope.installation_id, window_b.scope.bot_id,
                               window_b.role_binding.role.subject_id)
        self.assertEqual(owner_a, owner_b)
        self.assertNotEqual(window_a.scope, window_b.scope)
        _configure_qzone_role(self.controller, window_a, at, consent=True, op_prefix="iso-a")
        _configure_qzone_role(self.controller, window_b, at, consent=False, op_prefix="iso-b")
        # Window A wrote an older diary yesterday; window B wrote a NEWER diary today.
        key_a = JournalKey(owner_a, JournalKind.DIARY, local_day(yesterday))
        app.role_journal.save(
            JournalDraft(JournalContext(key_a, window_a.scope, "Asia/Shanghai"),
                         body="窗口甲专属日记：午后修好了书桌上的旧台灯。"),
            expected_revision=0, operation_id="iso-diary-a", observed_at=yesterday,
        )
        key_b = JournalKey(owner_a, JournalKind.DIARY, local_day(at))
        app.role_journal.save(
            JournalDraft(JournalContext(key_b, window_b.scope, "Asia/Shanghai"),
                         body="窗口乙私密日记：昨晚和好友去看了午夜场电影。"),
            expected_revision=0, operation_id="iso-diary-b", observed_at=at,
        )

        publisher = QzoneLifePublisher(self.controller, draw=lambda: 0.0)
        draft_reply = NS(completion_text="午后把旧台灯修好了，重新亮起来的感觉还挺安心。")
        with patch.object(self.native.provider, "text_chat", AsyncMock(return_value=draft_reply)) as chat:
            result = await publisher.tick(now=at)

        self.assertEqual(result["reason"], "published", result)
        self.assertEqual(chat.await_count, 1)
        prompt = chat.await_args.kwargs["prompt"]
        self.assertIn("窗口甲专属日记：午后修好了书桌上的旧台灯。", prompt)
        self.assertNotIn("窗口乙私密日记", prompt)

        # Even if window A has no diary of its own at all, it must never read window B's diary.
        app.role_journal.delete(
            JournalContext(key_a, window_a.scope, "Asia/Shanghai"),
            expected_revision=1, operation_id="iso-diary-a-del", observed_at=at,
        )
        self.clock.advance(timedelta(hours=8))
        later = self.clock.now()
        with patch.object(self.native.provider, "text_chat", AsyncMock(
                return_value=NS(completion_text="晚上泡了杯热茶发了会儿呆，屋里很安静。"))) as chat2:
            result2 = await publisher.tick(now=later)
        self.assertEqual(result2["reason"], "published", result2)
        prompt2 = chat2.await_args.kwargs["prompt"]
        self.assertNotIn("窗口乙私密日记", prompt2)
        # Recent published post snippet is also injected to guide diversity.
        self.assertIn("最近已经发过的说说", prompt2)
        self.assertIn("旧台灯修好了", prompt2)

    async def test_revoking_role_b_consent_during_role_a_draft_prevents_role_b_draft_and_llm_send(self):
        config = multi_config()
        config["multi_host"]["targets"] = [
            config["multi_host"]["targets"][0],
            {"target_id": "second-bot-target", "platform_id": "qq-second", "account_id": "101",
             "bot_id": "bot-b", "participant_id": "300"},
        ]
        config["multi_host"]["personas"] = [
            {"bot_id": "bot-a", "persona_id": "original", "role_id": "role-a"},
            {"bot_id": "bot-b", "persona_id": "second", "role_id": "role-b"},
        ]
        self.controller = self.make_controller(config)
        self.window_personas["qq-second:FriendMessage:300"] = "second"
        transport = FakeTransport()
        with patch("astrbot_host.multi_controller.build_qzone_transport", return_value=transport):
            app = await self.start_host()
        session = self.controller.session
        ctx_a, _ = await session.synchronize(session.settings.target("first"))
        ctx_b, _ = await session.synchronize(session.settings.target("second-bot-target"))
        at = self.clock.now()

        _configure_qzone_role(self.controller, ctx_a, at, consent=True, op_prefix="race-a")
        _configure_qzone_role(self.controller, ctx_b, at, consent=True, op_prefix="race-b")
        _save_role_profile_and_diary(
            self.controller, ctx_a, at,
            name="角色甲", personality="沉静",
            diary_body="角色甲日记：早上煮了咖啡。", op_prefix="race-a-profile",
        )
        _save_role_profile_and_diary(
            self.controller, ctx_b, at,
            name="角色乙私密名", personality="敏感",
            diary_body="角色乙绝密日记：绝不可发给模型。", op_prefix="race-b-profile",
        )

        prompts_seen = []

        async def draft_and_revoke_b(*, prompt, system_prompt, contexts, max_tokens):
            prompts_seen.append(prompt)
            # While role A awaits _draft(), operator revokes role B's QZone consent.
            _configure_qzone_role(self.controller, ctx_b, at, consent=False, op_prefix="race-b-revoke")
            return NS(completion_text="早上慢悠悠煮了一壶手冲咖啡，屋里全是烘豆的香气。")

        publisher = QzoneLifePublisher(self.controller, draw=lambda: 0.0)
        with patch.object(self.native.provider, "text_chat", AsyncMock(side_effect=draft_and_revoke_b)) as chat:
            result = await publisher.tick(now=at)

        self.assertEqual(result["reason"], "published", result)
        self.assertEqual(chat.await_count, 1)
        self.assertEqual(len(prompts_seen), 1)
        self.assertIn("角色甲日记", prompts_seen[0])
        self.assertNotIn("角色乙绝密日记", prompts_seen[0])
        self.assertNotIn("角色乙私密名", prompts_seen[0])
        # Role B's state must remain untouched (no attempt lock written, no publish).
        self.assertEqual(app.qzone.state(ctx_b).last_action_at, {})
        self.assertEqual(app.qzone.state(ctx_b).publish_count, 0)
        publish_reqs = [req for req in transport.sent if req.description == "post.publish"]
        self.assertEqual(len(publish_reqs), 1)

    async def test_mid_draft_consent_revocation_aborts_before_publish(self):
        transport = FakeTransport()
        with patch("astrbot_host.multi_controller.build_qzone_transport", return_value=transport):
            app = await self.start_host()
        session = self.controller.session
        ctx_a, _ = await session.synchronize(session.settings.target("first"))
        at = self.clock.now()
        _configure_qzone_role(self.controller, ctx_a, at, consent=True, op_prefix="mid-revoke-init")

        async def revoke_during_draft(*, prompt, system_prompt, contexts, max_tokens):
            _configure_qzone_role(self.controller, ctx_a, at, consent=False, op_prefix="mid-revoke-now")
            return NS(completion_text="窗外下起了毛毛雨，刚好泡杯茶看看书。")

        publisher = QzoneLifePublisher(self.controller, draw=lambda: 0.0)
        with patch.object(self.native.provider, "text_chat", AsyncMock(side_effect=revoke_during_draft)):
            revoked = await publisher.tick(now=at)
        self.assertEqual(revoked, {"reason": "qzone_unauthorized"})
        self.assertEqual(transport.sent, [])
        self.assertEqual(app.qzone.state(ctx_a).publish_count, 0)

    async def test_mid_draft_persona_switch_aborts_before_publish(self):
        config = multi_config()
        config["multi_host"]["personas"] = [
            {"bot_id": "bot-a", "persona_id": "original", "role_id": "role-a"},
            {"bot_id": "bot-a", "persona_id": "second", "role_id": "role-b"},
        ]
        config["multi_host"]["targets"] = [config["multi_host"]["targets"][0]]
        self.controller = self.make_controller(config)
        transport = FakeTransport()
        with patch("astrbot_host.multi_controller.build_qzone_transport", return_value=transport):
            app = await self.start_host()
        session = self.controller.session
        target = session.settings.target("first")
        ctx_a, _ = await session.synchronize(target)
        at = self.clock.now()
        _configure_qzone_role(self.controller, ctx_a, at, consent=True, op_prefix="mid-switch-init")

        async def switch_during_draft(*, prompt, system_prompt, contexts, max_tokens):
            switched_scope = ScopeRef(
                ctx_a.scope.installation_id, target.platform_id, ScopeKind.PRIVATE,
                target.participant_id, bot_id=target.bot_id, account_id=target.account_id,
                conversation_id="conv-role-b", session_id=target.participant_id,
            )
            active_role = SubjectRef(ctx_a.scope.installation_id, SubjectKind.BOT, "role-b")
            session.authority.select(target, switched_scope, active_role)
            return NS(completion_text="傍晚散步回来顺路买了盒草莓。")

        publisher = QzoneLifePublisher(self.controller, draw=lambda: 0.0)
        with patch.object(self.native.provider, "text_chat", AsyncMock(side_effect=switch_during_draft)):
            retired = await publisher.tick(now=at)
        self.assertEqual(retired, {"reason": "qzone_unavailable"})
        self.assertEqual(transport.sent, [])
        self.assertEqual(app.qzone.state(ctx_a).publish_count, 0)

    async def test_action_blocked_logs_known_duplicate_reason_and_defers_to_normal_interval(self):
        transport = FakeTransport()
        with patch("astrbot_host.multi_controller.build_qzone_transport", return_value=transport):
            app = await self.start_host()
        session = self.controller.session
        context, _ = await session.synchronize(session.settings.target("first"))
        at = self.clock.now()
        _configure_qzone_role(self.controller, context, at, op_prefix="block-loop")

        # Seed a recent post so drafting the exact/near-duplicate text triggers real qzone_action_blocked.
        duplicate_text = "午后下楼买了杯热柠檬茶，回来的时候风有点凉，顺手把阳台上的衣服收进来了。"
        seeded = note_publish(app.qzone.state(context), text=duplicate_text,
                              at=at - timedelta(hours=8), day=local_day(at - timedelta(hours=8)))
        app.qzone.store.write(owner_key(context), seeded, at=at - timedelta(hours=8))

        publisher = QzoneLifePublisher(self.controller, draw=lambda: 0.0)
        with patch.object(self.native.provider, "text_chat",
                          AsyncMock(return_value=NS(completion_text=duplicate_text))) as chat:
            with self.assertLogs("astrbot_host.qzone_life_publish", level="WARNING") as captured:
                blocked = await publisher.tick(now=at)
            self.assertEqual(blocked, {"reason": "publish_failed", "code": "qzone_action_blocked"})
            self.assertEqual(chat.await_count, 1)
            log_text = "\n".join(captured.output)
            self.assertIn("code=qzone_action_blocked", log_text)
            self.assertIn("reason=这条说说和刚发过的重复", log_text)

            # 30 minutes later must stay in interval (NOT loop every 30 min via RETRY_KEY).
            after_30m = await publisher.tick(now=at + timedelta(minutes=30))
            self.assertEqual(after_30m, {"reason": "interval"})
            self.assertEqual(chat.await_count, 1)

        # Unknown TaskError text can contain multi-part credentials.
        secret_err = TaskError("qzone_action_blocked", "Authorization: Bearer test-token Cookie: a=test-cookie")
        later_6h = at + timedelta(hours=6)
        with patch.object(self.native.provider, "text_chat",
                          AsyncMock(return_value=NS(completion_text="全新一条毫不相关的傍晚散步记录。"))), \
             patch.object(app.qzone, "publish", AsyncMock(side_effect=secret_err)):
            with self.assertLogs("astrbot_host.qzone_life_publish", level="WARNING") as captured2:
                blocked2 = await publisher.tick(now=later_6h)
        self.assertEqual(blocked2, {"reason": "publish_failed", "code": "qzone_action_blocked"})
        log_text2 = "\n".join(captured2.output)
        self.assertIn("code=qzone_action_blocked", log_text2)
        self.assertNotIn("reason=", log_text2)
        self.assertNotIn("test-token", log_text2)
        self.assertNotIn("test-cookie", log_text2)
        self.assertNotIn(life_publish.RETRY_KEY, app.qzone.state(context).last_action_at)

        # Transient qzone_operation_failed still uses short retry_wait (30m) and retries cleanly.
        later_12h = at + timedelta(hours=12)
        with patch.object(self.native.provider, "text_chat",
                          AsyncMock(return_value=NS(completion_text="雨后去巷口书店翻了半小时画册，买到一张明信片。"))), \
             patch.object(app.qzone, "publish",
                          AsyncMock(side_effect=TaskError("qzone_operation_failed", "上游临时抖动"))):
            transient = await publisher.tick(now=later_12h)
        self.assertEqual(transient, {"reason": "publish_failed", "code": "qzone_operation_failed"})
        self.assertIn(life_publish.RETRY_KEY, app.qzone.state(context).last_action_at)
        self.assertEqual(await publisher.tick(now=later_12h + timedelta(minutes=10)), {"reason": "retry_wait"})

    async def test_multi_host_life_publish_reads_activities_schedules_and_generates_scene_image(self):
        import json
        from companion_v2.foundation.refs import SourceRef
        from companion_v2.qzone.images import PublishImage
        from companion_v2.sources.models import SourceRecord
        from tests.test_qzone_images import ImageTransport, picture

        scene_text = "雨停后沿着老街散步，石板路上还映着暖黄的路灯光。"
        transport = ImageTransport(photos=1, text=scene_text, psc=True)
        with patch("astrbot_host.multi_controller.build_qzone_transport", return_value=transport):
            app = await self.start_host()
        session = self.controller.session
        target = session.settings.target("first")
        context, _ = await session.synchronize(target)
        at = self.clock.now()
        _configure_qzone_role(self.controller, context, at, op_prefix="life-img-gen")
        _save_role_profile_and_diary(
            self.controller, context, at,
            name="星缘", personality="安静温和",
            diary_body="傍晚雨停后出门走了走。", op_prefix="life-img-gen",
        )
        owner = RoleOwnerRef(context.scope.installation_id, context.scope.bot_id,
                             context.role_binding.role.subject_id)
        app.role_life.add_activity(
            RoleActivity(
                activity_id="act-walk-1",
                role_id=context.role_binding.role.subject_id,
                scope=context.scope,
                started_at=at - timedelta(minutes=30),
                updated_at=at - timedelta(minutes=20),
                state=RoleActivityState.COMPLETED,
                description="雨停后出门散步",
                checkpoint="走过南街口",
            )
        )
        src_ref = SourceRef("cal-src-1", "conversation.user", context.scope, VersionRef("cal-src-1", 1), at)
        app.messages.sources.publish(
            SourceRecord(src_ref, context.audience, "h" * 64, "傍晚散步计划"),
            observed_at=at,
        ).unwrap()
        app.calendar.command(
            context, identifier="plan-walk-1", action="create", expected_revision=0,
            operation_key="cal-plan-1",
            data={"kind": "role_plan", "title": "傍晚河边散步", "detail": "雨停了去吹吹风",
                  "schedule": json.loads(encode(Schedule(at - timedelta(hours=1), "Asia/Shanghai"))),
                  "duration_minutes": 45, "presence": "散步中"},
            sources=(src_ref,),
        )
        app.calendar.command(
            context, identifier="user-private-1", action="create", expected_revision=0,
            operation_key="cal-user-1",
            data={"kind": "user_report", "title": "私密用户医院复查", "detail": "绝不可进公开说说",
                  "schedule": json.loads(encode(Schedule(at - timedelta(hours=1), "Asia/Shanghai"))),
                  "duration_minutes": 30, "presence": ""},
            sources=(src_ref,),
        )

        generated_img = picture("blue")

        async def _fake_generate_role_image(run, prompt, identity, *, invocation_id, call_id, on_dispatch=None):
            run.begin_effect()
            return NS(
                state="succeeded", effect="committed", operation_id="life-img-op-1",
                receipt_ref="life-img-rcpt-1",
                images=(NS(content=generated_img.content,
                           descriptor=NS(mime_type=generated_img.mime_type)),),
            )

        fake_port = NS(
            role_image_route=lambda ctx, identity=None: NS(descriptor=NS(tool=NS(action="image.generate"))),
            generate_role_image=AsyncMock(side_effect=_fake_generate_role_image),
        )
        app.qzone_life_images.port = fake_port

        reply_json = json.dumps({
            "event_summary": "雨停后出门散步",
            "text": scene_text,
            "image_source": "generate",
            "album_image_id": "",
            "image_prompt": "傍晚雨停后的安静老街，湿润石板路倒映暖黄色路灯",
            "include_character": False,
        }, ensure_ascii=False)
        publisher = QzoneLifePublisher(self.controller, draw=lambda: 0.0)
        with patch.object(self.native.provider, "text_chat", AsyncMock(return_value=NS(completion_text=reply_json))) as chat:
            result = await publisher.tick(now=at)

        self.assertEqual(result["reason"], "published", result)
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["image_count"], 1)
        prompt = chat.await_args.kwargs["prompt"]
        self.assertIn("雨停后出门散步", prompt)
        self.assertIn("傍晚河边散步", prompt)
        self.assertNotIn("私密用户医院复查", prompt)
        fake_port.generate_role_image.assert_awaited_once()
        self.assertEqual([req.description for req in transport.sent],
                         ["image.upload", "post.publish", "feed.list"])
        self.assertEqual(app.messages.image_quota._used(context, "proactive", at), 1)

    async def test_multi_host_life_publish_selects_album_image_and_degrades_on_gen_failure(self):
        import json
        from companion_v2.references.daily_outfit import PREFIX as OUTFIT_PREFIX, daily_outfit_image_id
        from companion_v2.storage.image_payloads import store_image
        from tests.test_qzone_images import ImageTransport, picture

        outfit_img = picture("green")
        caption = "早上换上浅绿针织衫出门买了花，阳光落在窗边。"
        transport = ImageTransport(photos=1, text=caption, psc=True)
        with patch("astrbot_host.multi_controller.build_qzone_transport", return_value=transport):
            app = await self.start_host()
        session = self.controller.session
        context, _ = await session.synchronize(session.settings.target("first"))
        at = self.clock.now()
        _configure_qzone_role(self.controller, context, at, op_prefix="life-album")
        owner = app.references.owner(context)
        marker = store_image(app.runtime.data_root, outfit_img.content, mime_type="image/png").decode("ascii")
        outfit_id = daily_outfit_image_id(owner, "2026-11-09", outfit_img.content_hash)
        with self.controller.runtime.transaction() as conn:
            conn.execute(
                "INSERT INTO runtime_metadata(key, value) VALUES (?, ?)",
                (
                    f"{OUTFIT_PREFIX}{owner}:2026-11-09",
                    json.dumps({
                        "task_id": "outfit-life-1",
                        "photo": {
                            "marker": marker,
                            "content_hash": outfit_img.content_hash,
                            "created_at": at.isoformat(),
                            "mime_type": "image/png",
                            "width": outfit_img.width,
                            "height": outfit_img.height,
                            "size_bytes": len(outfit_img.content),
                            "activities": ["清晨买花"],
                        },
                    }),
                ),
            )

        publisher = QzoneLifePublisher(self.controller, draw=lambda: 0.0)
        album_json = json.dumps({
            "event_summary": "清晨买花",
            "text": caption,
            "image_source": "album",
            "album_image_id": outfit_id,
            "image_prompt": "",
        }, ensure_ascii=False)
        with patch.object(self.native.provider, "text_chat", AsyncMock(return_value=NS(completion_text=album_json))) as chat:
            res_album = await publisher.tick(now=at)
        self.assertEqual(res_album["reason"], "published", res_album)
        self.assertEqual(res_album["image_count"], 1)
        self.assertIn(outfit_id, chat.await_args.kwargs["prompt"])
        self.assertEqual([req.description for req in transport.sent], ["image.upload", "post.publish", "feed.list"])

        # When generation fails, life publish degrades cleanly to text-only.
        self.clock.advance(timedelta(hours=8))
        later = self.clock.now()
        transport.sent.clear()
        fallback_caption = "夜里窗外起风了，泡了杯热牛奶翻完最后几页书。"
        transport.body = fallback_caption
        transport.photos = 0
        app.qzone_life_images.port = NS(
            role_image_route=lambda ctx, identity=None: NS(descriptor=NS(tool=NS(action="image.generate"))),
            generate_role_image=AsyncMock(return_value=NS(
                state="failed", effect="not_committed", receipt_ref="fail-rcpt", images=(),
            )),
        )
        gen_fail_json = json.dumps({
            "event_summary": "夜里看书",
            "text": fallback_caption,
            "image_source": "generate",
            "album_image_id": "",
            "image_prompt": "书桌上的热牛奶和摊开的书",
        }, ensure_ascii=False)
        with patch.object(self.native.provider, "text_chat", AsyncMock(return_value=NS(completion_text=gen_fail_json))):
            res_text = await publisher.tick(now=later)
        self.assertEqual(res_text["reason"], "published", res_text)
        self.assertNotIn("image_count", res_text)
        self.assertEqual([req.description for req in transport.sent], ["post.publish", "feed.list"])

    async def test_mid_draft_consent_revocation_skips_image_generation(self):
        transport = FakeTransport()
        with patch("astrbot_host.multi_controller.build_qzone_transport", return_value=transport):
            app = await self.start_host()
        session = self.controller.session
        ctx_a, _ = await session.synchronize(session.settings.target("first"))
        at = self.clock.now()
        _configure_qzone_role(self.controller, ctx_a, at, consent=True, op_prefix="mid-revoke-img-init")

        fake_port = NS(
            role_image_route=lambda ctx, identity=None: NS(descriptor=NS(tool=NS(action="image.generate"))),
            generate_role_image=AsyncMock(),
        )
        app.qzone_life_images.port = fake_port

        async def revoke_during_draft(*, prompt, system_prompt, contexts, max_tokens):
            _configure_qzone_role(self.controller, ctx_a, at, consent=False, op_prefix="mid-revoke-img-now")
            return NS(completion_text=json.dumps({
                "event_summary": "雨后散步",
                "text": "雨刚停下来，沿着河边走了几步。",
                "image_source": "generate",
                "album_image_id": "",
                "image_prompt": "雨后河边",
            }, ensure_ascii=False))

        publisher = QzoneLifePublisher(self.controller, draw=lambda: 0.0)
        with patch.object(self.native.provider, "text_chat", AsyncMock(side_effect=revoke_during_draft)):
            revoked = await publisher.tick(now=at)
        self.assertEqual(revoked, {"reason": "qzone_unauthorized"})
        fake_port.generate_role_image.assert_not_awaited()
        self.assertEqual(transport.sent, [])
        self.assertEqual(app.qzone.state(ctx_a).publish_count, 0)


if __name__ == "__main__":
    unittest.main()
