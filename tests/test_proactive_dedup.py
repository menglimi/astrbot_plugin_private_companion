"""Receipt-backed duplicate decisions and foreground reply ancestry."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from threading import Event, current_thread, main_thread
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from companion_v2.conversation.date_context import WEEKDAYS, period_label
from companion_v2.conversation.segmentation import SegmentationPolicy
from companion_v2.conversation.foreground import BackgroundPromptTrigger
from companion_v2.conversation.flow import ConversationPolicy
from companion_v2.execution.models import TaskError
from companion_v2.proactive.configuration import configuration_fields, dedup_policies
from companion_v2.proactive.prompts import news_presentation_instruction
from companion_v2.proactive.preferences import DEDUP_EVENT_POLICY, DEDUP_PURPOSE_POLICY, ContactPreferences
from tests.contact_schedule_support import ContactScheduleFixture
from tests.test_management_access import NOW
from tests.test_role_tool_loop import answered


class ProactiveDedupTests(ContactScheduleFixture, unittest.IsolatedAsyncioTestCase):
    async def test_afternoon_contact_keeps_yesterdays_reply_as_context_beyond_dedup_window(self):
        self.clock.advance(timedelta(hours=12, minutes=8))

        async def model(envelope):
            return answered('{"decision":"skip","text":""}'
                if isinstance(envelope.original.event, BackgroundPromptTrigger)
                else "怎么会不理你呢？昨晚已经回复你了。")

        self.model_behavior = model
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 0)))
        app = host.application
        self.settings(app, {"enabled": True}).unwrap()
        batch = host.receive(self.event("yesterdays-answered-message", text="不理我")).unwrap()
        await self.done(app, batch)
        self.clock.advance(timedelta(hours=19, minutes=1))

        opportunity = self.opportunity("afternoon-greeting")
        await self.contact(app, opportunity, (self.intent(opportunity, purpose="greeting"),))
        request = self.contact_calls()[-1].original
        intent = json.loads(next(item.payload for item in request.context.materials
                                 if item.purpose == "contact_intent"))
        self.assertEqual((intent["local_date"], intent["local_time"]), ("2026-09-22", "15:09"))
        dialogue = [json.loads(item.payload) for item in request.context.materials
                    if item.purpose == "recent_private_dialogue"]
        self.assertEqual([(item["role"], item["text"])
                          for item in dialogue[0]["messages"]], [
            ("user", "不理我"), ("assistant", "怎么会不理你呢？昨晚已经回复你了。")])
        self.assertEqual(len(dialogue), 1)
        self.assertNotIn("last_confirmed_reply", {item.purpose for item in request.context.materials})
        self.assertIn("不要把旧消息当成仍待回复的内容重新接续", request.instruction)
        self.assertIn("不要为了补齐问候而重新接续昨晚已回复的消息", request.instruction)
        self.assertIn("role=user 的旧发言不是本轮新请求", request.instruction)

        self.settings(app, {"generation_history_limit": 0, "dedup_enabled": False}).unwrap()
        await self.contact(app, self.opportunity("without-history"))
        self.assertFalse(any(item.owner == "delivery.history"
                             for item in self.contact_calls()[-1].original.context.materials))
        self.assertFalse(any(item.owner == "conversation.private_history"
                             for item in self.contact_calls()[-1].original.context.materials))

    async def test_confirmed_exact_repeat_skips_with_reason_and_policy_can_exempt_route(self):
        app = await self.enabled()
        first = (await self.contact(app)).unwrap().task
        await self.delivery_state(app, first.task_id, "delivered")
        self.advance(app, 1)
        second = (await self.contact(app, self.opportunity("second"))).unwrap().task
        data = json.loads(app.runtime.read_one("SELECT record_json FROM proactive_contacts WHERE task_id = ?",
                                              (second.task_id,))[0])
        self.assertEqual((data["decision"], data["dedup_reason"]), ("skip", "exact_repeat"))
        self.assertEqual(app.proactive.observe(self.contact_context(), second.task_id).unwrap()["dedup_reason"], "exact_repeat")
        self.assertEqual(len(self.sent), 1)
        self.settings(app, {"dedup_policies": "content_fingerprint,life_event"}).unwrap()
        third = (await self.contact(app, self.opportunity("third"))).unwrap().task
        await self.delivery_state(app, third.task_id, "delivered")
        self.assertEqual(len(self.sent), 2)

    async def test_window_zero_is_unbounded_and_expired_window_allows_repeat(self):
        app = await self.enabled()
        first = (await self.contact(app)).unwrap().task
        await self.delivery_state(app, first.task_id, "delivered")
        context = self.contact_context()
        self.clock.advance(timedelta(minutes=241))
        prefs = self.preferences(app)
        self.assertFalse(app.proactive._duplicate(context, "刚想起上次的话题。", prefs, self.clock.now(), purpose="chat"))
        self.settings(app, {"dedup_sent_window_minutes": 0}).unwrap()
        prefs = self.preferences(app)
        self.assertTrue(app.proactive._duplicate(context, "刚想起上次的话题。", prefs, self.clock.now(), purpose="chat"))

    async def test_prompt_only_marks_in_window_proactive_history_for_dedup(self):
        app = await self.enabled()
        first = (await self.contact(app)).unwrap().task
        await self.delivery_state(app, first.task_id, "delivered")
        self.clock.advance(timedelta(minutes=241))
        second = (await self.contact(app, self.opportunity("after-window"))).unwrap().task
        await self.delivery_state(app, second.task_id, "delivered")
        past = [m for m in self.contact_calls()[-1].original.context.materials
                if m.owner == "delivery.history"]
        self.assertEqual([m.purpose for m in past], ["confirmed_background_message"])
        self.settings(app, {"dedup_sent_window_minutes": 0}).unwrap()
        await self.contact(app, self.opportunity("unbounded"))
        recent = [m for m in self.contact_calls()[-1].original.context.materials
                  if m.owner == "delivery.history"]
        self.assertEqual([m.purpose for m in recent], ["dedup_candidate", "dedup_candidate"])

    async def test_unresolved_blocks_repeat_but_failed_or_other_window_does_not(self):
        app = await self.enabled()
        with patch.object(app.background, "advance", return_value=False):
            first = (await self.contact(app)).unwrap().task
        context = self.contact_context()
        prefs = self.preferences(app)
        self.assertTrue(app.proactive._duplicate(context, "刚想起上次的话题。", prefs, self.clock.now(), purpose="chat"))
        other = self.other_window_context()
        self.assertFalse(app.proactive._duplicate(other, "刚想起上次的话题。",
                                                  self.preferences(app, other), self.clock.now(), purpose="chat"))
        app.proactive.cancel(context, first.task_id, expected_revision=first.revision).unwrap()
        self.assertFalse(app.proactive._duplicate(context, "刚想起上次的话题。", prefs, self.clock.now(), purpose="chat"))

    async def test_last_foreground_reply_is_scoped_and_withdrawal_invalidates_reference(self):
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 0)))
        app = host.application
        self.settings(app, {"enabled": True}).unwrap()
        batch = host.receive(self.event()).unwrap()
        await self.done(app, batch)
        context = self.context(app, batch)
        reply = app.background_history.latest_reply(context, self.clock.now(), within_minutes=0)
        self.assertEqual(reply["text"], "记住了。")
        self.assertIsNone(app.background_history.latest_reply(
            self.other_window_context(), self.clock.now(), within_minutes=0))
        async def same(envelope):
            if isinstance(envelope.original.event, BackgroundPromptTrigger):
                return answered('{"decision":"send","text":"记住了。"}')
            return answered("记住了。")
        self.model_behavior = same
        self.settings(app, {"generation_history_limit": 1}).unwrap()
        task = (await self.contact(app)).unwrap().task
        materials = self.contact_calls()[-1].original.context.materials
        dialogue = [json.loads(item.payload) for item in materials
                    if item.purpose == "recent_private_dialogue"]
        last_reply = [json.loads(item.payload) for item in materials
                      if item.purpose == "last_confirmed_reply"]
        self.assertEqual([item["role"] for item in dialogue], ["user"])
        self.assertEqual([item["text"] for item in last_reply], ["记住了。"])
        self.assertEqual(app.proactive.observe(context, task.task_id).unwrap()["dedup_reason"], "exact_repeat")
        event_key = app.runtime.read_one("SELECT event_key FROM conversation_inputs WHERE batch_id = ?", (batch,))[0]
        app.withdraw(context, event_key).unwrap()
        with self.assertRaises(TaskError):
            app.background_history.verify_reply(reply["reference"], context, self.clock.now())

    async def test_reply_withdrawn_during_contact_generation_cannot_be_committed(self):
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 0)))
        app = host.application
        self.settings(app, {"enabled": True}).unwrap()
        batch = host.receive(self.event()).unwrap()
        await self.done(app, batch)
        context = self.context(app, batch)
        event_key = app.runtime.read_one("SELECT event_key FROM conversation_inputs WHERE batch_id = ?", (batch,))[0]
        async def revoke(envelope):
            if isinstance(envelope.original.event, BackgroundPromptTrigger):
                self.assertTrue(any(m.purpose == "recent_private_dialogue" for m in envelope.original.context.materials))
                app.withdraw(context, event_key).unwrap()
                return answered('{"decision":"send","text":"另一个话题"}')
            return answered("记住了。")
        self.model_behavior = revoke
        result = await self.contact(app)
        self.assertNotEqual(result.unwrap().task.state, "succeeded")
        self.assertEqual(self.contacts(app), ())
        self.assertEqual(len(self.sent), 1)

    async def test_parallel_checks_of_same_reply_do_not_look_cyclic(self):
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 0)))
        app = host.application
        batch = host.receive(self.event()).unwrap()
        await self.done(app, batch)
        context = self.context(app, batch)
        entered, release = Event(), Event()
        original = app.messages.reply_images.for_part

        def hold_worker(*args, **kwargs):
            if current_thread() is not main_thread():
                entered.set()
                if not release.wait(3):
                    raise AssertionError("parallel reply check did not finish")
            return original(*args, **kwargs)

        with patch.object(app.messages.reply_images, "for_part", side_effect=hold_worker):
            with ThreadPoolExecutor(max_workers=1) as pool:
                worker = pool.submit(app.background_history.latest_reply, context, self.clock.now(),
                                     within_minutes=0)
                try:
                    self.assertTrue(entered.wait(3))
                    concurrent = app.background_history.latest_reply(context, self.clock.now(), within_minutes=0)
                finally:
                    release.set()
                first = worker.result(timeout=3)
        self.assertEqual(concurrent["reference"], first["reference"])

    async def test_policy_input_rejects_unknown_or_repeated_routes(self):
        app = await self.enabled()
        for value in ("semantic,semantic", "semantic,unknown"):
            with self.subTest(value=value):
                self.assertFalse(self.settings(app, {"dedup_policies": value}).is_success)
        self.settings(app, {"dedup_policies": "  semantic，life_event  "}).unwrap()
        self.assertEqual(self.preferences(app).values["dedup_policies"], "semantic,life_event")

    async def test_semantic_overlap_preferences_reach_the_real_contact_prompt(self):
        app = await self.enabled()
        self.settings(app, {"dedup_min_shared_tokens": 3, "dedup_min_overlap_ratio": 0.65}).unwrap()
        await self.contact(app)
        request = self.contact_calls()[-1].original
        instruction = request.instruction
        self.assertIn("短话题最少共享锚点 3 个", request.user_instruction)
        self.assertIn("较长话题最低重叠比例 0.65", request.user_instruction)
        self.assertIn("即使更换比喻、句式、视角或细节修饰", request.user_instruction)
        self.assertIn("真实的新事实、新观察、新观点", request.user_instruction)
        self.assertNotIn("短话题最少共享锚点 3 个", instruction)
        intent = json.loads(next(item.payload for item in request.context.materials
                                 if item.purpose == "contact_intent"))
        local_now = self.clock.now().astimezone(ZoneInfo(intent["timezone"]))
        self.assertEqual(intent["local_now"], local_now.isoformat())
        self.assertEqual(intent["local_date"], local_now.date().isoformat())
        self.assertEqual(intent["local_time"], local_now.strftime("%H:%M"))
        self.assertEqual(intent["weekday"], WEEKDAYS[local_now.weekday()])
        self.assertEqual(intent["is_weekend"], local_now.weekday() >= 5)
        self.assertEqual(intent["period"], period_label(local_now.hour))
        self.assertIn("下午不要说“早安”或“早上好”", instruction)
        self.assertIn("不要把旧消息当成仍待回复的内容重新接续", instruction)
        for changes in ({"dedup_min_shared_tokens": 0}, {"dedup_min_shared_tokens": 5},
                        {"dedup_min_overlap_ratio": -0.1}, {"dedup_min_overlap_ratio": 1.1}):
            with self.subTest(changes=changes):
                self.assertFalse(self.settings(app, changes).is_success)


class ProactiveFormattingPromptTests(unittest.TestCase):
    def test_news_prompts_do_not_request_blank_lines(self):
        single = news_presentation_instruction(SegmentationPolicy(enabled=False, max_segments=1))
        bubbles = news_presentation_instruction(SegmentationPolicy(
            enabled=True, llm_controlled=True, max_segments=3))
        self.assertIn("不插入空行", single)
        self.assertNotIn("留一个空行", single)
        self.assertIn("消息边界前后也不要填充空白行", bubbles)

    def test_paragraph_rule_boundary_is_compatible_with_edge_cleanup(self):
        paragraphs = news_presentation_instruction(SegmentationPolicy(rule_mode="paragraphs"))
        self.assertIn("用一个空行作为分段边界", paragraphs)
        self.assertIn("程序会裁掉各条消息首尾的空行", paragraphs)


class WeatherDedupCoverageTests(unittest.TestCase):
    """The weather window only works if its routes are enabled by default.

    ``dedup_weather_window_minutes`` is read only when the contact's origin is a
    weather one, so a route missing from ``dedup_policies`` makes the window dead
    code for that contact while the setting still looks configured. That is a silent
    failure -- nothing errors, the operator simply keeps getting repeats.
    """

    def setUp(self):
        self.defaults = {field.field_id.split(".")[-1]: field.default
                         for field in configuration_fields()}
        self.enabled = set(dedup_policies(self.defaults["dedup_policies"]))

    def test_every_weather_origin_route_is_enabled_by_default(self):
        weather_origins = ("weather_event", "weather_change_event", "official_alert_event")
        for origin in weather_origins:
            with self.subTest(origin=origin):
                route = next(route for marker, route in DEDUP_EVENT_POLICY if marker == origin)
                self.assertIn(route, self.enabled)

    def test_every_registered_contact_purpose_has_a_deduplication_route(self):
        from companion_v2.proactive.configuration import PURPOSES
        self.assertEqual(set(DEDUP_PURPOSE_POLICY), set(PURPOSES))

    def test_a_weather_window_alone_cannot_suppress_anything(self):
        """Guards the reasoning above: the window is inert without a matching route."""
        policy = ContactPreferences(
            None, {"dedup_enabled": True, "dedup_policies": "life_event"}, NOW)
        for origin in ("weather_event", "weather_change_event", "official_alert_event"):
            with self.subTest(origin=origin):
                self.assertFalse(policy.dedup_applies("environment", {origin: {}}))

    def test_disabling_dedup_outright_releases_every_route(self):
        """The off switch must be a full release, not a narrower window."""
        policy = ContactPreferences(
            None, {"dedup_enabled": False, "dedup_policies": self.defaults["dedup_policies"]}, NOW)
        for origin in ("weather_event", "weather_change_event", "official_alert_event"):
            with self.subTest(origin=origin):
                self.assertFalse(policy.dedup_applies("environment", {origin: {}}))

    def test_the_default_policy_string_survives_a_round_trip(self):
        self.assertEqual(dedup_policies(self.defaults["dedup_policies"]),
                         tuple(dedup_policies(self.defaults["dedup_policies"])))
