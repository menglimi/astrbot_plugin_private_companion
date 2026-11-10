"""Image quota gates: scope classification, local-day counting, and configured limits."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
import json
import unittest

from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.configuration.registry import FieldRegistry
from companion_v2.conversation.media.quota import (GROUP, PERCEPTION_TIMEZONE_FIELD, QUOTA_FIELDS,
                                                   ImageQuotaConfigurationOwner, ImageQuotaGate,
                                                   configuration_fields)
from companion_v2.conversation.media.output_settings import (GROUP as IMAGE_OUTPUT_GROUP,
    ImageOutputConfigurationOwner, configuration_fields as output_configuration_fields)
from companion_v2.execution.models import TaskError, stamp
from companion_v2.foundation.refs import ScopeKind
from companion_v2.storage.runtime import StorageRuntime
from tests.test_conversation_messages import MessageFixture
from tests.test_management_access import INSTALLATION, NOW, SCOPE


class ImageQuotaTests(MessageFixture):
    def parent(self, identifier="quota-parent", *, context=None, kind="conversation.reply"):
        return SimpleNamespace(spec=self.spec(identifier, kind=kind, context=context))

    def set_limit(self, scope, value, *, context=None):
        context = context or self.context
        owner = ImageQuotaConfigurationOwner(self.runtime)
        target = ConfigTarget(context.scope.installation_id, ConfigLayer.ROLE, context.role_binding.role,
                              bot_id=context.scope.bot_id)
        owner.write(target, QUOTA_FIELDS, changes={GROUP + "." + scope + "_max_daily": value}, reset=(),
                    expected_revision=owner.read(target, QUOTA_FIELDS).revision, observed_at=NOW)

    def set_output(self, **changes):
        target = ConfigTarget(self.context.scope.installation_id, ConfigLayer.ROLE,
                              self.context.role_binding.role, bot_id=self.context.scope.bot_id)
        fields = output_configuration_fields()
        owner = ImageOutputConfigurationOwner(self.runtime)
        owner.write(target, fields,
                    changes={IMAGE_OUTPUT_GROUP + "." + name: value for name, value in changes.items()},
                    reset=(), expected_revision=owner.read(target, fields).revision, observed_at=NOW)

    def group_context(self, actor=None):
        group_scope = replace(SCOPE, kind=ScopeKind.GROUP, native_scope_id="group-native",
                              conversation_id="group-conversation", session_id="group-session")
        return SimpleNamespace(scope=group_scope, actor=actor or self.context.actor,
                               role_binding=self.context.role_binding)

    def attempt(self, identifier, *, context=None, kind="conversation.reply", at=NOW, action="image.generate",
                native=False, result_state="succeeded"):
        context = context or self.context
        record = {"spec": {"context": {
            "role_binding": {"scope": {"installation_id": context.scope.installation_id,
                                       "bot_id": context.scope.bot_id},
                             "role": {"subject_id": context.role_binding.role.subject_id}},
            "actor": {"subject": {"kind": context.actor.subject.kind.value,
                                  "subject_id": context.actor.subject.subject_id}},
            "audience": {"scope": {"kind": context.scope.kind.value}},
        }}}
        with self.runtime.transaction() as connection:
            connection.execute("""INSERT INTO execution_tasks (task_id, origin_key, kind, lane, dedupe_key,
                request_hash, revision, state, generation, attempt_no, attempt_id, next_run_at, deadline_at,
                created_at, updated_at, record_json) VALUES (?, 'quota-origin', ?, 'foreground', ?, 'quota-hash',
                1, 'succeeded', 1, 0, NULL, ?, ?, ?, ?, ?)""",
                (identifier, kind, "dedupe-" + identifier, stamp(at), stamp(at), stamp(at), stamp(at),
                 json.dumps(record)))
            if native:
                connection.execute("INSERT INTO runtime_metadata (key, value) VALUES (?, ?)",
                    ("astrbot.core-tool." + identifier,
                     json.dumps({"task_id": identifier, "action": action, "state": result_state})))
                return
            connection.execute("INSERT INTO model_requests (task_id, request_hash, plan_json, created_at) VALUES (?, 'hash', '{}', ?)",
                               (identifier, stamp(at)))
            connection.execute("""INSERT INTO conversation_batches (batch_id, origin_key, context_json, role_instruction,
                state, revision, created_at, contributions_json) VALUES (?, 'quota-origin', '{}', '', 'open', 1, ?, '{}')""",
                ("batch-" + identifier, stamp(at)))
            connection.execute("""INSERT INTO execution_tasks (task_id, origin_key, kind, lane, dedupe_key,
                request_hash, revision, state, generation, attempt_no, attempt_id, next_run_at, deadline_at,
                created_at, updated_at, record_json) VALUES (?, 'quota-origin', ?, 'background', ?, 'quota-hash',
                1, 'succeeded', 1, 0, NULL, ?, ?, ?, ?, '{}')""",
                ("child-" + identifier, "image.generate", "dedupe-child-" + identifier,
                 stamp(at), stamp(at), stamp(at), stamp(at)))
            connection.execute("""INSERT INTO extension_tool_invocations (invocation_id, parent_task_id, tool_call_id,
                batch_id, task_id, action, descriptor_json, provider_json, arguments_json, state, effect, created_at)
                VALUES (?, ?, 'call-1', ?, ?, ?, '{}', '{}', '{}', ?, 'none', ?)""",
                ("inv-" + identifier, identifier, "batch-" + identifier, "child-" + identifier, action,
                 result_state, stamp(at)))

    def test_registry_contract_and_unlimited_default(self):
        fields = configuration_fields()
        self.assertEqual(tuple(f.field_id for f in fields),
                         tuple(GROUP + "." + name for name in ("private_owner_max_daily", "private_friend_max_daily",
                                                               "group_max_daily", "proactive_max_daily")))
        for field in fields:
            self.assertEqual((field.default, field.minimum, field.maximum), (-1, -1, 100))
        FieldRegistry(version="quota.v1", fields=fields)
        self.messages.image_quota.admit(self.parent(), NOW)

    def test_zero_limit_closes_scope(self):
        self.set_limit("private_friend", 0)
        with self.assertRaises(TaskError) as caught:
            self.messages.image_quota.admit(self.parent(), NOW)
        self.assertEqual(caught.exception.code, "image_quota_closed")

    def test_limit_blocks_after_confirmed_successes(self):
        self.set_limit("private_friend", 2)
        gate = self.messages.image_quota
        gate.admit(self.parent(), NOW)
        self.attempt("quota-a1")
        gate.admit(self.parent(), NOW)
        self.attempt("quota-a2")
        with self.assertRaises(TaskError) as caught:
            gate.admit(self.parent(), NOW)
        self.assertEqual(caught.exception.code, "image_quota_exhausted")

    def test_failed_and_unknown_image_results_do_not_consume_quota(self):
        self.set_limit("private_friend", 1)
        self.attempt("quota-failed", result_state="failed")
        self.attempt("quota-unknown", result_state="unknown")
        self.attempt("quota-native-failed", native=True, result_state="failed")
        self.attempt("quota-native-unknown", native=True, result_state="unknown")

        gate = self.messages.image_quota
        self.assertEqual(gate._used(self.context, "private_friend", NOW), 0)
        gate.admit(self.parent(), NOW)

        self.attempt("quota-native-success", native=True, result_state="succeeded")
        self.assertEqual(gate._used(self.context, "private_friend", NOW), 1)
        with self.assertRaises(TaskError) as caught:
            gate.admit(self.parent("quota-after-success"), NOW)
        self.assertEqual(caught.exception.code, "image_quota_exhausted")

    def test_rule_mode_does_not_apply_a_second_daily_cap(self):
        gate = self.messages.image_quota
        for number in range(4):
            self.attempt("quota-rule-" + str(number))
        self.assertEqual(gate._used(self.context, "private_friend", NOW), 4)
        gate.admit(self.parent("quota-rule-next"), NOW)

    def test_gallery_attachment_cap_does_not_limit_proactive_image_generation(self):
        context = self.context_for(self.core)
        fields = output_configuration_fields()
        owner = ImageOutputConfigurationOwner(self.runtime)
        target = ConfigTarget(context.scope.installation_id, ConfigLayer.ROLE,
                              context.role_binding.role, bot_id=context.scope.bot_id)
        owner.write(target, fields,
            changes={IMAGE_OUTPUT_GROUP + ".proactive_max_daily": 0}, reset=(),
            expected_revision=owner.read(target, fields).revision, observed_at=NOW)

        self.messages.image_quota.admit(
            self.parent("quota-proactive-generation", context=context, kind="proactive.compose"), NOW)

    def test_attempts_before_local_midnight_do_not_count(self):
        self.set_limit("private_friend", 1)
        self.attempt("quota-old", at=NOW - timedelta(days=1))
        self.messages.image_quota.admit(self.parent(), NOW)

    def test_native_and_legacy_user_attempts_share_daily_scope_limits(self):
        self.set_limit("private_friend", 2)
        self.attempt("quota-legacy")
        self.attempt("quota-native-old", kind="astrbot.core-tool.image", at=NOW - timedelta(days=1), native=True)
        self.attempt("quota-native", kind="astrbot.core-tool.image", native=True)
        gate = self.messages.image_quota
        self.assertEqual(gate._used(self.context, "private_friend", NOW), 2)
        with self.assertRaises(TaskError) as caught:
            gate.admit_native(self.context, NOW)
        self.assertEqual(caught.exception.code, "image_quota_exhausted")
        self.assertEqual(gate._used(self.context_for(self.core), "private_owner", NOW), 0)

    def test_day_boundary_follows_the_calendar_timezone(self):
        """A day must reset at the same midnight the role's calendar uses.

        NOW is 2026-09-21T00:00Z and the attempt is at 11:00Z, which sits between
        the two boundaries: UTC+14 starts its day at 10:00Z so the attempt is
        today and the allowance is spent, while UTC+8 starts at 16:00Z so the same
        attempt is yesterday and nothing is counted.
        """
        from companion_v2.conversation.perception_settings import (PerceptionConfigurationOwner,
            configuration_fields as perception_fields, target_for as perception_target_for)
        fields = perception_fields()
        owner = PerceptionConfigurationOwner(self.runtime)
        target = perception_target_for(self.context)

        def set_zone(name):
            owner.write(target, fields, changes={PERCEPTION_TIMEZONE_FIELD: name}, reset=(),
                        expected_revision=owner.read(target, fields).revision, observed_at=NOW)

        self.set_limit("private_friend", 1)
        self.attempt("quota-zone-attempt", at=NOW - timedelta(hours=13))
        set_zone("Pacific/Kiritimati")
        self.assertEqual(self.messages.image_quota._used(self.context, "private_friend", NOW), 1)
        with self.assertRaises(TaskError) as caught:
            self.messages.image_quota.admit(self.parent("quota-zone-ahead"), NOW)
        self.assertEqual(caught.exception.code, "image_quota_exhausted")
        set_zone("Asia/Shanghai")
        self.assertEqual(self.messages.image_quota._day_start(NOW, self.context).isoformat(),
                         "2026-09-20T16:00:00+00:00")
        self.assertEqual(self.messages.image_quota._used(self.context, "private_friend", NOW), 0)
        self.messages.image_quota.admit(self.parent("quota-zone-behind"), NOW)

    def test_core_and_standard_subjects_hold_separate_quotas(self):
        core_context = self.context_for(self.core)
        self.set_limit("private_owner", 0, context=core_context)
        self.messages.image_quota.admit(self.parent(), NOW)
        with self.assertRaises(TaskError) as caught:
            self.messages.image_quota.admit(self.parent("quota-core", context=core_context), NOW)
        self.assertEqual(caught.exception.code, "image_quota_closed")

    def test_proactive_parent_kind_counts_separately(self):
        core_context = self.context_for(self.core)
        self.set_limit("proactive", 1)
        self.attempt("quota-proactive", context=core_context, kind="proactive.compose")
        with self.assertRaises(TaskError) as caught:
            self.messages.image_quota.admit(self.parent("quota-p", context=core_context, kind="proactive.compose"), NOW)
        self.assertEqual(caught.exception.code, "image_quota_exhausted")
        self.set_limit("private_owner", 1, context=core_context)
        self.messages.image_quota.admit(self.parent("quota-reply", context=core_context), NOW)

    def test_group_image_switch_blocks_group_requests_without_affecting_private(self):
        self.set_output(group_enabled=False)
        gate = self.messages.image_quota
        group_parent = SimpleNamespace(spec=SimpleNamespace(kind="conversation.reply",
                                                            context=self.group_context()))
        with self.assertRaises(TaskError) as caught:
            gate.admit(group_parent, NOW)
        self.assertEqual(caught.exception.code, "group_image_requests_disabled")
        gate.admit(self.parent(), NOW)

    def test_core_only_image_permission_blocks_standard_requesters_and_allows_core(self):
        self.set_output(request_permission="core_only")
        gate = self.messages.image_quota
        with self.assertRaises(TaskError) as caught, \
                self.assertLogs("companion_v2.conversation.media.quota", level="INFO") as logs:
            gate.admit(self.parent(), NOW)
        self.assertEqual(caught.exception.code, "image_requests_core_only")
        # A real dispatch attempt is the one place worth an INFO notice.
        self.assertTrue(any("生图请求被拒" in line for line in logs.output))
        core_context = self.context_for(self.core)
        gate.admit(self.parent("quota-core-image-permission", context=core_context), NOW)

    def test_request_policy_does_not_block_proactive_image_generation(self):
        core_context = self.context_for(self.core)
        self.set_output(group_enabled=False, request_permission="core_only")
        gate = self.messages.image_quota
        gate.admit(self.parent("quota-proactive-policy", context=core_context,
                               kind="proactive.compose"), NOW)

    def test_group_scope_counts_separately_from_private(self):
        gate = self.messages.image_quota
        group_scope = replace(SCOPE, kind=ScopeKind.GROUP, native_scope_id="group-native",
                              conversation_id="group-conversation", session_id="group-session")
        group_context = SimpleNamespace(scope=group_scope, actor=self.context.actor,
                                        role_binding=self.context.role_binding)
        group_parent = SimpleNamespace(spec=SimpleNamespace(kind="conversation.reply", context=group_context))
        self.set_limit("group", 0)
        with self.assertRaises(TaskError) as caught:
            gate.admit(group_parent, NOW)
        self.assertEqual(caught.exception.code, "image_quota_closed")
        self.set_limit("private_friend", 1)
        gate.admit(self.parent(), NOW)

    def test_counts_survive_runtime_restart(self):
        self.set_limit("private_friend", 1)
        self.attempt("quota-restart")
        self.runtime.close()
        second = StorageRuntime.open(data_root=self.root, installation_id=INSTALLATION)
        self.addCleanup(second.close)
        gate = ImageQuotaGate(SimpleNamespace(runtime=second, timezone_name="Asia/Shanghai"))
        with self.assertRaises(TaskError) as caught:
            gate.admit(self.parent(), NOW)
        self.assertEqual(caught.exception.code, "image_quota_exhausted")


if __name__ == "__main__":
    unittest.main()
