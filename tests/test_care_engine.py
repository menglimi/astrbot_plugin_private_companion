"""Care concerns: impulses are observed, never executed in phase 1."""

import asyncio
import json
import unittest
from datetime import datetime
from types import SimpleNamespace

from astrbot_host.native_extensions.reality.mihome_web_api import MiHomeWebAPI
from companion_v2.proactive.care import DeviceObservation, appraise, normalize_settings


ALIAS = "卧室灯开关"
AC_ALIAS = "远程空调"
DID = "1205290545"
AC_DID = "2004717121"


class CareEngineTests(unittest.TestCase):
    def setUp(self):
        self.settings = normalize_settings({"enable": True})

    def observation(self, **state):
        return DeviceObservation(alias=ALIAS, category="灯", online=True, state=state)

    def test_light_left_on_at_night_becomes_a_held_impulse(self):
        impulses = appraise(self.settings, [self.observation(on=True)], datetime(2026, 11, 5, 23, 40))
        self.assertEqual(len(impulses), 1)
        self.assertEqual(impulses[0]["concern_id"], "sleep_light")
        self.assertEqual(impulses[0]["verdict"], "held")
        self.assertEqual(impulses[0]["action"], "power_off")
        self.assertIn("灯", impulses[0]["title"])

    def test_daytime_or_already_off_produces_nothing(self):
        self.assertEqual(appraise(self.settings, [self.observation(on=True)],
                                  datetime(2026, 11, 5, 15, 0)), [])
        self.assertEqual(appraise(self.settings, [self.observation(on=False)],
                                  datetime(2026, 11, 5, 23, 40)), [])

    def test_low_night_temperature_becomes_a_held_impulse(self):
        observation = DeviceObservation(alias=AC_ALIAS, category="空调类别", online=True,
                                        state={"on": True, "target_temperature": 22})
        impulses = appraise(self.settings, [observation], datetime(2026, 11, 5, 0, 20))
        self.assertEqual(len(impulses), 1)
        self.assertEqual(impulses[0]["action"], "target_temperature")
        self.assertEqual(impulses[0]["verdict"], "held")

    def test_dry_air_is_noticed_without_an_action(self):
        observation = DeviceObservation(alias="温湿度计", category="温湿度计类别", online=True,
                                        state={"humidity": 31})
        impulses = appraise(self.settings, [observation], datetime(2026, 11, 5, 15, 0))
        self.assertEqual(len(impulses), 1)
        self.assertEqual(impulses[0]["concern_id"], "humidity_low")
        self.assertEqual(impulses[0]["action"], "")
        self.assertEqual(appraise(self.settings, [DeviceObservation(
            alias="温湿度计", category="温湿度计类别", online=True, state={"humidity": 48})],
            datetime(2026, 11, 5, 15, 0)), [])

    def test_disabled_concern_stays_silent(self):
        settings = normalize_settings({"enable": True,
                                       "concerns": {"sleep_light": {"enabled": False}}})
        self.assertEqual(appraise(settings, [self.observation(on=True)],
                                  datetime(2026, 11, 5, 23, 40)), [])

    def test_master_switch_off_produces_nothing(self):
        self.assertEqual(appraise(normalize_settings({}), [self.observation(on=True)],
                                  datetime(2026, 11, 5, 23, 40)), [])

    def test_actions_default_to_low_risk_only_and_are_bounded(self):
        settings = normalize_settings({"enable": True})
        self.assertTrue(settings["actions"]["sleep_light"]["enabled"])
        self.assertFalse(settings["actions"]["ac_temperature"]["enabled"])
        custom = normalize_settings({"enable": True, "actions": {
            "ac_temperature": {"enabled": True, "daily_cap": 99, "patience_minutes": 1}}})
        self.assertTrue(custom["actions"]["ac_temperature"]["enabled"])
        self.assertEqual(custom["actions"]["ac_temperature"]["daily_cap"], 5)
        self.assertEqual(custom["actions"]["ac_temperature"]["patience_minutes"], 15)

    def test_legacy_single_action_switch_migrates_to_the_light_action(self):
        migrated = normalize_settings({"enable": True, "action_enabled": False})
        self.assertFalse(migrated["actions"]["sleep_light"]["enabled"])
        self.assertFalse(migrated["actions"]["ac_temperature"]["enabled"])


class CareApiTests(unittest.IsolatedAsyncioTestCase):
    def api_for(self):
        api = object.__new__(MiHomeWebAPI)
        config = {
            "enabled": True,
            "device_map": json.dumps({ALIAS: DID, AC_ALIAS: AC_DID}, ensure_ascii=False),
            "device_category_map": json.dumps({ALIAS: "灯", AC_ALIAS: "空调类别"}, ensure_ascii=False),
            "control_tool": {"enable": True, "admin_only": True,
                             "allowed_devices": [ALIAS, AC_ALIAS]},
            "enable_readonly_tool": True,
            "care": {"enable": True},
        }
        api.plugin = SimpleNamespace(mihome_config=config)
        api._operation_lock = asyncio.Lock()
        api._cached_device_rows = lambda: [
            {"did": DID, "name": ALIAS, "aliases": [ALIAS]},
            {"did": AC_DID, "name": AC_ALIAS, "aliases": [AC_ALIAS]},
        ]
        api._mapping_snapshot = lambda: ({ALIAS: DID, AC_ALIAS: AC_DID},
                                         {ALIAS: "灯", AC_ALIAS: "空调类别"}, [])
        api._ensure_operation_available = lambda **_kwargs: None

        async def status(alias):
            if alias == ALIAS:
                return {"available": True, "properties": {"on": True}}
            return {"available": True, "properties": {"on": True, "target_temperature": 22}}

        api.extension_read_device_status = status

        async def keep():
            return None

        api._persist_config = keep
        return api

    async def test_tick_records_impulses_without_execution(self):
        api = self.api_for()
        result = await api.extension_care_tick(now=datetime(2026, 11, 5, 23, 40))
        self.assertGreaterEqual(len(result["impulses"]), 1)
        stored = api.plugin.mihome_config["care"]["impulses"]
        self.assertTrue(stored)
        self.assertTrue(all(item["verdict"] == "held" for item in stored))

    async def test_second_tick_does_not_duplicate_the_same_impulse(self):
        api = self.api_for()
        await api.extension_care_tick(now=datetime(2026, 11, 5, 23, 40))
        second = await api.extension_care_tick(now=datetime(2026, 11, 5, 23, 45))
        self.assertEqual(second["impulses"], [])

    async def test_feedback_can_pause_and_resume_a_concern(self):
        api = self.api_for()
        paused = await api.extension_care_feedback("sleep_light", "pause")
        self.assertFalse(paused["concerns"]["sleep_light"]["enabled"])
        resumed = await api.extension_care_feedback("sleep_light", "allow")
        self.assertTrue(resumed["concerns"]["sleep_light"]["enabled"])

    async def test_status_exposes_the_concerns_and_devices(self):
        api = self.api_for()
        status = api.extension_care_status()
        self.assertTrue(status["enable"])
        self.assertEqual(len(status["devices"]), 2)
        self.assertTrue(any(item["id"] == "sleep_light" for item in status["concerns_meta"]))

    async def test_action_waits_then_turns_the_light_off_once(self):
        api = self.api_for()
        api.plugin.mihome_config["care"] = {"enable": True, "actions": {
            "sleep_light": {"enabled": True, "daily_cap": 1, "patience_minutes": 15}}}
        calls = []

        async def capture(alias, is_on, *, native=False, actor=None):
            calls.append((alias, is_on))
            return {"alias": alias, "is_on": is_on, "confirmed": True}

        api.extension_control_power = capture
        first = await api.extension_care_tick(now=datetime(2026, 11, 5, 23, 0))
        self.assertTrue(first["impulses"])
        self.assertEqual(first["impulses"][0]["verdict"], "held")
        second = await api.extension_care_tick(now=datetime(2026, 11, 5, 23, 20))
        self.assertTrue(any(item["verdict"] == "acted" for item in second["impulses"]))
        self.assertEqual(calls, [(ALIAS, False)])
        self.assertEqual(api.plugin.mihome_config["care"]["counters"]["by_action"]["sleep_light"], 1)

    async def test_daily_cap_stops_a_second_action(self):
        api = self.api_for()
        api.plugin.mihome_config["care"] = {"enable": True, "actions": {
            "sleep_light": {"enabled": True, "daily_cap": 1, "patience_minutes": 15}}}
        calls = []

        async def capture(alias, is_on, *, native=False, actor=None):
            calls.append((alias, is_on))
            return {"alias": alias, "is_on": is_on, "confirmed": True}

        api.extension_control_power = capture
        await api.extension_care_tick(now=datetime(2026, 11, 5, 23, 0))
        await api.extension_care_tick(now=datetime(2026, 11, 5, 23, 20))
        third = await api.extension_care_tick(now=datetime(2026, 11, 5, 23, 40))
        self.assertTrue(all(item["verdict"] == "held" for item in third["impulses"]))
        self.assertEqual(len(calls), 1)

    async def test_praise_shortens_patience_and_pause_stops_actions(self):
        api = self.api_for()
        api.plugin.mihome_config["care"] = {"enable": True, "actions": {
            "sleep_light": {"enabled": True, "daily_cap": 1, "patience_minutes": 30}}}
        praised = await api.extension_care_feedback("sleep_light", "praise")
        self.assertEqual(praised["actions"]["sleep_light"]["patience_minutes"], 25)
        self.assertEqual(praised["learning"]["sleep_light"]["praise"], 1)
        self.assertEqual(praised["learning"]["sleep_light"]["trust"], 1)
        paused = await api.extension_care_feedback("sleep_light", "pause")
        self.assertFalse(paused["concerns"]["sleep_light"]["enabled"])
        self.assertFalse(paused["actions"]["sleep_light"]["enabled"])
        self.assertEqual(paused["learning"]["sleep_light"]["trust"], 0)

    async def test_medium_risk_temperature_needs_separate_authorization(self):
        api = self.api_for()
        calls = []

        async def capture(alias, action, value, *, native=False, actor=None, binding_revision=""):
            calls.append((alias, action, value))
            return {"alias": alias, "confirmed": True}

        api.extension_device_action = capture
        api.plugin.mihome_config["care"] = {
            "enable": True,
            "concerns": {"sleep_light": {"enabled": False}},
            "actions": {"sleep_light": {"enabled": False},
                        "ac_temperature": {"enabled": False}},
        }
        await api.extension_care_tick(now=datetime(2026, 11, 5, 23, 0))
        await api.extension_care_tick(now=datetime(2026, 11, 5, 23, 50))
        self.assertEqual(calls, [])
        api.plugin.mihome_config["care"]["actions"]["ac_temperature"] = {
            "enabled": True, "daily_cap": 1, "patience_minutes": 15}
        await api.extension_care_tick(now=datetime(2026, 11, 6, 23, 0))
        await api.extension_care_tick(now=datetime(2026, 11, 6, 23, 20))
        self.assertEqual(calls, [(AC_ALIAS, "target_temperature", "24")])


if __name__ == "__main__":
    unittest.main()
