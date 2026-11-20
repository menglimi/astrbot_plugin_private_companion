"""Account recovery uses synthetic adapters and never connects to a host."""

import asyncio
from types import SimpleNamespace as NS
import unittest

from companion_v2.adapters.astrbot.connections import NativeAccountConnections
from companion_v2.adapters.astrbot.native import HostAccount
from companion_v2.execution.models import TaskError
from tests.native_adapter_support import FakePlatform


def official(platform_id="official", appid="app-a"):
    platform = FakePlatform(platform_id)
    platform.metadata.name = "qq_official"
    platform.appid = appid
    platform.client = NS(is_shutting_down=False, is_closed=lambda: False)
    return platform


class AccountConnectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.accounts = (HostAccount("official", "official", "bot-a"), HostAccount("onebot", "100", "bot-b"))
        self.platforms = {"official": official(), "onebot": FakePlatform("onebot")}
        self.context = NS(get_platform_inst=self.platforms.get)
        self.connections = NativeAccountConnections(self.context, self.accounts)

    def assert_code(self, code, operation):
        with self.assertRaises(TaskError) as raised:
            operation()
        self.assertEqual(raised.exception.code, code)

    async def test_offline_account_does_not_block_another_and_can_load_later(self):
        del self.platforms["official"]
        connections = NativeAccountConnections(self.context, self.accounts)
        self.assertIs(connections.acquire(self.accounts[1]).platform, self.platforms["onebot"])
        self.assert_code("host_platform_unavailable", lambda: connections.acquire(self.accounts[0]))
        self.platforms["official"] = official()
        self.assertIs(connections.acquire(self.accounts[0]).platform, self.platforms["official"])
        self.assertIsNotNone(connections.statuses()[0]["last_recovered_at"])

    async def test_same_app_replacement_invalidates_only_its_old_lease(self):
        a, b = [self.connections.acquire(account) for account in self.accounts]
        waiter = asyncio.create_task(self.connections.wait_changed(a))
        await asyncio.sleep(0)
        self.platforms["official"] = official()
        self.connections.refresh()
        await asyncio.wait_for(waiter, 1)
        self.assert_code("host_connection_changed", lambda: self.connections.assert_current(a))
        self.assertEqual(self.connections.assert_current(b), b)
        current = self.connections.acquire(self.accounts[0])
        self.assertGreater(current.generation, a.generation)
        self.assertIsNot(current.client, a.client)
        self.assertIsNotNone(self.connections.statuses()[0]["last_recovered_at"])

    async def test_changed_app_id_is_refused_even_on_the_same_platform_object(self):
        lease = self.connections.acquire(self.accounts[0])
        self.platforms["official"].appid = "app-other"
        self.assert_code("host_account_changed", lambda: self.connections.assert_current(lease))
        self.assertEqual(self.connections.statuses()[0]["state"], "identity_changed")
        self.platforms["official"] = official(appid="app-other")
        self.assert_code("host_account_changed", lambda: self.connections.acquire(self.accounts[0]))

    async def test_secret_rotation_does_not_change_identity(self):
        lease = self.connections.acquire(self.accounts[0])
        self.platforms["official"].secret = "rotated-fixture-secret"
        self.assertEqual(self.connections.assert_current(lease), lease)

    async def test_same_client_offline_and_online_create_new_generation(self):
        lease = self.connections.acquire(self.accounts[0])
        self.platforms["official"].client.is_shutting_down = True
        self.assert_code("host_account_offline", lambda: self.connections.acquire(self.accounts[0]))
        self.assertEqual(self.connections.statuses()[0]["state"], "waiting")
        self.platforms["official"].client.is_shutting_down = False
        self.assert_code("host_connection_changed", lambda: self.connections.assert_current(lease))
        self.assertEqual(self.connections.statuses()[0]["state"], "online")

    async def test_onebot_transport_reconnect_invalidates_lease_without_replacing_client(self):
        lease = self.connections.acquire(self.accounts[1])
        self.platforms["onebot"].client._wsr_api_clients["100"] = object()
        self.assert_code("host_connection_changed", lambda: self.connections.assert_current(lease))

    async def test_duplicate_refresh_does_not_change_generation_or_recovery_time(self):
        lease = self.connections.acquire(self.accounts[0])
        first = self.connections.statuses()
        for _ in range(3):
            self.connections.refresh()
        self.assertEqual(first, self.connections.statuses())
        self.assertEqual(self.connections.generation(self.accounts[0]), lease.generation)

    async def test_generation_read_does_not_accept_a_new_connection(self):
        lease = self.connections.acquire(self.accounts[0])
        self.platforms["official"] = official()
        self.assertEqual(self.connections.generation(self.accounts[0]), lease.generation)
        self.assertGreater(self.connections.acquire(self.accounts[0]).generation, lease.generation)

    async def test_wait_on_an_already_changed_lease_returns_immediately(self):
        lease = self.connections.acquire(self.accounts[0])
        self.platforms["official"] = official()
        self.connections.refresh()
        await asyncio.wait_for(self.connections.wait_changed(lease), 1)

    async def test_worker_thread_refresh_wakes_loop_waiter(self):
        lease = self.connections.acquire(self.accounts[0])
        waiter = asyncio.create_task(self.connections.wait_changed(lease))
        await asyncio.sleep(0)
        self.platforms["official"] = official()
        await asyncio.to_thread(self.connections.refresh)
        await asyncio.wait_for(waiter, 1)
        self.assert_code("host_connection_changed", lambda: self.connections.assert_current(lease))

    async def test_cancelled_waiter_is_removed_before_next_worker_refresh(self):
        lease = self.connections.acquire(self.accounts[0])
        waiter = asyncio.create_task(self.connections.wait_changed(lease))
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.platforms["official"] = official()
        await asyncio.to_thread(self.connections.refresh)
        self.assertEqual(self.connections.statuses()[0]["state"], "online")

    async def test_missing_optional_old_sdk_status_interfaces_retains_online_fallback(self):
        self.platforms["official"].client = object()
        self.assertIs(self.connections.acquire(self.accounts[0]).client, self.platforms["official"].client)

    async def test_missing_old_sdk_identity_does_not_accept_an_unverifiable_replacement(self):
        del self.platforms["official"].appid
        connections = NativeAccountConnections(self.context, self.accounts)
        lease = connections.acquire(self.accounts[0])
        self.assertEqual(connections.assert_current(lease), lease)
        self.platforms["official"] = official()
        self.assert_code("host_account_identity_unavailable", lambda: connections.acquire(self.accounts[0]))

    async def test_old_sdk_can_attach_its_first_client_after_platform_creation(self):
        del self.platforms["official"].appid
        self.platforms["official"].client = None
        connections = NativeAccountConnections(self.context, self.accounts)
        self.assert_code("host_account_offline", lambda: connections.acquire(self.accounts[0]))
        self.platforms["official"].client = object()
        self.assertIs(connections.acquire(self.accounts[0]).client, self.platforms["official"].client)

    async def test_online_platforms_excludes_waiting_accounts_until_first_online(self):
        self.platforms["onebot"].client._wsr_api_clients.pop("100")
        connections = NativeAccountConnections(self.context, self.accounts)
        self.assertEqual(connections.statuses()[1]["state"], "waiting")
        self.assertEqual(
            connections.online_platforms(),
            {"official": (self.platforms["official"], self.platforms["official"].client)},
        )

        still_waiting = FakePlatform("onebot")
        still_waiting.client._wsr_api_clients.pop("100")
        self.platforms["onebot"] = still_waiting
        connections.refresh()
        self.assertNotIn("onebot", connections.online_platforms())

        first_online = FakePlatform("onebot")
        self.platforms["onebot"] = first_online
        connections.refresh()
        self.assertEqual(connections.online_platforms()["onebot"], (first_online, first_online.client))

    async def test_shared_platform_counts_online_when_any_account_on_it_is_online(self):
        shared_accounts = (
            HostAccount("onebot", "100", "bot-a"),
            HostAccount("onebot", "101", "bot-b"),
        )
        self.platforms["onebot"].client._wsr_api_clients.pop("101")
        connections = NativeAccountConnections(self.context, shared_accounts)
        self.assertEqual(
            connections.online_platforms(),
            {"onebot": (self.platforms["onebot"], self.platforms["onebot"].client)},
        )
        self.platforms["onebot"].client._wsr_api_clients.pop("100")
        connections.refresh()
        self.assertEqual(connections.online_platforms(), {})
