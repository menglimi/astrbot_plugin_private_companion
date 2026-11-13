"""Optional runtime settings must not require the participant to be an administrator."""

import unittest
from dataclasses import replace

from companion_v2.configuration.models import ConfigLayer, ConfigTarget, encode
from companion_v2.execution.models import TaskError
from companion_v2.foundation.refs import AudienceRef
from companion_v2.identity_policy.models import AuthorizationTier
from companion_v2.identity_policy.personas import ConversationRoute
from companion_v2.role_work.state.configuration import (DEFAULTS, EXPRESSION_PROMPTS, GROUP, OWNER,
    configuration_fields, ensure_window_presentation_grants, ensure_role_state_grants,
    migrate_presence_to_role, role_target_for, target_for, validate_expression_prompts,
    validate_grant_scope)
from tests.test_host_application import HostFixture


class RoleStateConfigurationTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    def test_state_expression_prompts_are_registered_and_bounded(self):
        fields = {field.field_id: field for field in configuration_fields()}
        for name in EXPRESSION_PROMPTS:
            self.assertEqual(DEFAULTS[name], "")
            self.assertIn(GROUP + "." + name, fields)
        validate_expression_prompts({**DEFAULTS, "energy_expression_prompt": "随活动自然变化"})
        for value in (None, 7, "x" * 513):
            with self.assertRaises(Exception):
                validate_expression_prompts({**DEFAULTS, "health_expression_prompt": value})

    async def test_standard_participant_consumes_core_settings_without_management_access(self):
        host = await self.start()
        app = host.application
        batch = host.receive(self.event()).unwrap()
        context = self.context(app, batch)
        self.policy.set_tier(self.important.subject, AuthorizationTier.STANDARD,
            expected_revision=self.policy.revision, observed_at=self.clock.now())
        self.grant(self.important, context.scope, "configuration.read")
        self.grant(self.important, context.scope, "configuration.write")
        values, token = app.role_state.settings(context)
        self.assertFalse(values["enabled"])
        self.assertFalse(values["typing_enabled"])
        self.assertEqual(values["typing_refresh_seconds"], 4)
        self.assertEqual(app.configuration.read(context.actor, target=target_for(context), group_id=GROUP,
            observed_at=self.clock.now()).failure.code, "core_access_required")
        snapshot = app.configuration.read(self.core, target=role_target_for(context), group_id=GROUP,
            observed_at=self.clock.now()).unwrap()
        changes = {"role.state.maintenance_minutes": 45}
        role_target = role_target_for(context)
        denied = app.configuration.patch(context.actor, target=role_target, group_id=GROUP,
            operation=self.operation(role_target.authorization_scope, owner=OWNER), expected_edit_token=snapshot.edit_token,
            changes=changes, observed_at=self.clock.now())
        self.assertEqual(denied.failure.code, "scope_not_granted")
        app.configuration.patch(self.core, target=role_target, group_id=GROUP,
            operation=self.operation(role_target.authorization_scope, owner=OWNER), expected_edit_token=snapshot.edit_token,
            changes=changes, observed_at=self.clock.now()).unwrap()
        changed, updated = app.role_state.settings(context)
        self.assertEqual(changed["maintenance_minutes"], 45)
        self.assertNotEqual(token, updated)
        self.assertEqual(self.called, [])
        self.assertEqual(self.sent, [])

    async def test_runtime_settings_reject_an_obsolete_account_binding(self):
        host = await self.start()
        app = host.application
        batch = host.receive(self.event()).unwrap()
        context = self.context(app, batch)
        self.identity.bind(binding_id="rebound", principal=context.actor.principal,
            subject=context.actor.subject, valid_from=self.clock.now(), expected_revision=self.identity.revision)
        with self.assertRaises(TaskError) as caught:
            app.role_state.settings(context)
        self.assertEqual(caught.exception.code, "binding_rebound")

    async def test_role_defaults_are_shared_and_window_values_override_them_locally(self):
        host = await self.start()
        app = host.application
        batch = host.receive(self.event()).unwrap()
        context = self.context(app, batch)
        role_target = role_target_for(context)
        role_before = app.configuration.read(self.core, target=role_target, group_id=GROUP,
            observed_at=self.clock.now()).unwrap()
        role_saved = app.configuration.patch(self.core, target=role_target, group_id=GROUP,
            operation=self.operation(role_target.authorization_scope, owner=OWNER),
            expected_edit_token=role_before.edit_token,
            changes={GROUP + ".maintenance_minutes": 45}, observed_at=self.clock.now()).unwrap()
        self.assertFalse(role_saved.replayed)

        effective = app.role_state.settings(context)[0]
        self.assertEqual(effective["maintenance_minutes"], 45)

        window_target = target_for(context)
        window_before = app.configuration.read(self.core, target=window_target, group_id=GROUP,
            observed_at=self.clock.now()).unwrap()
        self.assertEqual(window_before.fields[GROUP + ".maintenance_minutes"].source, role_target)
        self.assertFalse(window_before.fields[GROUP + ".maintenance_minutes"].explicit)
        local = app.configuration.patch(self.core, target=window_target, group_id=GROUP,
            operation=self.operation(window_target.authorization_scope, owner=OWNER),
            expected_edit_token=window_before.edit_token,
            changes={GROUP + ".maintenance_minutes": 20}, observed_at=self.clock.now())
        self.assertTrue(local.is_success, local.failure if local.failure else "")
        self.assertEqual(app.role_state.settings(context)[0]["maintenance_minutes"], 20)

        other_scope = replace(context.scope, native_scope_id="other-window",
            conversation_id="other-conversation", session_id="other-session")
        self.personas.register_route(ConversationRoute(other_scope, user=context.actor.subject),
                                     observed_at=self.clock.now())
        other_target = ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW,
            context.role_binding.role, other_scope)
        other = app.configuration.read(self.core, target=other_target, group_id=GROUP,
            observed_at=self.clock.now()).unwrap()
        self.assertEqual(other.values[GROUP + ".maintenance_minutes"], 45)
        app.configuration.patch(self.core, target=window_target, group_id=GROUP,
            operation=self.operation(window_target.authorization_scope, owner=OWNER),
            expected_edit_token=app.configuration.read(self.core, target=window_target, group_id=GROUP,
                observed_at=self.clock.now()).unwrap().edit_token,
            changes={GROUP + ".typing_enabled": True}, observed_at=self.clock.now()).unwrap()
        self.assertTrue(app.configuration.read(self.core, target=window_target, group_id=GROUP,
            observed_at=self.clock.now()).unwrap().values[GROUP + ".typing_enabled"])
        self.assertFalse(app.configuration.read(self.core, target=other_target, group_id=GROUP,
            observed_at=self.clock.now()).unwrap().values[GROUP + ".typing_enabled"])
        role_current = app.configuration.read(self.core, target=role_target, group_id=GROUP,
            observed_at=self.clock.now()).unwrap()
        self.assertEqual(role_current.values[GROUP + ".maintenance_minutes"], 45)


    async def test_presence_is_role_shared_and_window_writes_are_rejected(self):
        host = await self.start()
        app = host.application
        batch = host.receive(self.event()).unwrap()
        context = self.context(app, batch)
        role_target = role_target_for(context)
        role_before = app.configuration.read(self.core, target=role_target, group_id=GROUP,
            observed_at=self.clock.now()).unwrap()
        app.configuration.patch(self.core, target=role_target, group_id=GROUP,
            operation=self.operation(role_target.authorization_scope, owner=OWNER),
            expected_edit_token=role_before.edit_token,
            changes={GROUP + ".presence_enabled": True, GROUP + ".online_status_enabled": True},
            observed_at=self.clock.now()).unwrap()

        other_scope = replace(context.scope, native_scope_id="other-window",
            conversation_id="other-conversation", session_id="other-session")
        self.personas.register_route(ConversationRoute(other_scope, user=self.important.subject),
                                     observed_at=self.clock.now())
        other_target = ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW,
            context.role_binding.role, other_scope)
        inherited = app.configuration.read(self.core, target=other_target, group_id=GROUP,
            observed_at=self.clock.now()).unwrap()
        self.assertTrue(inherited.values[GROUP + ".presence_enabled"])
        self.assertTrue(inherited.values[GROUP + ".online_status_enabled"])
        self.assertEqual(inherited.fields[GROUP + ".presence_enabled"].source, role_target)

        other_context = replace(context,
            role_binding=replace(context.role_binding, scope=other_scope),
            audience=AudienceRef(other_scope, (context.actor.subject,)))
        ensure_window_presentation_grants(app.runtime, other_context, observed_at=self.clock.now())
        self.assertTrue(self.identity.authorize(self.important, scope=other_scope,
            capability="platform.presence", observed_at=self.clock.now()).is_success)

        window_target = target_for(context)
        window_before = app.configuration.read(self.core, target=window_target, group_id=GROUP,
            observed_at=self.clock.now()).unwrap()
        denied = app.configuration.patch(self.core, target=window_target, group_id=GROUP,
            operation=self.operation(window_target.authorization_scope, owner=OWNER),
            expected_edit_token=window_before.edit_token,
            changes={GROUP + ".presence_enabled": False}, observed_at=self.clock.now())
        self.assertEqual(denied.failure.code, "configuration_scope_not_supported")

    def test_grant_scope_is_bounded(self):
        validate_grant_scope({**DEFAULTS, "maintenance_grant_scope": "window"})
        validate_grant_scope({**DEFAULTS, "maintenance_grant_scope": "role"})
        with self.assertRaises(Exception):
            validate_grant_scope({**DEFAULTS, "maintenance_grant_scope": "all"})

    async def test_role_grant_scope_reissues_state_grants_for_a_second_window(self):
        host = await self.start()
        app = host.application
        batch = host.receive(self.event()).unwrap()
        context = self.context(app, batch)
        self.assertFalse(self.identity.authorize(self.important, scope=context.scope,
            capability="role.state.maintain", observed_at=self.clock.now()).is_success)

        role_target = role_target_for(context)
        before = app.configuration.read(self.core, target=role_target, group_id=GROUP,
            observed_at=self.clock.now()).unwrap()
        app.configuration.patch(self.core, target=role_target, group_id=GROUP,
            operation=self.operation(role_target.authorization_scope, owner=OWNER),
            expected_edit_token=before.edit_token,
            changes={GROUP + ".enabled": True}, observed_at=self.clock.now()).unwrap()

        other_scope = replace(context.scope, native_scope_id="other-window",
            conversation_id="other-conversation", session_id="other-session")
        other_context = replace(context,
            role_binding=replace(context.role_binding, scope=other_scope),
            audience=AudienceRef(other_scope, (context.actor.subject,)))
        ensure_role_state_grants(app.runtime, other_context, observed_at=self.clock.now())
        for capability in ("role.state.read", "role.state.maintain"):
            self.assertTrue(self.identity.authorize(self.important, scope=other_scope,
                capability=capability, observed_at=self.clock.now()).is_success)

        revision = self.identity.revision
        ensure_role_state_grants(app.runtime, other_context, observed_at=self.clock.now())
        self.assertEqual(self.identity.revision, revision)

    async def test_window_grant_scope_does_not_cross_authorize_a_second_window(self):
        host = await self.start()
        app = host.application
        batch = host.receive(self.event()).unwrap()
        context = self.context(app, batch)
        role_target = role_target_for(context)
        before = app.configuration.read(self.core, target=role_target, group_id=GROUP,
            observed_at=self.clock.now()).unwrap()
        app.configuration.patch(self.core, target=role_target, group_id=GROUP,
            operation=self.operation(role_target.authorization_scope, owner=OWNER),
            expected_edit_token=before.edit_token,
            changes={GROUP + ".enabled": True, GROUP + ".maintenance_grant_scope": "window"},
            observed_at=self.clock.now()).unwrap()

        other_scope = replace(context.scope, native_scope_id="other-window",
            conversation_id="other-conversation", session_id="other-session")
        other_context = replace(context,
            role_binding=replace(context.role_binding, scope=other_scope),
            audience=AudienceRef(other_scope, (context.actor.subject,)))
        ensure_role_state_grants(app.runtime, other_context, observed_at=self.clock.now())
        self.assertFalse(self.identity.authorize(self.important, scope=other_scope,
            capability="role.state.maintain", observed_at=self.clock.now()).is_success)

    async def test_legacy_window_presence_is_promoted_to_role_defaults(self):
        host = await self.start()
        app = host.application
        batch = host.receive(self.event()).unwrap()
        context = self.context(app, batch)
        window_target = target_for(context)
        role_target = role_target_for(context)
        with self.runtime.transaction() as connection:
            current = connection.execute(
                "SELECT revision,updated_at FROM configuration_targets WHERE target_key=?",
                (window_target.key,)).fetchone()
            revision = (current["revision"] if current else 0) + 1
            updated_at = self.clock.now().isoformat()
            connection.execute("INSERT OR IGNORE INTO configuration_targets VALUES (?,?,0,?)",
                (window_target.key, encode(window_target.document()), updated_at))
            connection.execute("INSERT OR REPLACE INTO configuration_values VALUES (?,?,?,?)",
                (window_target.key, GROUP + ".presence_enabled", "true", revision))
            connection.execute("UPDATE configuration_targets SET revision=?,updated_at=? WHERE target_key=?",
                (revision, updated_at, window_target.key))
        promoted = migrate_presence_to_role(self.runtime)
        self.assertIn(GROUP + ".presence_enabled", promoted)
        role_snapshot = app.configuration.read(self.core, target=role_target, group_id=GROUP,
            observed_at=self.clock.now()).unwrap()
        self.assertTrue(role_snapshot.values[GROUP + ".presence_enabled"])
        self.assertEqual(role_snapshot.fields[GROUP + ".presence_enabled"].source, role_target)

    async def test_state_simulation_switch_is_role_only(self):
        host = await self.start()
        app = host.application
        batch = host.receive(self.event()).unwrap()
        context = self.context(app, batch)
        role_target = role_target_for(context)
        role_before = app.configuration.read(self.core, target=role_target, group_id=GROUP,
            observed_at=self.clock.now()).unwrap()
        app.configuration.patch(self.core, target=role_target, group_id=GROUP,
            operation=self.operation(role_target.authorization_scope, owner=OWNER),
            expected_edit_token=role_before.edit_token, changes={GROUP + ".enabled": True},
            observed_at=self.clock.now()).unwrap()

        other_scope = replace(context.scope, native_scope_id="other-window",
            conversation_id="other-conversation", session_id="other-session")
        self.personas.register_route(ConversationRoute(other_scope, user=context.actor.subject),
                                     observed_at=self.clock.now())
        other_target = ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW,
            context.role_binding.role, other_scope)
        inherited = app.configuration.read(self.core, target=other_target, group_id=GROUP,
            observed_at=self.clock.now()).unwrap()
        self.assertTrue(inherited.values[GROUP + ".enabled"])
        self.assertEqual(inherited.fields[GROUP + ".enabled"].source, role_target)
        for field, value in (("enabled", False), ("maintenance_grant_scope", "role")):
            denied = app.configuration.patch(self.core, target=other_target, group_id=GROUP,
                operation=self.operation(other_target.authorization_scope, owner=OWNER),
                expected_edit_token=inherited.edit_token, changes={GROUP + "." + field: value},
                observed_at=self.clock.now())
            self.assertEqual(denied.failure.code, "configuration_scope_not_supported")


if __name__ == "__main__":
    unittest.main()
