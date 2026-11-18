"""Real AstrBot dispatch preserves other plugins and avoids default replies."""

from dataclasses import replace
from contextlib import ExitStack
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

# Establish a temporary AstrBot import root before importing the installed SDK.
from tests.test_main_no_reply import ENTRY
from tests.test_management_access import NOW

from astrbot.core.message.components import At, Plain
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.pipeline.process_stage.stage import ProcessStage
from astrbot.core.pipeline.process_stage.method.star_request import StarRequestSubStage
from astrbot.core.pipeline.waking_check import stage as waking
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.event_message_type import EventMessageType, EventMessageTypeFilter
from astrbot.core.star.filter.regex import RegexFilter
from astrbot.core.star.session_plugin_manager import sp
from astrbot.core.star.star import star_map
from astrbot.core.star.star_handler import (
    EventType, StarHandlerMetadata, StarHandlerRegistry, star_handlers_registry,
)
from astrbot_host.event_routing import (
    GROUP_REPLY_GATE_EXTRA, arm_group_reply_gate, release_group_reply_gate,
)


PEER_MODULE = "_companion_event_routing_peer"


class LocalEvent(AstrMessageEvent):
    def __init__(self, text, *, private=False, mention=False):
        message = AstrBotMessage()
        message.type = MessageType.FRIEND_MESSAGE if private else MessageType.GROUP_MESSAGE
        message.self_id, message.message_id = "100", "synthetic-event"
        message.group_id = "" if private else "300"
        message.session_id = "200" if private else "300"
        message.sender = MessageMember(user_id="200", nickname="synthetic-sender")
        message.message = [At(qq="100"), Plain(text)] if mention else [Plain(text)]
        message.raw_message = {"post_type": "message"}
        super().__init__(text, message, PlatformMetadata("aiocqhttp", "local test", "qq-main"),
                         message.session_id)
        self.sent = []

    async def send(self, message):
        self.sent.append(message)
        self._has_send_oper = True


class PluginEventRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        self.staging = NS(
            runtime=None, now=lambda: NOW,
            claims=lambda event: True, target_for_event=lambda event: NS(key="synthetic-route"),
            handles_binding=lambda event: False,
            receive_notice=AsyncMock(return_value=None), observe_group_early=AsyncMock(return_value=None),
            persona_mapping_for_event=AsyncMock(return_value=NS(role_id="synthetic-role")),
            group_reply_candidate=AsyncMock(return_value=NS(failure=None, unwrap=lambda: {"admitted": False})),
            session_topology=NS(reply_enabled=AsyncMock(return_value=True), observe=AsyncMock()),
            receive_claimed=AsyncMock(return_value=NS(failure=None, unwrap=lambda: "synthetic-batch")),
            prepare_core_reply=AsyncMock(return_value=NS(failure=None, unwrap=lambda: {
                "event_id": "synthetic-event", "turn_id": "synthetic-core-turn"})),
        )
        self.plugin.staging = self.staging
        self.registry = StarHandlerRegistry()
        for metadata in star_handlers_registry.get_handlers_by_module_name(ENTRY.__name__):
            if metadata.event_type is EventType.AdapterMessageEvent:
                self.registry.append(replace(metadata, handler=getattr(self.plugin, metadata.handler_name),
                                             extras_configs=dict(metadata.extras_configs)))
        self.assertTrue(any(item.handler_name == "receive_companion_message" for item in self.registry))
        self.model_requests = []
        self.context = NS(astrbot_config={
            "platform_settings": {}, "admins_id": [], "wake_prefix": ["/"],
            "provider_settings": {"enable": True},
        }, db_helper=None, astrbot_config_id="synthetic-test")
        self.waking_stage = waking.WakingCheckStage()
        self.process_stage = ProcessStage()
        self.process_stage.ctx = self.context
        self.process_stage.star_request_sub_stage = StarRequestSubStage()
        self.process_stage.agent_sub_stage = NS(process=self.model_request)
        self.peers = []
        patches = ExitStack()
        self.addCleanup(patches.close)
        patches.enter_context(patch.object(waking, "star_handlers_registry", self.registry))
        patches.enter_context(patch.dict(star_map, {
            ENTRY.__name__: NS(name=ENTRY.PLUGIN_NAME, activated=True, reserved=False),
            PEER_MODULE: NS(name="synthetic-peer", activated=True, reserved=False),
        }))
        patches.enter_context(patch.object(sp, "get_async", new=AsyncMock(return_value={})))

    async def model_request(self, event):
        self.model_requests.append(event)
        yield

    def add_peer(self, handler, *, priority=0, event_filter=None):
        metadata = StarHandlerMetadata(
            event_type=EventType.AdapterMessageEvent,
            handler_full_name=PEER_MODULE + "." + handler.__name__, handler_name=handler.__name__,
            handler_module_path=PEER_MODULE, handler=handler,
            event_filters=[event_filter or EventMessageTypeFilter(EventMessageType.ALL)],
            extras_configs={"priority": priority},
        )
        if isinstance(event_filter, CommandFilter):
            event_filter.init_handler_md(metadata)
        self.registry.append(metadata)

    async def dispatch(self, event):
        await self.waking_stage.initialize(self.context)
        await self.waking_stage.process(event)
        self.assertFalse(event.is_stopped(), "waking must admit the synthetic event")
        async for _ in self.process_stage.process(event):
            result = event.get_result()
            if result is not None and result.chain:
                await event.send(result)

    async def test_prefix_free_sign_in_listener_consumes_event_before_companion(self):
        async def sign_in(event):
            if event.get_message_str() in {"签到", "星缘签到"}:
                self.peers.append(event.get_message_str())
                event.stop_event()
                yield event.plain_result("签到成功").stop_event()

        # The installed points plugin uses this ordinary listener priority.
        self.add_peer(sign_in, priority=100000,
                      event_filter=EventMessageTypeFilter(EventMessageType.GROUP_MESSAGE))
        for text in ("签到", "星缘签到"):
            with self.subTest(text=text):
                event = LocalEvent(text)
                await self.dispatch(event)
                self.assertTrue(event.is_stopped())
                self.assertEqual(len(event.sent), 1)
                self.assertEqual(self.peers[-1], text)
        self.staging.persona_mapping_for_event.assert_not_awaited()
        self.staging.receive_claimed.assert_not_awaited()
        self.assertEqual(self.model_requests, [])

    async def test_regex_reply_without_stopping_does_not_get_a_companion_reply(self):
        async def regex_reply(event):
            self.peers.append(event.get_message_str())
            yield event.plain_result("正则插件已处理")

        self.add_peer(regex_reply, event_filter=RegexFilter(r"^星缘签到$"))
        event = LocalEvent("星缘签到", mention=True)
        await self.dispatch(event)
        self.assertFalse(event.is_stopped())
        self.assertEqual(len(event.sent), 1)
        self.assertEqual(self.peers, ["星缘签到"])
        self.staging.persona_mapping_for_event.assert_not_awaited()
        self.staging.receive_claimed.assert_not_awaited()
        self.assertEqual(self.model_requests, [])

    async def test_silent_companion_preserves_listeners_before_and_after_intake(self):
        async def activity_counter(event):
            self.peers.append("before")

        async def later_observer(event):
            self.peers.append("after")

        async def model_probe(event):
            # A lower-priority handler must see the temporary gate while the
            # companion is still deciding whether this group turn is admitted.
            self.peers.append("probe")
            self.assertTrue(event.call_llm)

        self.add_peer(activity_counter)
        self.add_peer(model_probe, priority=-100000)
        self.add_peer(later_observer, priority=-240000)
        event = LocalEvent("普通群消息", mention=True)
        await self.dispatch(event)
        self.assertEqual(self.peers, ["before", "probe", "after"])
        self.assertFalse(event.is_stopped())
        self.assertTrue(event.call_llm)
        self.assertEqual(event.sent, [])
        self.assertEqual(self.model_requests, [])
        self.staging.receive_claimed.assert_not_awaited()

    async def test_group_reply_gate_restores_default_flag_after_admission(self):
        event = LocalEvent("普通群消息", mention=True)
        self.assertFalse(event.call_llm)
        state = arm_group_reply_gate(event)
        self.assertEqual(state["status"], "pending")
        self.assertTrue(event.call_llm)
        self.assertEqual(event.get_extra(GROUP_REPLY_GATE_EXTRA), state)
        self.assertTrue(release_group_reply_gate(event))
        self.assertEqual(state["status"], "released")
        self.assertFalse(event.call_llm)

    async def test_claimed_private_reply_preserves_later_detection_and_uses_core_model(self):
        async def private_observer(event):
            self.peers.append(event.get_message_str())

        self.add_peer(private_observer, priority=-240000)
        event = LocalEvent("在吗", private=True)
        await self.dispatch(event)
        self.assertEqual(self.peers, ["在吗"])
        self.assertFalse(event.is_stopped())
        self.assertTrue(event.is_wake)
        self.assertTrue(event.is_at_or_wake_command)
        self.assertFalse(event.call_llm)
        self.staging.prepare_core_reply.assert_awaited_once()
        self.staging.receive_claimed.assert_not_awaited()
        self.assertEqual(self.model_requests, [event])

    async def test_native_command_keeps_its_normal_dispatch(self):
        async def native_command(event):
            self.peers.append(event.get_message_str())
            yield event.plain_result("框架命令已处理")

        self.add_peer(native_command, event_filter=CommandFilter("签到"))
        event = LocalEvent("/签到")
        await self.dispatch(event)
        self.assertEqual(len(event.sent), 1)
        self.assertEqual(self.peers, ["签到"])
        self.assertFalse(event.call_llm)
        self.staging.persona_mapping_for_event.assert_not_awaited()
        self.staging.receive_claimed.assert_not_awaited()
        self.assertEqual(self.model_requests, [])

    async def test_unclaimed_private_message_retains_astrbot_default_model(self):
        self.staging.claims = lambda event: False
        self.staging.target_for_event = lambda event: None
        event = LocalEvent("在吗", private=True)
        with patch.object(ENTRY, "refuse_stranger", new=AsyncMock(return_value=None)):
            await self.dispatch(event)
        self.assertFalse(event.is_stopped())
        self.assertFalse(event.call_llm)
        self.assertEqual(self.model_requests, [event])

    async def test_stranger_notice_preserves_later_detection_without_default_model(self):
        self.staging.claims = lambda event: False
        self.staging.target_for_event = lambda event: None

        async def private_observer(event):
            self.peers.append(event.get_message_str())

        self.add_peer(private_observer, priority=-240000)
        event = LocalEvent("在吗", private=True)
        with patch.object(ENTRY, "refuse_stranger", new=AsyncMock(return_value={"state": "replied"})):
            await self.dispatch(event)
        self.assertEqual(self.peers, ["在吗"])
        self.assertFalse(event.is_stopped())
        self.assertTrue(event.call_llm)
        self.assertEqual(self.model_requests, [])


if __name__ == "__main__":
    unittest.main()
