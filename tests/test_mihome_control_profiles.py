"""Mi Home control follows the reference plugin: profile-declared names only."""

import asyncio
import json
import unittest
from types import SimpleNamespace

from astrbot_host.native_extensions.reality.mihome_web_api import MiHomeWebAPI, WebAPIError


AC_MODEL = "lumi.acpartner.mcn02"
AC_DID = "2004717121"


class FakeClient:
    def __init__(self):
        self.calls = []

    async def set_property(self, did, prop, value, device_name=""):
        self.calls.append(("set_property", did, prop, value))
        return True

    async def run_action(self, did, action, device_name=""):
        self.calls.append(("run_action", did, action))
        return True


def api_for(*, admin_only=False, allowed=("客厅空调",)):
    api = object.__new__(MiHomeWebAPI)
    config = {
        "enabled": True,
        "device_map": json.dumps({"客厅空调": AC_DID}, ensure_ascii=False),
        "device_category_map": "{}",
        "control_tool": {"enable": True, "admin_only": admin_only, "allowed_devices": list(allowed)},
        "enable_readonly_tool": True,
        "scene_tool": {"enable": False, "admin_only": True},
    }
    api.plugin = SimpleNamespace(mihome_config=config, client=FakeClient(),
                                 data_manager=SimpleNamespace(auth_exists=lambda: True))
    api._operation_lock = asyncio.Lock()
    api._cached_device_rows = lambda: [
        {"did": AC_DID, "model": AC_MODEL, "name": "远程空调", "aliases": ["客厅空调"]}]
    api._mapping_snapshot = lambda: ({"客厅空调": AC_DID}, {}, [])
    api._ensure_operation_available = lambda **_kwargs: None
    return api


async def _keep_config():
    return None


class MiHomeControlProfileTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_derives_missing_aliases_from_cloud_names(self):
        api = api_for()
        api._mapping_snapshot = lambda: ({}, {}, [])
        api._persist_config = _keep_config
        changed = await api._auto_assign_aliases([
            {"did": "1", "name": "卧室灯开关"},
            {"did": "2", "name": "排插"},
            {"did": "3", "name": "排插"},
        ])
        self.assertTrue(changed)
        mapping = json.loads(api.plugin.mihome_config["device_map"])
        self.assertEqual(mapping, {"卧室灯开关": "1", "排插": "2", "排插 2": "3"})

        api._mapping_snapshot = lambda: (mapping, {}, [])
        self.assertFalse(await api._auto_assign_aliases([{"did": "1", "name": "改过的名字"}]),
                         "已有别名的设备不能被自动改名")

    async def test_sync_fills_the_profile_category_and_the_power_whitelist(self):
        api = api_for()
        api._mapping_snapshot = lambda: ({}, {}, [])
        api._persist_config = _keep_config
        await api._auto_assign_aliases([{"did": AC_DID, "name": "远程空调", "model": AC_MODEL}])
        config = api.plugin.mihome_config
        self.assertEqual(json.loads(config["device_category_map"]), {"远程空调": "空调类别"})
        self.assertIn("远程空调", config["control_tool"]["allowed_devices"])
        self.assertTrue(config["control_tool"]["enable"])

    async def test_ir_devices_are_never_auto_allowlisted(self):
        api = api_for()
        api._mapping_snapshot = lambda: ({}, {}, [])
        api._persist_config = _keep_config
        await api._auto_assign_aliases([{"did": "ir.1234", "name": "客厅电视", "model": "miir.tv"}])
        self.assertNotIn("客厅电视", api.plugin.mihome_config["control_tool"]["allowed_devices"])

    async def test_inspect_exposes_the_profile_chinese_names(self):
        profile = api_for().extension_device_profile("客厅空调")
        self.assertEqual(profile["model"], AC_MODEL)
        self.assertTrue(profile["direct_control_supported"])
        names = [item["name"] for item in profile["writable_properties"]]
        self.assertIn("温度", names)
        self.assertIn("模式", names)
        self.assertIn("开关", names)
        self.assertIn("target_temperature", [item["key"] for item in profile["writable_properties"]])
        self.assertTrue(any(item["name"] for item in profile["readable_properties"]))

    async def test_a_chinese_property_name_folds_into_the_cloud_key(self):
        api = api_for()
        receipt = await api.extension_device_action("客厅空调", "温度", "26")
        self.assertEqual(receipt["property"], "target_temperature")
        self.assertEqual(receipt["value"], 26)
        self.assertTrue(receipt["confirmed"])
        self.assertEqual(api._client.calls, [("set_property", AC_DID, "target_temperature", 26)])

    async def test_a_chinese_value_folds_through_the_value_map(self):
        api = api_for()
        await api.extension_device_action("客厅空调", "模式", "制冷")
        self.assertEqual(api._client.calls, [("set_property", AC_DID, "mode", 1)])

    async def test_a_name_the_profile_never_declared_is_refused(self):
        api = api_for()
        with self.assertRaises(WebAPIError) as caught:
            await api.extension_device_action("客厅空调", "起飞", "现在")
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(api._client.calls, [], "被拒绝的名称不能触达设备")

    async def test_admin_only_refuses_the_native_model_call(self):
        api = api_for(admin_only=True)
        with self.assertRaises(WebAPIError) as caught:
            await api.extension_device_action("客厅空调", "温度", "26", native=True)
        self.assertEqual(caught.exception.status_code, 403)
        self.assertIn("核心管理员", str(caught.exception.message))
        self.assertEqual(api._client.calls, [])

    async def test_a_core_administrator_window_may_still_control(self):
        api = api_for(admin_only=True)
        api._is_core_admin = lambda actor: (actor or {}).get("subject_id") == "admin-1"
        receipt = await api.extension_device_action(
            "客厅空调", "温度", "26", native=True, actor={"subject_id": "admin-1", "kind": "human"})
        self.assertTrue(receipt["confirmed"])
        self.assertEqual(api._client.calls, [("set_property", AC_DID, "target_temperature", 26)])

    async def test_an_unmapped_alias_cannot_be_inspected(self):
        api = api_for()
        with self.assertRaises(WebAPIError) as caught:
            api.extension_device_profile("不存在的设备")
        self.assertEqual(caught.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
