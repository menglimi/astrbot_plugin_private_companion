"""Lark events use an API client distinct from the platform websocket client."""

import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

from astrbot_host.calendar_command import receive_calendar_command
from astrbot_host.multi_session import MultiPrivateBridge
from astrbot_host.recalls import authenticate_notice_route
from companion_v2.adapters.astrbot.native import HostAccount, HostPersona, persona_instruction
from companion_v2.execution.models import TaskError, digest
from companion_v2.foundation.refs import ScopeKind
from companion_v2.identity_policy.models import AuthenticatedHostPrincipal
from tests.native_adapter_support import FakeEvent, FakePlatform, NativeFixture
from tests.test_management_access import INSTALLATION, NOW, ROLE


def lark_event(platform, *, text="生成日程"):
    event = FakeEvent(NS(platform=platform), event_id="om_message", sender="ou_member", text=text)
    event.bot = platform.lark_api
    event.message_obj.raw_message = NS(chat_type="p2p", message_id="om_message")
    event.message_obj.message_str = text
    return event


class LarkPrivateCaptureTests(NativeFixture, unittest.IsolatedAsyncioTestCase):
    async def test_private_capture_and_core_reply_match_the_platform_api_client(self):
        platform = self.native.platform
        platform.metadata.name = "lark"
        platform.lark_api = object()
        self.native.persona_manager.resolve_selected_persona = AsyncMock(
            return_value=("original", self.native.persona, None, False))
        self.identity.bind(binding_id="native-lark", principal=AuthenticatedHostPrincipal(
            INSTALLATION, "host", "qq-main", "ou_member", "bot-a", "qq-main"),
            subject=self.important.subject, valid_from=NOW, expected_revision=self.identity.revision)
        target = NS(target_id="private-lark", route_key=("qq-main", "qq-main", "ou_member"), bot_id="bot-a")
        settings = NS(targets=(target,), group_targets=(),
                      private_target=lambda platform_id, login, sender: target
                      if (platform_id, login, sender) == target.route_key else None,
                      window_link_for_target=lambda _target_id: None)
        bridge = MultiPrivateBridge(self.native, version="4.27.2", event_type=FakeEvent,
            installation_id=INSTALLATION, host_id="host", identities=self.identity,
            accounts=(HostAccount("qq-main", "qq-main", "bot-a"),),
            personas=(HostPersona("bot-a", "original", ROLE,
                                  digest(persona_instruction(self.native.persona))),),
            settings=settings, authority=NS(ensure_actor=Mock()))
        event = lark_event(platform, text="你好")

        captured = await bridge.capture(event)
        core = await bridge.capture_core_reply(event)
        self.assertTrue(captured.is_success, captured.failure)
        self.assertTrue(core.is_success, core.failure)
        self.assertEqual(captured.unwrap().principal.account_id, "qq-main")

        event.bot = object()
        self.assertEqual((await bridge.capture(event)).failure.code, "host_client_mismatch")
        self.assertEqual((await bridge.capture_core_reply(event)).failure.code, "host_client_mismatch")


class LarkPrivateCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_calendar_command_uses_authenticated_lark_session_transport(self):
        platform = FakePlatform("lark-main")
        platform.metadata.name = "lark"
        platform.lark_api = object()
        platform.send_by_session = AsyncMock()
        event = lark_event(platform)
        target = NS(bot_id="bot-lark")
        session = NS(application=NS(state="running"),
            bridge=NS(accounts={("lark-main", "lark-main"): NS(bot_id="bot-lark")},
                      platforms={"lark-main": (platform, platform.client)}),
            plan_calendar=AsyncMock(return_value={"state": "succeeded", "saved": 2}))
        controller = NS(session=session, _terminated=False, native_types=NS(event=FakeEvent),
            context=NS(get_platform_inst=lambda platform_id: platform if platform_id == "lark-main" else None),
            settings=NS(targets=(target,), private_target=lambda platform_id, login, sender: target
                        if (platform_id, login, sender) == ("lark-main", "lark-main", "ou_member") else None))

        result = await receive_calendar_command(controller, event, session, target=target)
        self.assertTrue(result.is_success, result.failure)
        session.plan_calendar.assert_awaited_once_with(event, target, mode="daily", refresh=False)
        sent_session = platform.send_by_session.await_args.args[0]
        self.assertEqual(sent_session.session_id, "ou_member")

        event.bot = object()
        with self.assertRaises(TaskError) as raised:
            authenticate_notice_route(controller, event, ScopeKind.PRIVATE, expected_post="message")
        self.assertEqual(raised.exception.code, "host_recall_account_changed")
