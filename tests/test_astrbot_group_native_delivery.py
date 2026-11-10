"""Installed SDK events and real reverse-WS encoding/ACK handling, without a network."""

import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from tests.group_host_support import GroupFixture, group_config
from tests.staging_bootstrap_support import wait_for_batch


class LocalSocket:
    def __init__(self, message_id):
        self.message_id, self.frames, self.status = message_id, [], "ok"

    async def send(self, payload):
        from aiocqhttp.api_impl import ResultStore
        frame = json.loads(payload)
        self.frames.append(frame)
        if self.status != "timeout":
            asyncio.get_running_loop().call_soon(ResultStore.add,
                {"echo": frame["echo"], "status": self.status,
                 "retcode": 0 if self.status == "ok" else 100, "data": {"message_id": self.message_id}})


class NativeGroupDeliveryTests(GroupFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        from aiocqhttp.api_impl import WebSocketReverseApi
        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
        self.types = replace(self.types, event=AiocqhttpMessageEvent, reverse_api=WebSocketReverseApi)
        self.sockets = {}
        for name, platform in self.native.platforms.items():
            clients = {"100": LocalSocket(-456), "101": LocalSocket(789)}
            platform.client._wsr_api_clients = clients
            platform.client._api._wsr_api = WebSocketReverseApi(clients, set(), 1)
            self.sockets[name] = clients
        self.controller = self.make_controller(group_config(group_delivery_mode="onebot_group"))

    def event_for(self, target="group-a", identifier="451"):
        from aiocqhttp import Event
        from astrbot.api.message_components import At, Plain
        from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
        from astrbot.core.platform.message_type import MessageType
        from astrbot.core.pipeline.waking_check.stage import build_unique_session_id
        configured = self.controller.settings.target(target)
        platform = self.native.platforms[configured.platform_id]
        message = AstrBotMessage()
        message.type, message.self_id = MessageType.GROUP_MESSAGE, configured.account_id
        message.group_id = message.session_id = configured.group_id
        message.sender, message.message_id = MessageMember("777", "测试群成员"), identifier
        message.message = [At(qq=int(configured.account_id)), Plain(text="群聊验证")]
        message.message_str = "群聊验证"
        message.raw_message = Event(post_type="message", message_type="group", sub_type="normal", anonymous=None,
            self_id=int(configured.account_id), user_id=777, sender={"user_id": 777},
            group_id=int(configured.group_id), message_id=int(identifier))
        event = self.types.event(message.message_str, message, platform.meta(), configured.group_id, platform.client)
        event.session_id = build_unique_session_id(event)
        event.created_at = self.clock.now().timestamp()
        return event

    async def reply(self, target="group-a", identifier="451"):
        event = self.event_for(target, identifier)
        batch = (await self.controller.receive_claimed(event)).unwrap()
        await wait_for_batch(self.controller.session, batch)
        return self.controller.preview(target, batch).unwrap()

    async def test_native_group_events_and_wire_echo_ack_keep_exact_account_and_group(self):
        from aiocqhttp.api_impl import ResultStore
        await self.start_host()
        for target in ("group-a", "group-b", "group-c"):
            preview = await self.reply(target)
            configured = self.controller.settings.target(target)
            socket = self.sockets[configured.platform_id][configured.account_id]
            frame = socket.frames[-1]
            self.assertEqual(frame["action"], "send_group_msg")
            self.assertEqual(frame["params"], {"self_id": configured.account_id,
                "group_id": int(configured.group_id), "message": [{"type": "text", "data": {"text": "我在这里。"}}]})
            self.assertIsInstance(frame["echo"]["seq"], int)
            self.assertTrue(preview["sent"])
            self.assertEqual(preview["speaker_id"], "777")
            self.assertTrue(preview["parts"][0]["platform_ref"].endswith(":" + str(socket.message_id)))
        self.assertEqual(self.sockets["qq-main"]["101"].frames, [])
        self.assertEqual(self.sockets["qq-second"]["100"].frames, [])
        self.assertEqual(ResultStore._futures, {})

    async def test_group_voice_uses_member_session_provider_and_one_wire_ack(self):
        self.controller = self.make_controller({**group_config(group_delivery_mode="onebot_group"),
            "voice_reply": {"enabled": True}})
        audio_root = self.root / "group-voice-temp"
        audio_root.mkdir()
        audio_file = audio_root / "reply.wav"
        audio_file.write_bytes(b"RIFFgroup-audio")
        selected = []

        def select(umo):
            selected.append(umo)

            async def synthesize(_):
                return str(audio_file)

            return NS(get_audio=synthesize)

        self.native.get_using_tts_provider = select
        with patch("astrbot_host.voice.get_astrbot_temp_path", return_value=str(audio_root)):
            await self.start_host()
            for target, identifier in (("group-a", "851"), ("group-c", "852")):
                report = await self.reply(target, identifier)
                self.assertTrue(report["sent"], report)
                configured = self.controller.settings.target(target)
                frames = [frame for frame in self.sockets[configured.platform_id][configured.account_id].frames
                          if frame["action"] == "send_group_msg"]
                self.assertEqual(len(frames), 1)
                self.assertEqual([segment["type"] for segment in frames[0]["params"]["message"]],
                    ["text", "record"])
        self.assertEqual(selected, ["qq-main:GroupMessage:777_300", "qq-second:GroupMessage:777_300"])

    async def test_native_failed_and_missing_ack_remain_unknown_without_retry(self):
        from aiocqhttp.api_impl import ResultStore
        await self.start_host()
        socket = self.sockets["qq-main"]["100"]
        for index, status in enumerate(("failed", "timeout")):
            socket.status = status
            result = await self.reply(identifier=str(900 + index))
            self.assertEqual(result["parts"][0]["state"], "unknown")
            self.assertFalse(result["sent"])
            self.assertEqual((await self.reply(identifier=str(900 + index)))["batch_id"], result["batch_id"])
        self.assertEqual(len(socket.frames), 2)
        self.assertEqual(ResultStore._futures, {})


if __name__ == "__main__":
    unittest.main()
