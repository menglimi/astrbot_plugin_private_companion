"""Isolated real SDK notice conversion and plugin lifecycle; no network clients."""

import asyncio
from datetime import timedelta
import importlib.util
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import types
from unittest.mock import patch


repo = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="v2-native-recall-entry-") as temporary:
    os.environ["ASTRBOT_ROOT"] = temporary

    from aiocqhttp import Event
    from astrbot.core import sp as astrbot_preferences
    try:
        from astrbot.core.pipeline.waking_check.stage import build_unique_session_id
    except ImportError:
        from astrbot.core.event_bus import UNIQUE_SESSION_ID_BUILDERS

        def build_unique_session_id(event):
            builder = UNIQUE_SESSION_ID_BUILDERS.get(event.get_platform_name())
            return builder(event) if builder else event.session_id
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import AiocqhttpAdapter
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
    from companion_v2.adapters.astrbot.staging import NativeAdapterTypes
    from companion_v2.foundation.clock import DeterministicClock
    from companion_v2.memory.codec import source as decode_source
    from tests.group_host_support import GroupContext, group_config
    from tests.native_adapter_support import FakeProvider, FakeReverseApi
    from tests.test_management_access import NOW

    logging.disable(logging.WARNING)
    assert Path(get_astrbot_data_path()).resolve() == Path(temporary).resolve() / "data"
    package = types.ModuleType("v2_recall_entry_fixture")
    package.__path__ = [str(repo)]
    sys.modules[package.__name__] = package
    spec = importlib.util.spec_from_file_location(package.__name__ + ".main", repo / "main.py")
    plugin_module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = plugin_module
    spec.loader.exec_module(plugin_module)

    native, clock = GroupContext(), DeterministicClock.at(NOW)
    async def window_persona(_umo):
        return "original"

    sdk = NativeAdapterTypes("4.27.2", AiocqhttpMessageEvent, FakeProvider,
                             types.SimpleNamespace, types.SimpleNamespace, FakeReverseApi,
                             session_persona_override_lookup=window_persona)
    controller_type = plugin_module.AstrBotMultiController

    def make_controller(context, config, root):
        return controller_type(context, config, root, native_types=sdk, now=clock.now)

    # Conversion methods do not need a running CQHttp instance. The resulting
    # native event is tied to the same pinned fake platform as the host fixture.
    adapter = object.__new__(AiocqhttpAdapter)
    adapter.bot, adapter.metadata = native.platform.client, native.platform.meta()

    async def event_for(identifier, *, group=False, recall=False, poke=False):
        sender = 777 if group else 200
        raw = Event(self_id=100, user_id=sender, time=int(clock.now().timestamp()))
        if group:
            raw["group_id"] = 300
        if recall or poke:
            raw.update(post_type="notice", notice_type="group_recall" if group else "friend_recall",
                       message_id=int(identifier))
            if group:
                raw["operator_id"] = 999
            if poke:
                raw.update(notice_type="notify", sub_type="poke", target_id=100)
        else:
            raw.update(post_type="message", message_type="group" if group else "private",
                       sub_type="normal" if group else "friend", message_id=int(identifier),
                       sender={"user_id": sender, "nickname": "合成参与者"},
                       message=[{"type": "text", "data": {"text": "用于撤回验证的合成原文"}}])
        message = await adapter.convert_message(raw)
        event = adapter.create_event(message)
        if group:
            event.session_id = build_unique_session_id(event)
        event.created_at = clock.now().timestamp()
        if recall:
            assert isinstance(message.raw_message, Event)
            assert message.message == []
            assert message.message_id != str(raw["message_id"])
            assert message.sender.user_id == str(sender)
            if group:
                assert message.session_id == "300" and event.session_id == "777_300"
        return event

    async def verify():
        with patch.object(plugin_module.StarTools, "get_data_dir", return_value=Path(temporary) / "v2"), \
                patch.object(plugin_module, "AstrBotMultiController", make_controller):
            config = group_config(group_delivery_mode="onebot_group")
            config["poke"] = {"enabled": True, "replies": ["在呢。"], "back_probability": 1.0}
            plugin = plugin_module.PrivateCompanionV2StagingPlugin(native, config)
        try:
            await plugin.initialize()
            controller = plugin.staging
            assert controller._accepting
            app = controller.session.application
            for identifier, group in (("5011", False), ("5012", True)):
                original = await event_for(identifier, group=group)
                assert controller.claims(original), controller.status()
                await plugin.observe_companion_recall(original)
                target = controller.target_for_event(original)
                prepared = await controller.prepare_core_reply(original, target)
                assert prepared.is_success, prepared.failure
                await asyncio.wait_for(prepared.unwrap()["native_source_future"], timeout=5)
                native_input = app.runtime.read_one(
                    "SELECT source_json FROM conversation_native_observations WHERE event_id = ? AND kind = 'input'",
                    (str(original.message_obj.message_id),))
                assert native_input is not None
                notice = await event_for(identifier, group=group, recall=True)
                assert not controller.claims(notice)
                await plugin.observe_companion_recall(notice)
                await plugin.receive_companion_message(notice)
                assert not notice.is_stopped(), "observation keeps notices available to other plugins"
                reference = decode_source(json.loads(native_input["source_json"]))
                assert app.messages.sources.validate_reference(reference, observed_at=clock.now()).failure.code == "source_revoked"
                await plugin.observe_companion_recall(await event_for(identifier, group=group, recall=True))
            calls = len(native.provider.client.state.calls)
            notice = await event_for("5999", group=True, recall=True)
            await plugin.observe_companion_recall(notice)
            before = app.runtime.read_one(
                "SELECT COUNT(*) FROM conversation_native_observations WHERE kind = 'input'", ())[0]
            late = await event_for("5999", group=True)
            target = controller.target_for_event(late)
            prepared = await controller.prepare_core_reply(late, target)
            assert prepared.is_success, prepared.failure
            await asyncio.wait_for(prepared.unwrap()["native_source_future"], timeout=5)
            assert app.runtime.read_one(
                "SELECT COUNT(*) FROM conversation_native_observations WHERE kind = 'input'", ())[0] == before
            poke = await event_for("6000", group=True, poke=True)
            await plugin.observe_companion_recall(poke)
            await plugin.receive_companion_message(poke)
            assert not poke.is_stopped()
            assert [action for action, _ in native.platform.client._api._wsr_api.calls] == [
                "send_group_msg", "group_poke"]
            clock.advance(timedelta(seconds=1))
            poke = await event_for("6001", group=True, poke=True)
            await plugin.observe_companion_recall(poke)
            await plugin.receive_companion_message(poke)
            assert not poke.is_stopped()
            assert [action for action, _ in native.platform.client._api._wsr_api.calls] == [
                "send_group_msg", "group_poke", "send_group_msg", "group_poke"]
            assert app.runtime.read_one("SELECT COUNT(*) FROM inbox WHERE consumer = ?",
                ("conversation.platform_recall.v1",))[0] == 3
            assert len(native.provider.client.state.calls) == calls
            assert not native.platforms["qq-second"].client._api._wsr_api.calls
        finally:
            await plugin.terminate()
            await astrbot_preferences.db_helper.engine.dispose()
        assert plugin.staging._terminated and plugin.staging.runtime is None
        assert not native.registered_web_apis
        assert not any(name == "companion_v2" or name.startswith("companion_v2.") for name in sys.modules)
        print("PASS: real SDK recall and poke entry, private/group conversion, admin and unique-session routing, "
              "duplicate and early notices, other-plugin visibility, shutdown and route cleanup; "
              "temporary data only, no QQ or provider traffic.")

    asyncio.run(verify())
