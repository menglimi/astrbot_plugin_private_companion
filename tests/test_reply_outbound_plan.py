"""T45/T51: the sealed outbound plan, its configuration, encoding and delivery."""

import asyncio
from dataclasses import replace
import json
import unittest

from companion_v2.configuration.models import ConfigLayer, ConfigTarget, ConfigurationError
from companion_v2.conversation.delivery import DeliveryEnvelope
from companion_v2.conversation.outbound_plan import component_order, outbound_plan
from companion_v2.conversation.reply_settings import (
    GROUP, OUTBOUND_DEFAULTS, OUTBOUND_LIMITS, ReplyConfigurationOwner,
    configuration_fields as reply_configuration_fields, outbound_settings,
)
from companion_v2.conversation.segmentation import SegmentationPolicy
from companion_v2.delivery.components import build_component_plan
from companion_v2.delivery.plan import OutboundPlan, PokePlan, VoicePlan
from companion_v2.execution.models import TaskContext, digest
from tests.group_host_support import GroupFixture, group_config
from tests.native_adapter_support import NativeFixture, completion, final_text
from tests.staging_bootstrap_support import wait_for_batch


RECORD = {"type": "record", "data": {"file": "base64://YQ=="}}
SEALED_INPUTS = (("7001", "200"),)
REPLY_TEXT = "我也说一句。"


class OutboundPlanContractTests(unittest.TestCase):
    """The derivation is pure, so generation and delivery see the same additions."""

    def test_component_order_accepts_only_declared_kinds(self):
        self.assertEqual(component_order({}), ())
        self.assertEqual(component_order({"component_order": '["at", "text"]'}), ("at", "text"))
        for value in ('{"at": 1}', '["at", "at"]', '["at", "unknown"]',
                      '["at", "text", "image", "voice", "face", "at"]', "not json", 12):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    component_order({"component_order": value})

    def test_outbound_defaults_disable_quoting_and_keep_current_placement(self):
        self.assertEqual(outbound_settings({}), OUTBOUND_DEFAULTS)
        self.assertEqual(OUTBOUND_DEFAULTS["component_order"], "[]")
        self.assertFalse(OUTBOUND_DEFAULTS["reply_quote_enabled"])
        with self.assertRaises(ConfigurationError):
            outbound_settings({"reply_quote_enabled": "yes"})
        with self.assertRaises(ConfigurationError):
            outbound_settings({"reply_quote_skip_chars": OUTBOUND_LIMITS["reply_quote_skip_chars"][1] + 1})
        with self.assertRaises(ConfigurationError):
            outbound_settings({"component_order": '["unknown"]'})

    def test_configuration_fields_register_every_outbound_setting(self):
        fields = {field.field_id: field
                  for field in reply_configuration_fields(SegmentationPolicy(), True, 0.0)}
        for name, default in OUTBOUND_DEFAULTS.items():
            field = fields[GROUP + "." + name]
            self.assertEqual(field.default, default)
            self.assertEqual(field.owner_id, GROUP)
        minimum, maximum = OUTBOUND_LIMITS["reply_quote_skip_chars"]
        self.assertEqual((fields[GROUP + ".reply_quote_skip_chars"].minimum,
                          fields[GROUP + ".reply_quote_skip_chars"].maximum), (minimum, maximum))

    def test_group_reply_quotes_the_sealed_trigger_only_once(self):
        settings = {"reply_quote_enabled": True}
        first = outbound_plan(settings=settings, scope_kind="group", sequence=1, first_text_sequence=1,
                              text=REPLY_TEXT, inputs=SEALED_INPUTS)
        self.assertEqual(first.document(), {"quote": "7001"})
        later = outbound_plan(settings=settings, scope_kind="group", sequence=2, first_text_sequence=1,
                              text="再补一句。", inputs=SEALED_INPUTS)
        self.assertIsNone(later)
        every = outbound_plan(settings={**settings, "reply_quote_first_part_only": False}, scope_kind="group",
                              sequence=2, first_text_sequence=1, text="再补一句。", inputs=SEALED_INPUTS)
        self.assertEqual(every.document(), {"quote": "7001"})

    def test_quote_respects_switches_short_replies_and_private_scope(self):
        self.assertIsNone(outbound_plan(settings={}, scope_kind="group", sequence=1, first_text_sequence=1,
                                        text=REPLY_TEXT, inputs=SEALED_INPUTS))
        self.assertIsNone(outbound_plan(settings={"reply_quote_enabled": True, "reply_quote_group_enabled": False},
                                        scope_kind="group", sequence=1, first_text_sequence=1, text=REPLY_TEXT,
                                        inputs=SEALED_INPUTS))
        self.assertIsNone(outbound_plan(settings={"reply_quote_enabled": True, "reply_quote_skip_chars": 4},
                                        scope_kind="group", sequence=1, first_text_sequence=1, text=" 你好 ",
                                        inputs=SEALED_INPUTS))
        self.assertIsNone(outbound_plan(settings={"reply_quote_enabled": True}, scope_kind="private", sequence=1,
                                        first_text_sequence=1, text=REPLY_TEXT, inputs=SEALED_INPUTS))
        self.assertIsNone(outbound_plan(settings={"reply_quote_enabled": True}, scope_kind="group", sequence=1,
                                        first_text_sequence=1, text=REPLY_TEXT,
                                        inputs=(("event-1", "user-1"),)))

    def test_negative_platform_message_ids_still_quote_and_address(self):
        # OneBot v11 ids are signed: SnowLuma hands out negative message ids, and
        # rejecting them silently disabled 引用触发消息 on those hosts.
        settings = {"reply_quote_enabled": True}
        quoted = outbound_plan(settings=settings, scope_kind="group", sequence=1, first_text_sequence=1,
                               text=REPLY_TEXT, inputs=(("-7001", "-1625457822"),))
        self.assertEqual(quoted.document(), {"quote": "-7001"})
        addressed = outbound_plan(settings={"reply_mention_speaker_enabled": True}, scope_kind="group",
                                  sequence=1, first_text_sequence=1, text=REPLY_TEXT,
                                  inputs=(("-7001", "-1625457822"),))
        self.assertEqual(addressed.document(), {"mentions": ["-1625457822"]})

    def test_addressing_follows_the_same_sealed_trigger(self):
        plan = outbound_plan(settings={"reply_mention_speaker_enabled": True}, scope_kind="group", sequence=1,
                             first_text_sequence=1, text=REPLY_TEXT, inputs=SEALED_INPUTS)
        self.assertEqual(plan.document(), {"mentions": ["200"]})
        self.assertIsNone(outbound_plan(settings={}, scope_kind="group", sequence=1, first_text_sequence=1,
                                        text=REPLY_TEXT, inputs=SEALED_INPUTS))

    def test_component_order_replaces_defaults_for_every_declared_kind(self):
        plan = build_component_plan("可见文字", ("image",), RECORD, order=("voice", "at", "text", "face", "image"),
                                    mentions=("200",), faces=(12,), speech="可见文字")
        self.assertEqual(plan.kinds, ("voice", "at", "text", "face", "image"))
        with self.assertRaises(ValueError):
            build_component_plan("可见文字", order=("unknown",))
        # Unusable identifiers are dropped instead of refusing the whole reply.
        dropped = build_component_plan("可见文字", mentions=("", "user-1", "0"), faces=(0, "12", 7))
        self.assertEqual(dropped.kinds, ("text", "face"))
        self.assertEqual(dropped.components[-1].value, 7)

    def test_component_plan_strips_paired_control_tags_and_keeps_sentinels(self):
        plan = build_component_plan(
            '正文<pc_reaction_expression>{"emotion":"无奈"}</pc_reaction_expression>')
        self.assertEqual(plan.components[0].value, "正文")
        leaked = build_component_plan(
            '好开心<pc_reaction_expression>{"x":1}[[PCTTS:abc123]]')
        self.assertEqual(leaked.components[0].value, "好开心[[PCTTS:abc123]]")

    def test_outbound_plan_rejects_unusable_identifiers(self):
        for quote in ("0", "01", "event-1", 12, "x" * 21):
            with self.subTest(quote=quote):
                with self.assertRaises(ValueError):
                    OutboundPlan(quote)
        with self.assertRaises(ValueError):
            OutboundPlan("1", ("200", "200"))
        with self.assertRaises(ValueError):
            OutboundPlan("1", (), (1, 1))
        with self.assertRaises(ValueError):
            OutboundPlan("1", (), (), ("text", "text"))


class OneBotOutboundPlanEncodingTests(NativeFixture, unittest.IsolatedAsyncioTestCase):
    async def envelope(self):
        await super().envelope()
        return DeliveryEnvelope("part-1", "operation-1", self.native_context, 1, "你好", digest("你好"),
                                self.native_sender.binding, lambda: True)

    async def test_quote_mention_face_and_order_reach_the_platform_payload(self):
        envelope = await self.envelope()
        selected = replace(envelope, plan=OutboundPlan("7001", ("200",), (12,), ("at", "text", "face")))
        api = self.native.platform.client._api._wsr_api
        self.assertEqual((await self.native_sender.send(selected)).status, "delivered")
        message = api.calls[-1][1]["message"]
        self.assertEqual([component["type"] for component in message], ["reply", "at", "text", "face"])
        self.assertEqual(message[0]["data"], {"id": "7001"})
        self.assertEqual(message[1]["data"], {"qq": "200"})
        self.assertEqual(message[2]["data"], {"text": "你好"})
        self.assertEqual(message[3]["data"], {"id": "12"})

    async def test_unsealed_envelope_keeps_plain_text_encoding(self):
        envelope = await self.envelope()
        api = self.native.platform.client._api._wsr_api
        self.assertEqual((await self.native_sender.send(envelope)).status, "delivered")
        self.assertEqual([component["type"] for component in api.calls[-1][1]["message"]], ["text"])

    async def test_sealed_voice_plan_renders_without_a_conversation_claim(self):
        envelope = await self.envelope()
        rendered = []

        async def voice(_, text, *, provider_id="", timeout_seconds=30):
            rendered.append((text, provider_id, timeout_seconds))
            return RECORD

        self.native_sender.voice = voice
        selected = replace(envelope, voice=VoicePlan("你好", position="after_text", timeout_seconds=12))
        api = self.native.platform.client._api._wsr_api
        self.assertEqual((await self.native_sender.send(selected)).status, "delivered")
        self.assertEqual(rendered, [("你好", "", 12)])
        sent = [component["type"] for call in api.calls
                for component in call[1].get("message", ())]
        # A voice plan may be emitted as one combined message or as a text
        # message followed by a record, depending on the native adapter.
        self.assertEqual([kind for kind in sent if kind in {"text", "record"}], ["text", "record"])

    async def test_sealed_poke_precedes_the_message_as_its_own_action(self):
        envelope = await self.envelope()
        api = self.native.platform.client._api._wsr_api
        self.assertEqual((await self.native_sender.send(replace(envelope, poke=PokePlan(2)))).status, "delivered")
        self.assertEqual([action for action, _ in api.calls], ["friend_poke", "friend_poke", "send_private_msg"])
        self.assertEqual(api.calls[0][1]["user_id"], int(envelope.context.scope.native_scope_id))

    async def test_unconfirmed_poke_never_blocks_the_sealed_message(self):
        envelope = await self.envelope()
        api = self.native.platform.client._api._wsr_api
        original = api.call_action

        async def flaky(action, **params):
            if action.endswith("_poke"):
                api.calls.append((action, params))
                raise TimeoutError("the poke result is unknown")
            return await original(action, **params)

        api.call_action = flaky
        self.assertEqual((await self.native_sender.send(replace(envelope, poke=PokePlan(1)))).status, "delivered")
        self.assertEqual([action for action, _ in api.calls], ["friend_poke", "send_private_msg"])
        self.assertEqual([component["type"] for component in api.calls[-1][1]["message"]], ["text"])


class GroupQuoteDeliveryTests(GroupFixture, unittest.IsolatedAsyncioTestCase):
    """The sealed group turn, not the adapter, decides whether a quote is attached."""

    @property
    def app(self):
        return self.controller.session.application

    @property
    def sent(self):
        return self.native.platform.client._api._wsr_api.calls

    async def setup_delivery(self):
        self.controller = self.make_controller(group_config(group_delivery_mode="onebot_group"))
        await self.start_host()

    async def settle(self, batch):
        await wait_for_batch(self.controller.session, batch)

        async def wait():
            while any(work.batch_id == batch for work, _ in self.app._workers.values()):
                await asyncio.sleep(0.005)
        await asyncio.wait_for(wait(), 3)

    async def receive(self, identifier, *, text="两位成员还在继续讨论", sender="200"):
        async def respond(payload, tools):
            return completion(final_text(REPLY_TEXT))
        self.native.provider.client.state.behavior = respond
        event = self.group_event(identifier=identifier, sender=sender, text=text)
        batch = (await self.controller.receive_claimed(event)).unwrap()
        await self.settle(batch)
        return TaskContext.restore(json.loads(self.app.messages.batch(batch)["context_json"])), batch

    def configure(self, context, changes):
        """Write this group window's sealed reply settings through their owner."""
        target = ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW, context.role_binding.role,
                              context.scope)
        runtime = self.app.runtime
        owner = ReplyConfigurationOwner(runtime)
        fields = reply_configuration_fields(SegmentationPolicy(), True, 0.0)
        revision = owner.read(target, fields).revision
        owner.write(
            target, fields,
            changes={GROUP + "." + name: value for name, value in changes.items()}, reset=(),
            expected_revision=revision, observed_at=self.clock.now())

    def message_types(self, index=-1):
        return [component["type"] for component in self.sent[index][1]["message"]]

    async def test_sealed_quote_and_mention_reach_the_group_message(self):
        await self.setup_delivery()
        context, _ = await self.receive("7001")
        self.assertEqual(self.message_types(), ["text"])
        self.configure(context, {"reply_quote_enabled": True, "reply_mention_speaker_enabled": True})
        _, second = await self.receive("7002")
        self.assertEqual(self.message_types(), ["reply", "at", "text"])
        message = self.sent[-1][1]["message"]
        self.assertEqual(message[0]["data"], {"id": "7002"})
        self.assertEqual(message[1]["data"], {"qq": "200"})
        parts = self.app.runtime.read_all("SELECT payload_hash FROM conversation_parts WHERE batch_id = ?",
                                          (second,))
        self.assertEqual(len(parts), 1)

    async def test_short_reply_threshold_and_disabled_switch_leave_the_turn_unquoted(self):
        await self.setup_delivery()
        context, _ = await self.receive("7003")
        self.configure(context, {"reply_quote_enabled": True, "reply_quote_skip_chars": 12})
        await self.receive("7004")
        self.assertEqual(self.message_types(), ["text"])
        self.configure(context, {"reply_quote_enabled": False, "reply_quote_skip_chars": 0})
        await self.receive("7005")
        self.assertEqual(self.message_types(), ["text"])

    async def test_component_order_is_captured_by_the_next_sealed_turn(self):
        await self.setup_delivery()
        context, _ = await self.receive("7006")
        self.configure(context, {"component_order": '["image", "text", "at"]',
                                 "reply_mention_speaker_enabled": True})
        _, batch = await self.receive("7007")
        captured = json.loads(self.app.messages.batch(batch)["contributions_json"])["reply_settings"]["settings"]
        self.assertEqual(captured["component_order"], '["image", "text", "at"]')
        self.assertEqual(self.message_types(), ["text", "at"])


if __name__ == "__main__":
    unittest.main()
