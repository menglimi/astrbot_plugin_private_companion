"""Core reply admission remains outside V2 batch generation and delivery."""

from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from companion_v2.conversation.foreground import ContextContribution, ContributionState
from companion_v2.foundation.outcome import Outcome
from tests.group_host_support import GroupFixture, group_config
from tests.multi_host_support import MultiFixture
from tests.native_adapter_support import FakeEvent


class PrivateCoreReplyAdmissionTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = await self.start_host()

    async def test_prepare_and_authorize_do_not_start_v2_generation_or_delivery(self):
        event = self.event(identifier="core-reply-private")
        target = self.controller.target_for_event(event)

        prepared_result = await self.controller.prepare_core_reply(event, target)
        self.assertTrue(prepared_result.is_success, prepared_result.failure)
        prepared = prepared_result.unwrap()
        self.assertEqual(prepared["channel"], "private")
        self.assertEqual(prepared["target_id"], "first")
        self.assertEqual(prepared["event_id"], "core-reply-private")
        self.assertIn("context", prepared)
        self.assertIn("instruction", prepared)
        self.assertIn("prompt_instruction", prepared)
        self.assertTrue(prepared["presentation_settings"]["content_cleanup_enabled"])

        authorized = await self.controller.authorize_core_reply(event, target, prepared)
        self.assertTrue(authorized.is_success, authorized.failure)
        self.assertEqual(self.app.runtime.read_all("SELECT batch_id FROM conversation_batches", ()), ())
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assertEqual(self.native.platforms["qq-main"].client._api._wsr_api.calls, [])

    async def test_core_reply_uses_v2_window_persona_mapping_without_native_persona_queries(self):
        event = self.event(identifier="core-reply-single-persona-resolution")
        before_prepare = self.native.role_lookups

        prepared = await self.controller.prepare_core_reply(event)

        self.assertTrue(prepared.is_success, prepared.failure)
        self.assertEqual(self.native.role_lookups - before_prepare, 0)
        before_authorize = self.native.role_lookups

        authorized = await self.controller.authorize_core_reply(event, prepared.unwrap())

        self.assertTrue(authorized.is_success, authorized.failure)
        self.assertEqual(self.native.role_lookups - before_authorize, 0)

    async def test_role_change_between_admission_and_send_is_rejected(self):
        event = self.event(identifier="core-reply-role-change")
        prepared = (await self.controller.prepare_core_reply(event)).unwrap()
        self.controller.session_topology.update_persona_selection_snapshot(
            event.unified_msg_origin, "100", "bot-a", "second", "role-b", "conversation-a")

        authorized = await self.controller.authorize_core_reply(event, prepared)

        self.assertFalse(authorized.is_success)
        self.assertIn(authorized.failure.code, {
            "role_binding_missing", "host_persona_changed", "core_reply_instruction_changed",
            "core_reply_context_changed",
        })

    async def test_first_native_conversation_is_created_before_core_build_and_authorization(self):
        from astrbot.core.astr_main_agent import _get_session_conv

        self.native.conversations["qq-main"] = None
        manager = self.native.conversation_manager

        async def create(umo, platform_id):
            self.native.conversations[platform_id] = "core-first-conversation"
            return "core-first-conversation"

        async def conversation(umo, cid, *, create_if_not_exists=True):
            return NS(cid=cid, persona_id="original", history="[]")

        manager.new_conversation = AsyncMock(side_effect=create)
        manager.get_conversation = conversation
        event = self.event(identifier="core-first-native-window")

        prepared = (await self.controller.prepare_core_reply(event)).unwrap()
        core_conversation = await _get_session_conv(event, self.native)
        authorized = await self.controller.authorize_core_reply(event, prepared)

        manager.new_conversation.assert_awaited_once_with(event.unified_msg_origin, "qq-main")
        self.assertEqual(core_conversation.cid, "core-first-conversation")
        self.assertIn("core-first-conversation", prepared["context"].scope.conversation_id)
        self.assertNotIn("unopened-", prepared["context"].scope.conversation_id)
        self.assertTrue(authorized.is_success, authorized.failure)
        self.assertEqual(self.app.runtime.read_all("SELECT batch_id FROM conversation_batches", ()), ())

    async def test_native_conversation_reset_after_preparation_remains_rejected(self):
        event = self.event(identifier="core-native-reset")
        prepared = (await self.controller.prepare_core_reply(event)).unwrap()
        self.native.conversations["qq-main"] = "genuinely-reset-conversation"

        authorized = await self.controller.authorize_core_reply(event, prepared)

        self.assertFalse(authorized.is_success)

    async def test_editing_same_native_persona_before_turn_refreshes_text_and_preserves_reply(self):
        self.native.persona_records["original"]["prompt"] = "你还是同一个角色，今天说话更加轻松。"
        event = self.event(identifier="core-native-prompt-edited")

        prepared = (await self.controller.prepare_core_reply(event)).unwrap()
        authorized = await self.controller.authorize_core_reply(event, prepared)

        self.assertEqual(prepared["instruction"], "你还是同一个角色，今天说话更加轻松。")
        self.assertEqual(prepared["context"].role_binding.role.subject_id, "role-a")
        self.assertTrue(authorized.is_success, authorized.failure)

    async def test_native_persona_edit_during_prepared_turn_requires_fresh_background(self):
        event = self.event(identifier="core-native-prompt-midturn")
        prepared = (await self.controller.prepare_core_reply(event)).unwrap()
        self.native.persona_records["original"]["prompt"] = "你还是这个角色，但本轮准备之后改过提示词。"

        authorized = await self.controller.authorize_core_reply(event, prepared)

        self.assertFalse(authorized.is_success)
        self.assertEqual(authorized.failure.code, "core_reply_instruction_changed")

    async def test_send_time_denial_keeps_an_unconfigured_route_as_not_claimed(self):
        event = self.event(identifier="core-reply-unconfigured-route")
        prepared = (await self.controller.prepare_core_reply(event)).unwrap()
        stranger = FakeEvent(NS(platform=self.native.platforms["qq-main"]),
                             event_id="core-reply-unconfigured-route", sender="999", login="100")

        authorized = await self.controller.authorize_core_reply(stranger, prepared)

        self.assertFalse(authorized.is_success)
        self.assertEqual(authorized.failure.code, "host_not_claimed")
        self.assertIsNone(self.controller.target_for_event(stranger, connected=False))

    async def test_send_time_denial_reports_a_host_that_stopped_accepting(self):
        event = self.event(identifier="core-reply-not-accepting")
        prepared = (await self.controller.prepare_core_reply(event)).unwrap()
        self.controller._accepting = False

        authorized = await self.controller.authorize_core_reply(event, prepared)

        self.assertFalse(authorized.is_success)
        self.assertEqual(authorized.failure.code, "host_not_accepting")

    async def test_first_conversation_creation_failure_can_fall_back_without_stopping_event(self):
        self.native.conversations["qq-main"] = None
        self.native.conversation_manager.new_conversation = AsyncMock(side_effect=OSError("temporarily unavailable"))

        result = await self.controller.prepare_core_reply(self.event(identifier="core-conversation-create-failed"))

        self.assertFalse(result.is_success)
        self.assertEqual(result.failure.code, "host_core_reply_conversation_unavailable")

    async def test_expression_preferences_capture_failure_preserves_core_reply(self):
        event = self.event(identifier="core-reply-settings-failure")
        self.app.reply_settings.snapshot = Mock(side_effect=RuntimeError("settings unavailable"))

        result = await self.controller.prepare_core_reply(event)

        self.assertTrue(result.is_success, result.failure)
        self.assertEqual(result.unwrap()["instruction"], self.native.persona["prompt"])
        self.assertNotIn("expression_preferences", result.unwrap()["context_sections"])
        self.assertEqual(self.app.runtime.read_all("SELECT batch_id FROM conversation_batches", ()), ())

    async def test_configured_expression_preferences_apply_and_disable_without_selecting_v2_route(self):
        from companion_v2.conversation.reply_settings import GROUP, ReplyConfigurationOwner, target_for

        self.app.router._route_selection = Mock(side_effect=AssertionError("expression must not select a V2 model"))
        original = (await self.controller.prepare_core_reply(self.event(identifier="core-style-context"))).unwrap()
        target = target_for(original["context"])
        owner = ReplyConfigurationOwner(self.app.runtime)
        fields = self.app.configuration.registry.group(GROUP)

        def configure(**changes):
            owner.write(target, fields, changes={GROUP + "." + key: value for key, value in changes.items()},
                reset=(), expected_revision=owner.read(target, fields).revision, observed_at=self.clock.now())

        configure(prompt_style_mode="guided", style_prompt="把新鲜发现兴冲冲地说给熟人听。",
            enable_humanized_expression=True,
            humanized_expression_prompt="留一点接话的余地，别机械地汇报。",
            content_cleanup_enabled=True, content_cleanup_words='["旧正文清理规则"]')
        self.app.reply_settings.capture = Mock(side_effect=AssertionError("core expression must not validate old delivery settings"))
        prepared = (await self.controller.prepare_core_reply(self.event(identifier="core-expression-on"))).unwrap()
        self.assertIn("把新鲜发现兴冲冲地说给熟人听。", prepared["stable_prompt_instruction"])
        self.assertIn("留一点接话的余地，别机械地汇报。", prepared["stable_prompt_instruction"])
        self.assertIn("沿用当前人格与关系，结合本轮语境自然回应", prepared["stable_prompt_instruction"])
        self.assertNotIn("日常聊天先回应对方此刻最想说的一点", prepared["stable_prompt_instruction"])
        self.assertIn("expression_preferences", prepared["context_sections"])
        self.assertNotIn("旧正文清理规则", prepared["stable_prompt_instruction"])
        self.assertEqual(prepared["presentation_settings"]["content_cleanup_words"], '["旧正文清理规则"]')
        self.assertTrue(prepared["presentation_settings"]["content_cleanup_enabled"])

        configure(enable_humanized_expression=False)
        prepared = (await self.controller.prepare_core_reply(self.event(identifier="core-humanized-off"))).unwrap()
        self.assertIn("把新鲜发现兴冲冲地说给熟人听。", prepared["stable_prompt_instruction"])
        self.assertNotIn("留一点接话的余地，别机械地汇报。", prepared["stable_prompt_instruction"])
        self.assertNotIn("日常表达要求", prepared["stable_prompt_instruction"])

        configure(prompt_style_mode="free")
        prepared = (await self.controller.prepare_core_reply(self.event(identifier="core-style-free"))).unwrap()
        self.assertIn("把新鲜发现兴冲冲地说给熟人听。", prepared["stable_prompt_instruction"])
        self.assertNotIn("沿用当前人格与关系，结合本轮语境自然回应", prepared["stable_prompt_instruction"])
        self.assertNotIn("日常聊天先回应对方此刻最想说的一点", prepared["stable_prompt_instruction"])

        configure(style_prompt="")
        prepared = (await self.controller.prepare_core_reply(self.event(identifier="core-expression-off"))).unwrap()
        self.assertNotIn("expression_preferences", prepared["context_sections"])
        self.app.router._route_selection.assert_not_called()
        self.app.reply_settings.capture.assert_not_called()
        self.assertEqual(self.app.runtime.read_all("SELECT batch_id FROM conversation_batches", ()), ())
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_reply_page_voice_policy_is_used_when_host_global_voice_is_disabled(self):
        from companion_v2.conversation.reply_settings import (
            GROUP, ReplyConfigurationOwner, target_for, voice_settings,
        )
        from companion_v2.conversation.voice_reply import VoiceReplyPolicy

        self.assertFalse(self.controller.settings.common.voice_reply_enabled)
        seed = (await self.controller.prepare_core_reply(
            self.event(identifier="core-voice-settings-seed"))).unwrap()
        target = target_for(seed["context"])
        owner = ReplyConfigurationOwner(self.app.runtime)
        fields = self.app.configuration.registry.group(GROUP)
        policy = VoiceReplyPolicy(generation_mode="model_tag", language="en",
                                  provider_id="reply-page-tts", extra_prompt="语气自然亲切")
        changes = {GROUP + "." + key: value for key, value in voice_settings(policy).items()}
        current = owner.read(target, fields)
        owner.write(target, fields, changes=changes, reset=(),
                    expected_revision=current.revision, observed_at=self.clock.now())

        prepared = (await self.controller.prepare_core_reply(
            self.event(identifier="core-voice-settings-active", text="Tell me a small story"))).unwrap()

        self.assertEqual(prepared["voice_reply_policy"], policy)
        from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA, inject_core_reply_prompt
        from tests.test_core_reply_voice import FakeEvent

        event = FakeEvent()
        event.set_extra(CORE_REPLY_CONTEXT_EXTRA, {"prepared": prepared})
        request = NS(system_prompt="原生角色", prompt="Tell me a small story")
        inject_core_reply_prompt(event, request)
        self.assertIn("<pc_tts>", request.system_prompt)
        self.assertIn("语气自然亲切", request.system_prompt)

    async def test_global_voice_policy_is_used_when_reply_page_snapshot_fails(self):
        from dataclasses import replace

        from companion_v2.conversation.voice_reply import VoiceReplyPolicy

        policy = VoiceReplyPolicy(generation_mode="model_tag", provider_id="host-global-tts")
        common = replace(self.controller.settings.common, voice_reply_enabled=True, voice_reply_policy=policy)
        settings = replace(self.controller.settings, common=common)
        self.controller.settings = settings
        self.controller.session.settings = settings
        self.app.reply_settings.snapshot = Mock(side_effect=RuntimeError("settings unavailable"))

        prepared = (await self.controller.prepare_core_reply(
            self.event(identifier="core-voice-settings-fallback"))).unwrap()

        self.assertEqual(prepared["voice_reply_policy"], policy)

    async def test_drama_direction_is_prepared_for_the_actual_selected_voice(self):
        from dataclasses import replace
        from astrbot_host.fish_audio import DRAMA_DIRECTION
        from companion_v2.conversation.voice_reply import VoiceReplyPolicy

        policy = VoiceReplyPolicy(generation_mode="model_tag", provider_id="fish-selected")
        settings = replace(self.controller.settings, common=replace(self.controller.settings.common,
            voice_reply_enabled=True, voice_reply_policy=policy))
        self.controller.settings = self.controller.session.settings = settings
        self.app.reply_settings.snapshot = Mock(side_effect=RuntimeError("settings unavailable"))
        voice = NS(provider_config={"type": "fishaudio_tts_api", "model": "drama-3"},
                   get_model=lambda: "drama-3")
        self.native.get_provider_by_id = lambda identifier: voice if identifier == "fish-selected" else self.native.provider
        self.native.get_all_tts_providers = lambda: [voice]
        prepared = (await self.controller.prepare_core_reply(
            self.event(identifier="core-drama-direction"))).unwrap()
        self.assertEqual(prepared["voice_provider_instruction"], DRAMA_DIRECTION)

    async def test_untriggered_voice_configuration_still_reaches_model_perception(self):
        from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA
        from astrbot_host.core_reply_perception import inject_core_reply_perception_context
        from companion_v2.conversation.reply_settings import (
            GROUP, ReplyConfigurationOwner, target_for, voice_settings,
        )
        from companion_v2.conversation.voice_reply import VoiceReplyPolicy
        from tests.test_core_reply_perception import perception_payload

        seed = (await self.controller.prepare_core_reply(self.event(identifier="voice-perception-seed"))).unwrap()
        policy = VoiceReplyPolicy(trigger_mode="request", generation_mode="model_tag", language="ja",
                                  ja_provider_id="fish-ja", foreign_text_mode="translation")
        owner = ReplyConfigurationOwner(self.app.runtime)
        target = target_for(seed["context"])
        fields = self.app.configuration.registry.group(GROUP)
        current = owner.read(target, fields)
        owner.write(target, fields,
                    changes={GROUP + "." + key: value for key, value in voice_settings(policy).items()},
                    reset=(), expected_revision=current.revision, observed_at=self.clock.now())
        voice = NS(provider_config={"id": "fish-ja", "type": "fishaudio_tts_api", "model": "drama-3"},
                   get_audio=AsyncMock())
        self.native.get_provider_by_id = lambda key: voice if key == "fish-ja" else self.native.provider
        self.native.get_all_tts_providers = lambda: [voice]
        event = self.event(identifier="core-untriggered-voice", text="现在用的Drama 3是什么？")

        prepared = (await self.controller.prepare_core_reply(event)).unwrap()
        self.assertIsNone(prepared["voice_reply_policy"])
        self.assertEqual(prepared["configured_voice_reply_policy"], policy)
        self.assertEqual(prepared["voice_provider_instruction"], "")
        event.set_extra(CORE_REPLY_CONTEXT_EXTRA, {"prepared": prepared})
        request = NS(extra_user_content_parts=[])
        await inject_core_reply_perception_context(event, request, native_context=self.native)
        route = perception_payload(request)["environment"]["tts_route"]
        self.assertEqual(route["status"], "available")
        self.assertEqual(route["provider_id"], "fish-ja")
        self.assertEqual(route["model"], "drama-3")
        self.assertEqual(route["language"], "ja")
        self.assertTrue(route["companion_voice_enabled"])
        self.assertFalse(route["companion_voice_active_this_turn"])
        self.assertTrue(route["drama_3_directions"])
        voice.get_audio.assert_not_awaited()

    async def test_effective_task_prompt_is_used_without_resolving_model_route(self):
        group = self.app.router.profiles["conversation.reply"].group
        configuration_read = self.app.router.configuration.read

        def configured(actor, *, target, group_id, observed_at):
            if group_id == group:
                return Outcome.success(NS(fields={group + ".prompt": NS(value="读到喜欢的东西时兴冲冲地分享")}))
            return configuration_read(actor, target=target, group_id=group_id, observed_at=observed_at)

        self.app.router.configuration.read = configured
        self.app.router._route_selection = Mock(side_effect=AssertionError("core prompt must not select a V2 model"))

        prepared = (await self.controller.prepare_core_reply(self.event(identifier="core-task-prompt"))).unwrap()

        self.assertIn("读到喜欢的东西时兴冲冲地分享", prepared["stable_prompt_instruction"])
        self.assertIn("task_prompt", prepared["context_sections"])
        self.app.router._route_selection.assert_not_called()

    async def test_authorized_background_is_injected_without_replacing_core_history(self):
        event = self.event(identifier="core-reply-background", text="下午做什么了")
        life = ContextContribution("life-test", "role.life", "role_activity",
            ContributionState.READY, '{"activity":"在书房读了一章小说","state":"completed"}')
        relationship = ContextContribution("relation-test", "relationship.memory", "relationship_background",
            ContributionState.READY, '{"interpretation":"喜欢轻松地聊书"}')
        self.app.messages.life_context = NS(capture=Mock(return_value={}),
                                            contributions=Mock(return_value=(life,)))
        self.app.messages.relationship_context = NS(capture=Mock(return_value={}),
                                                    contributions=Mock(return_value=(relationship,)))

        prepared = (await self.controller.prepare_core_reply(event)).unwrap()

        self.assertEqual(prepared["text"], "下午做什么了")
        self.assertIn("在书房读了一章小说", prepared["prompt_instruction"])
        self.assertIn("喜欢轻松地聊书", prepared["prompt_instruction"])
        self.assertEqual(set(prepared["context_sections"]) & {"life", "relationship"}, {"life", "relationship"})
        self.assertEqual(self.app.runtime.read_all("SELECT batch_id FROM conversation_batches", ()), ())
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_one_failed_optional_domain_does_not_drop_other_background(self):
        event = self.event(identifier="core-reply-background-fallback")
        self.app.messages.life_context = NS(capture=Mock(side_effect=RuntimeError("life unavailable")))
        relationship = ContextContribution("relation-ok", "relationship.memory", "relationship_background",
            ContributionState.READY, "可以自然地聊最近读过的书")
        self.app.messages.relationship_context = NS(capture=Mock(return_value={}),
                                                    contributions=Mock(return_value=(relationship,)))

        result = await self.controller.prepare_core_reply(event)

        self.assertTrue(result.is_success, result.failure)
        self.assertIn("可以自然地聊最近读过的书", result.unwrap()["prompt_instruction"])
        self.assertNotIn("life", result.unwrap()["context_sections"])

    async def test_private_inspirations_preserve_original_source_without_fetching_news(self):
        event = self.event(identifier="core-news-followup", text="刚才那条在哪看到的")
        material = ContextContribution("shared-news", "proactive.inspirations", "shared_news_provenance",
            ContributionState.READY, '{"items":[{"text":"竟然发现这么漂亮的星图",'
            '"provenance":{"media":"原媒体","url":"https://example.invalid/original"}}]}')
        owner = NS(capture_contribution=Mock(return_value=material))
        self.app.messages.proactive_inspirations = owner

        prepared = (await self.controller.prepare_core_reply(event)).unwrap()

        self.assertIn("https://example.invalid/original", prepared["prompt_instruction"])
        self.assertIn("没有追问时自然接话，不附来源页脚", prepared["prompt_instruction"])
        self.assertIn("proactive_inspirations", prepared["context_sections"])
        owner.capture_contribution.assert_called_once()
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_private_calendar_uses_configured_timezone_without_calling_external_port(self):
        from companion_v2.conversation.perception_settings import DEFAULTS

        self.app.perception_settings.capture = Mock(return_value={"settings": {
            **DEFAULTS, "calendar_timezone": "UTC", "guidance": "自然地说起当下的时间",
        }})
        external = Mock(side_effect=AssertionError("ordinary core reply must not invoke perception provider"))
        self.app.messages.solar_term_port = NS(lookup=external)

        prepared = (await self.controller.prepare_core_reply(self.event(identifier="core-calendar"))).unwrap()

        self.assertIn('"timezone":"UTC"', prepared["perception_context"])
        self.assertIn(self.clock.now().strftime("%H:%M"), prepared["perception_context"])
        self.assertEqual(prepared["perception_guidance"], "自然地说起当下的时间")
        self.assertTrue(any("current_local_time" in item for item in prepared["perception_instructions"]))
        self.assertIn("perception", prepared["context_sections"])
        external.assert_not_called()

    async def test_disabled_calendar_keeps_local_time_without_calendar_or_empty_inspirations(self):
        from companion_v2.conversation.perception_settings import DEFAULTS

        self.app.perception_settings.capture = Mock(return_value={"settings": {**DEFAULTS, "calendar_enabled": False}})
        contribution = Mock(return_value=None)
        self.app.messages.proactive_inspirations = NS(capture_contribution=contribution)

        prepared = (await self.controller.prepare_core_reply(self.event(identifier="core-no-calendar"))).unwrap()

        self.assertIn("perception", prepared["context_sections"])
        self.assertIn("current_local_time", prepared["perception_context"])
        self.assertNotIn("local_calendar_context", prepared["perception_context"])
        self.assertNotIn("proactive_inspirations", prepared["context_sections"])
        contribution.assert_called_once()

    async def test_private_denial_binds_notice_context_to_the_failed_preparation(self):
        event = self.event(identifier="core-reply-private-denied")
        context = self.controller.authority.context("first", "role-a")
        identity = self.controller.authority.identities
        identity.revoke_grant(self.controller.authority.grant_id(context.scope, "conversation.reply"),
                              revoked_at=self.clock.now(), expected_revision=identity.revision)

        result = await self.controller.prepare_core_reply(event)

        self.assertEqual(result.failure.code, "private_access_dialogue_denied")
        self.assertIsInstance(result, Outcome)
        self.assertFalse(hasattr(self.controller.session, "last_refusal"),
                         "a shared refusal slot must not transport this event's context")
        refusal = result.refusal
        self.assertIsNotNone(refusal)
        self.assertEqual(refusal.scope, context.scope)
        self.assertEqual(refusal.actor.subject, context.actor.subject)
        receive = AsyncMock(side_effect=AssertionError("core refusal must not retry inbound receive"))
        self.controller.session.receive = receive
        with patch.object(self.controller, "_refuse_private", new=AsyncMock(return_value={"state": "sent"})) as refuse:
            receipt = await self.controller.refuse_core_reply(event, refusal)
        self.assertEqual(receipt, {"state": "sent"})
        refuse.assert_awaited_once_with(self.controller.session, event, refusal)
        receive.assert_not_awaited()

    async def test_calendar_proxy_does_not_admit_ordinary_message_to_v2(self):
        result = await self.controller.receive_core_command(self.event(identifier="ordinary-core-command"))

        self.assertTrue(result.is_success, result.failure)
        self.assertEqual(result.unwrap(), {"handled": False})
        self.assertEqual(self.app.runtime.read_all("SELECT batch_id FROM conversation_batches", ()), ())
        self.assertEqual(self.native.provider.client.state.calls, [])


class GroupCoreReplyAdmissionTests(GroupFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.controller = self.make_controller(group_config(group_delivery_mode="onebot_group"))
        self.app = await self.start_host()

    async def test_group_admission_is_bound_to_the_same_context_and_event(self):
        event = self.group_event(identifier="core-reply-group", text="小星看看")
        event.message_obj.message.insert(0, NS(type="At", qq="100"))
        with patch.object(self.app.reply_settings, "capture", side_effect=ValueError("legacy segmentation configuration")):
            candidate = await self.controller.group_reply_candidate(event)
        self.assertTrue(candidate.is_success, candidate.failure)
        self.assertTrue(candidate.unwrap()["admitted"])

        prepared = await self.controller.prepare_core_reply(event, group_admission=candidate.unwrap())
        self.assertTrue(prepared.is_success, prepared.failure)
        self.assertEqual(prepared.unwrap()["channel"], "group")
        self.assertEqual(prepared.unwrap()["target_id"], "group-a")
        self.assertTrue(prepared.unwrap()["presentation_settings"]["content_cleanup_enabled"])

        authorized = await self.controller.authorize_core_reply(event, prepared.unwrap())
        self.assertTrue(authorized.is_success, authorized.failure)
        self.assertEqual(self.app.runtime.read_all("SELECT batch_id FROM conversation_batches", ()), ())
        self.assertEqual(self.native.provider.client.state.calls, [])

        changed_candidate = dict(candidate.unwrap(), event_id="another-event")
        rejected = await self.controller.prepare_core_reply(event, group_admission=changed_candidate)
        self.assertFalse(rejected.is_success)
        self.assertEqual(rejected.failure.code, "group_admission_changed")

    async def test_send_time_denial_names_a_disconnected_account_not_a_missing_target(self):
        event = self.group_event(identifier="core-reply-client-offline")
        prepared = (await self.controller.prepare_core_reply(event)).unwrap()
        # OneBot drops the account's reverse-WebSocket API entry while the
        # message route itself stays configured.
        clients = self.native.platforms["qq-main"].client._wsr_api_clients
        offline = clients.pop("100")
        try:
            self.assertIsNotNone(self.controller.target_for_event(event, connected=False))
            self.assertIsNone(self.controller.target_for_event(event))
            authorized = await self.controller.authorize_core_reply(event, prepared)
        finally:
            clients["100"] = offline

        self.assertFalse(authorized.is_success)
        self.assertEqual(authorized.failure.code, "host_client_offline")

    async def test_first_group_grants_policy_revision_change_does_not_change_identity(self):
        event = self.group_event(target="group-b", identifier="first-group-core-policy")
        authority = self.controller.authority
        old_actor = authority.actor(self.controller.target_for_event(event), "200")

        prepared = (await self.controller.prepare_core_reply(event)).unwrap()
        context = prepared["context"]
        current = authority.identities.resolve_authenticated(
            context.actor.principal, observed_at=self.clock.now()).unwrap()
        authorized = await self.controller.authorize_core_reply(event, prepared)

        self.assertGreater(current.policy_revision, old_actor.policy_revision)
        self.assertEqual(current.subject, old_actor.subject)
        self.assertEqual(current.binding_id, old_actor.binding_id)
        self.assertEqual(current.binding_revision, old_actor.binding_revision)
        self.assertEqual(context.actor, current)
        self.assertTrue(authorized.is_success, authorized.failure)

    async def test_first_group_authorization_uses_the_actor_refreshed_after_provisioning(self):
        from companion_v2.adapters.astrbot.staging_config import REQUIRED_CAPABILITIES

        event = self.group_event(target="group-b", identifier="first-group-fresh-actor")
        identities = self.controller.authority.identities
        observed = []
        original = identities.authorize

        def recording(resolved, *, scope, capability, observed_at):
            if capability in REQUIRED_CAPABILITIES:
                observed.append((capability, resolved.policy_revision, identities.revision))
            return original(resolved, scope=scope, capability=capability, observed_at=observed_at)

        identities.authorize = recording
        try:
            prepared = (await self.controller.prepare_core_reply(event)).unwrap()
        finally:
            identities.authorize = original

        self.assertEqual({capability for capability, _resolved, _current in observed},
                         set(REQUIRED_CAPABILITIES))
        self.assertTrue(all(resolved == current for _capability, resolved, current in observed),
                        "every grant check must use the actor resolved after provisioning")
        self.assertEqual(len({resolved for _capability, resolved, _current in observed}), 1)
        self.assertEqual(prepared["context"].actor.policy_revision, identities.revision)

    async def test_first_group_grant_is_added_even_when_private_reply_grant_exists(self):
        private_event = self.event(identifier="existing-private-reply")
        private_prepared = (await self.controller.prepare_core_reply(private_event)).unwrap()
        private_context = private_prepared["context"]
        self.assertEqual(private_context.scope.kind.value, "private")

        event = self.group_event(target="group-b", identifier="new-group-with-private-grant")
        prepared = (await self.controller.prepare_core_reply(event)).unwrap()
        context = prepared["context"]
        self.assertEqual(context.scope.kind.value, "group")

        identities = self.controller.authority.identities
        grants = [grant for grant in identities.grants_for_subject(context.actor.subject)
                  if grant.capability == "conversation.reply" and grant.state.value == "active"]
        self.assertTrue(any(grant.scope == private_context.scope for grant in grants))
        self.assertTrue(any(grant.scope == context.scope for grant in grants))
        authorized = await self.controller.authorize_core_reply(event, prepared)
        self.assertTrue(authorized.is_success, authorized.failure)

    async def test_first_group_revoked_grant_is_not_reissued_by_preparation(self):
        event = self.group_event(target="group-b", identifier="fresh-group-revocation")
        prepared = (await self.controller.prepare_core_reply(event)).unwrap()
        context = prepared["context"]
        identities = self.controller.authority.identities
        grant_id = self.controller.authority.groups.grant_id(
            context.scope, context.actor.subject, "conversation.reply")
        identities.revoke_grant(grant_id, revoked_at=self.clock.now(),
                                expected_revision=identities.revision)
        revision = identities.revision

        again = await self.controller.prepare_core_reply(
            self.group_event(target="group-b", identifier="fresh-group-after-revocation"))

        self.assertFalse(again.is_success)
        self.assertIn(again.failure.code, {"authorization_revoked", "host_grant_changed",
                                           "scope_not_granted", "authorization_denied"})
        grants = [grant for grant in identities.grants_for_subject(context.actor.subject)
                  if grant.grant_id == grant_id]
        self.assertEqual(len(grants), 1)
        self.assertEqual(grants[0].state.value, "revoked")
        self.assertEqual(identities.revision, revision,
                         "preparation must not mint a replacement grant")

    async def test_repeated_first_group_preparation_is_idempotent(self):
        event = self.group_event(target="group-b", identifier="first-group-idempotent")
        first = (await self.controller.prepare_core_reply(event)).unwrap()
        identities = self.controller.authority.identities
        revision = identities.revision
        grants = tuple(grant.grant_id for grant in identities.grants_for_subject(
            first["context"].actor.subject))

        second = (await self.controller.prepare_core_reply(
            self.group_event(target="group-b", identifier="first-group-idempotent"))).unwrap()

        self.assertEqual(first["context"].key, second["context"].key)
        self.assertEqual(first["context"].actor, second["context"].actor)
        self.assertEqual(first["context"].role_binding, second["context"].role_binding)
        self.assertEqual(identities.revision, revision)
        self.assertEqual(tuple(grant.grant_id for grant in identities.grants_for_subject(
            second["context"].actor.subject)), grants)

    async def test_group_revoked_permission_is_rechecked_after_stable_identity_match(self):
        event = self.group_event(identifier="core-group-permission-revoked")
        prepared = (await self.controller.prepare_core_reply(event)).unwrap()
        context = prepared["context"]
        identities = self.controller.authority.identities
        grant = self.controller.authority.groups.grant_id(context.scope, context.actor.subject, "conversation.reply")
        identities.revoke_grant(grant, revoked_at=self.clock.now(), expected_revision=identities.revision)

        authorized = await self.controller.authorize_core_reply(event, prepared)

        self.assertFalse(authorized.is_success)
        self.assertNotEqual(authorized.failure.code, "core_reply_context_changed")

    async def test_first_group_candidate_and_core_reply_share_created_native_conversation(self):
        from astrbot.core.astr_main_agent import _get_session_conv

        event = self.group_event(identifier="first-group-native-cid", text="小星看看")
        event.message_obj.message.insert(0, NS(type="At", qq="100"))
        self.native.conversations[event.unified_msg_origin] = None
        manager = self.native.conversation_manager

        async def create(umo, platform_id):
            self.native.conversations[umo] = "core-first-group-conversation"
            return "core-first-group-conversation"

        async def conversation(umo, cid, *, create_if_not_exists=True):
            return NS(cid=cid, persona_id="original", history="[]")

        manager.new_conversation = AsyncMock(side_effect=create)
        manager.get_conversation = conversation
        candidate = (await self.controller.group_reply_candidate(event)).unwrap()
        prepared = (await self.controller.prepare_core_reply(event, group_admission=candidate)).unwrap()
        core_conversation = await _get_session_conv(event, self.native)
        authorized = await self.controller.authorize_core_reply(event, prepared)

        manager.new_conversation.assert_awaited_once_with(event.unified_msg_origin, "qq-main")
        self.assertTrue(candidate["admitted"])
        self.assertEqual(candidate["context_key"], prepared["context"].key)
        self.assertEqual(core_conversation.cid, "core-first-group-conversation")
        self.assertTrue(authorized.is_success, authorized.failure)

    async def test_group_background_never_reads_private_memory_or_relationship(self):
        private = Mock(side_effect=AssertionError("private background must not enter a group"))
        self.app.messages.memory_context = NS(delegated=private)
        self.app.messages.relationship_context = NS(capture=private)
        self.app.messages.relationship_profile_context = NS(capture=private)
        self.app.messages.life_context = NS(capture=private)
        event = self.group_event(identifier="core-reply-group-background", text="我们聊聊书")

        prepared = (await self.controller.prepare_core_reply(event)).unwrap()

        self.assertIn("group_speaker", prepared["context_sections"])
        self.assertIn("当前群聊发言者", prepared["prompt_instruction"])
        private.assert_not_called()
        self.assertEqual(self.app.runtime.read_all("SELECT batch_id FROM conversation_batches", ()), ())


if __name__ == "__main__":
    unittest.main()
