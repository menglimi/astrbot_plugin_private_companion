"""Group model routes expose the global Laya settings without inventing group logs."""

from tests import astrbot_data_root  # noqa: F401 - isolate the SDK before import

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from astrbot_host.group_model_api import GroupModelWebAPI
from companion_v2.foundation.outcome import Outcome


class GroupLayaAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_group_routes_read_and_save_global_settings(self):
        with TemporaryDirectory() as directory:
            controller = NS(data_root=Path(directory), _control_lock=asyncio.Lock())
            api = GroupModelWebAPI(controller, "group", "role")
            api._prepare = AsyncMock(return_value=("admin", NS(authorize=lambda _operator: Outcome.success(True)), "role"))
            routes = {path: methods for path, _, methods, _ in api.routes("/group/model")}
            self.assertEqual(routes["/group/model/laya"], ["GET"])
            self.assertEqual(routes["/group/model/laya/save"], ["POST"])

            with patch("astrbot_host.group_model_api.request", NS(query={})):
                status = await api.laya_status()
                logs = await api.laya_logs()
            self.assertEqual(status["status"], "ok")
            self.assertEqual(logs["data"], {"events": [], "available": False})

            body = json.dumps({"revision": status["data"]["revision"],
                               "changes": {"followup_confidence_threshold": 40}}).encode("utf-8")
            with patch("astrbot_host.group_model_api.request", NS(query={}, body=AsyncMock(return_value=body))):
                saved = await api.laya_save()
            self.assertEqual(saved["status"], "ok")
            self.assertEqual(saved["data"]["followup_confidence_threshold"], 40)
            self.assertEqual(api._prepare.await_count, 3)

    async def test_group_laya_rejects_an_operator_without_model_management_access(self):
        with TemporaryDirectory() as directory:
            controller = NS(data_root=Path(directory), _control_lock=asyncio.Lock())
            api = GroupModelWebAPI(controller, "group", "role")
            api._prepare = AsyncMock(return_value=("viewer", NS(authorize=lambda _operator:
                Outcome.failed("core_access_required", "administrator required")), "role"))
            with patch("astrbot_host.group_model_api.request", NS(query={})):
                result = await api.laya_status()
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["data"]["code"], "core_access_required")


if __name__ == "__main__":
    unittest.main()
