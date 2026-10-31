"""The memory overview counts every authorized conversation input owner.

Two writers store an authorized conversation input: the host event adapter
publishes ``conversation.host_input`` and the native core observation path
publishes ``conversation.native_input``. The overview must report both, and it
must not count assistant output or withdrawn inputs.
"""

import unittest

from companion_v2.configuration.registry import FieldRegistry
from companion_v2.configuration.service import ConfigurationService
from companion_v2.execution.models import digest
from companion_v2.foundation.refs import SourceRef, VersionRef
from companion_v2.memory.configuration import configuration_fields
from companion_v2.memory.management import MemoryManagement
from companion_v2.memory.service import MemoryService
from companion_v2.sources.models import SourceRecord
from tests.memory_support import MemoryFixture


class MemoryOverviewSourcesTests(MemoryFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.config = ConfigurationService(
            self.runtime, FieldRegistry(version="memory-overview-test", fields=configuration_fields()))
        self.manager = MemoryManagement(MemoryService(self.tasks, configuration=self.config), self.core)

    def publish(self, owner, identifier):
        reference = SourceRef(identifier, owner, self.context.scope,
                              VersionRef(identifier, 1), self.clock.now())
        body = "已授权的对话输入 " + identifier
        return self.messages.sources.publish(
            SourceRecord(reference, self.context.audience, digest(body), body),
            observed_at=self.clock.now()).unwrap()

    def available(self):
        return self.manager.overview(self.context).unwrap()["sources"]["available_messages"]

    def test_host_and_native_inputs_are_both_available_messages(self):
        self.assertEqual(self.available(), 0)
        self.publish("conversation.host_input", "host-input-1")
        self.assertEqual(self.available(), 1)
        self.publish("conversation.native_input", "native-input-1")
        self.assertEqual(self.available(), 2)
        # Publisher replays keep one row per version; the count stays stable.
        self.publish("conversation.native_input", "native-input-1")
        self.assertEqual(self.available(), 2)

    def test_assistant_output_and_withdrawn_inputs_are_not_available(self):
        self.publish("conversation.native_input", "native-input-2")
        self.publish("conversation.native_assistant", "native-output-1")
        self.assertEqual(self.available(), 1)
        source = self.publish("conversation.host_input", "host-input-2")
        self.assertEqual(self.available(), 2)
        self.messages.sources.revoke(source.reference, expected_governance_revision=1,
                                     observed_at=self.clock.now()).unwrap()
        self.assertEqual(self.available(), 1)
