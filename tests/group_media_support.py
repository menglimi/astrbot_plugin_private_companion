"""Synthetic group originals and media, using real SDK image/reply components."""

from copy import deepcopy
from dataclasses import replace
from functools import partial
import json

from tests.group_host_support import GroupFixture, group_config
from tests.native_adapter_support import FakeReverseApi
from tests.test_conversation_images import FakeImage, image_bytes
from tests.test_conversation_quotes import quote_raw
from tests.staging_bootstrap_support import wait_for_batch
from tests.audio_support import FakeRecord
from tests.video_support import FakeVideo
from astrbot.core.message.components import Image, Plain, Reply
from astrbot_host.audio.loader import load_native_audio
from astrbot_host.media import load_native_image
from astrbot_host.video.loader import load_native_video
from companion_v2.adapters.astrbot.group_quotes import read_group_quote
from companion_v2.adapters.astrbot.quotes import read_private_quote
from companion_v2.conversation.messages import event_from_document
from companion_v2.execution.models import TaskContext


def group_raw(*, group="300", account="100", sender="201", **kwargs):
    return {**quote_raw(sender=sender, **kwargs), "message_type": "group", "sub_type": "normal",
            "group_id": int(group), "self_id": int(account)}


def sdk_image(content=None):
    return Image(file=FakeImage(content or image_bytes()).file)


class GroupMediaFixture(GroupFixture):
    batch_timeout_seconds = 4

    def setUp(self):
        super().setUp()
        self.types = replace(self.types, image=Image, image_loader=load_native_image, reply=Reply,
            quote_loader=partial(read_private_quote, reverse_api_type=FakeReverseApi, image_loader=load_native_image),
            group_quote_loader=partial(read_group_quote, reverse_api_type=FakeReverseApi, image_loader=load_native_image,
                audio_loader=load_native_audio, video_loader=load_native_video),
            audio=FakeRecord, audio_loader=load_native_audio, video=FakeVideo, video_loader=load_native_video)
        self.config = group_config()
        self.controller = self.make_controller(self.config)
        self.records, self.history_calls, self.sequence = {}, [], 1000
        self.api = self.native.platform.client._api._wsr_api
        original = self.api.call_action

        async def dispatch(action, **params):
            if action == "get_group_msg_history":
                self.history_calls.append((action, params))
                return {"messages": [deepcopy(self.records[params["message_seq"]])]}
            result = await original(action, **params)
            return {**result, "message_id": 7000 + len(self.api.calls)}

        self.api.call_action = dispatch

    @property
    def app(self):
        return self.controller.session.application

    def media_event(self, parts, **options):
        self.sequence += 1
        event = self.group_event(identifier=options.pop("identifier", str(self.sequence)), **options)
        event.message_obj.message = parts
        return event

    async def receive_parts(self, parts, **options):
        event = self.media_event(parts, **options)
        result = await self.controller.receive_claimed(event)
        self.assertTrue(result.is_success, result.failure)
        batch = result.unwrap()
        await wait_for_batch(self.controller.session, batch, timeout_seconds=self.batch_timeout_seconds)
        row = self.app.messages.batch(batch)
        self.assertEqual(row["state"], "ready", dict(self.app.flow.row(batch)))
        return TaskContext.restore(json.loads(row["context_json"])), batch

    def input(self, context, batch):
        row = self.app.runtime.read_one("SELECT * FROM conversation_inputs WHERE batch_id = ?", (batch,))
        return event_from_document(json.loads(row["event_json"]), context)

    def request(self, context, batch):
        return self.app.messages._request(self.app.messages.batch(batch), context, self.clock.now())

    async def with_delivery(self):
        self.config = group_config(group_delivery_mode="onebot_group")
        self.controller = self.make_controller(self.config)
        await self.start_host()
