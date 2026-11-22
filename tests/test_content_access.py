"""Persisted Content access provisioning, management hook, and planning summary."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from astrbot_host.content_access import (
    CONTENT_CAPABILITIES,
    CONTENT_EXTENSION,
    content_authorization_status,
    ensure_content_access,
)
from companion_v2.agenda.planning import planning_profile
from companion_v2.agenda.planning_inputs import capture
from companion_v2.agenda.planning_output import INSTRUCTION as PLANNING_INSTRUCTION
from companion_v2.capabilities import CapabilityAuthorizationState, CapabilityPackageDescriptor
from companion_v2.capabilities.repositories import CapabilityRepository
from companion_v2.configuration.management import ContentAuthoringManagement
from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.configuration.owners import SparseConfigurationOwner
from companion_v2.configuration.registry import FieldRegistry
from companion_v2.content.catalogue import content_database
from companion_v2.content.models import Call, Chapter, JsonObject, Origin, WorkOwner
from companion_v2.content.repository import ContentRepository
from companion_v2.content.service import ContentService
from companion_v2.conversation.content.access import document, owner_for
from companion_v2.conversation.content.authoring_service import (
    FIELDS as AUTHORING_FIELDS,
    GROUP as AUTHORING_GROUP,
)
from companion_v2.execution.models import TaskContext
from companion_v2.foundation.refs import AudienceRef, ScopeKind
from companion_v2.identity_policy.models import AuthorizationTier
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.identity_policy.repository import PersistentIdentityDirectory
from companion_v2.model_gateway.routing import configuration_fields
from tests.g6_support import G6Fixture, NOW
from tests.test_management_access import SCOPE
from tests.test_role_state import StateFixture


class ContentAccessProvisioningTests(G6Fixture):
    def setUp(self):
        super().setUp()
        self.scope = replace(
            self.scope,
            bot_id="bot-a",
            account_id="100",
            conversation_id="conversation-a",
            session_id="200",
        )
        self.context = NS(
            scope=self.scope,
            actor=NS(subject=self.subject),
            role_binding=NS(role=self.role),
        )
        self.package = CapabilityPackageDescriptor(
            CONTENT_EXTENSION,
            "astrbot_plugin_private_companion.content",
            "companion",
            "1",
            "content-hash",
            CONTENT_CAPABILITIES,
        )
        self.capabilities.register_descriptor(self.package, registered_at=NOW).unwrap()

    def install_content(self):
        self.capabilities.acquire(
            extension_id=CONTENT_EXTENSION,
            package_id=self.package.package_id,
            package_version=self.package.package_version,
            expected_revision=0,
            observed_at=NOW,
        ).unwrap()

    def set_tier(self, tier: AuthorizationTier, subject=None):
        policy = ObjectAccessPolicy(self.runtime)
        policy.set_tier(
            subject or self.subject,
            tier,
            expected_revision=policy.revision,
            observed_at=self.clock.now(),
        )

    def set_authoring(self, **changes):
        target = ConfigTarget(
            self.scope.installation_id,
            ConfigLayer.ROLE,
            self.role,
            bot_id=self.scope.bot_id,
        )
        owner = SparseConfigurationOwner(self.runtime)
        owner.write(
            target,
            AUTHORING_FIELDS,
            changes={AUTHORING_GROUP + "." + name: value for name, value in changes.items()},
            reset=(),
            expected_revision=owner.read(target, AUTHORING_FIELDS).revision,
            observed_at=self.clock.now(),
        )

    def ensure(self, context=None):
        return ensure_content_access(
            self.runtime, context or self.context, observed_at=self.clock.now()
        )

    def consent(self, capability="content.work.create", context=None):
        context = context or self.context
        return self.capabilities.consent(
            extension_id=CONTENT_EXTENSION,
            capability_id=capability,
            subject=context.actor.subject,
            scope=context.scope,
        )

    def choose(self, state, *, capability="content.work.create", revision=1, context=None):
        context = context or self.context
        return self.capabilities.set_authorization(
            extension_id=CONTENT_EXTENSION,
            capability_id=capability,
            subject=context.actor.subject,
            scope=context.scope,
            state=state,
            expected_revision=revision,
            observed_at=self.clock.now(),
        ).unwrap()

    def records(self):
        return tuple(
            tuple(tuple(row) for row in self.runtime.read_all("SELECT * FROM " + table, ()))
            for table in ("capability_role_selections", "capability_authorizations")
        )

    def test_core_private_provisions_role_and_seven_capabilities_idempotently(self):
        self.set_tier(AuthorizationTier.CORE)
        self.assertFalse(self.ensure())
        self.assertEqual(self.records(), ((), ()))

        self.install_content()
        self.set_tier(AuthorizationTier.STANDARD)
        self.assertFalse(self.ensure())
        self.assertEqual(self.records(), ((), ()))

        self.set_tier(AuthorizationTier.IMPORTANT)
        self.assertFalse(self.ensure())
        self.assertEqual(self.records(), ((), ()))

        self.set_tier(AuthorizationTier.CORE)
        self.set_authoring(enabled=False)
        self.assertFalse(self.ensure())
        self.assertEqual(self.records(), ((), ()))

        self.set_authoring(enabled=True)
        self.assertTrue(self.ensure())
        self.assertEqual(len(self.records()[0]), 1)
        self.assertEqual(len(self.records()[1]), 7)
        for capability in CONTENT_CAPABILITIES:
            self.assertEqual(
                self.consent(capability).unwrap().state,
                CapabilityAuthorizationState.ACTIVE,
            )

        before = self.records()
        self.clock.advance(timedelta(minutes=1))
        self.assertFalse(self.ensure())
        self.assertEqual(self.records(), before)

    def test_explicit_role_disable_is_not_overwritten(self):
        self.set_tier(AuthorizationTier.CORE)
        self.install_content()
        self.capabilities.set_role_enabled(
            extension_id=CONTENT_EXTENSION,
            role=self.role,
            bot_id=self.scope.bot_id,
            enabled=False,
            expected_revision=0,
            observed_at=NOW,
        ).unwrap()
        before = self.records()
        with self.assertNoLogs("astrbot_host.content_access", level="WARNING"):
            self.assertFalse(self.ensure())
        self.assertEqual(self.records(), before)

    def test_conversation_switch_carries_active_and_preserves_revocation(self):
        self.set_tier(AuthorizationTier.CORE)
        self.install_content()
        self.assertTrue(self.ensure())
        self.choose(CapabilityAuthorizationState.REVOKED, capability="content.work.create")

        switched = NS(
            scope=replace(self.scope, conversation_id="conversation-b"),
            actor=self.context.actor,
            role_binding=self.context.role_binding,
        )
        self.assertEqual(
            self.consent("content.work.create", context=switched).failure.code,
            "capability_consent_missing",
        )
        self.assertTrue(self.ensure(switched))
        self.assertEqual(
            self.consent("content.work.create", context=switched).failure.code,
            "capability_consent_revoked",
        )
        for capability in CONTENT_CAPABILITIES[1:]:
            self.assertEqual(
                self.consent(capability, context=switched).unwrap().state,
                CapabilityAuthorizationState.ACTIVE,
            )

        self.clock.advance(timedelta(minutes=1))
        self.choose(
            CapabilityAuthorizationState.ACTIVE,
            capability="content.work.create",
            revision=1,
            context=switched,
        )
        third = NS(
            scope=replace(self.scope, conversation_id="conversation-c"),
            actor=self.context.actor,
            role_binding=self.context.role_binding,
        )
        self.assertTrue(self.ensure(third))
        self.assertTrue(self.consent("content.work.create", context=third).is_success)

    def test_group_scope_and_paused_or_removing_installation_are_ignored(self):
        self.set_tier(AuthorizationTier.CORE)
        self.install_content()
        group_context = NS(
            scope=replace(
                self.scope,
                kind=ScopeKind.GROUP,
                native_scope_id="group-1",
                session_id="group-1",
                conversation_id="group-conv",
            ),
            actor=self.context.actor,
            role_binding=self.context.role_binding,
        )
        self.assertFalse(self.ensure(group_context))
        self.assertEqual(self.records(), ((), ()))

        self.capabilities.pause(
            extension_id=CONTENT_EXTENSION, expected_revision=1, observed_at=NOW
        ).unwrap()
        self.assertFalse(self.ensure())
        self.assertEqual(self.records(), ((), ()))

        self.capabilities.request_discard(
            extension_id=CONTENT_EXTENSION, expected_revision=2, observed_at=NOW
        ).unwrap()
        self.assertFalse(self.ensure())
        self.assertEqual(self.records(), ((), ()))

    def test_failed_authorization_write_rolls_back_partial_role_selection(self):
        self.set_tier(AuthorizationTier.CORE)
        self.install_content()
        with patch.object(
            CapabilityRepository, "set_authorization", side_effect=RuntimeError("synthetic")
        ), self.assertLogs("astrbot_host.content_access", level="WARNING"):
            self.assertFalse(self.ensure())
        self.assertEqual(self.records(), ((), ()))

    def test_invocation_grant_carries_across_conversations_and_respects_revocation(self):
        self.set_tier(AuthorizationTier.CORE)
        self.install_content()
        self.assertTrue(self.ensure())
        identities = PersistentIdentityDirectory(self.runtime)
        grants = identities.grants_for_subject(self.subject)
        self.assertEqual(len(grants), 1)
        self.assertEqual(grants[0].capability, "extension.tool.invoke")

        switched = NS(
            scope=replace(self.scope, conversation_id="conversation-b"),
            actor=self.context.actor,
            role_binding=self.context.role_binding,
        )
        self.assertTrue(self.ensure(switched))
        grants_after_switch = identities.grants_for_subject(self.subject)
        self.assertTrue(any(grant.scope == switched.scope for grant in grants_after_switch))

        for grant in grants_after_switch:
            identities.revoke_grant(
                grant.grant_id,
                revoked_at=self.clock.now(),
                expected_revision=identities.revision,
            )
        third = NS(
            scope=replace(self.scope, conversation_id="conversation-c"),
            actor=self.context.actor,
            role_binding=self.context.role_binding,
        )
        self.ensure(third)
        self.assertFalse(
            any(grant.scope == third.scope for grant in identities.grants_for_subject(self.subject))
        )

    def test_content_authorization_status_reflects_role_and_session_readiness(self):
        self.set_tier(AuthorizationTier.CORE)
        self.install_content()
        self.assertEqual(
            content_authorization_status(self.runtime),
            {
                "ready": False,
                "role_enabled_count": 0,
                "authorized_scope_count": 0,
                "issue": "capability_role_not_enabled",
            },
        )

        self.capabilities.set_role_enabled(
            extension_id=CONTENT_EXTENSION,
            role=self.role,
            bot_id=self.scope.bot_id,
            enabled=True,
            expected_revision=0,
            observed_at=NOW,
        ).unwrap()
        self.assertEqual(
            content_authorization_status(self.runtime),
            {
                "ready": False,
                "role_enabled_count": 1,
                "authorized_scope_count": 0,
                "issue": "capability_consent_missing",
            },
        )

        self.assertTrue(self.ensure())
        self.assertEqual(
            content_authorization_status(self.runtime),
            {
                "ready": True,
                "role_enabled_count": 1,
                "authorized_scope_count": 1,
                "issue": None,
            },
        )

        for capability in ("content.work.create", "content.work.save_candidate"):
            self.choose(CapabilityAuthorizationState.REVOKED, capability=capability)
        self.assertEqual(
            content_authorization_status(self.runtime),
            {
                "ready": False,
                "role_enabled_count": 1,
                "authorized_scope_count": 0,
                "issue": "capability_consent_missing",
            },
        )


class ContentAuthoringManagementAndPlanningTests(StateFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.profiles += (planning_profile("primary"),)
        self.registry = FieldRegistry(
            version="content-planning-tests", fields=configuration_fields(self.profiles)
        )

    def core_context(self, app):
        scope = replace(SCOPE, bot_id="bot-a")
        role_binding = self.identity.resolve_role(SCOPE, observed_at=NOW).unwrap()
        role_binding = replace(role_binding, scope=scope)
        return TaskContext(self.core, role_binding, AudienceRef(scope, (self.core.subject,)))

    def install_content(self, app, *, role=None, bot_id=None, enable_role=False):
        repo = CapabilityRepository(app.runtime)
        package = CapabilityPackageDescriptor(
            CONTENT_EXTENSION,
            "astrbot_plugin_private_companion.content",
            "companion",
            "1",
            "content-hash",
            CONTENT_CAPABILITIES,
        )
        repo.register_descriptor(package, registered_at=NOW).unwrap()
        repo.acquire(
            extension_id=CONTENT_EXTENSION,
            package_id=package.package_id,
            package_version=package.package_version,
            expected_revision=0,
            observed_at=NOW,
        ).unwrap()
        if enable_role and role is not None:
            repo.set_role_enabled(
                extension_id=CONTENT_EXTENSION,
                role=role,
                bot_id=bot_id,
                enabled=True,
                expected_revision=0,
                observed_at=NOW,
            ).unwrap()
        return repo

    async def test_content_authoring_management_heals_core_access_when_enabled(self):
        host, app, _, _ = self.setup_state()
        await self.start(host)
        context = self.core_context(app)
        self.grant(self.core, context.scope, "configuration.read")
        self.grant(self.core, context.scope, "configuration.write")
        self.install_content(app)
        editor = ContentAuthoringManagement(app, context)

        status = editor.status(self.core).unwrap()
        editor.configure(
            self.core,
            {
                "operation_key": "disable-authoring",
                "edit_token": status["edit_token"],
                "changes": {"enabled": False},
            },
        ).unwrap()
        self.assertEqual(
            content_authorization_status(app.runtime)["issue"], "capability_role_not_enabled"
        )

        status = editor.status(self.core).unwrap()
        editor.configure(
            self.core,
            {
                "operation_key": "enable-authoring",
                "edit_token": status["edit_token"],
                "changes": {"enabled": True, "direction_prompt": "偏好克制的短篇随笔"},
            },
        ).unwrap()
        self.assertTrue(content_authorization_status(app.runtime)["ready"])
        repo = CapabilityRepository(app.runtime)
        self.assertTrue(
            repo.require_role_enabled(
                extension_id=CONTENT_EXTENSION,
                role=context.role_binding.role,
                bot_id=context.scope.bot_id,
            ).is_success
        )
        for capability in CONTENT_CAPABILITIES:
            self.assertTrue(
                repo.authorize(
                    extension_id=CONTENT_EXTENSION,
                    capability_id=capability,
                    subject=context.actor.subject,
                    scope=context.scope,
                ).is_success,
                capability,
            )

    def test_planning_inputs_injects_compact_authoring_summary(self):
        _, app, context, manager = self.setup_state()
        self.configure(manager, calendar_enabled=True, planning_enabled=True)
        context_with_bot = replace(
            context,
            role_binding=replace(
                context.role_binding,
                scope=replace(context.scope, bot_id="bot-a"),
            ),
            audience=replace(context.audience, scope=replace(context.scope, bot_id="bot-a")),
        )
        app.state_maintenance.bind(context_with_bot, "原角色人格")
        self.grant(context_with_bot.actor, context_with_bot.scope, "calendar.write")
        self.grant(context_with_bot.actor, context_with_bot.scope, "calendar.read")

        # Without Content installed, planning material stays clean.
        without_content = capture(
            app.calendar_planning,
            context_with_bot,
            "原角色人格",
            "daily",
            "no-content",
            role_global=True,
        )
        self.assertNotIn("content_authoring", without_content["material"])

        repo = self.install_content(
            app,
            role=context_with_bot.role_binding.role,
            bot_id=context_with_bot.scope.bot_id,
            enable_role=True,
        )
        owner = SparseConfigurationOwner(app.runtime)
        target = ConfigTarget(
            context_with_bot.scope.installation_id,
            ConfigLayer.ROLE,
            context_with_bot.role_binding.role,
            bot_id=context_with_bot.scope.bot_id,
        )
        owner.write(
            target,
            AUTHORING_FIELDS,
            changes={
                AUTHORING_GROUP + ".enabled": True,
                AUTHORING_GROUP + ".direction_prompt": "偏好音乐与生活观察类短篇",
            },
            reset=(),
            expected_revision=owner.read(target, AUTHORING_FIELDS).revision,
            observed_at=NOW,
        )

        # Populate two works in works.sqlite3 (one finished 5 days ago, one drafting 2 days ago).
        works_root = content_database(app.runtime.data_root).parents[1]
        content_repo = ContentRepository(works_root)
        self.addCleanup(content_repo.close)
        service = ContentService(content_repo, NS(check=lambda _request: None))
        work_owner = WorkOwner(**owner_for(context_with_bot))
        contributor = JsonObject.capture({"kind": "role_output"})
        actor_doc = JsonObject.capture(document(context_with_bot.actor))
        role_doc = JsonObject.capture(document(context_with_bot.role_binding))
        audience_doc = JsonObject.capture(document(context_with_bot.audience))

        with patch(
            "companion_v2.content.service._now",
            return_value=(NOW - timedelta(days=5)).isoformat(),
        ):
            service.create(
                Call(
                    Origin(
                        "req-1",
                        JsonObject.capture({"schema": "test", "legacy_status": "finished"}),
                        actor_doc,
                        role_doc,
                        audience_doc,
                    ),
                    lambda: None,
                ),
                work_owner,
                operation_key="seed-finished",
                title="私密成品标题不应泄露",
                work_type="短篇小说",
                chapters=(Chapter("c1", "私密正文内容不应泄露"),),
                contributor=contributor,
            )
        with patch(
            "companion_v2.content.service._now",
            return_value=(NOW - timedelta(days=2)).isoformat(),
        ):
            service.create(
                Call(
                    Origin(
                        "req-2",
                        JsonObject.capture({"schema": "test", "legacy_status": "drafting"}),
                        actor_doc,
                        role_doc,
                        audience_doc,
                    ),
                    lambda: None,
                ),
                work_owner,
                operation_key="seed-drafting",
                title="私密连载标题不应泄露",
                work_type="短篇小说",
                chapters=(Chapter("c1", "私密连载正文不应泄露"),),
                contributor=contributor,
            )

        daily = capture(
            app.calendar_planning,
            context_with_bot,
            "原角色人格",
            "daily",
            "with-content-daily",
            role_global=True,
        )
        summary = daily["material"]["content_authoring"]
        self.assertEqual(
            summary,
            {
                "enabled": True,
                "direction_prompt": "偏好音乐与生活观察类短篇",
                "works_total": 2,
                "drafting_works": 1,
                "days_since_last_work": 2,
            },
        )
        encoded = json.dumps(summary, ensure_ascii=False, separators=(",", ":"))
        self.assertLess(len(encoded), 600)
        material_text = json.dumps(daily["material"], ensure_ascii=False)
        self.assertNotIn("私密成品标题不应泄露", material_text)
        self.assertNotIn("私密正文内容不应泄露", material_text)

        horizon = capture(
            app.calendar_planning,
            context_with_bot,
            "原角色人格",
            "horizon",
            "with-content-horizon",
            role_global=True,
        )
        self.assertEqual(horizon["material"]["content_authoring"], summary)

        # Disabled setting or disabled role suppresses injection.
        owner.write(
            target,
            AUTHORING_FIELDS,
            changes={AUTHORING_GROUP + ".enabled": False},
            reset=(),
            expected_revision=owner.read(target, AUTHORING_FIELDS).revision,
            observed_at=NOW,
        )
        disabled_cfg = capture(
            app.calendar_planning,
            context_with_bot,
            "原角色人格",
            "daily",
            "disabled-cfg",
            role_global=True,
        )
        self.assertNotIn("content_authoring", disabled_cfg["material"])

        owner.write(
            target,
            AUTHORING_FIELDS,
            changes={AUTHORING_GROUP + ".enabled": True},
            reset=(),
            expected_revision=owner.read(target, AUTHORING_FIELDS).revision,
            observed_at=NOW,
        )
        repo.set_role_enabled(
            extension_id=CONTENT_EXTENSION,
            role=context_with_bot.role_binding.role,
            bot_id=context_with_bot.scope.bot_id,
            enabled=False,
            expected_revision=1,
            observed_at=NOW,
        ).unwrap()
        disabled_role = capture(
            app.calendar_planning,
            context_with_bot,
            "原角色人格",
            "daily",
            "disabled-role",
            role_global=True,
        )
        self.assertNotIn("content_authoring", disabled_role["material"])

        self.assertIn("content_authoring", PLANNING_INSTRUCTION)
        self.assertIn("不必每天安排", PLANNING_INSTRUCTION)


if __name__ == "__main__":
    unittest.main()
