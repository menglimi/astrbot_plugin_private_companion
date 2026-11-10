"""One memory-provider registry per host context, however many facades exist.

The plugin's public ``extension_api``, the native-extension service and the
manifest route all construct ``UnifiedCompanionExtensionAPI`` on the same
AstrBot context. Each construction used to install a fresh registry, so a
third-party registration made through the public facade raised
"memory provider registry is closed" once any other facade had been built.
"""

import unittest

from companion_v2.adapters.astrbot.memory_providers import (
    ExternalMemoryProviderRegistry, memory_provider_registry,
    shared_memory_provider_registry,
)


class Context:
    """Stand-in for the AstrBot context: the registry only uses it as a host."""


def owner():
    return object()


class SharedMemoryRegistryTests(unittest.TestCase):
    def test_a_second_facade_reuses_the_live_registry(self):
        context = Context()
        first = shared_memory_provider_registry(context)
        token = first.register("memory_diary", plugin=owner(), read=lambda request: None)
        self.assertTrue(token)

        second = shared_memory_provider_registry(context)

        self.assertIs(second, first)
        self.assertIs(memory_provider_registry(context), first)
        self.assertEqual([item["provider_id"] for item in second.snapshot()], ["memory_diary"])
        self.assertTrue(second.unregister(token))

    def test_register_still_works_after_another_facade_is_built(self):
        context = Context()
        public = shared_memory_provider_registry(context)
        # Building the other facades is exactly what used to break this call.
        shared_memory_provider_registry(context)
        shared_memory_provider_registry(context)

        token = public.register("memory_diary", plugin=owner(), write=lambda record: record["memory_id"])

        self.assertTrue(token)
        self.assertEqual(memory_provider_registry(context), public)

    def test_a_registry_from_another_host_lifetime_is_not_shared(self):
        context = Context()
        old_owner = object()
        old = shared_memory_provider_registry(context, owner=old_owner)
        new_owner = object()

        fresh = shared_memory_provider_registry(context, owner=new_owner)

        self.assertIsNot(fresh, old)
        self.assertIs(fresh.owner, new_owner)
        self.assertIs(memory_provider_registry(context), fresh)

    def test_a_closed_registry_is_replaced(self):
        context = Context()
        first = shared_memory_provider_registry(context)
        first.close()

        second = shared_memory_provider_registry(context)

        self.assertIsNot(second, first)
        self.assertFalse(second.closed)
        self.assertIs(memory_provider_registry(context), second)

    def test_a_registry_whose_host_stopped_is_replaced(self):
        context = Context()
        stopped = ExternalMemoryProviderRegistry(context, host_active=lambda: False)

        fresh = shared_memory_provider_registry(context)

        self.assertIsNot(fresh, stopped)
        self.assertTrue(fresh.host_active())
        self.assertIs(memory_provider_registry(context), fresh)


if __name__ == "__main__":
    unittest.main()
