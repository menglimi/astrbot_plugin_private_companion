"""Privacy and behaviour contract for the group-only poke context stream B."""

from datetime import timedelta
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


    async def test_failed_context_reply_is_reported_instead_of_looking_like_a_skip(self):
        """A failed stream B must not be logged as "[SKIP] or empty".

        ``PokeReplyModel.reply`` used to collapse every failed attempt into
        ``None``, and the gateway's impossible retry used to replace the real
        code with ``model_request_already_started``. Both hid the failure.
        """
        self.configure(context_reply=True, delay=0)
        await self.start_host()
        await self.seed_group_observation(text="今晚吃什么", speaker="201", identifier="seed-1")

        async def model_behavior(payload, tools):
            raise RuntimeError("provider transport failed")

        self.native.provider.client.state.behavior = model_behavior
        with self.assertLogs(level="INFO") as captured:
            result = (await self.controller.receive_notice(
                poke_notice(self.group_event(identifier="701")))).unwrap()
            self.assertEqual(result["state"], "delivered")
            await self.wait_context_tasks()
        text = "\n".join(captured.output)
        self.assertNotIn("model_request_already_started", text)
        self.assertIn("[poke-context] 流B 未完成", text)
        self.assertIn("code=model_usage_unknown", text)
        self.assertNotIn("模型判断无需接话", text)
        # One dispatch only: the sentinel must never be provoked by a retry.
        self.assertEqual(len(self.native.provider.client.state.calls), 1)
        api = self.native.platform.client._api._wsr_api
        self.assertEqual([action for action, _ in api.calls], ["send_group_msg"])  # stream A's preset

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
        """Enabled + delay=0 with a fresh banter message: the stream must
        run (the model is invoked) and stay silent on [SKIP]."""
        self.configure(context_reply=True, delay=0)
        await self.start_host()
        await self.seed_group_observation(text="哈哈哈草", speaker="201", identifier="seed-banter")
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

    async def test_empty_and_stale_windows_skip_without_calling_model(self):
        """Empty windows or observations older than 90s must never invoke the model."""
        self.configure(context_reply=True, delay=0)
        await self.start_host()

        async def fail_if_called(payload, tools):
            raise AssertionError("stale or empty context must skip with zero LLM calls")

        self.native.provider.client.state.behavior = fail_if_called
        # 1) Empty group window -> zero LLM calls
        result = (await self.controller.receive_notice(
            poke_notice(self.group_event(identifier="701"), time=int(self.clock.now().timestamp())))).unwrap()
        self.assertEqual(result["state"], "delivered")
        await self.wait_context_tasks()
        self.assertEqual(len(self.native.provider.client.state.calls), 0)

        # 2) Observation older than 90s (e.g. 90 minutes earlier) -> zero LLM calls
        await self.seed_group_observation(text="很久之前的旧话题", speaker="201", identifier="seed-old")
        self.clock.advance(timedelta(minutes=90))
        later = poke_notice(self.group_event(identifier="702", sender="202"),
                            time=int(self.clock.now().timestamp()))
        result2 = (await self.controller.receive_notice(later)).unwrap()
        self.assertEqual(result2["state"], "delivered")
        await self.wait_context_tasks()
        self.assertEqual(len(self.native.provider.client.state.calls), 0)

    async def test_repeated_poke_does_not_reconsume_answered_topic_and_isolates_bot_replies(self):
        """After stream B answers a fresh message, another poke with no new message
        must skip with zero calls, and bot replies must never pollute human observations."""
        self.configure(context_reply=True, delay=0)
        await self.start_host()
        await self.seed_group_observation(text="今晚吃什么", speaker="201", identifier="seed-1")
        calls = 0

        async def model_behavior(payload, tools):
            nonlocal calls
            calls += 1
            return completion(final_text("去吃火锅吧。"))

        self.native.provider.client.state.behavior = model_behavior
        first = poke_notice(self.group_event(identifier="701", sender="201"),
                            time=int(self.clock.now().timestamp()))
        (await self.controller.receive_notice(first)).unwrap()
        await self.wait_context_tasks()
        self.assertEqual(calls, 1)

        # Verify group_admission.observations only contains human messages (never bot poke replies)
        admission = self.controller.session.application.tools.group_participation.admission
        all_obs = [item for bucket in admission.observations.values() for item in bucket]
        self.assertEqual(len(all_obs), 1)
        self.assertEqual(all_obs[0]["speaker_id"], "201")
        # Bot replies land in their own bounded ledgers instead.
        runtime = self.controller.session.runtime
        ledger = runtime.read_all("SELECT key, value FROM runtime_metadata WHERE key LIKE ?",
                                  ("conversation.group_poke_replies.v1:%",))
        self.assertEqual(len(ledger), 1)
        sources = [item["source"] for item in __import__("json").loads(ledger[0]["value"])["items"]]
        self.assertEqual(sources, ["poke_a", "poke_b"])
        self.assertEqual(len(runtime.read_all("SELECT key FROM runtime_metadata WHERE key LIKE ?",
                                              ("conversation.group_poke_watermark.v1:%",))), 1)

        # Second poke 10s later with no new human message -> zero LLM calls
        self.clock.advance(timedelta(seconds=10))
        second = poke_notice(self.group_event(identifier="702", sender="202"),
                             time=int(self.clock.now().timestamp()))
        (await self.controller.receive_notice(second)).unwrap()
        await self.wait_context_tasks()
        self.assertEqual(calls, 1)

    async def test_poker_identity_is_injected_and_marked_in_timeline(self):
        """Stream B must clearly identify who poked and distinguish them from other speakers."""
        self.configure(context_reply=True, delay=0)
        await self.start_host()
        await self.seed_group_observation(text="之前的话题先放一边", speaker="201", identifier="seed-other")
        await self.seed_group_observation(text="看看新买的键盘", speaker="202", identifier="seed-poker")
        seen = {}

        async def model_behavior(payload, tools):
            import json as _json
            raw = payload["messages"][-1]["content"]
            seen["text"] = str(_json.loads(raw)["materials"][0]["text"])
            return completion(final_text("这把键盘配色看着挺清爽的。"))

        self.native.provider.client.state.behavior = model_behavior
        poke_ev = self.group_event(identifier="703", sender="202")
        poke_ev.message_obj.sender.nickname = "小北"
        poke_ev.message_obj.raw_message["sender"] = {"user_id": 202, "nickname": "小北"}
        notice = poke_notice(poke_ev, time=int(self.clock.now().timestamp()))
        notice.message_obj.sender.nickname = "小北"
        notice.message_obj.raw_message["sender"] = {"user_id": 202, "nickname": "小北"}
        (await self.controller.receive_notice(notice)).unwrap()
        await self.wait_context_tasks()
        self.assertIn("小北(202)", seen["text"])
        self.assertIn("小北(202)[戳你的人]: 看看新买的键盘", seen["text"])
        self.assertNotIn("201[戳你的人]", seen["text"])

    async def test_conflicting_core_reply_suppresses_stream_b(self):
        """If the main group reply path is active or already answered around the poke,
        stream B must abort instead of sending a duplicate/conflicting message."""
        self.configure(context_reply=True, delay=0)
        await self.start_host()
        ev = await self.seed_group_observation(text="看看新买的键盘", speaker="202", identifier="seed-core")
        admission = self.controller.session.application.tools.group_participation.admission
        target = self.controller.settings.target("group-a")
        ctx = (await self.controller.session.prepare_group(ev, target)).unwrap()
        admission.note_live_core_turn(ctx, "core-ev-1", active=True)

        async def fail_if_called(payload, tools):
            raise AssertionError("stream B must abort while a core group turn is live")

        self.native.provider.client.state.behavior = fail_if_called
        notice = poke_notice(self.group_event(identifier="704", sender="202"),
                             time=int(self.clock.now().timestamp()))
        (await self.controller.receive_notice(notice)).unwrap()
        await self.wait_context_tasks()
        self.assertEqual(len(self.native.provider.client.state.calls), 0)
        admission.note_live_core_turn(ctx, "core-ev-1", active=False)

    async def test_rapid_same_group_pokes_coalesce_stream_b(self):
        """Multiple rapid pokes in the same group cancel the earlier pending stream B
        so only the latest one executes."""
        self.configure(context_reply=True, delay=1)
        await self.start_host()
        await self.seed_group_observation(text="有人在吗", speaker="201", identifier="seed-coalesce")
        calls = 0

        async def model_behavior(payload, tools):
            nonlocal calls
            calls += 1
            return completion(final_text("在的呀。"))

        self.native.provider.client.state.behavior = model_behavior
        t0 = int(self.clock.now().timestamp())
        (await self.controller.receive_notice(
            poke_notice(self.group_event(identifier="705", sender="201"), time=t0))).unwrap()
        (await self.controller.receive_notice(
            poke_notice(self.group_event(identifier="706", sender="202"), time=t0 + 1))).unwrap()
        await self.wait_context_tasks()
        self.assertEqual(calls, 1)

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
            except (Exception, asyncio.CancelledError):
                pass


if __name__ == "__main__":
    unittest.main()
