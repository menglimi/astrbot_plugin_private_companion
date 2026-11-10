"""Independent real SDK entry, nested group media, local WS ACK and teardown."""

import asyncio
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


def run(*, quoted=False):
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="v2-group-forward-native-") as temporary:
        os.environ["ASTRBOT_ROOT"] = temporary
        from tests.group_host_support import GroupContext, group_config
        from tests.group_media_support import group_raw, sdk_image
        from tests.forward_support import node_raw
        from tests.audio_support import audio_bytes, FakeRecord
        from tests.video_support import video_bytes, FakeVideo
        from tests.native_adapter_support import FakeProvider, completion, final_text
        from tests.staging_bootstrap_support import wait_for_batch
        from tests.test_management_access import NOW
        from astrbot_host.native import installed_native_types
        from companion_v2.execution.models import TaskContext
        from companion_v2.foundation.clock import DeterministicClock
        from aiocqhttp import Event
        from aiocqhttp.api_impl import WebSocketReverseApi, ResultStore
        from astrbot.api.message_components import Image, Plain, Forward, Record, Video, Reply
        from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
        from astrbot.core.platform.message_type import MessageType
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        logging.disable(logging.CRITICAL)
        assert Path(get_astrbot_data_path()).resolve() == Path(temporary).resolve() / "data"
        package = types.ModuleType("v2_group_forward_fixture")
        package.__path__ = [str(repo)]
        sys.modules[package.__name__] = package
        spec = importlib.util.spec_from_file_location(package.__name__ + ".main", repo / "main.py")
        entry = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = entry
        spec.loader.exec_module(entry)
        native, clock = GroupContext(), DeterministicClock.at(NOW)
        voice, clip, direct = audio_bytes(), video_bytes(), sdk_image()
        nested = node_raw("嵌套音视频", sender="999", audios=(voice,), videos=(clip,))
        outer = node_raw("有限的转发记录", sender="888", nested=[nested])
        record = group_raw(message_id="4601", sender="777", text="", count=0)
        record["message"] = [
            {"type": "image", "data": {"file": direct.file}},
            {"type": "forward", "data": {"id": "sdk-group-forward", "content": [outer]}},
        ]
        direct_parts = [direct, Forward(id="sdk-group-forward")]
        if quoted:
            direct_parts.extend((Record(file=FakeRecord().file), Video(file=FakeVideo(video_bytes(count=1)).file)))
            record["message"].extend((
                {"type": "record", "data": {"file": direct_parts[2].file}},
                {"type": "video", "data": {"file": direct_parts[3].file}},
            ))
        observed = []

        async def model(payload, _):
            content = next(m["content"] for m in payload["messages"] if m["role"] == "user")
            material_text = content[0]["text"] if isinstance(content, list) else content.partition("\n")[0]
            materials = json.loads(material_text)["materials"]
            observed.append(materials)
            if len(observed) <= (2 if quoted else 1):
                assert sum(c["type"] == "input_audio" for c in content) == (2 if quoted else 1)
                assert sum(c["type"] == "image_url" for c in content) == (6 if quoted else 5)
                audio = next(m for m in materials if m["purpose"].startswith("forwarded_audio:"))
                video = next(m for m in materials if m["purpose"].startswith("forwarded_video:"))
                attribution = json.loads(audio["text"])
                assert (attribution["sender_claim"], attribution["forwarded_by"], attribution["node_path"]) == ("999", "777", [0, 1, 0])
                assert attribution["sender"] == "unknown"
                if quoted and len(observed) == 2:
                    assert attribution["quoted_by"] == "778" and attribution["original_message_id"] == "4601"
                    for purpose in ("quoted_audio:", "quoted_video:"):
                        media = next(json.loads(m["text"]) for m in materials if m["purpose"].startswith(purpose))
                        assert (media["sender"], media["quoted_by"]) == ("777", "778")
                assert json.loads(video["text"])["audio_status"] == "not_inspected"
                return completion(final_text("这是节点附带的资料。", evidence=audio["evidence"]))
            assert isinstance(content, str)
            history = [json.loads(m["text"]) for m in materials if m["purpose"] == "group_conversation_history"]
            forwarded = next(m for m in history if ("quotations" if quoted else "forwarded_records") in m)
            assert forwarded["speaker_id"] == ("778" if quoted else "777")
            if quoted:
                assert forwarded["quotations"][0]["sender"] == "777"
                assert forwarded["forwarded_records"][0]["quoted_by"] == "778"
            assert all(a["view"] == "historical_metadata_only" for a in forwarded["attachments"])
            assert any("audio" in a for a in forwarded["attachments"])
            assert any("video" in a for a in forwarded["attachments"])
            assert "base64" not in material_text
            return completion(final_text("继续聊。"))

        native.provider.client.state.behavior = model

        class LocalSocket:
            def __init__(self):
                self.frames = []

            async def send(self, payload):
                frame = json.loads(payload)
                self.frames.append(frame)
                if frame["action"] == "get_group_msg_history":
                    assert frame["params"] == dict(self_id="100", group_id="300", message_seq="4601", count=1,
                                                   reverse_order=False, parse_mult_msg=True)
                    data = {"messages": [record]}
                else:
                    assert frame["action"] == "send_group_msg" and frame["params"]["group_id"] == 300
                    data = {"message_id": 9000 + len(self.frames)}
                asyncio.get_running_loop().call_soon(ResultStore.add,
                    {"echo": frame["echo"], "status": "ok", "retcode": 0, "data": data})

        socket = LocalSocket()
        platform = native.platform
        platform.client._wsr_api_clients = {"100": socket}
        platform.client._api._wsr_api = WebSocketReverseApi(platform.client._wsr_api_clients, set(), 0.2)
        sdk = replace(installed_native_types(), provider=FakeProvider,
                      function_tool=types.SimpleNamespace, tool_set=types.SimpleNamespace)
        controller_type = entry.AstrBotMultiController

        def controller(context, config, root):
            return controller_type(context, config, root, native_types=sdk, now=clock.now)

        def event_for(sender, identifier, parts):
            message = AstrBotMessage()
            message.type, message.self_id = MessageType.GROUP_MESSAGE, "100"
            message.group_id = message.session_id = "300"
            message.sender, message.message_id = MessageMember(sender, "合成群成员"), identifier
            message.message, message.message_str = parts, "群转发验证"
            raw_parts = []
            for part in parts:
                if isinstance(part, Plain):
                    raw_parts.append({"type": "text", "data": {"text": part.text}})
                elif isinstance(part, Image):
                    raw_parts.append({"type": "image", "data": {"file": part.file}})
                elif isinstance(part, Forward):
                    raw_parts.append({"type": "forward", "data": {"id": part.id}})
                elif isinstance(part, Reply):
                    raw_parts.append({"type": "reply", "data": {"id": part.id}})
                elif isinstance(part, (Record, Video)):
                    raw_parts.append({"type": "record" if isinstance(part, Record) else "video", "data": {"file": part.file}})
                else:
                    raise AssertionError("unexpected SDK fixture component")
            message.raw_message = Event(post_type="message", message_type="group", sub_type="normal", anonymous=None,
                self_id=100, user_id=int(sender), sender={"user_id": int(sender)}, group_id=300,
                message_id=int(identifier), message=raw_parts)
            event = sdk.event(message.message_str, message, platform.meta(), "300", platform.client)
            event.created_at = clock.now().timestamp()
            return event

        async def verify():
            with patch.object(entry.StarTools, "get_data_dir", return_value=Path(temporary) / "v2"), \
                    patch.object(entry, "AstrBotMultiController", controller):
                config = group_config(group_delivery_mode="onebot_group")
                if quoted:
                    config["model"]["maximum_input_estimate"] = 500000
                plugin = entry.PrivateCompanionV2StagingPlugin(native, config)
            try:
                await plugin.initialize()
                ctrl = plugin.staging
                (await ctrl.initialize_profile()).unwrap()
                (await ctrl.start()).unwrap()
                app, batches = ctrl.session.application, []
                turns = [("777", "4601", direct_parts)]
                if quoted:
                    turns.append(("778", "4602", [Reply(id="4601"), Plain("讨论引用资料")]))
                turns.append(("779" if quoted else "778", "4603", [Plain("继续聊")]))
                for sender, identifier, parts in turns:
                    event = event_for(sender, identifier, parts)
                    await plugin.receive_companion_message(event)
                    assert event.is_stopped()
                    batch = app.runtime.read_all("SELECT * FROM conversation_batches ORDER BY rowid", ())[-1]
                    batches.append(batch["batch_id"])
                    # The quote scenario revalidates originals through three
                    # confirmed turns; measured local completion exceeds 4 s.
                    await wait_for_batch(ctrl.session, batch["batch_id"], timeout_seconds=15 if quoted else 4)
                    assert app.flow.row(batch["batch_id"])["state"] == "complete"
                    assert app.runtime.read_one("SELECT state FROM conversation_parts WHERE batch_id = ?", (batch["batch_id"],))[0] == "delivered"
                row = app.messages.batch(batches[-1])
                context = TaskContext.restore(json.loads(row["context_json"]))
                request = app.messages._request(row, context, clock.now())
                assert not request.images and not request.audios and not request.videos
                assert [f["action"] for f in socket.frames] == ["get_group_msg_history", "send_group_msg"] * (2 if quoted else 1) + ["send_group_msg"]
                assert len(observed) == (3 if quoted else 2) and ResultStore._futures == {}
                assert app.runtime.read_one("SELECT COUNT(*) FROM memory_atoms", ())[0] == 0
            finally:
                await plugin.terminate()
            assert plugin.staging._terminated and plugin.staging.runtime is None and not native.registered_web_apis
            assert not any(name == "companion_v2" or name.startswith("companion_v2.") for name in sys.modules)
            print("PASS: real SDK plugin entry, group forward" + (" and quoted" if quoted else "") + " audio/video with sampled frames, shared image budget, cross-member metadata history, local WS ACKs, shutdown and route cleanup; synthetic media only.")

        asyncio.run(verify())


if __name__ == "__main__":
    run()
