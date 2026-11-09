from types import SimpleNamespace
import unittest
from unittest.mock import patch

from companion_v2.conversation.external_tools import ExternalToolExecutor
from companion_v2.execution.models import TaskError
from companion_v2.foundation.refs import ScopeKind


class GroupRealityToolGateTests(unittest.TestCase):
    def setUp(self):
        self.executor = object.__new__(ExternalToolExecutor)
        self.executor.runtime = SimpleNamespace()
        self.context = SimpleNamespace(scope=SimpleNamespace(kind=ScopeKind.GROUP),
                                       actor=SimpleNamespace(subject=object()))

    def test_group_device_tool_is_mounted_but_rejects_non_core(self):
        with patch("companion_v2.conversation.external_tools.is_core", return_value=False):
            with self.assertRaises(TaskError) as raised:
                self.executor._require_group_admin("reality.device.control", self.context)
        self.assertEqual(raised.exception.code, "reality_admin_only")

    def test_group_device_tool_allows_core_admin(self):
        with patch("companion_v2.conversation.external_tools.is_core", return_value=True):
            self.executor._require_group_admin("reality.device.control", self.context)

    def test_private_device_tool_is_not_gated_here(self):
        private = SimpleNamespace(scope=SimpleNamespace(kind=ScopeKind.PRIVATE),
                                  actor=SimpleNamespace(subject=object()))
        self.executor._require_group_admin("reality.device.control", private)


if __name__ == "__main__":
    unittest.main()
