"""Host end-to-end tests for Content access self-healing, logging, and honest status."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests import astrbot_data_root  # noqa: F401 - isolate SDK root before AstrBot imports
from tests import test_astrbot_staging_host as host_support  # noqa: F401
from astrbot_host.capability_role_selection import AUTO_ROLE_EXTENSIONS
from astrbot_host.content_access import CONTENT_EXTENSION, ensure_content_access
from astrbot_host.core_reply import prepare_core_reply_event
from astrbot_host.core_reply_tools import close_core_reply_tools, inject_core_reply_tools
from astrbot_host.integration_registry import IntegrationRegistry
from astrbot_host.native_extensions.scheduled_authoring import ScheduledAuthoring
from companion_v2.adapters.astrbot.model_wire import decode_text_response
from companion_v2.agenda.models import Schedule
from companion_v2.capabilities.models import CapabilityAuthorizationState
from companion_v2.capabilities.repositories import CapabilityRepository
from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.configuration.owners import SparseConfigurationOwner
from companion_v2.configuration.registry import FieldRegistry
from companion_v2.conversation.content.authoring_service import (
    FIELDS as AUTHORING_FIELDS,
    GROUP as AUTHORING_GROUP,
)
from companion_v2.conversation.content.authoring_tasks import DRAFT_KIND, authoring_profiles
from companion_v2.conversation.content.authoring_workflow import INSTRUCTION as AUTHORING_INSTRUCTION
from companion_v2.conversation.flow import ConversationPolicy
from companion_v2.execution.models import ClaimResult, TaskContext, encode, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import AudienceRef
from companion_v2.identity_policy.models import AuthorizationTier
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.identity_policy.personas import ConversationRoute
from companion_v2.model_gateway.routing import configuration_fields
from companion_v2.role_work.life.management import RoleLifeManagement
from tests.native_content_support import NativeContentFixture
from tests.test_astrbot_core_reply import FakeEvent
from tests.test_host_application import HostFixture
from tests.test_management_access import NOW, SCOPE
from tests.test_role_tool_loop import answered


class ContentAuthoringHostAccessTests(NativeContentFixture, HostFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.profiles += authoring_profiles("primary", draft_tokens=87)
        self.registry = FieldRegistry(
            version="content-host-access-test.v1",
            fields=configuration_fields(self.profiles),
        )

    async def start_assembled_only_host(self):
        """Start host with Content installed ONLY (zero role/session rows — production bug state)."""
        self.create_content_port()
        host = self.host(
            tool_port=self.native_registry,
            policy=ConversationPolicy(0, 0, 180, 0),
        )
        repository = CapabilityRepository(self.runtime)
        self.native_registry.register_catalog(observed_at=self.clock.now()).unwrap()
        package = self.native_registry.declarations()[0].descriptor.package
        repository.acquire(
            extension_id=CONTENT_EXTENSION,
            package_id=package.package_id,
            package_version=package.package_version,
            expected_revision=0,
            observed_at=NOW,
        ).unwrap()
        started = await self.start(host)
        await self.content_lease.start()
        return started

    def private_context(self, actor, *, scope=SCOPE):
        if scope != SCOPE:
            self.personas.register_route(
                ConversationRoute(scope, user=actor.subject), observed_at=self.clock.now()
            )
            if not self.identity.resolve_role(scope, observed_at=self.clock.now()).is_success:
                self.identity.bind_role(
                    scope=scope,
                    role=self.window.role,
                    expected_revision=0,
                    observed_at=self.clock.now(),
                )
            self.grant(actor, scope, "conversation.reply")
        return TaskContext(
            actor,
            require(self.identity.resolve_role(scope, observed_at=self.clock.now())),
            AudienceRef(scope, (actor.subject,)),
        )

    def test_auto_role_extensions_includes_content(self):
        self.assertIn(CONTENT_EXTENSION, AUTO_ROLE_EXTENSIONS)

    def promote_private_user_to_core(self):
        policy = ObjectAccessPolicy(self.runtime)
        policy.set_tier(
            self.important.subject,
            AuthorizationTier.CORE,
            expected_revision=policy.revision,
            observed_at=self.clock.now(),
        )
        return self.identity.resolve_authenticated(
            self.important.principal, observed_at=self.clock.now()
        ).unwrap()

    async def test_foreground_core_private_turn_self_heals_and_survives_conversation_switch(self):
        host = await self.start_assembled_only_host()
        app = host.application
        self.addAsyncCleanup(close_core_reply_tools, app)
        core_user = self.promote_private_user_to_core()

        # Before self-heal, both tables are empty and Content tools are invisible.
        self.assertEqual(
            self.runtime.read_all(
                "SELECT * FROM capability_role_selections WHERE extension_id=?",
                (CONTENT_EXTENSION,),
            ),
            (),
        )
        self.assertEqual(
            self.runtime.read_all(
                "SELECT * FROM capability_authorizations WHERE extension_id=?",
                (CONTENT_EXTENSION,),
            ),
            (),
        )

        async def offer_for(scope, identifier):
            context = self.private_context(core_user, scope=scope)
            prepared = {"context": context, "event_id": identifier, "turn_id": "turn-" + identifier}
            event = FakeEvent()
            prepare_core_reply_event(
                event,
                prepared,
                authorize=lambda _: asyncio.sleep(
                    0,
                    result=app.tasks.check_context_access(
                        context, action="conversation.reply", observed_at=self.clock.now()
                    ),
                ),
            )
            request = SimpleNamespace(func_tool=None, system_prompt="原角色人格")
            offered = inject_core_reply_tools(event, request, application=app)
            return offered, request

        offered, request = await offer_for(SCOPE, "first-turn")
        self.assertTrue(
            {"content.authoring.create", "content.authoring.continue"}.issubset(offered),
            offered,
        )
        self.assertEqual(
            {action for action in offered if action.startswith("content.work.")},
            set(self.content_port.manifest()["package"]["capabilities"]),
        )
        self.assertIn(AUTHORING_INSTRUCTION, request.system_prompt)

        # Switching conversation_id seamlessly continues Content authoring visibility.
        switched_scope = replace(SCOPE, conversation_id="conversation-switched")
        switched_offered, switched_request = await offer_for(switched_scope, "second-turn")
        self.assertTrue(
            {"content.authoring.create", "content.authoring.continue"}.issubset(switched_offered),
            switched_offered,
        )
        self.assertIn(AUTHORING_INSTRUCTION, switched_request.system_prompt)

    async def test_scheduled_draft_waits_for_a_slot_and_saves_fenced_json(self):
        host = await self.start_assembled_only_host()
        app = host.application
        context = self.private_context(self.promote_private_user_to_core())
        life = app.life_runtime.bind(context, "原人格")
        manager = RoleLifeManagement(app, context)
        status = manager.status(self.core)
        (await manager.command(self.core, dict(action="configure", changes={"enabled": True},
             edit_token=status["edit_token"], view_token=status["view_token"], operation_key="enable-life"))).unwrap()
        life.refresh()
        start = self.clock.now() + timedelta(minutes=2)
        app.calendar.command(context, identifier="writing", action="create", expected_revision=0,
            operation_key="writing-calendar", planning={"mode": "routine", "routine": "writing"},
            data={"kind": "role_plan", "title": "写一篇新故事", "detail": "海边随笔",
                  "schedule": json.loads(encode(Schedule(start, "UTC"))), "duration_minutes": 30, "presence": ""})

        async def model(envelope):
            self.assertEqual(envelope.original.output_format, "content_authoring")
            self.assertIn("finished", envelope.original.instruction)
            raw = '```json\n{"title":"岸边","work_type":"随笔","body":"潮水退去。"}\n```'
            draft, _ = decode_text_response(raw, SimpleNamespace(output_format="content_authoring"))
            return answered(draft.text)

        self.model_behavior = model
        claim = app.tasks.claim_task
        refused = []

        def first_claim(ctx, task_id):
            if task_id.startswith("content-authoring-") and not refused:
                refused.append(task_id)
                return Outcome.success(ClaimResult(None, "task_lane_full"))
            return claim(ctx, task_id)

        def receipt():
            row = app.runtime.read_one("SELECT value FROM runtime_metadata WHERE key LIKE 'content.calendar:%'", ())
            return json.loads(row["value"]) if row is not None else {}

        with patch.object(app.tasks, "claim_task", side_effect=first_claim):
            self.advance(app, 120)
            await self.until(lambda: receipt().get("state") in {"written", "failed"})
        self.assertEqual(len(refused), 1)
        self.assertEqual(receipt()["state"], "written", receipt())

    async def test_scheduled_authoring_ready_self_heals_deadlock_and_logs_rate_limited_denials(self):
        host = await self.start_assembled_only_host()
        app = host.application
        core_user = self.promote_private_user_to_core()
        core_context = self.private_context(core_user, scope=SCOPE)
        scheduled = app.scheduled_authoring
        self.assertIsInstance(scheduled, ScheduledAuthoring)
        self.assertIn(DRAFT_KIND, app.gateway.router.profiles)

        window = {
            "id": "plan-writing-1",
            "sequence": 0,
            "title": "写一篇短篇随笔",
            "detail": "安静动笔",
            "end": (self.clock.now() + timedelta(hours=1)).isoformat(),
        }

        # Core context with zero initial role/consent rows self-heals and becomes ready.
        self.assertTrue(scheduled.ready(core_context, window))
        self.assertEqual(
            len(
                self.runtime.read_all(
                    "SELECT * FROM capability_authorizations WHERE extension_id=? AND state='active'",
                    (CONTENT_EXTENSION,),
                )
            ),
            7,
        )

        # An empty catalogue can still reserve a writing window for a new work.
        now = self.clock.now()
        until = now + timedelta(days=7)
        page = app.calendar.occurrences(core_context, start=now, end=until, diagnostics=True)
        planned = scheduled.plan_continuation(
            core_context, now, {"planning_enabled": True, "planning_timezone": "UTC"},
            page, sleep_plan=None,
        )
        items = app.calendar.occurrences(core_context, start=now, end=until, diagnostics=True)["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["start"], planned)
        self.assertEqual(items[0]["planning"]["routine"], "writing")
        self.assertEqual(items[0]["title"], "文学创作")

        # Explicit role disable blocks ready() and emits a rate-limited warning with error.code.
        now_tick = [1000.0]
        scheduled._log_clock = lambda: now_tick[0]
        repo = CapabilityRepository(self.runtime)
        repo.set_role_enabled(
            extension_id=CONTENT_EXTENSION,
            role=core_context.role_binding.role,
            bot_id=core_context.scope.bot_id,
            enabled=False,
            expected_revision=1,
            observed_at=self.clock.now(),
        ).unwrap()

        with self.assertLogs(
            "astrbot_host.native_extensions.scheduled_authoring", level="WARNING"
        ) as first_logs:
            self.assertFalse(scheduled.ready(core_context, window))
        self.assertTrue(
            any("capability_role_not_enabled" in line for line in first_logs.output),
            first_logs.output,
        )

        # Immediate repeat within 300s is suppressed by rate limiter.
        now_tick[0] += 30.0
        with self.assertNoLogs(
            "astrbot_host.native_extensions.scheduled_authoring", level="WARNING"
        ):
            self.assertFalse(scheduled.ready(core_context, window))

        # After cooldown window, warning emits again.
        now_tick[0] += scheduled.BLOCKED_LOG_INTERVAL_SECONDS + 1.0
        with self.assertLogs(
            "astrbot_host.native_extensions.scheduled_authoring", level="WARNING"
        ) as later_logs:
            self.assertFalse(scheduled.ready(core_context, window))
        self.assertTrue(
            any("capability_role_not_enabled" in line for line in later_logs.output),
            later_logs.output,
        )

        # Disabling authoring config logs content_authoring_disabled when a writing window exists.
        owner = SparseConfigurationOwner(self.runtime)
        target = ConfigTarget(
            core_context.scope.installation_id,
            ConfigLayer.ROLE,
            core_context.role_binding.role,
            bot_id=core_context.scope.bot_id,
        )
        owner.write(
            target,
            AUTHORING_FIELDS,
            changes={AUTHORING_GROUP + ".enabled": False},
            reset=(),
            expected_revision=owner.read(target, AUTHORING_FIELDS).revision,
            observed_at=self.clock.now(),
        )
        with self.assertLogs(
            "astrbot_host.native_extensions.scheduled_authoring", level="WARNING"
        ) as cfg_logs:
            self.assertFalse(scheduled.ready(core_context, window))
        self.assertTrue(
            any("content_authoring_disabled" in line for line in cfg_logs.output),
            cfg_logs.output,
        )

        # Already-claimed window returns False silently with no warning.
        with self.runtime.transaction() as connection:
            connection.execute(
                "INSERT INTO runtime_metadata(key, value) VALUES (?, ?)",
                (scheduled.key(core_context, window), encode({"state": "written"})),
            )
        with self.assertNoLogs(
            "astrbot_host.native_extensions.scheduled_authoring", level="WARNING"
        ):
            self.assertFalse(scheduled.ready(core_context, window))

    async def test_integration_registry_reports_honest_content_authorization_status(self):
        host = await self.start_assembled_only_host()
        app = host.application
        registry = IntegrationRegistry(SimpleNamespace(get_all_stars=lambda: ()))

        def content_item():
            snapshot = registry.snapshot(
                application=app,
                native_extensions=self.native_registry,
                terminated=False,
            )
            return next(
                item for item in snapshot["extensions"] if item["extension_id"] == CONTENT_EXTENSION
            )

        # Installed only, zero role/session rows -> honest "waiting", never fake "connected".
        missing_role = content_item()
        self.assertEqual(missing_role["integration_state"], "waiting")
        self.assertEqual(
            missing_role["authorization"],
            {
                "ready": False,
                "role_enabled_count": 0,
                "authorized_scope_count": 0,
                "issue": "capability_role_not_enabled",
            },
        )
        self.assertIn("尚未对角色启用", missing_role["reason"])

        # Role enabled, zero session consent -> still honest "waiting".
        repo = CapabilityRepository(self.runtime)
        repo.set_role_enabled(
            extension_id=CONTENT_EXTENSION,
            role=self.window.role,
            bot_id=SCOPE.bot_id,
            enabled=True,
            expected_revision=0,
            observed_at=NOW,
        ).unwrap()
        missing_consent = content_item()
        self.assertEqual(missing_consent["integration_state"], "waiting")
        self.assertEqual(
            missing_consent["authorization"],
            {
                "ready": False,
                "role_enabled_count": 1,
                "authorized_scope_count": 0,
                "issue": "capability_consent_missing",
            },
        )
        self.assertIn("尚无已授权的私聊会话", missing_consent["reason"])

        # After core private access provisioning -> transitions cleanly to "connected".
        core_context = self.private_context(self.core, scope=SCOPE)
        self.assertTrue(
            ensure_content_access(self.runtime, core_context, observed_at=self.clock.now())
        )
        ready = content_item()
        self.assertEqual(ready["integration_state"], "connected")
        self.assertEqual(
            ready["authorization"],
            {
                "ready": True,
                "role_enabled_count": 1,
                "authorized_scope_count": 1,
                "issue": None,
            },
        )

        # Revoking create + save_candidate drops back to honest "waiting".
        for capability in ("content.work.create", "content.work.save_candidate"):
            repo.set_authorization(
                extension_id=CONTENT_EXTENSION,
                capability_id=capability,
                subject=core_context.actor.subject,
                scope=core_context.scope,
                state=CapabilityAuthorizationState.REVOKED,
                expected_revision=1,
                observed_at=self.clock.now(),
            ).unwrap()
        revoked = content_item()
        self.assertEqual(revoked["integration_state"], "waiting")
        self.assertFalse(revoked["authorization"]["ready"])


if __name__ == "__main__":
    unittest.main()
