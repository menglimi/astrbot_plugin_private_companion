"""Real SDK core group replies with local model/OneBot doubles and teardown."""

import asyncio
from copy import deepcopy
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


def run(*, deliver_images=False):
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="v2-group-media-core-") as temporary:
        os.environ["ASTRBOT_ROOT"] = temporary
        from tests.group_host_support import GroupContext, group_config
        from tests.group_media_support import group_raw, sdk_image
        from tests.native_adapter_support import FakeProvider
        from tests.test_management_access import NOW
        from astrbot_host.native import installed_native_types
        from companion_v2.foundation.clock import DeterministicClock
        from aiocqhttp import CQHttp, Event
        from aiocqhttp.api_impl import WebSocketReverseApi, ResultStore
        from astrbot.api.message_components import At, Image, Plain, Reply
        from astrbot.core import db_helper
        from astrbot.core.config import DEFAULT_CONFIG
        from astrbot.core.message.message_event_result import MessageEventResult, ResultContentType
        from astrbot.core.pipeline.process_stage.stage import ProcessStage
        from astrbot.core.pipeline.respond.stage import RespondStage
        from astrbot.core.pipeline.result_decorate import stage as decorate_module
        from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
        from astrbot.core.platform.message_type import MessageType
        from astrbot.core.provider.entities import ProviderRequest
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path
        from astrbot.core.utils.quoted_message_parser import extract_quoted_message_images

        logging.disable(logging.CRITICAL)
        assert Path(get_astrbot_data_path()).resolve() == Path(temporary).resolve() / "data"
        package = types.ModuleType("v2_group_media_fixture")
        package.__path__ = [str(repo)]
        sys.modules[package.__name__] = package
        spec = importlib.util.spec_from_file_location(package.__name__ + ".main", repo / "main.py")
        entry = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = entry
        spec.loader.exec_module(entry)
        native, clock = GroupContext(), DeterministicClock.at(NOW)

        class LocalSocket:
            def __init__(self):
                self.frames, self.records = [], {}
                self.send_count = 0

            async def send(self, payload):
                frame = json.loads(payload)
                self.frames.append(frame)
                if frame["action"] == "get_msg":
                    identifier = "8001" if deliver_images else "4501"
                    assert frame["params"] == {"message_id": identifier}
                    data = deepcopy(self.records[identifier])
                    assert str(data["sender"]["user_id"]) == ("100" if deliver_images else "777")
                else:
                    assert frame["action"] == "send_group_msg" and frame["params"]["group_id"] == 300
                    self.send_count += 1
                    data = {"message_id": 8000 + self.send_count}
                    record = group_raw(message_id=str(data["message_id"]), sender="100", count=0)
                    record["message"] = deepcopy(frame["params"]["message"])
                    self.records[str(data["message_id"])] = record
                asyncio.get_running_loop().call_soon(ResultStore.add,
                    {"echo": frame["echo"], "status": "ok", "retcode": 0, "data": data})

        socket, platform = LocalSocket(), native.platform
        platform.client = CQHttp()
        platform.client._wsr_api_clients = {"100": socket}
        platform.client._api._wsr_api = WebSocketReverseApi(platform.client._wsr_api_clients, set(), 0.2)
        sdk = replace(installed_native_types(), provider=FakeProvider)
        controller_type = entry.AstrBotMultiController

        def controller(context, config, root):
            return controller_type(context, config, root, native_types=sdk, now=clock.now)

        def event_for(sender, identifier, parts):
            message = AstrBotMessage()
            message.type, message.self_id = MessageType.GROUP_MESSAGE, "100"
            message.group_id = message.session_id = "300"
            message.sender, message.message_id = MessageMember(sender, "合成群成员"), identifier
            # Simulate the adapter's recognized @Bot, retaining the original
            # image/reply components for AstrBot rather than a V2 JSON envelope.
            message.message = [At(qq="100"), *parts]
            message.message_str = "".join(part.text for part in parts if isinstance(part, Plain))
            raw_parts = []
            for part in message.message:
                if isinstance(part, Plain):
                    raw_parts.append({"type": "text", "data": {"text": part.text}})
                elif isinstance(part, Image):
                    raw_parts.append({"type": "image", "data": {"file": part.file, "url": part.url}})
                elif isinstance(part, Reply):
                    raw_parts.append({"type": "reply", "data": {"id": part.id}})
                elif isinstance(part, At):
                    raw_parts.append({"type": "at", "data": {"qq": part.qq}})
                else:
                    raise AssertionError("unexpected SDK fixture component")
            message.raw_message = Event(post_type="message", message_type="group", sub_type="normal", anonymous=None,
                self_id=100, user_id=int(sender), sender={"user_id": int(sender)}, group_id=300,
                message_id=int(identifier), message=raw_parts)
            event = sdk.event(message.message_str, message, platform.meta(), "300", platform.client)
            event.created_at = clock.now().timestamp()
            event.is_at_or_wake_command = True
            event.set_extra("activated_handlers", [])
            return event

        class CoreAgentFixture:
            """Local model boundary; core SDK owns attachments, results and send."""
            def __init__(self, plugin):
                self.plugin, self.requests, self.history = plugin, [], []

            async def process(self, event):
                original = tuple(event.message_obj.message)
                request = ProviderRequest(prompt=event.message_str, system_prompt="AstrBot 当前人格",
                                          contexts=deepcopy(self.history))
                for component in original:
                    if isinstance(component, Image):
                        request.image_urls.append(await component.convert_to_file_path())
                    elif isinstance(component, Reply):
                        request.image_urls.extend(await extract_quoted_message_images(event, component))
                images = tuple(request.image_urls)
                await self.plugin.prepare_companion_core_request(event, request)
                assert not event.is_stopped()
                assert tuple(event.message_obj.message) == original
                assert tuple(request.image_urls) == images
                assert "AstrBot 当前人格" in request.system_prompt
                self.requests.append(request)
                text = "把原图附在这里。" if deliver_images and images else "图里有红色。" if images else "接着聊刚才的群话题。"
                result = MessageEventResult().message(text).set_result_content_type(ResultContentType.LLM_RESULT)
                if deliver_images and images:
                    image = images[0]
                    result.chain.append(Image(file=image) if image.startswith("base64://") else Image.fromFileSystem(image))
                event.set_result(result)
                self.history.extend([{"role": "user", "content": event.message_str},
                                     {"role": "assistant", "content": text}])
                yield

        async def verify():
            with patch.object(entry.StarTools, "get_data_dir", return_value=Path(temporary) / "v2"), \
                    patch.object(entry, "AstrBotMultiController", controller):
                plugin = entry.PrivateCompanionV2StagingPlugin(native, group_config(group_delivery_mode="onebot_group"))
            try:
                # initialize() now creates and starts the saved host itself.
                await plugin.initialize()
                ctrl = plugin.staging
                assert ctrl.session is not None and ctrl.session.application.state == "running"
                app = ctrl.session.application
                config = deepcopy(DEFAULT_CONFIG)
                config["t2i"] = False
                config["provider_tts_settings"]["enable"] = False
                config["platform_settings"].update(reply_with_mention=False, reply_with_quote=False, reply_prefix="")
                config["platform_settings"]["segmented_reply"]["enable"] = False
                pipeline_context = types.SimpleNamespace(astrbot_config=config,
                    plugin_manager=types.SimpleNamespace(context=types.SimpleNamespace(get_using_tts_provider=lambda _: None)))
                process, agent = ProcessStage(), CoreAgentFixture(plugin)
                process.ctx, process.agent_sub_stage = pipeline_context, agent
                decorate, respond = decorate_module.ResultDecorateStage(), RespondStage()
                await decorate.initialize(pipeline_context)
                await respond.initialize(pipeline_context)
                handlers = [types.SimpleNamespace(handler_module_path="core_group_test", handler_name="v2_guard",
                                                   handler=plugin.guard_companion_core_delivery)]

                async def after_send(current, *_):
                    await plugin.observe_companion_core_reply_sent(current)
                    return False

                original_image = sdk_image()
                original_record = group_raw(message_id="4501", sender="777", count=0, text="原图")
                original_record["message"].append({"type": "image", "data": {"file": original_image.file}})
                socket.records["4501"] = original_record
                turns = [("777", "4501", [Plain("原图"), original_image]),
                         ("778", "4502", [Reply(id="8001" if deliver_images else "4501",
                            sender_id="100" if deliver_images else "777"), Plain("接着聊")])]
                if deliver_images:
                    turns.append(("779", "4503", [Plain("继续群话题")]))
                with patch.object(ctrl, "receive_claimed", side_effect=AssertionError("ordinary reply must use AstrBot core")), \
                        patch.object(decorate_module.star_handlers_registry, "get_handlers_by_event_type", return_value=handlers), \
                        patch.dict(decorate_module.star_map, {"core_group_test": types.SimpleNamespace(name="core_group_test")}), \
                        patch("astrbot.core.pipeline.respond.stage.call_event_hook", side_effect=after_send):
                    for sender, identifier, parts in turns:
                        event = event_for(sender, identifier, parts)
                        await plugin.receive_companion_message(event)
                        assert not event.is_stopped() and event.is_at_or_wake_command
                        state = event.get_extra("_companion_v2_core_reply_context")
                        assert state["prepared"]["channel"] == "group"
                        async for _ in process.process(event):
                            async for _ in decorate.process(event):
                                pass
                            await respond.process(event)
                        assert state["send_attempts"] == state["send_returns"] == 1 and state["send_unknown"] == 0, (
                            identifier, state["send_attempts"], state["send_returns"], state["send_unknown"],
                            [frame["action"] for frame in socket.frames])
                        assert event.send.__func__ is sdk.event.send

                # Drain only the asynchronous observations, without inventing a
                # V2 confirmed-delivery batch from the SDK's local send return.
                observer = ctrl.session.core_reply_observations
                if observer._pending:
                    await asyncio.gather(*tuple(observer._pending))
                assert [len(request.image_urls) for request in agent.requests] == [1, 1] + ([0] if deliver_images else [])
                sends = [frame["params"]["message"] for frame in socket.frames if frame["action"] == "send_group_msg"]
                expected = [["text", "image"], ["text", "image"], ["text"]] if deliver_images else [["text"], ["text"]]
                assert [[part["type"] for part in message] for message in sends] == expected
                if deliver_images:
                    assert sends[0] == sends[1]
                assert [frame["action"] for frame in socket.frames] == ["send_group_msg", "get_msg", "send_group_msg"] + (["send_group_msg"] if deliver_images else [])
                outputs = app.runtime.read_all("SELECT delivery_state FROM conversation_native_observations WHERE kind = 'assistant'", ())
                assert len(outputs) == len(turns) and all(row["delivery_state"] == "send_returned" for row in outputs)
                assert app.runtime.read_one("SELECT COUNT(*) FROM conversation_batches", ())[0] == 0
                assert app.runtime.read_one("SELECT COUNT(*) FROM model_rounds", ())[0] == 0
                assert app.runtime.read_one("SELECT COUNT(*) FROM memory_atoms", ())[0] == 0
                assert native.provider.client.state.calls == []
                assert ResultStore._futures == {}
            finally:
                try:
                    await plugin.terminate()
                finally:
                    await db_helper.engine.dispose()
            assert plugin.staging._terminated and plugin.staging.runtime is None and not native.registered_web_apis
            assert not any(name == "companion_v2" or name.startswith("companion_v2.") for name in sys.modules)
            print("PASS: AstrBot core group image delivery, cross-member Bot image quote, native attachments, local WS ACKs, termination and route cleanup; no real model or QQ traffic.")

        asyncio.run(verify())


if __name__ == "__main__":
    run()
