"""Privacy and behaviour contract for the group-only poke context stream B."""

import asyncio
from types import SimpleNamespace as NS
import unittest

from companion_v2.conversation.pokes import PokePolicy
from tests.group_host_support import GroupFixture, group_config
from tests.native_adapter_support import completion, final_text
from tests.test_astrbot_poke_host import poke_notice
from tests.test_management_access import NOW


class PokeContextPolicyTests(unittest.TestCase):
    def test_defaults_keep_the_stream_off(self):
        policy = PokePolicy.parse(None)
        self.assertFalse(policy.context_reply_enabled)
        self.assertEqual(policy.context_delay_seconds, 5)
        policy = PokePolicy.parse({"enabled": True, "replies": ["在呢。"]})
        self.assertFalse(policy.context_reply_enabled)
        self.assertEqual(policy.context_delay_seconds, 5)

    def test_ui_roundtrip_preserves_context_fields(self):
        """The dashboard save path must persist both context fields verbatim."""
        saved = {"enabled": True, "trigger_probability": 0.7, "replies": ["在呢。"],
                 "back_probability": 0.1, "back_times": 1,
                 "model_reply_enabled": True, "model_reply_probability": 0.7,
                 "context_reply_enabled": True, "context_delay_seconds": 5}
        policy = PokePolicy.parse(saved)
        self.assertTrue(policy.context_reply_enabled)
        self.assertEqual(policy.context_delay_seconds, 5)
        # The API view must expose them again with the same names the UI collects.
        view = {"context_reply_enabled": policy.context_reply_enabled,
                "context_delay_seconds": policy.context_delay_seconds}
        reparsed = PokePolicy.parse({**saved, **view})
        self.assertTrue(reparsed.context_reply_enabled)
        self.assertEqual(reparsed.context_delay_seconds, 5)

    def test_delay_bounds_are_enforced(self):
        policy_zero = PokePolicy.parse({"enabled": True, "replies": ["在呢。"], "context_delay_seconds": 0})
        self.assertEqual(policy_zero.context_delay_seconds, 0)
        with self.assertRaises(ValueError):
            PokePolicy.parse({"enabled": True, "replies": ["在呢。"], "context_delay_seconds": -1})
        with self.assertRaises(ValueError):
            PokePolicy.parse({"enabled": True, "replies": ["在呢。"], "context_delay_seconds": 31})
        with self.assertRaises(ValueError):
            PokePolicy.parse({"enabled": True, "replies": ["在呢。"], "context_delay_seconds": "3"})


class PokeContextStreamTests(GroupFixture, unittest.IsolatedAsyncioTestCase):
    def configure(self, *, context_reply=False, delay=1, trigger=1.0):
        config = group_config()
        config["poke"] = {"enabled": True, "trigger_probability": trigger,
                          "replies": ["在呢。"], "back_probability": 0.0,
                          "context_reply_enabled": context_reply,
                          "context_delay_seconds": delay}
        self.controller = self.make_controller(config)
        return config

    async def seed_group_observation(self, *, text="今晚吃什么", speaker="201", identifier="seed"):
        event = self.group_event(identifier=identifier, sender=speaker, text=text)
        target = self.controller.settings.target("group-a")
        result = await self.controller.session.prepare_group(event, target)
        self.assertTrue(result.is_success, result.failure)
        return event

    async def test_disabled_by_default_never_runs_the_stream(self):
        self.configure(context_reply=False)
        await self.start_host()
        await self.seed_group_observation()
        async def model_behavior(payload, tools):
            raise AssertionError("the context stream must not call the model while disabled")
        self.native.provider.client.state.behavior = model_behavior
        result = (await self.controller.receive_notice(
            poke_notice(self.group_event(identifier="701")))).unwrap()
        self.assertEqual(result["state"], "delivered")
        await self.wait_context_tasks()
        calls = self.native.provider.client.state.calls
        self.assertEqual(len(calls), 0)  # only stream A; the model was never invoked
        api = self.native.platform.client._api._wsr_api
        self.assertEqual([action for action, _ in api.calls], ["send_group_msg"])


    async def test_stream_b_runs_even_when_stream_a_dice_misses(self):
        """Stream A's deterministic dice must not gate stream B."""
        self.configure(context_reply=True, delay=1, trigger=0.0)
        await self.start_host()
        await self.seed_group_observation(text="今晚吃什么", speaker="201", identifier="seed-1")

        async def model_behavior(payload, tools):
            return completion(final_text("来了来了，锅底我都想好了。"))

        self.native.provider.client.state.behavior = model_behavior
        result = (await self.controller.receive_notice(
            poke_notice(self.group_event(identifier="701")))).unwrap()
        self.assertEqual(result["state"], "skipped")  # stream A stayed silent
        await self.wait_context_tasks()
        api = self.native.platform.client._api._wsr_api
        self.assertEqual([action for action, _ in api.calls], ["send_group_msg"])
        self.assertEqual(api.calls[0][1]["message"], [{"type": "text",
            "data": {"text": "来了来了，锅底我都想好了。"}}])


    async def test_zero_delay_stream_reads_context_and_sends(self):
        """End-to-end: enabled + delay=0 must actually read the bounded
        context and deliver the model reply (the instant-snapshot mode)."""
        self.configure(context_reply=True, delay=0)
        await self.start_host()
        await self.seed_group_observation(text="今晚吃什么", speaker="201", identifier="seed-1")
        seen = {}

        async def model_behavior(payload, tools):
            import json as _json
            raw = payload["messages"][-1]["content"]
            try:
                seen["text"] = str(_json.loads(raw)["materials"][0]["text"])
            except Exception:
                seen["text"] = str(raw)
            return completion(final_text("锅底我已经想好了，等着就是。"))

        self.native.provider.client.state.behavior = model_behavior
        result = (await self.controller.receive_notice(
            poke_notice(self.group_event(identifier="701")))).unwrap()
        self.assertEqual(result["state"], "delivered")
        await self.wait_context_tasks()
        api = self.native.platform.client._api._wsr_api
        # Stream A preset reply + stream B context reply
        self.assertEqual([action for action, _ in api.calls],
                         ["send_group_msg", "send_group_msg"])
        self.assertIn("今晚吃什么", seen["text"])
        second = api.calls[1][1]
        self.assertEqual(second["message"], [{"type": "text",
            "data": {"text": "锅底我已经想好了，等着就是。"}}])

    async def test_zero_delay_stream_skip_stays_silent(self):
        """Enabled + delay=0 with nothing to pick up: the stream must
        actually run (the model is invoked) and stay silent on [SKIP]."""
        self.configure(context_reply=True, delay=0)
        await self.start_host()
        calls = 0

        async def model_behavior(payload, tools):
            nonlocal calls
            calls += 1
            return completion(final_text("[SKIP]"))

        self.native.provider.client.state.behavior = model_behavior
        result = (await self.controller.receive_notice(
            poke_notice(self.group_event(identifier="701")))).unwrap()
        self.assertEqual(result["state"], "delivered")
        await self.wait_context_tasks()
        self.assertEqual(calls, 1)  # the stream ran and asked the model
        api = self.native.platform.client._api._wsr_api
        self.assertEqual([action for action, _ in api.calls], ["send_group_msg"])

    async def test_private_poke_never_runs_the_stream(self):
        self.configure(context_reply=True)
        await self.start_host()
        async def model_behavior(payload, tools):
            raise AssertionError("the context stream must not run for private pokes")
        self.native.provider.client.state.behavior = model_behavior
        result = (await self.controller.receive_notice(
            poke_notice(self.event(identifier="701")))).unwrap()
        self.assertEqual(result["state"], "delivered")
        await self.wait_context_tasks()
        self.assertEqual(len(self.native.provider.client.state.calls), 0)
        api = self.native.platform.client._api._wsr_api
        self.assertEqual([action for action, _ in api.calls], ["send_private_msg"])

    async def test_group_stream_sends_bounded_context_and_skip_is_silent(self):
        self.configure(context_reply=True, delay=1)
        await self.start_host()
        long_text = "长" * 400
        await self.seed_group_observation(text="今晚吃什么", speaker="201", identifier="seed-1")
        await self.seed_group_observation(text=long_text, speaker="202", identifier="seed-2")
        seen = {}

        async def model_behavior(payload, tools):
            import json as _json
            raw = payload["messages"][-1]["content"]
            try:
                seen["text"] = str(_json.loads(raw)["materials"][0]["text"])
            except Exception:
                seen["text"] = str(raw)
            return completion(final_text("[SKIP]"))

        self.native.provider.client.state.behavior = model_behavior
        result = (await self.controller.receive_notice(
            poke_notice(self.group_event(identifier="701")))).unwrap()
        self.assertEqual(result["state"], "delivered")
        await self.wait_context_tasks()
        # Stream A preset reply only; the stream B context reply stayed silent.
        api = self.native.platform.client._api._wsr_api
        self.assertEqual([action for action, _ in api.calls], ["send_group_msg"])
        text = seen["text"]
        self.assertIn("今晚吃什么", text)
        self.assertIn("长", text)
        self.assertNotIn("长" * 200, text)
        self.assertEqual(len(self.native.provider.client.state.calls), 1)  # only stream B calls the model

    async def test_group_stream_replies_when_the_model_joins_the_topic(self):
        self.configure(context_reply=True, delay=1)
        await self.start_host()
        await self.seed_group_observation(text="今晚吃什么", speaker="201", identifier="seed-1")

        async def model_behavior(payload, tools):
            return completion(final_text("晚上想吃火锅的话，我可以陪你们研究锅底。"))

        self.native.provider.client.state.behavior = model_behavior
        result = (await self.controller.receive_notice(
            poke_notice(self.group_event(identifier="701")))).unwrap()
        self.assertEqual(result["state"], "delivered")
        await self.wait_context_tasks()
        api = self.native.platform.client._api._wsr_api
        self.assertEqual([action for action, _ in api.calls],
                         ["send_group_msg", "send_group_msg"])
        second = api.calls[1][1]
        self.assertEqual(second["group_id"], 300)
        self.assertEqual(second["message"], [{"type": "text",
            "data": {"text": "晚上想吃火锅的话，我可以陪你们研究锅底。"}}])

    async def wait_context_tasks(self):
        app = self.controller.session.application
        tasks = set(getattr(app, "poke_context_tasks", ()))
        for task in tuple(tasks):
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=30)
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
