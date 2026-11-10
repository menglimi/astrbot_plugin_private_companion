"""Group image choices, ordered wire payloads, history and confirmed Bot quotes."""

import base64
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from companion_v2.adapters.astrbot.model_wire import tool_alias
from companion_v2.conversation.media.reply import OWNER
from companion_v2.delivery.media import ImageDeliveryEnvelope
from companion_v2.execution.models import TaskError, digest, encode
from companion_v2.foundation.refs import AudienceRef
from tests.animation_support import animation_bytes
from tests.forward_support import node_raw
from tests.group_image_delivery_support import GroupImageDeliveryFixture
from tests.group_media_support import sdk_image, Plain, Reply
from tests.group_quote_media_support import Forward
from tests.image_delivery_support import offered_images, image_reply
from tests.native_adapter_support import completion
from tests.staging_bootstrap_support import wait_for_batch
from tests.test_conversation_images import image_bytes


class GroupImageDeliveryTests(GroupImageDeliveryFixture, unittest.IsolatedAsyncioTestCase):
    async def test_preview_projects_ordered_image_metadata_without_confirmed_send(self):
        await self.start_host()
        context, batch = await self.receive_parts([sdk_image()])
        preview = self.controller.preview("group-a", batch).unwrap()
        self.assertEqual(preview["delivery_mode"], "preview")
        self.assertFalse(preview["sent"])
        self.assertEqual(len(preview["parts"][0]["images"]), 1)
        self.assertNotIn("base64", encode(preview))
        self.assertFalse(self.app.messages.history(context).unwrap()["items"])
        self.assertFalse(self.sends())

    async def test_ordered_originals_attach_only_to_last_part_and_history_keeps_metadata(self):
        async def reverse(payload, _):
            return image_reply(payload, text="第一段\n这是原图。", handles=list(reversed(offered_images(payload))))
        self.native.provider.client.state.behavior = reverse
        await self.with_delivery()
        self.app.generator.split = lambda text: tuple(text.splitlines(keepends=True))
        body = animation_bytes(count=6)
        a, first = await self.receive_parts([sdk_image(body), sdk_image(image_bytes("blue"))])
        sends = self.sends()
        self.assertEqual([[part["type"] for part in message["message"]] for message in sends], [["text"], ["text", "image", "image"]])
        self.assertEqual([base64.b64decode(part["data"]["file"].removeprefix("base64://")) for part in sends[-1]["message"][1:]],
                         [image_bytes("blue"), body])
        parts = self.parts(first)
        self.assertEqual(parts[0]["payload_hash"], digest(parts[0]["text"]))
        self.assertNotEqual(parts[1]["payload_hash"], digest(parts[1]["text"]))
        self.assertTrue(all(p["state"] == "delivered" for p in parts))
        manifest = self.app.runtime.read_one("SELECT payload_json FROM sources WHERE owner = ?", (OWNER,))[0]
        self.assertNotIn("base64", manifest)
        self.native.provider.client.state.behavior = None
        b, later = await self.receive_parts([Plain("下一个成员接话")], sender="201")
        request = self.request(b, later)
        self.assertFalse(request.images or request.reply_images)
        bot_history = [json.loads(m.payload) for m in request.context.materials
                       if m.purpose == "group_conversation_history" and json.loads(m.payload)["type"] == "confirmed_group_reply"]
        self.assertEqual(len(bot_history), 2)
        self.assertNotIn("images", bot_history[0])
        self.assertEqual([i["content_hash"] for i in bot_history[1]["images"]],
                         [i["content_hash"] for i in self.controller.preview("group-a", first).unwrap()["parts"][-1]["images"]])
        self.assertTrue(all(i["view"] == "historical_metadata_only" for i in bot_history[1]["images"]))
        self.assertNotIn("base64", encode(bot_history))

    async def test_quoted_forward_animation_and_tool_continuation_keep_original_handles(self):
        views = []
        async def respond(payload, _):
            views.append(offered_images(payload))
            if len(views) == 1:
                return completion(tools=[NS(id="group-images-query", type="function", function=NS(
                    name=tool_alias("group.memory.query"), arguments="{}"))])
            return image_reply(payload)
        self.native.provider.client.state.behavior = respond
        await self.with_delivery()
        body = animation_bytes(count=6)
        self.store(parts=[Forward(id="group-quoted-forward")], nodes=[node_raw(sender="999", images=(body,))])
        context, batch = await self.quoted()
        self.assertEqual(len(views), 2)
        self.assertEqual(views[0], views[1])
        self.assertEqual(len(views[0]), 1)
        self.assertEqual(base64.b64decode(self.sends()[0]["message"][1]["data"]["file"].removeprefix("base64://")), body)
        self.assertEqual(self.app.flow.row(batch)["state"], "complete")
        self.assertEqual(self.app.runtime.read_one("SELECT COUNT(*) FROM memory_atoms", ())[0], 0)

    async def test_exact_group_account_and_member_session_choose_image_transport(self):
        await self.with_delivery()
        for index, target in enumerate(("group-a", "group-b", "group-c")):
            context, batch = await self.receive_parts([sdk_image()], target=target, unique=True, sender=str(701 + index))
            configured = self.controller.settings.target(target)
            sent = self.sends(configured.platform_id)[-1]
            self.assertEqual((sent["self_id"], sent["group_id"]), (configured.account_id, int(configured.group_id)))
            self.assertEqual([part["type"] for part in sent["message"]], ["text", "image"])
            self.assertEqual(context.scope.session_id, str(701 + index) + "_" + configured.group_id)
            self.assertEqual(self.parts(batch)[0]["state"], "delivered")

    async def test_confirmed_bot_images_can_be_quoted_and_remain_linked_to_originals(self):
        await self.with_delivery()
        a, first = await self.receive_parts([sdk_image(animation_bytes(count=6))], sender="201")
        identifier = self.sent_original(first)
        b, quoted = await self.receive_parts([Reply(id=identifier)])
        self.assertEqual(len(self.request(b, quoted).images), 1)
        self.assertEqual(self.sends()[0]["message"], self.sends()[1]["message"])
        quote = self.input(b, quoted).quotes[0]
        self.assertEqual(quote.sender_id, "100")
        self.app.withdraw(a, self.input(a, first).scoped_key).unwrap()
        await self.restart()
        with self.assertRaises(TaskError):
            self.request(b, quoted)
        self.assertFalse((await self.controller.receive_claimed(self.media_event([Reply(id=identifier)]))).is_success)
        self.assertEqual(len(self.sends()), 2)

    async def test_bot_quote_changed_missing_or_reordered_images_roll_back_admission(self):
        await self.with_delivery()
        _, first = await self.receive_parts([sdk_image(), sdk_image(image_bytes("blue"))])
        identifier = self.sent_original(first)
        original = deepcopy(self.records[identifier])
        mutations = (
            lambda raw: raw["message"][1]["data"].update(file=sdk_image(image_bytes("green")).file),
            lambda raw: raw["message"].pop(),
            lambda raw: raw["message"].__setitem__(slice(1, 3), list(reversed(raw["message"][1:]))),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                self.records[identifier] = deepcopy(original)
                mutate(self.records[identifier])
                result = await self.controller.receive_claimed(self.media_event([Reply(id=identifier)], sender="777"))
                self.assertEqual(result.failure.code, "quote_original_changed")
                self.assertEqual(self.app.runtime.read_one("SELECT COUNT(*) FROM conversation_inputs", ())[0], 1)
        self.assertEqual(len(self.sends()), 1)

    async def test_other_request_handles_cannot_send_another_groups_originals(self):
        await self.with_delivery()
        await self.receive_parts([sdk_image()], target="group-a")
        prior = offered_images(self.native.provider.client.state.calls[-1][0])
        async def foreign(payload, _):
            self.assertNotEqual(prior, offered_images(payload))
            return image_reply(payload, handles=prior)
        self.native.provider.client.state.behavior = foreign
        batch = (await self.controller.receive_claimed(self.media_event([sdk_image()], target="group-b"))).unwrap()
        await wait_for_batch(self.controller.session, batch)
        self.assertEqual(self.app.flow.row(batch)["state"], "blocked")
        self.assertFalse(self.parts(batch))
        self.assertEqual(len(self.sends()), 1)

    async def test_envelope_cannot_change_group_or_collective_audience(self):
        await self.start_host()
        a, first = await self.receive_parts([sdk_image()], target="group-a")
        b, _ = await self.receive_parts([Plain("另一个群")], target="group-b")
        images = self.request(a, first).images
        from companion_v2.delivery.media import payload_hash
        values = dict(part_id="part", operation_id="operation", sequence=1, text="原图",
                      payload_hash=payload_hash("原图", images), sender=self.controller.session.sender_binding, images=images)
        for context in (b, replace(a, audience=AudienceRef(a.scope, (a.actor.subject,)))):
            with self.subTest(scope=context.scope), self.assertRaises((TaskError, ValueError)):
                ImageDeliveryEnvelope(context=context, **values)

    async def test_sealed_history_image_reads_share_the_ancestry_budget(self):
        await self.with_delivery()
        _, first = await self.receive_parts([sdk_image()])
        identifier = self.sent_original(first)
        b, quoted = await self.receive_parts([Reply(id=identifier)], sender="201")
        with patch("companion_v2.conversation.group_history.MAX_ANCESTORS", 1):
            with self.assertRaises(TaskError) as caught:
                self.request(b, quoted)
        self.assertEqual(caught.exception.code, "group_history_budget")
