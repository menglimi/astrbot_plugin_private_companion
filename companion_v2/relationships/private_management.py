"""Private-member editors sharing the established configuration owners."""

import logging

from companion_v2.configuration.management import (
    AcquaintanceManagement, PrivateAcquaintanceManagement,
    PrivateAcquaintanceLearningManagement, ProactiveContactManagement,
    RelationshipProfileManagement, PrivateExpressionLearningManagement, RoleGrowthManagement,
)
from companion_v2.configuration.contract import contract_of, layers_of, provenance_of
from companion_v2.configuration.models import ConfigLayer, ConfigurationError
from companion_v2.conversation.reply_management import ReplyManagement
from companion_v2.conversation.reply_settings import GROUP as REPLY_GROUP, OWNER as REPLY_OWNER
from companion_v2.execution.models import TaskError, digest, require
from companion_v2.execution.service import outcome
from companion_v2.foundation.refs import OperationRef, ScopeKind, VersionRef
from companion_v2.proactive.configuration import target_for
from companion_v2.proactive.rhythm_configuration import relationship_target_for
from companion_v2.storage.models import OperationState
from companion_v2.storage.repositories import OperationRepository


PRIVATE_REPLY_FIELDS = frozenset((
    "enabled", "streaming", "streaming_mode", "llm_controlled", "rule_mode", "threshold",
    "min_segment_chars", "max_segments", "part_gap_ms", "part_gap_mode", "part_gap_min_ms",
    "part_gap_max_ms", "part_gap_log_base", "prompt", "split_words", "match_width_variants",
    "rule_regex", "style_prompt", "enable_humanized_expression", "humanized_expression_prompt",
    "content_cleanup_enabled", "content_cleanup_mode", "content_cleanup_scope", "content_cleanup_rule",
    "content_cleanup_words", "content_replacement_enabled", "content_replacements",
    "reply_quote_enabled", "reply_quote_first_part_only", "reply_quote_skip_chars",
    "image_position", "reply_mention_speaker_enabled", "forward_message_adaptation_enabled",
    "voice_enabled", "voice_trigger_mode", "voice_generation_mode", "voice_segment_mode",
    "voice_delivery_mode", "voice_position", "voice_language", "voice_foreign_text_mode",
    "voice_provider_id", "voice_zh_provider_id", "voice_ja_provider_id", "voice_en_provider_id",
    "voice_probability", "voice_cooldown_seconds", "voice_max_speech_chars", "voice_timeout_seconds",
    "voice_trigger_keywords", "voice_extra_prompt", "enable_private_image_self_recognition",
    "private_image_self_recognition_hint", "image_understanding_private_enabled",
    "video_understanding_private_enabled", "file_understanding_private_enabled",
    "enable_private_image_vision_cache", "private_image_vision_cache_max_items",
    "private_image_vision_custom_prompt", "private_image_vision_max_chars",
    "private_image_vision_wait_seconds", "private_image_provider_failure_cooldown_seconds",
    "video_understanding_enabled", "video_understanding_max_chars",
    "video_understanding_wait_seconds", "video_self_recognition_hint", "video_understanding_prompt",
    "file_understanding_enabled", "file_understanding_format_groups", "file_understanding_max_size_mb",
    "file_understanding_max_chars", "file_understanding_timeout_seconds", "file_understanding_prompt",
))


logger = logging.getLogger(__name__)


class PrivateContactManagement(ProactiveContactManagement):
    """This private window's contact preferences override the global defaults."""

    target_layer = ConfigLayer.WINDOW
    operation_prefix = "proactive.contact-window-config:"

    def _target(self):
        return target_for(self.context)

    def _authorize(self, operator, action):
        if (self.context.scope.kind is not ScopeKind.PRIVATE
                or self.context.audience.recipients != (self.context.actor.subject,)):
            raise TaskError(self._code("scope_invalid"), "select one original private participant")
        target = super()._authorize(operator, action)
        self.app.tasks._context(self.context, self.app.now())
        return target

    @outcome
    def status(self, operator):
        status = require(super().status(operator))
        schedule = getattr(self.app, "contact_schedule", None)
        if schedule is not None:
            status["schedule_diagnostic"] = schedule.diagnostic_for(
                self.context, at=self.app.now(), host_state=self.app.state)
        return status


class PrivateReplyManagement:
    """Private-window reply overrides inherit the selected role's defaults."""

    group = REPLY_GROUP
    owner = REPLY_OWNER
    operation_prefix = "reply-settings:"

    def __init__(self, app, context):
        self.app, self.context = app, context
        self.manager = ReplyManagement(app, context, layer=ConfigLayer.WINDOW)

    def _target(self):
        return self.manager.target

    def _snapshot(self, operator):
        if (self.context.scope.kind is not ScopeKind.PRIVATE
                or self.context.audience.recipients != (self.context.actor.subject,)):
            raise TaskError("reply_configuration_scope_invalid", "select one original private participant")
        return self.manager._snapshot(operator)

    @outcome
    def status(self, operator):
        with self.app.runtime.read_snapshot():
            snapshot = self._snapshot(operator)
            role_target = self.manager.target.__class__(
                self.context.scope.installation_id, ConfigLayer.ROLE,
                self.context.role_binding.role, bot_id=self.context.scope.bot_id,
            )
            inherited = require(self.app.configuration.read(operator, target=role_target,
                group_id=REPLY_GROUP, observed_at=self.app.now()))
            prefix = REPLY_GROUP + "."
            names = PRIVATE_REPLY_FIELDS
            fields = {name: item for name, item in contract_of(
                self.app.configuration.registry, REPLY_GROUP).items() if name in names}
            return {
                "settings": {key.removeprefix(prefix): value for key, value in snapshot.values.items()
                             if key.removeprefix(prefix) in names},
                "inherited_settings": {key.removeprefix(prefix): value for key, value in inherited.values.items()
                                       if key.removeprefix(prefix) in names},
                "edit_token": snapshot.edit_token,
                "revision": snapshot.revision,
                "configuration_token": snapshot.resolution_token,
                "scope": ConfigLayer.WINDOW.value,
                "layers": layers_of(self.manager.target),
                "provenance": {name: value for name, value in provenance_of(snapshot, REPLY_GROUP).items()
                               if name in names},
                "fields": fields,
                "editable_fields": sorted(names),
            }

    @outcome
    def configure(self, operator, data):
        required = {"operation_key", "edit_token", "changes"}
        if (type(data) is not dict or not required <= set(data) or set(data) - required - {"reset_to_inherit"}
                or type(data["operation_key"]) is not str or not data["operation_key"].strip()
                or len(data["operation_key"]) > 128 or type(data["edit_token"]) is not str
                or len(data["edit_token"]) != 64 or type(data["changes"]) is not dict
                or any(type(name) is not str for name in data["changes"])):
            raise TaskError("reply_configuration_invalid", "provide explicit private reply changes")
        changes = data["changes"]
        resets = data.get("reset_to_inherit", [])
        if (not isinstance(resets, list) or any(type(name) is not str for name in resets)
                or not changes and not resets or set(changes) - PRIVATE_REPLY_FIELDS
                or set(resets) - PRIVATE_REPLY_FIELDS or set(changes).intersection(resets)
                or len(set(resets)) != len(resets)):
            raise TaskError("reply_configuration_invalid", "private reply settings are invalid")
        with self.app.runtime.transaction():
            self._snapshot(operator)
            require(self.app.tasks.check_generation())
            target = self._target()
            op_id = self.operation_prefix + digest([operator.subject, target.key, data["operation_key"]])
            try:
                receipt = require(self.app.configuration.patch(operator, target=target, group_id=REPLY_GROUP,
                    operation=OperationRef(op_id, REPLY_OWNER, target.authorization_scope,
                                           VersionRef(op_id, 1), op_id),
                    expected_edit_token=data["edit_token"],
                    changes={REPLY_GROUP + "." + name: value for name, value in changes.items()},
                    reset_to_inherit=tuple(REPLY_GROUP + "." + name for name in resets),
                    observed_at=self.app.now()))
            except ConfigurationError as error:
                raise TaskError(error.code, str(error)) from error
            result = {"replayed": receipt.replayed, "operation_id": op_id,
                      "status": require(self.status(operator))}
        self.app.notify()
        return result


EDITORS = {"profile": RelationshipProfileManagement, "person": PrivateAcquaintanceManagement,
           "person_learning": PrivateAcquaintanceLearningManagement,
           "contact": PrivateContactManagement, "growth": RoleGrowthManagement,
           "expression": PrivateExpressionLearningManagement, "reply": PrivateReplyManagement}


class PrivateMemberSettings:
    def __init__(self, app, context):
        self.app, self.context = app, context

    @outcome
    def status(self, operator):
        sections, section_errors = {}, {}
        for name, editor in EDITORS.items():
            try:
                with self.app.runtime.read_snapshot():
                    sections[name] = require(editor(self.app, self.context).status(operator))
            except TaskError as error:
                section_errors[name] = {"code": error.code}
            except Exception:
                logger.exception("[CompanionV2] 私聊成员配置分区读取失败 section=%s", name)
                section_errors[name] = {"code": "member_settings_section_failed"}
        return {"sections": sections, "section_errors": section_errors}

    def _replayed_section_operation(self, name, editor, operator, section_key):
        targets = [editor._target()]
        if name == "contact":
            legacy_target = relationship_target_for(self.context)
            if legacy_target not in targets:
                targets.append(legacy_target)
        prefixes = tuple(dict.fromkeys((getattr(editor, "operation_prefix", None),
                                        f"{editor.group}-config:")))
        for target in targets:
            for prefix in prefixes:
                if prefix is None:
                    continue
                operation_id = prefix + digest([operator.subject, target.key, section_key])
                if self.app.configuration.store.read_one("""SELECT operation_id FROM configuration_changes
                        WHERE operation_id = ? AND target_key = ? AND group_id = ?""",
                        (operation_id, target.key, editor.group)) is not None:
                    return operation_id
        raise TaskError("configuration_operation_incomplete", "original member section result is unavailable")

    @outcome
    def configure(self, operator, command):
        if (type(command) is not dict or set(command) != {"operation_key", "sections"}
                or type(command["operation_key"]) is not str or not command["operation_key"].strip()
                or len(command["operation_key"]) > 100
                or type(command["sections"]) is not dict or not command["sections"]
                or set(command["sections"]) - set(EDITORS)):
            raise TaskError("private_member_command_invalid", "provide member settings and stable operation key")
        editors = {name: EDITORS[name](self.app, self.context) for name in command["sections"]}
        op_id = "private-member-settings:" + digest([operator.subject, self.context.key, command["operation_key"]])
        operation = OperationRef(op_id, "relationships.private-settings", self.context.scope,
                                 VersionRef(op_id, 1), op_id)
        store = self.app.configuration.store
        operations = OperationRepository(store)
        # Validate every original token before shared owner revisions advance.
        # All section writes and the receipt must share the configuration store
        # transaction; the main runtime no longer owns these rows.
        with store.transaction():
            snapshots = {name: require(editor.status(operator)) for name, editor in editors.items()}
            require(self.app.tasks.check_generation())
            receipt = require(operations.reserve(operation, parameter_hash=digest([
                operator.principal, operator.subject, operator.binding_id, operator.binding_revision,
                self.context.role_binding, command["sections"]]), observed_at=self.app.now()))
            for name, data in command["sections"].items():
                allowed_section_keys = {"edit_token", "changes", "reset_to_inherit"} if name in (
                    "reply", "person", "person_learning", "contact") else {
                    "edit_token", "changes"}
                if (type(data) is not dict or not {"edit_token", "changes"} <= set(data)
                        or set(data) - allowed_section_keys
                        or type(data["edit_token"]) is not str or len(data["edit_token"]) != 64
                        or type(data["changes"]) is not dict
                        or (not data["changes"] and not data.get("reset_to_inherit"))
                        or ("reset_to_inherit" in data and (type(data["reset_to_inherit"]) is not list
                            or any(type(field) is not str for field in data["reset_to_inherit"])) )):
                    raise TaskError("private_member_command_invalid", "provide each section's edit token and explicit changes")
                if not receipt.replayed and data["edit_token"] != snapshots[name]["edit_token"]:
                    raise TaskError("configuration_revision_conflict", "configuration, inheritance, binding, or policy changed")
            if receipt.replayed and receipt.state is not OperationState.COMPLETED:
                raise TaskError("configuration_operation_incomplete", "original member settings result is unavailable")
            results = {}
            for name, data in command["sections"].items():
                editor = editors[name]
                section_key = op_id + ":" + name
                if receipt.replayed:
                    results[name] = {"replayed": True, "operation_id":
                        self._replayed_section_operation(name, editor, operator, section_key),
                        "status": require(editor.status(operator))}
                else:
                    latest = require(editor.status(operator))
                    results[name] = require(editor.configure(operator,
                        {**data, "edit_token": latest["edit_token"], "operation_key": section_key}))
            if not receipt.replayed:
                require(operations.transition(op_id, expected_state=OperationState.ACCEPTED,
                    next_state=OperationState.COMPLETED, observed_at=self.app.now()))
            status = require(self.status(operator))
        self.app.notify()
        return {"results": results, **status}
