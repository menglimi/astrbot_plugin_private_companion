"""Real MultiHost -> registry -> Memory SQLite -> prompt/summary contract.

MEMORY_COMPANION_CHECKOUT selects a source checkout, never an installed plugin.
"""
import importlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace as NS
import sys
import unittest

from tests import test_account_link_host as fixtures
from tests.multi_host_support import MultiFixture
from companion_v2.adapters.astrbot.memory_providers import memory_provider_registry
from companion_v2.execution.models import stamp
from companion_v2.relationships.account_link_memory import CONFIRMATION

MEMORY = os.environ.get("MEMORY_COMPANION_CHECKOUT")


@unittest.skipUnless(MEMORY, "set MEMORY_COMPANION_CHECKOUT to run the real two-plugin host test")
class LinkedMemoryHostTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    prepare = fixtures.AccountLinkHostTests.prepare
    flush = fixtures.AccountLinkHostTests.flush
    person = staticmethod(fixtures.AccountLinkHostTests.person)
    request = fixtures.AccountLinkHostTests.request
    command = fixtures.AccountLinkHostTests.command

    async def asyncSetUp(self):
        await fixtures.AccountLinkHostTests.asyncSetUp(self)
        from astrbot_host.integration_registry import UnifiedCompanionExtensionAPI
        root = Path(MEMORY).resolve()
        package = ModuleType("astrbot_plugin_memory_companion")
        package.__path__ = [str(root)]
        sys.modules.setdefault("astrbot_plugin_memory_companion", package)
        linkage_module = importlib.import_module("astrbot_plugin_memory_companion.core.companion_linkage")
        models = importlib.import_module("astrbot_plugin_memory_companion.core.models")
        store_module = importlib.import_module("astrbot_plugin_memory_companion.core.store")
        self.memory_temp = TemporaryDirectory(); self.addCleanup(self.memory_temp.cleanup)
        self.store = store_module.MemoryStore(Path(self.memory_temp.name) / "memory.db")
        self.store.initialize(); self.addCleanup(self.store.close)
        self.plugin = NS(context=self.native, service=NS(store=self.store,
            config=NS(bool=lambda *args: True), _user_memory_category=lambda _: "other"))
        self.native.get_all_stars = lambda: [NS(star_cls=self.plugin, activated=True)]
        self.extension = UnifiedCompanionExtensionAPI(self.controller)
        self.addCleanup(self.extension.close_memory_providers)
        self.linkage = linkage_module.CompanionMemoryLinkage(self.plugin)
        self.assertTrue(self.linkage.register()); self.addCleanup(self.linkage.unregister)
        for label, context in (("first", self.a), ("second", self.b)):
            item = models.MemoryRecord(id="memory-" + label, memory_type="manual_memory",
                subject=models.EntityRef(kind="user", id=context.scope.native_scope_id),
                object=models.EntityRef.bot_self(context.scope.account_id),
                scope="private", session_id=f"{context.scope.platform}:FriendMessage:{context.scope.session_id}",
                platform="aiocqhttp", owner_bot_id=context.scope.account_id,
                metadata={"persona_id": "original"}, visibility="private_pair", lifecycle="stable_memory",
                content="SYNTHETIC_LONG_MEMORY_" + label, created_at=stamp(self.controller.now()),
                occurred_at=stamp(self.controller.now()))
            await self.store.insert_memory(item)

    async def enable(self):
        response = await self.request("save", body=self.command(memory={"enabled": True,
            "history": "since_enabled", "confirmation": CONFIRMATION}))
        self.assertEqual(response["status"], "ok", response)

    async def test_real_host_bidirectional_prompt_and_summary(self):
        await self.enable()
        for target in ("first", "second"):
            prepared = await self.prepare(target, "memory-" + target, "synthetic question")
            prompt = prepared["prompt_instruction"]
            self.assertIn("SYNTHETIC_LONG_MEMORY_first", prompt)
            self.assertIn("SYNTHETIC_LONG_MEMORY_second", prompt)
            self.assertIn("source_session_id", prompt)
            from astrbot_host.memory_linkage import read_external_memory_summary
            summary = await read_external_memory_summary(self.controller, prepared["context"])
            self.assertEqual(summary["state"], "ready", summary)
            self.assertEqual(summary["counts"]["total"], 2)

    async def test_default_off_and_unlink_stop_cross_account_injection(self):
        prepared = await self.prepare("first", "before-enable", "synthetic question")
        self.assertIn("SYNTHETIC_LONG_MEMORY_first", prepared["prompt_instruction"])
        self.assertNotIn("SYNTHETIC_LONG_MEMORY_second", prepared["prompt_instruction"])
        await self.enable()
        response = await self.request("unlink", body={**self.person(self.a),
            "expected_revision": self.links.read()["revision"], "operation_key": "unlink-memory"})
        self.assertEqual(response["status"], "ok", response)
        prepared = await self.prepare("first", "after-unlink", "synthetic question")
        self.assertIn("SYNTHETIC_LONG_MEMORY_first", prepared["prompt_instruction"])
        self.assertNotIn("SYNTHETIC_LONG_MEMORY_second", prepared["prompt_instruction"])

    async def test_api_requires_separate_history_confirmation(self):
        response = await self.request("save", body=self.command(memory={"enabled": True,
            "history": "all", "confirmation": CONFIRMATION}))
        self.assertEqual(response["status"], "error", response)
        self.assertIn("account_link_memory_confirmation_required", str(response))
        self.assertEqual(self.links.read()["revision"], 0)


class NativeMemoryIdentityTests(unittest.TestCase):
    def test_wechat_mapping_uses_native_login(self):
        from astrbot_host.multi_session import MultiHostSession
        platform = NS(bot_self_id="native-login", meta=lambda: NS(name="wechat_native"))
        scope = NS(account_id="adapter-alias")
        session = NS(bridge=NS(platform=lambda original: platform if original is scope else None))
        self.assertEqual(MultiHostSession._linked_memory_identity(session, NS(scope=scope)),
            {"owner_bot_id": "native-login", "platform_name": "wechat_native"})

    def test_unknown_native_login_fails_closed(self):
        from astrbot_host.multi_session import MultiHostSession
        from companion_v2.execution.models import TaskError
        for owner in ("", "self", None):
            platform = NS(bot_self_id=owner, meta=lambda: NS(name="wechat_native"))
            with self.subTest(owner=owner), self.assertRaises(TaskError):
                MultiHostSession._linked_memory_identity(NS(bridge=NS(platform=lambda _: platform)), NS(scope=NS()))
