"""Group listening stays silent; admitted non-mention replies carry reasons."""

from datetime import timedelta
import asyncio
from dataclasses import replace as dc_replace
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.configuration.owners import SparseConfigurationOwner
from companion_v2.identity_policy.models import AuthorizationTier
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.conversation.participation import CONFIG_GROUP, GroupParticipationConfigurationOwner, configuration_fields
from companion_v2.conversation.reply_settings import (GROUP as REPLY_GROUP, ReplyConfigurationOwner, target_for,
    configuration_fields as reply_fields)
from astrbot.core.config import DEFAULT_CONFIG
from astrbot.core.pipeline.process_stage.stage import ProcessStage
from companion_v2.conversation.reset import ConversationWindow, reset_window
from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA, activate_core_reply_delivery_guard
from astrbot.core.agent.message import Message, ToolCall
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot_host.core_reply_tools import native_tool_name
from astrbot_host.core_reply_tracking import (
    finish_core_reply_tracking, mark_core_reply_inflight, prepare_core_reply_tracking,
    observe_core_reply_tool_delivery,
)
from tests.group_host_support import GroupFixture, group_config
from tests.test_main_no_reply import ENTRY


class GroupReplyAdmissionTests(GroupFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.controller = self.make_controller(group_config(group_delivery_mode="onebot_group"))
        self.app = await self.start_host()
        self.plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        self.plugin.staging = self.controller
        # Public-help scenarios explicitly opt in. New groups only wake from
        # explicit addressing and configured participation policies.
        await self.configure(group_wakeup_question_enabled=True)

    def event(self, identifier, text, *, sender="200", mention=False, reply=None, target="group-a"):
        event = self.group_event(identifier=identifier, text=text, sender=sender, target=target)
        event.stopped = False
        event.stop_event = lambda: setattr(event, "stopped", True)
        event.is_wake = True
        if mention:
            event.message_obj.message.insert(0, NS(type="At", qq="100"))
        if reply is not None:
            event.message_obj.message.insert(0, NS(type="Reply", id=reply))
        return event

    async def candidate(self, event):
        result = await self.controller.group_reply_candidate(event)
        self.assertTrue(result.is_success, result.failure)
        return result.unwrap()

    @property
    def admission(self):
        return self.app.tools.group_participation.admission

    async def core_turn(self, event, *, result_type="LLM_RESULT"):
        await self.plugin.receive_companion_message(event)
        state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        self.assertIsInstance(state, dict)
        event.get_result = lambda: NS(result_content_type=NS(name=result_type))
        event.core_request = NS(system_prompt="AstrBot prompt")
        await self.plugin.prepare_companion_core_request(event, event.core_request)
        await self.flush_tracking()
        return state["prepared"]

    async def core_send(self, event, text="可以先看看设置。"):
        await self.plugin.guard_companion_core_delivery(event)
        result = await event.send(text)
        await self.plugin.observe_companion_core_reply_sent(event)
        await self.flush_tracking()
        return result

    async def flush_tracking(self):
        observer = self.controller.session.core_reply_observations
        if observer._pending:
            await asyncio.gather(*tuple(observer._pending), return_exceptions=True)

    def assert_no_v2_generation(self):
        self.assertEqual(self.app.runtime.read_all("SELECT * FROM conversation_batches", ()), ())
        self.assertEqual(self.app.runtime.read_all("SELECT * FROM conversation_parts", ()), ())
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def configure(self, **changes):
        context = (await self.controller.observe_group_early(self.event("settings", "普通群聊天"))).unwrap()
        settings = self.controller.settings.common
        fields = reply_fields(settings.segmentation, settings.streaming, settings.policy.part_gap_seconds)
        owner = ReplyConfigurationOwner(self.app.runtime)
        target = target_for(context)
        owner.write(target, fields, changes={"conversation.presentation." + key: value for key, value in changes.items()},
                    reset=(), expected_revision=owner.read(target, fields).revision, observed_at=self.clock.now())
        return context

    async def test_cleanup_role_defaults_preserve_explicit_group_overrides(self):
        context = (await self.controller.observe_group_early(self.group_event(identifier="reply-role-scope"))).unwrap()
        target = target_for(context)
        other_scope = dc_replace(target.scope, native_scope_id="other-group",
                                 conversation_id="other-conversation", session_id="other-session")
        other_target = dc_replace(target, scope=other_scope)
        common = self.controller.settings.common
        fields = reply_fields(common.segmentation, common.streaming, common.policy.part_gap_seconds)
        local_owner = SparseConfigurationOwner(self.app.runtime)
        reply_owner = ReplyConfigurationOwner(self.app.runtime)
        cleanup_words = REPLY_GROUP + ".content_cleanup_words"
        cleanup_enabled = REPLY_GROUP + ".content_cleanup_enabled"
        split_words = REPLY_GROUP + ".split_words"

        for window, values in ((target, {cleanup_enabled: True, cleanup_words: '["，"]'}),
                               (other_target, {cleanup_enabled: False, cleanup_words: '["。"]',
                                               split_words: '["？"]'})):
            snapshot = local_owner.read(window, fields)
            local_owner.write(window, fields, changes=values, reset=(), expected_revision=snapshot.revision,
                              observed_at=self.clock.now())

        role_target = target_for(context, ConfigLayer.ROLE)
        snapshot = reply_owner.read(role_target, fields)
        reply_owner.write(role_target, fields, changes={cleanup_enabled: True, cleanup_words: '["，"]'},
                          reset=(), expected_revision=snapshot.revision, observed_at=self.clock.now())
        shared = reply_owner.read(other_target, fields)

        self.assertFalse(shared.fields[cleanup_enabled].value)
        self.assertEqual(shared.fields[cleanup_words].value, '["。"]')
        self.assertEqual(shared.fields[cleanup_enabled].source.layer, ConfigLayer.WINDOW)
        self.assertEqual(shared.fields[split_words].value, '["？"]')
        self.assertEqual(shared.fields[split_words].source.layer, ConfigLayer.WINDOW)
        unconfigured = dc_replace(target, scope=dc_replace(target.scope, native_scope_id="third-group",
                                   conversation_id="third-window", session_id="third-window"))
        inherited = reply_owner.read(unconfigured, fields)
        self.assertTrue(inherited.fields[cleanup_enabled].value)
        self.assertEqual(inherited.fields[cleanup_words].value, '["，"]')
        self.assertEqual(inherited.fields[cleanup_enabled].source.layer, ConfigLayer.ROLE)
        self.assertFalse(inherited.fields[split_words].explicit)

    def configure_participation(self, context, **changes):
        target = ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW, context.role_binding.role, context.scope)
        owner, fields = GroupParticipationConfigurationOwner(self.app.runtime), configuration_fields()
        owner.write(target, fields, changes={CONFIG_GROUP + "." + key: value for key, value in changes.items()},
                    reset=(), expected_revision=owner.read(target, fields).revision, observed_at=self.clock.now())

    async def choose(self, event, mode, *, reason="", call_id="native-group-choice", extra_calls=()):
        arguments = {"mode": mode, "reason": reason}
        call = ToolCall(id=call_id, function=ToolCall.FunctionBody(
            name=native_tool_name("group.participation.choose"), arguments=json.dumps(arguments)))
        wrapper = NS(context=NS(event=event), messages=[Message(role="assistant", tool_calls=[call, *extra_calls])])
        tool = event.core_request.func_tool.get_tool(native_tool_name("group.participation.choose"))
        self.assertIsNotNone(tool)
        return json.loads(await tool.call(wrapper, **arguments))

    async def test_native_group_prompt_includes_effective_wakeup_and_participation_references(self):
        context = await self.configure(group_wakeup_interest_probability=.01,
            group_wakeup_interest_keywords=json.dumps(["星图"], ensure_ascii=False), group_wakeup_question_threshold=95,
            group_wakeup_cooldown_seconds=90)
        self.configure_participation(context, enable_group_interjection=True, group_interject_max_daily=0,
                                     group_interject_min_interval_minutes=33)
        event = self.event("core-effective-hints", "这也太离谱了")
        prepared = await self.core_turn(event)
        prompt = "\n".join(part.text for part in event.core_request.extra_user_content_parts)
        self.assertIn("group_participation", prepared["context_sections"])
        for text in ("求助强度参考 95/100", "兴趣话题自主接话宜低频", "兴趣候选抽取概率为 1%",
                     "自主插话参考额度剩余 0/0", "最小间隔参考 33 分钟", "本轮没有明确 @"):
            self.assertIn(text, prompt)
        self.assertIsNotNone(event.core_request.func_tool.get_tool(native_tool_name("group.participation.choose")))
        self.assert_no_v2_generation()

    async def test_interest_probability_is_a_candidate_gate(self):
        await self.configure(group_wakeup_interest_keywords='["星图"]',
                             group_wakeup_interest_probability=0)
        blocked = await self.candidate(self.event("interest-zero", "我在看星图的介绍"))
        self.assertNotEqual(blocked["reason"], "interest")
        await self.configure(group_wakeup_interest_probability=1)
        admitted = await self.candidate(self.event("interest-one", "我在看星图的介绍"))
        self.assertEqual(admitted["reason"], "interest")

    async def test_interest_probability_samples_candidates_below_one(self):
        await self.configure(group_wakeup_interest_keywords='["星图"]',
                             group_wakeup_interest_probability=0.5)
        results = [await self.candidate(self.event(f"interest-sample-{index}", "我在看星图的介绍"))
                   for index in range(8)]
        reasons = [result["reason"] for result in results]
        self.assertIn("interest", reasons)
        self.assertTrue(any(reason != "interest" for reason in reasons))

    async def test_native_group_skip_is_silent_and_never_counts_as_a_successful_send_or_missing_reply(self):
        event = self.event("core-skip", "这个问题有人知道怎么处理吗")
        prepared = await self.core_turn(event)
        decision = await self.choose(event, "skip", reason="大家正在接话，先旁听")
        self.assertEqual(decision["state"], "succeeded")
        await self.plugin.guard_companion_core_delivery(event)
        await event.send("模型后续不该发出的正文")
        await self.plugin.observe_companion_core_reply_sent(event)
        await self.flush_tracking()
        saved = self.admission.recorded_core_reply(prepared["context"], prepared["event_id"])
        self.assertEqual(saved["participation"]["mode"], "skip")
        self.assertEqual((saved["send_attempts"], saved["send_returns"]), (0, 0))
        self.assertEqual(event.sent, [])
        self.assertEqual(self.app.runtime.read_all("SELECT * FROM inbound_no_replies", ()), ())
        business = event.core_request.func_tool.get_tool(native_tool_name("turn.evidence"))
        self.assertIsNotNone(business)
        denied = json.loads(await business.call(NS(context=NS(event=event), messages=[])))
        self.assertEqual(denied["code"], "group_participation_skipped")
        self.assert_no_v2_generation()

    async def test_native_group_skip_cannot_revoke_prior_business_or_be_mixed_with_business_tools(self):
        event = self.event("core-skip-order", "有人知道怎么处理吗")
        await self.core_turn(event)
        other = ToolCall(id="business", function=ToolCall.FunctionBody(name=native_tool_name("turn.evidence"), arguments="{}"))
        denied = await self.choose(event, "skip", extra_calls=(other,))
        self.assertEqual(denied["code"], "group_participation_skip_alone")
        business = event.core_request.func_tool.get_tool(native_tool_name("turn.evidence"))
        await business.call(NS(context=NS(event=event), messages=[]))
        denied = await self.choose(event, "skip")
        self.assertEqual(denied["code"], "group_participation_started")

    async def test_native_interjection_budget_uses_send_returns_and_resets_with_the_window(self):
        context = await self.configure(group_wakeup_bot_name="小星")
        self.configure_participation(context, enable_group_interjection=True, group_interject_max_daily=2,
                                     group_interject_min_interval_minutes=33)
        event = self.event("core-autonomous-1", "这也太离谱了")
        prepared = await self.core_turn(event)
        await self.choose(event, "reply", reason="这个梗自然能接上")
        await self.core_send(event, "哈哈真的离谱")
        self.clock.advance(timedelta(seconds=1))
        second = self.event("core-autonomous-2", "小星你觉得呢", sender="201")
        await self.core_turn(second)
        reference = "\n".join(part.text for part in second.core_request.extra_user_content_parts)
        self.assertIn("自主插话参考额度剩余 1/2", reference)
        self.assertIn("最小间隔参考 33 分钟", reference)
        self.assertIn("宿主发送调用已返回", reference)
        self.assertNotIn("已确认 Bot 回复", reference)
        self.assertNotIn("自主插话参考额度剩余", second.core_request.system_prompt)
        self.clock.advance(timedelta(seconds=1))
        window = ConversationWindow.from_scope(prepared["context"].scope)
        reset_window(self.app.runtime, window, reset_id="core-participation-reset", at=self.clock.now())
        self.admission.reset_window(window)
        self.clock.advance(timedelta(seconds=1))
        third = self.event("core-autonomous-3", "小星再看看", sender="201")
        await self.core_turn(third)
        self.assertIn("自主插话参考额度剩余 2/2", "\n".join(part.text for part in third.core_request.extra_user_content_parts))
        self.assert_no_v2_generation()

    async def test_unmatched_messages_keep_observation_without_intake_logs_batches_or_model_calls(self):
        context = (await self.controller.observe_group_early(self.event("settings", "普通群聊天"))).unwrap()
        target = ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW, context.role_binding.role, context.scope)
        owner, fields = GroupParticipationConfigurationOwner(self.app.runtime), configuration_fields()
        owner.write(target, fields, changes={CONFIG_GROUP + ".enable_group_social_context": True,
                                           CONFIG_GROUP + ".enable_group_mood_detection": True}, reset=(),
                    expected_revision=owner.read(target, fields).revision, observed_at=self.clock.now())
        participation = self.app.tools.group_participation
        with patch.object(ENTRY.logger, "info") as info, patch.object(participation, "observe_early", wraps=participation.observe_early) as observe:
            for index, text in enumerate(("大家好", "晚饭吃面", "笑死", "？？？")):
                event = self.event("ordinary-" + str(index), text)
                await self.plugin.observe_companion_group_early(event)
                await self.plugin.receive_companion_message(event)
                self.assertFalse(event.stopped)
                self.assertTrue(event.call_llm)
            self.assertTrue(observe.called)
            self.assertFalse(any("接管入站" in call.args[0] or "入站已受理" in call.args[0] for call in info.call_args_list))
        self.assertEqual(self.app.runtime.read_all("SELECT * FROM conversation_batches", ()), ())
        self.assertEqual(self.app.runtime.read_all("SELECT * FROM inbound_no_replies", ()), ())
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assertTrue(self.app.runtime.read_all("SELECT key FROM runtime_metadata WHERE key LIKE '%:mood:%'", ()))

    async def test_mention_only_is_admitted_and_wake_listener_flag_is_not_a_trigger(self):
        self.assertFalse((await self.candidate(self.event("ordinary", "晚饭吃面")))["admitted"])
        decision = await self.candidate(self.event("at-only", "", mention=True))
        self.assertEqual(decision["reason"], "mention")
        self.assertTrue(decision["mentioned"])
        awakened = self.event("native-wake", "继续")
        awakened.is_at_or_wake_command = True
        self.assertEqual((await self.candidate(awakened))["reason"], "native_wakeup")
        quoted = self.event("native-quote", "", reply="previous-native-message")
        quoted.message_obj.message[0].sender_id = "100"
        quoted.is_at_or_wake_command = True
        self.assertEqual((await self.candidate(quoted))["reason"], "native_quote")
        mentioned_all = self.event("mention-all", "大家看看")
        mentioned_all.message_obj.message.insert(0, NS(type="At", qq="all"))
        mentioned_all.is_at_or_wake_command = True
        self.assertEqual((await self.candidate(mentioned_all))["reason"], "mention_all")

    async def test_interjection_requires_an_opening_and_repeat_counts_silent_observations(self):
        context = (await self.controller.observe_group_early(self.event("settings", "普通群聊天"))).unwrap()
        target = ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW, context.role_binding.role, context.scope)
        owner, fields = GroupParticipationConfigurationOwner(self.app.runtime), configuration_fields()
        owner.write(target, fields, changes={CONFIG_GROUP + ".enable_group_interjection": True}, reset=(),
                    expected_revision=owner.read(target, fields).revision, observed_at=self.clock.now())
        self.assertFalse((await self.candidate(self.event("no-opening", "晚饭吃面")))["admitted"])
        self.assertEqual((await self.candidate(self.event("opening", "这也太离谱了")))["reason"], "interjection")
        for index in range(3):
            self.clock.advance(timedelta(seconds=1))
            self.assertFalse((await self.candidate(self.event("silent-repeat-" + str(index), "好", sender=str(210 + index))))["admitted"])
        self.clock.advance(timedelta(seconds=1))
        self.assertEqual((await self.candidate(self.event("repeat-trigger", "好", sender="214")))["reason"], "repeat")

    async def test_mentioning_another_member_suppresses_role_name_direct_word(self):
        await self.configure(group_wakeup_bot_name="小星")
        event = self.event("other-at-with-role-name", "小星 小天使")
        event.message_obj.message.insert(0, NS(type="At", qq="201"))
        decision = await self.candidate(event)
        self.assertNotEqual(decision["reason"], "direct_word")
        self.assertFalse(decision["admitted"])

    async def test_direct_words_question_and_disabled_strategies_use_effective_settings(self):
        await self.configure(group_wakeup_bot_name="小星", group_wakeup_question_enabled=False)
        self.assertEqual((await self.candidate(self.event("named", "小星来看看")))["reason"], "direct_word")
        self.assertFalse((await self.candidate(self.event("question-off", "如何修复？")))["admitted"])
        await self.configure(group_wakeup_question_enabled=True)
        self.assertEqual((await self.candidate(self.event("question-on", "有没有人知道怎么修复")))["reason"], "question")
        await self.configure(group_wakeup_enabled=False)
        self.assertFalse((await self.candidate(self.event("wake-off", "小星来看看")))["admitted"])
        self.assertTrue((await self.candidate(self.event("at-when-off", "来看看", mention=True)))["admitted"])

    async def test_core_user_mention_keyword_boosts_voice_without_trusting_other_mentions(self):
        await self.configure(voice_enabled=True, voice_trigger_mode="request", voice_trigger_keywords="朗读",
            voice_probability=0, voice_cooldown_seconds=300,
            voice_primary_mention_keywords="专属语音词", voice_primary_mention_probability=1,
            voice_primary_mention_prompt="语气更亲近一些。")
        target = self.controller.settings.target("group-a")
        core = self.controller.authority.ensure_actor(target, "201")
        policy = ObjectAccessPolicy(self.app.runtime)
        policy.set_tier(core.subject, AuthorizationTier.CORE,
            expected_revision=policy.revision, observed_at=self.clock.now())

        ordinary = self.event("ordinary-mention-voice", "专属语音词")
        ordinary.message_obj.message.insert(0, NS(type="At", qq="100"))
        ordinary.message_obj.message.insert(0, NS(type="At", qq="202"))
        await self.core_turn(ordinary)
        ordinary_prepared = ordinary.get_extra(CORE_REPLY_CONTEXT_EXTRA)["prepared"]
        self.assertIsNone(ordinary_prepared["voice_reply_policy"])

        directed = self.event("core-mention-voice", "专属语音词")
        directed.message_obj.message.insert(0, NS(type="At", qq="100"))
        directed.message_obj.message.insert(0, NS(type="At", qq="201"))
        await self.core_turn(directed)
        directed_prepared = directed.get_extra(CORE_REPLY_CONTEXT_EXTRA)["prepared"]
        voice = directed_prepared["voice_reply_policy"]
        self.assertEqual(voice.trigger_mode, "adaptive")
        self.assertEqual(voice.cooldown_seconds, 0)
        self.assertIn("语气更亲近一些。", voice.instruction)

    async def test_non_mention_core_reply_logs_reason_and_send_return_without_platform_confirmation(self):
        await self.configure(group_wakeup_bot_name="小星")
        event = self.event("named-reply", "小星来看看")
        with self.assertLogs("astrbot_host.core_reply_tracking", level="INFO") as logs:
            prepared = await self.core_turn(event)
            await self.core_send(event)
        self.assertEqual(len(logs.output), 2)
        self.assertTrue(all("非@群回复原因" in line and "trigger=direct_word" in line and "reason_source=policy" in line
                            and "命中配置的直接唤醒词或名称" in line for line in logs.output))
        self.assertIn("stage=completed", logs.output[-1])
        self.assertIn("send_state=returned", logs.output[-1])
        self.assertTrue(all("platform_confirmed=false" in line for line in logs.output))
        self.assertFalse(event.stopped)
        self.assertFalse(getattr(event, "call_llm", False))
        saved = self.admission.recorded_core_reply(prepared["context"], prepared["event_id"])
        self.assertEqual(saved["state"], "completed")
        self.assertEqual(saved["platform_ids"], [])
        self.assertEqual(event.sent, ["可以先看看设置。"])
        self.assert_no_v2_generation()

    async def test_repeated_core_hooks_keep_one_turn_and_do_not_duplicate_reason_logs(self):
        event = self.event("help-reply", "如何处理这个问题？")
        with self.assertLogs("astrbot_host.core_reply_tracking", level="INFO") as logs:
            prepared = await self.core_turn(event)
            for _ in range(2):
                prepare_core_reply_tracking(event, prepared, admission=self.admission, now=self.clock.now,
                                            reason="question", mentioned=False)
                mark_core_reply_inflight(event)
            await self.core_send(event)
            saved = self.admission.recorded_core_reply(prepared["context"], prepared["event_id"])
            for _ in range(2):
                finish_core_reply_tracking(event)
                mark_core_reply_inflight(event)
            self.assertEqual(self.admission.recorded_core_reply(prepared["context"], prepared["event_id"]), saved)
        self.assertEqual(len(logs.output), 2)
        self.assertTrue(all("reason_source=policy" in line for line in logs.output))
        self.assertEqual(saved["send_attempts"], 1)
        self.assertEqual(saved["send_returns"], 1)
        self.assert_no_v2_generation()

    async def test_active_core_follow_up_excludes_other_speakers_and_expires_without_a_v2_task(self):
        first = self.event("long-task", "处理这个任务", mention=True)
        with patch.object(self.app, "accept", wraps=self.app.accept) as accept:
            prepared = await self.core_turn(first)
            accept.assert_not_called()
        self.clock.advance(timedelta(seconds=1))
        follow_up = self.event("follow-up", "再补充一点信息")
        self.assertEqual((await self.candidate(follow_up))["reason"], "active_follow_up")
        self.assertFalse((await self.candidate(self.event("other-speaker", "晚饭吃面", sender="201")))["admitted"])
        self.assertEqual(self.admission.recorded_core_reply(prepared["context"], prepared["event_id"])["state"], "inflight")
        self.clock.advance(timedelta(minutes=3))
        self.assertFalse((await self.candidate(self.event("stale-task", "再补充一点信息")))["admitted"])
        self.assertEqual(first.sent, [])
        self.assert_no_v2_generation()

    async def test_core_return_allows_same_speaker_follow_up_and_own_quote_in_the_original_group(self):
        first = self.event("first-reply", "说一句", mention=True)
        original_send = first.send
        async def native_send(message):
            await original_send(message)
            return {"message_id": "core-platform-id"}
        first.send = native_send
        await self.core_turn(first)
        self.assertEqual(await self.core_send(first), {"message_id": "core-platform-id"})
        self.clock.advance(timedelta(seconds=1))
        self.assertEqual((await self.candidate(self.event("natural-follow-up", "再补充一点")))["reason"], "reply_follow_up")
        self.assertFalse((await self.candidate(self.event("other", "晚饭吃面", sender="201")))["admitted"])
        self.assertEqual((await self.candidate(self.event("own-quote", "再补充一点", reply="core-platform-id")))["reason"], "quoted_reply")
        self.assertFalse((await self.candidate(self.event("wrong-group", "再补充一点", reply="core-platform-id", target="group-b")))["admitted"])
        native_quote = self.event("native-own-quote", "继续说", reply="id-not-returned-to-v2")
        native_quote.message_obj.message[0].sender_id = "100"
        self.assertEqual((await self.candidate(native_quote))["reason"], "native_quote")
        self.assert_no_v2_generation()
        self.clock.advance(timedelta(minutes=3))
        self.assertFalse((await self.candidate(self.event("later", "再补充一点")))["admitted"])

    async def test_core_return_allows_first_other_member_to_answer(self):
        first = self.event("bot-question", "问大家一个问题", mention=True)
        await self.core_turn(first)
        await self.core_send(first, "大家怎么看？")
        self.clock.advance(timedelta(seconds=2))
        answer = self.event("member-answer", "我觉得可以试试", sender="201")
        self.assertEqual((await self.candidate(answer))["reason"], "reply_follow_up")
        self.assert_no_v2_generation()

    async def test_silent_skip_on_follow_up_preserves_continuation_window_until_other_speaker_or_expiry(self):
        first = self.event("wake-anchor", "说一句", mention=True)
        await self.core_turn(first)
        await self.core_send(first, "你想聊哪一部分？")
        self.clock.advance(timedelta(seconds=2))
        intermediate = self.event("intermediate-follow-up", "先等一下")
        self.assertEqual((await self.candidate(intermediate))["reason"], "reply_follow_up")
        await self.core_turn(intermediate)
        prompt = "\n".join(part.text for part in intermediate.core_request.extra_user_content_parts)
        self.assertIn("机器人上一轮互动后的非点名接话候选", prompt)
        await self.choose(intermediate, "skip", reason="先旁听等对方说完")
        finish_core_reply_tracking(intermediate)
        await self.flush_tracking()
        self.clock.advance(timedelta(seconds=2))
        next_line = self.event("continued-after-skip", "我想先聊第一部分")
        self.assertEqual((await self.candidate(next_line))["reason"], "reply_follow_up")
        self.clock.advance(timedelta(seconds=1))
        await self.controller.observe_group_early(self.event("other-intervenes", "我插一句", sender="201"))
        self.clock.advance(timedelta(seconds=1))
        self.assertFalse((await self.candidate(self.event("after-other", "继续刚才的")))["admitted"])

    async def test_unknown_core_send_does_not_fall_back_to_an_older_reply_or_replay(self):
        first = self.event("known", "先说一句", mention=True)
        await self.core_turn(first)
        await self.core_send(first)
        self.clock.advance(timedelta(seconds=1))
        unknown = self.event("unknown", "再说一句", mention=True)
        attempted = []
        async def uncertain_send(message):
            attempted.append(message)
            unknown._has_send_oper = True
            raise TimeoutError("platform outcome unavailable")
        unknown.send = uncertain_send
        prepared = await self.core_turn(unknown)
        activate_core_reply_delivery_guard(unknown)
        with self.assertRaises(TimeoutError):
            await unknown.send("可能已送到平台的回复")
        await self.plugin.observe_companion_core_reply_sent(unknown)
        for _ in range(2):
            finish_core_reply_tracking(unknown)
            mark_core_reply_inflight(unknown)
        await self.flush_tracking()
        saved = self.admission.recorded_core_reply(prepared["context"], prepared["event_id"])
        self.assertEqual(saved["send_state"], "unknown")
        self.assertEqual(saved["unknown_sends"], 1)
        self.assertEqual(saved["platform_ids"], [])
        self.clock.advance(timedelta(seconds=1))
        self.assertFalse((await self.candidate(self.event("unknown-follow-up", "再补充一点")))["admitted"])
        self.assertEqual(attempted, ["可能已送到平台的回复"])
        self.assert_no_v2_generation()

    async def test_other_plugin_send_and_model_error_are_not_a_core_reply_receipt(self):
        event = self.event("plugin-notice", "说一句", mention=True)
        prepared = await self.core_turn(event, result_type="AGENT_RUNNER_ERROR")
        await event.send("其他插件进度消息")
        await self.core_send(event, "模型运行失败")
        saved = self.admission.recorded_core_reply(prepared["context"], prepared["event_id"])
        self.assertTrue(event._has_send_oper)
        self.assertEqual(saved["send_state"], "not_started")
        self.assertEqual(saved["send_attempts"], 0)
        self.clock.advance(timedelta(seconds=1))
        self.assertFalse((await self.candidate(self.event("no-final-reply", "再补充一点")))["admitted"])
        self.assert_no_v2_generation()

    async def test_agent_done_without_final_send_closes_inflight_and_later_delivery_reopens(self):
        event = self.event("empty-agent", "说一句", mention=True)
        prepared = await self.core_turn(event)
        await self.plugin.observe_companion_core_agent_done(event, None, NS(role="assistant", completion_text=""))
        await self.flush_tracking()
        saved = self.admission.recorded_core_reply(prepared["context"], prepared["event_id"])
        self.assertEqual((saved["state"], saved["send_attempts"]), ("completed", 0))
        self.clock.advance(timedelta(seconds=1))
        self.assertFalse((await self.candidate(self.event("empty-agent-follow-up", "再补充一点")))["admitted"])
        await self.core_send(event, "核心装饰完成后的回复")
        saved = self.admission.recorded_core_reply(prepared["context"], prepared["event_id"])
        self.assertEqual((saved["state"], saved["send_returns"]), ("completed", 1))

    async def test_streaming_core_reply_finishes_without_a_decorating_or_after_sent_hook(self):
        event = self.event("stream", "说一句", mention=True)

        async def native_stream(generator, *args, **kwargs):
            async for chunk in generator:
                await event.send(chunk)

        async def chunks():
            yield MessageChain(chain=[Plain("核心流式回复")])

        event.send_streaming = native_stream
        prepared = await self.core_turn(event, result_type="STREAMING_RESULT")
        self.assertFalse(event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["delivery_guard_active"])
        await event.send_streaming(chunks(), False)
        await self.flush_tracking()
        saved = self.admission.recorded_core_reply(prepared["context"], prepared["event_id"])
        self.assertEqual(saved["state"], "completed")
        self.assertEqual(saved["send_state"], "returned")
        self.assertEqual(saved["platform_ids"], [])
        self.clock.advance(timedelta(seconds=1))
        self.assertEqual((await self.candidate(self.event("stream-follow-up", "再补充一点")))["reason"], "reply_follow_up")
        self.assert_no_v2_generation()

    async def test_native_tool_rounds_rearm_tracking_after_each_sent_phase(self):
        event = self.event("multi-phase", "先解释再查工具", mention=True)
        prepared = await self.core_turn(event)
        await self.core_send(event, "我先看看")
        self.clock.advance(timedelta(seconds=1))
        await self.core_send(event, "查到了，接着说")
        saved = self.admission.recorded_core_reply(prepared["context"], prepared["event_id"])
        self.assertEqual(saved["send_attempts"], 2)
        self.assertEqual(saved["send_returns"], 2)
        self.assertEqual(event.sent, ["我先看看", "查到了，接着说"])
        self.assertEqual(saved["state"], "completed")
        self.assert_no_v2_generation()

    async def test_direct_native_tool_sends_record_ids_and_rearm_for_final_reply(self):
        event = self.event("direct-tool-reply", "先发图片再解释", mention=True)
        prepared = await self.core_turn(event)
        observe_core_reply_tool_delivery(event, "started")
        await self.flush_tracking()
        inflight = self.admission.recorded_core_reply(prepared["context"], prepared["event_id"])
        self.assertEqual(inflight["state"], "inflight")
        observe_core_reply_tool_delivery(event, "returned", {"message_id": "tool-image-id"})
        await self.flush_tracking()
        saved = self.admission.recorded_core_reply(prepared["context"], prepared["event_id"])
        self.assertEqual((saved["send_attempts"], saved["send_returns"]), (1, 1))
        self.assertEqual(saved["platform_ids"], ["tool-image-id"])
        self.assertEqual(saved["state"], "completed")
        self.assertEqual(event.sent, [])
        self.clock.advance(timedelta(seconds=1))
        self.assertEqual((await self.candidate(self.event("tool-follow-up", "再补充一点")))["reason"],
                         "reply_follow_up")
        self.assertEqual((await self.candidate(self.event("tool-quote", "看看", reply="tool-image-id")))["reason"],
                         "quoted_reply")
        observe_core_reply_tool_delivery(event, "started")
        observe_core_reply_tool_delivery(event, "returned", {"data": {"message_id": "tool-second-id"}})
        await self.core_send(event, "图片里是这样的")
        saved = self.admission.recorded_core_reply(prepared["context"], prepared["event_id"])
        self.assertEqual((saved["send_attempts"], saved["send_returns"]), (3, 3))
        self.assertEqual(saved["platform_ids"], ["tool-image-id", "tool-second-id"])
        self.assertEqual(event.sent, ["图片里是这样的"])
        self.assertEqual(saved["state"], "completed")
        self.assert_no_v2_generation()

    async def test_direct_native_tool_unknown_is_preserved_after_final_send(self):
        event = self.event("direct-tool-unknown", "发图片再解释", mention=True)
        prepared = await self.core_turn(event)
        observe_core_reply_tool_delivery(event, "started")
        observe_core_reply_tool_delivery(event, "unknown")
        await self.flush_tracking()
        saved = self.admission.recorded_core_reply(prepared["context"], prepared["event_id"])
        self.assertEqual(saved["state"], "completed")
        self.assertEqual(saved["send_state"], "unknown")
        self.assertEqual((saved["send_attempts"], saved["send_returns"], saved["unknown_sends"]), (1, 0, 1))
        self.assertEqual(saved["platform_ids"], [])
        self.assertEqual(event.sent, [])
        self.clock.advance(timedelta(seconds=1))
        self.assertFalse((await self.candidate(self.event("unknown-tool-follow-up", "再补充一点")))["admitted"])
        await self.core_send(event, "继续说明")
        saved = self.admission.recorded_core_reply(prepared["context"], prepared["event_id"])
        self.assertEqual(saved["send_state"], "unknown")
        self.assertEqual((saved["send_attempts"], saved["send_returns"], saved["unknown_sends"]), (2, 1, 1))
        self.assertEqual(event.sent, ["继续说明"])
        self.assert_no_v2_generation()

    async def test_direct_native_tool_denied_does_not_invent_a_send_attempt(self):
        event = self.event("direct-tool-denied", "发张图片", mention=True)
        prepared = await self.core_turn(event)
        observe_core_reply_tool_delivery(event, "denied")
        await self.flush_tracking()
        saved = self.admission.recorded_core_reply(prepared["context"], prepared["event_id"])
        self.assertEqual(saved["state"], "completed")
        self.assertEqual(saved["send_state"], "denied")
        self.assertEqual((saved["send_attempts"], saved["send_returns"], saved["unknown_sends"]), (0, 0, 0))
        self.assertEqual(saved["platform_ids"], [])
        self.assertEqual(event.sent, [])
        self.assert_no_v2_generation()

    async def test_stream_envelope_and_its_native_send_are_counted_once(self):
        event = self.event("nested-stream", "说一句", mention=True)
        async def native_stream(*args, **kwargs):
            await event.send("流式回退的原生文本")
        event.send_streaming = native_stream
        prepared = await self.core_turn(event, result_type="STREAMING_RESULT")
        await event.send_streaming("native-stream", False)
        await self.flush_tracking()
        saved = self.admission.recorded_core_reply(prepared["context"], prepared["event_id"])
        self.assertEqual(saved["send_attempts"], 1)
        self.assertEqual(saved["send_returns"], 1)
        self.assertEqual(event.sent, ["流式回退的原生文本"])
        self.assert_no_v2_generation()

    async def test_direct_native_tool_known_failure_does_not_create_a_return_or_unknown(self):
        event = self.event("direct-tool-failed", "发图片再解释", mention=True)
        prepared = await self.core_turn(event)
        observe_core_reply_tool_delivery(event, "started")
        observe_core_reply_tool_delivery(event, "failed")
        await self.flush_tracking()
        saved = self.admission.recorded_core_reply(prepared["context"], prepared["event_id"])
        self.assertEqual(saved["state"], "completed")
        self.assertEqual(saved["send_state"], "failed")
        self.assertEqual((saved["send_attempts"], saved["send_returns"], saved["unknown_sends"], saved["send_failed"]),
                         (1, 0, 0, 1))
        self.assertEqual(event.sent, [])
        self.clock.advance(timedelta(seconds=1))
        self.assertFalse((await self.candidate(self.event("failed-tool-follow-up", "再补充一点")))["admitted"])
        await self.core_send(event, "图片没发出去，先继续说明")
        saved = self.admission.recorded_core_reply(prepared["context"], prepared["event_id"])
        self.assertEqual(saved["send_state"], "returned")
        self.assertEqual((saved["send_attempts"], saved["send_returns"], saved["unknown_sends"], saved["send_failed"]),
                         (2, 1, 0, 1))
        self.assertEqual(event.sent, ["图片没发出去，先继续说明"])
        self.assert_no_v2_generation()

    async def test_reset_excludes_late_core_send_from_quote_and_natural_follow_up(self):
        event = self.event("before-reset", "说一句", mention=True)
        original_send = event.send
        async def native_send(message):
            await original_send(message)
            return {"message_id": "before-reset-id"}
        event.send = native_send
        prepared = await self.core_turn(event)
        self.clock.advance(timedelta(seconds=1))
        window = ConversationWindow.from_scope(prepared["context"].scope)
        reset_window(self.app.runtime, window, reset_id="core-reset", at=self.clock.now())
        self.admission.reset_window(window)
        self.clock.advance(timedelta(seconds=1))
        await self.core_send(event)
        self.assertFalse((await self.candidate(self.event("after-reset", "再补充一点")))["admitted"])
        self.assertFalse((await self.candidate(self.event("reset-quote", "再补充一点", reply="before-reset-id")))["admitted"])
        self.assert_no_v2_generation()

    async def test_optional_tracking_storage_failure_does_not_block_native_core_send(self):
        event = self.event("storage-failure", "说一句", mention=True)
        await self.core_turn(event)
        with patch.object(self.admission, "mark_core_reply", side_effect=OSError("optional tracking unavailable")):
            await self.core_send(event)
        self.assertEqual(event.sent, ["可以先看看设置。"])
        self.assert_no_v2_generation()

    async def test_default_followup_fuse_counts_events_and_blocks_native_fallback_and_weak_wakes(self):
        first = self.event("fuse-root", "先说一句", mention=True)
        await self.core_turn(first)
        await self.core_send(first)
        for index in range(3):
            self.clock.advance(timedelta(seconds=1))
            event = self.event("fuse-turn-" + str(index), "接着聊这一点")
            candidate = await self.candidate(event)
            self.assertEqual(candidate["reason"], "reply_follow_up")
            self.assertEqual(candidate["continuation"]["used_turns"], index)
            prepared = await self.core_turn(event)
            await self.core_send(event, "第一段")
            await self.core_send(event, "同轮工具处理后的第二段")
            saved = self.admission.recorded_core_reply(prepared["context"], prepared["event_id"])
            self.assertEqual(saved["send_returns"], 2)
        self.clock.advance(timedelta(seconds=1))
        blocked = self.event("fuse-blocked", "这个问题应该怎么处理？")
        blocked.is_at_or_wake_command = True
        candidate = await self.candidate(self.event("fuse-status", "接着聊"))
        self.assertFalse(candidate["admitted"])
        self.assertEqual(candidate["continuation"]["used_turns"], 3)
        self.assertTrue(candidate["continuation"]["limit_reached"])
        # Keep the native wake flag off during admission, then simulate a
        # later host stage setting it; the fuse must still prevent fallback.
        blocked.is_at_or_wake_command = False
        await self.plugin.receive_companion_message(blocked)
        blocked.is_at_or_wake_command = True
        self.assertTrue(blocked.call_llm)
        self.assertIsNone(blocked.get_extra(CORE_REPLY_CONTEXT_EXTRA))
        stage = ProcessStage()
        stage.ctx = NS(astrbot_config=DEFAULT_CONFIG)
        async for _ in stage.process(blocked):
            self.fail("the native model must not run after the follow-up fuse")
        self.assertEqual(blocked.sent, [])
        self.assert_no_v2_generation()

    async def test_explicit_wakeup_reply_reopens_capped_chain_and_other_groups_are_independent(self):
        await self.configure(group_conversation_followup_max_turns=1, group_wakeup_bot_name="小星")
        root_prepared = None
        for identifier, text, mention in (("one-root", "开始", True), ("one-follow", "接着说", False)):
            event = self.event(identifier, text, mention=mention)
            prepared = await self.core_turn(event)
            if mention:
                root_prepared = prepared
            await self.core_send(event)
            self.clock.advance(timedelta(seconds=1))
        self.assertFalse((await self.candidate(self.event("one-blocked", "接着说")))["admitted"])
        other = await self.candidate(self.event("another-group", "接着说", target="group-b"))
        self.assertFalse(other["continuation"]["limit_reached"])
        original_binding = root_prepared["context"].role_binding
        other_role = dc_replace(original_binding.role, subject_id="synthetic-other-role")
        other_context = dc_replace(root_prepared["context"],
                                   role_binding=dc_replace(original_binding, role=other_role))
        role_status = self.admission.continuation_status(other_context, root_prepared["instruction"],
            self.clock.now(), settings={}, event_id="different-role-turn")
        self.assertEqual(role_status["used_turns"], 0)
        named = self.event("name-reopens", "小星，这次继续")
        self.assertEqual((await self.candidate(named))["reason"], "direct_word")
        await self.core_turn(named)
        await self.core_send(named)
        self.clock.advance(timedelta(seconds=1))
        follow = await self.candidate(self.event("reopened", "然后呢"))
        self.assertEqual(follow["reason"], "reply_follow_up")
        self.assertEqual(follow["continuation"]["used_turns"], 0)

    async def test_skip_and_laya_wait_do_not_consume_or_extend_sent_followup_chain(self):
        await self.configure(group_conversation_followup_max_turns=1, group_conversation_followup_seconds=15)
        first = self.event("wait-root", "开始", mention=True)
        await self.core_turn(first)
        await self.core_send(first)
        self.clock.advance(timedelta(seconds=2))
        skipped = self.event("skip-no-charge", "先等一下")
        await self.core_turn(skipped)
        await self.choose(skipped, "skip", reason="等对方补充")
        finish_core_reply_tracking(skipped)
        await self.flush_tracking()
        self.clock.advance(timedelta(seconds=2))
        waiting = self.event("wait-no-charge", "我还没说完")
        prepared = await self.core_turn(waiting)
        self.admission.mark_core_participation(prepared["context"], prepared["event_id"],
            mode="skip", reason="等待补充", source="laya", decision_action="wait")
        finish_core_reply_tracking(waiting)
        await self.flush_tracking()
        self.clock.advance(timedelta(seconds=2))
        fresh = await self.candidate(self.event("after-wait", "这一部分"))
        self.assertEqual(fresh["reason"], "active_follow_up")
        self.assertEqual(fresh["continuation"]["used_turns"], 0)
        self.assertEqual(fresh["continuation"]["pending_turns"], 0)
        self.clock.advance(timedelta(seconds=10))
        self.assertFalse((await self.candidate(self.event("wait-expired", "这一部分")))["admitted"])

    async def test_pending_followup_reserves_last_turn_but_a_failed_send_releases_it(self):
        await self.configure(group_conversation_followup_max_turns=1)
        root = self.event("pending-root", "开始", mention=True)
        await self.core_turn(root)
        await self.core_send(root)
        self.clock.advance(timedelta(seconds=1))
        pending = self.event("pending-last", "接着说")
        prepared = await self.core_turn(pending)
        candidate = await self.candidate(self.event("while-pending", "再补一条"))
        self.assertEqual(candidate["continuation"]["used_turns"], 0)
        self.assertEqual(candidate["continuation"]["pending_turns"], 1)
        self.assertFalse(candidate["admitted"])
        observe_core_reply_tool_delivery(pending, "started")
        observe_core_reply_tool_delivery(pending, "failed")
        await self.flush_tracking()
        candidate = await self.candidate(self.event("after-failure", "再补一条"))
        self.assertEqual(candidate["continuation"]["used_turns"], 0)
        self.assertEqual(candidate["continuation"]["pending_turns"], 0)
        self.assertFalse(candidate["continuation"]["limit_reached"])
        self.assertFalse(candidate["admitted"])
        self.assertEqual(self.admission.recorded_core_reply(prepared["context"], prepared["event_id"])["send_state"], "failed")

    async def test_fuse_state_unavailable_suppresses_default_reply_instead_of_failing_open(self):
        event = self.event("fuse-state-unavailable", "接着说")
        with patch.object(self.admission, "continuation_status", side_effect=OSError("policy state unavailable")):
            await self.plugin.receive_companion_message(event)
        event.is_at_or_wake_command = True
        self.assertTrue(event.call_llm)
        self.assertIsNone(event.get_extra(CORE_REPLY_CONTEXT_EXTRA))
        stage = ProcessStage()
        stage.ctx = NS(astrbot_config=DEFAULT_CONFIG)
        async for _ in stage.process(event):
            self.fail("an unavailable follow-up fuse must not fall through to AstrBot")

    async def test_followup_switch_and_seconds_are_effective(self):
        await self.configure(group_conversation_followup_enabled=False)
        root = self.event("disabled-root", "开始", mention=True)
        await self.core_turn(root)
        await self.core_send(root)
        self.clock.advance(timedelta(seconds=1))
        self.assertFalse((await self.candidate(self.event("disabled-follow", "接着说")))["admitted"])
        await self.configure(group_conversation_followup_enabled=True, group_conversation_followup_seconds=15)
        self.clock.advance(timedelta(seconds=13))
        self.assertEqual((await self.candidate(self.event("inside-window", "接着说")))["reason"], "reply_follow_up")
        self.clock.advance(timedelta(seconds=2))
        self.assertFalse((await self.candidate(self.event("outside-window", "接着说")))["admitted"])


if __name__ == "__main__":
    unittest.main()
