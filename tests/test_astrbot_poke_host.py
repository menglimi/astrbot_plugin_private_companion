"""Authenticated OneBot poke handling across private and group delivery."""

from pathlib import Path
from datetime import timedelta
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest

from astrbot_host.controller import AstrBotStagingController
from companion_v2.adapters.astrbot.staging import NativeAdapterTypes
from companion_v2.conversation.pokes import PokePolicy
from companion_v2.foundation.clock import DeterministicClock
from tests.group_host_support import GroupFixture, group_config
from tests.native_adapter_support import FakeEvent, FakeProvider, FakeReverseApi, completion, final_text
from tests.recall_host_support import recall_notice, sdk_environment
from tests.test_management_access import NOW


def poke_notice(original, *, target=None, time=None):
    notice = recall_notice(original)
    raw = notice.message_obj.raw_message
    raw.update(notice_type="notify", sub_type="poke",
               target_id=int(original.message_obj.self_id) if target is None else target)
    raw.pop("message_id", None)
    raw.pop("operator_id", None)
    if time is not None:
        raw["time"] = time
    return notice


class PokePolicyTests(unittest.TestCase):
    def test_defaults_and_invalid_configuration(self):
        policy = PokePolicy.parse(None)
        self.assertFalse(policy.enabled)
        self.assertEqual(policy.back_times, 1)
        self.assertFalse(policy.model_reply_enabled)
        self.assertEqual(policy.model_reply_probability, 0.3)
        self.assertFalse(policy.use_model_reply("default-event"))
        with self.assertRaises(ValueError):
            PokePolicy.parse({"enabled": True, "replies": []})
        with self.assertRaises(ValueError):
            PokePolicy.parse({"trigger_probability": 2})
        with self.assertRaises(ValueError):
            PokePolicy.parse({"back_probability": True})
        with self.assertRaises(ValueError):
            PokePolicy.parse({"back_times": 0})
        with self.assertRaises(ValueError):
            PokePolicy.parse({"back_times": 6})


class MultiHostPokeTests(GroupFixture, unittest.IsolatedAsyncioTestCase):
    def configure(self, *, private="preview", group="preview", back=0.0, back_times=1, trigger=1.0,
                  model_reply=False, model_probability=0.3):
        config = group_config(delivery_mode=private, group_delivery_mode=group)
        config["poke"] = {"enabled": True, "trigger_probability": trigger,
                          "replies": ["在呢。"], "back_probability": back, "back_times": back_times,
                          "model_reply_enabled": model_reply, "model_reply_probability": model_probability}
        self.controller = self.make_controller(config)
        return config

    async def test_private_target_poke_ignores_legacy_grants_and_survives_restart(self):
        config = self.configure(private="onebot_private", back=1.0)
        await self.start_host()
        notice = poke_notice(self.event(identifier="701"))
        api = self.native.platform.client._api._wsr_api
        result = (await self.controller.receive_notice(notice)).unwrap()
        self.assertEqual((result["state"], result["poke_back"]), ("delivered", True))
        self.assertEqual([action for action, _ in api.calls], ["send_private_msg", "friend_poke"])

        next_notice = poke_notice(self.event(identifier="702"), time=int(NOW.timestamp()) + 1)
        result = (await self.controller.receive_notice(next_notice)).unwrap()
        self.assertEqual((result["state"], result["poke_back"]), ("delivered", True))
        self.assertEqual([action for action, _ in api.calls],
                         ["send_private_msg", "friend_poke", "send_private_msg", "friend_poke"])
        self.assertEqual(api.calls[0][1]["user_id"], 200)
        self.assertEqual(api.calls[1][1]["user_id"], 200)

        await self.controller.terminate()
        self.controller = self.make_controller(config)
        (await self.controller.load_existing()).unwrap()
        (await self.controller.start()).unwrap()
        self.assertEqual((await self.controller.receive_notice(next_notice)).unwrap()["state"], "replayed")
        self.assertEqual(len(api.calls), 4)

    async def test_group_poke_uses_configured_route_without_legacy_grants(self):
        self.configure(group="onebot_group", back=1.0, model_reply=True, model_probability=1.0)
        await self.start_host()
        async def model_behavior(payload, tools):
            return completion(final_text("群里的戳戳收到啦。"))

        self.native.provider.client.state.behavior = model_behavior
        original = self.group_event(identifier="701", sender="201", unique=True)
        notice = poke_notice(original)
        api = self.native.platform.client._api._wsr_api
        result = (await self.controller.receive_notice(notice)).unwrap()
        self.assertEqual((result["state"], result["poke_back"]), ("delivered", True))
        self.assertEqual(result["response_kind"], "model")
        self.assertEqual([action for action, _ in api.calls], ["send_group_msg", "group_poke"])
        self.assertEqual((api.calls[0][1]["group_id"], api.calls[1][1]["user_id"]), (300, 201))
        self.assertEqual(api.calls[0][1]["message"], [{"type": "text", "data": {"text": "群里的戳戳收到啦。"}}])

        other_group = poke_notice(self.group_event("group-b", identifier="702"),
                                  time=int(NOW.timestamp()) + 2)
        other_result = (await self.controller.receive_notice(other_group)).unwrap()
        self.assertEqual((other_result["state"], other_result["poke_back"]), ("delivered", True))
        self.assertEqual([action for action, _ in api.calls],
                         ["send_group_msg", "group_poke", "send_group_msg", "group_poke"])
        wrong_target = poke_notice(self.group_event(identifier="703"), target=999,
                                   time=int(NOW.timestamp()) + 3)
        self.assertIsNone(await self.controller.receive_notice(wrong_target))
        self.assertEqual(len(api.calls), 4)

    async def test_back_count_repeats_the_native_poke_action(self):
        self.configure(private="onebot_private", back=1.0, back_times=3)
        await self.start_host()
        result = (await self.controller.receive_notice(poke_notice(self.event(identifier="701")))).unwrap()
        api = self.native.platform.client._api._wsr_api

        self.assertEqual((result["poke_back"], result["poke_back_count"]), (True, 3))
        self.assertEqual([action for action, _ in api.calls], ["send_private_msg", "friend_poke",
                         "friend_poke", "friend_poke"])

    async def test_model_poke_reply_uses_supervised_prompt_without_creating_a_user_turn(self):
        self.configure(private="onebot_private", model_reply=True, model_probability=1.0)
        await self.start_host()

        calls = 0

        async def model_behavior(payload, tools):
            nonlocal calls
            calls += 1
            return completion(final_text("模型即兴：我被你发现啦。" if calls == 1 else "超长" * 121))

        self.native.provider.client.state.behavior = model_behavior
        app = self.controller.session.application
        before = app.runtime.read_one("SELECT COUNT(*) AS count FROM conversation_batches", ())['count']
        result = (await self.controller.receive_notice(poke_notice(self.event(identifier="701")))).unwrap()
        after = app.runtime.read_one("SELECT COUNT(*) AS count FROM conversation_batches", ())['count']
        api = self.native.platform.client._api._wsr_api

        self.assertEqual(result["response_kind"], "model")
        self.assertEqual(api.calls[0][1]["message"], [{"type": "text", "data": {"text": "模型即兴：我被你发现啦。"}}])
        self.assertEqual(before, after)
        fallback = (await self.controller.receive_notice(poke_notice(
            self.event(identifier="702"), time=int(NOW.timestamp()) + 1))).unwrap()
        self.assertEqual(fallback["response_kind"], "preset")
        self.assertEqual(api.calls[1][1]["message"], [{"type": "text", "data": {"text": "在呢。"}}])
        self.assertEqual(len(self.native.provider.client.state.calls), 2)

    async def test_model_failure_keeps_the_real_code_instead_of_the_session_sentinel(self):
        """A failed poke reply names its real cause and is never replayed.

        ``LocalModelGateway.open_session`` writes the task's single
        ``model_requests`` row before the first dispatch and keeps it, so a second
        ``generate`` for the same run can only raise
        ``model_request_already_started``. Retrying there replaced
        ``model_usage_unknown`` (provider failure ``provider_request_failed``)
        with that sentinel and suppressed every owner warning.
        """
        self.configure(private="onebot_private", model_reply=True, model_probability=1.0)
        await self.start_host()

        async def model_behavior(payload, tools):
            raise RuntimeError("provider transport failed")

        self.native.provider.client.state.behavior = model_behavior
        api = self.native.platform.client._api._wsr_api
        with self.assertLogs(level="INFO") as captured:
            result = (await self.controller.receive_notice(poke_notice(self.event(identifier="701")))).unwrap()
        self.assertEqual(result["response_kind"], "preset")
        self.assertEqual(api.calls[0][1]["message"], [{"type": "text", "data": {"text": "在呢。"}}])
        # One dispatch only: the sentinel must never be provoked by a retry.
        self.assertEqual(len(self.native.provider.client.state.calls), 1)
        text = "\n".join(captured.output)
        self.assertNotIn("model_request_already_started", text)
        pokes_logs = [record.getMessage() for record in captured.records
                      if record.name == "companion_v2.conversation.pokes"]
        self.assertTrue(pokes_logs, "the poke owner must record the failed attempt")
        self.assertTrue(any("code=model_usage_unknown" in item for item in pokes_logs), pokes_logs)
        # The pre-existing fallback warning finally fires, with the true code.
        self.assertIn("戳一戳模型回复未完成，使用预设短句", text)
        self.assertIn("code=model_usage_unknown", text)
        app = self.controller.session.application
        rows = app.runtime.read_all("""SELECT c.state AS state, f.code AS code FROM model_calls c
            JOIN execution_tasks t USING(task_id) LEFT JOIN model_call_failures f USING(call_id)
            WHERE t.kind = 'conversation.poke_reply' ORDER BY c.started_at""", ())
        self.assertEqual([(row["state"], row["code"]) for row in rows],
                         [("unknown", "provider_request_failed")])

    async def test_lost_ack_during_repeated_poke_is_unknown_and_not_replayed(self):
        self.configure(private="onebot_private", back=1.0, back_times=3)
        await self.start_host()
        api = self.native.platform.client._api._wsr_api
        original_call = api.call_action
        poke_calls = 0

        async def lose_second_poke_ack(action, **params):
            nonlocal poke_calls
            result = await original_call(action, **params)
            if action == "friend_poke":
                poke_calls += 1
                if poke_calls == 2:
                    raise TimeoutError("lost repeated poke acknowledgement")
            return result

        api.call_action = lose_second_poke_ack
        notice = poke_notice(self.event(identifier="701"))
        result = (await self.controller.receive_notice(notice)).unwrap()
        self.assertEqual((result["state"], result["poke_back"], result["poke_back_count"]),
                         ("delivered", "unknown", 1))
        call_count = len(api.calls)
        self.assertEqual((await self.controller.receive_notice(notice)).unwrap()["state"], "replayed")
        self.assertEqual(len(api.calls), call_count)

    async def test_self_poke_and_unconfigured_sender_are_ignored(self):
        self.configure(private="onebot_private")
        await self.start_host()
        (await self.controller.authorize_delivery("first")).unwrap()
        own = poke_notice(self.event(identifier="701"), time=int(NOW.timestamp()) + 1)
        own.message_obj.raw_message["user_id"] = 100
        own.message_obj.sender.user_id = "100"
        own.session.session_id = own.message_obj.session_id = "100"
        own.unified_msg_origin = "qq-main:FriendMessage:100"
        self.assertIsNone(await self.controller.receive_notice(own))
        stranger = poke_notice(self.event(identifier="702"), time=int(NOW.timestamp()) + 2)
        stranger.message_obj.raw_message["user_id"] = 999
        stranger.message_obj.sender.user_id = "999"
        stranger.session.session_id = stranger.message_obj.session_id = "999"
        stranger.unified_msg_origin = "qq-main:FriendMessage:999"
        self.assertIsNone(await self.controller.receive_notice(stranger))
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])

    async def test_lost_ack_and_negative_message_id_do_not_duplicate(self):
        self.configure(private="onebot_private", back=1.0)
        await self.start_host()
        (await self.controller.authorize_delivery("first")).unwrap()
        api = self.native.platform.client._api._wsr_api
        api.error = TimeoutError("lost text acknowledgement")
        notice = poke_notice(self.event(identifier="701"))
        self.assertEqual((await self.controller.receive_notice(notice)).unwrap()["state"], "unknown")
        api.error = None
        self.assertEqual((await self.controller.receive_notice(notice)).unwrap()["state"], "replayed")
        self.assertEqual(len(api.calls), 1)

        api.result = {"message_id": -701}
        next_notice = poke_notice(self.event(identifier="702"), time=int(NOW.timestamp()) + 1)
        result = (await self.controller.receive_notice(next_notice)).unwrap()
        self.assertEqual((result["message_id"], result["poke_back"]), ("-701", True))

    async def test_legacy_delivery_revocation_does_not_cancel_poke_back(self):
        self.configure(private="onebot_private", back=1.0)
        await self.start_host()
        (await self.controller.authorize_delivery("first")).unwrap()
        api = self.native.platform.client._api._wsr_api
        original_call = api.call_action

        async def revoke_after_text(action, **params):
            result = await original_call(action, **params)
            if action == "send_private_msg":
                self.controller.authority.revoke_delivery("first")
            return result

        api.call_action = revoke_after_text
        result = (await self.controller.receive_notice(poke_notice(self.event(identifier="701")))).unwrap()
        self.assertEqual((result["state"], result["poke_back"]), ("delivered", True))
        self.assertEqual([action for action, _ in api.calls], ["send_private_msg", "friend_poke"])

    async def test_probability_skip_is_durable(self):
        self.configure(private="onebot_private", trigger=0.0)
        await self.start_host()
        notice = poke_notice(self.event(identifier="701"))
        self.assertEqual((await self.controller.receive_notice(notice)).unwrap()["state"], "skipped")
        self.assertEqual((await self.controller.receive_notice(notice)).unwrap()["state"], "replayed")
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])


class StagingPokeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="companion-single-poke-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.native = sdk_environment.WebContext()
        self.clock = DeterministicClock.at(NOW)
        self.types = NativeAdapterTypes("4.27.2", FakeEvent, FakeProvider, NS, NS, FakeReverseApi)
        config = sdk_environment.host_config(staging={"enabled": True, "profile_id": "poke-private",
                                                       "delivery_mode": "onebot_private"})
        config["poke"] = {"enabled": True, "replies": ["在呢。"], "back_probability": 0.0}
        self.controller = AstrBotStagingController(self.native, config, self.root,
            native_types=self.types, now=self.clock.now)
        self.addAsyncCleanup(self.controller.terminate)

    async def test_single_target_poke_ignores_legacy_delivery_grant_expiry(self):
        (await self.controller.initialize_profile()).unwrap()
        (await self.controller.authorize_delivery()).unwrap()
        (await self.controller.start()).unwrap()
        notice = poke_notice(FakeEvent(self.native, event_id="701"))
        result = (await self.controller.receive_notice(notice)).unwrap()
        self.assertEqual(result["state"], "delivered")
        self.assertEqual(self.native.platform.client._api._wsr_api.calls[0][0], "send_private_msg")
        self.clock.advance(timedelta(minutes=16))
        expired = poke_notice(FakeEvent(self.native, event_id="702"), time=int(self.clock.now().timestamp()))
        result = (await self.controller.receive_notice(expired)).unwrap()
        self.assertEqual(result["state"], "delivered")
        self.assertEqual(len(self.native.platform.client._api._wsr_api.calls), 2)


if __name__ == "__main__":
    unittest.main()
