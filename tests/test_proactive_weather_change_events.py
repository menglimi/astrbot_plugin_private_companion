"""Fresh role-location changes through the existing private contact path."""

from datetime import timedelta
import json
import unittest

from companion_v2.conversation.foreground import BackgroundPromptTrigger
from companion_v2.agenda.planning_config import WeatherLocation
from companion_v2.proactive.external_materials import WeatherSnapshot
from companion_v2.proactive.weather_change_events import SOURCE_OWNER, significant_change
from companion_v2.role_work.state.management import StateManagement
from tests.contact_schedule_support import ContactScheduleFixture
from tests.test_planning_weather import LOCATION_JSON
from tests.test_role_tool_loop import answered


class WeatherChangeContactTests(ContactScheduleFixture, unittest.IsolatedAsyncioTestCase):
    async def setup_change(self, *, enabled=True, location=LOCATION_JSON):
        host, batch = await self.scheduled()
        app = host.application
        context = self.context(app, batch)
        manager = StateManagement(app, context)
        if location:
            before = manager.status(self.core).unwrap()
            manager.command(self.core, dict(action="configure", operation_key="weather-change-location",
                edit_token=before["edit_token"], role_edit_token=before["role_edit_token"],
                changes={"planning_weather_location": location})).unwrap()
        self.settings(app, {"chat.enabled": False, "weather_change_enabled": enabled}).unwrap()
        return app, context, manager

    def snapshot(self, location, *, code, condition, temperature=22, wind=12, age=10, provider="open-meteo"):
        return WeatherSnapshot(provider, location.name, self.clock.now() - timedelta(minutes=age),
                               temperature, condition, 75, wind, (), code)

    async def sampled(self, app, port, count):
        await self.until(lambda: port.calls >= count and not app.contact_schedule.active_handles)
        self.assertEqual(app.state, "running", app.failure_code)

    async def test_opt_in_and_location_are_required(self):
        app, _, _ = await self.setup_change(enabled=False, location="")

        class WeatherPort:
            calls = 0

            async def fetch(self, location):
                self.calls += 1
                raise AssertionError("disabled sampling")

        port = WeatherPort()
        app.contact_schedule.weather_change_events.port = port
        self.advance(app, 60)
        app.contact_schedule.advance()
        self.assertEqual(port.calls, 0)
        self.settings(app, {"weather_change_enabled": True}).unwrap()
        app.contact_schedule.advance()
        self.assertEqual(port.calls, 0)

    async def test_baseline_then_significant_change_sends_once_across_restart(self):
        app, context, _ = await self.setup_change()
        test = self

        class WeatherPort:
            calls = 0

            async def fetch(self, location):
                self.calls += 1
                return test.snapshot(location, code=0 if self.calls == 1 else 61,
                                     condition="晴" if self.calls == 1 else "小雨", provider="qweather")

        port = WeatherPort()
        app.contact_schedule.weather_change_events.port = port
        self.advance(app, 60)
        await self.sampled(app, port, 1)
        self.assertEqual(self.contacts(app), ())
        baseline = app.runtime.read_one("SELECT * FROM proactive_weather_samples WHERE origin_key=?", (context.key,))
        self.assertIsNone(baseline["event_json"])

        self.advance(app, 3600)
        await self.automatic_delivery(app)
        self.assertEqual(port.calls, 2)
        self.assertEqual(len(self.contact_calls()), 1)
        request = self.contact_calls()[0].original
        materials = [json.loads(item.payload) for item in request.context.materials if item.owner == SOURCE_OWNER]
        self.assertEqual(len(materials), 1)
        self.assertEqual(materials[0]["event"], "role_location_weather_change")
        self.assertEqual(materials[0]["provider"], "qweather")
        self.assertEqual(materials[0]["previous"]["weather_code"], 0)
        self.assertEqual(materials[0]["current"]["provider"], "qweather")
        self.assertEqual(materials[0]["current"]["weather_code"], 61)
        self.assertNotIn("_receipt", materials[0])
        self.assertIn("不称灾害、预警或趋势", request.instruction)
        self.assertEqual(json.loads(self.contacts(app)[0]["record_json"])["purpose"], "environment")

        await app.close()
        restarted = (await self.start(self.host(runtime=self.open_runtime()), 1, 1)).application
        restarted.contact_schedule.weather_change_events.port = port
        restarted.contact_schedule.advance()
        self.assertEqual(port.calls, 2)
        self.assertEqual(len(self.contact_calls()), 1)

    async def test_small_change_and_stale_sample_do_not_contact(self):
        app, context, _ = await self.setup_change()
        test = self

        class WeatherPort:
            calls = 0

            async def fetch(self, location):
                self.calls += 1
                if self.calls == 3:
                    return test.snapshot(location, code=61, condition="小雨", age=180)
                return test.snapshot(location, code=0 if self.calls == 1 else 2,
                                     condition="晴" if self.calls == 1 else "局部多云", temperature=22)

        port = WeatherPort()
        app.contact_schedule.weather_change_events.port = port
        self.advance(app, 60)
        await self.sampled(app, port, 1)
        self.advance(app, 3600)
        await self.sampled(app, port, 2)
        self.assertEqual(self.contacts(app), ())
        self.advance(app, 3600)
        await self.sampled(app, port, 3)
        self.assertEqual(self.contacts(app), ())
        row = app.runtime.read_one("SELECT * FROM proactive_weather_samples WHERE origin_key=?", (context.key,))
        self.assertEqual(json.loads(row["sample_json"])["weather_code"], 2)

    async def test_location_change_starts_a_new_baseline(self):
        app, context, manager = await self.setup_change()
        new_location = WeatherLocation("新城市", 39.9, 116.4)
        new_location_json = json.dumps({"name": new_location.name,
            "latitude": new_location.latitude, "longitude": new_location.longitude}, ensure_ascii=False)
        test = self

        class WeatherPort:
            calls = 0

            async def fetch(self, location):
                self.calls += 1
                test.assertEqual(location.name, "角色所在城市" if self.calls == 1 else "新城市")
                codes = (0, 61, 71)
                conditions = ("晴", "小雨", "小雪")
                index = self.calls - 1
                return test.snapshot(location, code=codes[index], condition=conditions[index])

        port = WeatherPort()
        app.contact_schedule.weather_change_events.port = port
        self.advance(app, 60)
        await self.sampled(app, port, 1)
        before = manager.status(self.core).unwrap()
        manager.command(self.core, dict(action="configure", operation_key="weather-change-new-location",
            edit_token=before["edit_token"], role_edit_token=before["role_edit_token"],
            changes={"planning_weather_location": new_location_json})).unwrap()

        self.advance(app, 3600)
        await self.sampled(app, port, 2)
        self.assertEqual(self.contacts(app), ())
        row = app.runtime.read_one("SELECT * FROM proactive_weather_samples WHERE origin_key=?", (context.key,))
        self.assertEqual(json.loads(row["sample_json"])["role_location"], new_location.name)
        self.assertIsNone(row["event_json"])

        self.advance(app, 3600)
        await self.automatic_delivery(app)
        self.assertEqual(port.calls, 3)
        request = self.contact_calls()[0].original
        material = next(json.loads(item.payload) for item in request.context.materials
                        if item.owner == SOURCE_OWNER)
        self.assertEqual(material["previous"]["role_location"], new_location.name)
        self.assertEqual(material["previous"]["weather_code"], 61)
        self.assertEqual(material["current"]["weather_code"], 71)

    async def test_disabling_during_generation_cancels_old_change(self):
        app, _, _ = await self.setup_change()
        test = self

        class WeatherPort:
            calls = 0

            async def fetch(self, location):
                self.calls += 1
                return test.snapshot(location, code=0 if self.calls == 1 else 61,
                                     condition="晴" if self.calls == 1 else "小雨")

        port = WeatherPort()
        app.contact_schedule.weather_change_events.port = port
        self.advance(app, 60)
        await self.sampled(app, port, 1)

        async def disable(envelope):
            if isinstance(envelope.original.event, BackgroundPromptTrigger):
                self.settings(app, {"weather_change_enabled": False}).unwrap()
                return answered('{"decision":"send","text":"这里下雨了"}')
            return answered("收到。")

        self.model_behavior = disable
        self.advance(app, 3600)
        await self.until(lambda: len(self.contact_calls()) == 1)
        await self.until(lambda: not app.proactive.active_handles)
        self.assertEqual(self.contacts(app), ())

    def test_only_clear_transitions_or_large_measurement_changes_count(self):
        previous = {"weather_code": 0, "temperature_c": 20, "wind_kph": 8}
        self.assertIsNone(significant_change(previous, {"weather_code": 2, "temperature_c": 21, "wind_kph": 10}))
        self.assertEqual(significant_change(previous, {"weather_code": 61, "temperature_c": 20, "wind_kph": 8}),
                         "weather_type_changed")
        self.assertEqual(significant_change(previous, {"weather_code": 0, "temperature_c": 27, "wind_kph": 8}),
                         "temperature_shift")
        self.assertEqual(significant_change(previous, {"weather_code": 0, "temperature_c": 20, "wind_kph": 36}),
                         "wind_strengthened")
