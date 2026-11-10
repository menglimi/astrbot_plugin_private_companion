"""开房工具的入口边界：公网访问要先准备好，群聊里不许贴链接。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
import unittest

from companion_v2.together.provider import NativeTogetherError, NativeTogetherProvider


class FakeStore:
    def __init__(self):
        self.events = []

    def update(self, scope, operation_id, **fields):
        self.events.append(fields)
        return {"id": operation_id, **fields}


class FakePlugin:
    def __init__(self, *, access=None, access_fails=False, running=True):
        self.room_server = SimpleNamespace(running=running)
        self._access = access if access is not None else {
            "url": "https://room.example", "tunnel_started": True,
            "tunnel_ready": True, "fixed_public_url": False}
        self._access_fails = access_fails
        self.prepared = 0
        self.via_mobile_gateway = []
        self.tickets = []

    async def _ensure_mobile_room_access(self, *, via_mobile_gateway=False):
        self.prepared += 1
        self.via_mobile_gateway.append(via_mobile_gateway)
        if self._access_fails:
            raise RuntimeError("tunnel unavailable")
        return dict(self._access)

    def _get_provider_by_id(self, provider_id):
        return SimpleNamespace(text_chat=lambda *args, **kwargs: None)

    def issue_room_ticket(self, *, mode, user_id, native_v2):
        self.tickets.append({"mode": mode, "user_id": user_id, "native_v2": native_v2})
        return SimpleNamespace(mode=mode, token="tok-1", created_at=1.0, expires_at=2.0)

    def _ticket_url(self, ticket):
        return f"https://room.example/join/{ticket.token}?mode={ticket.mode}"


def provider_with(plugin):
    provider = object.__new__(NativeTogetherProvider)
    provider.plugin = plugin
    provider.store = FakeStore()
    provider._room_lock = asyncio.Lock()
    provider._closed = False
    return provider


def request(kind="private", mode="call"):
    return {
        "context": {"audience": {"scope": {"kind": kind, "platform": "aiocqhttp", "session_id": "995051631"}}},
        "host_execution": {"model_provider_id": "p1", "tts_provider_id": "", "tts_language": "zh",
                           "tts_timeout_seconds": 30},
        "host_persona": {"bot_id": "b1", "account_id": "a1", "persona_id": "xiaoling", "prompt": "你是小凌。"},
        "arguments": {"mode": mode},
    }


class RoomCreationTests(unittest.TestCase):
    def test_private_room_prepares_public_access_before_signing_the_link(self):
        plugin = FakePlugin()
        provider = provider_with(plugin)
        operation = asyncio.run(provider._create_room(request(), "scope-1", {"id": "op"}, user_id="u1"))
        self.assertEqual(plugin.prepared, 1)
        self.assertEqual(operation["state"], "succeeded")
        room = operation["result"]["room"]
        self.assertIn("/join/tok-1", room["url"])
        self.assertTrue(operation["result"]["access"]["tunnel_started"])
        self.assertTrue(operation["result"]["access"]["tunnel_ready"])
        self.assertEqual(plugin.tickets[0]["user_id"], "u1")

    def test_mobile_gateway_room_uses_the_already_prepared_local_access(self):
        from companion_v2.together.mobile.origin import MOBILE_ROOM_CALL
        plugin = FakePlugin(access={"url": "http://127.0.0.1:6321", "tunnel_started": False,
                                    "tunnel_ready": True, "fixed_public_url": False,
                                    "mobile_gateway": True})
        provider = provider_with(plugin)
        token = MOBILE_ROOM_CALL.set(True)
        try:
            operation = asyncio.run(provider._create_room(request(), "scope-1", {"id": "op"}, user_id="u1"))
        finally:
            MOBILE_ROOM_CALL.reset(token)
        self.assertEqual(plugin.via_mobile_gateway, [True])
        self.assertEqual(operation["state"], "succeeded")
        self.assertFalse(operation["result"]["access"]["tunnel_started"])
        self.assertTrue(operation["result"]["access"]["tunnel_ready"])

    def test_fixed_public_url_is_not_an_auto_tunnel(self):
        plugin = FakePlugin(access={"url": "https://together.example.com", "tunnel_started": False,
                                    "tunnel_ready": True, "fixed_public_url": True})
        provider = provider_with(plugin)
        operation = asyncio.run(provider._create_room(request(), "scope-1", {"id": "op"}, user_id="u1"))
        self.assertFalse(operation["result"]["access"]["tunnel_started"])
        self.assertTrue(operation["result"]["access"]["fixed_public_url"])

    def test_public_access_failure_is_reported_instead_of_handing_out_a_localhost_link(self):
        provider = provider_with(FakePlugin(access_fails=True))
        with self.assertRaises(NativeTogetherError) as caught:
            asyncio.run(provider._create_room(request(), "scope-1", {"id": "op"}, user_id="u1"))
        self.assertEqual(caught.exception.code, "together_public_access_unavailable")

    def test_group_chat_never_gets_a_room_credential(self):
        plugin = FakePlugin()
        provider = provider_with(plugin)
        with self.assertRaises(NativeTogetherError) as caught:
            asyncio.run(provider._create_room(request(kind="group"), "scope-1", {"id": "op"}, user_id="u1"))
        self.assertEqual(caught.exception.code, "together_private_room_required")
        self.assertEqual(plugin.tickets, [])
        self.assertEqual(plugin.prepared, 0)

    def test_room_server_must_be_running(self):
        provider = provider_with(FakePlugin(running=False))
        with self.assertRaises(NativeTogetherError) as caught:
            asyncio.run(provider._create_room(request(), "scope-1", {"id": "op"}, user_id="u1"))
        self.assertEqual(caught.exception.code, "together_room_server_unavailable")


if __name__ == "__main__":
    unittest.main()
