"""Real persisted Image defaults retain explicit decisions and native route isolation."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import patch

from astrbot_host.image_access import GROUP_IMAGE_CAPABILITIES, IMAGE_CAPABILITIES, ensure_image_access
from companion_v2.capabilities import CapabilityAuthorizationState, CapabilityPackageDescriptor
from companion_v2.capabilities.repositories import CapabilityRepository
from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.conversation.media.output_settings import (
    GROUP as IMAGE_OUTPUT_GROUP, ImageOutputConfigurationOwner, configuration_fields,
)
from companion_v2.foundation.refs import ScopeKind
from companion_v2.identity_policy.models import AuthorizationTier
from companion_v2.identity_policy.repository import PersistentIdentityDirectory
from tests.g6_support import G6Fixture, NOW


class ImageAccessTests(G6Fixture):
    def setUp(self):
        super().setUp()
        self.scope = replace(self.scope, bot_id="bot-a", account_id="100",
                             conversation_id="conversation-a", session_id="200")
        self.context = NS(scope=self.scope, actor=NS(subject=self.subject), role_binding=NS(role=self.role))
        self.package = CapabilityPackageDescriptor(
            "image", "native-image", "companion", "1", "image-hash", IMAGE_CAPABILITIES)
        self.capabilities.register_descriptor(self.package, registered_at=NOW).unwrap()

    def install_image(self):
        self.capabilities.acquire(extension_id="image", package_id=self.package.package_id,
            package_version=self.package.package_version, expected_revision=0, observed_at=NOW).unwrap()

    def ensure(self, context=None):
        ensure_image_access(self.runtime, context or self.context, observed_at=self.clock.now())

    def consent(self, capability="image.generate", context=None):
        context = context or self.context
        return self.capabilities.consent(extension_id="image", capability_id=capability,
                                         subject=context.actor.subject, scope=context.scope)

    def choose(self, state, *, revision=1, context=None):
        context = context or self.context
        return self.capabilities.set_authorization(extension_id="image", capability_id="image.generate",
            subject=context.actor.subject, scope=context.scope, state=state, expected_revision=revision,
            observed_at=self.clock.now()).unwrap()

    def records(self):
        return tuple(tuple(tuple(row) for row in self.runtime.read_all("SELECT * FROM " + table, ()))
                     for table in ("capability_role_selections", "capability_authorizations"))

    def group_context(self, **changes):
        scope = replace(self.scope, kind=ScopeKind.GROUP, native_scope_id="group-1",
                        session_id="group-1", conversation_id="group-conversation", **changes)
        return NS(scope=scope, actor=self.context.actor, role_binding=self.context.role_binding)

    def set_image_output(self, **changes):
        target = ConfigTarget(self.scope.installation_id, ConfigLayer.ROLE, self.role,
                              bot_id=self.scope.bot_id)
        fields = configuration_fields()
        owner = ImageOutputConfigurationOwner(self.runtime)
        owner.write(target, fields,
            changes={IMAGE_OUTPUT_GROUP + "." + name: value for name, value in changes.items()},
            reset=(), expected_revision=owner.read(target, fields).revision, observed_at=self.clock.now())

    def test_missing_installation_can_be_repaired_after_native_start(self):
        self.ensure()
        self.assertEqual(self.records(), ((), ()))
        self.install_image()
        self.ensure()
        self.assertEqual(len(self.records()[0]), 1)
        self.assertEqual(len(self.records()[1]), 3)
        for capability in IMAGE_CAPABILITIES:
            self.assertEqual(self.consent(capability).unwrap().state, CapabilityAuthorizationState.ACTIVE)
        before = self.records()
        self.clock.advance(timedelta(minutes=1))
        self.ensure()
        self.assertEqual(self.records(), before)

    def test_explicit_role_disable_is_not_retried_or_overwritten(self):
        self.install_image()
        self.capabilities.set_role_enabled(extension_id="image", role=self.role, bot_id=self.scope.bot_id,
            enabled=False, expected_revision=0, observed_at=NOW).unwrap()
        before = self.records()
        with self.assertNoLogs("astrbot_host.image_access", level="WARNING"):
            self.ensure()
        self.assertEqual(self.records(), before)

    def test_revocation_survives_repeat_and_storage_reopen(self):
        self.install_image()
        self.ensure()
        self.choose(CapabilityAuthorizationState.REVOKED)
        before = self.records()
        self.runtime.close()
        self.runtime = self.open_runtime()
        self.capabilities = CapabilityRepository(self.runtime)
        with self.assertNoLogs("astrbot_host.image_access", level="WARNING"):
            self.ensure()
        self.assertEqual(self.records(), before)
        self.assertEqual(self.consent().failure.code, "capability_consent_revoked")

    def test_paused_or_removing_module_does_not_gain_defaults(self):
        self.install_image()
        self.capabilities.pause(extension_id="image", expected_revision=1, observed_at=NOW).unwrap()
        self.ensure()
        self.assertEqual(self.records(), ((), ()))
        self.capabilities.request_discard(extension_id="image", expected_revision=2, observed_at=NOW).unwrap()
        self.ensure()
        self.assertEqual(self.records(), ((), ()))

    def test_group_uses_its_own_policy_gated_defaults_without_private_inheritance(self):
        self.install_image()
        group = self.group_context()
        self.ensure(group)
        for capability in GROUP_IMAGE_CAPABILITIES:
            self.assertEqual(self.consent(capability, context=group).unwrap().state,
                             CapabilityAuthorizationState.ACTIVE)
        self.assertEqual(self.consent("image.mask_edit", context=group).failure.code,
                         "capability_consent_missing")
        self.assertEqual(self.consent().failure.code, "capability_consent_missing")
        before = self.records()
        self.ensure(group)
        self.assertEqual(self.records(), before)

    def test_group_policy_denials_do_not_provision_image_consent(self):
        self.install_image()
        group = self.group_context()
        self.set_image_output(group_enabled=False)
        # The turn-level probe runs on every group message; it must not tell the
        # operator that an image request was refused when nobody asked for one.
        with self.assertNoLogs("astrbot_host.image_access", level="INFO"):
            self.ensure(group)
        self.assertEqual(self.consent("image.generate", context=group).failure.code,
                         "capability_consent_missing")
        self.set_image_output(group_enabled=True, request_permission="core_only")
        with self.assertNoLogs("astrbot_host.image_access", level="INFO"):
            self.ensure(group)
        for capability in GROUP_IMAGE_CAPABILITIES:
            self.assertEqual(self.consent(capability, context=group).failure.code,
                             "capability_consent_missing")

    def test_group_revocation_survives_repeat_and_does_not_touch_private(self):
        self.install_image()
        group = self.group_context()
        self.ensure(group)
        result = self.capabilities.set_authorization(extension_id="image",
            capability_id="image.generate", subject=group.actor.subject, scope=group.scope,
            state=CapabilityAuthorizationState.REVOKED, expected_revision=1,
            observed_at=self.clock.now()).unwrap()
        self.assertEqual(result.state, CapabilityAuthorizationState.REVOKED)
        self.ensure(group)
        self.assertEqual(self.consent("image.generate", context=group).failure.code,
                         "capability_consent_revoked")
        self.assertEqual(self.consent("image.edit", context=group).unwrap().state,
                         CapabilityAuthorizationState.ACTIVE)
        self.assertEqual(self.consent().failure.code, "capability_consent_missing")

    def test_new_conversation_keeps_revocation_without_implicit_cross_scope_access(self):
        self.install_image()
        self.ensure()
        self.choose(CapabilityAuthorizationState.REVOKED)
        new = NS(scope=replace(self.scope, conversation_id="new-conversation"),
                 actor=self.context.actor, role_binding=self.context.role_binding)
        self.assertEqual(self.consent(context=new).failure.code, "capability_consent_missing")
        self.ensure(new)
        self.assertEqual(self.consent(context=new).failure.code, "capability_consent_revoked")
        for capability in IMAGE_CAPABILITIES[1:]:
            self.assertTrue(self.consent(capability, new).is_success)
        # A later deliberate re-enable is the next conversation's default.
        self.clock.advance(timedelta(minutes=1))
        self.choose(CapabilityAuthorizationState.ACTIVE, context=new)
        latest = NS(scope=replace(self.scope, conversation_id="latest-conversation"),
                    actor=self.context.actor, role_binding=self.context.role_binding)
        self.ensure(latest)
        self.assertTrue(self.consent(context=latest).is_success)

    def test_route_choices_do_not_cross_user_bot_account_platform_or_window(self):
        self.install_image()
        self.ensure()
        self.choose(CapabilityAuthorizationState.REVOKED)
        contexts = [NS(scope=self.scope, actor=NS(subject=replace(self.subject, subject_id="other")),
                       role_binding=self.context.role_binding)]
        for field, value in (("bot_id", "bot-b"), ("account_id", "101"), ("platform", "other-platform"),
                             ("native_scope_id", "201"), ("session_id", "201")):
            contexts.append(NS(scope=replace(self.scope, **{field: value}),
                               actor=self.context.actor, role_binding=self.context.role_binding))
        for context in contexts:
            with self.subTest(scope=context.scope, subject=context.actor.subject):
                self.ensure(context)
                self.assertTrue(self.consent(context=context).is_success)
        self.assertEqual(self.consent().failure.code, "capability_consent_revoked")

    def test_failed_default_write_rolls_back_partial_role_selection(self):
        self.install_image()
        with patch.object(CapabilityRepository, "set_authorization", side_effect=RuntimeError("synthetic")), \
                self.assertLogs("astrbot_host.image_access", level="WARNING"):
            self.ensure()
        self.assertEqual(self.records(), ((), ()))

    def test_invocation_grant_is_idempotent_and_keeps_revocation_across_conversations(self):
        self.install_image()
        self.ensure()
        identities = PersistentIdentityDirectory(self.runtime)
        grants = identities.grants_for_subject(self.subject)
        self.assertEqual(len(grants), 1)
        self.assertEqual(grants[0].capability, "extension.tool.invoke")
        self.ensure()
        self.assertEqual(identities.grants_for_subject(self.subject), grants)
        identities.revoke_grant(grants[0].grant_id, revoked_at=self.clock.now(),
                                expected_revision=identities.revision)
        self.ensure()
        new = NS(scope=replace(self.scope, conversation_id="new-conversation"),
                 actor=self.context.actor, role_binding=self.context.role_binding)
        self.ensure(new)
        self.assertEqual(len(identities.grants_for_subject(self.subject)), 1)

    def test_expired_or_future_invocation_grants_are_not_refreshed(self):
        self.install_image()
        identities = PersistentIdentityDirectory(self.runtime)
        for label, starts, expires in (("expired", NOW - timedelta(hours=2), NOW - timedelta(hours=1)),
                                       ("future", NOW + timedelta(hours=1), None)):
            with self.subTest(label=label):
                scope = replace(self.scope, native_scope_id=label, session_id=label)
                identities.issue_grant(grant_id=label, subject=self.subject, scope=scope,
                    capability="extension.tool.invoke", tier=AuthorizationTier.STANDARD,
                    valid_from=starts, expires_at=expires, expected_revision=identities.revision)
                context = NS(scope=scope, actor=self.context.actor, role_binding=self.context.role_binding)
                before = identities.grants_for_subject(self.subject)
                self.ensure(context)
                self.ensure(NS(scope=replace(scope, conversation_id="new-conversation"),
                               actor=context.actor, role_binding=context.role_binding))
                self.assertEqual(identities.grants_for_subject(self.subject), before)

    def test_later_explicit_grant_restores_defaults_for_new_conversations(self):
        self.install_image()
        self.ensure()
        identities = PersistentIdentityDirectory(self.runtime)
        grant = identities.grants_for_subject(self.subject)[0]
        self.clock.advance(timedelta(minutes=1))
        identities.revoke_grant(grant.grant_id, revoked_at=self.clock.now(),
                                expected_revision=identities.revision)
        self.clock.advance(timedelta(minutes=1))
        identities.issue_grant(grant_id="explicit-reenable", subject=self.subject,
            scope=replace(self.scope, conversation_id="reenabled-conversation"),
            capability="extension.tool.invoke", tier=AuthorizationTier.STANDARD,
            valid_from=self.clock.now(), expected_revision=identities.revision)
        new = NS(scope=replace(self.scope, conversation_id="next-conversation"),
                 actor=self.context.actor, role_binding=self.context.role_binding)
        self.ensure(new)
        grants = identities.grants_for_subject(self.subject)
        self.assertTrue(any(grant.scope == new.scope for grant in grants))
