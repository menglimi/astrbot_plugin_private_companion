"""Delayed group relay and private receipt event dispatch."""

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import time
import unittest

from astrbot_host.relay_pending import add_task, claim_private_receipts
from astrbot_host.relay_pending_events import process_group_presence, process_private_receipts
from companion_v2.storage.runtime import StorageRuntime


NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)


class FakeReverseApi:
    def __init__(self):
        self.calls = []

    async def call_action(self, action, **kwargs):
        self.calls.append((action, kwargs))
        return {"message_id": 900 + len(self.calls)}


class RelayPendingEventTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="v2-relay-pending-")
        self.addCleanup(temporary.cleanup)
        self.runtime = StorageRuntime.open(data_root=Path(temporary.name), installation_id="relay-events")
        self.addCleanup(self.runtime.close)
        self.api = FakeReverseApi()
        self.client = SimpleNamespace(_api=SimpleNamespace(_wsr_api=self.api),
                                      _wsr_api_clients={"account": self.api})
        bridge = SimpleNamespace(platforms={}, groups=SimpleNamespace(platforms={}))
        self.controller = SimpleNamespace(
            runtime=self.runtime,
            native_types=SimpleNamespace(reverse_api=FakeReverseApi),
            session=SimpleNamespace(bridge=bridge),
        )
        self.event = SimpleNamespace(bot=self.client, message_str="好的",
                                     platform_meta=SimpleNamespace(name="aiocqhttp"))

    async def test_delayed_group_relay_fires_when_target_speaks(self):
        add_task(self.runtime, {
            "kind": "group_wait", "state": "waiting", "group_id": "5005",
            "target_user_id": "7777", "mention_id": "7777", "text": "看到后回我",
            "source_kind": "private", "source_id": "1001", "source_user": "1001",
            "expires_at": time.time() + 3600, "signature": "delayed-1",
        })
        target = SimpleNamespace(group_id="5005", platform_id="onebot", account_id="account")
        context = SimpleNamespace(actor=SimpleNamespace(principal=SimpleNamespace(native_account_id="7777")))
        await process_group_presence(self.controller, self.event, target, context)
        action, kwargs = self.api.calls[0]
        self.assertEqual(action, "send_group_msg")
        self.assertEqual(kwargs["group_id"], 5005)
        self.assertEqual(kwargs["message"][0], {"type": "at", "data": {"qq": "7777"}})
        self.assertEqual(kwargs["message"][-1]["data"]["text"], "看到后回我")

    async def test_private_receipt_reports_next_reply_to_source(self):
        add_task(self.runtime, {
            "kind": "private_receipt", "state": "waiting_reply", "target_user_id": "2002",
            "source_kind": "private", "source_id": "1001", "source_user": "1001",
            "question": "今晚来吗", "sent_text": "今晚来吗", "confirm_before_report": False,
            "expires_at": time.time() + 3600, "signature": "receipt-1",
        })
        target = SimpleNamespace(participant_id="2002", platform_id="onebot", account_id="account")
        await process_private_receipts(self.controller, self.event, target)
        action, kwargs = self.api.calls[0]
        self.assertEqual(action, "send_private_msg")
        self.assertEqual(kwargs["user_id"], 1001)
        self.assertIn("今晚来吗", kwargs["message"][0]["data"]["text"])
        self.assertIn("好的", kwargs["message"][0]["data"]["text"])

    async def test_receipt_permission_is_requested_before_reporting(self):
        add_task(self.runtime, {
            "kind": "private_receipt", "state": "waiting_reply", "target_user_id": "2002",
            "source_kind": "private", "source_id": "1001", "source_user": "1001",
            "question": "方便吗", "sent_text": "方便吗", "confirm_before_report": True,
            "expires_at": time.time() + 3600, "signature": "receipt-2",
        })
        target = SimpleNamespace(participant_id="2002", platform_id="onebot", account_id="account")
        await process_private_receipts(self.controller, self.event, target)
        self.assertEqual(self.api.calls[0][0], "send_private_msg")
        self.assertEqual(self.api.calls[0][1]["user_id"], 2002)
        self.assertIn("可以", self.api.calls[0][1]["message"][0]["data"]["text"])

        self.event.message_str = "可以"
        await process_private_receipts(self.controller, self.event, target)
        self.assertEqual(self.api.calls[1][0], "send_private_msg")
        self.assertEqual(self.api.calls[1][1]["user_id"], 1001)
        self.assertIn("方便吗", self.api.calls[1][1]["message"][0]["data"]["text"])


if __name__ == "__main__":
    unittest.main()
