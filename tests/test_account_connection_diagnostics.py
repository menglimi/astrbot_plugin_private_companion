"""Read-only connection hints; discovery must never masquerade as a saved mapping."""
from copy import deepcopy
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from tests import astrbot_data_root  # noqa: F401
from astrbot_host.account_session_api import AccountSessionWebAPI
from astrbot_host.setup_api import SetupWebAPI


class AccountConnectionDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        constructor = patch.object(SetupWebAPI, "__init__", return_value=None)
        constructor.start()
        self.addCleanup(constructor.stop)

    async def test_discovery_distinguishes_running_clientless_adapter_from_ready_client(self):
        client = NS(is_shutting_down=False, is_closed=lambda: False)
        native = NS(meta=lambda: NS(id="native-test", name="qq_official"),
                    get_client=lambda: client)
        clientless = NS(meta=lambda: NS(id="official-test", name="weixin_oc"), get_client=lambda: None)
        platforms = {"native-test": native, "official-test": clientless}
        controller = NS(context=NS(get_platform_inst=platforms.get), config={})
        rows = [{"platform_id": p, "account_id": p, "bot_id": "bot-" + p}
                for p in ("native-test", "official-test", "missing-test")]
        original = {"accounts": rows, "personas": [], "targets": []}
        saved = deepcopy(original)
        with patch.object(SetupWebAPI, "api_targets", AsyncMock(return_value={"status": "ok", "data": original})):
            result = await AccountSessionWebAPI(controller)._discovered()
        states = {row["platform_id"]: row["connection_status"] for row in result["accounts"]}
        self.assertEqual(states, {"native-test": "ready", "official-test": "client_unavailable",
                                  "missing-test": "platform_unavailable"})
        self.assertEqual(original, saved)
        self.assertEqual(controller.config, {})

    async def test_shutting_down_or_closed_client_remains_unavailable(self):
        for shutting_down, closed in ((True, False), (False, True)):
            with self.subTest(shutting_down=shutting_down, closed=closed):
                client = NS(is_shutting_down=shutting_down, is_closed=lambda: closed)
                platform = NS(meta=lambda: NS(id="client-test", name="qq_official"),
                              get_client=lambda: client)
                controller = NS(context=NS(get_platform_inst=lambda _: platform))
                body = {"accounts": [{"platform_id": "client-test", "account_id": "client-test"}], "targets": []}
                with patch.object(SetupWebAPI, "api_targets", AsyncMock(return_value={"status": "ok", "data": body})):
                    result = await AccountSessionWebAPI(controller)._discovered()
                self.assertEqual(result["accounts"][0]["connection_status"], "account_unavailable")

    async def test_failed_inspection_exposes_no_exception_text_and_never_changes_configuration(self):
        def fail(_):
            raise RuntimeError("synthetic credential must not reach the page")
        controller = NS(context=NS(get_platform_inst=fail), config={"synthetic": True})
        body = {"accounts": [{"platform_id": "native-test", "account_id": "native-test"}]}
        with patch.object(SetupWebAPI, "api_targets", AsyncMock(return_value={"status": "ok", "data": body})):
            result = await AccountSessionWebAPI(controller)._discovered()
        self.assertEqual(result["accounts"][0]["connection_status"], "unknown")
        self.assertNotIn("credential", str(result))
        self.assertEqual(controller.config, {"synthetic": True})
