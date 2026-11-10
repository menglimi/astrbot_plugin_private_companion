"""Manual memory addition stays an administrator statement with real evidence."""

from __future__ import annotations

import unittest

from companion_v2.configuration.registry import FieldRegistry
from companion_v2.configuration.service import ConfigurationService
from companion_v2.execution.models import TaskError
from companion_v2.memory.configuration import configuration_fields
from companion_v2.memory.management import MemoryManagement
from companion_v2.memory.service import MemoryService
from tests.memory_support import MemoryFixture


class MemoryAddTests(MemoryFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.config = ConfigurationService(self.runtime, FieldRegistry(version="memory-add-test",
                                                                       fields=configuration_fields()))
        self.service = MemoryService(self.tasks, configuration=self.config)
        self.manager = MemoryManagement(self.service, self.core)

    def add(self, **changes):
        arguments = dict(operation_key="manual-add", subject="self", predicate="职责",
                         object="照看小屋", representation="管理员手动记录：角色负责照看小屋。")
        arguments.update(changes)
        return self.manager.add(self.context, **arguments)

    def test_manual_add_is_readable_attributed_to_the_operator_and_replays(self):
        first = self.add().unwrap()
        self.assertEqual(first["revision"], 1)
        items = self.manager.list(self.context).unwrap()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["object"], "照看小屋")
        self.assertEqual(items[0]["subject"], "self")
        self.assertEqual(items[0]["attribution"], "third_party")
        self.assertEqual(self.add().unwrap(), first)

    def test_manual_add_rejects_a_reused_key_with_different_content(self):
        self.add().unwrap()
        with self.assertRaises(TaskError) as raised:
            self.add(object="另一件事")
        self.assertEqual(raised.exception.code, "memory_operation_conflict")
        # 冲突失败不能把已经发布的“管理来源”留在库里。
        sources = self.runtime.read_all("SELECT source_id FROM sources WHERE owner = ?",
                                        ("management.memory",))
        self.assertEqual(len(sources), 1)

    def test_manual_add_rejects_unknown_subjects_and_empty_text(self):
        for subject in ("user", "entity:someone"):
            with self.subTest(subject=subject), self.assertRaises(ValueError):
                self.add(operation_key="bad-subject-" + subject.replace(":", "-"), subject=subject)
        for field, value in (("predicate", " "), ("object", ""), ("representation", 5)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.add(operation_key="bad-" + field, **{field: value})


if __name__ == "__main__":
    unittest.main()
