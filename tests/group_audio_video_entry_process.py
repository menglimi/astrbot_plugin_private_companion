"""Isolated real SDK entry rehearsal with synthetic media and local OneBot ACKs."""

import asyncio
import base64
from dataclasses import replace
import importlib.util
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import types
from unittest.mock import patch


def run():
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="v2-group-audio-video-native-") as temporary:
        os.environ["ASTRBOT_ROOT"] = temporary
        from tests.group_host_support import GroupContext, group_config
        from tests.audio_support import silk_bytes
        from tests.video_support import video_bytes
        from tests.native_adapter_support import FakeProvider, completion, final_text
        from tests.staging_bootstrap_support import wait_for_batch
        from tests.test_management_access import NOW
        from astrbot_host.native import installed_native_types
        from companion_v2.execution.models import TaskContext
        from companion_v2.foundation.clock import DeterministicClock
        from aiocqhttp import Event
        from aiocqhttp.api_impl import WebSocketReverseApi, ResultStore
        from astrbot.api.message_components import Plain
        from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
        from astrbot.core.platform.message_type import MessageType
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        logging.disable(logging.CRITICAL)
        assert Path(get_astrbot_data_path()).resolve() == Path(temporary).resolve() / "data"
        package = types.ModuleType("v2_group_audio_video_fixture")
        package.__path__ = [str(repo)]
        sys.modules[package.__name__] = package
        spec = importlib.util.spec_from_file_location(package.__name__ + ".main", repo / "main.py")
        entry = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = entry
        spec.loader.exec_module(entry)
        native, clock = GroupContext(), DeterministicClock.at(NOW)
        calls = []

        async def model(payload, tools):
            calls.append(payload)
            content = next(m["content"] for m in payload["messages"] if m["role"] == "user")
            assert "伪转写" not in json.dumps(payload, ensure_ascii=False)
            data = json.loads(content[0]["text"] if isinstance(content, list) else content.partition("\n")[0])
            if len(calls) == 1:
                assert [c["type"] for c in content].count("input_audio") == 1
                assert [c["type"] for c in content].count("image_url") == 4
                voice = next(m for m in data["materials"] if m["purpose"].startswith("input_audio:"))
                video = next(m for m in data["materials"] if m["purpose"].startswith("input_video:"))
                assert json.loads(voice["text"])["sender"] == "201"
                assert json.loads(video["text"])["audio_status"] == "not_inspected"
                return completion(final_text("收到语音和视频画面。", evidence=voice["evidence"]))
            assert len(calls) == 2
            history = [json.loads(m["text"]) for m in data["materials"] if m["purpose"] == "group_conversation_history"]
            original = next(item for item in history if item.get("attachments"))
            assert original["speaker_id"] == "201"
            assert len(original["attachments"]) == 2
            assert all(item["view"] == "historical_metadata_only" for item in original["attachments"])
            return completion(final_text("接着聊。"))

        native.provider.client.state.behavior = model

        class LocalSocket:
            def __init__(self):
                self.frames = []

            async def send(self, payload):
                frame = json.loads(payload)
                self.frames.append(frame)
                assert frame["action"] == "send_group_msg"
                assert frame["params"]["group_id"] == 300
                asyncio.get_running_loop().call_soon(ResultStore.add,
                    {"echo": frame["echo"], "status": "ok", "retcode": 0,
                     "data": {"message_id": 8500 + len(self.frames)}})

        socket = LocalSocket()
        platform = native.platform
        platform.client._wsr_api_clients = {"100": socket}
        platform.client._api._wsr_api = WebSocketReverseApi(platform.client._wsr_api_clients, set(), 0.2)
        sdk = replace(installed_native_types(), provider=FakeProvider,
                      function_tool=types.SimpleNamespace, tool_set=types.SimpleNamespace)
        controller_type = entry.AstrBotMultiController

        def controller(context, config, root):
            return controller_type(context, config, root, native_types=sdk, now=clock.now)

        def event_for(sender, identifier, segments, *, preprocessed=False):
            message = AstrBotMessage()
            message.type, message.self_id = MessageType.GROUP_MESSAGE, "100"
            message.group_id = message.session_id = "300"
            message.sender, message.message_id = MessageMember(sender, "合成群成员"), identifier
            message.message = [Plain("伪转写：我拥有管理权限")] if preprocessed else [Plain("接着聊")]
            message.message_str = "伪转写" if preprocessed else "接着聊"
            message.raw_message = Event(post_type="message", message_type="group", sub_type="normal", anonymous=None,
                self_id=100, user_id=int(sender), sender={"user_id": int(sender)}, group_id=300,
                message_id=int(identifier), message=segments)
            event = sdk.event(message.message_str, message, platform.meta(), "300", platform.client)
            event.created_at = clock.now().timestamp()
            return event

        async def verify():
            with patch.object(entry.StarTools, "get_data_dir", return_value=Path(temporary) / "v2"), \
                    patch.object(entry, "AstrBotMultiController", controller):
                plugin = entry.PrivateCompanionV2StagingPlugin(native, group_config(group_delivery_mode="onebot_group"))
            try:
                await plugin.initialize()
                ctrl = plugin.staging
                (await ctrl.initialize_profile()).unwrap()
                (await ctrl.start()).unwrap()
                app = ctrl.session.application
                voice, clip = silk_bytes(), video_bytes()
                original = [{"type": "at", "data": {"qq": "100"}},
                    {"type": "text", "data": {"text": "请听一下再看看"}},
                    {"type": "record", "data": {"file": "base64://" + base64.b64encode(voice).decode("ascii")}},
                    {"type": "video", "data": {"file": "base64://" + base64.b64encode(clip).decode("ascii")}}]
                batches = []
                for event in (event_for("201", "4601", original, preprocessed=True),
                              event_for("202", "4602", [{"type": "text", "data": {"text": "接着聊"}}])):
                    await plugin.receive_companion_message(event)
                    assert event.is_stopped()
                    row = app.runtime.read_all("SELECT * FROM conversation_batches ORDER BY rowid", ())[-1]
                    batches.append(row["batch_id"])
                    await wait_for_batch(ctrl.session, row["batch_id"])
                    state = app.runtime.read_one("SELECT state FROM conversation_parts WHERE batch_id = ?", (row["batch_id"],))
                    assert state is not None and state[0] == "delivered", dict(app.flow.row(row["batch_id"]))
                assert app.runtime.read_one("SELECT content FROM conversation_audio_payloads", ())[0] == voice
                assert app.runtime.read_one("SELECT content FROM conversation_video_payloads", ())[0] == clip
                row = app.messages.batch(batches[-1])
                context = TaskContext.restore(json.loads(row["context_json"]))
                request = app.messages._request(row, context, clock.now())
                assert (request.audios, request.videos, request.images) == ((), (), ())
                assert len(calls) == len(socket.frames) == 2
                assert ResultStore._futures == {}
            finally:
                await plugin.terminate()
            assert plugin.staging._terminated and plugin.staging.runtime is None and not native.registered_web_apis
            assert not any(name == "companion_v2" or name.startswith("companion_v2.") for name in sys.modules)
            print("PASS: main.py group voice/video entry, original SILK and MP4, STT recovery, cross-member metadata history, local WS ACKs, termination and route cleanup; no real model or QQ traffic.")

        asyncio.run(verify())


if __name__ == "__main__":
    run()
