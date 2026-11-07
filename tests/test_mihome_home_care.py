"""Opt-in household routines: validation, scheduling and one-run-per-day."""

import asyncio
import json
import unittest
from datetime import datetime
from types import SimpleNamespace

from astrbot_host.native_extensions.reality.mihome_web_api import MiHomeWebAPI, WebAPIError


AC_MODEL = "lumi.acpartner.mcn02"
AC_DID = "2004717121"
ALIAS = "客厅空调"


class FakeClient:
    def __init__(self):
        self.calls = []

    async def control_power(self, did, is_on, device_name=""):
        self.calls.append(("control_power", did, is_on))
        return True

    async def set_property(self, did, prop, value, device_name=""):
        self.calls.append(("set_property", did, prop, value))
        return True


def api_for():
    api = object.__new__(MiHomeWebAPI)
    config = {
        "enabled": True,
        "device_map": json.dumps({ALIAS: AC_DID}, ensure_ascii=False),
        "device_category_map": "{}",
        "control_tool": {"enable": True, "admin_only": True, "allowed_devices": [ALIAS]},
        "enable_readonly_tool": True,
        "scene_tool": {"enable": False, "admin_only": True},
        "home_care": {"enable": False, "routines": [], "runs": []},
    }
    api.plugin = SimpleNamespace(mihome_config=config, client=FakeClient(),
                                 data_manager=SimpleNamespace(auth_exists=lambda: True))
    api._operation_lock = asyncio.Lock()
    api._cached_device_rows = lambda: [{"did": AC_DID, "model": AC_MODEL, "name": "远程空调",
                                        "aliases": [ALIAS]}]
    api._mapping_snapshot = lambda: ({ALIAS: AC_DID}, {"无类别": None}, [])
    api._ensure_operation_available = lambda **_kwargs: None

    async def keep():
        return None

    api._persist_config = keep
    return api


def routine(**updates):
    value = {"id": "sleep_light_off", "label": "睡前关灯", "time": "23:30", "days": [0, 1, 2, 3, 4, 5, 6],
             "alias": ALIAS, "action": "power_off", "value": False, "enabled": True}
    value.update(updates)
    return value


class HomeCareTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_rejects_a_device_outside_the_control_whitelist(self):
        api = api_for()
        with self.assertRaises(WebAPIError) as caught:
            await api.extension_home_care_save({"enable": True,
                                                "routines": [routine(alias="客厅灯")]})
        self.assertEqual(caught.exception.status_code, 400)

    async def test_save_keeps_the_plan_and_exposes_it_to_the_page(self):
        api = api_for()
        status = await api.extension_home_care_save({"enable": True, "routines": [routine()]})
        self.assertTrue(status["enable"])
        self.assertEqual(status["routines"][0]["time"], "23:30")
        self.assertEqual(status["devices"][0]["alias"], ALIAS)

    async def test_due_routine_runs_once_per_day(self):
        api = api_for()
        await api.extension_home_care_save({"enable": True, "routines": [routine()]})
        at = datetime(2026, 11, 5, 23, 30)
        first = await api.extension_home_care_tick(now=at)
        second = await api.extension_home_care_tick(now=at)
        self.assertEqual(first["ran"], 1)
        self.assertEqual(second["ran"], 0)
        self.assertEqual(api.plugin.client.calls, [("control_power", AC_DID, False)])
        self.assertEqual(len(api.plugin.mihome_config["home_care"]["runs"]), 1)

    async def test_wrong_time_or_disabled_plan_does_nothing(self):
        api = api_for()
        await api.extension_home_care_save({"enable": True, "routines": [routine(enabled=False)]})
        self.assertEqual((await api.extension_home_care_tick(now=datetime(2026, 11, 5, 23, 30)))["ran"], 0)
        await api.extension_home_care_save({"enable": True, "routines": [routine()]})
        self.assertEqual((await api.extension_home_care_tick(now=datetime(2026, 11, 5, 22, 0)))["ran"], 0)
        self.assertEqual(api.plugin.client.calls, [])

    async def test_temperature_plan_uses_the_profile_property(self):
        api = api_for()
        await api.extension_home_care_save({"enable": True,
                                            "routines": [routine(action="temperature", value=26)]})
        await api.extension_home_care_tick(now=datetime(2026, 11, 5, 23, 30))
        self.assertEqual(api.plugin.client.calls,
                         [("set_property", AC_DID, "target_temperature", 26)])

    async def test_manual_run_records_the_receipt(self):
        api = api_for()
        await api.extension_home_care_save({"enable": False, "routines": [routine()]})
        result = await api.extension_home_care_run("sleep_light_off")
        self.assertTrue(result["result"]["ok"])
        self.assertEqual(result["runs"][-1]["routine_id"], "sleep_light_off")


if __name__ == "__main__":
    unittest.main()
