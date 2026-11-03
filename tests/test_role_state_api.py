"""Authenticated management and host lifecycle of the pinned state domain."""

import asyncio
import json
import unittest
from datetime import timedelta

from tests import test_role_growth_api as support
from astrbot.api.web import PluginRequest, bind_request_context
from starlette.requests import Request
from astrbot_host.state_api import PREFIX
from astrbot_host.management_identity import dashboard_operator
from companion_v2.identity_policy.models import AuthorizationTier, GrantState
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.proactive.external_materials import WeatherSnapshot
from companion_v2.role_work.state.configuration import GROUP, role_target_for
from tests.test_management_access import NOW
from tests.test_role_state import state_data, calendar_data
from tests.native_adapter_support import completion


class StateAPITests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = support.GrowthManagementHostTests.asyncSetUp
    initialize = support.GrowthManagementHostTests.initialize

    async def request(self, *, username="admin", data=None, raw=None, query=""):
        body = raw if raw is not None else json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else b""
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}
        path = PREFIX + ("/command" if data is not None or raw is not None else "")
        native = Request({"type": "http", "method": "POST" if path.endswith("command") else "GET", "path": path,
            "headers": [(b"content-type", b"application/json")], "query_string": query.encode("utf-8")}, receive=receive)
        with bind_request_context(PluginRequest(native, username=username)):
            return await self.routes[path]()

    async def weather(self, *, username="admin"):
        path = PREFIX + "/weather"
        async def receive():
            return {"type":"http.request", "body":b"", "more_body":False}
        native = Request({"type":"http", "method":"GET", "path":path,
            "headers":[], "query_string":b""}, receive=receive)
        with bind_request_context(PluginRequest(native, username=username)):
            return await self.routes[path]()

    async def test_admin_can_read_state_after_participant_reply_is_revoked(self):
        session = await self.initialize()
        identity = session.application.tasks.identity
        grants = [grant for grant in identity.snapshot().grants
                  if grant.capability == "conversation.reply" and grant.subject == self.target.actor.subject
                  and grant.scope == self.target.scope and grant.state is GrantState.ACTIVE]
        self.assertTrue(grants)
        for grant in grants:
            identity.revoke_grant(grant.grant_id, revoked_at=NOW,
                                  expected_revision=identity.revision)
        self.assertEqual(session.application.tasks.check_context_access(
            self.target, action="conversation.reply", observed_at=NOW).failure.code,
            "authorization_revoked")
        status = await self.request()
        self.assertEqual(status["status"], "ok", status)
        self.assertIn("calendar", status["data"])
        self.assertIn("state", status["data"])
        state = status["data"]["state"]
        write = await self.request(data=dict(action="state.write", operation_key="revoked-participant-write",
            view_token=state["view_token"], expected_revision=state["revision"], data=state_data()))
        self.assertEqual(write["data"]["code"], "authorization_revoked", write)
        self.assertEqual(session.application.tasks.check_context_access(
            self.target, action="conversation.reply", observed_at=NOW).failure.code,
            "authorization_revoked")
        self.assertEqual((await self.request(username=None))["data"]["code"],
                         "dashboard_identity_required")

    async def test_weather_reads_only_the_configured_role_location_and_reports_live_snapshot(self):
        session = await self.initialize()
        calls = []
        location = {"name":"角色所在城市", "latitude":31.23, "longitude":121.47}

        class WeatherPort:
            async def fetch(self, requested):
                calls.append(requested)
                return WeatherSnapshot("open-meteo", requested.name, NOW - timedelta(minutes=8),
                                       22.4, "小雨", 75, 12, ())

        session.application.calendar_planning.weather_port = WeatherPort()
        disabled = await self.weather()
        self.assertEqual((disabled["status"], disabled["data"]["state"]), ("ok", "disabled"))
        self.assertEqual(calls, [])

        before = (await self.request())["data"]
        configured = await self.request(data={"action":"configure_role", "operation_key":"weather-enabled",
            "role_edit_token":before["role_edit_token"], "changes":{"planning_weather_enabled":True,
                "planning_weather_location":json.dumps(location, ensure_ascii=False)}})
        self.assertEqual(configured["status"], "ok", configured)
        current = await self.weather()
        self.assertEqual(current["status"], "ok", current)
        self.assertEqual(current["data"]["state"], "live")
        self.assertEqual(current["data"]["location"], location["name"])
        self.assertEqual(current["data"]["condition"], "小雨")
        self.assertEqual(current["data"]["temperature_c"], 22.4)
        self.assertEqual([(item.name, item.latitude, item.longitude) for item in calls],
               [(location["name"], location["latitude"], location["longitude"])])
        self.assertEqual((await self.weather())["data"], current["data"])
        self.assertEqual(len(calls), 1, "repeated page reads reuse the observation")
        self.assertEqual((await self.weather(username=None))["data"]["code"], "dashboard_identity_required")
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])

    async def test_role_defaults_apply_across_windows_and_mixed_settings_save_atomically(self):
        session = await self.initialize()
        app = session.application
        before = (await self.request())["data"]
        self.assertIsInstance(before.get("role_edit_token"), str)

        role_saved = await self.request(data=dict(action="configure_role", operation_key="role-default",
            role_edit_token=before["role_edit_token"], changes={"maintenance_minutes":45}))
        self.assertEqual(role_saved["status"], "ok", role_saved)
        role_status = (await self.request())["data"]
        self.assertEqual(role_status["settings"]["maintenance_minutes"], 45)
        self.assertEqual(app.role_state.settings(session.profile.target)[0]["maintenance_minutes"], 45)

        local_saved = await self.request(data=dict(action="configure_window", operation_key="local-platform-flag",
            edit_token=role_status["edit_token"],
            changes={"maintenance_minutes":20, "typing_enabled":True}))
        self.assertEqual(local_saved["status"], "ok", local_saved)
        self.assertEqual(app.role_state.settings(session.profile.target)[0]["maintenance_minutes"], 20)
        self.assertTrue(app.role_state.settings(session.profile.target)[0]["typing_enabled"])
        role_target = role_target_for(session.profile.target)
        operator = dashboard_operator(app.runtime, "admin", at=NOW)
        role_snapshot = app.configuration.read(operator, target=role_target, group_id=GROUP,
            observed_at=NOW).unwrap()
        self.assertEqual(role_snapshot.values[GROUP + ".maintenance_minutes"], 45)
        self.assertTrue((await self.request(data={"action":"configure_window", "operation_key":"local-platform-flag",
            "edit_token":role_status["edit_token"],
            "changes":{"maintenance_minutes":20, "typing_enabled":True}}))["data"]["replayed"])

    async def test_configuration_and_human_edit_with_cas_and_idempotency(self):
        session = await self.initialize()
        status = await self.request()
        self.assertEqual(status["status"], "ok", status)
        self.assertEqual(status["data"]["presentation_diagnostics"]["port"], "installed")
        self.assertIn("sleep_schedule", status["data"])
        self.assertTrue(status["data"]["sleep_schedule"]["shared"])
        self.assertEqual(status["data"]["sleep_schedule"]["owner"]["role_id"], self.target.role_binding.role.subject_id)
        self.assertFalse(status["data"]["sleep_schedule"]["enabled"])
        body = dict(action="configure", operation_key="enable", edit_token=status["data"]["edit_token"],
                    role_edit_token=status["data"]["role_edit_token"],
                    changes={"enabled": True, "calendar_enabled": True, "typing_enabled": True})
        changed = await self.request(data=body)
        self.assertEqual(changed["status"], "ok", changed)
        replayed = await self.request(data=body)
        self.assertEqual(replayed["status"], "ok", replayed)
        self.assertTrue(replayed["data"]["replayed"])
        diagnostic = (await self.request())["data"]["presentation_diagnostics"]["typing"]
        self.assertEqual((diagnostic["state"], diagnostic["reason"]), ("paused", "host_not_running"))
        current = (await self.request())["data"]["state"]
        body = dict(action="state.write", operation_key="edit", view_token=current["view_token"],
                    expected_revision=0, data=state_data(hunger="有些饿", sleep="还没准备睡"))
        self.assertEqual((await self.request(data=body))["status"], "ok")
        self.assertTrue((await self.request(data=body))["data"]["replayed"])
        self.assertEqual((await self.request(data=body | {"operation_key": "stale"}))["data"]["code"], "record_revision_conflict")
        self.assertEqual((await self.request())["data"]["state"]["values"]["hunger"], "有些饿")
        self.assertIsNotNone(session.application.presence)
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])

    async def test_calendar_human_acceptance_and_new_view_after_revision(self):
        await self.initialize()
        before = (await self.request())["data"]
        body = dict(action="calendar.create", operation_key="appointment", id="appointment", expected_revision=0,
            view_token=before["create_token"], data=calendar_data(kind="commitment"))
        created = await self.request(data=body)
        self.assertEqual(created["status"], "ok", created)
        self.assertEqual(created["data"]["state"], "proposed")
        daily = (await self.request())["data"]["occurrences"]["items"]
        self.assertTrue(any(item["id"] == "appointment" for item in daily),
                        "当天日程应由主页状态接口返回")
        current = (await self.request())["data"]["calendar"]["items"][0]
        accepted = await self.request(data=dict(action="calendar.accept", operation_key="accept", id="appointment",
            expected_revision=current["revision"], view_token=current["view_token"]))
        self.assertEqual(accepted["data"]["state"], "accepted", accepted)
        stored = (await self.request())["data"]["calendar"]["items"][0]
        self.assertNotEqual(stored["accepted_by"][0]["subject_id"], self.target.actor.subject.subject_id)

    async def test_calendar_public_wording_uses_original_version_and_exact_fields(self):
        await self.initialize()
        before = (await self.request())["data"]
        created = await self.request(data=dict(action="calendar.create", operation_key="plan", id="plan", expected_revision=0,
            view_token=before["create_token"], data=calendar_data()))
        self.assertEqual(created["status"], "ok", created)
        item = (await self.request())["data"]["calendar"]["items"][0]
        command = dict(action="calendar.presence", operation_key="wording", id="plan", expected_revision=item["revision"],
                       view_token=item["view_token"], data={"presence": "散步中"})
        for extra in ({"sources": []}, {"target_id": "other"}, {"operator": "other"}):
            self.assertEqual((await self.request(data=command | extra))["data"]["code"], "state_command_invalid")
        self.assertEqual((await self.request(username=None, data=command))["data"]["code"], "dashboard_identity_required")
        self.assertEqual((await self.request(data=command))["status"], "ok")
        self.assertTrue((await self.request(data=command))["data"]["replayed"])
        current = (await self.request())["data"]["calendar"]["items"][0]
        self.assertEqual((current["presence"], current["title"], current["revision"]), ("散步中", item["title"], 2))
        self.assertEqual((await self.request(data=command | {"operation_key": "stale"}))["data"]["code"], "record_revision_conflict")
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])

    async def test_no_login_forged_target_malformed_json_or_noncore_can_mutate(self):
        self.assertEqual((await self.request(username=None))["data"]["code"], "dashboard_identity_required")
        self.assertEqual((await self.request())["data"]["code"], "staging_not_initialized")
        await self.initialize()
        before = (await self.request())["data"]
        body = dict(action="configure", operation_key="enable", edit_token=before["edit_token"],
                    role_edit_token=before["role_edit_token"], changes={"enabled": True})
        for extra in ({"context": {}}, {"operator": "admin"}, {"role_id": "someone"}):
            self.assertEqual((await self.request(data=body | extra))["data"]["code"], "state_command_invalid")
        for raw in (b'[]', b'\xff', b'{"action":"maintain","action":"configure"}', b'{"action":[]}'):
            self.assertEqual((await self.request(raw=raw))["data"]["code"], "state_command_invalid")
        for query in ("limit=0", "limit=1&limit=2", "operator=admin"):
            self.assertEqual((await self.request(query=query))["data"]["code"], "state_query_invalid")
        denied = await self.request(data=body | {"changes": {"typing_enabled": "true"}})
        self.assertEqual(denied["data"]["code"], "state_configuration_invalid")
        operator = dashboard_operator(self.controller.runtime, "admin", at=NOW)
        policy = ObjectAccessPolicy(self.controller.runtime)
        policy.set_tier(operator.subject, AuthorizationTier.STANDARD, expected_revision=policy.revision, observed_at=NOW)
        self.assertIn((await self.request(data=body))["data"]["code"], ("core_access_required", "authorization_denied"))

    async def test_native_startup_runs_state_profile_and_manual_request_uses_same_scheduler(self):
        session = await self.initialize()
        async def respond(payload, tools):
            return completion(json.dumps(state_data(hunger="有点饿", presence="休息中"), ensure_ascii=False))
        self.native.provider.client.state.behavior = respond
        status = (await self.request())["data"]
        self.assertEqual((await self.request(data=dict(action="configure", operation_key="enabled",
            role_edit_token=status["role_edit_token"],
            edit_token=status["edit_token"], changes={"enabled": True, "typing_enabled": True, "presence_enabled": True})))["status"], "ok")
        started = await self.controller.start()
        self.assertTrue(started.is_success, started.failure)
        async def revision(value):
            async def wait():
                while True:
                    row = session.application.role_state.records.get(self.target, "current")
                    if row and row["revision"] == value:
                        return
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(wait(), 5)
        await revision(1)
        current = (await self.request())["data"]["state"]
        body = dict(action="maintain", operation_key="manual", view_token=current["view_token"], expected_revision=1)
        result = await self.request(data=body)
        self.assertEqual(result["data"]["state"], "scheduled", result)
        await revision(2)
        self.assertTrue((await self.request(data=body))["data"]["replayed"])
        self.assertEqual(len(self.native.provider.client.state.calls), 2)
        self.assertEqual(len(self.native.platform.client._api._wsr_api.calls), 1)
        action, parameters = self.native.platform.client._api._wsr_api.calls[0]
        self.assertEqual(action, "set_diy_online_status")
        self.assertEqual(parameters["wording"], "休息中")
        self.assertIsNotNone(session.application.presence)

    async def test_operator_view_token_and_shutdown_routes(self):
        await self.initialize()
        state = (await self.request())["data"]["state"]
        body = dict(action="state.write", operation_key="edit", view_token=state["view_token"],
                    expected_revision=0, data=state_data())
        self.assertEqual((await self.request(username="other-admin", data=body))["data"]["code"], "state_view_stale")
        self.assertEqual((await self.request(username="api_key:123"))["data"]["code"], "dashboard_identity_required")
        await self.controller.terminate()
        self.assertEqual(self.native.registered_web_apis, [])
        self.assertEqual((await self.request())["data"]["code"], "staging_host_terminated")

    async def test_cycle_auto_release_and_feeling_contract_use_versioned_api(self):
        await self.initialize()
        status = (await self.request())["data"]
        self.assertEqual(status["settings"]["cycle_initialization"], "natural")
        configured = await self.request(data=dict(action="configure", operation_key="initialize",
            role_edit_token=status["role_edit_token"],
            edit_token=status["edit_token"], changes={"enabled": True, "cycle_enabled": True,
                "cycle_initialization": "unknown", "cycle_discomfort_enabled": True}))
        self.assertEqual(configured["status"], "ok", configured)
        async def command(action, key, data):
            current = (await self.request())["data"]["state"]
            return dict(action=action, operation_key=key, expected_revision=current["revision"],
                        view_token=current["view_token"], data=data)
        aligned = await self.request(data=await command("cycle.align", "not-applicable",
            {"applicability": "not_applicable", "day": None}))
        self.assertEqual(aligned["status"], "ok", aligned)
        assertion = {"applicability": "applicable", "description": "有点腰酸",
                     "feeling": {"episode_id": None, "trend": "continuing"}}
        self.assertEqual((await self.request(data=await command("state.write", "ordinary", state_data(cycle=assertion))))["status"], "ok")
        self.assertEqual((await self.request())["data"]["state"]["cycle_timeline"]["applicability"], "not_applicable")
        automatic = await command("cycle.align", "automatic", {"applicability": "auto", "day": None})
        self.assertEqual((await self.request(data=automatic))["status"], "ok")
        self.assertEqual((await self.request(data=await command("state.write", "after-release", state_data(cycle=assertion))))["status"], "ok")
        current = (await self.request())["data"]["state"]
        self.assertEqual(current["cycle_timeline"]["applicability"], "applicable")
        self.assertEqual(current["cycle_timeline"]["initialization"], "unknown")
        self.assertNotIn("cycle_day", current["cycle_timeline"])
        self.assertEqual(current["cycle_feeling"]["trend"], "continuing")
        self.assertTrue((await self.request(data=automatic))["data"]["replayed"])
        self.assertEqual((await self.request(data=automatic | {"operation_key": "stale"}))["data"]["code"], "record_revision_conflict")

    async def test_cycle_variation_is_optional_and_adopted_through_versioned_state_api(self):
        session = await self.initialize()
        before = (await self.request())["data"]
        self.assertFalse(before["settings"]["cycle_variation_enabled"])
        configured = await self.request(data=dict(action="configure", operation_key="vary",
            role_edit_token=before["role_edit_token"],
            edit_token=before["edit_token"], changes={"enabled": True, "cycle_enabled": True,
                "cycle_initialization": "first_day", "cycle_variation_enabled": True}))
        self.assertEqual(configured["status"], "ok", configured)
        current = (await self.request())["data"]["state"]
        command = dict(action="state.write", operation_key="varied-state", expected_revision=current["revision"],
            view_token=current["view_token"], data=state_data(cycle={"applicability": "applicable", "description": None}))
        saved = await self.request(data=command)
        self.assertEqual(saved["status"], "ok", saved)
        self.assertTrue((await self.request(data=command))["data"]["replayed"])
        active = (await self.request())["data"]
        timeline = active["state"]["cycle_timeline"]
        self.assertTrue(timeline["variation_enabled"])
        self.assertEqual(sum(p["days"] for p in timeline["phase_plan"]), timeline["cycle_days"])
        self.assertNotIn("variation_seed", timeline)
        invalid = await self.request(data=dict(action="configure", operation_key="invalid-variation",
            edit_token=active["edit_token"], changes={"cycle_variation_enabled": "true"}))
        self.assertEqual(invalid["data"]["code"], "state_configuration_invalid")
        disabled = await self.request(data=dict(action="configure", operation_key="fixed-next-round",
            role_edit_token=active["role_edit_token"],
            edit_token=active["edit_token"], changes={"cycle_variation_enabled": False}))
        self.assertEqual(disabled["status"], "ok", disabled)
        current = (await self.request())["data"]["state"]
        self.assertEqual(current["cycle_timeline"], timeline)
        adopted = await self.request(data=command | {"operation_key": "save-next-round",
            "expected_revision": current["revision"], "view_token": current["view_token"]})
        self.assertEqual(adopted["status"], "ok", adopted)
        next_state = (await self.request())["data"]["state"]["cycle_timeline"]
        self.assertEqual(next_state["phase_plan"], timeline["phase_plan"])
        self.assertFalse(next_state["pending_plan"]["variation_enabled"])
        self.assertIsNotNone(session.application.presence)
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])

    async def test_cycle_configuration_and_explicit_alignment_use_pinned_target(self):
        session = await self.initialize()
        before = (await self.request())["data"]
        configured = await self.request(data=dict(action="configure", operation_key="cycle-settings",
            role_edit_token=before["role_edit_token"],
            edit_token=before["edit_token"], changes={"enabled": True, "cycle_enabled": True,
                "cycle_mode": "six_phase", "cycle_menstrual_days": 4, "cycle_discomfort_enabled": True,
                "cycle_expression_prompt": "自然表达，不反复播报。"}))
        self.assertEqual(configured["status"], "ok", configured)
        state = (await self.request())["data"]["state"]
        command = dict(action="cycle.align", operation_key="cycle-day", expected_revision=state["revision"],
            view_token=state["view_token"], data={"applicability": "applicable", "day": 14})
        self.assertEqual((await self.request(data=command | {"role_id": "other"}))["data"]["code"], "state_command_invalid")
        aligned = await self.request(data=command)
        self.assertEqual(aligned["status"], "ok", aligned)
        self.assertTrue((await self.request(data=command))["data"]["replayed"])
        result = (await self.request())["data"]
        timeline = result["state"]["cycle_timeline"]
        self.assertEqual((timeline["phase"], timeline["cycle_day"], timeline["cycle_days"]), ("ovulation", 14, 27))
        self.assertTrue(timeline["discomfort_enabled"])
        self.assertEqual(timeline["expression_prompt"], "自然表达，不反复播报。")
        disabled = await self.request(data=dict(action="configure", operation_key="cycle-off",
            role_edit_token=result["role_edit_token"],
            edit_token=result["edit_token"], changes={"cycle_enabled": False}))
        self.assertEqual(disabled["status"], "ok", disabled)
        self.assertNotIn("cycle_timeline", (await self.request())["data"]["state"])
        self.assertIsNotNone(session.application.presence)
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])

    async def test_cycle_alignment_guards_settings_and_replays_after_later_configuration(self):
        await self.initialize()
        before = (await self.request())["data"]
        configured = await self.request(data=dict(action="configure", operation_key="enable-guarded-cycle",
            role_edit_token=before["role_edit_token"],
            edit_token=before["edit_token"], changes={"enabled": True, "cycle_enabled": True}))
        self.assertEqual(configured["status"], "ok", configured)
        before = (await self.request())["data"]
        saved = await self.request(data=dict(action="state.write", operation_key="body-before-alignment",
            expected_revision=before["state"]["revision"], view_token=before["state"]["view_token"],
            data=state_data(hunger="有些饿", health="精神不错")))
        self.assertEqual(saved["status"], "ok", saved)
        before = (await self.request())["data"]
        command = dict(action="cycle.align", operation_key="guarded-day", expected_revision=before["state"]["revision"],
            view_token=before["state"]["view_token"], settings_token=before["cycle_settings_token"],
            data={"applicability": "applicable", "day": 4})
        local = await self.request(data=dict(action="configure", operation_key="unrelated-local-settings",
            edit_token=before["edit_token"], role_edit_token=before["role_edit_token"],
            changes={"typing_refresh_seconds": 5, "maintenance_minutes": 45}))
        self.assertEqual(local["status"], "ok", local)
        before = (await self.request())["data"]
        self.assertEqual(before["cycle_settings_token"], command["settings_token"])
        changed = await self.request(data=dict(action="configure_role", operation_key="concurrent-cycle-length",
            role_edit_token=before["role_edit_token"], changes={"cycle_menstrual_days": 6}))
        self.assertEqual(changed["status"], "ok", changed)
        denied = await self.request(data=command)
        self.assertEqual(denied["data"]["code"], "role_cycle_settings_conflict", denied)
        current = (await self.request())["data"]
        self.assertEqual(current["state"]["revision"], before["state"]["revision"])
        command["settings_token"] = current["cycle_settings_token"]
        aligned = await self.request(data=command)
        self.assertEqual(aligned["status"], "ok", aligned)
        current = (await self.request())["data"]
        self.assertEqual(current["state"]["values"]["hunger"], "有些饿")
        self.assertEqual(current["state"]["cycle_timeline"]["cycle_day"], 4)
        # Later settings can hide the original feelings without changing the
        # parameters used to recover the already-completed calibration receipt.
        changed = await self.request(data=dict(action="configure_role", operation_key="hide-hunger-after-alignment",
            role_edit_token=current["role_edit_token"], changes={"hunger_enabled": False, "cycle_timezone": "UTC"}))
        self.assertEqual(changed["status"], "ok", changed)
        replayed = await self.request(data=command)
        self.assertEqual(replayed["status"], "ok", replayed)
        self.assertTrue(replayed["data"]["replayed"])
        self.assertEqual(replayed["data"]["operation_id"], aligned["data"]["operation_id"])
        self.assertEqual((await self.request())["data"]["state"]["revision"], current["state"]["revision"])
        modified = await self.request(data=command | {"settings_token": "another-token"})
        self.assertEqual(modified["data"]["code"], "idempotency_key_conflict", modified)
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])

    async def test_cycle_alignment_rejects_invalid_settings_token_without_writing(self):
        await self.initialize()
        before = (await self.request())["data"]
        for token in (None, 1, {}, []):
            result = await self.request(data=dict(action="cycle.align", operation_key="invalid-token",
                expected_revision=before["state"]["revision"], view_token=before["state"]["view_token"],
                settings_token=token, data={"applicability": "unknown", "day": None}))
            self.assertEqual(result["data"]["code"], "state_command_invalid", result)
        self.assertEqual((await self.request())["data"]["state"]["revision"], before["state"]["revision"])


if __name__ == "__main__":
    unittest.main()
