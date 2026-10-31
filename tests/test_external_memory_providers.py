"""Registered memory callbacks survive neither stale plugins nor stale scopes."""

import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from companion_v2.adapters.astrbot.memory_owner import read_memory_owner
from companion_v2.adapters.astrbot.memory_providers import ExternalMemoryProviderRegistry
from companion_v2.adapters.astrbot.memory_push import memory_push_provider
from companion_v2.capabilities.memory_push import MemoryPushRecord, push_memory
from companion_v2.execution.models import TaskError
from tests.group_host_support import GroupFixture


class ExternalMemoryRegistryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.plugin = object()
        self.star = NS(star_cls=self.plugin, activated=True)
        self.native = NS(get_all_stars=lambda: [self.star])
        self.registry = ExternalMemoryProviderRegistry(self.native)
        self.addCleanup(self.registry.close)

    def register(self, provider_id="third_party", **changes):
        return self.registry.register(provider_id, **{"plugin": self.plugin, "read": lambda _: {}, **changes})

    async def test_priority_is_deterministic_per_capability_and_metadata_does_not_invoke_callbacks(self):
        read = Mock(side_effect=AssertionError("metadata must not read memory"))
        self.register("b", read=read)
        self.register("a", read=read)
        self.register("writer", read=None, write=read, priority=10)
        self.assertEqual(self.registry.select("read").provider_id, "a")
        self.assertEqual(self.registry.select("write").provider_id, "writer")
        snapshot = self.registry.snapshot()
        self.assertEqual([item["provider_id"] for item in snapshot], ["writer", "a", "b"])
        self.assertNotIn("registration_id", json.dumps(snapshot))
        read.assert_not_called()
        self.assertFalse(read_memory_owner(self.native).ownership.delegated)

    async def test_reregistration_and_hot_reload_fence_old_removal_tokens(self):
        old = self.register()
        new = self.register()
        self.assertFalse(self.registry.unregister(old))
        self.assertIsNotNone(self.registry.select("read"))
        with self.assertRaises(ValueError):
            self.register(plugin=object())
        self.star.star_cls = object()
        self.assertIsNone(self.registry.select("read"))
        latest = self.register(plugin=self.star.star_cls)
        self.assertFalse(self.registry.unregister(new))
        self.assertTrue(self.registry.unregister(latest))
        self.assertIsNone(self.registry.select())

    async def test_plugin_status_and_explicit_lifecycle_both_control_ownership(self):
        active = True
        self.register(handles_model_memory=True, display_name="第三方记忆", is_active=lambda: active)
        self.assertEqual(read_memory_owner(self.native).ownership.provider, "third_party")
        active = False
        self.assertFalse(read_memory_owner(self.native).ownership.delegated)
        active = True
        self.star.activated = False
        self.assertIsNone(self.registry.select())
        self.star.activated = True
        self.native.get_all_stars = lambda: []
        self.assertIsNone(self.registry.select())

    async def test_old_host_cannot_use_or_close_a_replacement_registry(self):
        self.register()
        previous = self.registry.select()
        replacement = ExternalMemoryProviderRegistry(self.native)
        self.addCleanup(replacement.close)
        replacement.register("new", plugin=self.plugin, read=lambda _: {})
        self.assertFalse(self.registry.active(previous))
        with self.assertRaises(ValueError):
            self.register()
        self.registry.close()
        self.assertEqual(replacement.select().provider_id, "new")

    async def test_result_from_unregistered_provider_is_discarded(self):
        started, finish = asyncio.Event(), asyncio.Event()

        async def read(request):
            started.set()
            await finish.wait()
            return {"items": [{"content": "stale content"}]}

        token = self.register(read=read)
        task = asyncio.create_task(self.registry.invoke(self.registry.select("read"), "read", {}))
        await asyncio.wait_for(started.wait(), 1)
        self.registry.unregister(token)
        finish.set()
        with self.assertRaises(TaskError) as caught:
            await task
        self.assertEqual(caught.exception.code, "external_memory_registration_stale")

    async def test_timeout_never_retries_or_falls_through_to_another_writer(self):
        first, fallback = [], AsyncMock(return_value="other")

        async def blocked(request):
            first.append(request)
            await asyncio.Event().wait()

        self.register("a", read=None, write=blocked, timeout_seconds=0.02)
        self.register("b", read=None, write=fallback)
        result = await push_memory(memory_push_provider(self.native), MemoryPushRecord("record-1", "今天读书"))
        self.assertIsNone(result)
        self.assertEqual(len(first), 1)
        fallback.assert_not_awaited()

    async def test_sync_callbacks_and_redacted_errors(self):
        self.register(read=lambda request: {"items": [request]})
        self.assertEqual(await self.registry.invoke(self.registry.select(), "read", {"query": "咖啡"}),
                         {"items": [{"query": "咖啡"}]})
        self.register(read=Mock(side_effect=RuntimeError("private provider detail")))
        with self.assertRaises(TaskError) as caught:
            await self.registry.invoke(self.registry.select(), "read", {})
        self.assertEqual(caught.exception.code, "external_memory_callback_failed")
        self.assertNotIn("private provider detail", str(caught.exception))

    async def test_cancellation_propagates_without_retry(self):
        read = AsyncMock(side_effect=asyncio.CancelledError)
        self.register(read=read)
        with self.assertRaises(asyncio.CancelledError):
            await self.registry.invoke(self.registry.select(), "read", {})
        self.assertEqual(read.await_count, 1)

    async def test_stopped_host_rejects_registrations_and_callbacks(self):
        running = True
        registry = ExternalMemoryProviderRegistry(self.native, host_active=lambda: running)
        self.addCleanup(registry.close)
        read = AsyncMock(return_value={})
        registry.register("host-bound", plugin=self.plugin, read=read)
        entry = registry.select()
        running = False
        with self.assertRaises(ValueError):
            registry.register("new", plugin=self.plugin, read=read)
        with self.assertRaises(TaskError):
            await registry.invoke(entry, "read", {})
        read.assert_not_awaited()

    async def test_plugin_termination_closes_registrations_before_host_drain(self):
        from tests.test_main_no_reply import ENTRY

        self.register()
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        plugin._imports_retired = True
        plugin.extension_api = NS(close_memory_providers=self.registry.close)

        async def terminate():
            self.assertIsNone(self.registry.select())
            self.assertEqual(self.registry.snapshot(), [])

        plugin.staging = NS(terminate=terminate, runtime=None)
        await plugin.terminate()


class ExternalMemoryHostTests(GroupFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from astrbot_host.integration_registry import UnifiedCompanionExtensionAPI

        self.app = await self.start_host()
        self.plugin = object()
        self.star = NS(star_cls=self.plugin, activated=True)
        self.native.get_all_stars = lambda: [self.star]
        self.api = UnifiedCompanionExtensionAPI(self.controller)
        self.addCleanup(self.api.close_memory_providers)
        self.reads, self.writes = [], []

    async def read(self, request):
        self.reads.append(request)
        return {"items": [{"id": "fact-1", "content": "你喜欢听雨声。", "source": "用户自述",
                           "private_debug": "must not escape"}], "private_debug": "must not escape"}

    async def write(self, record):
        self.writes.append(record)
        return record["memory_id"]

    def register(self, **changes):
        return self.api.register_memory_provider("third_party", **{
            "plugin": self.plugin, "read": self.read, "write": self.write, **changes})

    async def prepared(self, identifier, *, event=None):
        result = await self.controller.prepare_core_reply(event or self.event(identifier=identifier, text="听雨声"))
        self.assertTrue(result.is_success, result.failure)
        prepared = result.unwrap()
        await prepared["native_source_future"]
        return prepared

    async def test_registered_reader_reaches_native_prompt_and_preserves_builtin_tools(self):
        token = self.register()
        prepared = await self.prepared("external-recall")
        self.assertIn("你喜欢听雨声。", prepared["prompt_instruction"])
        self.assertIn("外部记忆参考", prepared["prompt_instruction"])
        self.assertNotIn("must not escape", prepared["prompt_instruction"])
        request, = self.reads
        context = prepared["context"]
        self.assertEqual(request["context"]["scope"], context.scope.document())
        self.assertEqual(request["context"]["role"]["subject_id"], context.role_binding.role.subject_id)
        self.assertEqual(request["context"]["principal"]["native_account_id"], "200")
        self.assertEqual(request["query"], "听雨声")
        self.assertFalse(self.app.tools._memory_is_external())
        self.assertIn("memory.query", [tool.action for tool in self.app.tools.memory.catalog_for(context)])
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assertTrue(self.api.unregister_memory_provider(token))
        later = await self.prepared("external-unregistered")
        self.assertNotIn("你喜欢听雨声。", later["prompt_instruction"])
        self.assertEqual(len(self.reads), 1)

    async def test_reader_is_never_called_for_groups_or_disabled_automatic_memory(self):
        self.register()
        await self.prepared("external-group", event=self.group_event(identifier="external-group"))
        self.assertEqual(self.reads, [])
        access = self.app.memory.writer.access
        prepared = await self.prepared("external-private")
        policy = access.policy_for(prepared["context"])
        with patch.object(access, "policy_for", return_value=replace(policy, automatic_context=False)):
            result = await self.prepared("external-disabled")
        self.assertNotIn("你喜欢听雨声。", result["prompt_instruction"])
        self.assertEqual(len(self.reads), 1)

    async def test_explicit_model_owner_takes_over_and_disable_restores_builtin_memory(self):
        self.register(handles_model_memory=True)
        prepared = await self.prepared("external-owner")
        self.assertEqual(self.reads, [])
        self.assertNotIn("你喜欢听雨声。", prepared["prompt_instruction"])
        self.assertTrue(self.app.tools._memory_is_external())
        self.star.activated = False
        self.assertFalse(self.app.tools._memory_is_external())

    async def test_generic_writer_receives_full_content_and_resolved_identity(self):
        self.register()
        prepared = await self.prepared("external-write")
        record = MemoryPushRecord("record-1", "长记录" * 1600, context=prepared["context"])
        port = memory_push_provider(self.native)
        self.assertEqual(await port.push(record), "record-1")
        self.assertEqual(await port.push(record), "record-1")
        self.assertEqual([item["memory_id"] for item in self.writes], ["record-1", "record-1"])
        self.assertEqual(self.writes[0]["content"], record.content)
        self.assertEqual(self.writes[0]["context"]["scope"], prepared["context"].scope.document())
        self.assertEqual(self.writes[0]["schema"], self.api.memory_provider_api_version)
        self.assertEqual(self.writes[0]["source_plugin"], "astrbot_plugin_private_companion")

    async def test_summary_uses_registered_callback_and_redacts_extra_material(self):
        from astrbot_host.memory_linkage import read_external_memory_summary

        summary = AsyncMock(return_value={"counts": {"preference": 3, "total": 3},
                                          "private_debug": "must not escape"})
        self.register(summary=summary)
        prepared = await self.prepared("external-summary")
        result = await read_external_memory_summary(self.controller, prepared["context"])
        self.assertEqual((result["state"], result["provider"], result["counts"]["total"]),
                         ("ready", "third_party", 3))
        self.assertNotIn("must not escape", json.dumps(result))
        self.assertEqual(summary.await_args.args[0]["purpose"], "summary")

    async def test_failed_read_keeps_reply_and_scoped_material_cannot_be_reused(self):
        from astrbot_host.external_memory import external_memory_section, prepare_external_memory

        self.register(read=AsyncMock(side_effect=RuntimeError("private provider detail")))
        prepared = await self.prepared("external-broken")
        self.assertNotIn("private provider detail", prepared["prompt_instruction"])
        self.register()
        material = await prepare_external_memory(self.native, self.app, prepared["context"], prepared["native_input"])
        other = await self.prepared("external-second", event=self.event("second", "external-second"))
        self.assertEqual(external_memory_section(material, self.app, other["context"]), "")
        self.star.activated = False
        self.assertEqual(external_memory_section(material, self.app, prepared["context"]), "")

    async def test_recall_enforces_budget_and_rechecks_policy_after_await(self):
        from astrbot_host.external_memory import prepare_external_memory

        prepared = await self.prepared("external-budget")
        context = prepared["context"]
        access = self.app.memory.writer.access
        policy = replace(access.policy_for(context), context_chars=256, context_items=2)
        self.register(read=AsyncMock(return_value={"items": [
            {"content": "超长" * 256}, {"content": "短记忆"}, {"content": "超出条数"}]}))
        with patch.object(access, "policy_for", return_value=policy):
            material = await prepare_external_memory(self.native, self.app, context, prepared["native_input"])
        self.assertEqual(material["items"], [{"content": "短记忆"}])

        current_policy = policy

        async def revoke(request):
            nonlocal current_policy
            current_policy = replace(policy, automatic_context=False)
            return {"items": [{"content": "不应注入"}]}

        self.register(read=revoke)
        with patch.object(access, "policy_for", side_effect=lambda _: current_policy):
            material = await prepare_external_memory(self.native, self.app, context, prepared["native_input"])
        self.assertIsNone(material)


if __name__ == "__main__":
    unittest.main()
