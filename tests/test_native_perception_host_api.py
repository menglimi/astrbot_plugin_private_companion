"""The V2 perception page edits only its selected private role's owners."""

import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from tests import test_astrbot_staging_host as support

from astrbot.api.web import PluginRequest, bind_request_context
from starlette.requests import Request

from astrbot_host.native_perception_api import NativePerceptionWebAPI, PREFIX
from astrbot_host.management_identity import dashboard_operator
from companion_v2.foundation.refs import ScopeKind
from companion_v2.role_work.state.management import StateManagement
from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.conversation.perception_settings import (PerceptionConfigurationOwner,
    configuration_fields as perception_fields, role_target_for, target_for as perception_window_target)
from companion_v2.relationships import anniversaries
from companion_v2.role_profile.configuration import (
    GROUP as ROLE_PROFILE_GROUP,
    RoleProfileConfigurationOwner,
    configuration_fields as role_profile_fields,
    target_for as role_profile_target_for,
)


class NativePerceptionAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="companion-native-perception-")
        self.addCleanup(temporary.cleanup)
        self.native = support.WebContext()
        types = support.NativeAdapterTypes("4.27.2", support.FakeEvent, support.FakeProvider,
                                           NS, NS, support.FakeReverseApi)
        self.controller = support.AstrBotStagingController(
            self.native, support.host_config(), Path(temporary.name), native_types=types,
            now=lambda: support.NOW)
        self.addAsyncCleanup(self.controller.terminate)
        self.controller.register_routes()
        self.routes = {(path, methods[0]): handler
                       for path, handler, methods, _ in self.native.registered_web_apis}

    async def call(self, *, body=None, username="admin", query=b"", route=PREFIX):
        method = "POST" if body is not None else "GET"
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else b""

        async def receive():
            return {"type": "http.request", "body": payload, "more_body": False}

        request_route = route if method == "GET" else route + "/configure"
        native = Request({"type": "http", "method": method, "path": request_route,
                          "headers": [], "query_string": query}, receive=receive)
        with bind_request_context(PluginRequest(native, username=username)):
            return await self.routes[(request_route, method)]()

    @staticmethod
    def command(section, token, changes, key):
        return {"section": section, "command": {"operation_key": key,
                "edit_token": token, "changes": changes}}

    def test_trace_contexts_include_all_private_windows_with_a_private_selection(self):
        selected = NS(key="private-a:role-a", scope=NS(kind=ScopeKind.PRIVATE),
            actor=NS(subject=NS(subject_id="user-a")), role_binding=NS(role=NS(subject_id="role-a")))
        other = NS(key="private-b:role-b", scope=NS(kind=ScopeKind.PRIVATE))
        host = NS(authority=NS(context=lambda target_id, role_id:
            selected if target_id == "target-a" else other), web_api=NS(target_inventory=lambda: [
                {"scope_kind": "private", "target_id": "target-a", "participant_id": "user-a",
                 "roles": [{"role_id": "role-a", "role_name": "澄溪"}]},
                {"scope_kind": "private", "target_id": "target-b", "participant_id": "user-b",
                 "roles": [{"role_id": "role-b", "role_name": "小溪"}]},
                {"scope_kind": "group", "target_id": "group-c", "roles": [{"role_id": "role-c"}]},
            ]))

        contexts, unavailable = NativePerceptionWebAPI._trace_contexts(
            NS(controller=host), NS(profile=NS(target=selected)))

        self.assertEqual(unavailable, 0)
        self.assertEqual([row[0].key for row in contexts], ["private-a:role-a", "private-b:role-b"])
        self.assertEqual([(row[1], row[2]) for row in contexts], [("user-a", "澄溪"), ("user-b", "小溪")])

    async def test_model_environment_news_and_stale_token(self):
        self.assertEqual((await self.call())["data"]["code"], "staging_not_initialized")
        (await self.controller.initialize_profile()).unwrap()
        initial = await self.call()
        self.assertEqual(initial["status"], "ok", initial)
        data = initial["data"]
        self.assertTrue(data["model"]["settings"]["calendar_enabled"])
        self.assertTrue(data["model"]["settings"]["holiday_enabled"])
        self.assertTrue(data["model"]["settings"]["automatic_solar_terms_enabled"])
        self.assertEqual(data["model"]["scope"], "role")
        self.assertFalse(data["environment"]["settings"]["planning_weather_enabled"])
        self.assertTrue(data["environment"]["sources"]["weather_ready"])
        weather_port = self.controller.session.application.contact_schedule.weather_events.port
        self.assertEqual(data["environment"]["sources"]["weather_provider"], weather_port.provider)
        self.assertTrue(data["news"]["source"]["assembled"])
        self.assertTrue(data["news"]["source"]["ready"])
        self.assertEqual(data["news"]["diagnostic"]["network_status"], "not_checked")
        self.assertEqual(data["contact"]["edit_token"], data["news"]["edit_token"])
        self.assertIn("quiet_start", data["contact"]["settings"])
        self.assertIn("allowed", data["contact"]["effective"])
        self.assertIn("chat", data["contact"]["purposes"])
        materials = await self.call(route=PREFIX + "/materials")
        self.assertEqual(materials["status"], "ok", materials)
        self.assertEqual(materials["data"]["items"], [])
        self.assertEqual(materials["data"]["count"], 0)
        for field in ("web_exploration_enabled", "web_exploration_interests",
                      "web_exploration_min_interval_hours", "web_exploration_result_count",
                      "external_event_self_link_enabled"):
            self.assertIn(field, data["news"]["settings"])

        model = await self.call(body=self.command("model", data["model"]["edit_token"],
            {"guidance": "只说有来源的观察。", "platform_enabled": False}, "model-1"))
        self.assertEqual(model["status"], "ok", model)
        self.assertEqual(model["data"]["status"]["model"]["settings"]["guidance"], "只说有来源的观察。")
        self.assertFalse(model["data"]["status"]["model"]["settings"]["platform_enabled"])

        location = json.dumps({"name":"测试区域", "latitude":12.34, "longitude":56.78,
                               "alert_city":"测试城市", "alert_area":"测试区域"}, ensure_ascii=False)
        environment = model["data"]["status"]["environment"]
        self.assertNotIn("edit_token", environment)
        duplicate_write = await self.call(body=self.command("environment", "obsolete-token",
            {"planning_weather_location":location, "planning_weather_enabled":True}, "environment-1"))
        self.assertEqual(duplicate_write["data"]["code"], "perception_request_invalid")
        app = self.controller.session.application
        context = self.controller.session.profile.target
        operator = dashboard_operator(self.controller.runtime, "admin", at=support.NOW)
        state = StateManagement(app, context)
        state_status = state.status(operator).unwrap()
        state.command(operator, {"action":"configure", "operation_key":"planning-weather-1",
            "edit_token":state_status["edit_token"], "role_edit_token":state_status["role_edit_token"], "changes":{
                "planning_weather_location":location, "planning_weather_enabled":True,
            }}).unwrap()
        updated = (await self.call())["data"]["environment"]["settings"]
        self.assertEqual(updated["planning_weather_location"], location)
        self.assertTrue(updated["planning_weather_enabled"])

        (await self.controller.start()).unwrap()
        latest = (await self.call())["data"]
        news = await self.call(body=self.command("news", latest["news"]["edit_token"],
            {"news_share_enabled":True, "news.enabled":True,
             "news_check_interval_minutes":60}, "news-1"))
        self.assertEqual(news["status"], "ok", news)
        self.assertTrue(news["data"]["status"]["news"]["settings"]["news_share_enabled"])
        self.assertEqual(news["data"]["status"]["news"]["settings"]["news_check_interval_minutes"], 60)
        replay = await self.call(body=self.command("news", latest["news"]["edit_token"],
            {"news_share_enabled":True, "news.enabled":True,
             "news_check_interval_minutes":60}, "news-1"))
        self.assertEqual(replay["status"], "ok", replay)
        self.assertTrue(replay["data"]["result"]["replayed"])

    async def test_perception_role_defaults_are_shared_and_window_overrides_stay_local(self):
        (await self.controller.initialize_profile()).unwrap()
        context = self.controller.session.profile.target
        runtime = self.controller.runtime
        definitions = perception_fields()
        owner = PerceptionConfigurationOwner(runtime)
        role_target = role_target_for(context)
        current_window = perception_window_target(context)
        other_scope = replace(context.scope, conversation_id="other-conversation", session_id="other-session")
        other_window = ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW,
                                    context.role_binding.role, other_scope)

        role_snapshot = owner.read(role_target, definitions)
        owner.write(role_target, definitions, changes={
            "conversation.perception.calendar_timezone": "UTC",
            "conversation.perception.guidance": "按角色语气自然提及日期。",
        }, reset=(), expected_revision=role_snapshot.revision, observed_at=support.NOW)
        local_snapshot = owner.read(current_window, definitions)
        owner.write(current_window, definitions, changes={
            "conversation.perception.calendar_timezone": "Asia/Tokyo",
        }, reset=(), expected_revision=local_snapshot.revision, observed_at=support.NOW)

        local = owner.read(current_window, definitions).fields
        inherited = owner.read(other_window, definitions).fields
        self.assertEqual(local["conversation.perception.calendar_timezone"].value, "Asia/Tokyo")
        self.assertEqual(inherited["conversation.perception.calendar_timezone"].value, "UTC")
        self.assertEqual(inherited["conversation.perception.guidance"].value, "按角色语气自然提及日期。")
        self.assertEqual(inherited["conversation.perception.guidance"].source, role_target)

    async def test_authentication_payload_and_owner_boundaries(self):
        (await self.controller.initialize_profile()).unwrap()
        data = (await self.call())["data"]
        body = self.command("model", data["model"]["edit_token"], {"calendar_enabled":False}, "identity-1")
        for username in (None, "", "api_key:synthetic"):
            self.assertEqual((await self.call(body=body, username=username))["data"]["code"],
                             "dashboard_identity_required")
        self.assertEqual((await self.call(query=b"target_id=another"))["data"]["code"],
                         "perception_request_invalid")
        self.assertEqual((await self.call(route=PREFIX + "/materials",
            query=b"day=2026-10-07"))["data"]["code"], "perception_request_invalid")
        forbidden = self.command("environment", "obsolete-token", {"enabled":True}, "forbidden-1")
        self.assertEqual((await self.call(body=forbidden))["data"]["code"], "perception_request_invalid")
        self.assertTrue((await self.call())["data"]["model"]["settings"]["calendar_enabled"])
        denied_runtime = await self.call(route=PREFIX + "/runtime", username=None)
        self.assertEqual(denied_runtime["data"]["code"], "dashboard_identity_required")
        denied_materials = await self.call(route=PREFIX + "/materials", username=None)
        self.assertEqual(denied_materials["data"]["code"], "dashboard_identity_required")
        await self.controller.terminate()
        self.assertEqual(self.native.registered_web_apis, [])
        self.assertEqual((await self.call())["data"]["code"], "staging_host_terminated")

    async def test_material_pool_combines_shared_items_with_proactive_message_traces(self):
        (await self.controller.initialize_profile()).unwrap()
        events = self.controller.session.application.contact_schedule.news_events
        item = {"id": "role-news-1", "category": "资讯", "title": "角色共享见闻",
                "captured_at": support.NOW.isoformat(), "source": "共享资讯池"}
        trace = {"id": "trace-1", "scope": "window", "category": "主动消息", "title": "早上问候",
                 "captured_at": support.NOW.isoformat(),
                 "source": "主动消息记录", "usage": {"status": "pending"},
                 "trace_target": "user-17", "trace_role": "澄溪"}
        with patch.object(events, "maintain_role", side_effect=RuntimeError("refresh unavailable")) as maintain:
            with patch.object(events, "role_materials", return_value={"state": "ready",
                "checked_at": support.NOW.isoformat(), "failure": None, "items": [item]}) as reader:
                with patch("astrbot_host.native_perception_api.read_proactive_contact_traces",
                           return_value={"items": [trace], "count": 1, "total_count": 4,
                                         "contexts_read": 1}) as trace_reader:
                    with patch("astrbot_host.native_perception_api.ContactManagement.status",
                               side_effect=AssertionError("global materials must not require private settings")):
                        with patch("astrbot_host.native_perception_api.logger.exception") as log_error:
                            response = await self.call(route=PREFIX + "/materials",
                                query=b"day=2026-10-07&timezone=Asia%2FShanghai")

        self.assertEqual(response["status"], "ok", response)
        self.assertEqual([row["scope"] for row in response["data"]["items"]], ["window", "role"])
        self.assertEqual(response["data"]["items"][0]["trace_target"], "user-17")
        self.assertEqual(response["data"]["items"][0]["usage"]["status"], "pending")
        self.assertEqual(response["data"]["shared_count"], 1)
        self.assertEqual(response["data"]["history_count"], 4)
        self.assertEqual(response["data"]["trace_count"], 1)
        self.assertEqual(response["data"]["trace_state"], "ready")
        maintain.assert_called_once()
        trace_reader.assert_called_once()
        self.assertEqual(trace_reader.call_args.kwargs["created_at_start"],
                         support.NOW.replace(year=2026, month=10, day=6, hour=16, minute=0, second=0, microsecond=0))
        self.assertEqual(trace_reader.call_args.kwargs["created_at_end"],
                         support.NOW.replace(year=2026, month=10, day=7, hour=16, minute=0, second=0, microsecond=0))
        log_error.assert_called_once()
        settings = reader.call_args.args[0]
        self.assertIn("news_source_kind", settings)

    async def test_role_material_pool_reports_and_logs_unexpected_read_failures(self):
        (await self.controller.initialize_profile()).unwrap()
        events = self.controller.session.application.contact_schedule.news_events
        with patch.object(events, "maintain_role"):
            with patch.object(events, "role_materials", side_effect=RuntimeError("synthetic failure")):
                with patch("astrbot_host.native_perception_api.logger.exception") as log_error:
                    response = await self.call(route=PREFIX + "/materials")

        self.assertEqual(response["data"]["code"], "material_pool_unavailable")
        log_error.assert_called_once()

    async def test_material_pool_schedules_role_news_warmup_on_running_event_loop(self):
        import asyncio
        from companion_v2.proactive.external_materials import parse_news_response

        (await self.controller.initialize_profile()).unwrap()
        events = self.controller.session.application.contact_schedule.news_events

        class WarmupPort:
            fingerprint = "warmup-news-v1"
            source_label = "warmup.example"

            def __init__(self):
                self.calls = 0

            async def fetch(self):
                self.calls += 1
                return parse_news_response({"items": [{
                    "id": "warmup-1",
                    "title": "巡视预热资讯",
                    "summary": "后台任务在事件循环中成功预热。",
                    "url": "https://warmup.example/1",
                    "published_at": support.NOW.isoformat(),
                    "category": "趣事",
                }]})

        port = WarmupPort()
        loop = asyncio.get_running_loop()
        with (patch.object(events, "_source", return_value=port),
              patch.object(events, "_source_enabled", return_value=True),
              patch("astrbot_host.native_perception_api.logger.exception") as log_error):
            first = await self.call(route=PREFIX + "/materials")
            self.assertEqual(first["status"], "ok", first)
            log_error.assert_not_called()
            workers = tuple(events._maintenance_workers.values())
            self.assertEqual(len(workers), 1)
            self.assertIsInstance(workers[0], asyncio.Task)
            self.assertIs(workers[0].get_loop(), loop)
            await asyncio.gather(*workers)

            second = await self.call(route=PREFIX + "/materials")

        self.assertEqual(port.calls, 1)
        self.assertEqual(second["status"], "ok", second)
        self.assertEqual([item["title"] for item in second["data"]["items"]], ["巡视预热资讯"])

    async def test_runtime_snapshot_uses_enabled_weather_and_returns_explicit_empty_states(self):
        class WeatherPort:
            def __init__(self):
                self.locations = []

            async def fetch(self, location):
                self.locations.append(location)
                return NS(provider="fixture-weather", location=location.name, observed_at=support.NOW,
                          condition="晴", temperature_c=22.0, humidity_pct=45.0, wind_kph=4.0, alerts=())

        (await self.controller.initialize_profile()).unwrap()
        (await self.controller.start()).unwrap()
        app = self.controller.session.application
        weather = WeatherPort()
        app.calendar_planning.weather_port = weather

        path = PREFIX + "/runtime"
        disabled = await self.call(route=path)
        self.assertEqual(disabled["status"], "ok", disabled)
        self.assertEqual(disabled["data"]["weather"]["state"], "disabled")
        self.assertEqual(weather.locations, [])
        self.assertEqual(disabled["data"]["calendar"]["status"], "available")
        self.assertEqual(disabled["data"]["screen"]["state"], "empty")
        # V2 carries these declarations itself; a fresh window has not enabled
        # either capability. That differs from an actually missing provider.
        registry = self.controller.native_extensions
        actions = {item.descriptor.tool.action for item in registry.declarations()}
        self.assertTrue({"reality.snapshot.read", "reality.camera.observe"}.issubset(actions))
        for section in ("reality", "camera"):
            self.assertEqual(disabled["data"][section], {
                "state": "blocked", "reason": "capability_not_installed"})
        with patch.object(registry, "declarations", return_value=()):
            missing = await self.call(route=path)
        self.assertEqual(missing["status"], "ok", missing)
        for section in ("reality", "camera"):
            self.assertEqual(missing["data"][section], {
                "state": "unavailable", "reason": "reality_provider_unavailable"})
        self.assertEqual(missing["data"]["screen"]["state"], "empty")
        self.assertEqual(weather.locations, [])

        settings = (await self.call())["data"]["environment"]["settings"]
        self.assertFalse(settings["planning_weather_enabled"])
        location = json.dumps({"name": "测试区域", "latitude": 12.34, "longitude": 56.78}, ensure_ascii=False)
        app = self.controller.session.application
        context = self.controller.session.profile.target
        operator = dashboard_operator(self.controller.runtime, "admin", at=support.NOW)
        state = StateManagement(app, context)
        state_status = state.status(operator).unwrap()
        state.command(operator, {"action": "configure", "operation_key": "runtime-weather",
            "edit_token": state_status["edit_token"], "role_edit_token": state_status["role_edit_token"], "changes": {
                "planning_weather_location": location, "planning_weather_enabled": True,
            }}).unwrap()
        configured = (await self.call())["data"]["environment"]["settings"]
        self.assertTrue(configured["planning_weather_enabled"])
        self.assertEqual(configured["planning_weather_location"], location)
        runtime = await self.call(route=path)
        self.assertEqual(runtime["status"], "ok", runtime)
        self.assertEqual(runtime["data"]["weather"]["state"], "available")
        self.assertEqual(runtime["data"]["weather"]["temperature_c"], 22.0)
        self.assertEqual(runtime["data"]["calendar"]["status"], "available")
        self.assertEqual(weather.locations[0].name, "测试区域")

        # Role weather must not read private contact, device or conversation snapshots.
        with patch.object(NativePerceptionWebAPI, "_status", side_effect=AssertionError("window read")):
            role_weather = await self.call(route=path, query=b"scope=role")
        self.assertEqual(role_weather["status"], "ok", role_weather)
        self.assertEqual(set(role_weather["data"]), {"weather", "calendar", "observed_at"})
        self.assertEqual(role_weather["data"]["weather"]["temperature_c"], 22.0)
        self.assertEqual(role_weather["data"]["weather"]["humidity_pct"], 45.0)
        self.assertEqual(role_weather["data"]["calendar"]["status"], "available")
        for query in (b"scope=other", b"scope=role&scope=window", b"scope=role&target_id=other"):
            invalid = await self.call(route=path, query=query)
            self.assertEqual(invalid["data"]["code"], "perception_request_invalid")
        denied = await self.call(route=path, query=b"scope=role", username=None)
        self.assertEqual(denied["data"]["code"], "dashboard_identity_required")

        owner = RoleProfileConfigurationOwner(app.runtime)
        target = role_profile_target_for(context)
        fields = role_profile_fields()
        snapshot = owner.read(target, fields)
        owner.write(target, fields, changes={ROLE_PROFILE_GROUP + ".birthday": "9月23日"},
                    reset=(), expected_revision=snapshot.revision, observed_at=support.NOW)
        anniversaries.remember(app.runtime, context, {
            "label": "第一次一起看星星", "reason": "用户原话明确说这是第一次一起看星星。",
            "confidence": 0.9, "evidence_handle": "s1",
            "occurred_at": support.NOW.isoformat(), "month": 9, "day": 23,
            "timezone": "Asia/Shanghai",
        }, source_run_id="perception-host-test", at=support.NOW)
        status = (await self.call())["data"]["contact"]
        dates = json.loads(status["dates"])
        self.assertEqual(status["lookaheadDays"], 7)
        self.assertTrue(any(row["label"] == "角色生日" and row["days_until"] == 2
                            and row["basis"] == "profile_role_birthday" for row in dates))
        self.assertTrue(any(row["label"] == "第一次一起看星星" and row["days_until"] == 2
                            and row["basis"] == "auto_relationship_anniversary" for row in dates))
