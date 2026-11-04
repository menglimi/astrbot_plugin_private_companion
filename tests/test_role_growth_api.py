"""Authenticated host requests exercise growth management without model or platform IO."""

from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock
from urllib.parse import quote

# Redirect SDK import-time data into the existing host fixture's temporary root.
from tests import test_astrbot_staging_host as host_support
from astrbot.api.web import PluginRequest, bind_request_context
from starlette.requests import Request

from astrbot_host.growth_api import PREFIX
from astrbot_host.management_identity import dashboard_operator
from companion_v2.execution.models import TaskError, digest, encode, stamp
from companion_v2.identity_policy.models import AuthorizationTier
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.role_work.growth.progress import receipt_fingerprint
from companion_v2.role_work.models import RoleActivity, RoleActivityState
from tests.native_adapter_support import FakeEvent, FakeProvider, FakeReverseApi
from tests.test_management_access import NOW


class GrowthManagementHostTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = TemporaryDirectory(prefix="companion-v2-growth-api-")
        self.addCleanup(self.temporary.cleanup)
        self.native = host_support.WebContext()
        types = host_support.NativeAdapterTypes("4.27.2", FakeEvent, FakeProvider,
                                                host_support.NS, host_support.NS, FakeReverseApi)
        self.controller = host_support.AstrBotStagingController(self.native, host_support.host_config(),
            Path(self.temporary.name) / "profiles", native_types=types, now=lambda: NOW)
        self.addAsyncCleanup(self.controller.terminate)
        self.controller.register_routes()
        self.routes = {route: handler for route, handler, _, _ in self.native.registered_web_apis}

    async def request(self, *, username="admin", data=None, raw=None, query="", suffix=""):
        body = raw if raw is not None else json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else b""
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}
        mutation = data is not None or raw is not None
        path = PREFIX + suffix + ("/command" if mutation else "")
        native = Request({"type": "http", "method": "POST" if mutation else "GET", "path": path,
            "headers": [(b"content-type", b"application/json")], "query_string": query.encode("utf-8")}, receive=receive)
        with bind_request_context(PluginRequest(native, username=username)):
            return await self.routes[path]()

    async def initialize(self):
        (await self.controller.initialize_profile()).unwrap()
        session = self.controller.session
        captured = (await session.bridge.capture(FakeEvent(self.native))).unwrap()
        event = session.host.inbound.ingest(captured, capability="text").unwrap()
        self.source = event.source
        self.target = session.profile.target
        return session

    async def proposal(self, **changes):
        status = await self.request()
        return dict(action="propose", operation_key="proposal", view_token=status["data"]["create_token"],
                    kind="goal", title="练习水彩", description="每周试着画一幅。",
                    progress_note="从构图练起。", sources=[json.loads(encode(self.source))]) | changes

    async def progress_review(self):
        session = await self.initialize()
        proposed = (await self.request(data=await self.proposal()))["data"]["item"]
        active = (await self.request(data=self.body(proposed)))["data"]["item"]
        app = session.application
        activity = app.role_life.add_activity(RoleActivity("growth-api-walk",
            self.target.role_binding.role.subject_id, self.target.scope, NOW,
            RoleActivityState.ACTIVE, description="到河边写生"))
        app.role_life.transition_activity(activity.activity_id, owner=activity.owner,
            expected_revision=1, state=RoleActivityState.COMPLETED,
            operation_id="growth-api-finished", observed_at=NOW)
        experience = app.growth_progress.receipts.read(self.target, "activity", "growth-api-finished", NOW)
        owner = app.role_growth_context.reader.owner(self.target)
        source = encode({"stream": "activity", "operation_id": "growth-api-finished",
                         "receipt_hash": receipt_fingerprint(experience)})
        with app.runtime.transaction() as connection:
            connection.execute("""INSERT INTO role_growth_progress_reviews VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL)""",
                ("growth-review:api", digest(owner), digest(self.target.scope), "growth-api-finished",
                 active["growth_id"], active["revision"], source,
                 "完成一次河边写生。", "pending", stamp(NOW)))
        return app, active

    @staticmethod
    def body(item, action="confirm", key="confirm", **changes):
        return dict(action=action, operation_key=key, growth_id=item["growth_id"],
            expected_revision=item["revision"], view_token=item["view_token"]) | changes

    async def test_logged_in_operator_creates_and_confirms_with_separate_attribution(self):
        session = await self.initialize()
        proposed = await self.request(data=await self.proposal())
        self.assertEqual(proposed["status"], "ok", proposed)
        item = proposed["data"]["item"]
        self.assertNotEqual(item["proposed_by"], self.target.actor.subject.subject_id)
        confirmed = await self.request(data=self.body(item))
        self.assertEqual(confirmed["status"], "ok", confirmed)
        self.assertEqual(confirmed["data"]["item"]["confirmed_by"], item["proposed_by"])
        self.assertEqual(confirmed["data"]["item"]["state"], "active")
        self.assertEqual(await self.request(data=self.body(item)), confirmed)
        self.assertFalse(session.application.tasks.identity.authorize(self.target.actor,
            scope=self.target.scope, capability="role.growth.manage", observed_at=NOW).is_success)
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])

    async def test_missing_login_and_unloaded_profile_do_not_initialize_data(self):
        self.assertEqual((await self.request(username=None))["data"]["code"], "dashboard_identity_required")
        self.assertEqual((await self.request())["data"]["code"], "staging_not_initialized")
        self.assertIsNone(self.controller.runtime)

    async def test_caller_cannot_supply_identity_scope_or_role(self):
        await self.initialize()
        payload = await self.proposal()
        for extra in ({"operator": "admin"}, {"context": {}}, {"role_id": "other"},
                      {"state": "active"}, {"confirmed_by": "admin"}, {"expected_revision": 0}):
            with self.subTest(extra=extra):
                denied = await self.request(data=payload | extra)
                self.assertEqual(denied["data"]["code"], "growth_command_invalid")
        wrong = replace(self.source, scope=replace(self.source.scope, session_id="other-session"))
        denied = await self.request(data=payload | {"sources": [json.loads(encode(wrong))]})
        self.assertEqual(denied["data"]["code"], "source_scope_mismatch")
        self.assertEqual((await self.request())["data"]["items"], [])

    async def test_invalid_json_source_shapes_and_queries_return_bounded_errors(self):
        await self.initialize()
        for raw in (b'{"action":"confirm","action":"retire"}', b'[]', b'\xff', b'{"action":[]}'):
            self.assertEqual((await self.request(raw=raw))["data"]["code"], "growth_command_invalid")
        payload = await self.proposal()
        source = payload["sources"][0]
        for value in ({}, [], source | {"owner": []}, source | {"issued_at": None},
                      source | {"version": {"lineage_id": "a", "revision": True}},
                      source | {"scope": source["scope"] | {"bot_id": []}}):
            denied = await self.request(data=payload | {"sources": [value]})
            self.assertEqual(denied["data"]["code"], "growth_command_invalid")
        for query in ("kind=unknown", "state=unknown", "limit=-1", "limit=true", "limit=1&limit=2", "operator=admin"):
            self.assertEqual((await self.request(query=query))["data"]["code"], "growth_query_invalid")

    async def test_another_admin_cannot_reuse_view_and_api_key_is_not_a_person(self):
        await self.initialize()
        proposal = await self.proposal()
        denied = await self.request(username="other-admin", data=proposal)
        self.assertEqual(denied["data"]["code"], "growth_view_stale")
        self.assertEqual((await self.request(username="api_key:123"))["data"]["code"], "dashboard_identity_required")

    async def test_revoked_operator_or_target_prevents_commands(self):
        await self.initialize()
        proposal = await self.proposal()
        operator = dashboard_operator(self.controller.runtime, "admin", at=NOW)
        policy = ObjectAccessPolicy(self.controller.runtime)
        policy.set_tier(operator.subject, AuthorizationTier.STANDARD, expected_revision=policy.revision, observed_at=NOW)
        denied = await self.request(data=proposal)
        self.assertEqual(denied["data"]["code"], "core_access_required")
        policy.identity.revoke_binding(operator.binding_id, expected_revision=policy.identity.revision, revoked_at=NOW)
        self.assertEqual((await self.request())["data"]["code"], "binding_revoked")
        policy.identity.revoke_binding(self.target.actor.binding_id, expected_revision=policy.identity.revision, revoked_at=NOW)
        self.assertEqual((await self.request(username="new-admin"))["data"]["code"], "binding_revoked")

    async def test_restart_restores_receipts_and_routes_are_unregistered(self):
        await self.initialize()
        proposed = (await self.request(data=await self.proposal()))["data"]["item"]
        receipt = await self.request(data=self.body(proposed))
        types = self.controller.native_types
        root = self.controller.data_root
        await self.controller.terminate()
        self.assertEqual(self.native.registered_web_apis, [])
        self.assertEqual((await self.request())["data"]["code"], "staging_host_terminated")
        self.controller = host_support.AstrBotStagingController(self.native, host_support.host_config(), root,
            native_types=types, now=lambda: NOW)
        self.addAsyncCleanup(self.controller.terminate)
        self.controller.register_routes()
        self.routes = {route: handler for route, handler, _, _ in self.native.registered_web_apis}
        (await self.controller.load_existing()).unwrap()
        self.assertEqual(await self.request(data=self.body(proposed)), receipt)

    async def test_progress_routes_accept_review_with_authenticated_dashboard_operator(self):
        app, active = await self.progress_review()
        self.assertIn(PREFIX + "/progress", self.routes)
        self.assertIn(PREFIX + "/progress/command", self.routes)
        listed = await self.request(suffix="/progress")
        self.assertEqual(listed["status"], "ok", listed)
        item = listed["data"]["items"][0]
        self.assertEqual(item["base_revision"], active["revision"])
        body = dict(review_id=item["review_id"], action="accept",
                    expected_revision=item["base_revision"], view_token=item["view_token"],
                    operation_key="api-progress-accept")
        denied = await self.request(suffix="/progress", username="other-admin", data=body)
        self.assertEqual(denied["data"]["code"], "growth_view_stale")
        refreshed = (await self.request(suffix="/progress"))["data"]["items"][0]
        body["view_token"] = refreshed["view_token"]
        accepted = await self.request(suffix="/progress", data=body)
        self.assertEqual(accepted["status"], "ok", accepted)
        self.assertEqual(accepted["data"]["applied_revision"], 3)
        self.assertTrue((await self.request(suffix="/progress", data=body))["data"]["replayed"])
        self.assertEqual((await self.request(suffix="/progress"))["data"]["items"], [])
        owner = app.role_growth_context.reader.owner(self.target)
        self.assertEqual(app.role_growth.record(active["growth_id"], owner=owner).revision, 3)

    async def test_progress_routes_reject_invalid_query_body_and_unloaded_host(self):
        self.assertEqual((await self.request(suffix="/progress"))["data"]["code"], "staging_not_initialized")
        await self.progress_review()
        for query in ("limit=0", "limit=1&limit=2", "state=pending"):
            self.assertEqual((await self.request(suffix="/progress", query=query))["data"]["code"],
                             "growth_progress_query_invalid")
        for raw in (b'[]', b'\xff', b'{"action":"accept","action":"reject"}'):
            self.assertEqual((await self.request(suffix="/progress", raw=raw))["data"]["code"],
                             "growth_progress_command_invalid")
        self.assertEqual((await self.request(suffix="/progress", username=None))["data"]["code"],
                         "dashboard_identity_required")
        await self.controller.terminate()
        self.assertEqual(self.native.registered_web_apis, [])
        self.assertEqual((await self.request(suffix="/progress"))["data"]["code"],
                         "staging_host_terminated")

    async def test_self_review_routes_read_pending_and_decided_items_for_the_pinned_role(self):
        self.assertIn(PREFIX + "/self-review", self.routes)
        self.assertIn(PREFIX + "/self-review/command", self.routes)
        self.assertEqual((await self.request(suffix="/self-review"))["data"]["code"],
                         "staging_not_initialized")
        await self.initialize()

        pending = await self.request(suffix="/self-review")
        self.assertEqual(pending["status"], "ok", pending)
        self.assertEqual(pending["data"]["items"], [])
        decided = await self.request(suffix="/self-review", query="state=decided")
        self.assertEqual(decided["status"], "ok", decided)
        self.assertEqual(decided["data"]["items"], [])
        self.assertEqual((await self.request(suffix="/self-review", query="state=all"))["data"]["code"],
                         "self_review_query_invalid")
        # The list cursor is "<created_at>|<item_id>" and travels as one query value,
        # so the host layer must decode the encoded separator and UTC offset.
        cursor = stamp(NOW) + "|self-review:" + "0" * 64 + ":item:0"
        forwarded = await self.request(suffix="/self-review",
            query="state=active&after_id=" + quote(cursor, safe=""))
        self.assertEqual(forwarded["status"], "ok", forwarded)
        self.assertEqual(forwarded["data"]["items"], [])
        # A bare item id is the cursor of the previous hash-ordered list: the page has
        # to re-read instead of resuming it.
        legacy = await self.request(suffix="/self-review",
            query="state=active&after_id=" + quote("self-review:" + "0" * 64 + ":item:0", safe=""))
        self.assertEqual(legacy["data"]["code"], "self_review_query_invalid")
        self.assertEqual((await self.request(suffix="/self-review", username=None))["data"]["code"],
                         "dashboard_identity_required")

        malformed = {"item_id":"missing", "action":"accept", "view_token":"view",
                     "operation_key":"review-test", "scope":{}}
        self.assertEqual((await self.request(suffix="/self-review", data=malformed))["data"]["code"],
                         "self_review_command_invalid")
        valid_shape = {key:value for key, value in malformed.items() if key != "scope"}
        self.assertEqual((await self.request(suffix="/self-review", data=valid_shape))["data"]["code"],
                         "self_review_item_unavailable")

    async def test_self_review_list_reports_a_row_whose_evidence_reset_instead_of_failing(self):
        session = await self.initialize()
        app = session.application
        owner = app.role_growth_context.reader.owner(self.target)
        created = NOW - timedelta(hours=1)
        window = {"opened_at": (created - timedelta(hours=1)).isoformat(),
                  "closed_at": created.isoformat(), "handles": ["h-1"], "kinds": ["conversation"]}
        run_id = "self-review:" + digest([self.target.key, "reset-window"])
        with app.runtime.transaction() as connection:
            connection.execute("INSERT INTO role_self_review_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, digest(owner), digest(self.target.scope), json.dumps(window),
                 "material-reset", "summary reset", "decided", stamp(created), stamp(created)))
            connection.execute("INSERT INTO role_self_review_items VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id + ":item:0", run_id, digest(owner), digest(self.target.scope), "keep",
                 "observation reset", "next action", 0.8, json.dumps(["h-1"]),
                 stamp(NOW + timedelta(hours=12)), "accepted", stamp(created),
                 None, None, None, 0))
        # The cited observation was reset away, so describing the evidence raises a
        # TaskError.  The route has to answer with that row degraded — a 500 here is
        # what made the dashboard report "当前状态未能确认" for the whole page.
        evidence = app.role_self_review_evidence
        evidence.describe_handles = Mock(side_effect=TaskError(
            "conversation_context_reset", "native history precedes this window's reset"))
        for query in ("state=active", "state=decided"):
            response = await self.request(suffix="/self-review", query=query)
            self.assertEqual(response["status"], "ok", response)
        item = (await self.request(suffix="/self-review", query="state=active"))["data"]["items"][0]
        self.assertEqual(item["unavailable_reason"], "self_review_evidence_changed")
        self.assertIsNone(item["observation"])
        self.assertIsNone(item["next_action"])
