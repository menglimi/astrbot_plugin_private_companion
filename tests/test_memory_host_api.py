import asyncio
import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests import test_astrbot_staging_host as support
from astrbot.api.web import PluginRequest, bind_request_context
from starlette.requests import Request
from astrbot_host.memory_api import PREFIX
from companion_v2.execution.models import encode, stamp
from companion_v2.memory.models import Proposal, owner_key
from companion_v2.memory.management import MemoryManagement
from companion_v2.adapters.astrbot.native import persona_instruction
from companion_v2.conversation.native_observations import NativeConversationObservations
from companion_v2.identity_policy.models import AuthorizationTier
from astrbot_host.management_identity import dashboard_operator
from tests.native_adapter_support import FakeEvent, FakeProvider, FakeReverseApi, completion
from tests.test_management_access import NOW


class MemoryHostAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = TemporaryDirectory(prefix="companion-memory-api-")
        self.addCleanup(temporary.cleanup)
        self.native = support.WebContext()
        types = support.NativeAdapterTypes("4.27.2", FakeEvent, FakeProvider, support.NS, support.NS, FakeReverseApi)
        self.controller = support.AstrBotStagingController(self.native, support.host_config(),
            Path(temporary.name) / "profiles", native_types=types, now=lambda: NOW)
        self.addAsyncCleanup(self.controller.terminate)
        self.controller.register_routes()
        self.routes = {(route, methods[0]): handler for route, handler, methods, _ in self.native.registered_web_apis}

    async def request(self, path="", *, username="admin", data=None, raw=None, query=""):
        body = raw if raw is not None else json.dumps(data).encode("utf-8") if data is not None else b""
        method = "POST" if body else "GET"
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}
        req = Request({"type": "http", "method": method, "path": PREFIX + path, "headers": [],
                       "query_string": query.encode("utf-8")}, receive=receive)
        with bind_request_context(PluginRequest(req, username=username)):
            return await self.routes[(PREFIX + path, method)]()

    async def initialize(self):
        (await self.controller.initialize_profile()).unwrap()
        session = self.controller.session
        target = session.profile.target
        captured = (await session.bridge.capture(FakeEvent(self.native))).unwrap()
        event = session.host.inbound.ingest(captured, capability="text").unwrap()
        manager = MemoryManagement(session.application.memory, dashboard_operator(self.controller.runtime, "admin", at=NOW))
        evidence = manager.writer.access.capture(target, event.source)
        manager.writer.submit(target, Proposal("add", predicate="测试事实", object="原值", representation="实际管理员提供的测试记录。",
            attribution="third_party", evidence=(evidence,)), operation_key="seed").unwrap()
        return session

    async def test_authentication_and_host_target_are_required(self):
        self.assertEqual((await self.request(username=None))["data"]["code"], "dashboard_identity_required")
        self.assertEqual((await self.request())["data"]["code"], "staging_not_initialized")
        await self.initialize()
        listed = (await self.request())["data"]
        self.assertNotEqual(listed["operator_id"], listed["subject_id"])
        item = listed["items"][0]
        args = dict(atom_id=item["atom_id"], expected_revision=item["revision"], view_token=item["view_token"],
                    operation_key="change", action="correct", value={"object": "新值", "representation": "管理员已更正。"})
        for extra in ({"operator": "admin"}, {"context": {}}, {"sources": []}, {"subject_id": "other"}):
            self.assertEqual((await self.request("/command", data=args | extra))["data"]["code"], "memory_request_invalid")
        changed = await self.request("/command", data=args)
        self.assertEqual(changed["status"], "ok", changed)
        self.assertEqual(await self.request("/command", data=args), changed)
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])

    async def test_overview_returns_private_counts_without_memory_content(self):
        await self.initialize()
        denied = await self.request("/overview", username=None)
        self.assertEqual(denied["data"]["code"], "dashboard_identity_required")

        result = await self.request("/overview")
        self.assertEqual(result["status"], "ok", result)
        overview = result["data"]["local"]
        self.assertEqual(overview["facts"]["active"], 1)
        self.assertGreaterEqual(overview["sources"]["available_messages"], 1)
        self.assertEqual(overview["reflections"]["total"], 0)
        self.assertEqual(result["data"]["linkage"]["state"], "not_owner")
        self.assertIsNone(result["data"]["linkage"]["counts"])
        self.assertNotIn("实际管理员提供的测试记录", json.dumps(result, ensure_ascii=False))

    async def test_fact_detail_and_source_reads_are_bound_to_the_authorized_query_context(self):
        await self.initialize()
        listed = (await self.request())["data"]["items"][0]
        denied = await self.request("/detail", username=None,
            data={"atom_id": listed["atom_id"], "revision": listed["revision"]})
        self.assertEqual(denied["data"]["code"], "dashboard_identity_required")

        detail = await self.request("/detail", data={"atom_id": listed["atom_id"], "revision": listed["revision"]})
        self.assertEqual(detail["status"], "ok", detail)
        self.assertEqual(detail["data"]["item"]["representation"], "实际管理员提供的测试记录。")
        self.assertTrue(detail["data"]["sources"])
        self.assertNotIn("evidence", detail["data"]["item"])
        self.assertNotIn("source", detail["data"]["item"])

        source = detail["data"]["sources"][0]
        arguments = {"session_id": detail["data"]["session_id"], "handle": source["handle"]}
        read = await self.request("/source", data=arguments)
        self.assertEqual(read["status"], "ok", read)
        self.assertEqual(read["data"]["text"], "你好")

        other_query = await self.request("/query", data={"query": "测试事实", "kind": "facts"})
        self.assertEqual(other_query["status"], "ok", other_query)
        foreign_session = await self.request("/source", data={
            "session_id": other_query["data"]["session_id"], "handle": source["handle"]})
        self.assertEqual(foreign_session["data"]["code"], "memory_source_unavailable")

        for extra in ({"subject_id": "another-user"}, {"scope": {}}, {"sources": []}):
            invalid = await self.request("/detail", data={"atom_id": listed["atom_id"],
                "revision": listed["revision"]} | extra)
            self.assertEqual(invalid["data"]["code"], "memory_request_invalid")
        forged = await self.request("/source", data=arguments | {"subject_id": "another-user"})
        self.assertEqual(forged["data"]["code"], "memory_request_invalid")

    async def test_memory_query_rejects_scope_overrides_and_requires_dashboard_identity(self):
        await self.initialize()
        denied = await self.request("/query", username=None, data={"query": "咖啡", "kind": "all"})
        self.assertEqual(denied["data"]["code"], "dashboard_identity_required")
        invalid = await self.request("/query", data={"query": "咖啡", "kind": "all", "subject_id": "another-user"})
        self.assertEqual(invalid["data"]["code"], "memory_request_invalid")
        invalid_kind = await self.request("/query", data={"query": "咖啡", "kind": "group"})
        self.assertEqual(invalid_kind["data"]["code"], "memory_request_invalid")

        result = await self.request("/query", data={"query": "测试事实", "kind": "facts"})
        self.assertEqual(result["status"], "ok", result)
        self.assertTrue(result["data"]["session_id"].startswith("memory-page-query-"))
        self.assertIn(result["data"]["index_state"], ("ready", "catching_up", "ranking"))
        self.assertEqual(result["data"]["scope_coverage"], "captured_authorized_sources_only")
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])

    async def test_settings_write_uses_shared_configuration_and_duplicate_keys_are_rejected(self):
        session = await self.initialize()
        settings = (await self.request("/settings"))["data"]
        changed = await self.request("/settings/command", data={"operation_key": "off", "expected_edit_token": settings["edit_token"],
            "changes": {"memory.enabled": False}})
        self.assertEqual(changed["status"], "ok", changed)
        self.assertFalse(session.application.memory.settings.policy(session.profile.target).enabled)
        self.assertEqual((await self.request())["status"], "ok")
        duplicate = await self.request("/settings/command", raw=b'{"changes":{},"changes":{}}')
        self.assertEqual(duplicate["data"]["code"], "memory_request_invalid")
        self.assertEqual((await self.request(query="limit=1&limit=2"))["data"]["code"], "memory_request_invalid")

    async def test_memory_policy_defaults_to_role_and_keeps_explicit_window_overrides(self):
        session = await self.initialize()
        app, context = session.application, session.profile.target
        status = (await self.request("/settings"))["data"]
        self.assertEqual(status["scope"], "role")
        self.assertEqual(status["layers"][-1], "role")
        saved = await self.request("/settings/command", data={
            "operation_key": "role-memory-policy", "expected_edit_token": status["edit_token"],
            "changes": {"memory.enabled": False},
        })
        self.assertEqual(saved["status"], "ok", saved)
        other_scope = replace(context.scope, native_scope_id="another-private-user",
            conversation_id="another-conversation", session_id="another-session")
        other_window = replace(context,
            role_binding=replace(context.role_binding, scope=other_scope),
            audience=replace(context.audience, scope=other_scope))
        self.assertFalse(app.memory.settings.policy(context).enabled)
        self.assertFalse(app.memory.settings.policy(other_window).enabled)

        local = (await self.request("/settings", query="layer=window"))["data"]
        self.assertEqual(local["scope"], "window")
        override = await self.request("/settings/command", data={
            "operation_key": "window-memory-policy", "expected_edit_token": local["edit_token"],
            "layer": "window", "changes": {"memory.enabled": True},
        })
        self.assertEqual(override["status"], "ok", override)
        self.assertTrue(app.memory.settings.policy(context).enabled)
        self.assertFalse(app.memory.settings.policy(other_window).enabled)

    async def test_route_cleanup_after_stop_and_restart_does_not_leave_old_handlers(self):
        await self.initialize()
        self.assertTrue(any(path.startswith(PREFIX) for path, _ in self.routes))
        (await self.controller.stop()).unwrap()
        self.assertEqual((await self.request())["status"], "ok")
        await self.controller.terminate()
        self.assertEqual(self.native.registered_web_apis, [])
        self.assertEqual((await self.request())["data"]["code"], "staging_host_terminated")

    async def test_pin_history_and_previewed_fact_clear_share_the_authenticated_target(self):
        await self.initialize()
        item = (await self.request())["data"]["items"][0]
        args = dict(atom_id=item["atom_id"], expected_revision=item["revision"],
                    view_token=item["view_token"], operation_key="pin", action="pin")
        self.assertEqual((await self.request("/command", data=args))["status"], "ok")
        history = await self.request("/revisions", query="atom_id=" + item["atom_id"])
        self.assertEqual(history["status"], "ok", history)
        self.assertEqual(len(history["data"]["items"]), 2)
        self.assertEqual(history["data"]["items"][-1]["context_mode"], "pinned")
        self.assertEqual((await self.request("/clear/preview", username=None))["data"]["code"],
                         "dashboard_identity_required")
        preview = (await self.request("/clear/preview"))["data"]
        self.assertEqual(preview["scope"], "private_relationship_facts_and_revisions")
        clear = dict(operation_key="clear-facts", expected_revision_token=preview["revision_token"])
        for extra in ({"subject_id": "another-user"}, {"operator": "admin"}, {"scope": {}}):
            self.assertEqual((await self.request("/clear", data=clear | extra))["data"]["code"], "memory_request_invalid")
        result = await self.request("/clear", data=clear)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["data"]["forgotten_count"], 1)
        self.assertEqual(await self.request("/clear", data=clear), result)
        hidden = (await self.request("/revisions", query="atom_id=" + item["atom_id"]))["data"]["items"]
        self.assertTrue(all(v["content"] is None for v in hidden))
        duplicate = b'{"operation_key":"a","operation_key":"b","expected_revision_token":"x"}'
        self.assertEqual((await self.request("/clear", raw=duplicate))["data"]["code"], "memory_request_invalid")
        self.assertEqual((await self.request("/revisions", query="atom_id=x&atom_id=y"))["data"]["code"],
                         "memory_request_invalid")
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])

    async def test_reflection_repair_api_reuses_receipts_and_hides_raw_output(self):
        session = await self.initialize()
        (await self.controller.start()).unwrap()
        app, context = session.application, session.profile.target
        app.tasks.now = lambda: NOW + timedelta(minutes=1)
        for action in ("memory.reflect", "memory.read", "memory.propose", "memory.subscribe"):
            app.tasks.identity.issue_grant(grant_id="fixture-" + action, subject=context.actor.subject, scope=context.scope,
                capability=action, tier=AuthorizationTier.STANDARD, valid_from=NOW,
                expected_revision=app.tasks.identity.revision)
        captured = (await session.bridge.capture(FakeEvent(self.native, event_id="reflection-input", text="我平时不喝咖啡"))).unwrap()
        NativeConversationObservations(app.messages).capture_input(context, captured, turn_id="reflection-input")
        before = len(self.native.provider.client.state.calls)
        prepared = app.memory_reflection.prepare(context, request_id="http-format-failure", start=NOW - timedelta(hours=1),
            end=app.tasks.now(), role_instruction=persona_instruction(self.native.persona)).unwrap()
        async def invalid(*_):
            return completion('{"summary":"private broken output",')
        self.native.provider.client.state.behavior = invalid
        failed = (await app.memory_reflection.run(context, prepared["batch_id"])).unwrap()
        self.assertEqual(failed["state"], "failed", failed)
        self.assertEqual(len(self.native.provider.client.state.calls), before + 2)
        denied = await self.request("/reflections", username=None)
        self.assertEqual(denied["data"]["code"], "dashboard_identity_required")
        listing = await self.request("/reflections")
        self.assertEqual(listing["status"], "ok", listing)
        leaf = next(v for v in listing["data"]["items"] if v["batch_id"] == failed["repair_batch"])
        self.assertTrue(leaf["retryable"])
        self.assertNotIn("private broken output", json.dumps(listing))
        self.assertNotIn("raw_output", json.dumps(listing))
        async def repaired(*_):
            return completion('{"summary":"已检查，没有新增认识。","proposals":[],"pending_decisions":[]}')
        self.native.provider.client.state.behavior = repaired
        arguments = {"batch_id": leaf["batch_id"], "operation_key": "repair-ui"}
        for extra in ({"subject_id": "someone-else"}, {"operator": "admin"}, {"evidence_handles": []}):
            result = await self.request("/reflections/retry", data=arguments | extra)
            self.assertEqual(result["data"]["code"], "memory_request_invalid")
        result = await self.request("/reflections/retry", data=arguments)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["data"]["state"], "complete")
        self.assertEqual(await self.request("/reflections/retry", data=arguments), result)
        self.assertEqual(len(self.native.provider.client.state.calls), before + 3)
        parent = await self.request("/reflections", query="batch_id=" + prepared["batch_id"])
        self.assertEqual(parent["data"]["state"], "complete")
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])

    async def test_reflection_api_rejects_ambiguous_inputs_and_disabled_memory_still_has_an_admin_queue(self):
        await self.initialize()
        self.assertEqual((await self.request("/reflections", query="limit=1&limit=2"))["data"]["code"], "memory_request_invalid")
        duplicate = b'{"batch_id":"x","batch_id":"y","operation_key":"r"}'
        self.assertEqual((await self.request("/reflections/retry", raw=duplicate))["data"]["code"], "memory_request_invalid")
        settings = (await self.request("/settings"))["data"]
        result = await self.request("/settings/command", data={"operation_key": "off-for-queue",
            "expected_edit_token": settings["edit_token"], "changes": {"memory.enabled": False}})
        self.assertEqual(result["status"], "ok")
        self.assertEqual((await self.request("/reflections"))["status"], "ok")

    async def test_background_reflection_returns_before_model_and_can_be_polled_after_completion(self):
        session = await self.initialize()
        (await self.controller.start()).unwrap()
        app, context = session.application, session.profile.target
        app.tasks.now = lambda: NOW + timedelta(minutes=1)
        for action in ("memory.reflect", "memory.read", "memory.propose", "memory.subscribe"):
            app.tasks.identity.issue_grant(grant_id="queue-" + action, subject=context.actor.subject, scope=context.scope,
                capability=action, tier=AuthorizationTier.STANDARD, valid_from=NOW,
                expected_revision=app.tasks.identity.revision)
        captured = (await session.bridge.capture(FakeEvent(self.native, event_id="queue-input", text="我平时不喝咖啡"))).unwrap()
        NativeConversationObservations(app.messages).capture_input(context, captured, turn_id="queue-input")
        calls = len(self.native.provider.client.state.calls)
        prepared = app.memory_reflection.prepare(context, request_id="http-queued", start=NOW - timedelta(hours=1),
            end=app.tasks.now(), role_instruction=persona_instruction(self.native.persona)).unwrap()
        started, finish = asyncio.Event(), asyncio.Event()
        async def respond(*_):
            started.set()
            await finish.wait()
            return completion('{"summary":"已检查，没有新增认识。","proposals":[],"pending_decisions":[]}')
        self.native.provider.client.state.behavior = respond
        args = {"batch_id": prepared["batch_id"], "operation_key": "queue-ui"}
        queued = await self.request("/reflections/queue", data=args)
        self.assertEqual(queued["status"], "ok", queued)
        self.assertEqual(queued["data"]["state"], "queued")
        self.assertFalse(started.is_set())
        self.assertEqual(await self.request("/reflections/queue", data=args), queued)
        await asyncio.wait_for(started.wait(), 3)
        listed = await self.request("/reflections", query="batch_id=" + prepared["batch_id"])
        self.assertEqual(listed["data"]["continuation"]["job_id"], queued["data"]["job_id"])
        self.assertNotIn("operator_json", json.dumps(listed))
        self.assertNotIn("raw_output", json.dumps(listed))
        finish.set()
        async def completed():
            while app.runtime.read_one("SELECT state FROM memory_reflection_jobs WHERE job_id = ?", (queued["data"]["job_id"],))[0] == "queued":
                await asyncio.sleep(0.01)
        await asyncio.wait_for(completed(), 3)
        replay = await self.request("/reflections/queue", data=args)
        self.assertEqual(replay["data"]["job_id"], queued["data"]["job_id"])
        self.assertEqual(replay["data"]["state"], "complete", replay)
        self.assertEqual(len(self.native.provider.client.state.calls), calls + 1)
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])

    async def test_background_reflection_requires_authentication_and_rejects_target_overrides(self):
        await self.initialize()
        args = {"batch_id": "batch", "operation_key": "job"}
        self.assertEqual((await self.request("/reflections/queue", data=args, username=None))["data"]["code"], "dashboard_identity_required")
        for extra in ({"context": {}}, {"operator": "admin"}, {"subject_id": "other"}, {"role_id": "other"}):
            result = await self.request("/reflections/queue", data=args | extra)
            self.assertEqual(result["data"]["code"], "memory_request_invalid")
        for raw in (b'{"batch_id":"a","batch_id":"b","operation_key":"x"}', b'[]', b'\xff'):
            self.assertEqual((await self.request("/reflections/queue", raw=raw))["data"]["code"], "memory_request_invalid")
        self.assertEqual((await self.request("/reflections/queue", data=args, query="target=other"))["data"]["code"], "memory_request_invalid")
        self.assertFalse((await self.request("/reflections/queue", data=args))["status"] == "ok")
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_reflection_api_pages_accumulated_unknowns_without_exposing_raw_returns(self):
        session = await self.initialize()
        context = session.profile.target
        manifest = encode({"evidence": [], "facts": [], "remaining_sources": 0, "unavailable_sources": 0,
                           "coverage": "captured_authorized_sources_only"})
        with session.application.runtime.transaction() as connection:
            for index in range(53):
                connection.execute("INSERT INTO memory_reflections VALUES (?, ?, ?, ?, ?, 'running', NULL, ?, NULL, ?, ?)",
                    (f"fixture-api-queue-{index:03d}", owner_key(context), "fixture-hash", encode(context), manifest,
                     encode({"items": {}, "sequence": 0}), stamp(NOW), stamp(NOW)))
            foreign = json.loads(encode(context))
            foreign["audience"]["scope"]["session_id"] = "other-session"
            foreign["role_binding"]["scope"]["session_id"] = "other-session"
            connection.execute("INSERT INTO memory_reflections VALUES (?, ?, ?, ?, ?, 'running', NULL, ?, NULL, ?, ?)",
                ("fixture-foreign-cursor", owner_key(context), "fixture-hash", encode(foreign), manifest,
                 encode({"items": {}, "sequence": 0}), stamp(NOW), stamp(NOW)))
        first = (await self.request("/reflections", query="limit=50"))["data"]
        self.assertEqual(len(first["items"]), 50)
        self.assertTrue(first["has_more"])
        cursor = first["next_cursor"]
        with session.application.runtime.transaction() as connection:
            connection.execute("UPDATE memory_reflections SET state = 'complete' WHERE batch_id = ?", (cursor,))
        second = (await self.request("/reflections", query="limit=50&after_id=" + cursor))["data"]
        self.assertEqual(len(second["items"]), 3)
        self.assertFalse(second["has_more"])
        self.assertIsNone(second["next_cursor"])
        self.assertEqual((await self.request("/reflections", query="after_id=fixture-foreign-cursor"))["data"]["code"],
                         "memory_reflection_unavailable")
        for query in ("after_id=a&after_id=b", "batch_id=a&after_id=b", "batch_id=a&after_id=", "after_id=" + "x" * 257):
            self.assertEqual((await self.request("/reflections", query=query))["data"]["code"], "memory_request_invalid")
        self.assertEqual((await self.request("/reflections", query="after_id=" + cursor, username=None))["data"]["code"],
                         "dashboard_identity_required")
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])

    async def test_reflection_history_includes_completed_and_empty_batches(self):
        session = await self.initialize()
        context = session.profile.target
        manifest = encode({"evidence": [], "facts": [], "remaining_sources": 0, "unavailable_sources": 0,
                           "coverage": "captured_authorized_sources_only", "selection": {}})
        with session.application.runtime.transaction() as connection:
            for state in ("complete", "empty", "running"):
                batch_id = "fixture-history-" + state
                connection.execute("INSERT INTO memory_reflections VALUES (?, ?, ?, ?, ?, ?, NULL, ?, NULL, ?, ?)",
                    (batch_id, owner_key(context), "fixture-hash", encode(context), manifest, state,
                     encode({"items": {}, "sequence": 0}), stamp(NOW), stamp(NOW)))

        pending = (await self.request("/reflections"))["data"]
        history = (await self.request("/reflections", query="history=1"))["data"]
        self.assertEqual([item["state"] for item in pending["items"]], ["running"])
        self.assertEqual({item["state"] for item in history["items"]}, {"complete", "empty", "running"})
        self.assertEqual(history["view"], "history")
        self.assertEqual((await self.request("/reflections", query="history=2"))["data"]["code"],
                         "memory_request_invalid")
        self.assertEqual((await self.request("/reflections", query="history=1", username=None))["data"]["code"],
                         "dashboard_identity_required")

    async def test_inactive_or_forgotten_fact_detail_degrades_to_readonly_metadata_and_keeps_revisions(self):
        session = await self.initialize()
        listed = (await self.request())["data"]["items"][0]
        forgot = await self.request("/command", data={
            "atom_id": listed["atom_id"],
            "expected_revision": listed["revision"],
            "view_token": listed["view_token"],
            "operation_key": "forget-for-detail",
            "action": "forget",
        })
        self.assertEqual(forgot["status"], "ok", forgot)
        after = (await self.request())["data"]["items"][0]
        self.assertEqual(after["state"], "invalidated")

        detail = await self.request("/detail", data={"atom_id": after["atom_id"], "revision": after["revision"]})
        self.assertEqual(detail["status"], "ok", detail)
        self.assertTrue(detail["data"]["item"]["read_only"])
        self.assertEqual(detail["data"]["item"]["state"], "invalidated")
        self.assertEqual(detail["data"]["item"]["unavailable_reason"], "memory_inactive")
        self.assertIsNone(detail["data"]["item"]["representation"])
        self.assertIsNone(detail["data"]["item"]["object"])
        self.assertEqual(detail["data"]["sources"], [])
        self.assertNotIn("实际管理员提供的测试记录", json.dumps(detail, ensure_ascii=False))

        revisions = await self.request("/revisions", query=f"atom_id={after['atom_id']}&limit=16")
        self.assertEqual(revisions["status"], "ok", revisions)
        self.assertGreaterEqual(len(revisions["data"]["items"]), 2)

        # Superseded and archived atoms also degrade safely while unauthorized / unknown / stale requests stay denied.
        for state in ("superseded", "archived"):
            with session.application.runtime.transaction() as connection:
                connection.execute("UPDATE memory_atoms SET state = ? WHERE atom_id = ?", (state, after["atom_id"]))
            degraded_detail = await self.request("/detail", data={"atom_id": after["atom_id"], "revision": after["revision"]})
            self.assertEqual(degraded_detail["status"], "ok", degraded_detail)
            self.assertEqual(degraded_detail["data"]["item"]["state"], state)
            self.assertEqual(degraded_detail["data"]["item"]["unavailable_reason"], "memory_inactive")
            self.assertTrue(degraded_detail["data"]["item"]["read_only"])

        denied = await self.request("/detail", username=None, data={"atom_id": after["atom_id"], "revision": after["revision"]})
        self.assertEqual(denied["data"]["code"], "dashboard_identity_required")
        missing = await self.request("/detail", data={"atom_id": "nonexistent-atom", "revision": 1})
        self.assertEqual(missing["data"]["code"], "memory_unavailable")
        stale = await self.request("/detail", data={"atom_id": after["atom_id"], "revision": after["revision"] + 5})
        self.assertEqual(stale["data"]["code"], "memory_revision_conflict")

    async def test_active_fact_with_revoked_source_degrades_readonly_within_single_snapshot(self):
        session = await self.initialize()
        listed = (await self.request())["data"]["items"][0]
        with session.application.runtime.transaction() as connection:
            connection.execute("UPDATE sources SET state = 'revoked'")
        detail = await self.request("/detail", data={"atom_id": listed["atom_id"], "revision": listed["revision"]})
        self.assertEqual(detail["status"], "ok", detail)
        self.assertEqual(detail["data"]["item"]["state"], "active")
        self.assertTrue(detail["data"]["item"]["read_only"])
        self.assertEqual(detail["data"]["item"]["unavailable_reason"], "source_revoked")
        self.assertIsNone(detail["data"]["item"]["representation"])
        self.assertIsNone(detail["data"]["item"]["object"])
        self.assertEqual(detail["data"]["sources"], [])
        self.assertNotIn("实际管理员提供的测试记录", json.dumps(detail, ensure_ascii=False))
