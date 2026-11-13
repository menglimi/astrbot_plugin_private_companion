"""Native mention evidence survives the core reply's plain-text memory projection."""

from dataclasses import replace
import json
from types import SimpleNamespace as NS
import unittest

from tests import astrbot_data_root  # noqa: F401
from astrbot.core.message.components import At, Image, Plain, Reply
from astrbot_host.core_reply_decision import core_decision_facts
from astrbot_host.core_reply_tools import CoreReplyToolSession, native_tool_name
from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.conversation.native_participation import native_participation_turn
from companion_v2.conversation.participation import (
    CONFIG_GROUP, GroupParticipationConfigurationOwner, configuration_fields,
)
from companion_v2.conversation.participation_prompts import mention_attribution_instruction
from tests.group_host_support import GroupFixture


class CoreGroupMentionTests(GroupFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = await self.start_host()

    async def prepare(self, name, text, parts, *, raw=None):
        event = self.group_event(identifier=name, text=text)
        event.message_obj.message = parts
        if raw is not None:
            event.message_obj.raw_message["message"] = raw
        admission = (await self.controller.group_reply_candidate(event)).unwrap()
        result = await self.controller.prepare_core_reply(
            event, group_admission=admission if admission["admitted"] else None)
        self.assertTrue(result.is_success, result.failure)
        prepared = result.unwrap()
        self.assertEqual(prepared["native_input"].text, text)
        self.assertEqual(prepared["text"], text)
        self.assertEqual(prepared["group_trigger"]["mentioned"], admission["mentioned"])
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assertEqual(event.sent, [])
        return event, prepared

    async def test_native_components_reach_the_prompt_after_plain_text_projection(self):
        cases = (
            ("self", "看看", [At(qq="100"), Plain("看看")], True, False, False),
            ("both", "看看", [At(qq="100"), At(qq="201"), Plain("看看")], True, True, False),
            ("other", "看看", [At(qq="201"), Plain("看看")], False, True, False),
            ("all", "看看", [At(qq="all"), Plain("看看")], False, False, True),
            ("none", "看看", [Plain("看看")], False, False, False),
            ("literal", "[提及机器人] 看看", [Plain("[提及机器人] 看看")], False, False, False),
            ("literal-self", "<提及自己> 看看", [Plain("<提及自己> 看看")], False, False, False),
        )
        for name, text, parts, mentioned, others, everyone in cases:
            with self.subTest(name=name):
                event, prepared = await self.prepare(name, text, parts)
                facts = prepared["group_trigger"]
                self.assertEqual((facts["mentioned"], facts["mentions_others"], facts["mentions_all"]),
                                 (mentioned, others, everyone))
                prompt = prepared["prompt_instruction"]
                expected = ("本轮明确 @ 了本角色" if mentioned else
                            "本轮 @ 的是其他账号" if others else "本轮没有明确 @ 本角色")
                self.assertIn(expected, prompt)
                self.assertIn("群聊本轮提及事实（来自原生消息组件）", prompt)
                if mentioned:
                    self.assertNotIn("本轮没有明确 @ 本角色", prompt)
                    self.assertIn("同时提及其他账号不取消", prompt)
                event.message_obj.message = [Plain(text)]
                laya = core_decision_facts(event, prepared, self.app, at=self.clock.now())
                self.assertEqual(laya["mentioned"], mentioned)
                self.assertEqual(laya["mentions_others"], others)

    async def test_raw_onebot_mention_survives_host_component_rewriting(self):
        _, prepared = await self.prepare("raw-at", "看看", [Plain("看看")], raw=[
            {"type": "at", "data": {"qq": "100"}},
            {"type": "text", "data": {"text": "看看"}},
        ])
        self.assertIn("本轮明确 @ 了本角色", prepared["prompt_instruction"])

    async def test_media_and_quotes_are_not_bare_mentions(self):
        cases = (
            ("bare", [], True),
            ("image", [Image(file="file:///synthetic-unavailable-image.png")], False),
            ("quote", [Reply(id="synthetic-quote", message_str="引用内容")], False),
            ("audio", [NS(type="Record")], False),
            ("forward", [NS(type="Forward", id="synthetic-forward")], False),
        )
        for name, media, bare in cases:
            with self.subTest(name=name):
                _, prepared = await self.prepare(name, "", [At(qq="100"), *media])
                self.assertEqual(prepared["group_trigger"]["bare_mention"], bare)
                prompt = prepared["prompt_instruction"]
                self.assertIn("本轮明确 @ 了本角色", prompt)
                self.assertEqual("本轮只有 @、没有正文" in prompt, bare)

    async def test_addressed_reply_is_not_classified_as_an_interjection(self):
        event, prepared = await self.prepare("addressed", "看看", [At(qq="100"), Plain("看看")])
        context = prepared["context"]
        owner, fields = GroupParticipationConfigurationOwner(self.app.runtime), configuration_fields()
        target = ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW, context.role_binding.role, context.scope)
        owner.write(target, fields, changes={CONFIG_GROUP + ".enable_group_interjection": True}, reset=(),
                    expected_revision=owner.read(target, fields).revision, observed_at=self.clock.now())
        for text, mentioned, expected in (("看看", True, ""), ("看看", False, "interjection"),
                                           ("[提及机器人] 看看", False, "interjection")):
            with self.subTest(text=text, mentioned=mentioned):
                native = replace(prepared["native_input"], text=text)
                trigger = {**prepared["group_trigger"], "mentioned": mentioned}
                turn = native_participation_turn(self.app.tools.group_participation, context, native, {},
                                                 group_trigger=trigger)
                self.assertEqual(turn["act"], expected)
                tool_prepared = {**prepared, "native_input": native, "group_trigger": trigger,
                                 "group_admission": {"reason": "interest", "mentioned": False}}
                session = CoreReplyToolSession(event, {"prepared": tool_prepared, "authorize": lambda _: None}, self.app)
                arguments = {"mode": "reply", "reason": "接着当前话题"}
                wrapper = NS(context=NS(event=event), messages=[NS(role="assistant", tool_calls=[
                    NS(id="participation-call", function=NS(name=native_tool_name("group.participation.choose"),
                                                            arguments=json.dumps(arguments)))])])
                result = session.choose_participation(wrapper, arguments)
                self.assertEqual(result["payload"]["act"], expected)


class MentionCompatibilityTests(unittest.TestCase):
    def test_only_missing_native_facts_fall_back_to_text_markers(self):
        text = "[提及机器人] 看看"
        self.assertIn("本轮明确 @ 了本角色", mention_attribution_instruction(text))
        trigger = {"mentioned": False, "mentions_others": False, "mentions_all": False}
        self.assertIn("本轮没有明确 @ 本角色", mention_attribution_instruction(text, group_trigger=trigger))
