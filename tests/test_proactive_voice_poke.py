"""Proactive voice and poke: sealed presentation plans that never join the fingerprint."""

import asyncio
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from companion_v2.configuration.registry import ValueKind
from companion_v2.conversation.delivery import DeliveryEnvelope
from companion_v2.conversation.reply_management import ReplyManagement
from companion_v2.delivery.plan import DeliveryContent, OutboundPlan, PokePlan, VoicePlan
from companion_v2.delivery.service import DeliveryRequest
from companion_v2.execution.models import TERMINAL, digest
from companion_v2.foundation.outcome import Outcome
from companion_v2.proactive.configuration import GROUP, configuration_fields
from companion_v2.proactive.prompts import contact_output
from tests.native_adapter_support import FakeReverseApi, NativeFixture
from tests.proactive_support import ContactFixture
from tests.test_role_tool_loop import answered


DEFAULTS = {"voice_action_enabled": False, "voice_action_max_chars": 30, "voice_action_cooldown_minutes": 60,
            "poke_action_enabled": False, "poke_action_daily_limit": 1, "poke_action_cooldown_minutes": 30}
LIMITS = {"voice_action_max_chars": (6, 80), "voice_action_cooldown_minutes": (0, 1440),
          "poke_action_daily_limit": (0, 20), "poke_action_cooldown_minutes": (0, 1440)}


class ProactiveVoicePokeTests(ContactFixture, unittest.IsolatedAsyncioTestCase):
    """The model expresses intent; sealed contacts and their accounting decide what leaves."""

    async def enabled_actions(self, **changes):
        host = await self.start(self.host())
        app = host.application
        self.settings(app, {"enabled": True, **changes}).unwrap()
        return app

    def choose(self, text="想起你上次提到的书，读到哪里了？", *, voice=True, poke=True, confidence=None, speech=None):
        decision = {"decision": "send", "text": text}
        if voice:
            decision["voice_intent"] = True
        if poke:
            decision["poke_intent"] = True
        if confidence is not None:
            decision["decision_confidence"] = confidence
        if speech is not None:
            decision["voice_text"] = speech

        async def respond(_envelope):
            return answered(json.dumps(decision, ensure_ascii=False))
        self.model_behavior = respond

    def record(self, task_id):
        return json.loads(self.runtime.read_one(
            "SELECT record_json FROM proactive_contacts WHERE task_id = ?", (task_id,))["record_json"])

    def raw_record(self, task_id):
        return self.runtime.read_one("SELECT record_json FROM proactive_contacts WHERE task_id = ?", (task_id,))["record_json"]

    def write_record(self, task_id, document):
        if not isinstance(document, str):
            document = json.dumps(document, ensure_ascii=False)
        with self.runtime.transaction() as connection:
            connection.execute("UPDATE proactive_contacts SET record_json = ? WHERE task_id = ?", (document, task_id))

    async def send_once(self, app, identifier="opportunity"):
        task = (await self.contact(app, self.opportunity(identifier))).unwrap().task
        # The fixture runs one background slot at a time: wait for the delivery
        # task itself to settle before a second contact may claim it.
        row = await self.delivery_state(app, task.task_id, "delivered")
        await self.until(lambda: app.runtime.read_one("SELECT state FROM execution_tasks WHERE task_id = ?",
                                                       (row["task_id"],))[0] in TERMINAL)
        return task

    def test_configuration_defaults_are_off_and_bounded(self):
        fields = {field.field_id: field for field in configuration_fields()}
        for name, default in DEFAULTS.items():
            field = fields[GROUP + "." + name]
            self.assertEqual(field.default, default, name)
            self.assertIs(field.kind, ValueKind.BOOLEAN if type(default) is bool else ValueKind.INTEGER)
            if name in LIMITS:
                self.assertEqual((field.minimum, field.maximum), LIMITS[name], name)

    async def test_out_of_range_and_wrongly_typed_settings_are_rejected(self):
        app = self.host().application
        for changes in ({"voice_action_max_chars": 5}, {"voice_action_max_chars": 81},
                        {"voice_action_cooldown_minutes": -1}, {"voice_action_cooldown_minutes": 1441},
                        {"poke_action_daily_limit": 21}, {"poke_action_cooldown_minutes": 1441},
                        {"voice_action_enabled": 1}, {"poke_action_daily_limit": "1"}):
            with self.subTest(changes=changes):
                self.assertFalse(self.settings(app, changes).is_success)
        self.assertEqual(self.runtime.read_one("SELECT COUNT(*) FROM configuration_changes", ())[0], 0)

    def test_contact_output_bounds_the_two_expression_intents(self):
        for decision in ({"decision": "send", "text": "正文", "voice_intent": "yes"},
                         {"decision": "send", "text": "正文", "poke_intent": 1},
                         {"decision": "send", "text": "正文", "voice_text": "台词"},
                         {"decision": "send", "text": "正文", "voice_intent": True, "voice_text": " "},
                         {"decision": "send", "text": "正文", "voice_intent": True, "voice_text": 1},
                         {"decision": "send", "text": "正文", "voice_intent": True, "voice_text": "长" * 2001}):
            with self.subTest(decision=decision):
                with self.assertRaises(ValueError):
                    contact_output(json.dumps(decision, ensure_ascii=False))
        for decision in ({"decision": "skip", "text": "", "voice_intent": True},
                         {"decision": "skip", "text": "", "poke_intent": False}):
            with self.subTest(decision=decision):
                with self.assertRaises(ValueError):
                    contact_output(json.dumps(decision, ensure_ascii=False))
        self.assertEqual(contact_output(json.dumps(
            {"decision": "send", "text": "正文", "voice_intent": True, "poke_intent": False},
            ensure_ascii=False)), ("正文", None))

    async def test_disabled_actions_seal_and_deliver_neither_plan(self):
        app = await self.enabled_actions()
        self.choose()
        task = await self.send_once(app)
        data = self.record(task.task_id)
        self.assertNotIn("voice", data)
        self.assertNotIn("poke", data)
        self.assertIsNone(self.sent[0].voice)
        self.assertIsNone(self.sent[0].poke)
        self.assertNotIn("voice_intent", self.called[0].original.instruction)
        self.assertNotIn("poke_intent", self.called[0].original.instruction)

    async def test_enabled_intents_seal_plans_outside_the_payload_fingerprint(self):
        app = await self.enabled_actions(voice_action_enabled=True, poke_action_enabled=True,
                                         voice_action_max_chars=12, poke_action_cooldown_minutes=0)
        self.choose(text="想起你上次提到的那本书，\n读到哪里了？我也有点想听你说。")
        task = await self.send_once(app)
        body = self.runtime.read_one("SELECT body FROM proactive_contacts WHERE task_id = ?", (task.task_id,))["body"]
        data = self.record(task.task_id)
        self.assertEqual(data["voice"]["speech"], " ".join(body.split()))
        self.assertNotIn("\n", data["voice"]["speech"])
        self.assertEqual((data["voice"]["provider_id"], data["voice"]["position"], data["voice"]["delivery_mode"]),
                         ("", "after_media", "voice_and_text"))
        self.assertEqual(data["poke"], {"count": 1})
        instruction = self.called[0].original.instruction
        self.assertIn("voice_intent", instruction)
        self.assertIn("不需要等用户明确要求语音", instruction)
        self.assertIn("不要因为示例通常只写文字", instruction)
        self.assertIn("早呀，今天想听你讲讲近况", instruction)
        self.assertIn("poke_intent", instruction)
        self.assertIn("不代表", instruction)
        row = app.proactive._delivery_row(task.task_id)
        request = DeliveryRequest.restore(row["request_json"])
        content = app.proactive.delivery_text(request, self.clock.now()).unwrap()
        self.assertIsInstance(content, DeliveryContent)
        self.assertEqual(content.text, body)
        self.assertEqual(content.voice, VoicePlan.parse(data["voice"]))
        self.assertEqual(content.poke, PokePlan.parse(data["poke"]))
        # The sealed fingerprint still covers the text alone: presentation side
        # effects may fail without invalidating an already-authorized message.
        self.assertEqual(digest(body), row["payload_hash"])
        self.assertEqual(self.sent[0].payload_hash, row["payload_hash"])
        self.assertIsInstance(self.sent[0], DeliveryEnvelope)
        self.assertEqual(self.sent[0].voice, VoicePlan.parse(data["voice"]))
        self.assertEqual(self.sent[0].poke, PokePlan(count=1))

    async def test_foreign_voice_uses_window_language_and_provider_without_cutting_the_body(self):
        app = await self.enabled_actions(voice_action_enabled=True, voice_action_max_chars=6)
        manager = ReplyManagement(app, self.contact_context())
        status = manager.status(self.core).unwrap()
        manager.configure(self.core, {"operation_key": "japanese-proactive", "edit_token": status["edit_token"],
            "changes": {"voice_language": "ja", "voice_ja_provider_id": "japanese-tts",
                        "voice_provider_id": "default-tts", "voice_timeout_seconds": 17}}).unwrap()
        text, speech = "烛雨大人那边现在也安静下来了吗？", "そちらも、もう静かになりましたか？"
        self.choose(text=text, speech=speech, poke=False)
        task = await self.send_once(app)
        data = self.record(task.task_id)
        self.assertEqual(data["voice_language"], "ja")
        self.assertEqual(data["voice"]["speech"], speech)
        self.assertEqual(data["voice"]["provider_id"], "japanese-tts")
        self.assertEqual(data["voice"]["timeout_seconds"], 17)
        self.assertEqual(self.sent[0].text, text)
        self.assertIn("voice_text 使用自然日语", self.called[0].original.instruction)
        self.assertIn("不会按字数截断", self.called[0].original.instruction)

    async def test_chinese_voice_reads_the_visible_text_even_when_model_rewrites_it(self):
        app = await self.enabled_actions(voice_action_enabled=True)
        text = "喂，外卖到底还要多久啊？本小姐都要饿扁了。"
        self.choose(text=text, speech="喂，外卖到底还要多久啊？", poke=False)
        task = await self.send_once(app)
        data = self.record(task.task_id)
        # 中文朗读必须与用户看到的正文一致：模型改写的短台词会被忽略。
        self.assertEqual(data["voice"]["speech"], text)
        self.assertEqual(self.sent[0].text, text)

    async def test_missing_foreign_speech_preserves_text_without_chinese_fallback(self):
        app = await self.enabled_actions(voice_action_enabled=True)
        manager = ReplyManagement(app, self.contact_context())
        status = manager.status(self.core).unwrap()
        manager.configure(self.core, {"operation_key": "missing-japanese-speech",
            "edit_token": status["edit_token"], "changes": {"voice_language": "ja"}}).unwrap()
        self.choose(text="今晚也想跟你说说话。", poke=False)
        task = await self.send_once(app)
        self.assertNotIn("voice", self.record(task.task_id))
        self.assertEqual(self.sent[0].text, "今晚也想跟你说说话。")
        self.assertIsNone(self.sent[0].voice)

    async def test_voice_cooldown_blocks_a_second_note_until_it_expires(self):
        app = await self.enabled_actions(voice_action_enabled=True, poke_action_enabled=True,
                                         voice_action_cooldown_minutes=60, poke_action_daily_limit=2,
                                         poke_action_cooldown_minutes=0)
        self.choose(text="第一条：想起你上次提到的书。")
        first = await self.send_once(app, "first")
        self.advance(app, 1)
        self.choose(text="第二条：今天路过那家书店了。")
        second = await self.send_once(app, "second")
        self.assertIn("voice", self.record(first.task_id))
        self.assertNotIn("voice", self.record(second.task_id))
        self.assertIsNone(self.sent[1].voice)
        self.assertEqual(self.record(second.task_id)["poke"], {"count": 1})
        self.assertNotIn("voice_intent", self.called[1].original.instruction)
        self.assertIn("poke_intent", self.called[1].original.instruction)
        self.advance(app, 61 * 60)
        self.choose(text="第三条：书店门口在放你喜欢的歌。")
        third = await self.send_once(app, "third")
        self.assertIn("voice", self.record(third.task_id))
        self.assertIn("voice_intent", self.called[2].original.instruction)

    async def test_poke_daily_limit_counts_the_local_day_and_resets_after_local_midnight(self):
        app = await self.enabled_actions(poke_action_enabled=True, poke_action_daily_limit=1,
                                         poke_action_cooldown_minutes=0)
        self.choose(text="第一条：轻轻碰你一下。")
        first = await self.send_once(app, "first")
        self.advance(app, 1)
        self.choose(text="第二条：再碰一下看看。")
        second = await self.send_once(app, "second")
        self.assertEqual(self.record(first.task_id)["poke"], {"count": 1})
        self.assertNotIn("poke", self.record(second.task_id))
        self.assertNotIn("poke_intent", self.called[1].original.instruction)
        # The relationship's own timezone (Asia/Shanghai by default) starts the new day at 16:00 UTC.
        self.advance(app, 16 * 3600)
        self.choose(text="第三条：新的一天开始了。")
        third = await self.send_once(app, "third")
        self.assertEqual(self.record(third.task_id)["poke"], {"count": 1})
        self.assertIn("poke_intent", self.called[2].original.instruction)

    async def test_body_cleared_by_the_transaction_guard_seals_neither_plan(self):
        app = await self.enabled_actions(voice_action_enabled=True, poke_action_enabled=True,
                                         voice_action_cooldown_minutes=0, poke_action_daily_limit=2,
                                         poke_action_cooldown_minutes=0)
        self.choose()
        first = await self.send_once(app, "first")
        self.advance(app, 1)
        # The first check passes and the in-transaction re-check clears the body.
        with patch.object(app.proactive, "_duplicate", side_effect=[False, True]):
            second = (await self.contact(app, self.opportunity("second"))).unwrap().task
        data = self.record(second.task_id)
        self.assertEqual((data["decision"], data["dedup_reason"]), ("skip", "exact_repeat"))
        self.assertNotIn("voice", data)
        self.assertNotIn("poke", data)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("voice", self.record(first.task_id))

    async def test_low_confidence_send_abandons_both_plans(self):
        app = await self.enabled_actions(voice_action_enabled=True, poke_action_enabled=True,
                                         voice_action_cooldown_minutes=0, poke_action_cooldown_minutes=0,
                                         silence_min_confidence=0.8)
        self.choose(text="这句话会被沉默门槛拦下。", confidence=0.4)
        task = (await self.contact(app)).unwrap().task
        data = self.record(task.task_id)
        self.assertEqual((data["decision"], data["silence_reason"]), ("skip", "below_confidence_threshold"))
        self.assertNotIn("voice", data)
        self.assertNotIn("poke", data)
        self.assertEqual(self.sent, [])

    async def test_damaged_sealed_plan_is_unavailable_instead_of_silently_downgraded(self):
        app = await self.enabled_actions(voice_action_enabled=True, poke_action_enabled=True,
                                         voice_action_cooldown_minutes=0, poke_action_cooldown_minutes=0)
        self.choose()
        with patch.object(app.background, "advance", return_value=False):
            task = (await self.contact(app)).unwrap().task
            sealed = self.raw_record(task.task_id)
            document = json.loads(sealed)
            row = app.proactive._delivery_row(task.task_id)
            request = DeliveryRequest.restore(row["request_json"])
            for damaged in ({"speech": ""}, {**document["voice"], "position": "middle"},
                            {**document["voice"], "timeout_seconds": "三十"}, {"speech": document["voice"]["speech"]}):
                with self.subTest(damaged=damaged):
                    self.write_record(task.task_id, {**document, "voice": damaged})
                    self.assertEqual(app.proactive.delivery_text(request, self.clock.now()).failure.code,
                                     "contact_draft_unavailable")
            self.write_record(task.task_id, {**document, "poke": {"count": 0}})
            self.assertEqual(app.proactive.delivery_text(request, self.clock.now()).failure.code,
                             "contact_draft_unavailable")
            self.write_record(task.task_id, sealed)
            self.assertEqual(app.proactive.delivery_text(request, self.clock.now()).unwrap().voice,
                             VoicePlan.parse(document["voice"]))
        app.notify()
        await self.delivery_state(app, task.task_id, "delivered")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0].voice, VoicePlan.parse(document["voice"]))
        self.assertEqual(self.sent[0].poke, PokePlan(count=1))

    async def test_restart_delivers_the_original_sealed_plan_without_another_decision(self):
        app = await self.enabled_actions(voice_action_enabled=True, poke_action_enabled=True,
                                         voice_action_cooldown_minutes=0, poke_action_cooldown_minutes=0)
        self.choose()
        with patch.object(app.background, "advance", return_value=False):
            task = (await self.contact(app)).unwrap().task
            sealed = app.proactive._delivery_row(task.task_id)["request_json"]
            document = self.record(task.task_id)
            await app.close()
        restarted = (await self.start(self.host(runtime=self.open_runtime()),
                                      task_generation=1, model_generation=1)).application
        row = await self.delivery_state(restarted, task.task_id, "delivered")
        self.assertEqual(row["request_json"], sealed)
        self.assertEqual((len(self.called), len(self.sent)), (1, 1))
        self.assertEqual(self.sent[0].voice, VoicePlan.parse(document["voice"]))
        self.assertEqual(self.sent[0].poke, PokePlan(count=1))


class ProactiveVoicePokeNativeTests(NativeFixture, unittest.IsolatedAsyncioTestCase):
    """The sealed plan reaches OneBot as a poke and a real record component."""

    async def sealed(self, **changes):
        await super().envelope()
        base = DeliveryEnvelope("part-1", "operation-1", self.native_context, 1, "晚上好。", digest("晚上好。"),
                                self.native_sender.binding, lambda: True)
        return replace(base, **changes)

    def api(self):
        return self.native.platform.client._api._wsr_api

    async def test_poke_opens_the_message_and_voice_has_an_independent_request(self):
        envelope = await self.sealed(voice=VoicePlan("晚上好，记得吃饭。"), poke=PokePlan(1))
        api = self.api()
        self.assertIsInstance(api, FakeReverseApi)
        spoken = []

        async def voice(_context, speech, *, provider_id="", timeout_seconds=30):
            spoken.append((speech, provider_id))
            return {"type": "record", "data": {"file": "base64://YQ=="}}

        self.native_sender.voice = voice
        receipt = await self.native_sender.send(envelope)
        self.assertEqual(receipt.status, "delivered")
        self.assertEqual([action for action, _ in api.calls], ["friend_poke", "send_private_msg", "send_private_msg"])
        self.assertEqual(api.calls[0][1], {"self_id": "100", "user_id": 200})
        self.assertEqual(spoken, [("晚上好，记得吃饭。", "")])
        self.assertEqual([component["type"] for component in api.calls[1][1]["message"]], ["text"])
        self.assertEqual(api.calls[1][1]["message"][0]["data"]["text"], envelope.text)
        self.assertEqual([component["type"] for component in api.calls[2][1]["message"]], ["record"])

    async def test_voice_timeout_cannot_erase_confirmed_final_text_or_replay_it(self):
        envelope = await self.sealed(text="现在也安静下来了吗？", payload_hash=digest("现在也安静下来了吗？"),
                                     voice=VoicePlan("そちらも静かになりましたか？"))
        api = self.api()

        async def voice(*_, **__):
            return {"type": "record", "data": {"file": "base64://YQ=="}}

        async def send(action, **params):
            api.calls.append((action, params))
            if params["message"][0]["type"] == "record":
                raise TimeoutError("voice may have been sent")
            return {"message_id": 123}

        self.native_sender.voice, api.call_action = voice, send
        receipt = await self.native_sender.send(envelope)
        self.assertEqual(receipt.status, "delivered")
        self.assertTrue(receipt.platform_ref.endswith(":123"))
        self.assertEqual(len(api.calls), 2)
        self.assertEqual(api.calls[0][1]["message"], [{"type": "text", "data": {"text": envelope.text}}])

    async def test_unknown_text_does_not_send_optional_voice(self):
        envelope = await self.sealed(voice=VoicePlan("こんばんは。"))
        api = self.api()

        async def voice(*_, **__):
            return {"type": "record", "data": {"file": "base64://YQ=="}}

        self.native_sender.voice = voice
        api.result = {}
        self.assertEqual((await self.native_sender.send(envelope)).status, "unknown")
        self.assertEqual(len(api.calls), 1)
        self.assertEqual([part["type"] for part in api.calls[0][1]["message"]], ["text"])

    async def test_quoted_text_preserves_leading_voice_as_a_separate_request(self):
        envelope = await self.sealed(voice=VoicePlan("こんばんは。", position="before_text"),
                                     plan=OutboundPlan(quote="123"))

        async def voice(*_, **__):
            return {"type": "record", "data": {"file": "base64://YQ=="}}

        self.native_sender.voice = voice
        self.assertEqual((await self.native_sender.send(envelope)).status, "delivered")
        messages = [params["message"] for _, params in self.api().calls]
        self.assertEqual([[part["type"] for part in message] for message in messages],
                         [["record"], ["reply", "text"]])
        self.assertEqual(messages[1][1]["data"]["text"], envelope.text)

    async def test_leading_voice_followed_by_revocation_remains_unknown_without_sending_text(self):
        envelope = await self.sealed(voice=VoicePlan("こんばんは。", position="before_text"))
        api = self.api()
        original = api.call_action

        async def voice(*_, **__):
            return {"type": "record", "data": {"file": "base64://YQ=="}}

        async def send(action, **params):
            result = await original(action, **params)
            self.native_sender.authorize = lambda: Outcome.failed("authorization_revoked", "withdrawn")
            return result

        self.native_sender.voice, api.call_action = voice, send
        self.assertEqual((await self.native_sender.send(envelope)).status, "unknown")
        self.assertEqual(len(api.calls), 1)
        self.assertEqual([part["type"] for part in api.calls[0][1]["message"]], ["record"])

    async def test_leading_voice_followed_by_qq_text_refusal_is_not_safe_to_replay(self):
        envelope = await self.sealed(voice=VoicePlan("こんばんは。", position="before_text"))
        api = self.api()

        async def voice(*_, **__):
            return {"type": "record", "data": {"file": "base64://YQ=="}}

        async def send(action, **params):
            api.calls.append((action, params))
            if params["message"][0]["type"] == "record":
                return {"message_id": 123}
            return {"retcode": 1200, "message": "EventChecker sendMsg"}

        self.native_sender.voice, api.call_action = voice, send
        self.assertEqual((await self.native_sender.send(envelope)).status, "unknown")
        self.assertEqual(len(api.calls), 2)

    async def test_revocation_after_text_prevents_optional_voice_without_losing_text_receipt(self):
        envelope = await self.sealed(voice=VoicePlan("こんばんは。"))
        api = self.api()
        original = api.call_action

        async def voice(*_, **__):
            return {"type": "record", "data": {"file": "base64://YQ=="}}

        async def send(action, **params):
            result = await original(action, **params)
            self.native_sender.authorize = lambda: Outcome.failed("authorization_revoked", "withdrawn")
            return result

        self.native_sender.voice, api.call_action = voice, send
        receipt = await self.native_sender.send(envelope)
        self.assertEqual(receipt.status, "delivered")
        self.assertEqual(len(api.calls), 1)
        self.assertEqual([part["type"] for part in api.calls[0][1]["message"]], ["text"])

    async def test_cancelled_optional_voice_preserves_the_confirmed_text_receipt(self):
        envelope = await self.sealed(voice=VoicePlan("こんばんは。"))
        api = self.api()

        async def voice(*_, **__):
            return {"type": "record", "data": {"file": "base64://YQ=="}}

        async def send(action, **params):
            api.calls.append((action, params))
            if params["message"][0]["type"] == "record":
                raise asyncio.CancelledError
            return {"message_id": 789}

        self.native_sender.voice, api.call_action = voice, send
        receipt = await self.native_sender.send(envelope)
        self.assertEqual(receipt.status, "delivered")
        self.assertTrue(receipt.platform_ref.endswith(":789"))
        self.assertEqual(len(api.calls), 2)

    async def test_unconfirmed_poke_never_blocks_the_sealed_text(self):
        envelope = await self.sealed(poke=PokePlan(1))
        api = self.api()
        original = api.call_action

        async def failing(action, **params):
            if action == "friend_poke":
                api.calls.append((action, params))
                raise TimeoutError("the poke response might be lost")
            return await original(action, **params)

        api.call_action = failing
        self.assertEqual((await self.native_sender.send(envelope)).status, "delivered")
        self.assertEqual([action for action, _ in api.calls], ["friend_poke", "send_private_msg"])

    async def test_text_only_envelope_keeps_its_single_call_contract(self):
        envelope = await self.sealed()
        api = self.api()
        self.assertEqual((await self.native_sender.send(envelope)).status, "delivered")
        self.assertEqual([action for action, _ in api.calls], ["send_private_msg"])
        self.assertEqual([component["type"] for component in api.calls[0][1]["message"]], ["text"])


if __name__ == "__main__":
    unittest.main()
