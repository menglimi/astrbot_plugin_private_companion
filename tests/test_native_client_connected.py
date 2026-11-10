"""Native account liveness must not depend on the adapter's key type."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from companion_v2.adapters.astrbot.native import native_client_connected


def platform(name="aiocqhttp", client=None):
    return SimpleNamespace(meta=lambda: SimpleNamespace(name=name, id="napcat"),
                           get_client=lambda: client)


class NativeClientConnectedTests(unittest.TestCase):
    def test_aiocqhttp_accepts_string_or_integer_self_ids(self):
        for key in ("3954267737", 3954267737):
            with self.subTest(key=key):
                client = SimpleNamespace(_wsr_api_clients={key: object()})
                self.assertTrue(native_client_connected(platform(client=client), "3954267737"))

    def test_aiocqhttp_rejects_absent_or_closed_connections(self):
        for clients in ({}, {"100": None}, None, {"100": object()}):
            with self.subTest(clients=clients):
                client = SimpleNamespace(_wsr_api_clients=clients) if clients is not None else None
                self.assertFalse(native_client_connected(platform(client=client), "3954267737"))

    def test_other_platforms_keep_their_existing_check(self):
        client = SimpleNamespace(is_shutting_down=False, is_closed=lambda: False)
        self.assertTrue(native_client_connected(platform("qq_official", client), "100"))
        closed = SimpleNamespace(is_shutting_down=False, is_closed=lambda: True)
        self.assertFalse(native_client_connected(platform("qq_official", closed), "100"))
        self.assertFalse(native_client_connected(platform("qq_official", SimpleNamespace(is_shutting_down=True)), "100"))


if __name__ == "__main__":
    unittest.main()
