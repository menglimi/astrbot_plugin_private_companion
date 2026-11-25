"""Host prompt integration: one role sees its own private/group windows."""

from datetime import timedelta
import json
from types import SimpleNamespace
import unittest

from companion_v2.execution.models import TaskContext
from tests.group_host_support import GroupFixture
from tests.staging_bootstrap_support import wait_for_batch


class RoleAwarenessHostTests(GroupFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await self.start_host()

    @property
    def app(self):
        return self.controller.session.application

    async def prepare(self, event):
        prepared = (await self.controller.prepare_core_reply(event)).unwrap()
        future = prepared.get("native_source_future")
        if future is not None:
            await future
        return prepared

    async def accepted_group(self, identifier, text):
        result = await self.controller.receive_claimed(
            self.group_event(identifier=identifier, sender="200", text=text))
        batch = result.unwrap()
        await wait_for_batch(self.controller.session, batch)
        context = TaskContext.restore(json.loads(self.app.messages.batch(batch)["context_json"]))
        return context, batch

    def materials(self, context, batch):
        request = self.app.messages._request(self.app.messages.batch(batch), context, self.clock.now())
        return request.context.materials

    async def test_private_prompt_sees_same_persons_group_activity(self):
        group = await self.prepare(self.group_event(identifier="aware-group", sender="200", text="明天谁有空"))
        self.assertIn("当前是群聊", group["prompt_instruction"])
        private = await self.prepare(self.event(target="first", identifier="aware-private", text="我刚在群里问了"))
        self.assertIn("【跨窗口在场】", private["prompt_instruction"])
        self.assertIn("明天谁有空", private["prompt_instruction"])

    async def test_private_repeat_gets_prior_group_reply_continuity(self):
        group_context, _ = await self.accepted_group(
            "continuity-group", "周末去杭州还是苏州？")
        with self.app.runtime.transaction() as connection:
            self.app.cross_window.record_reply(
                connection, group_context, self.clock.now(),
                text="我倾向杭州，先看周六天气。", event_key="continuity-reply",
                enabled=True)
        private_context, private_batch = await self.accepted(
            "first", "continuity-private", "周末去杭州还是苏州？")
        material = next(item for item in self.materials(private_context, private_batch)
                        if item.purpose == "cross_window_awareness")
        payload = json.loads(material.payload)
        self.assertEqual(payload["continuity"]["state"], "repeat")
        self.assertEqual(payload["continuity"]["prior_question"], "周末去杭州还是苏州？")
        self.assertIn("杭州", payload["continuity"]["prior_reply"])

    async def test_private_unrelated_question_has_no_repeat_continuity(self):
        group_context, _ = await self.accepted_group(
            "continuity-unrelated-group", "周末去杭州还是苏州？")
        with self.app.runtime.transaction() as connection:
            self.app.cross_window.record_reply(
                connection, group_context, self.clock.now(),
                text="我倾向杭州。", event_key="continuity-unrelated-reply",
                enabled=True)
        private_context, private_batch = await self.accepted(
            "first", "continuity-unrelated-private", "今晚吃什么？")
        material = next(item for item in self.materials(private_context, private_batch)
                        if item.purpose == "cross_window_awareness")
        payload = json.loads(material.payload)
        self.assertNotIn("continuity", payload)

    async def test_continuity_respects_its_short_window(self):
        group_context, _ = await self.accepted_group(
            "continuity-expiry-group", "周末去杭州还是苏州？")
        with self.app.runtime.transaction() as connection:
            self.app.cross_window.record_reply(
                connection, group_context, self.clock.now(),
                text="我倾向杭州。", event_key="continuity-expiry-reply",
                enabled=True)
        self.clock.advance(timedelta(minutes=31))
        private_context, private_batch = await self.accepted(
            "first", "continuity-expiry-private", "周末去杭州还是苏州？")
        material = next(item for item in self.materials(private_context, private_batch)
                        if item.purpose == "cross_window_awareness")
        payload = json.loads(material.payload)
        self.assertNotIn("continuity", payload)

    async def test_core_private_prompt_gets_repeat_continuity(self):
        group_context, _ = await self.accepted_group(
            "continuity-prompt-group", "周末去杭州还是苏州？")
        with self.app.runtime.transaction() as connection:
            self.app.cross_window.record_reply(
                connection, group_context, self.clock.now(),
                text="我倾向杭州，先看周六天气。", event_key="continuity-prompt-reply",
                enabled=True)
        private = await self.prepare(self.event(
            target="first", identifier="continuity-prompt-private",
            text="周末去杭州还是苏州？"))
        self.assertIn('"prior_reply":"我倾向杭州', private["prompt_instruction"])

    async def test_group_prompt_only_gets_the_private_contact_boolean(self):
        await self.prepare(self.event(target="first", identifier="aware-private-seed", text="私聊里的秘密"))
        group = await self.prepare(self.group_event(identifier="aware-group-after", sender="200", text="在群里说话"))
        self.assertIn("【跨窗口在场】", group["prompt_instruction"])
        self.assertIn("private_contact", group["prompt_instruction"])
        self.assertNotIn("私聊里的秘密", group["prompt_instruction"])

    async def test_group_prompt_adds_window_transition_on_a_short_directed_message(self):
        await self.prepare(self.event(target="first", identifier="awareness-pinch-private", text="捏"))
        self.clock.advance(timedelta(seconds=72))
        event = self.group_event(identifier="awareness-pinch-group", text="捏")
        event.message_obj.message.insert(0, SimpleNamespace(type="At", qq="100"))
        group = await self.prepare(event)
        self.assertIn('"recent_window_transition":true', group["prompt_instruction"])

        self.clock.advance(timedelta(seconds=1))
        normal = await self.prepare(self.group_event(identifier="awareness-pinch-unrelated", text="捏个饭团"))
        self.assertNotIn('"recent_window_transition":true', normal["prompt_instruction"])

    async def test_private_prompt_sees_a_bounded_peer_count(self):
        await self.prepare(self.group_event(identifier="aware-peer-first", sender="201", text="我先说一句"))
        await self.prepare(self.group_event(identifier="aware-peer-second", sender="200", text="我也来"))
        private = await self.prepare(self.event(target="first", identifier="aware-peer-private",
                                                text="我刚在群里接话"))
        self.assertIn("【跨窗口在场】", private["prompt_instruction"])
        self.assertIn('"peers":1', private["prompt_instruction"])

    async def test_v2_batches_seal_the_same_audience_filtered_view(self):
        group_context, group_batch = await self.accepted_group("aware-v2-group", "明天谁有空")
        private_context, private_batch = await self.accepted("first", "aware-v2-private", "私聊里的秘密")
        private_material = next(item for item in self.materials(private_context, private_batch)
                                if item.purpose == "cross_window_awareness")
        payload = json.loads(private_material.payload)
        self.assertEqual(payload["recent_group_activity"][0]["topic"], "明天谁有空")

        self.clock.advance(timedelta(minutes=1))
        group_context, group_batch = await self.accepted_group("aware-v2-group-after", "在群里说话")
        group_material = next(item for item in self.materials(group_context, group_batch)
                              if item.purpose == "cross_window_awareness")
        self.assertEqual(json.loads(group_material.payload), {"private_contact": True, "recent_window_transition": True})
        self.assertNotIn("私聊里的秘密", group_material.payload)
