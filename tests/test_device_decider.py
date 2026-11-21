"""The decider turns one observation into one declared action; no device here."""

from __future__ import annotations

from dataclasses import replace
from io import BytesIO
import unittest

from PIL import Image, ImageDraw

from companion_v2.device.adb import AdbClient, UiElement
from companion_v2.device.checkout import checkout_boundary, checkout_frame
from companion_v2.device.decider import (
    DecisionError, ModelPhoneDecider, decision_prompt, parse_decision,
)
from companion_v2.device.session import PhoneAction, PhoneObservation, PhoneSession
from tests.test_device_adb import ChangingAdb, FakeAdb


class ParseDecisionTests(unittest.TestCase):
    def test_accepts_the_declared_contract(self):
        action = parse_decision('{"action":"tap_text","text":"签到"}')
        self.assertEqual(action.kind, "tap_text")
        self.assertEqual(action.text, "签到")

    def test_strips_one_markdown_fence(self):
        action = parse_decision('```json\n{"action":"done","reason":"已签到"}\n```')
        self.assertEqual(action.kind, "done")

    def test_rejects_non_json_and_unknown_fields(self):
        for text in ("looks good", '{"action":"tap"}', '{"action":"shell","text":"rm -rf /"}'):
            with self.assertRaises(DecisionError) as caught:
                parse_decision(text)
            self.assertEqual(caught.exception.code, "phone_decision_invalid")

    def test_express_payment_is_a_checkout_boundary(self):
        button = UiElement("极速支付", "", "test.takeout", "android.widget.Button", True, True,
                           (80, 325, 180, 375), 1)
        observation = PhoneObservation(0, 200, 400, (button,), "checkout", 0)

        self.assertTrue(checkout_boundary(observation, PhoneAction("tap_text", text="极速支付")))
        self.assertTrue(checkout_boundary(observation, PhoneAction("tap", x=130, y=350)))

    def test_meituan_checkout_fingerprint_ignores_banner_above_order_form(self):
        tabs = UiElement("", "", "com.sankuai.meituan", "android.widget.FrameLayout", False, True,
                         (0, 50, 200, 90))
        delivery = replace(tabs, text="外卖配送", class_name="android.widget.TextView",
                           bounds=(20, 60, 80, 80))
        pickup = replace(delivery, text="到店自取", bounds=(120, 60, 180, 80))
        pay = replace(delivery, text="极速支付", bounds=(80, 325, 180, 375))
        image = Image.new("RGB", (200, 400), "white")

        def snapshot():
            buffer = BytesIO()
            image.save(buffer, format="PNG")
            data = buffer.getvalue()
            return PhoneObservation(0, 200, 400, (tabs, delivery, pickup, pay), "", len(data), data)

        initial = checkout_frame(snapshot(), form=True)
        ImageDraw.Draw(image).rectangle((10, 20, 150, 46), fill="yellow")
        self.assertEqual(checkout_frame(snapshot(), form=True), initial)
        ImageDraw.Draw(image).rectangle((5, 325, 60, 375), fill="red")
        self.assertNotEqual(checkout_frame(snapshot(), form=True), initial)


class PromptTests(unittest.TestCase):
    def test_prompt_carries_goal_and_visible_labels(self):
        client = AdbClient("adb.exe", serial="SER123", runner=FakeAdb())
        observation = __import__("companion_v2.device.session", fromlist=["PhoneSession"]) \
            .PhoneSession(client, goal="g").observe(0)
        text = decision_prompt("打开应用并签到", observation, ())
        self.assertIn("打开应用并签到", text)
        self.assertIn("签到", text)
        self.assertIn("第一步", text)


class DeciderRepairTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_repair_after_an_invalid_answer(self):
        answers = iter(["I cannot", '{"action":"done","reason":"已确认"}'])
        seen = []

        async def generate(prompt):
            seen.append(prompt)
            return next(answers)

        decider = ModelPhoneDecider(generate)
        client = AdbClient("adb.exe", serial="SER123", runner=FakeAdb())
        session = PhoneSession(client, goal="签个到")
        observation = session.observe(0)
        action = await decider.decide("签个到", observation, ())
        self.assertEqual(action.kind, "done")
        self.assertEqual(len(seen), 2)
        self.assertIn("上一次输出不合约", seen[1])

    async def test_two_invalid_answers_fail_loudly(self):
        async def generate(prompt):
            return "still not json"

        decider = ModelPhoneDecider(generate)
        client = AdbClient("adb.exe", serial="SER123", runner=FakeAdb())
        session = PhoneSession(client, goal="签个到")
        with self.assertRaises(DecisionError):
            await decider.decide("签个到", session.observe(0), ())


class AsyncSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_session_runs_the_same_bounded_loop(self):
        answers = iter([
            '{"action":"launch","package":"com.example.app"}',
            '{"action":"tap_text","text":"签到"}',
            '{"action":"done","reason":"看到今日已签到"}',
        ])

        async def generate(prompt):
            return next(answers)

        client = AdbClient("adb.exe", serial="SER123", runner=ChangingAdb())
        session = PhoneSession(client, goal="打开应用并签到", max_steps=4)
        outcome = await ModelPhoneDecider(generate).run(session)
        self.assertEqual(outcome.state, "done")
        self.assertEqual([step.action.kind for step in outcome.steps], ["launch", "tap_text", "done"])
        self.assertTrue(all(step.ok for step in outcome.steps))


if __name__ == "__main__":
    unittest.main()
