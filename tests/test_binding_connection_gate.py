"""Save validation is atomic across selected accounts; no live platforms are contacted."""
from copy import deepcopy
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from tests import astrbot_data_root  # noqa: F401
from companion_v2.execution.models import TaskError
from tests.multi_host_support import MultiFixture


class BindingConnectionGateTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def test_clientless_selected_account_refuses_save_without_stopping_existing_host(self):
        await self.start_host()
        before = deepcopy(self.controller.config)
        save = Mock()
        self.controller.save_plugin_config = save
        self.native.platforms["qq-second"] = NS(
            meta=lambda: NS(name="weixin_oc", id="qq-second"), get_client=lambda: None)
        with self.assertLogs("astrbot_host.multi_controller", level="WARNING") as logs:
            with self.assertRaises(TaskError) as caught:
                await self.controller.apply_bindings(deepcopy(before))
        self.assertEqual(caught.exception.code, "host_account_unavailable")
        self.assertIn("qq-second:101", str(caught.exception))
        self.assertIn("client_present=False", " ".join(logs.output))
        self.assertEqual(self.controller.config, before)
        self.assertTrue(self.controller._accepting)
        self.assertEqual(self.controller.session.application.state, "running")
        save.assert_not_called()

    async def test_unselected_clientless_discovery_does_not_block_other_account_bindings(self):
        await self.start_host()
        self.native.platforms["official-extra"] = NS(
            meta=lambda: NS(name="weixin_oc", id="official-extra"), get_client=lambda: None)
        def save(document):
            self.controller.config.clear()
            self.controller.config.update(deepcopy(document))
        self.controller.save_plugin_config = save
        result = await self.controller.apply_bindings(deepcopy(self.controller.config))
        self.assertTrue(result["saved"], result)
        self.assertTrue(self.controller._accepting)
        self.assertNotIn("official-extra", [a["platform_id"] for a in self.controller.config["multi_host"].get("accounts", [])])
